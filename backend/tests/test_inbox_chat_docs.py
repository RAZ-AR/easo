"""Notifications (bell, e-mail and Telegram copies), the order chat and the document register."""

import uuid

import pytest
from sqlalchemy import select

from app.models import Channel, Notification, User
from app.platform.clock import utcnow
from app.services import notifications as svc
from app.workers.outbox import dispatch_pending
from tests.conftest import login
from tests.test_flow import checkout, product_id, put

pytestmark = pytest.mark.usefixtures("seeded")


async def dispatch(sessionmaker) -> None:
    async with sessionmaker() as s:
        await dispatch_pending(s, utcnow())
        await s.commit()


async def bell(client, headers) -> dict:
    return (await client.get("/notifications", headers=headers)).json()


async def nova_offer_on_chanakh(client) -> tuple[dict, dict, dict]:
    nova, lori = await login(client, "nova@easo.local"), await login(client, "lori@easo.local")
    await put(client, nova, await product_id(client, nova, "Chanakh cheese"), 7, offer=2900)
    [order] = (await checkout(client, nova)).json()["orders"]
    return order, nova, lori


async def test_new_order_rings_the_sellers_bell_not_the_buyers(client, sessionmaker):
    order, nova, lori = await nova_offer_on_chanakh(client)
    await dispatch(sessionmaker)
    seller_bell = await bell(client, lori)
    assert seller_bell["unread"] == 1
    [n] = seller_bell["items"]
    assert (n["type"], n["order_id"], n["payload"]["counterparty"]) == (
        "order_created",
        order["id"],
        "Bar Nova",
    )
    # The seller's manager hears too; the buyer who placed it does not
    assert (await bell(client, await login(client, "manager@easo.local")))["unread"] == 1
    assert (await bell(client, nova))["unread"] == 0

    # Retrying the same outbox events never notifies twice
    async with sessionmaker() as s:
        from app.models import OutboxEvent

        for e in await s.scalars(select(OutboxEvent)):
            await svc.fan_out(s, e)
        await s.commit()
    assert (await bell(client, lori))["unread"] == 1

    r = await client.post("/notifications/read", headers=lori, json={})
    assert r.status_code == 204
    assert (await bell(client, lori))["unread"] == 0


async def test_counter_offer_notifies_the_buyer(client, sessionmaker):
    order, nova, lori = await nova_offer_on_chanakh(client)
    line = order["lines"][0]["id"]
    r = await client.post(
        f"/orders/{order['id']}/negotiation/respond",
        headers=lori,
        json={"lines": [{"order_line_id": line, "decision": "countered", "price_amd": 3100}]},
    )
    assert r.status_code == 200, r.text
    await dispatch(sessionmaker)
    types = [n["type"] for n in (await bell(client, nova))["items"]]
    assert types == ["offer_countered"]


async def test_order_chat(client, sessionmaker):
    order, nova, lori = await nova_offer_on_chanakh(client)
    oid = order["id"]
    r = await client.post(f"/orders/{oid}/messages", headers=nova, json={"text": "  Можно к 9 утра?  "})
    assert r.status_code == 201, r.text
    assert (r.json()["text"], r.json()["side"], r.json()["mine"]) == ("Можно к 9 утра?", "buyer", True)
    assert (await client.post(f"/orders/{oid}/messages", headers=nova, json={"text": " "})).status_code == 422

    await dispatch(sessionmaker)
    seller_view = (await client.get(f"/orders/{oid}", headers=lori)).json()
    assert seller_view["unread_messages"] == 1
    msgs = (await client.get(f"/orders/{oid}/messages", headers=lori)).json()
    assert [(m["side"], m["mine"]) for m in msgs] == [("buyer", False)]
    # Opening the chat clears its bell entries
    assert (await client.get(f"/orders/{oid}", headers=lori)).json()["unread_messages"] == 0

    # Strangers see nothing
    arax = await login(client, "arax@easo.local")
    assert (await client.get(f"/orders/{oid}/messages", headers=arax)).status_code == 404
    assert (
        await client.post(f"/orders/{oid}/messages", headers=arax, json={"text": "hi"})
    ).status_code == 404


async def test_email_copies_skip_demo_addresses_and_are_sent_once(client, sessionmaker, monkeypatch):
    order, nova, lori = await nova_offer_on_chanakh(client)
    async with sessionmaker() as s:
        anahit = await s.scalar(select(User).where(User.email == "lori@easo.local"))
        real = User(name="Real owner", email="owner@lori.am")
        s.add(real)
        await s.flush()
        from app.models import Membership, Role

        lori_org = (await s.scalar(select(Membership).where(Membership.user_id == anahit.id))).organization_id
        s.add(Membership(user_id=real.id, organization_id=lori_org, role=Role.manager))
        await s.commit()
    await dispatch(sessionmaker)

    sent: list[tuple[str, str, str]] = []

    class FakeMailer:
        name = "fake"

        async def send(self, to, subject, text):
            sent.append((to, subject, text))

    monkeypatch.setattr(svc, "get_mailer", lambda: FakeMailer())
    async with sessionmaker() as s:
        assert await svc.deliver_pending(s, utcnow()) == 1
        await s.commit()
        assert await svc.deliver_pending(s, utcnow()) == 0
    [(to, subject, text)] = sent
    assert to == "owner@lori.am" and order["number"] in subject and f"/orders/{order['id']}" in text


async def test_notification_settings_and_telegram_link(client, sessionmaker):
    nova = await login(client, "nova@easo.local")
    s0 = (await client.get("/me/notifications", headers=nova)).json()
    assert (s0["email"], s0["telegram"], s0["telegram_linked"]) == (True, True, False)
    r = await client.put("/me/notifications", headers=nova, json={"email": False, "telegram": True})
    assert r.json()["email"] is False

    url = (await client.post("/me/telegram/link", headers=nova)).json()["url"]
    token = url.split("start=")[1]
    assert len(token) <= 64
    async with sessionmaker() as s:
        user = await svc.link_telegram(s, token, 777)
        await s.commit()
        assert user.telegram_id == 777
    assert (await client.get("/me/notifications", headers=nova)).json()["telegram_linked"] is True

    # A tampered or old token is refused
    from app.platform.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        svc.read_telegram_link_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
    old = svc.telegram_link_token(uuid.uuid4(), now_s=1_000_000)
    with pytest.raises(ValidationFailed):
        svc.read_telegram_link_token(old)


def _signed_widget(bot_token: str, **user) -> dict:
    """A Telegram Login Widget payload signed like Telegram does."""
    import hashlib
    import hmac
    import time

    data = {**user, "auth_date": int(time.time())}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(bot_token.encode()).digest()
    return {**data, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()}


async def test_telegram_sign_in_finds_user_who_linked_the_bot(client, sessionmaker, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "telegram_bot_token", "123:test")
    nova = await login(client, "nova@easo.local")
    nova_id = (await client.get("/me", headers=nova)).json()["id"]
    token = (await client.post("/me/telegram/link", headers=nova)).json()["url"].split("start=")[1]
    async with sessionmaker() as s:
        await svc.link_telegram(s, token, 4242)
        await s.commit()

    r = await client.post("/auth/telegram", json=_signed_widget("123:test", id=4242, first_name="Nova"))
    assert r.status_code == 200, r.text
    me = (await client.get("/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me["id"] == nova_id and me["memberships"]

    # A Telegram account nobody linked is a new user
    r = await client.post("/auth/telegram", json=_signed_widget("123:test", id=5151, first_name="Ani"))
    other = (await client.get("/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert other["id"] != nova_id and other["memberships"] == []


async def test_document_register(client, sessionmaker):
    nova, pack = await login(client, "nova@easo.local"), await login(client, "pack@easo.local")
    await put(client, nova, await product_id(client, nova, "Paper cup 250 ml"), 10)
    [order] = (await checkout(client, nova)).json()["orders"]
    await client.post(f"/orders/{order['id']}/confirm", headers=pack, json={})

    sales = (await client.get("/documents", headers=pack)).json()
    assert {d["kind"] for d in sales["items"]} == {"invoice", "delivery_note"}
    assert all(
        d["counterparty"] == "Bar Nova" and d["order_number"] == order["number"] for d in sales["items"]
    )
    purchases = (await client.get("/documents", headers=nova)).json()
    assert purchases["total"] == 2 and purchases["items"][0]["counterparty"] == "Yerevan Pack"

    only_invoices = (await client.get("/documents", headers=pack, params={"kind": "invoice"})).json()
    assert [d["kind"] for d in only_invoices["items"]] == ["invoice"]
    number = only_invoices["items"][0]["number"]
    assert (await client.get("/documents", headers=pack, params={"q": number})).json()["total"] == 1
    assert (await client.get("/documents", headers=pack, params={"q": "Nova"})).json()["total"] == 2
    future = (await client.get("/documents", headers=pack, params={"date_from": "2099-01-01"})).json()
    assert future["total"] == 0
    # Another seller sees none of them
    assert (await client.get("/documents", headers=await login(client, "lori@easo.local"))).json()[
        "total"
    ] == 0
    # The bell is per user and app-only
    async with sessionmaker() as s:
        assert not await s.scalar(select(Notification.id).where(Notification.channel == Channel.telegram))
