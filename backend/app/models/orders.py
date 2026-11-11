"""Cart, checkouts, per-seller orders, order lines, event journal, claims."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Sequence,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Amd, Base, I18nText, Qty, Timestamped, UUIDPk, str_enum
from app.models.catalog import ClientSource, Unit

order_number_seq = Sequence("order_number_seq", metadata=Base.metadata)


class OrderStatus(StrEnum):
    negotiating = "negotiating"
    placed = "placed"
    confirmed = "confirmed"
    rejected = "rejected"
    cancelled = "cancelled"
    delivering = "delivering"
    delivered = "delivered"
    disputed = "disputed"
    accepted = "accepted"


class PaymentStatus(StrEnum):
    unpaid = "unpaid"
    paid = "paid"


class PriceSource(StrEnum):
    list = "list"
    tier = "tier"
    agreement = "agreement"
    negotiated = "negotiated"


class ClaimType(StrEnum):
    short = "short"  # delivered less
    damaged = "damaged"
    wrong = "wrong"  # wrong item
    missing = "missing"  # not delivered at all


class ClaimStatus(StrEnum):
    open = "open"
    accepted = "accepted"
    rejected = "rejected"
    escalated = "escalated"


class CartItem(UUIDPk, Timestamped, Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("buyer_id", "product_id"),
        CheckConstraint("qty > 0", name="qty_positive"),
    )

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    qty: Mapped[Decimal] = mapped_column(Qty)
    note: Mapped[str | None] = mapped_column(Text)
    # Buyer's proposed unit price; turns into a negotiation at checkout
    offer_price_amd: Mapped[int | None] = mapped_column(Amd)


class Checkout(UUIDPk, Timestamped, Base):
    """One "send all" press: groups the per-seller orders created from one cart."""

    __tablename__ = "checkouts"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("addresses.id", ondelete="SET NULL"))

    orders: Mapped[list["Order"]] = relationship(back_populates="checkout")


class Order(UUIDPk, Timestamped, Base):
    __tablename__ = "orders"
    # Read the server-generated number back on INSERT (RETURNING), so it is known right after flush
    __mapper_args__ = {"eager_defaults": True}

    # Human-readable, e.g. EASO-26-000123
    number: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        server_default=text(
            "'EASO-' || to_char(now(), 'YY') || '-' || lpad(nextval('order_number_seq')::text, 6, '0')"
        ),
    )
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("checkouts.id", ondelete="SET NULL"), index=True
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    placed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("addresses.id", ondelete="SET NULL"))
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus), index=True)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    # The chosen delivery slot on delivery_date (None: any time the seller delivers)
    slot_from: Mapped[time | None] = mapped_column(Time)
    slot_to: Mapped[time | None] = mapped_column(Time)
    # Urgent same-day delivery; its fee is part of delivery_fee_amd and kept here as a snapshot
    express: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    express_fee_amd: Mapped[int] = mapped_column(Amd, default=0, server_default="0")
    subtotal_amd: Mapped[int] = mapped_column(Amd, default=0)
    discount_amd: Mapped[int] = mapped_column(Amd, default=0)  # order-level negotiated discount
    delivery_fee_amd: Mapped[int] = mapped_column(Amd, default=0)
    total_amd: Mapped[int] = mapped_column(Amd, default=0)
    # Snapshots taken when the order is accepted, so later rule changes never rewrite history
    client_source: Mapped[ClientSource | None] = mapped_column(str_enum(ClientSource))
    commission_rate_bp: Mapped[int | None] = mapped_column(Integer)
    commission_amd: Mapped[int | None] = mapped_column(Amd)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus), default=PaymentStatus.unpaid
    )
    buyer_note: Mapped[str | None] = mapped_column(Text)
    seller_note: Mapped[str | None] = mapped_column(Text)
    # e.g. offer_expired, buyer, seller_rejected
    cancel_reason: Mapped[str | None] = mapped_column(String(48))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    checkout: Mapped[Checkout | None] = relationship(back_populates="orders")
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.position"
    )


class OrderLine(UUIDPk, Timestamped, Base):
    __tablename__ = "order_lines"
    __table_args__ = (CheckConstraint("qty_ordered > 0", name="qty_positive"),)

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    name_snapshot: Mapped[dict] = mapped_column(I18nText)
    unit: Mapped[Unit] = mapped_column(str_enum(Unit))
    qty_ordered: Mapped[Decimal] = mapped_column(Qty)
    qty_confirmed: Mapped[Decimal | None] = mapped_column(Qty)
    qty_received: Mapped[Decimal | None] = mapped_column(Qty)
    # Unit price before negotiation: catalog, tier or contract (see price_source). Declined offers keep it.
    list_price_amd: Mapped[int] = mapped_column(Amd)
    # Null while the line is under negotiation
    final_price_amd: Mapped[int | None] = mapped_column(Amd)
    price_source: Mapped[PriceSource] = mapped_column(str_enum(PriceSource), default=PriceSource.list)
    line_total_amd: Mapped[int | None] = mapped_column(Amd)
    vat_rate_bp: Mapped[int] = mapped_column(Integer, default=0)  # snapshot; 0 = seller not a VAT payer

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderEvent(UUIDPk, Base):
    """Append-only journal of everything that happened to an order (status changes, offers, edits)."""

    __tablename__ = "order_events"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Null = system or seller autopilot
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict | None] = mapped_column(I18nText)


class OrderMessage(UUIDPk, Base):
    """Chat between the buyer and the seller inside one order."""

    __tablename__ = "order_messages"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    side: Mapped[str] = mapped_column(String(8))  # buyer | seller
    text: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Claim(UUIDPk, Timestamped, Base):
    __tablename__ = "claims"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"))
    opened_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[ClaimType] = mapped_column(str_enum(ClaimType))
    qty: Mapped[Decimal | None] = mapped_column(Qty)
    photos: Mapped[list[str]] = mapped_column(ARRAY(String(500)), default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(str_enum(ClaimStatus), default=ClaimStatus.open)
    # Amount taken off the order when the claim is accepted
    resolution_amd: Mapped[int | None] = mapped_column(Amd)
