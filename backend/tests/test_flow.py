"""End-to-end purchasing flows through the API on the demo data (app/seed.py).

Cafe Arax (Kentron) is Lori's own client (0% commission); Bar Nova (Arabkir) found Yerevan Pack on the platform.
Lori cheese: 3 500 ֏/kg, autopilot floor 3 000, auto-accept from 3 300, offers from 20 000 ֏ per line.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Order, OutboxEvent
from app.platform.clock import utcnow
from app.services import negotiation as nego_svc
from app.services import orders as order_svc
from tests.conftest import login

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture
async def as_user(client):
    cache: dict[str, dict] = {}

    async def headers(email: str) -> dict:
        if email not in cache:
            cache[email] = await login(client, f"{email}@easo.local")
        return cache[email]

    return headers


async def product_id(client, headers, name_en: str) -> str:
    page = (await client.get("/products", headers=headers, params={"q": name_en, "limit": 100})).json()
    return next(p["id"] for p in page["items"] if p["name"]["en"] == name_en)


async def put(client, headers, pid, qty, offer=None):
    r = await client.put(
        f"/cart/items/{pid}", headers=headers, json={"qty": str(qty), "offer_price_amd": offer}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def checkout(client, headers, **body):
    return await client.post("/checkout", headers=headers, json=body)


# --- happy paths ------------------------------------------------------------------------------------------


async def test_autopilot_accepts_offer_at_checkout(client, as_user):
    arax = await as_user("arax")
    cheese = await product_id(client, arax, "Lori cheese")
    cart = await put(client, arax, cheese, 12, offer=3300)
    assert cart["problems"] == [] and cart["groups"][0]["lines"][0]["offer_price_amd"] == 3300

    r = await checkout(client, arax)
    assert r.status_code == 201, r.text
    [order] = r.json()["orders"]
    assert order["status"] == "placed"
    assert order["negotiation"]["status"] == "agreed"
    assert order["negotiation"]["offers"][-1]["response"] == "accepted"
    [line] = order["lines"]
    assert (line["final_price_amd"], line["price_source"]) == (3300, "negotiated")
    # 12 x 3 300 = 39 600 + delivery 1 000 (free from 50 000)
    assert (order["subtotal_amd"], order["delivery_fee_amd"], order["total_amd"]) == (39_600, 1000, 40_600)
    assert order["number"].startswith("EASO-")
    # The cart is emptied
    assert (await client.get("/cart", headers=arax)).json()["groups"] == []


async def test_autopilot_counter_then_buyer_accepts(client, as_user):
    arax = await as_user("arax")
    await put(client, arax, await product_id(client, arax, "Lori cheese"), 12, offer=3100)
    [order] = (await checkout(client, arax)).json()["orders"]
    assert order["status"] == "negotiating"
    nego = order["negotiation"]
    assert nego["status"] == "awaiting_buyer" and nego["my_turn"]
    counter = nego["offers"][-1]
    assert (counter["from_side"], counter["is_auto"], counter["lines"][0]["proposed_price_amd"]) == (
        "seller",
        True,
        3300,
    )
    assert "respond" in order["allowed_actions"]

    line_id = order["lines"][0]["id"]
    r = await client.post(
        f"/orders/{order['id']}/negotiation/respond",
        headers=arax,
        json={"lines": [{"order_line_id": line_id, "decision": "accepted"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "placed"
    assert r.json()["lines"][0]["final_price_amd"] == 3300


async def test_autopilot_declines_lowball_and_order_goes_at_list_price(client, as_user):
    arax = await as_user("arax")
    await put(client, arax, await product_id(client, arax, "Lori cheese"), 12, offer=2000)
    [order] = (await checkout(client, arax)).json()["orders"]
    assert order["status"] == "placed"
    assert order["negotiation"]["status"] == "declined"
    assert (order["lines"][0]["final_price_amd"], order["lines"][0]["price_source"]) == (3500, "list")


async def test_manual_negotiation_rounds(client, as_user):
    """No rule for chanakh cheese: a human answers. Prices converge round by round."""
    nova, lori = await as_user("nova"), await as_user("lori")
    await put(client, nova, await product_id(client, nova, "Chanakh cheese"), 7, offer=2900)  # 22 400 ֏ line
    [order] = (await checkout(client, nova)).json()["orders"]
    oid, line_id = order["id"], order["lines"][0]["id"]
    assert order["negotiation"]["status"] == "awaiting_seller" and not order["negotiation"]["my_turn"]

    # Buyer cannot answer their own offer
    r = await client.post(f"/orders/{oid}/negotiation/respond", headers=nova, json={"lines": []})
    assert (r.status_code, r.json()["code"]) == (409, "not_your_turn")

    seller_view = (await client.get(f"/orders/{oid}", headers=lori)).json()
    assert seller_view["negotiation"]["my_turn"] and seller_view["my_side"] == "seller"

    async def answer(headers, decision, price=None):
        return await client.post(
            f"/orders/{oid}/negotiation/respond",
            headers=headers,
            json={"lines": [{"order_line_id": line_id, "decision": decision, "price_amd": price}]},
        )

    # Seller counter above the list price is refused
    r = await answer(lori, "countered", 3200)
    assert (r.status_code, r.json()["details"]["problems"][0]["code"]) == (422, "counter_not_below_price")
    assert (await answer(lori, "countered", 3100)).status_code == 200
    # Buyer must move up and stay below the counter
    r = await answer(nova, "countered", 2800)
    assert r.json()["details"]["problems"][0]["code"] == "offer_must_increase"
    assert (await answer(nova, "countered", 3000)).status_code == 200
    r = await answer(lori, "accepted")
    body = r.json()
    assert body["status"] == "placed"
    assert body["lines"][0]["final_price_amd"] == 3000
    assert [o["from_side"] for o in body["negotiation"]["offers"]] == ["buyer", "seller", "buyer"]
    assert body["negotiation"]["round"] == 3


async def test_order_level_discount(client, as_user):
    nova, pack = await as_user("nova"), await as_user("pack")
    await put(client, nova, await product_id(client, nova, "Paper cup 250 ml"), 10)  # tier 4 200 -> 42 000
    seller = (await client.get("/cart", headers=nova)).json()["groups"][0]["seller"]["id"]
    r = await checkout(client, nova, order_offers=[{"seller_id": seller, "percent_bp": 700}])
    [order] = r.json()["orders"]
    assert order["negotiation"]["scope"] == "order" and order["negotiation"]["status"] == "awaiting_seller"
    oid = order["id"]
    r = await client.post(
        f"/orders/{oid}/negotiation/respond",
        headers=pack,
        json={"order": {"decision": "countered", "percent_bp": 500}},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/orders/{oid}/negotiation/respond", headers=nova, json={"order": {"decision": "accepted"}}
    )
    body = r.json()
    assert body["status"] == "placed"
    # 42 000 - 5% = 39 900, delivery 1 000 (below 50 000)
    assert (body["subtotal_amd"], body["discount_amd"], body["total_amd"]) == (42_000, 2_100, 40_900)


async def test_full_lifecycle_with_commission(client, as_user, sessionmaker):
    nova, pack = await as_user("nova"), await as_user("pack")
    cups = await product_id(client, nova, "Paper cup 250 ml")
    await put(client, nova, cups, 10)
    await put(client, nova, await product_id(client, nova, "Napkins 24x24"), 2)
    [order] = (await checkout(client, nova)).json()["orders"]
    oid = order["id"]
    assert order["status"] == "placed" and order["total_amd"] == 54_000  # 42 000 + 12 000, free delivery
    assert set(order["allowed_actions"]) == {"cancel"}

    cups_line = next(li["id"] for li in order["lines"] if li["name_snapshot"]["en"].startswith("Paper cup"))
    r = await client.post(
        f"/orders/{oid}/confirm", headers=pack, json={"lines": [{"order_line_id": cups_line, "qty": "8"}]}
    )
    assert r.status_code == 200, r.text
    # Prices stay as fixed at checkout (tier 4 200); only the quantity changes
    assert r.json()["status"] == "confirmed" and r.json()["subtotal_amd"] == 8 * 4200 + 12_000
    for action in ("ship", "deliver"):
        assert (await client.post(f"/orders/{oid}/{action}", headers=pack)).status_code == 200
    r = await client.post(f"/orders/{oid}/accept", headers=nova, json={})
    body = r.json()
    assert body["status"] == "accepted"
    assert body["allowed_actions"] == ["reorder"]  # mark_paid is the seller's
    async with sessionmaker() as s:
        o = await s.get(Order, order["id"])
        # Platform client, first order: disposables 10% + 2.5% bonus on 45 600 ֏
        assert (o.client_source, o.commission_rate_bp, o.commission_amd) == ("platform", 1250, 5700)
    r = await client.post(f"/orders/{oid}/mark_paid", headers=pack)
    assert r.json()["payment_status"] == "paid"


async def test_own_client_pays_no_commission(client, as_user, sessionmaker):
    arax, lori = await as_user("arax"), await as_user("lori")
    await put(client, arax, await product_id(client, arax, "Sour cream 20%"), 10)
    [order] = (await checkout(client, arax)).json()["orders"]
    oid = order["id"]
    await client.post(f"/orders/{oid}/confirm", headers=lori, json={})
    await client.post(f"/orders/{oid}/deliver", headers=lori)
    await client.post(f"/orders/{oid}/accept", headers=arax, json={})
    async with sessionmaker() as s:
        o = await s.get(Order, order["id"])
        assert (o.client_source, o.commission_amd) == ("seller_invited", 0)


async def test_contract_price_applies_to_next_cart(client, as_user):
    arax, lori = await as_user("arax"), await as_user("lori")
    cheese = await product_id(client, arax, "Lori cheese")
    await put(client, arax, cheese, 12, offer=3300)
    [order] = (await checkout(client, arax)).json()["orders"]
    r = await client.post(
        "/agreements", headers=lori, json={"order_line_id": order["lines"][0]["id"], "days": 30}
    )
    assert r.status_code == 201, r.text
    # Buyer cannot create contracts
    r = await client.post("/agreements", headers=arax, json={"order_line_id": order["lines"][0]["id"]})
    assert r.status_code == 403
    cart = await put(client, arax, cheese, 5)
    line = cart["groups"][0]["lines"][0]
    assert (line["unit_price_amd"], line["price_source"]) == (3300, "agreement")


# --- timers ----------------------------------------------------------------------------------------------


async def test_unanswered_offer_expires_and_reorder_refills_cart(client, as_user, sessionmaker):
    nova = await as_user("nova")
    chanakh = await product_id(client, nova, "Chanakh cheese")
    await put(client, nova, chanakh, 7, offer=2900)
    [order] = (await checkout(client, nova)).json()["orders"]
    async with sessionmaker() as s:
        assert await nego_svc.expire_due(s, utcnow() + timedelta(hours=1)) == 0
        assert await nego_svc.expire_due(s, utcnow() + timedelta(hours=25)) == 1
        await s.commit()
    body = (await client.get(f"/orders/{order['id']}", headers=nova)).json()
    assert (body["status"], body["cancel_reason"], body["negotiation"]["status"]) == (
        "cancelled",
        "offer_expired",
        "expired",
    )
    assert (await client.post(f"/orders/{order['id']}/reorder", headers=nova)).json() == {"added": 1}
    line = (await client.get("/cart", headers=nova)).json()["groups"][0]["lines"][0]
    assert (line["product_id"], line["qty"], line["offer_price_amd"]) == (chanakh, "7.000", None)


async def test_auto_accept_after_48h(client, as_user, sessionmaker):
    arax, lori = await as_user("arax"), await as_user("lori")
    await put(client, arax, await product_id(client, arax, "Sour cream 20%"), 10)
    [order] = (await checkout(client, arax)).json()["orders"]
    await client.post(f"/orders/{order['id']}/confirm", headers=lori, json={})
    await client.post(f"/orders/{order['id']}/deliver", headers=lori)
    async with sessionmaker() as s:
        assert await order_svc.auto_accept_due(s, utcnow() + timedelta(hours=47)) == 0
        assert await order_svc.auto_accept_due(s, utcnow() + timedelta(hours=49)) == 1
        await s.commit()
    assert (await client.get(f"/orders/{order['id']}", headers=arax)).json()["status"] == "accepted"


# --- validation, permissions, isolation ------------------------------------------------------------------


async def test_checkout_is_all_or_nothing(client, as_user, sessionmaker):
    nova = await as_user("nova")
    await put(client, nova, await product_id(client, nova, "Chanakh cheese"), 7)  # Lori: 22 400 >= 15 000
    await put(client, nova, await product_id(client, nova, "Lid 80 mm"), 1)  # Pack: 3 800 < 20 000
    r = await checkout(client, nova)
    assert r.status_code == 422
    assert {p["code"] for p in r.json()["details"]["problems"]} == {"min_order_not_reached"}
    async with sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Order)) == 0
    assert len((await client.get("/cart", headers=nova)).json()["groups"]) == 2


async def test_staff_limits(client, as_user):
    chef = await as_user("chef")  # Cafe Arax staff: limit 50 000, cannot negotiate
    cheese = await product_id(client, chef, "Lori cheese")
    r = await client.put(f"/cart/items/{cheese}", headers=chef, json={"qty": "12", "offer_price_amd": 3300})
    assert r.json()["details"]["problems"][0]["code"] == "not_allowed_to_negotiate"
    await put(client, chef, cheese, 20)  # 20 x 3 350 = 67 000
    r = await checkout(client, chef)
    assert r.status_code == 422 and r.json()["details"]["problems"][0]["code"] == "over_staff_limit"
    assert (await client.get("/cart", headers=chef)).json()["can_negotiate"] is False


async def test_idempotent_checkout(client, as_user, sessionmaker):
    arax = await as_user("arax")
    await put(client, arax, await product_id(client, arax, "Sour cream 20%"), 10)
    headers = {**arax, "Idempotency-Key": "tap-1"}
    first = await checkout(client, headers)
    second = await checkout(client, headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["orders"][0]["id"] == second.json()["orders"][0]["id"]
    async with sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Order)) == 1


async def test_orders_are_isolated(client, as_user):
    arax, nova, pack, lori = [await as_user(u) for u in ("arax", "nova", "pack", "lori")]
    await put(client, arax, await product_id(client, arax, "Sour cream 20%"), 10)
    [order] = (await checkout(client, arax)).json()["orders"]
    assert (await client.get(f"/orders/{order['id']}", headers=nova)).status_code == 404
    assert (await client.post(f"/orders/{order['id']}/confirm", headers=pack, json={})).status_code == 404
    # The buyer cannot do the seller's part
    assert (await client.post(f"/orders/{order['id']}/confirm", headers=arax, json={})).status_code == 404
    assert (await client.get("/orders", headers=nova)).json()["total"] == 0
    assert (await client.get("/orders", headers=lori)).json()["total"] == 1


async def test_events_go_to_outbox(client, as_user, sessionmaker):
    arax = await as_user("arax")
    await put(client, arax, await product_id(client, arax, "Lori cheese"), 12, offer=3100)
    await checkout(client, arax)
    async with sessionmaker() as s:
        topics = list(await s.scalars(select(OutboxEvent.topic).order_by(OutboxEvent.created_at)))
    assert topics[:3] == ["order.created", "order.offer_made", "order.offer_countered"]


async def test_next_step_saving_and_timeline(client, as_user):
    arax, lori = await as_user("arax"), await as_user("lori")
    await put(client, arax, await product_id(client, arax, "Lori cheese"), 12, offer=3100)
    [order] = (await checkout(client, arax)).json()["orders"]
    assert order["next_step"] == "answer_counter" and order["deadline"] is not None
    assert [e["type"] for e in order["events"]] == ["created", "offer_made", "offer_countered"]
    assert order["events"][-1]["auto"] is True

    line_id = order["lines"][0]["id"]
    r = await client.post(
        f"/orders/{order['id']}/negotiation/respond",
        headers=arax,
        json={"lines": [{"order_line_id": line_id, "decision": "accepted"}]},
    )
    body = r.json()
    assert body["saving_amd"] == (3500 - 3300) * 12
    assert body["next_step"] is None

    [item] = (await client.get("/orders", headers=lori)).json()["items"]
    assert (item["my_side"], item["next_step"], item["saving_amd"]) == ("seller", "confirm", 2400)
    assert item["preview"][0]["en"] == "Lori cheese"


# --- hints for both sides ---------------------------------------------------------------------------------


async def test_legacy_absolute_autopilot_reads_as_discounts(client, as_user):
    lori = await as_user("lori")
    cheese = next(
        p
        for p in (await client.get("/seller/products", headers=lori)).json()
        if p["name"]["en"] == "Lori cheese"
    )
    ap = cheese["autopilot"]
    # list 3 500: accept from 3 300 = -5.71%, floor 3 000 = -14.28% (rounded down)
    assert (ap["accept_discount_bp"], ap["max_discount_bp"], ap["share_hint"]) == (571, 1428, False)


async def test_cart_shows_rules_and_shared_hint(client, as_user):
    arax, lori = await as_user("arax"), await as_user("lori")
    cheese = await product_id(client, arax, "Lori cheese")
    cart = await put(client, arax, cheese, 12)
    [group] = cart["groups"]
    assert (cart["offer_max_rounds"], group["offer_response_hours"]) == (3, 24)
    assert group["lines"][0]["offer_hint_bp"] is None  # the seller did not share it

    product = next(
        p
        for p in (await client.get("/seller/products", headers=lori)).json()
        if p["name"]["en"] == "Lori cheese"
    )
    body = {k: v for k, v in product.items() if k not in ("id", "archived", "vat_rate_bp")}
    body["autopilot"] = {**product["autopilot"], "share_hint": True}
    r = await client.put(f"/seller/products/{product['id']}", headers=lori, json=body)
    assert r.status_code == 200, r.text
    line = (await client.get("/cart", headers=arax)).json()["groups"][0]["lines"][0]
    assert line["offer_hint_bp"] == 571

    # Saved as a percentage now: the autopilot still accepts 3 300 on a 3 500 list price
    await put(client, arax, cheese, 12, offer=3300)
    [order] = (await checkout(client, arax)).json()["orders"]
    assert order["negotiation"]["status"] == "agreed"


async def test_seller_sees_client_history_buyer_does_not(client, as_user):
    arax, lori = await as_user("arax"), await as_user("lori")
    cheese = await product_id(client, arax, "Lori cheese")
    await put(client, arax, cheese, 12, offer=3300)
    [first] = (await checkout(client, arax)).json()["orders"]
    assert first["client"] is None

    await put(client, arax, cheese, 12, offer=3100)
    [second] = (await checkout(client, arax)).json()["orders"]
    seen = (await client.get(f"/orders/{second['id']}", headers=lori)).json()
    c = seen["client"]
    assert (c["source"], c["orders"], c["negotiated_orders"], c["agreed_orders"]) == (
        "seller_invited",
        1,
        1,
        1,
    )
    assert c["avg_order_amd"] == first["total_amd"]
    assert c["avg_discount_bp"] == (3500 - 3300) * 10_000 // 3500
