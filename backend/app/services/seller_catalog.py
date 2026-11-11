"""Seller cabinet: own products, volume prices, the autopilot per product, delivery terms.

Editing works before verification (sellers prepare the catalog while the operator checks documents);
products appear in the catalog only once the company is verified.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.search import build_search_text
from app.domain import product_specs
from app.domain.negotiation import MAX_DISCOUNT_BP, discount_bp
from app.models import (
    Category,
    DeliveryZone,
    OfferRule,
    OrgKind,
    PriceTier,
    Product,
    RuleScope,
    SellerProfile,
    TaxRegime,
    Unit,
)
from app.platform.authz import Action, Actor
from app.platform.errors import NotFound, ValidationFailed

LANGS = ("hy", "ru", "en")


@dataclass
class TierIn:
    min_qty: Decimal
    price_amd: int


@dataclass
class AutopilotIn:
    """Discounts in basis points from the line price: 300 = "agree to up to 3% at once"."""

    enabled: bool = True
    accept_discount_bp: int | None = None  # agree at once to a discount up to this
    max_discount_bp: int | None = None  # decline anything deeper; None = no floor
    auto_counter: bool = True  # in between: counter halfway
    min_line_amd: int | None = None
    share_hint: bool = False  # show buyers the auto-accept discount


@dataclass
class ProductIn:
    name: dict
    unit: Unit
    base_price_amd: int
    category_id: uuid.UUID | None = None
    sku: str | None = None
    description: dict | None = None
    pack_size: str | None = None
    vat_included: bool = True
    min_qty: Decimal = Decimal(1)
    qty_step: Decimal = Decimal(1)
    in_stock: bool = True
    allow_offers: bool = False
    is_service: bool = False
    specs: dict = field(default_factory=dict)  # characteristics, domain/product_specs.py
    tiers: list[TierIn] = field(default_factory=list)
    autopilot: AutopilotIn | None = None


def _seller(actor: Actor) -> Actor:
    seller = actor.with_mode(OrgKind.seller)
    seller.require(Action.catalog_edit)
    return seller


def _validate(data: ProductIn) -> list[dict]:
    p: list[dict] = []
    name = {k: v.strip() for k, v in (data.name or {}).items() if k in ("hy", "ru", "en") and v and v.strip()}
    if not name:
        p.append({"field": "name", "code": "required"})
    data.name = name
    if data.base_price_amd <= 0:
        p.append({"field": "base_price_amd", "code": "bad_price"})
    if data.min_qty <= 0 or data.qty_step <= 0:
        p.append({"field": "min_qty", "code": "bad_qty"})
    duplicate_qty = len({t.min_qty for t in data.tiers}) != len(data.tiers)
    for t in data.tiers:
        if duplicate_qty or t.min_qty <= data.min_qty or not 0 < t.price_amd < data.base_price_amd:
            p.append({"field": "tiers", "code": "bad_tier"})
            break
    a = data.autopilot
    if a and a.enabled:
        if a.accept_discount_bp is None or not 0 < a.accept_discount_bp <= MAX_DISCOUNT_BP:
            p.append({"field": "autopilot", "code": "bad_accept_discount"})
        elif (
            a.max_discount_bp is not None and not a.accept_discount_bp <= a.max_discount_bp <= MAX_DISCOUNT_BP
        ):
            p.append({"field": "autopilot", "code": "floor_above_accept"})
    return p


async def _apply(session: AsyncSession, actor: Actor, product: Product, data: ProductIn) -> None:
    product.name = data.name
    product.description = data.description
    product.category_id = data.category_id
    product.sku = data.sku or None
    product.unit = data.unit
    product.pack_size = data.pack_size or None
    product.base_price_amd = data.base_price_amd
    product.vat_included = data.vat_included
    product.vat_rate_bp = 2000 if actor.org.tax_regime == TaxRegime.vat else 0
    product.min_qty = data.min_qty
    product.qty_step = data.qty_step
    product.in_stock = data.in_stock
    if data.is_service:  # no haggling over a service
        data.allow_offers = False
    product.is_service = data.is_service
    product.specs = data.specs
    product.allow_offers = data.allow_offers
    product.search_text = build_search_text(data.name)
    await session.flush()
    await session.execute(delete(PriceTier).where(PriceTier.product_id == product.id))
    for t in sorted(data.tiers, key=lambda t: t.min_qty):
        session.add(PriceTier(product_id=product.id, min_qty=t.min_qty, price_amd=t.price_amd))
    rule = await session.scalar(
        select(OfferRule).where(OfferRule.scope == RuleScope.product, OfferRule.scope_id == product.id)
    )
    a = data.autopilot
    if a is None or not data.allow_offers:
        if rule is not None:
            await session.delete(rule)
        return
    if rule is None:
        rule = OfferRule(seller_id=actor.org.id, scope=RuleScope.product, scope_id=product.id)
        session.add(rule)
    rule.enabled = a.enabled
    # Percentages follow the line price (volume and contract prices too); absolute prices are legacy
    rule.floor_price_amd = None
    rule.auto_accept_price_amd = None
    rule.auto_accept_pct_bp = 10_000 - a.accept_discount_bp if a.accept_discount_bp is not None else None
    rule.floor_pct_bp = 10_000 - a.max_discount_bp if a.max_discount_bp is not None else None
    rule.auto_counter = a.auto_counter
    rule.min_line_amd = a.min_line_amd
    rule.share_hint = a.share_hint


def autopilot_out(rule: OfferRule, base_price_amd: int) -> AutopilotIn:
    """A stored rule as discounts; legacy absolute prices are turned into discounts from the list price."""

    def discount(price: int | None, pct_bp: int | None) -> int | None:
        if price is not None:
            return discount_bp(price, base_price_amd)
        return 10_000 - pct_bp if pct_bp is not None else None

    return AutopilotIn(
        enabled=rule.enabled,
        accept_discount_bp=discount(rule.auto_accept_price_amd, rule.auto_accept_pct_bp),
        max_discount_bp=discount(rule.floor_price_amd, rule.floor_pct_bp),
        auto_counter=rule.auto_counter,
        min_line_amd=rule.min_line_amd,
        share_hint=rule.share_hint,
    )


async def _category_exists(session: AsyncSession, category_id: uuid.UUID | None) -> bool:
    return category_id is None or await session.get(Category, category_id) is not None


async def _check_specs(session: AsyncSession, data: ProductIn) -> list[dict]:
    """The category must exist; characteristics are kept only if they fit it (and cleaned)."""
    category = await session.get(Category, data.category_id) if data.category_id else None
    if data.category_id and category is None:
        return [{"field": "category_id", "code": "unknown_category"}]
    data.specs, problems = product_specs.clean(
        data.specs, category.slug if category else None, data.is_service
    )
    return problems


async def create(session: AsyncSession, actor: Actor, data: ProductIn) -> Product:
    actor = _seller(actor)
    problems = _validate(data) + await _check_specs(session, data)
    if problems:
        raise ValidationFailed(problems)
    product = Product(
        seller_id=actor.org.id, name=data.name, unit=data.unit, base_price_amd=data.base_price_amd
    )
    session.add(product)
    await _apply(session, actor, product, data)
    return product


async def _own(session: AsyncSession, actor: Actor, product_id: uuid.UUID) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.seller_id != actor.org.id:
        raise NotFound("product_not_found")
    return product


async def update(session: AsyncSession, actor: Actor, product_id: uuid.UUID, data: ProductIn) -> Product:
    actor = _seller(actor)
    product = await _own(session, actor, product_id)
    problems = _validate(data) + await _check_specs(session, data)
    if problems:
        raise ValidationFailed(problems)
    await _apply(session, actor, product, data)
    return product


async def set_archived(
    session: AsyncSession, actor: Actor, product_id: uuid.UUID, archived: bool, now: datetime
) -> Product:
    actor = _seller(actor)
    product = await _own(session, actor, product_id)
    product.archived_at = now if archived else None
    return product


async def list_own(
    session: AsyncSession, actor: Actor, archived: bool = False
) -> list[tuple[Product, OfferRule | None]]:
    actor = _seller(actor)
    stmt = (
        select(Product)
        .where(Product.seller_id == actor.org.id)
        .options(selectinload(Product.tiers), selectinload(Product.documents))
    )
    stmt = stmt.where(Product.archived_at.is_not(None) if archived else Product.archived_at.is_(None))
    products = list(await session.scalars(stmt.order_by(Product.created_at.desc())))
    rules = {
        r.scope_id: r
        for r in await session.scalars(
            select(OfferRule).where(OfferRule.seller_id == actor.org.id, OfferRule.scope == RuleScope.product)
        )
    }
    return [(p, rules.get(p.id)) for p in products]


# --- delivery terms ----------------------------------------------------------------------------------------


@dataclass
class ZoneIn:
    district: str
    weekdays: list[int]


@dataclass
class SettingsIn:
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
    zones: list[ZoneIn] = field(default_factory=list)
    description: dict[str, str] | None = None  # "about us" on the supplier page, hy/ru/en


async def get_settings_for(session: AsyncSession, actor: Actor) -> tuple[SellerProfile, list[DeliveryZone]]:
    actor = _seller(actor)
    profile = await session.get(SellerProfile, actor.org.id)
    if profile is None:
        profile = SellerProfile(organization_id=actor.org.id, cutoff_time=time(16, 0), lead_days=1)
        session.add(profile)
        await session.flush()
    zones = list(await session.scalars(select(DeliveryZone).where(DeliveryZone.seller_id == actor.org.id)))
    return profile, zones


async def save_settings(
    session: AsyncSession, actor: Actor, data: SettingsIn
) -> tuple[SellerProfile, list[DeliveryZone]]:
    profile, _ = await get_settings_for(session, actor)
    problems = []
    if data.min_order_amd < 0 or data.delivery_fee_amd < 0:
        problems.append({"code": "bad_amount"})
    if not 0 <= data.lead_days <= 14:
        problems.append({"field": "lead_days", "code": "bad_lead_days"})
    if any(
        v is not None and v < 0
        for v in (data.small_order_fee_amd, data.express_fee_amd, data.express_min_amd)
    ):
        problems.append({"code": "bad_amount"})
    if data.window_to <= data.window_from:
        problems.append({"field": "window", "code": "bad_window"})
    if data.slot_hours not in (0, 1, 2, 3, 4):
        problems.append({"field": "slot_hours", "code": "bad_slot_hours"})
    if not 1 <= data.express_hours <= 12:
        problems.append({"field": "express_hours", "code": "bad_hours"})
    if not 1 <= data.offer_response_hours <= 72:
        problems.append({"field": "offer_response_hours", "code": "bad_hours"})
    if any(
        not z.district.strip() or not z.weekdays or any(d not in range(1, 8) for d in z.weekdays)
        for z in data.zones
    ):
        problems.append({"field": "zones", "code": "bad_zone"})
    if problems:
        raise ValidationFailed(problems)
    profile.min_order_amd = data.min_order_amd
    profile.cutoff_time = data.cutoff_time
    profile.lead_days = data.lead_days
    profile.delivery_fee_amd = data.delivery_fee_amd
    profile.free_delivery_from_amd = data.free_delivery_from_amd
    profile.offer_response_hours = data.offer_response_hours
    profile.small_order_fee_amd = data.small_order_fee_amd
    profile.window_from = data.window_from
    profile.window_to = data.window_to
    profile.slot_hours = data.slot_hours
    profile.express_fee_amd = data.express_fee_amd
    profile.express_until = data.express_until
    profile.express_hours = data.express_hours
    profile.express_min_amd = data.express_min_amd
    about = {k: v.strip()[:1000] for k, v in (data.description or {}).items() if k in LANGS and v.strip()}
    profile.description = about or None
    await session.execute(delete(DeliveryZone).where(DeliveryZone.seller_id == actor.org.id))
    for z in data.zones:
        session.add(
            DeliveryZone(
                seller_id=actor.org.id, district=z.district.strip(), weekdays=sorted(set(z.weekdays))
            )
        )
    await session.flush()
    return await get_settings_for(session, actor)
