from sqlalchemy import select

from app.config import get_settings
from app.models import Checkout, Order, OrderStatus, Organization
from tests.conftest import login


async def test_health(client):
    r = await client.get("/health")
    assert r.json() == {"status": "ok"}


async def test_me_requires_token(client):
    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_dev_login_and_me(client, seeded):
    headers = await login(client, "chef@easo.local")
    me = (await client.get("/me", headers=headers)).json()
    assert me["email"] == "chef@easo.local"
    [m] = me["memberships"]
    assert m["role"] == "buyer_staff"
    assert m["organization"]["display_name"] == "Cafe Arax"


async def test_dev_login_off(client, seeded, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_login", False)
    r = await client.post("/auth/dev-login", json={"email": "chef@easo.local"})
    assert r.status_code == 404


async def test_categories(client, seeded):
    headers = await login(client, "arax@easo.local")
    cats = (await client.get("/categories", headers=headers)).json()
    assert [c["slug"] for c in cats][:3] == ["disposables", "cleaning", "grocery"]
    assert cats[0]["name"]["hy"] == "Միանգամյա սպասք"


async def test_products_list_has_seller_and_tiers(client, seeded):
    headers = await login(client, "arax@easo.local")
    page = (await client.get("/products", headers=headers, params={"limit": 100})).json()
    assert page["total"] == 15  # Dilijan Honey is not verified: hidden
    cheese = next(p for p in page["items"] if p["name"]["en"] == "Lori cheese")
    assert cheese["seller"]["display_name"] == "Լոռի Պանիր"
    assert cheese["base_price_amd"] == 3500
    assert cheese["tiers"] == [{"min_qty": "20.000", "price_amd": 3350}]


async def test_search_in_three_scripts(client, seeded):
    headers = await login(client, "arax@easo.local")

    async def names(q: str) -> set[str]:
        page = (await client.get("/products", headers=headers, params={"q": q})).json()
        return {p["name"]["en"] for p in page["items"]}

    # Armenian, Russian, and Latin transliteration of the Armenian word find the same cheese
    for q in ("պանիր", "сыр", "panir"):
        assert "Lori cheese" in await names(q), q
    assert await names("стакан") == {"Paper cup 250 ml"}


async def test_products_filters(client, seeded, sessionmaker):
    headers = await login(client, "arax@easo.local")
    page = (await client.get("/products", headers=headers, params={"offers_only": True})).json()
    assert all(p["allow_offers"] for p in page["items"]) and page["total"] == 6

    async with sessionmaker() as s:
        farm = await s.scalar(select(Organization).where(Organization.display_name == "Green Valley"))
        farm.status = "blocked"
        await s.commit()
    page = (await client.get("/products", headers=headers, params={"limit": 100})).json()
    assert page["total"] == 11  # blocked seller's products are hidden


async def test_order_numbers_come_from_sequence(sessionmaker, seeded):
    async with sessionmaker() as s:
        buyer = await s.scalar(select(Organization).where(Organization.display_name == "Cafe Arax"))
        seller = await s.scalar(select(Organization).where(Organization.display_name == "Yerevan Pack"))
        checkout = Checkout(buyer_id=buyer.id)
        orders = [
            Order(checkout=checkout, buyer_id=buyer.id, seller_id=seller.id, status=OrderStatus.placed)
            for _ in range(2)
        ]
        s.add_all(orders)
        await s.commit()
        a, b = sorted(o.number for o in orders)
    assert a.startswith("EASO-") and len(a) == len("EASO-26-000001")
    assert int(b[-6:]) == int(a[-6:]) + 1
