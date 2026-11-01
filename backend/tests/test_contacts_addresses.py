"""Contact checks, delivery addresses with receivers, the address check on the map, cart suggestions."""

from decimal import Decimal

import httpx
import pytest

from app.domain import contacts
from app.domain import suggestions as rules
from app.domain.pricing import Tier
from app.integrations import geocoder as geo
from tests.conftest import login
from tests.test_flow import product_id, put

# --- contacts ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "phone"),
    [
        ("091 23-45-67", "+374 91 234 567"),
        ("+37491234567", "+374 91 234 567"),
        ("374 91 234567", "+374 91 234 567"),
        ("0037410545454", "+374 10 545 454"),
        ("91234567", "+374 91 234 567"),
        ("12345", None),
        ("+7 912 345 67 89", None),
        ("0012345678", None),
    ],
)
def test_phone(raw, phone):
    assert contacts.normalize_phone(raw) == phone


def test_email_and_account():
    assert contacts.normalize_email(" Ani@Cafe.AM ") == "ani@cafe.am"
    assert contacts.normalize_email("ani@cafe") is None
    assert contacts.email_suggestion("ani@gmial.com") == "ani@gmail.com"
    assert contacts.email_suggestion("ani@mail.ri") == "ani@mail.ru"
    assert contacts.email_suggestion("ani@cafe.am") is None
    assert contacts.normalize_bank_account("1570 0012 3456 7890") == "1570001234567890"
    assert contacts.normalize_bank_account("12345") is None
    out, problems = contacts.check({"phone": "091234567", "email": "", "bank_account": "1"})
    assert out == {"phone": "+374 91 234 567", "email": None}
    assert problems == [{"field": "bank_account", "code": "bad_bank_account"}]


@pytest.mark.usefixtures("seeded")
async def test_requisites_are_checked_and_normalized(client):
    arax = await login(client, "arax@easo.local")
    r = await client.patch(
        "/organizations/me", headers=arax, json={"phone": "091-23-45-67", "email": "X@Cafe.am"}
    )
    assert (r.json()["phone"], r.json()["email"]) == ("+374 91 234 567", "x@cafe.am")
    r = await client.patch("/organizations/me", headers=arax, json={"bank_account": "123"})
    assert r.json()["details"]["problems"] == [{"field": "bank_account", "code": "bad_bank_account"}]


# --- delivery addresses ----------------------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
async def test_several_addresses_with_receivers(client):
    arax = await login(client, "arax@easo.local")
    before = (await client.get("/addresses", headers=arax)).json()
    r = await client.post(
        "/addresses",
        headers=arax,
        json={
            "label": "Kitchen 2",
            "text": "Komitas Ave 40",
            "district": "Arabkir",
            "contact_name": "Gor",
            "contact_phone": "094 11 22 33",
            "delivery_notes": "Back door, 9-12",
        },
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["contact_phone"], a["district"]) == ("+374 94 112 233", "Arabkir")

    bad = await client.post("/addresses", headers=arax, json={"text": " ", "contact_phone": "1"})
    assert {p["field"] for p in bad.json()["details"]["problems"]} == {"text", "contact_phone"}

    r = await client.put(f"/addresses/{a['id']}", headers=arax, json={**a, "contact_name": "Gor A."})
    assert r.json()["contact_name"] == "Gor A."
    assert len((await client.get("/addresses", headers=arax)).json()) == len(before) + 1

    # The cart delivers to the chosen address; a removed one leaves the list but not old orders
    cart = (await client.get("/cart", headers=arax, params={"address_id": a["id"]})).json()
    assert cart["address"]["contact_name"] == "Gor A."
    assert (await client.delete(f"/addresses/{a['id']}", headers=arax)).status_code == 204
    assert len((await client.get("/addresses", headers=arax)).json()) == len(before)
    assert (await client.get("/cart", headers=arax, params={"address_id": a["id"]})).status_code == 404

    nova = await login(client, "nova@easo.local")
    assert (await client.delete(f"/addresses/{before[0]['id']}", headers=nova)).status_code == 404


# --- address check on the map ----------------------------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False, px=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


NOMINATIM_ABOVYAN = [
    {
        "lat": "40.1834",
        "lon": "44.5148",
        "name": "",
        "display_name": "10, Աբովյան փողոց, Կենտրոն, Երևան, 0001, Հայաստան",
        "address": {
            "house_number": "10",
            "road": "Աբովյան փողոց",
            "suburb": "Կենտրոն",
            "city": "Երևան",
            "country": "Հայաստան",
        },
    }
]


async def test_geocoder_finds_house_and_district():
    calls = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["q"])
        return httpx.Response(200, json=NOMINATIM_ABOVYAN)

    redis = FakeRedis()
    g = geo.NominatimGeocoder(
        "https://nominatim.test", redis, httpx.AsyncClient(transport=httpx.MockTransport(answer))
    )
    place = await g.resolve("Абовяна 10", "hy")
    assert place is not None
    assert (place.text, place.district, place.house) == ("Աբովյան փողոց 10, Երևան", "Kentron", "10")
    assert calls == ["Абовяна 10, Երևան"]  # the city is added for the search
    # Cached: the same address does not go to Nominatim again (and needs no turn in the queue)
    redis.data.pop("geo:lock")
    assert (await g.resolve("  абовяна   10 ", "hy")).district == "Kentron"
    assert len(calls) == 1


async def test_geocoder_not_found_and_down():
    empty = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    assert await geo.NominatimGeocoder("https://n.test", FakeRedis(), empty).resolve("Nowhere 1") is None
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(geo.GeocoderUnavailable):
        await geo.NominatimGeocoder("https://n.test", FakeRedis(), down).resolve("Abovyan 10")
    names = ("Նորք-Մարաշ", "Арабкир", "Arabkir district", "Нор-Норк", "Канакер-Зейтун", "Unknown")
    assert [geo.district_of(n) for n in names] == [
        "Nork-Marash",
        "Arabkir",
        "Arabkir",
        "Nor Nork",
        "Kanaker-Zeytun",
        None,
    ]


@pytest.mark.usefixtures("seeded")
async def test_geo_endpoint_falls_back_when_map_is_down(client, monkeypatch):
    async def down(self, q, lang="hy"):
        raise geo.GeocoderUnavailable("down")

    monkeypatch.setattr(geo.NominatimGeocoder, "resolve", down)
    arax = await login(client, "arax@easo.local")
    r = await client.get("/geo/resolve", headers=arax, params={"q": "Abovyan 10", "lang": "ru"})
    assert r.json() == {
        "found": False,
        "unavailable": True,
        "text": None,
        "street": None,
        "house": None,
        "district": None,
        "lat": None,
        "lng": None,
    }
    assert (await client.get("/geo/resolve", params={"q": "Abovyan 10"})).status_code == 401


# --- cart suggestions ----------------------------------------------------------------------------------------------


def test_suggestion_rules():
    assert rules.complement_categories({"dairy"}) == ["bakery", "produce", "disposables"]
    assert "dairy" not in rules.complement_categories({"dairy", "coffee"})
    step = rules.next_tier(Decimal(12), 3500, [Tier(Decimal(20), 3350), Tier(Decimal(50), 3200)])
    assert step == rules.TierStep(Decimal(20), 3350, Decimal(8), 3000)
    assert rules.next_tier(Decimal(12), 3300, [Tier(Decimal(20), 3350)]) is None  # contract price is lower
    ranked = rules.rank_fillers(
        [
            rules.Candidate("cheap", 900, False, 50),
            rules.Candidate("fits", 11_000, False, 1),
            rules.Candidate("usual", 4_500, True, 0),
        ],
        gap_amd=10_400,
    )
    assert [c.product_id for c in ranked] == ["usual", "fits", "cheap"]


@pytest.mark.usefixtures("seeded")
async def test_cart_suggestions(client):
    arax = await login(client, "arax@easo.local")
    cheese = await product_id(client, arax, "Lori cheese")
    await put(client, arax, cheese, 12)  # 42 000 at Lori: free delivery from 50 000
    items = (await client.get("/cart/suggestions", headers=arax)).json()
    kinds = {}
    for s in items:
        kinds.setdefault(s["kind"], []).append(s)

    fill = kinds["fill_free_delivery"]
    assert fill and all(s["seller"]["display_name"] == fill[0]["seller"]["display_name"] for s in fill)
    assert fill[0]["gap_amd"] > 0 and cheese not in {s["product_id"] for s in items if s["kind"] != "tier"}
    tier = kinds.get("tier", [])
    assert not tier or (tier[0]["product_id"] == cheese and Decimal(tier[0]["qty"]) == 20)
    # Cheese goes with bread, greens, packaging from other sellers that deliver to Kentron
    complements = kinds["complement"]
    assert complements and all(s["seller"]["display_name"] != "Լոռի Պանիր" for s in complements)
    assert len({s["product_id"] for s in items}) == len(items)  # one reason per product
