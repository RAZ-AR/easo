"""The product card: characteristics by category, photos (re-encoded to WebP), certificates."""

import io

import pytest
from PIL import Image

from app.config import get_settings
from app.domain import product_specs as ps
from tests.conftest import login
from tests.test_flow import product_id

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture(autouse=True)
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))


def photo(size=(2400, 1800), color=(220, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


PDF = b"%PDF-1.4 declaration\n"


def test_specs_follow_the_category():
    meat, problems = ps.clean(
        {
            "thermal": "chilled",
            "animal": "beef",
            "fat": "12,5",
            "country": "AM",
            "halal": True,
            "roast": "dark",
        },
        "meat",
        False,
    )
    # roast is a coffee characteristic: dropped for meat
    assert (meat, problems) == (
        {"thermal": "chilled", "animal": "beef", "fat": 12.5, "country": "AM", "halal": True},
        [],
    )
    _, problems = ps.clean({"thermal": "warm-ish", "shelf_life": "a week"}, "meat", False)
    assert {p["code"] for p in problems} == {"bad_choice", "bad_number"}
    assert ps.clean({"producer": "X"}, None, True) == ({}, [])  # a service has no characteristics
    assert {s.code for s in ps.specs_for("disposables", False)} >= {
        "material",
        "volume",
        "per_pack",
        "barcode",
    }


async def test_seller_fills_the_card_and_buyers_see_it(client):
    lori, arax = await login(client, "lori@easo.local"), await login(client, "arax@easo.local")
    cheese = await product_id(client, arax, "Lori cheese")
    mine = next(p for p in (await client.get("/seller/products", headers=lori)).json() if p["id"] == cheese)
    body = {
        k: v for k, v in mine.items() if k not in ("id", "archived", "vat_rate_bp", "images", "documents")
    }
    body["specs"] = {
        "milk": "cow",
        "fat": 45,
        "storage": "chilled",
        "shelf_life": 30,
        "producer": "Lori Panir",
        "thermal": "frozen",
    }
    r = await client.put(f"/seller/products/{cheese}", headers=lori, json=body)
    assert r.status_code == 200, r.text
    assert (
        "thermal" not in r.json()["specs"] and r.json()["specs"]["fat"] == 45
    )  # meat-only spec dropped for cheese

    # Two photos: re-encoded to WebP with a thumbnail; the second becomes the main one
    for color in ((200, 30, 30), (30, 30, 200)):
        r = await client.post(
            f"/seller/products/{cheese}/images",
            headers=lori,
            files={"file": ("p.jpg", photo(color=color), "image/jpeg")},
        )
        assert r.status_code == 200, r.text
    images = r.json()["images"]
    assert len(images) == 2 and images[0]["url"].endswith(".webp") and images[0]["thumb"].endswith("-s.webp")
    second = images[1]["url"]
    images = (await client.post(f"/seller/products/{cheese}/images/1/main", headers=lori)).json()["images"]
    assert images[0]["url"] == second

    # The seller hides the second photo: buyers only see the main one, the seller sees both
    r = await client.put(f"/seller/products/{cheese}/images/1/shown", headers=lori, json={"shown": False})
    assert [i["shown"] for i in r.json()["images"]] == [True, False]
    hidden = r.json()["images"][1]["url"]
    card = (await client.get(f"/products/{cheese}")).json()
    assert [i["url"] for i in card["images"]] == [second]
    # Making a hidden photo the main one shows it again
    r = await client.post(f"/seller/products/{cheese}/images/1/main", headers=lori)
    assert r.json()["images"][0] == {"url": hidden, "thumb": hidden[:-5] + "-s.webp", "shown": True}
    images = (await client.post(f"/seller/products/{cheese}/images/1/main", headers=lori)).json()["images"]
    assert images[0]["url"] == second

    big = (await client.get(images[0]["url"])).content
    small = (await client.get(images[0]["thumb"])).content
    assert Image.open(io.BytesIO(big)).size == (1600, 1200) and Image.open(io.BytesIO(small)).size == (
        480,
        360,
    )
    bad = await client.post(
        f"/seller/products/{cheese}/images",
        headers=lori,
        files={"file": ("x.jpg", b"not an image", "image/jpeg")},
    )
    assert bad.json()["details"]["problems"][0]["code"] == "bad_image"

    # A declaration of conformity
    r = await client.post(
        f"/seller/products/{cheese}/documents",
        headers=lori,
        data={"kind": "declaration", "number": "EAEU N RU D-AM.123", "valid_until": "2027-12-31"},
        files={"file": ("decl.pdf", PDF, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    doc = r.json()["documents"][0]
    assert (doc["kind"], doc["number"], doc["name"]) == ("declaration", "EAEU N RU D-AM.123", "decl.pdf")

    # The catalog shows the main photo and that a certificate exists
    listed = next(p for p in (await client.get("/products", params={"q": "Lori cheese"})).json()["items"])
    assert listed["has_documents"] and listed["images"][0]["url"] == second

    # The card: a guest sees the goods, not the seller, and cannot open the certificate
    guest = (await client.get(f"/products/{cheese}")).json()
    assert (
        guest["seller"] is None
        and guest["specs"]["milk"] == "cow"
        and guest["documents"][0]["kind"] == "declaration"
    )
    assert "name" not in guest["documents"][0] or guest["documents"][0]["name"] is None
    assert (await client.get(f"/products/{cheese}/documents/{doc['id']}")).status_code == 401
    card = (await client.get(f"/products/{cheese}", headers=arax)).json()
    assert card["seller"]["display_name"] and card["seller_min_order_amd"] is not None
    file = await client.get(f"/products/{cheese}/documents/{doc['id']}", headers=arax)
    assert (file.status_code, file.content) == (200, PDF)

    # Cleanup works and stays inside the seller's own products
    pack = await login(client, "pack@easo.local")
    assert (
        await client.delete(f"/seller/products/{cheese}/documents/{doc['id']}", headers=pack)
    ).status_code == 404
    r = await client.delete(f"/seller/products/{cheese}/documents/{doc['id']}", headers=lori)
    assert r.json()["documents"] == []
    r = await client.delete(f"/seller/products/{cheese}/images/0", headers=lori)
    assert len(r.json()["images"]) == 1
    assert (await client.get(second)).status_code == 404


async def test_spec_definitions_and_media_paths(client):
    d = (await client.get("/product-specs")).json()
    thermal = next(s for s in d["specs"] if s["code"] == "thermal")
    assert thermal["options"]["chilled"]["ru"] == "Охлаждённое" and thermal["categories"] == ["meat"]
    assert d["doc_kinds"]["veterinary"]["ru"] == "Ветеринарное свидетельство"
    assert (
        await client.get("/media/products/00000000-0000-0000-0000-000000000000/..%2F..%2Fsecret")
    ).status_code == 404
