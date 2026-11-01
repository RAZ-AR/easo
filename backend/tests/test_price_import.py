"""Price list import: reading cells, guessing columns, the plan, and the API on the demo data."""

import io
import uuid
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.domain import price_import as imp
from app.models import Unit
from tests.conftest import login

F = imp.Field


# --- cells and columns -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "price"),
    [
        ("3 500", 3500),
        ("3500.00", 3500),
        ("3,500 ֏", 3500),
        ("3 500,50", 3501),
        ("1.234,4", 1234),
        (4200, 4200),
        (99.6, 100),
        ("", None),
        ("цена", None),
    ],
)
def test_parse_price(raw, price):
    assert imp.parse_price(raw) == price


def test_parse_unit_and_stock_words():
    assert [imp.parse_unit(u) for u in ("кг", "Kg.", "կգ", "шт", "уп", "литр", "?")] == [
        Unit.kg,
        Unit.kg,
        Unit.kg,
        Unit.pcs,
        Unit.pack,
        Unit.l,
        None,
    ]
    assert [imp.parse_bool(v) for v in ("да", "Нет", "1", "այո", "maybe")] == [True, False, True, True, None]


def test_guess_mapping_from_armsoft_like_headers():
    ru = imp.guess_mapping(
        ["Код", "Наименование", "Ед. изм.", "Цена", "Остаток"], [["1", "Сыр", "кг", "1", "2"]]
    )
    assert ru == {0: F.sku, 1: F.name_ru, 2: F.unit, 3: F.price, 4: F.stock_qty}
    hy = imp.guess_mapping(["Կոդ", "Անվանում", "Չափման միավոր", "Գին"], [["1", "Լոռի պանիր", "կգ", "1"]])
    assert hy == {0: F.sku, 1: F.name_hy, 2: F.unit, 3: F.price}
    two = imp.guess_mapping(["Name (en)", "Название рус", "Price"], [["Cheese", "Сыр", "1"]])
    assert two == {0: F.name_en, 1: F.name_ru, 2: F.price}


# --- the plan ----------------------------------------------------------------------------------------------


CHEESE = imp.Existing(
    id=uuid.uuid4(),
    sku="L-1",
    name={"ru": "Сыр лори", "en": "Lori cheese"},
    price_amd=3500,
    unit=Unit.kg,
    in_stock=True,
    min_qty=Decimal(1),
    pack_size=None,
    archived=False,
    top_tier_price_amd=3350,
)
MILK = imp.Existing(
    id=uuid.uuid4(),
    sku=None,
    name={"ru": "Мацун"},
    price_amd=900,
    unit=Unit.l,
    in_stock=True,
    min_qty=Decimal(1),
    pack_size=None,
    archived=False,
)
MAP = {0: F.sku, 1: F.name_ru, 2: F.price, 3: F.unit}


def rows(*cells):
    return [r for i, c in enumerate(cells) if (r := imp.read_row(i + 2, list(c), MAP))]


def test_plan_creates_updates_and_reports():
    items = imp.plan(
        rows(
            ("L-1", "Сыр лори", "3 600", "кг"),  # price up
            ("L-1", "Сыр лори", "3 600", "кг"),  # same code twice
            ("M-7", "Мацун", "900", "л"),  # first import: matched by name, the code is attached
            ("N-1", "Сметана", "1 800", ""),  # new, unit from the default
            ("N-2", "", "500", "шт"),  # new without a name
            ("N-3", "Тан", "abc", "л"),  # bad price
            ("", "", "", ""),  # empty row is skipped
        ),
        [CHEESE, MILK],
        default_unit=Unit.kg,
    )
    got = [(i.line, i.action, i.problems) for i in items]
    assert got == [
        (2, imp.Action.update, []),
        (3, imp.Action.error, ["duplicate_sku", "duplicate_row"]),
        (4, imp.Action.update, []),
        (5, imp.Action.create, []),
        (6, imp.Action.error, ["no_name"]),
        (7, imp.Action.error, ["bad_price"]),
    ]
    assert [(c.field, c.old, c.new) for c in items[0].changes] == [("price_amd", 3500, 3600)]
    assert [(c.field, c.new) for c in items[2].changes] == [("sku", "M-7")]


def test_plan_warns_and_marks_missing_out_of_stock():
    [cheese, milk] = imp.plan(rows(("L-1", "", "1 000", "")), [CHEESE, MILK], missing_out_of_stock=True)
    assert cheese.action == imp.Action.update
    assert cheese.warnings == ["price_jump", "tiers_above_price"]
    assert (milk.action, milk.product_id) == (imp.Action.out_of_stock, MILK.id)
    [same] = imp.plan(rows(("L-1", "Сыр лори", "3500", "кг")), [CHEESE])
    assert same.action == imp.Action.same


# --- API ---------------------------------------------------------------------------------------------------


CSV = "Код;Наименование;Ед. изм.;Цена;Остаток\nL-1;Сыр лори;кг;3 600;12\nL-9;Брынза;кг;2 900;0\n"


async def _upload(client, headers, data: bytes, name: str):
    return await client.post("/seller/import/parse", headers=headers, files={"file": (name, data)})


@pytest.mark.usefixtures("seeded")
async def test_import_csv_end_to_end(client):
    lori = await login(client, "lori@easo.local")
    r = await _upload(client, lori, CSV.encode("cp1251"), "price.csv")
    assert r.status_code == 200, r.text
    parsed = r.json()
    assert parsed["mapping"] == {"0": "sku", "1": "name_ru", "2": "unit", "3": "price", "4": "stock_qty"}
    body = {"mapping": parsed["mapping"], "rows": parsed["rows"]}

    preview = (await client.post("/seller/import/preview", headers=lori, json=body)).json()
    # "Сыр лори" in the demo has no code yet: matched by name, gets L-1 and the new price
    assert (preview["counts"]["update"], preview["counts"]["create"]) == (1, 1)
    upd = next(i for i in preview["items"] if i["action"] == "update")
    assert {c["field"] for c in upd["changes"]} == {"price_amd", "sku"}

    applied = (await client.post("/seller/import/apply", headers=lori, json=body)).json()
    assert applied["counts"]["create"] == 1
    again = (await client.post("/seller/import/preview", headers=lori, json=body)).json()
    assert again["counts"]["same"] == 2  # the same file twice changes nothing

    mine = {p["sku"]: p for p in (await client.get("/seller/products", headers=lori)).json()}
    assert (mine["L-1"]["base_price_amd"], mine["L-9"]["in_stock"], mine["L-9"]["unit"]) == (
        3600,
        False,
        "kg",
    )


@pytest.mark.usefixtures("seeded")
async def test_import_xlsx_and_errors(client):
    lori = await login(client, "lori@easo.local")
    book = Workbook()
    sheet = book.active
    sheet.append(["Կոդ", "Անվանում", "Գին", "Միավոր"])
    sheet.append([1001, "Չանախ", 3300, "կգ"])
    buf = io.BytesIO()
    book.save(buf)
    parsed = (await _upload(client, lori, buf.getvalue(), "գներ.xlsx")).json()
    assert parsed["rows"][0][0] == 1001
    assert parsed["mapping"] == {"0": "sku", "1": "name_hy", "2": "price", "3": "unit"}
    applied = (
        await client.post(
            "/seller/import/apply", headers=lori, json={"mapping": parsed["mapping"], "rows": parsed["rows"]}
        )
    ).json()
    assert applied["counts"]["create"] == 1 and applied["items"][0]["sku"] == "1001"

    r = await _upload(client, lori, b"old", "price.xls")
    assert r.json()["details"]["problems"] == [{"field": "file", "code": "old_excel"}]
    r = await client.post(
        "/seller/import/preview", headers=lori, json={"mapping": {"0": "price"}, "rows": []}
    )
    assert r.json()["details"]["problems"][0]["code"] == "map_sku_or_name"
    arax = await login(client, "arax@easo.local")
    assert (await _upload(client, arax, CSV.encode(), "price.csv")).status_code == 403
