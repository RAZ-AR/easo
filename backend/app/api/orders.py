import uuid
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.auth.deps import CurrentActor, Session
from app.config import get_settings
from app.domain.orders import FINAL, By, OrderAction, allowed_actions
from app.domain.pricing import line_total_amd
from app.models import (
    Address,
    DocumentKind,
    Negotiation,
    NegotiationStatus,
    Order,
    OrderEvent,
    OrderStatus,
    Organization,
    PaymentStatus,
    PriceSource,
)
from app.platform import idempotency
from app.platform.authz import Actor
from app.platform.clock import YEREVAN, utcnow
from app.platform.errors import NotFound
from app.schemas import (
    AcceptIn,
    AddressOut,
    AgreementIn,
    AgreementOut,
    ClientOut,
    ConfirmIn,
    DocumentOut,
    DocumentPage,
    DocumentRow,
    EventOut,
    MessageIn,
    MessageOut,
    NegotiationOut,
    OfferOut,
    OrderLineOut,
    OrderListItem,
    OrderOut,
    OrderPage,
    ReasonIn,
    RespondIn,
    SellerBrief,
    SrcNumberIn,
)
from app.services import agreements as agreement_svc
from app.services import chat as chat_svc
from app.services import clients as client_svc
from app.services import documents as doc_svc
from app.services import negotiation as nego_svc
from app.services import notifications as notification_svc
from app.services import orders as order_svc

router = APIRouter(tags=["orders"])


def _brief(org: Organization | None) -> SellerBrief:
    return (
        SellerBrief(id=org.id, display_name=org.display_name)
        if org
        else SellerBrief(id=uuid.UUID(int=0), display_name="?")
    )


def _negotiation_out(nego: Negotiation, side: By) -> NegotiationOut:
    my = "buyer" if side == By.buyer else "seller"
    my_turn = nego.status == (
        NegotiationStatus.awaiting_buyer if side == By.buyer else NegotiationStatus.awaiting_seller
    )
    used = sum(1 for o in nego.offers if o.from_side == my)
    return NegotiationOut(
        status=nego.status,
        scope=nego.scope,
        round=nego.round,
        expires_at=nego.expires_at,
        my_turn=my_turn,
        rounds_left=max(0, get_settings().offer_max_rounds - used),
        offers=[OfferOut.model_validate(o) for o in nego.offers],
    )


def _actions(order: Order, side: By, nego: Negotiation | None) -> list[str]:
    actions = [
        str(a)
        for a in allowed_actions(order.status, side)
        if a not in (OrderAction.agree, OrderAction.expire)
    ]
    if nego is not None and order.status == OrderStatus.negotiating:
        my_turn = nego.status == (
            NegotiationStatus.awaiting_buyer if side == By.buyer else NegotiationStatus.awaiting_seller
        )
        if my_turn:
            actions.append("respond")
    if side == By.seller and order.payment_status == PaymentStatus.unpaid and order.status in (
        OrderStatus.confirmed, OrderStatus.delivering, OrderStatus.delivered, OrderStatus.accepted
    ):  # fmt: skip
        actions.append("mark_paid")
    if side == By.buyer and order.status in FINAL:
        actions.append("reorder")
    return actions


def _next_step(order: Order, side: By, nego: Negotiation | None) -> tuple[str | None, datetime | None]:
    """What this side should do next, and the deadline if there is one (drives inbox triage)."""
    hours = get_settings().auto_accept_after_delivery_hours
    turn = (
        nego is not None
        and order.status == OrderStatus.negotiating
        and nego.status
        == (NegotiationStatus.awaiting_buyer if side == By.buyer else NegotiationStatus.awaiting_seller)
    )
    if turn:
        return ("answer_counter" if side == By.buyer else "answer_offer"), nego.expires_at
    if side == By.seller:
        if order.status == OrderStatus.placed:
            return "confirm", None
        if order.status in (OrderStatus.confirmed, OrderStatus.delivering):
            return "deliver", None
        if order.status == OrderStatus.accepted and order.payment_status == PaymentStatus.unpaid:
            return "mark_paid", None
    elif order.status == OrderStatus.delivered:
        return "accept_delivery", order.status_changed_at + timedelta(hours=hours)
    return None, None


def _saving(order: Order) -> int:
    """Won in negotiation: (price before negotiation - agreed price) x qty, plus the order discount."""
    won = 0
    for li in order.lines:
        if li.price_source == PriceSource.negotiated and li.final_price_amd is not None:
            won += line_total_amd(li.list_price_amd - li.final_price_amd, order_svc.effective_qty(li))
    return won + order.discount_amd


async def _negotiations(session: Session, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, Negotiation]:
    if not order_ids:
        return {}
    rows = await session.scalars(select(Negotiation).where(Negotiation.order_id.in_(order_ids)))
    return {n.order_id: n for n in rows}


async def order_out(session: Session, actor: Actor, order: Order) -> OrderOut:
    side = order_svc.side_of(actor, order)
    assert side is not None
    nego = await nego_svc.load_for_order(session, order.id)
    buyer = await session.get(Organization, order.buyer_id)
    seller = await session.get(Organization, order.seller_id)
    address = await session.get(Address, order.address_id) if order.address_id else None
    events = await session.scalars(
        select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.at, OrderEvent.id)
    )
    step, deadline = _next_step(order, side, nego)
    client = (
        ClientOut(**asdict(await client_svc.profile(session, order.seller_id, order.buyer_id, order.id)))
        if side == By.seller
        else None
    )
    return OrderOut(
        id=order.id,
        number=order.number,
        status=order.status,
        my_side=str(side),
        buyer=_brief(buyer),
        seller=_brief(seller),
        delivery_date=order.delivery_date,
        slot_from=order.slot_from,
        slot_to=order.slot_to,
        express=order.express,
        subtotal_amd=order.subtotal_amd,
        discount_amd=order.discount_amd,
        delivery_fee_amd=order.delivery_fee_amd,
        total_amd=order.total_amd,
        payment_status=order.payment_status,
        cancel_reason=order.cancel_reason,
        buyer_note=order.buyer_note,
        seller_note=order.seller_note,
        created_at=order.created_at,
        lines=[OrderLineOut.model_validate(li) for li in order.lines],
        negotiation=_negotiation_out(nego, side) if nego else None,
        allowed_actions=_actions(order, side, nego),
        address=AddressOut.model_validate(address) if address else None,
        next_step=step,
        deadline=deadline,
        unread_messages=await notification_svc.unread_messages(session, actor.user, order),
        saving_amd=_saving(order),
        events=[EventOut(type=e.type, at=e.at, auto=bool((e.payload or {}).get("auto"))) for e in events],
        client=client,
    )


async def _reload(session: Session, actor: Actor, order_id: uuid.UUID) -> OrderOut:
    order = await order_svc.get_for_actor(session, actor, order_id)
    await session.refresh(order, ["lines"])
    return await order_out(session, actor, order)


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    session: Session,
    actor: CurrentActor,
    status: Annotated[list[OrderStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    orders, total = await order_svc.list_for_actor(session, actor, status, limit, offset)
    other_ids = {o.seller_id if actor.is_buyer else o.buyer_id for o in orders}
    names = {org.id: org for org in [await session.get(Organization, i) for i in other_ids] if org}
    negos = await _negotiations(session, [o.id for o in orders])
    side = By.buyer if actor.is_buyer else By.seller
    steps = {o.id: _next_step(o, side, negos.get(o.id)) for o in orders}
    return OrderPage(
        items=[
            OrderListItem(
                id=o.id,
                number=o.number,
                status=o.status,
                counterparty=_brief(names.get(o.seller_id if actor.is_buyer else o.buyer_id)),
                total_amd=o.total_amd,
                delivery_date=o.delivery_date,
                slot_from=o.slot_from,
                slot_to=o.slot_to,
                express=o.express,
                created_at=o.created_at,
                lines_count=len(o.lines),
                payment_status=o.payment_status,
                my_side=str(side),
                next_step=steps[o.id][0],
                deadline=steps[o.id][1],
                saving_amd=_saving(o),
                preview=[li.name_snapshot for li in o.lines[:3]],
            )
            for o in orders
        ],
        total=total,
    )


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, session: Session, actor: CurrentActor) -> OrderOut:
    return await order_out(session, actor, await order_svc.get_for_actor(session, actor, order_id))


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
async def confirm(order_id: uuid.UUID, body: ConfirmIn, session: Session, actor: CurrentActor) -> OrderOut:
    qty = {li.order_line_id: li.qty for li in body.lines}
    await order_svc.confirm(session, actor, order_id, qty, utcnow(), body.note)
    await session.commit()
    return await _reload(session, actor, order_id)


@router.post("/orders/{order_id}/accept", response_model=OrderOut)
async def accept(order_id: uuid.UUID, body: AcceptIn, session: Session, actor: CurrentActor) -> OrderOut:
    received = {li.order_line_id: li.qty for li in body.lines}
    await order_svc.accept(session, actor, order_id, received, utcnow())
    await session.commit()
    return await _reload(session, actor, order_id)


@router.post("/orders/{order_id}/negotiation/respond", response_model=OrderOut)
async def respond(
    order_id: uuid.UUID,
    body: RespondIn,
    session: Session,
    actor: CurrentActor,
    idempotency_key: Annotated[str | None, Header(max_length=80)] = None,
):
    now = utcnow()
    endpoint = f"respond:{order_id}"
    stored = await idempotency.replay(session, actor.org.id, idempotency_key, endpoint, now)
    if stored is not None:
        return JSONResponse(stored.response, status_code=stored.status_code)
    await nego_svc.respond(
        session,
        actor,
        order_id,
        now,
        lines=[nego_svc.LineAnswer(a.order_line_id, a.decision, a.price_amd) for a in body.lines] or None,
        whole=nego_svc.OrderAnswer(body.order.decision, body.order.percent_bp) if body.order else None,
    )
    await session.flush()
    out = await _reload(session, actor, order_id)
    idempotency.store(session, actor.org.id, idempotency_key, endpoint, 200, out.model_dump(mode="json"))
    await session.commit()
    return out


@router.post("/orders/{order_id}/reorder")
async def reorder(order_id: uuid.UUID, session: Session, actor: CurrentActor) -> dict[str, int]:
    added = await order_svc.reorder(session, actor, order_id)
    await session.commit()
    return {"added": added}


@router.post("/agreements", response_model=AgreementOut, status_code=201)
async def create_agreement(body: AgreementIn, session: Session, actor: CurrentActor):
    agreement = await agreement_svc.create_from_line(session, actor, body.order_line_id, body.days, utcnow())
    await session.commit()
    return agreement


@router.get("/agreements", response_model=list[AgreementOut])
async def list_agreements(session: Session, actor: CurrentActor):
    return await agreement_svc.list_for_actor(session, actor)


@router.get("/orders/{order_id}/documents", response_model=list[DocumentOut])
async def order_documents(order_id: uuid.UUID, session: Session, actor: CurrentActor):
    order = await order_svc.get_for_actor(session, actor, order_id)
    return await doc_svc.list_for_order(session, order)


@router.post("/orders/{order_id}/documents", response_model=list[DocumentOut])
async def issue_documents(order_id: uuid.UUID, session: Session, actor: CurrentActor):
    """Issue the printable documents due for the order's status (also for orders confirmed earlier)."""
    order = await order_svc.get_for_actor(session, actor, order_id, lock=True)
    await doc_svc.ensure_for_order(session, order, utcnow())
    await session.commit()
    return await doc_svc.list_for_order(session, order)


@router.post("/orders/{order_id}/documents/src", response_model=DocumentOut)
async def record_src_number(order_id: uuid.UUID, body: SrcNumberIn, session: Session, actor: CurrentActor):
    order = await order_svc.get_for_actor(session, actor, order_id)
    doc = await doc_svc.set_src_number(session, actor, order, body.kind, body.number, utcnow())
    await session.commit()
    return doc


@router.get("/documents", response_model=DocumentPage)
async def list_documents(
    session: Session,
    actor: CurrentActor,
    kind: Annotated[list[DocumentKind] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    """The company's documents on the side it acts on now (X-Mode): purchases or sales."""

    def start(d: date | None) -> datetime | None:
        return datetime.combine(d, time.min, tzinfo=YEREVAN) if d else None

    rows, total = await doc_svc.list_for_org(
        session,
        actor,
        kinds=kind,
        date_from=start(date_from),
        date_to=start(date_to + timedelta(days=1)) if date_to else None,
        q=q,
        limit=limit,
        offset=offset,
    )
    return DocumentPage(
        items=[
            DocumentRow(
                id=d.id,
                kind=d.kind,
                number=d.number,
                external_id=d.external_id,
                status=d.status,
                issued_at=d.issued_at,
                order_id=o.id,
                order_number=o.number,
                order_total_amd=o.total_amd,
                counterparty=org.display_name,
            )
            for d, o, org in rows
        ],
        total=total,
    )


@router.get("/orders/{order_id}/messages", response_model=list[MessageOut])
async def list_messages(order_id: uuid.UUID, session: Session, actor: CurrentActor):
    rows = await chat_svc.list_messages(session, actor, order_id, utcnow())
    await session.commit()
    return [
        MessageOut.model_validate(m).model_copy(update={"mine": m.user_id == actor.user.id}) for m in rows
    ]


@router.post("/orders/{order_id}/messages", response_model=MessageOut, status_code=201)
async def post_message(order_id: uuid.UUID, body: MessageIn, session: Session, actor: CurrentActor):
    msg = await chat_svc.post_message(session, actor, order_id, body.text, utcnow())
    await session.commit()
    return MessageOut.model_validate(msg).model_copy(update={"mine": True})


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, session: Session, actor: CurrentActor):
    doc, _ = await doc_svc.get_for_actor(session, actor, document_id)
    return doc


# Declared last: the generic path must not shadow /reorder and /negotiation/respond
@router.post("/orders/{order_id}/{action}", response_model=OrderOut)
async def simple_action(
    order_id: uuid.UUID,
    action: Annotated[str, "cancel | reject | ship | deliver | mark_paid"],
    session: Session,
    actor: CurrentActor,
    body: ReasonIn | None = None,
) -> OrderOut:
    now = utcnow()
    if action == "mark_paid":
        await order_svc.mark_paid(session, actor, order_id, now)
    elif action in ("cancel", "reject", "ship", "deliver"):
        await order_svc.simple_action(
            session, actor, order_id, OrderAction(action), now, body.reason if body else None
        )
    else:
        raise NotFound("unknown_action")
    await session.commit()
    return await _reload(session, actor, order_id)
