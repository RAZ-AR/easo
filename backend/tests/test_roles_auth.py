"""Sign-in by e-mail code, the guest catalog, one company on both sides, and the gates of each side."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Organization, Product
from tests.conftest import login
from tests.test_flow import checkout, product_id, put

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture(autouse=True)
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))


async def email_login(client, email: str) -> dict:
    r = await client.post("/auth/email/start", json={"email": email, "lang": "ru"})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = await client.post("/auth/email/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# --- e-mail code ----------------------------------------------------------------------------------------


async def test_email_code_creates_user_and_signs_in(client):
    headers = await email_login(client, "New.Chef@Example.am")
    me = (await client.get("/me", headers=headers)).json()
    assert (me["email"], me["memberships"]) == ("new.chef@example.am", [])
    # The same address signs into the same user
    again = await email_login(client, "new.chef@example.am")
    assert (await client.get("/me", headers=again)).json()["id"] == me["id"]


async def test_email_code_for_existing_user(client):
    headers = await email_login(client, "arax@easo.local")
    me = (await client.get("/me", headers=headers)).json()
    assert me["memberships"][0]["organization"]["display_name"] == "Cafe Arax"


async def test_wrong_code_and_rate_limit(client):
    await client.post("/auth/email/start", json={"email": "x@example.am"})
    r = await client.post("/auth/email/verify", json={"email": "x@example.am", "code": "000000x"})
    assert r.json()["details"]["problems"][0]["code"] == "code_wrong"
    r = await client.post("/auth/email/start", json={"email": "not-an-email"})
    assert r.json()["details"]["problems"][0]["code"] == "bad_email"
    for _ in range(4):
        await client.post("/auth/email/start", json={"email": "x@example.am"})
    assert (await client.post("/auth/email/start", json={"email": "x@example.am"})).status_code == 429


async def test_dev_code_hidden_without_dev_login(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_login", False)
    r = await client.post("/auth/email/start", json={"email": "y@example.am"})
    assert r.json() == {"sent": True, "dev_code": None}


async def test_google_and_telegram_need_keys(client):
    assert (await client.post("/auth/google", json={"credential": "x"})).status_code == 401
    assert (
        await client.post("/auth/telegram", json={"id": 1, "hash": "x", "auth_date": 1})
    ).status_code == 401


# --- guest catalog --------------------------------------------------------------------------------------


async def test_guest_sees_products_without_sellers(client):
    assert (await client.get("/categories")).status_code == 200
    page = (await client.get("/products", params={"limit": 100})).json()
    assert page["guest"] is True and page["total"] == 15
    assert all(p["seller"] is None for p in page["items"])
    # Signed-in users see the seller
    arax = await login(client, "arax@easo.local")
    assert (await client.get("/products", headers=arax)).json()["items"][0]["seller"] is not None
    # Guest cart rebuild: products by id
    ids = [p["id"] for p in page["items"][:2]]
    assert (await client.get("/products", params={"ids": ids})).json()["total"] == 2


async def test_guest_cannot_order(client):
    assert (await client.get("/cart")).status_code == 401
    assert (await client.post("/checkout", json={})).status_code == 401


# --- both sides in one company ---------------------------------------------------------------------------


async def test_buyer_adds_seller_role_and_waits_for_verification(client, sessionmaker):
    arax = await login(client, "arax@easo.local")
    org = (await client.post("/organizations/me/roles", headers=arax, json={"role": "seller"})).json()
    assert (org["is_buyer"], org["is_seller"]) == (True, True)
    # The buyer check covered only the registry extract: selling goes back to the operator
    assert org["verification_status"] == "pending"
    me = (await client.get("/me", headers=arax)).json()
    assert me["memberships"][0]["organization"]["is_seller"] is True
    seller_mode = {**arax, "X-Mode": "seller"}
    assert (await client.get("/orders", headers=seller_mode)).json()["total"] == 0
    r = await client.post(
        "/seller/products",
        headers=seller_mode,
        json={"name": {"ru": "Пахлава"}, "unit": "kg", "base_price_amd": 5000},
    )
    assert r.status_code == 201, r.text  # the catalog can be prepared meanwhile
    guest = (await client.get("/products", params={"q": "Пахлава"})).json()
    assert guest["total"] == 0


async def test_cannot_buy_own_products(client, sessionmaker):
    lori = await login(client, "lori@easo.local")
    await client.post(
        "/organizations/me/roles", headers=lori, json={"role": "buyer", "delivery_address": "Stepanavan"}
    )
    buyer_mode = {**lori, "X-Mode": "buyer"}
    # Own products are not offered in the catalog ...
    page = (await client.get("/products", headers=buyer_mode, params={"limit": 100})).json()
    assert all(p["seller"]["display_name"] != "Լոռի Պանիր" for p in page["items"])
    # ... and the cart refuses them anyway
    async with sessionmaker() as s:
        cheese = await s.scalar(select(Product.id).where(Product.name["en"].astext == "Lori cheese"))
    cart = await put(client, buyer_mode, cheese, 12)
    assert "own_products" in cart["groups"][0]["problems"]


async def test_duplicate_tier_quantities_are_rejected(client):
    lori = await login(client, "lori@easo.local")
    r = await client.post(
        "/seller/products",
        headers=lori,
        json={
            "name": {"ru": "Сыр"},
            "unit": "kg",
            "base_price_amd": 3000,
            "tiers": [{"min_qty": "10", "price_amd": 2900}, {"min_qty": "10", "price_amd": 2800}],
        },
    )
    assert (r.status_code, r.json()["details"]["problems"][0]["code"]) == (422, "bad_tier")


async def test_login_e_mails_limited_per_client(client, memory_ratelimit):
    for i in range(20):
        r = await client.post("/auth/email/start", json={"email": f"u{i}@example.am"})
        assert r.status_code == 200, r.text
    r = await client.post("/auth/email/start", json={"email": "u99@example.am"})
    assert (r.status_code, r.json()["code"]) == (429, "too_many_codes")


async def test_external_e_mail_matches_regardless_of_case(client, sessionmaker):
    from app.auth import service
    from app.auth.identity import ExternalIdentity
    from app.models import AuthProvider

    await client.post("/auth/email/start", json={"email": "anna@firm.am"})
    async with sessionmaker() as s:
        first = await service.login_or_register(
            s, ExternalIdentity(provider=AuthProvider.email, external_id="anna@firm.am", email="anna@firm.am")
        )
        await s.commit()
        again = await service.login_or_register(
            s, ExternalIdentity(provider=AuthProvider.google, external_id="g-1", email="Anna@Firm.am")
        )
        assert again.id == first.id


async def test_new_buyer_orders_only_after_uploading_extract(client):
    me = await email_login(client, "owner@cascade.am")
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["buyer"],
            "tin": "02598301",
            "legal_name": "Cascade Cafe LLC",
            "delivery_address": "Tamanyan 3",
        },
    )
    assert r.status_code == 201
    await put(client, me, await product_id(client, me, "Sour cream 20%"), 10)  # the cart is open
    r = await checkout(client, me)
    assert (r.status_code, r.json()["code"]) == (403, "upload_registry_extract")
    await client.post(
        "/verification/files", headers=me, data={"kind": "state_registration"},
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )  # fmt: skip
    assert (await checkout(client, me)).status_code == 201


async def test_unverified_seller_products_are_hidden(client, sessionmaker):
    async with sessionmaker() as s:
        pack = await s.scalar(select(Organization).where(Organization.display_name == "Yerevan Pack"))
        pack.verification_status = "pending"
        await s.commit()
        hidden = await s.scalar(select(Product.id).where(Product.seller_id == pack.id).limit(1))
    page = (await client.get("/products", params={"limit": 100})).json()
    assert str(hidden) not in {p["id"] for p in page["items"]}


# --- seller cabinet ---------------------------------------------------------------------------------------


PRODUCT = {
    "name": {"hy": "Մոցարելլա", "ru": "Моцарелла", "en": "Mozzarella"},
    "unit": "kg",
    "base_price_amd": 4200,
    "min_qty": "2",
    "qty_step": "1",
    "allow_offers": True,
    "tiers": [{"min_qty": "10", "price_amd": 4000}],
    "autopilot": {"accept_discount_bp": 600, "max_discount_bp": 1200, "share_hint": True},
}


async def test_seller_manages_products_and_terms(client):
    lori = await login(client, "lori@easo.local")
    r = await client.post("/seller/products", headers=lori, json=PRODUCT)
    assert r.status_code == 201, r.text
    p = r.json()
    ap = p["autopilot"]
    assert (p["vat_rate_bp"], p["tiers"][0]["price_amd"]) == (0, 4000)
    assert (ap["accept_discount_bp"], ap["max_discount_bp"], ap["share_hint"]) == (600, 1200, True)
    # Visible in the catalog and found by its Russian name in Latin letters
    found = (await client.get("/products", params={"q": "motsarella"})).json()["items"]
    assert [x["id"] for x in found] == [p["id"]]

    upd = {**PRODUCT, "base_price_amd": 4300, "tiers": [], "allow_offers": False}
    p2 = (await client.put(f"/seller/products/{p['id']}", headers=lori, json=upd)).json()
    assert (p2["base_price_amd"], p2["tiers"], p2["autopilot"]) == (4300, [], None)

    assert (await client.post(f"/seller/products/{p['id']}/archive", headers=lori)).json()["archived"] is True
    assert p["id"] not in {
        x["id"] for x in (await client.get("/products", headers=lori, params={"limit": 100})).json()["items"]
    }
    assert len((await client.get("/seller/products", headers=lori, params={"archived": True})).json()) == 1

    bad = {**PRODUCT, "autopilot": {"accept_discount_bp": 900, "max_discount_bp": 500}}
    r = await client.post("/seller/products", headers=lori, json=bad)
    assert r.json()["details"]["problems"][0]["code"] == "floor_above_accept"
    no_accept = {**PRODUCT, "autopilot": {"accept_discount_bp": None}}
    r = await client.post("/seller/products", headers=lori, json=no_accept)
    assert r.json()["details"]["problems"][0]["code"] == "bad_accept_discount"

    s = (await client.get("/seller/settings", headers=lori)).json()
    s.update(min_order_amd=25_000, zones=[{"district": "Kentron", "weekdays": [1, 3, 5]}])
    saved = (await client.put("/seller/settings", headers=lori, json=s)).json()
    assert (saved["min_order_amd"], saved["zones"]) == (
        25_000,
        [{"district": "Kentron", "weekdays": [1, 3, 5]}],
    )


async def test_buyers_cannot_use_seller_cabinet(client):
    arax = await login(client, "arax@easo.local")
    r = await client.get("/seller/products", headers=arax)
    assert (r.status_code, r.json()["code"]) == (403, "not_a_seller")


async def test_other_sellers_products_are_not_editable(client):
    lori, pack = await login(client, "lori@easo.local"), await login(client, "pack@easo.local")
    p = (await client.post("/seller/products", headers=lori, json=PRODUCT)).json()
    assert (await client.put(f"/seller/products/{p['id']}", headers=pack, json=PRODUCT)).status_code == 404


# --- operator: sign in as a demo user -----------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
async def test_operator_can_sign_in_as_demo_user_only(client):
    admin = await login(client, "admin@easo.local")
    users = (await client.get("/admin/demo-users", headers=admin)).json()
    emails = {u["email"] for u in users}
    assert {"arax@easo.local", "baker@easo.local", "newbie@easo.local", "manager@easo.local"} <= emails
    baker = next(u for u in users if u["email"] == "baker@easo.local")
    assert (baker["roles"], baker["role"]) == (["buyer", "seller"], "owner")

    r = await client.post(f"/admin/demo-users/{baker['id']}/token", headers=admin)
    assert r.status_code == 200
    me = (await client.get("/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me["email"] == "baker@easo.local"

    # Buyers cannot use it
    arax = await login(client, "arax@easo.local")
    assert (await client.get("/admin/demo-users", headers=arax)).status_code == 403
    assert (await client.post(f"/admin/demo-users/{baker['id']}/token", headers=arax)).status_code == 403


async def test_operator_cannot_sign_in_as_real_user(client, sessionmaker):
    from app.models import User

    async with sessionmaker() as s:
        admin = User(name="Op", email="op@example.com", is_platform_admin=True)
        real = User(name="Real", email="real@example.com")
        s.add_all([admin, real])
        await s.commit()
        real_id = real.id
    from app.auth.tokens import issue_token

    h = {"Authorization": f"Bearer {issue_token(admin.id)}"}
    assert (await client.post(f"/admin/demo-users/{real_id}/token", headers=h)).status_code == 404
