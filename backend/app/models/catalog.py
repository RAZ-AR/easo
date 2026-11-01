"""Catalog and prices: categories, products, tiers, autopilot rules, seller-buyer links, contract prices."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Amd, Base, I18nText, Qty, Timestamped, UUIDPk, str_enum


class Unit(StrEnum):
    kg = "kg"
    l = "l"  # noqa: E741
    pcs = "pcs"
    pack = "pack"


class RuleScope(StrEnum):
    seller = "seller"
    category = "category"
    product = "product"


class ClientSource(StrEnum):
    seller_invited = "seller_invited"  # the seller brought this buyer: 0% commission
    platform = "platform"  # the buyer found the seller on Easo


class LinkStatus(StrEnum):
    active = "active"
    blocked = "blocked"


class AgreementStatus(StrEnum):
    active = "active"
    expired = "expired"
    cancelled = "cancelled"


class Category(UUIDPk, Timestamped, Base):
    __tablename__ = "categories"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[dict] = mapped_column(I18nText)
    sort: Mapped[int] = mapped_column(SmallInteger, default=0)
    # Commission in basis points (100 bp = 1%); null = take from commission_rules
    default_commission_bp: Mapped[int | None] = mapped_column(Integer)


class Product(UUIDPk, Timestamped, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("seller_id", "sku"),
        CheckConstraint("base_price_amd > 0", name="price_positive"),
        CheckConstraint("min_qty > 0 AND qty_step > 0", name="qty_positive"),
        # Trigram search over all three languages + transliteration (Postgres has no Armenian FTS dictionary)
        Index(
            "ix_products_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    sku: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[dict] = mapped_column(I18nText)
    description: Mapped[dict | None] = mapped_column(I18nText)
    unit: Mapped[Unit] = mapped_column(str_enum(Unit))
    pack_size: Mapped[str | None] = mapped_column(String(64))  # e.g. "box of 12", "5 kg bag"
    base_price_amd: Mapped[int] = mapped_column(Amd)  # per unit
    vat_included: Mapped[bool] = mapped_column(Boolean, default=True)
    vat_rate_bp: Mapped[int] = mapped_column(Integer, default=2000)  # 20% VAT; 0 for turnover-tax sellers
    min_qty: Mapped[Decimal] = mapped_column(Qty, default=1)
    qty_step: Mapped[Decimal] = mapped_column(Qty, default=1)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    # A seller's paid extra (delivery to the hour, unloading): not in the catalog, offered in the cart
    is_service: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Storage keys of photos, the first one is the main photo (each has a "-s" thumbnail next to it)
    images: Mapped[list[str]] = mapped_column(ARRAY(String(500)), default=list)
    # Photos the seller keeps but does not show to buyers (a subset of images)
    hidden_images: Mapped[list[str]] = mapped_column(ARRAY(String(500)), default=list, server_default="{}")
    # Characteristics by code (domain/product_specs.py): {"thermal": "chilled", "country": "AM", "fat": 45}
    specs: Mapped[dict] = mapped_column(I18nText, default=dict, server_default="{}")
    search_text: Mapped[str] = mapped_column(Text, default="")
    # Archived products disappear from the catalog but stay in order history
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tiers: Mapped[list["PriceTier"]] = relationship(
        back_populates="product", order_by="PriceTier.min_qty", cascade="all, delete-orphan"
    )
    documents: Mapped[list["ProductDocument"]] = relationship(
        back_populates="product", order_by="ProductDocument.created_at", cascade="all, delete-orphan"
    )


class ProductDocKind(StrEnum):
    declaration = "declaration"
    certificate = "certificate"
    veterinary = "veterinary"
    phytosanitary = "phytosanitary"
    halal = "halal"
    quality = "quality"
    other = "other"


class ProductDocument(UUIDPk, Timestamped, Base):
    """A certificate or declaration for one product. Buyers who signed in can open the file."""

    __tablename__ = "product_documents"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ProductDocKind] = mapped_column(str_enum(ProductDocKind))
    number: Mapped[str | None] = mapped_column(String(80))
    valid_until: Mapped[date | None] = mapped_column(Date)
    storage_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(80))
    original_name: Mapped[str | None] = mapped_column(String(200))

    product: Mapped[Product] = relationship(back_populates="documents")


class PriceTier(UUIDPk, Timestamped, Base):
    """Volume price: from min_qty units the price per unit is price_amd."""

    __tablename__ = "price_tiers"
    __table_args__ = (UniqueConstraint("product_id", "min_qty"),)

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    min_qty: Mapped[Decimal] = mapped_column(Qty)
    price_amd: Mapped[int] = mapped_column(Amd)

    product: Mapped[Product] = relationship(back_populates="tiers")


class OfferRule(UUIDPk, Timestamped, Base):
    """Seller autopilot for price offers. The buyer never sees these numbers (docs/02-mvp.md §6.3)."""

    __tablename__ = "offer_rules"
    __table_args__ = (UniqueConstraint("seller_id", "scope", "scope_id"),)

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[RuleScope] = mapped_column(str_enum(RuleScope))
    # category_id or product_id; null when scope = seller
    scope_id: Mapped[uuid.UUID | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_line_amd: Mapped[int | None] = mapped_column(Amd)
    # Absolute price for a product rule; percent of list price (bp) for category/seller rules
    floor_price_amd: Mapped[int | None] = mapped_column(Amd)
    floor_pct_bp: Mapped[int | None] = mapped_column(Integer)
    auto_accept_price_amd: Mapped[int | None] = mapped_column(Amd)
    auto_accept_pct_bp: Mapped[int | None] = mapped_column(Integer)
    auto_counter: Mapped[bool] = mapped_column(Boolean, default=True)
    only_regulars: Mapped[bool] = mapped_column(Boolean, default=False)
    min_qty: Mapped[Decimal | None] = mapped_column(Qty)
    # The seller lets buyers see the auto-accept discount as a hint ("agrees to up to 3% at once")
    share_hint: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ClientLink(UUIDPk, Timestamped, Base):
    """A seller-buyer pair. Its source decides the commission."""

    __tablename__ = "client_links"
    __table_args__ = (UniqueConstraint("seller_id", "buyer_id"),)

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[ClientSource] = mapped_column(str_enum(ClientSource))
    first_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LinkStatus] = mapped_column(str_enum(LinkStatus), default=LinkStatus.active)
    # The seller's link the buyer joined through (seller_invited)
    invite_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invites.id", ondelete="SET NULL"))


class PriceAgreement(UUIDPk, Timestamped, Base):
    """Contract price fixed for one buyer after a successful negotiation."""

    __tablename__ = "price_agreements"
    __table_args__ = (CheckConstraint("valid_until >= valid_from", name="valid_range"),)

    client_link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client_links.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    price_amd: Mapped[int] = mapped_column(Amd)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date] = mapped_column(Date)
    source_negotiation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("negotiations.id", ondelete="SET NULL")
    )
    status: Mapped[AgreementStatus] = mapped_column(str_enum(AgreementStatus), default=AgreementStatus.active)
    note: Mapped[str | None] = mapped_column(Text)
