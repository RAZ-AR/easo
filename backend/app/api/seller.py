"""Seller cabinet API: products and delivery terms. Acts in seller mode whatever the app's current mode is."""

import uuid
from dataclasses import asdict
from datetime import date, time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Form, UploadFile
from pydantic import BaseModel

from app.auth.deps import CurrentActor, Session
from app.core.spreadsheet import UnreadableFile, read_table
from app.domain import price_import as imp
from app.models import OfferRule, Product, ProductDocKind, Unit
from app.platform.clock import utcnow
from app.platform.errors import ValidationFailed
from app.schemas import ORM, ImageOut, ProductDocOut, TierOut
from app.services import price_import as import_svc
from app.services import product_media as media
from app.services import seller_catalog as svc

router = APIRouter(prefix="/seller", tags=["seller"])


class TierIn(BaseModel):
    min_qty: Decimal
    price_amd: int


class AutopilotIO(BaseModel):
    """Discounts in basis points from the line price (300 = 3%)."""

    enabled: bool = True
    accept_discount_bp: int | None = None
    max_discount_bp: int | None = None
    auto_counter: bool = True
    min_line_amd: int | None = None
    share_hint: bool = False


class ProductIn(BaseModel):
    name: dict[str, str]
    unit: Unit
    base_price_amd: int
    category_id: uuid.UUID | None = None
    sku: str | None = None
    description: dict[str, str] | None = None
    pack_size: str | None = None
    vat_included: bool = True
    min_qty: Decimal = Decimal(1)
    qty_step: Decimal = Decimal(1)
    in_stock: bool = True
    allow_offers: bool = False
    is_service: bool = False
    specs: dict = {}
    tiers: list[TierIn] = []
    autopilot: AutopilotIO | None = None


class SellerProductOut(ORM):
    id: uuid.UUID
    sku: str | None
    category_id: uuid.UUID | None
    name: dict[str, str]
    description: dict[str, str] | None
    unit: Unit
    pack_size: str | None
    base_price_amd: int
    vat_included: bool
    vat_rate_bp: int
    min_qty: Decimal
    qty_step: Decimal
    in_stock: bool
    allow_offers: bool
    is_service: bool
    archived: bool
    specs: dict
    images: list[ImageOut]
    documents: list[ProductDocOut]
    tiers: list[TierOut]
    autopilot: AutopilotIO | None


class ZoneIO(BaseModel):
    district: str
    weekdays: list[int]


class SettingsIO(BaseModel):
    min_order_amd: int = 0
    cutoff_time: time = time(16, 0)
    lead_days: int = 1
    delivery_fee_amd: int = 0
    free_delivery_from_amd: int | None = None
    offer_response_hours: int = 24
    small_order_fee_amd: int | None = None
    window_from: time = time(9, 0)
    window_to: time = time(18, 0)
    slot_hours: int = 0
    express_fee_amd: int | None = None
    express_until: time = time(12, 0)
    express_hours: int = 3
    express_min_amd: int = 0
    zones: list[ZoneIO] = []
    description: dict[str, str] | None = None


def _out(p: Product, rule: OfferRule | None) -> SellerProductOut:
    return SellerProductOut(
        id=p.id,
        sku=p.sku,
        category_id=p.category_id,
        name=p.name,
        description=p.description,
        unit=p.unit,
        pack_size=p.pack_size,
        base_price_amd=p.base_price_amd,
        vat_included=p.vat_included,
        vat_rate_bp=p.vat_rate_bp,
        min_qty=p.min_qty,
        qty_step=p.qty_step,
        in_stock=p.in_stock,
        allow_offers=p.allow_offers,
        is_service=p.is_service,
        archived=p.archived_at is not None,
        specs=p.specs or {},
        images=[ImageOut(**i) for i in media.image_urls(p, public=False)],
        documents=[
            ProductDocOut(
                id=d.id, kind=d.kind, number=d.number, valid_until=d.valid_until, name=d.original_name
            )
            for d in p.documents
        ],
        tiers=[
            TierOut(min_qty=t.min_qty, price_amd=t.price_amd)
            for t in sorted(p.tiers, key=lambda t: t.min_qty)
        ],
        autopilot=AutopilotIO(**asdict(svc.autopilot_out(rule, p.base_price_amd))) if rule else None,
    )


def _data(body: ProductIn) -> svc.ProductIn:
    return svc.ProductIn(
        **body.model_dump(exclude={"tiers", "autopilot"}),
        tiers=[svc.TierIn(t.min_qty, t.price_amd) for t in body.tiers],
        autopilot=svc.AutopilotIn(**body.autopilot.model_dump()) if body.autopilot else None,
    )


async def _reload(session: Session, actor: CurrentActor, product_id: uuid.UUID) -> SellerProductOut:
    for p, rule in await svc.list_own(session, actor) + await svc.list_own(session, actor, archived=True):
        if p.id == product_id:
            await session.refresh(p, ["tiers", "documents"])
            return _out(p, rule)
    raise AssertionError("product vanished")


@router.get("/products", response_model=list[SellerProductOut])
async def list_products(session: Session, actor: CurrentActor, archived: bool = False):
    return [_out(p, r) for p, r in await svc.list_own(session, actor, archived)]


@router.post("/products", response_model=SellerProductOut, status_code=201)
async def create_product(body: ProductIn, session: Session, actor: CurrentActor):
    product = await svc.create(session, actor, _data(body))
    await session.commit()
    return await _reload(session, actor, product.id)


@router.put("/products/{product_id}", response_model=SellerProductOut)
async def update_product(product_id: uuid.UUID, body: ProductIn, session: Session, actor: CurrentActor):
    await svc.update(session, actor, product_id, _data(body))
    await session.commit()
    return await _reload(session, actor, product_id)


@router.post("/products/{product_id}/archive", response_model=SellerProductOut)
async def archive_product(product_id: uuid.UUID, session: Session, actor: CurrentActor):
    await svc.set_archived(session, actor, product_id, True, utcnow())
    await session.commit()
    return await _reload(session, actor, product_id)


@router.post("/products/{product_id}/restore", response_model=SellerProductOut)
async def restore_product(product_id: uuid.UUID, session: Session, actor: CurrentActor):
    await svc.set_archived(session, actor, product_id, False, utcnow())
    await session.commit()
    return await _reload(session, actor, product_id)


# --- photos and documents of a product ----------------------------------------------------------------------


@router.post("/products/{product_id}/images", response_model=SellerProductOut)
async def add_image(product_id: uuid.UUID, file: UploadFile, session: Session, actor: CurrentActor):
    await media.add_image(session, actor, product_id, await file.read(), file.content_type or "")
    await session.commit()
    return await _reload(session, actor, product_id)


@router.delete("/products/{product_id}/images/{index}", response_model=SellerProductOut)
async def remove_image(product_id: uuid.UUID, index: int, session: Session, actor: CurrentActor):
    await media.remove_image(session, actor, product_id, index)
    await session.commit()
    return await _reload(session, actor, product_id)


@router.post("/products/{product_id}/images/{index}/main", response_model=SellerProductOut)
async def main_image(product_id: uuid.UUID, index: int, session: Session, actor: CurrentActor):
    await media.make_main(session, actor, product_id, index)
    await session.commit()
    return await _reload(session, actor, product_id)


class ShownIn(BaseModel):
    shown: bool


@router.put("/products/{product_id}/images/{index}/shown", response_model=SellerProductOut)
async def show_image(product_id: uuid.UUID, index: int, body: ShownIn, session: Session, actor: CurrentActor):
    await media.show_image(session, actor, product_id, index, body.shown)
    await session.commit()
    return await _reload(session, actor, product_id)


@router.post("/products/{product_id}/documents", response_model=SellerProductOut)
async def add_document(
    product_id: uuid.UUID,
    file: UploadFile,
    session: Session,
    actor: CurrentActor,
    kind: Annotated[ProductDocKind, Form()],
    number: Annotated[str | None, Form()] = None,
    valid_until: Annotated[date | None, Form()] = None,
):
    data = await file.read()
    await media.add_document(
        session,
        actor,
        product_id,
        kind,
        data,
        file.content_type or "",
        file.filename or "",
        number,
        valid_until,
    )
    await session.commit()
    return await _reload(session, actor, product_id)


@router.delete("/products/{product_id}/documents/{doc_id}", response_model=SellerProductOut)
async def remove_document(product_id: uuid.UUID, doc_id: uuid.UUID, session: Session, actor: CurrentActor):
    await media.remove_document(session, actor, product_id, doc_id)
    await session.commit()
    return await _reload(session, actor, product_id)


@router.get("/settings", response_model=SettingsIO)
async def get_settings(session: Session, actor: CurrentActor):
    profile, zones = await svc.get_settings_for(session, actor)
    await session.commit()
    return _settings_out(profile, zones)


@router.put("/settings", response_model=SettingsIO)
async def put_settings(body: SettingsIO, session: Session, actor: CurrentActor):
    data = svc.SettingsIn(
        **body.model_dump(exclude={"zones"}), zones=[svc.ZoneIn(z.district, z.weekdays) for z in body.zones]
    )
    profile, zones = await svc.save_settings(session, actor, data)
    await session.commit()
    return _settings_out(profile, zones)


def _settings_out(profile, zones) -> SettingsIO:
    return SettingsIO(
        min_order_amd=profile.min_order_amd,
        cutoff_time=profile.cutoff_time,
        lead_days=profile.lead_days,
        delivery_fee_amd=profile.delivery_fee_amd,
        free_delivery_from_amd=profile.free_delivery_from_amd,
        offer_response_hours=profile.offer_response_hours,
        small_order_fee_amd=profile.small_order_fee_amd,
        window_from=profile.window_from,
        window_to=profile.window_to,
        slot_hours=profile.slot_hours,
        express_fee_amd=profile.express_fee_amd,
        express_until=profile.express_until,
        express_hours=profile.express_hours,
        express_min_amd=profile.express_min_amd,
        description=profile.description,
        zones=[ZoneIO(district=z.district, weekdays=z.weekdays) for z in zones],
    )


# --- price list import --------------------------------------------------------------------------------------


class ParsedTable(BaseModel):
    headers: list[str]
    rows: list[list[Any]]
    mapping: dict[int, imp.Field]  # guessed: column index -> field


class ImportBody(BaseModel):
    mapping: dict[int, imp.Field]
    rows: list[list[Any]]
    default_unit: Unit | None = None
    missing_out_of_stock: bool = False


class ChangeOut(BaseModel):
    field: str
    old: Any
    new: Any


class ImportItemOut(BaseModel):
    action: imp.Action
    line: int | None
    product_id: uuid.UUID | None
    sku: str | None
    name: dict[str, str]
    price_amd: int | None
    unit: Unit | None
    changes: list[ChangeOut]
    problems: list[str]
    warnings: list[str]


class ImportResult(BaseModel):
    counts: dict[str, int]
    items: list[ImportItemOut]


def _json(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def _result(items: list[imp.Item]) -> ImportResult:
    counts = {a.value: 0 for a in imp.Action}
    out = []
    for it in items:
        counts[it.action.value] += 1
        r = it.row
        out.append(
            ImportItemOut(
                action=it.action,
                line=it.line,
                product_id=it.product_id,
                sku=r.sku if r else None,
                name=r.name if r else {},
                price_amd=r.price_amd if r else None,
                unit=r.unit if r else None,
                changes=[ChangeOut(field=c.field, old=_json(c.old), new=_json(c.new)) for c in it.changes],
                problems=it.problems,
                warnings=it.warnings,
            )
        )
    return ImportResult(counts=counts, items=out)


def _import_in(body: ImportBody) -> import_svc.ImportIn:
    return import_svc.ImportIn(
        mapping=body.mapping,
        rows=body.rows,
        default_unit=body.default_unit,
        missing_out_of_stock=body.missing_out_of_stock,
    )


@router.post("/import/parse", response_model=ParsedTable)
async def parse_price_list(file: UploadFile, actor: CurrentActor):
    svc._seller(actor)
    try:
        headers, rows = read_table(await file.read(), file.filename or "", imp.MAX_ROWS)
    except UnreadableFile as e:
        raise ValidationFailed([{"field": "file", "code": e.code}]) from e
    texts = [[str(c) if c is not None else "" for c in r] for r in rows]
    return ParsedTable(headers=headers, rows=rows, mapping=imp.guess_mapping(headers, texts))


@router.post("/import/preview", response_model=ImportResult)
async def preview_import(body: ImportBody, session: Session, actor: CurrentActor):
    return _result(await import_svc.preview(session, actor, _import_in(body)))


@router.post("/import/apply", response_model=ImportResult)
async def apply_import(body: ImportBody, session: Session, actor: CurrentActor):
    items = await import_svc.apply(session, actor, _import_in(body))
    await session.commit()
    return _result(items)
