"""Sign-up by TIN, verification by an operator, and order documents."""

import pytest

from app.config import get_settings
from app.services.documents import vat_in
from tests.conftest import login
from tests.test_flow import checkout, product_id, put

pytestmark = pytest.mark.usefixtures("seeded")

PDF = b"%PDF-1.4 demo extract\n"


@pytest.fixture(autouse=True)
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))


async def register(client, name="Tigran", email="tigran@example.am") -> dict:
    r = await client.post("/auth/dev-register", json={"name": name, "email": email})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload(client, headers, kind="state_registration", content=PDF, ctype="application/pdf"):
    return await client.post(
        "/verification/files",
        headers=headers,
        data={"kind": kind},
        files={"file": ("extract.pdf", content, ctype)},
    )


# --- registry lookup ---------------------------------------------------------------------------------------


async def test_lookup_by_tin(client):
    me = await register(client)
    r = await client.get("/registry/lookup", headers=me, params={"tin": "0274 4119"})
    body = r.json()
    assert (body["tin"], body["found"], body["provider"]) == ("02744119", True, "demo")
    assert "Sevan Fish" in body["company"]["legal_name"] and body["company"]["tax_regime"] == "vat"
    # A seeded company is already on Easo
    assert (await client.get("/registry/lookup", headers=me, params={"tin": "00000001"})).json()[
        "already_registered"
    ]
    r = await client.get("/registry/lookup", headers=me, params={"tin": "123"})
    assert r.status_code == 422 and r.json()["details"]["problems"][0]["code"] == "bad_tin"
    # Unknown TIN: manual entry
    assert (await client.get("/registry/lookup", headers=me, params={"tin": "99999999"})).json()[
        "found"
    ] is False


# --- seller sign-up and verification -------------------------------------------------------------------


async def test_seller_signs_up_gets_verified_and_trades(client):
    me = await register(client)
    assert (await client.get("/me", headers=me)).json()["memberships"] == []
    company = (await client.get("/registry/lookup", headers=me, params={"tin": "02744119"})).json()["company"]
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["seller"],
            "tin": company["tin"],
            "legal_name": company["legal_name"],
            "display_name": "Sevan Fish",
            "tax_regime": company["tax_regime"],
            "legal_address": company["legal_address"],
            "director_name": company["director_name"],
            "bank_account": "1570000000000100",
        },
    )
    assert r.status_code == 201, r.text
    org = r.json()
    assert (org["is_seller"], org["verification_status"], org["registry_source"]) == (True, "none", "demo")

    # Nothing uploaded yet
    r = await client.post("/verification/submit", headers=me)
    assert r.status_code == 422 and r.json()["details"]["problems"][0]["kind"] == "state_registration"
    # Only documents and images are accepted
    assert (await upload(client, me, content=b"MZ", ctype="application/x-msdownload")).status_code == 422
    f = (await upload(client, me)).json()
    await upload(client, me, kind="haccp")
    v = (await client.post("/verification/submit", headers=me)).json()
    assert v["organization"]["verification_status"] == "pending"
    assert v["required"] == ["state_registration"] and "haccp" in v["recommended"]
    assert len(v["files"]) == 2

    admin = await login(client, "admin@easo.local")
    queue = (await client.get("/admin/verification", headers=admin)).json()
    assert [q["organization"]["display_name"] for q in queue] == ["Dilijan Honey", "Sevan Fish"]
    # Approving the company needs the registration extract approved first
    r = await client.post(f"/admin/organizations/{org['id']}/review", headers=admin, json={"approve": True})
    assert r.status_code == 422
    assert (
        await client.post(f"/admin/files/{f['id']}/review", headers=admin, json={"approve": True})
    ).status_code == 200
    r = await client.post(
        f"/admin/organizations/{org['id']}/review", headers=admin, json={"approve": True, "note": "ok"}
    )
    assert (r.json()["status"], r.json()["verification_status"]) == ("active", "verified")
    # Non-admins cannot review
    assert (await client.get("/admin/verification", headers=me)).status_code == 403


async def test_files_are_private(client):
    me = await register(client)
    await client.post(
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
    f = (await upload(client, me)).json()
    assert (await client.get(f"/files/{f['id']}", headers=me)).content == PDF
    other = await login(client, "nova@easo.local")
    assert (await client.get(f"/files/{f['id']}", headers=other)).status_code == 404
    admin = await login(client, "admin@easo.local")
    assert (await client.get(f"/files/{f['id']}", headers=admin)).status_code == 200


async def test_buyer_signup_rules(client):
    me = await register(client)
    # Contacts are checked; the delivery address is added later (several, from the company cabinet)
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["buyer"],
            "tin": "02598301",
            "legal_name": "Cascade",
            "phone": "123",
            "email": "a@b",
        },
    )
    assert {(p["field"], p["code"]) for p in r.json()["details"]["problems"]} == {
        ("phone", "bad_phone"),
        ("email", "bad_email"),
    }
    # A TIN already on Easo
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["buyer"],
            "tin": "00000011",
            "legal_name": "X",
            "delivery_address": "Somewhere",
        },
    )
    assert (r.status_code, r.json()["code"]) == (409, "tin_taken")
    # A closed company from the registry
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["buyer"],
            "tin": "02611111",
            "legal_name": "Old",
            "delivery_address": "Somewhere",
        },
    )
    assert r.json()["details"]["problems"][0]["code"] == "company_inactive"
    # Buyers start trading at once; typed-by-hand requisites are marked manual
    r = await client.post(
        "/organizations",
        headers=me,
        json={
            "accept_terms": True,
            "roles": ["buyer"],
            "tin": "02598301",
            "legal_name": "Cascade Cafe (typed)",
            "delivery_address": "Tamanyan 3",
        },
    )
    assert (r.json()["status"], r.json()["registry_source"]) == ("active", "manual")


# --- documents ------------------------------------------------------------------------------------------


def test_vat_inside_inclusive_price():
    assert vat_in(42_000, 2000) == 7_000  # 42 000 incl. 20% VAT
    assert vat_in(12_000, 0) == 0


async def test_documents_follow_the_order(client):
    nova, pack = await login(client, "nova@easo.local"), await login(client, "pack@easo.local")
    await put(
        client, nova, await product_id(client, nova, "Paper cup 250 ml"), 10
    )  # 42 000, VAT payer seller
    [order] = (await checkout(client, nova)).json()["orders"]
    oid = order["id"]
    assert (await client.get(f"/orders/{oid}/documents", headers=nova)).json() == []

    await client.post(f"/orders/{oid}/confirm", headers=pack, json={})
    docs = (await client.get(f"/orders/{oid}/documents", headers=nova)).json()
    kinds = {d["kind"]: d for d in docs}
    assert set(kinds) == {"invoice", "delivery_note"}
    inv = kinds["invoice"]
    assert inv["number"].startswith("INV-") and inv["number"].endswith("-0001")
    p = inv["payload"]
    assert p["seller"]["tin"] == "00000002" and p["seller"]["bank_name"] == "Ardshinbank"
    assert p["buyer"]["legal_name"] == "Nova Bar LLC" and p["delivery_address"] == "Komitas Ave 5"
    # 42 000 goods + 1 000 delivery (below free delivery), VAT inside both: 7 000 + 167
    assert p["totals"]["total_amd"] == 43_000 and p["totals"]["vat_amd"] == 7_167
    assert p["lines"][0]["vat_rate_bp"] == 2000 and p["title"]["hy"] == "Հաշիվ վճարման համար"
    assert kinds["delivery_note"]["payload"]["transport"]["to"] == "Komitas Ave 5"

    # Issuing again changes nothing
    assert len((await client.post(f"/orders/{oid}/documents", headers=pack)).json()) == 2

    # SRC numbers: only the seller records them
    r = await client.post(
        f"/orders/{oid}/documents/src", headers=nova, json={"kind": "tax_invoice", "number": "X1"}
    )
    assert r.status_code == 404
    r = await client.post(
        f"/orders/{oid}/documents/src", headers=pack, json={"kind": "tax_invoice", "number": " 0012345678 "}
    )
    assert (r.json()["external_id"], r.json()["status"]) == ("0012345678", "issued")

    await client.post(f"/orders/{oid}/deliver", headers=pack)
    await client.post(
        f"/orders/{oid}/accept",
        headers=nova,
        json={"lines": [{"order_line_id": order["lines"][0]["id"], "qty": "9"}]},
    )
    act = next(
        d
        for d in (await client.get(f"/orders/{oid}/documents", headers=pack)).json()
        if d["kind"] == "acceptance_act"
    )
    assert act["number"].startswith("ACT-") and act["payload"]["lines"][0]["qty"] == "9"

    # Numbering continues per seller
    await put(client, nova, await product_id(client, nova, "Napkins 24x24"), 4)
    [o2] = (await checkout(client, nova)).json()["orders"]
    await client.post(f"/orders/{o2['id']}/confirm", headers=pack, json={})
    inv2 = next(
        d
        for d in (await client.get(f"/orders/{o2['id']}/documents", headers=pack)).json()
        if d["kind"] == "invoice"
    )
    assert inv2["number"].endswith("-0002")

    # Strangers see nothing
    arax = await login(client, "arax@easo.local")
    assert (await client.get(f"/documents/{inv['id']}", headers=arax)).status_code == 404
    assert (await client.get(f"/documents/{inv['id']}", headers=nova)).status_code == 200


async def test_non_vat_seller_note(client):
    arax, lori = await login(client, "arax@easo.local"), await login(client, "lori@easo.local")
    await put(client, arax, await product_id(client, arax, "Sour cream 20%"), 10)
    [order] = (await checkout(client, arax)).json()["orders"]
    await client.post(f"/orders/{order['id']}/confirm", headers=lori, json={})
    inv = next(
        d
        for d in (await client.get(f"/orders/{order['id']}/documents", headers=arax)).json()
        if d["kind"] == "invoice"
    )
    assert (inv["payload"]["vat_note"], inv["payload"]["totals"]["vat_amd"]) == ("not_vat_payer", 0)


# --- consent to the offer and the privacy policy -------------------------------------------------------


async def test_signup_needs_consent_and_records_it(client, sessionmaker):
    from sqlalchemy import select

    from app.domain import legal
    from app.models import Consent

    me = await register(client)
    body = {"roles": ["buyer"], "tin": "02598301", "legal_name": "Cascade Cafe LLC"}
    r = await client.post("/organizations", headers=me, json=body)
    assert ("accept_terms", "required") in {(p["field"], p["code"]) for p in r.json()["details"]["problems"]}
    assert sorted((await client.get("/me", headers=me)).json()["missing_consents"]) == ["privacy", "terms"]

    r = await client.post(
        "/organizations", headers={**me, "User-Agent": "test-browser"}, json={**body, "accept_terms": True}
    )
    assert r.status_code == 201, r.text
    assert (await client.get("/me", headers=me)).json()["missing_consents"] == []
    async with sessionmaker() as session:
        rows = (await session.scalars(select(Consent).where(Consent.organization_id == r.json()["id"]))).all()
    assert {(c.document, c.version) for c in rows} == set(legal.CURRENT_VERSIONS.items())
    assert {c.user_agent for c in rows} == {"test-browser"}


async def test_existing_user_accepts_a_new_version(client, monkeypatch):
    from app.domain import legal
    from app.models import LegalDoc

    me = await register(client)
    assert (await client.post("/auth/consent", headers=me)).status_code == 204
    assert (await client.post("/auth/consent", headers=me)).status_code == 204  # twice is fine
    assert (await client.get("/me", headers=me)).json()["missing_consents"] == []
    # A new offer text: everyone is asked again, the old acceptance stays on record
    monkeypatch.setitem(legal.CURRENT_VERSIONS, LegalDoc.terms, "2027-01-01")
    assert (await client.get("/me", headers=me)).json()["missing_consents"] == ["terms"]
    await client.post("/auth/consent", headers=me)
    assert (await client.get("/me", headers=me)).json()["missing_consents"] == []
