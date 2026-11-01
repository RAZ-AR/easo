"""Order documents (docs/04-documents-and-verification.md).

Easo issues printable documents itself: invoice for payment, delivery note (with a transport section) and
the acceptance act. Each one freezes requisites, lines, VAT and totals in `payload` at issue time, so a
later change of a company's bank account never rewrites an old invoice.

The official SRC e-invoice and e-waybill are issued in the tax system (no public API confirmed); the
seller types their numbers into the order and we keep them as `tax_invoice` / `waybill` documents.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pricing import line_total_amd
from app.models import (
    Address,
    Document,
    DocumentCounter,
    DocumentKind,
    DocumentStatus,
    Order,
    OrderStatus,
    Organization,
    OrgKind,
    TaxRegime,
)
from app.platform.authz import Action, Actor
from app.platform.clock import local
from app.platform.errors import Conflict, NotFound, ValidationFailed
from app.platform.outbox import emit

PREFIX = {DocumentKind.invoice: "INV", DocumentKind.delivery_note: "DN", DocumentKind.acceptance_act: "ACT"}
PRINTABLE = tuple(PREFIX)
SRC_KINDS = (DocumentKind.tax_invoice, DocumentKind.waybill)

TITLES = {
    DocumentKind.invoice: {"hy": "Հաշիվ վճարման համար", "ru": "Счёт на оплату", "en": "Invoice"},
    DocumentKind.delivery_note: {"hy": "Ապրանքագիր", "ru": "Товарная накладная", "en": "Delivery note"},
    DocumentKind.acceptance_act: {
        "hy": "Ընդունման-հանձնման ակտ",
        "ru": "Акт приёма-передачи",
        "en": "Acceptance act",
    },
    DocumentKind.tax_invoice: {"hy": "Էլեկտրոնային հաշվարկային փաստաթուղթ", "ru": "Электронный счёт КГД",
                               "en": "SRC e-invoice"},
    DocumentKind.waybill: {"hy": "Էլեկտրոնային ուղեկցող փաստաթուղթ", "ru": "Путевой лист КГД",
                           "en": "SRC e-waybill"},
}  # fmt: skip

# Which printable documents exist at which order status
DUE: dict[OrderStatus, tuple[DocumentKind, ...]] = {
    OrderStatus.confirmed: (DocumentKind.invoice, DocumentKind.delivery_note),
    OrderStatus.delivering: (DocumentKind.invoice, DocumentKind.delivery_note),
    OrderStatus.delivered: (DocumentKind.invoice, DocumentKind.delivery_note),
    OrderStatus.accepted: (DocumentKind.invoice, DocumentKind.delivery_note, DocumentKind.acceptance_act),
    OrderStatus.disputed: (DocumentKind.invoice, DocumentKind.delivery_note),
}


def vat_in(amount: int, rate_bp: int) -> int:
    """VAT contained in a VAT-inclusive amount: amount x r / (1 + r)."""
    if not rate_bp:
        return 0
    return int(
        (Decimal(amount) * rate_bp / (10_000 + rate_bp)).quantize(Decimal(1), rounding="ROUND_HALF_UP")
    )


def _party(org: Organization) -> dict:
    return {
        "legal_name": org.legal_name,
        "display_name": org.display_name,
        "tin": org.tin,
        "registration_number": org.registration_number,
        "legal_address": org.legal_address,
        "director_name": org.director_name,
        "bank_name": org.bank_name,
        "bank_account": org.bank_account,
        "phone": org.phone,
        "vat_payer": org.tax_regime == TaxRegime.vat,
        "tax_regime": org.tax_regime,
    }


async def _next_number(session: AsyncSession, seller_id: uuid.UUID, kind: DocumentKind, year: int) -> str:
    """Gap-free per seller/kind/year: the counter row is locked until the transaction ends."""
    await session.execute(
        insert(DocumentCounter)
        .values(seller_id=seller_id, kind=kind, year=year, last=0)
        .on_conflict_do_nothing(index_elements=["seller_id", "kind", "year"])
    )
    counter = await session.scalar(
        select(DocumentCounter)
        .where(
            DocumentCounter.seller_id == seller_id, DocumentCounter.kind == kind, DocumentCounter.year == year
        )
        .with_for_update()
    )
    assert counter is not None
    counter.last += 1
    return f"{PREFIX[kind]}-{year}-{counter.last:04d}"


def _qty(line, kind: DocumentKind) -> Decimal:
    if kind == DocumentKind.acceptance_act and line.qty_received is not None:
        return Decimal(line.qty_received)
    return Decimal(line.qty_confirmed if line.qty_confirmed is not None else line.qty_ordered)


async def build_payload(
    session: AsyncSession, order: Order, kind: DocumentKind, number: str, now: datetime
) -> dict:
    seller = await session.get(Organization, order.seller_id)
    buyer = await session.get(Organization, order.buyer_id)
    address = await session.get(Address, order.address_id) if order.address_id else None
    assert seller and buyer
    lines = []
    subtotal = 0
    for li in order.lines:
        qty = _qty(li, kind)
        price = li.final_price_amd if li.final_price_amd is not None else li.list_price_amd
        amount = line_total_amd(price, qty)
        subtotal += amount
        row = {
            "n": len(lines) + 1,
            "name": li.name_snapshot,
            "unit": li.unit,
            "qty": str(qty.normalize()),
            "price_amd": price,
            "amount_amd": amount,
            "vat_rate_bp": li.vat_rate_bp,
        }
        if kind == DocumentKind.acceptance_act:
            row["qty_confirmed"] = str(
                Decimal(li.qty_confirmed if li.qty_confirmed is not None else li.qty_ordered)
            )
        lines.append(row)
    # The order discount and delivery are shown once; VAT is spread the same way
    discount = order.discount_amd if subtotal else 0
    goods = subtotal - discount
    delivery = order.delivery_fee_amd
    vat = sum(vat_in(r["amount_amd"], r["vat_rate_bp"]) for r in lines)
    if subtotal:
        vat = round(vat * goods / subtotal)
    seller_rate = 2000 if seller.tax_regime == TaxRegime.vat else 0
    vat += vat_in(delivery, seller_rate)
    for r in lines:
        r["vat_amd"] = vat_in(r["amount_amd"], r["vat_rate_bp"])
    payload = {
        "kind": kind,
        "title": TITLES[kind],
        "number": number,
        "date": local(now).date().isoformat(),
        "order_number": order.number,
        "order_date": local(order.created_at).date().isoformat() if order.created_at else None,
        "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
        "seller": _party(seller),
        "buyer": _party(buyer),
        "delivery_address": address.text if address else None,
        "lines": lines,
        "totals": {
            "subtotal_amd": subtotal,
            "discount_amd": discount,
            "delivery_fee_amd": delivery,
            "total_amd": goods + delivery,
            "vat_amd": vat,
        },
        "vat_note": None if seller.tax_regime == TaxRegime.vat else "not_vat_payer",
    }
    if kind == DocumentKind.delivery_note:
        payload["transport"] = {
            "from": seller.legal_address,
            "to": address.text if address else None,
            # Who signs for the goods at the address
            "receiver": ", ".join(x for x in (address.contact_name, address.contact_phone) if x)
            if address
            else None,
            "vehicle": None,  # filled by hand on the printout or in the SRC e-waybill
            "driver": None,
        }
    return payload


async def ensure_for_order(session: AsyncSession, order: Order, now: datetime) -> list[Document]:
    """Issue the printable documents that are due for the order's status and do not exist yet. Idempotent."""
    due = DUE.get(order.status, ())
    if not due:
        return []
    existing = {d.kind for d in await session.scalars(select(Document).where(Document.order_id == order.id))}
    created = []
    for kind in due:
        if kind in existing:
            continue
        number = await _next_number(session, order.seller_id, kind, local(now).year)
        doc = Document(
            order_id=order.id,
            kind=kind,
            number=number,
            status=DocumentStatus.issued,
            issued_at=now,
            payload=await build_payload(session, order, kind, number, now),
        )
        session.add(doc)
        created.append(doc)
        emit(
            session, "document.issued", order_id=order.id, kind=kind, number=number, seller_id=order.seller_id
        )
    return created


async def list_for_order(session: AsyncSession, order: Order) -> list[Document]:
    rows = await session.scalars(
        select(Document).where(Document.order_id == order.id).order_by(Document.created_at)
    )
    return list(rows)


async def get_for_actor(
    session: AsyncSession, actor: Actor, document_id: uuid.UUID
) -> tuple[Document, Order]:
    from app.services import orders as order_svc  # orders imports this module

    doc = await session.get(Document, document_id)
    if doc is None:
        raise NotFound("document_not_found")
    order = await order_svc.get_for_actor(session, actor, doc.order_id)
    return doc, order


async def set_src_number(
    session: AsyncSession, actor: Actor, order: Order, kind: DocumentKind, number: str, now: datetime
) -> Document:
    """The seller issued the e-invoice / e-waybill in the SRC system and records its number here."""
    if actor.org.id != order.seller_id:
        raise NotFound("order_not_found")
    actor.with_mode(OrgKind.seller).require(Action.order_seller)
    if kind not in SRC_KINDS:
        raise ValidationFailed([{"code": "not_an_src_document"}])
    number = number.strip()
    if not number or len(number) > 64:
        raise ValidationFailed([{"code": "bad_number"}])
    if order.status in (
        OrderStatus.negotiating,
        OrderStatus.placed,
        OrderStatus.cancelled,
        OrderStatus.rejected,
    ):
        raise Conflict("order_not_confirmed")
    doc = await session.scalar(select(Document).where(Document.order_id == order.id, Document.kind == kind))
    if doc is None:
        doc = Document(order_id=order.id, kind=kind)
        session.add(doc)
    doc.external_id = number
    doc.status = DocumentStatus.issued
    doc.issued_at = now
    doc.payload = {"kind": kind, "title": TITLES[kind], "external_id": number}
    return doc


# --- the company's document register (the "Documents" page) ---------------------------------------------


async def list_for_org(
    session: AsyncSession,
    actor: Actor,
    *,
    kinds: list[DocumentKind] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[tuple[Document, Order, Organization]], int]:
    """Documents of the company's orders on the side it acts on now: purchases or sales."""
    mine = Order.buyer_id if actor.is_buyer else Order.seller_id
    theirs = Order.seller_id if actor.is_buyer else Order.buyer_id
    stmt = (
        select(Document, Order, Organization)
        .join(Order, Order.id == Document.order_id)
        .join(Organization, Organization.id == theirs)
        .where(mine == actor.org.id)
    )
    if kinds:
        stmt = stmt.where(Document.kind.in_(kinds))
    if date_from:
        stmt = stmt.where(Document.issued_at >= date_from)
    if date_to:
        stmt = stmt.where(Document.issued_at < date_to)
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            Document.number.ilike(term)
            | Document.external_id.ilike(term)
            | Order.number.ilike(term)
            | Organization.display_name.ilike(term)
            | Organization.legal_name.ilike(term)
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await session.execute(
        stmt.order_by(Document.issued_at.desc().nulls_last(), Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [tuple(r) for r in rows.all()], total
