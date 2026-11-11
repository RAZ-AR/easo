import uuid
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from app.auth.deps import CurrentActor, Session
from app.config import get_settings
from app.platform import idempotency
from app.platform.authz import Action
from app.platform.clock import utcnow
from app.schemas import (
    AddressOut,
    CartGroupOut,
    CartItemIn,
    CartLineOut,
    CartOut,
    CheckoutIn,
    ExpressOut,
    SellerBrief,
    SlotOut,
    SuggestionOut,
)
from app.services import cart as cart_svc
from app.services import suggestions as suggestion_svc
from app.services.checkout import DeliveryChoice
from app.services.checkout import checkout as do_checkout

from .orders import order_out

router = APIRouter(tags=["cart"])


async def _cart_out(session: Session, actor: CurrentActor, address_id: uuid.UUID | None = None) -> CartOut:
    view = await cart_svc.build_cart(session, actor, utcnow(), address_id)
    groups = []
    for g in view.groups:
        groups.append(
            CartGroupOut(
                seller=SellerBrief(id=g.seller.id, display_name=g.seller.display_name),
                lines=[
                    CartLineOut(
                        product_id=li.product.id,
                        name=li.product.name,
                        unit=li.product.unit,
                        qty=li.item.qty,
                        min_qty=li.product.min_qty,
                        qty_step=li.product.qty_step,
                        base_price_amd=li.product.base_price_amd,
                        unit_price_amd=li.unit_price.amount_amd,
                        price_source=li.unit_price.source,
                        line_total_amd=li.line_total_amd,
                        offer_price_amd=li.item.offer_price_amd,
                        offers_allowed=li.product.allow_offers,
                        offer_min_line_amd=li.offer_min_line_amd,
                        offer_hint_bp=li.offer_hint_bp,
                        problems=li.problems,
                    )
                    for li in g.lines
                ],
                subtotal_amd=g.totals.subtotal_amd,
                delivery_fee_amd=g.totals.delivery_fee_amd,
                total_amd=g.totals.total_amd,
                min_order_amd=g.profile.min_order_amd if g.profile else 0,
                missing_amd=g.missing_amd,
                free_delivery_from_amd=g.profile.free_delivery_from_amd if g.profile else None,
                delivery_date=g.delivery_date,
                offer_response_hours=(
                    g.profile.offer_response_hours if g.profile else get_settings().offer_response_hours
                ),
                problems=g.problems,
                small_order_fee_amd=g.profile.small_order_fee_amd if g.profile else None,
                slots=[SlotOut(start=a, end=b) for a, b in g.slots],
                express=(
                    ExpressOut(
                        fee_amd=g.profile.express_fee_amd,
                        hours=g.profile.express_hours,
                        until=g.profile.express_until,
                        min_amd=g.profile.express_min_amd,
                        problem=g.express_problem,
                    )
                    if g.profile and g.profile.express_fee_amd is not None
                    else None
                ),
            )
        )
    return CartOut(
        groups=groups,
        total_amd=view.total_amd,
        address=AddressOut.model_validate(view.address) if view.address else None,
        problems=view.problems,
        can_negotiate=actor.can(Action.offer_make),
        offer_max_rounds=get_settings().offer_max_rounds,
    )


@router.get("/cart/suggestions", response_model=list[SuggestionOut])
async def cart_suggestions(
    session: Session, actor: CurrentActor, address_id: uuid.UUID | None = None
) -> list[SuggestionOut]:
    items = await suggestion_svc.for_cart(session, actor, utcnow(), address_id)
    return [
        SuggestionOut(
            kind=s.kind,
            seller=SellerBrief(id=s.seller.id, display_name=s.seller.display_name),
            product_id=s.product.id,
            name=s.product.name,
            unit=s.product.unit,
            price_amd=s.price_amd,
            qty=s.qty,
            min_qty=s.product.min_qty,
            qty_step=s.product.qty_step,
            gap_amd=s.gap_amd,
            saving_amd=s.saving_amd,
            last_qty=s.last_qty,
        )
        for s in items
    ]


@router.get("/cart", response_model=CartOut)
async def get_cart(session: Session, actor: CurrentActor, address_id: uuid.UUID | None = None) -> CartOut:
    return await _cart_out(session, actor, address_id)


@router.put("/cart/items/{product_id}", response_model=CartOut)
async def put_item(product_id: uuid.UUID, body: CartItemIn, session: Session, actor: CurrentActor) -> CartOut:
    await cart_svc.set_item(session, actor, product_id, body.qty, body.offer_price_amd, body.note)
    await session.commit()
    return await _cart_out(session, actor)


@router.delete("/cart/items/{product_id}", response_model=CartOut)
async def delete_item(product_id: uuid.UUID, session: Session, actor: CurrentActor) -> CartOut:
    await cart_svc.remove_item(session, actor, product_id)
    await session.commit()
    return await _cart_out(session, actor)


@router.delete("/cart", response_model=CartOut)
async def clear_cart(session: Session, actor: CurrentActor) -> CartOut:
    await cart_svc.clear(session, actor)
    await session.commit()
    return await _cart_out(session, actor)


@router.post("/checkout", status_code=201)
async def checkout(
    body: CheckoutIn,
    session: Session,
    actor: CurrentActor,
    idempotency_key: Annotated[str | None, Header(max_length=80)] = None,
):
    now = utcnow()
    stored = await idempotency.replay(session, actor.org.id, idempotency_key, "checkout", now)
    if stored is not None:
        return JSONResponse(stored.response, status_code=stored.status_code)
    orders = await do_checkout(
        session,
        actor,
        now,
        address_id=body.address_id,
        order_offers={o.seller_id: o.percent_bp for o in body.order_offers},
        note=body.note,
        deliveries={d.seller_id: DeliveryChoice(d.slot_start, d.express) for d in body.deliveries},
    )
    result = {"orders": [await order_out(session, actor, o) for o in orders]}
    idempotency.store(session, actor.org.id, idempotency_key, "checkout", 201, result)
    await session.commit()
    return result
