"""Platform money and plumbing: commission rules and statements, notifications, audit log, tax documents."""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Amd, Base, I18nText, Timestamped, UUIDPk, str_enum
from app.models.catalog import ClientSource


class StatementStatus(StrEnum):
    draft = "draft"
    issued = "issued"
    paid = "paid"


class Channel(StrEnum):
    app = "app"  # the bell in the web app; the other channels copy what it shows
    telegram = "telegram"
    push = "push"
    email = "email"


class DocumentKind(StrEnum):
    invoice = "invoice"  # invoice for payment (Հաշիվ վճարման համար), with bank details
    delivery_note = "delivery_note"  # goods delivery note (Ապրանքագիր) with transport section
    acceptance_act = "acceptance_act"  # acceptance act (Ընդունման-հանձնման ակտ), received quantities
    tax_invoice = "tax_invoice"  # SRC e-invoice issued in the tax system; we keep its number
    waybill = "waybill"  # SRC e-waybill; we keep its number


class DocumentStatus(StrEnum):
    draft = "draft"
    issued = "issued"
    failed = "failed"
    cancelled = "cancelled"


class CommissionRule(UUIDPk, Timestamped, Base):
    """Rate in basis points. category_id null = default for all categories."""

    __tablename__ = "commission_rules"

    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    client_source: Mapped[ClientSource] = mapped_column(str_enum(ClientSource))
    rate_bp: Mapped[int] = mapped_column(Integer)
    first_order_bonus_bp: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[date] = mapped_column(Date)


class CommissionStatement(UUIDPk, Timestamped, Base):
    """Monthly commission invoice from Easo to a seller."""

    __tablename__ = "commission_statements"
    __table_args__ = (UniqueConstraint("seller_id", "period"),)

    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    period: Mapped[date] = mapped_column(Date)  # first day of the month
    turnover_amd: Mapped[int] = mapped_column(Amd, default=0)
    commission_amd: Mapped[int] = mapped_column(Amd, default=0)
    status: Mapped[StatementStatus] = mapped_column(str_enum(StatementStatus), default=StatementStatus.draft)
    file_url: Mapped[str | None] = mapped_column(String(500))


class Notification(UUIDPk, Timestamped, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[Channel] = mapped_column(str_enum(Channel))
    type: Mapped[str] = mapped_column(String(48))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    payload: Mapped[dict | None] = mapped_column(I18nText)
    # (event, recipient, channel): a retried job never sends the same message twice
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPk, Timestamped, Base):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Set when an operator acts "as" a customer
    impersonated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict | None] = mapped_column(I18nText)


class Document(UUIDPk, Timestamped, Base):
    """A document of an order.

    Printable ones (invoice, delivery note, act) carry a frozen payload: requisites, lines and totals as they
    were when issued. SRC ones (tax_invoice, waybill) keep the number from the tax system.
    """

    __tablename__ = "documents"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[DocumentKind] = mapped_column(str_enum(DocumentKind))
    external_id: Mapped[str | None] = mapped_column(String(64))  # SRC number
    number: Mapped[str | None] = mapped_column(String(32))  # our number, e.g. INV-2026-0007
    status: Mapped[DocumentStatus] = mapped_column(str_enum(DocumentStatus), default=DocumentStatus.draft)
    payload: Mapped[dict | None] = mapped_column(I18nText)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentCounter(Base):
    """Gap-free numbering per seller, document kind and year."""

    __tablename__ = "document_counters"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[DocumentKind] = mapped_column(str_enum(DocumentKind), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class OutboxEvent(UUIDPk, Base):
    """Side effects to run after commit (docs/03-architecture.md §3.6), written in the same transaction."""

    __tablename__ = "outbox"

    topic: Mapped[str] = mapped_column(String(48), index=True)  # e.g. order.placed, offer.received
    payload: Mapped[dict] = mapped_column(I18nText)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class IdempotencyKey(Base):
    """Stored response of a non-repeatable request (checkout, offer answers), replayed on retry for 24 h."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    endpoint: Mapped[str] = mapped_column(String(80))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(I18nText)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
