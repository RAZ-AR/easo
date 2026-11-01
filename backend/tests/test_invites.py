"""Supplier links: the public page, joining as an own client (0% commission), sign-up through a link."""

import pytest
from sqlalchemy import select

from app.models import ClientLink, ClientSource, Organization
from tests.test_flow import as_user  # noqa: F401  (fixture)
from tests.test_roles_auth import email_login

pytestmark = pytest.mark.usefixtures("seeded")


def seller(headers: dict) -> dict:
    return {**headers, "X-Mode": "seller"}


async def link_of(sessionmaker, seller_name: str, buyer_name: str) -> ClientLink | None:
    async with sessionmaker() as s:
        ids = {
            o.display_name: o.id
            for o in await s.scalars(
                select(Organization).where(Organization.display_name.in_([seller_name, buyer_name]))
            )
        }
        return await s.scalar(
            select(ClientLink).where(
                ClientLink.seller_id == ids[seller_name], ClientLink.buyer_id == ids[buyer_name]
            )
        )


async def invite_code(client, headers) -> str:
    r = await client.get("/seller/invite", headers=seller(headers))
    assert r.status_code == 200, r.text
    return r.json()["code"]


async def test_seller_link_is_stable_and_can_be_replaced(client, as_user):  # noqa: F811
    lori = await as_user("lori")
    first = (await client.get("/seller/invite", headers=seller(lori))).json()
    assert first["url"].endswith(f"/s/{first['code']}") and first["invited_clients"] == 1  # Arax, seeded
    assert (await client.get("/seller/invite", headers=seller(lori))).json()["code"] == first["code"]

    qr = await client.get(f"/s/{first['code']}/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/svg+xml" and b"<svg" in qr.content

    new = (await client.post("/seller/invite/rotate", headers=seller(lori))).json()
    assert new["code"] != first["code"]
    assert (await client.get(f"/s/{first['code']}")).status_code == 404
    assert (await client.get(f"/s/{new['code'].lower()}")).status_code == 200  # typed by hand


async def test_guest_sees_the_supplier_page_and_its_catalog(client, as_user):  # noqa: F811
    lori = await as_user("lori")
    await client.put(
        "/seller/settings",
        headers=seller(lori),
        json={**(await client.get("/seller/settings", headers=seller(lori))).json(),
              "description": {"ru": "  Сыры из Лори  ", "fr": "ignored"}},
    )  # fmt: skip
    code = await invite_code(client, lori)

    page = (await client.get(f"/s/{code}")).json()
    assert page["display_name"] == "Լոռի Պանիր" and page["verified"] and page["client"] is None
    assert page["products"] > 0 and page["description"] == {"ru": "Сыры из Лори"}

    items = (await client.get("/products", params={"invite": code, "limit": 100})).json()["items"]
    assert len(items) == page["products"]
    assert {i["seller"]["display_name"] for i in items} == {"Լոռի Պանիր"}  # named, even for a guest
    plain = (await client.get("/products", params={"limit": 100})).json()["items"]
    assert all(i["seller"] is None for i in plain)  # the open catalog still hides sellers
    assert (await client.get("/products", params={"invite": "NOSUCHCODE"})).status_code == 404


async def test_buyer_joins_as_own_client(client, as_user, sessionmaker):  # noqa: F811
    code = await invite_code(client, await as_user("lori"))
    nova = await as_user("nova")
    assert (await client.get(f"/s/{code}", headers=nova)).json()["client"] is False

    r = await client.post(f"/s/{code}/join", headers=nova)
    assert (r.status_code, r.json()["status"]) == (200, "joined")
    assert (await client.post(f"/s/{code}/join", headers=nova)).json()["status"] == "already"
    assert (await client.get(f"/s/{code}", headers=nova)).json()["client"] is True
    link = await link_of(sessionmaker, "Լոռի Պանիր", "Bar Nova")
    assert link.source == ClientSource.seller_invited and link.invite_id is not None

    clients = (await client.get("/seller/clients", headers=seller(await as_user("lori")))).json()
    assert {c["display_name"]: c["source"] for c in clients} == {
        "Cafe Arax": "seller_invited",
        "Bar Nova": "seller_invited",
    }


async def test_a_platform_client_stays_a_platform_client(client, as_user, sessionmaker):  # noqa: F811
    code = await invite_code(client, await as_user("pack"))
    r = await client.post(f"/s/{code}/join", headers=await as_user("nova"))
    assert r.json()["status"] == "already"
    assert (await link_of(sessionmaker, "Yerevan Pack", "Bar Nova")).source == ClientSource.platform


async def test_a_company_cannot_join_its_own_link(client, as_user):  # noqa: F811
    baker = await as_user("baker")  # Ararat Bakery buys and sells
    code = await invite_code(client, baker)
    r = await client.post(f"/s/{code}/join", headers=baker)
    assert (r.status_code, r.json()["code"]) == (403, "own_invite")


async def test_unverified_supplier_page_shows_no_products(client, as_user):  # noqa: F811
    code = await invite_code(client, await as_user("honey"))  # Dilijan Honey waits for the operator
    page = (await client.get(f"/s/{code}")).json()
    assert (page["verified"], page["products"]) == (False, 0)


async def test_sign_up_through_a_link(client, as_user, sessionmaker):  # noqa: F811
    code = await invite_code(client, await as_user("lori"))
    body = {"accept_terms": True, "roles": ["buyer"], "legal_name": "Cascade Cafe LLC"}

    me = await email_login(client, "owner@cascade.am")
    r = await client.post("/organizations", headers=me, json={**body, "tin": "02598301", "invite_code": code})
    assert r.status_code == 201, r.text
    assert (
        await link_of(sessionmaker, "Լոռի Պանիր", "Cascade Cafe LLC")
    ).source == ClientSource.seller_invited

    # A stale link does not stop the sign-up
    other = await email_login(client, "owner@dolmama.am")
    r = await client.post(
        "/organizations",
        headers=other,
        json={**body, "legal_name": "Dolmama LLC", "tin": "00012345", "invite_code": "GONE1234"},
    )
    assert r.status_code == 201, r.text
