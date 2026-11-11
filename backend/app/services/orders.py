"""Orders after checkout: access, status changes, totals, acceptance with commission, reorder.

Services never commit: the caller (API, bot, scheduler) owns the transaction.
Lock order is always orders -> negotiations -> order_lines (docs/03-architecture.md §3.5).
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.domain import commission as commission_domain
from app.domain.orders import (
    By,
    InvalidTransition,
    LineAmount,
    OrderAction,
    compute_totals,
    transition,
)
from app.domain.pricing import line_total_amd
from app.models import (
    CartItem,
    Category,
    ClientLink,
    ClientSource,
    CommissionRule,
    Negotiation,
    NegotiationScope,
    NegotiationStatus,
    OfferResponse,
    Order,
    OrderEvent,
    OrderLine,
    OrderStatus,
    OrgKind,
    PaymentStatus,
    Product,
    SellerProfile,
)
from app.platform.authz import Action, Actor
from app.platform.clock import local
from app.platform.errors import Conflict, NotFound, ValidationFailed
from app.platform.outbox import emit, jsonable
from app.services import documents

# --- loading and access ------------------------------------------------------------------------------------


async def load(session: AsyncSession, order_id: uuid.UUID, lock: bool = False) -> Order | None:
    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.lines))
    if lock:
        stmt = stmt.with_for_update(of=Order)
    return await session.scalar(stmt)


def side_of(actor: Actor, order: Order) -> By | None:
    if actor.org.id == order.buyer_id:
        return By.buyer
    if actor.org.id == order.seller_id:
        return By.seller
    return None


async def get_for_actor(
    session: AsyncSession, actor: Actor, order_id: uuid.UUID, lock: bool = False
) -> Order:
    order = await load(session, order_id, lock)
    # Someone else's order looks exactly like a missing one
    if order is None or side_of(actor, order) is None:
        raise NotFound("order_not_found")
    return order


async def list_for_actor(
    session: AsyncSession, actor: Actor, statuses: list[OrderStatus] | None, limit: int, offset: int
) -> tuple[list[Order], int]:
    column = Order.buyer_id if actor.is_buyer else Order.seller_id
    stmt = select(Order).where(column == actor.org.id)
    if statuses:
        stmt = stmt.where(Order.status.in_(statuses))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await session.scalars(
        stmt.options(selectinload(Order.lines)).order_by(Order.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows), total


# --- journal ----------------------------------------------------------------------------------------------


def log(session: AsyncSession, order: Order, type_: str, user_id: uuid.UUID | None, **payload: Any) -> None:
    """Journal entry for people + outbox event for side effects, in the same transaction."""
    session.add(
        OrderEvent(order_id=order.id, actor_user_id=user_id, type=type_, payload=jsonable(payload) or None)
    )
    emit(
        session,
        f"order.{type_}",
        order_id=order.id,
        number=order.number,
        buyer_id=order.buyer_id,
        seller_id=order.seller_id,
        by_user=user_id,
        **payload,
    )


def apply(order: Order, action: OrderAction, by: By, now: datetime) -> OrderStatus:
    try:
        order.status = transition(order.status, action, by)
    except InvalidTransition as e:
        raise Conflict("invalid_transition", str(e), {"status": e.status, "action": e.action}) from None
    order.status_changed_at = now
    return order.status


# --- money ------------------------------------------------------------------------------------------------


def effective_qty(line: OrderLine) -> Decimal:
    return Decimal(line.qty_confirmed if line.qty_confirmed is not None else line.qty_ordered)


async def order_discount_bp(session: AsyncSession, order: Order) -> int:
    """Discount agreed in an order-level negotiation (the accepted percent offer), else 0."""
    nego = await session.scalar(
        select(Negotiation).where(Negotiation.order_id == order.id).options(selectinload(Negotiation.offers))
    )
    if nego is None or nego.scope != NegotiationScope.order or nego.status != NegotiationStatus.agreed:
        return 0
    accepted = [o for o in nego.offers if o.response == OfferResponse.accepted and o.percent_bp]
    return accepted[-1].percent_bp if accepted else 0


async def recompute(session: AsyncSession, order: Order) -> None:
    """Totals from lines. Lines still under negotiation count at their pre-negotiation price (estimate)."""
    for line in order.lines:
        price = line.final_price_amd
        line.line_total_amd = line_total_amd(price, effective_qty(line)) if price is not None else None
    profile = await session.get(SellerProfile, order.seller_id)
    totals = compute_totals(
        [
            LineAmount(
                effective_qty(li), li.final_price_amd if li.final_price_amd is not None else li.list_price_amd
            )
            for li in order.lines
        ],
        discount_bp=await order_discount_bp(session, order),
        delivery_fee_amd=profile.delivery_fee_amd if profile else 0,
        free_delivery_from_amd=profile.free_delivery_from_amd if profile else None,
        min_order_amd=profile.min_order_amd if profile else 0,
        small_order_fee_amd=profile.small_order_fee_amd if profile else None,
        express_fee_amd=order.express_fee_amd or 0,
    )
    order.subtotal_amd = totals.subtotal_amd
    order.discount_amd = totals.discount_amd
    order.delivery_fee_amd = totals.delivery_fee_amd
    order.total_amd = totals.total_amd


async def ensure_client_link(session: AsyncSession, seller_id: uuid.UUID, buyer_id: uuid.UUID) -> ClientLink:
    link = await session.scalar(
        select(ClientLink).where(ClientLink.seller_id == seller_id, ClientLink.buyer_id == buyer_id)
    )
    if link is None:
        link = ClientLink(
            seller_id=seller_id, buyer_id=buyer_id, source=ClientSource.platform, orders_count=0
        )
        session.add(link)
        await session.flush()
    return link


async def _commission_rate(
    session: AsyncSession, category: Category | None, source: ClientSource, today
) -> tuple[int, int]:
    """(rate bp, first-order bonus bp): category rule -> category default (platform) -> default rule -> 0."""
    rules = list(
        await session.scalars(
            select(CommissionRule)
            .where(CommissionRule.client_source == source, CommissionRule.valid_from <= today)
            .order_by(CommissionRule.valid_from.desc())
        )
    )
    default = next((r for r in rules if r.category_id is None), None)
    bonus = default.first_order_bonus_bp if default else 0
    if category is not None:
        specific = next((r for r in rules if r.category_id == category.id), None)
        if specific is not None:
            return specific.rate_bp, specific.first_order_bonus_bp or bonus
        if source == ClientSource.platform and category.default_commission_bp is not None:
            return category.default_commission_bp, bonus
    return (default.rate_bp if default else 0), bonus


async def settle_commission(session: AsyncSession, order: Order, now: datetime) -> None:
    link = await ensure_client_link(session, order.seller_id, order.buyer_id)
    first = link.orders_count == 0 and link.source == ClientSource.platform
    today = local(now).date()
    goods = order.subtotal_amd - order.discount_amd
    parts: list[tuple[int, int]] = []
    bonus_bp = 0
    for line in order.lines:
        if not line.line_total_amd:
            continue
        product = await session.get(Product, line.product_id) if line.product_id else None
        category = (
            await session.get(Category, product.category_id) if product and product.category_id else None
        )
        rate, bonus = await _commission_rate(session, category, link.source, today)
        bonus_bp = bonus if first else 0
        # Spread the order discount over lines proportionally
        share = line.line_total_amd * goods // order.subtotal_amd if order.subtotal_amd else 0
        parts.append((share, rate))
    result = commission_domain.compute(parts, bonus_bp)
    order.client_source = link.source
    order.commission_amd = result.amount_amd
    order.commission_rate_bp = result.rate_bp
    link.orders_count += 1
    link.first_order_at = link.first_order_at or now


# --- actions ----------------------------------------------------------------------------------------------


def _require_side(actor: Actor, order: Order, by: By) -> None:
    if side_of(actor, order) != by:
        raise NotFound("order_not_found")
    # The order decides the side, not the app's current mode
    mode = OrgKind.buyer if by == By.buyer else OrgKind.seller
    actor.with_mode(mode).require(Action.order_buyer if by == By.buyer else Action.order_seller)


async def confirm(
    session: AsyncSession,
    actor: Actor,
    order_id: uuid.UUID,
    confirmed_qty: dict[uuid.UUID, Decimal],
    now: datetime,
    note: str | None = None,
) -> Order:
    """Seller confirms; may lower quantities (0 removes a line from delivery) but never raise them."""
    order = await get_for_actor(session, actor, order_id, lock=True)
    _require_side(actor, order, By.seller)
    lines = {li.id: li for li in order.lines}
    problems = [{"order_line_id": str(k), "code": "unknown_line"} for k in confirmed_qty if k not in lines]
    for line in order.lines:
        qty = confirmed_qty.get(line.id, Decimal(line.qty_ordered))
        if qty < 0 or qty > Decimal(line.qty_ordered):
            problems.append({"order_line_id": str(line.id), "code": "bad_confirmed_qty"})
        line.qty_confirmed = qty
    if problems:
        raise ValidationFailed(problems)
    if all(Decimal(li.qty_confirmed) == 0 for li in order.lines):
        raise ValidationFailed([{"code": "nothing_to_deliver"}], "reject the order instead")
    apply(order, OrderAction.confirm, By.seller, now)
    order.seller_note = note or order.seller_note
    before = order.total_amd
    await recompute(session, order)
    await documents.ensure_for_order(session, order, now)  # invoice + delivery note
    changed = [str(li.id) for li in order.lines if Decimal(li.qty_confirmed) != Decimal(li.qty_ordered)]
    log(
        session,
        order,
        "confirmed",
        actor.user.id,
        changed_lines=changed,
        total_before=before,
        total=order.total_amd,
    )
    return order


async def simple_action(
    session: AsyncSession,
    actor: Actor,
    order_id: uuid.UUID,
    action: OrderAction,
    now: datetime,
    reason: str | None = None,
) -> Order:
    """reject / ship / deliver (seller), cancel (buyer)."""
    by = By.buyer if action == OrderAction.cancel else By.seller
    order = await get_for_actor(session, actor, order_id, lock=True)
    _require_side(actor, order, by)
    was_negotiating = order.status == OrderStatus.negotiating
    apply(order, action, by, now)
    if action in (OrderAction.cancel, OrderAction.reject):
        order.cancel_reason = reason or ("buyer" if by == By.buyer else "seller_rejected")
    if was_negotiating:
        nego = await session.scalar(
            select(Negotiation).where(Negotiation.order_id == order.id).with_for_update()
        )
        if nego is not None:
            nego.status = NegotiationStatus.declined
    log(session, order, str(order.status), actor.user.id, reason=reason)
    return order


async def accept(
    session: AsyncSession,
    actor: Actor | None,
    order_id: uuid.UUID,
    received_qty: dict[uuid.UUID, Decimal],
    now: datetime,
) -> Order:
    """Buyer accepts delivery (actor None = the 48 h auto-accept). Received quantities are recorded."""
    if actor is None:
        order = await load(session, order_id, lock=True)
        if order is None:
            raise NotFound("order_not_found")
        by = By.system
    else:
        order = await get_for_actor(session, actor, order_id, lock=True)
        _require_side(actor, order, By.buyer)
        by = By.buyer
    for line in order.lines:
        line.qty_received = received_qty.get(line.id, effective_qty(line))
    apply(order, OrderAction.accept, by, now)
    await settle_commission(session, order, now)
    await documents.ensure_for_order(session, order, now)  # acceptance act with received quantities
    short = [str(li.id) for li in order.lines if Decimal(li.qty_received) < effective_qty(li)]
    log(session, order, "accepted", actor.user.id if actor else None, auto=actor is None, short_lines=short)
    return order


async def mark_paid(session: AsyncSession, actor: Actor, order_id: uuid.UUID, now: datetime) -> Order:
    order = await get_for_actor(session, actor, order_id, lock=True)
    _require_side(actor, order, By.seller)
    if order.status in (
        OrderStatus.negotiating,
        OrderStatus.placed,
        OrderStatus.cancelled,
        OrderStatus.rejected,
    ):
        raise Conflict("invalid_transition", "only confirmed orders can be paid")
    if order.payment_status == PaymentStatus.paid:
        return order
    order.payment_status = PaymentStatus.paid
    log(session, order, "paid", actor.user.id)
    return order


async def reorder(session: AsyncSession, actor: Actor, order_id: uuid.UUID) -> int:
    """Put the order's products back into the cart (current prices). Returns how many lines were added."""
    actor.require(Action.cart_edit)
    order = await get_for_actor(session, actor, order_id)
    if side_of(actor, order) != By.buyer:
        raise NotFound("order_not_found")
    added = 0
    for line in order.lines:
        product = await session.get(Product, line.product_id) if line.product_id else None
        if product is None or product.archived_at is not None or not product.in_stock:
            continue
        item = await session.scalar(
            select(CartItem).where(CartItem.buyer_id == actor.org.id, CartItem.product_id == product.id)
        )
        if item is None:
            session.add(
                CartItem(
                    buyer_id=actor.org.id, user_id=actor.user.id, product_id=product.id, qty=line.qty_ordered
                )
            )
        else:
            item.qty = Decimal(item.qty) + Decimal(line.qty_ordered)
        added += 1
    return added


async def auto_accept_due(session: AsyncSession, now: datetime) -> int:
    """Scheduler job: delivered orders nobody accepted for 48 h are accepted by the system."""
    hours = get_settings().auto_accept_after_delivery_hours
    due = list(
        await session.scalars(
            select(Order.id).where(
                Order.status == OrderStatus.delivered, Order.status_changed_at <= now - timedelta(hours=hours)
            )
        )
    )
    done = 0
    for order_id in due:
        try:
            async with session.begin_nested():
                await accept(session, None, order_id, {}, now)
            done += 1
        except Conflict:
            continue  # the buyer accepted or disputed meanwhile
    return done
