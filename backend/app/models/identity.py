"""People and organizations: users, orgs, memberships, addresses, seller terms, invites, consents."""

import uuid
from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Amd, Base, I18nText, Timestamped, UUIDPk, str_enum


class Locale(StrEnum):
    hy = "hy"
    ru = "ru"
    en = "en"


class OrgKind(StrEnum):
    """A side of the marketplace. A company can play both; the user switches the mode in the app."""

    buyer = "buyer"
    seller = "seller"


class AuthProvider(StrEnum):
    email = "email"
    google = "google"
    telegram = "telegram"


class OrgStatus(StrEnum):
    pending = "pending"  # seller waiting for moderation
    active = "active"
    blocked = "blocked"


class TaxRegime(StrEnum):
    vat = "vat"
    turnover = "turnover"
    micro = "micro"


class VenueType(StrEnum):
    cafe = "cafe"
    restaurant = "restaurant"
    bar = "bar"
    bakery = "bakery"
    hotel = "hotel"
    catering = "catering"
    other = "other"


class VerificationStatus(StrEnum):
    none = "none"  # nothing submitted yet
    pending = "pending"  # submitted, waiting for an operator
    verified = "verified"
    rejected = "rejected"


class LegalDoc(StrEnum):
    terms = "terms"  # public offer: the agreement between Easo and a company
    privacy = "privacy"  # personal data policy


class Role(StrEnum):
    owner = "owner"
    manager = "manager"  # seller-side sales manager
    buyer_staff = "buyer_staff"  # chef, barman: builds carts, limited ordering


class User(UUIDPk, Timestamped, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    locale: Mapped[Locale] = mapped_column(str_enum(Locale), default=Locale.hy)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Copies of in-app notifications; Telegram also needs telegram_id (linked through the bot)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Organization(UUIDPk, Timestamped, Base):
    __tablename__ = "organizations"

    # One company may buy and sell (a bakery buys flour and sells bread). Selling needs verification.
    is_buyer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_name: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(200))
    # Armenian taxpayer ID (ՀՎՀՀ), 8 digits
    tin: Mapped[str | None] = mapped_column(String(16), unique=True)
    tax_regime: Mapped[TaxRegime | None] = mapped_column(str_enum(TaxRegime))
    status: Mapped[OrgStatus] = mapped_column(str_enum(OrgStatus), default=OrgStatus.active)
    venue_type: Mapped[VenueType | None] = mapped_column(str_enum(VenueType))
    # Requisites printed on invoices, delivery notes and acts
    registration_number: Mapped[str | None] = mapped_column(String(32))  # state register number
    legal_address: Mapped[str | None] = mapped_column(Text)
    director_name: Mapped[str | None] = mapped_column(String(200))
    bank_name: Mapped[str | None] = mapped_column(String(200))
    bank_account: Mapped[str | None] = mapped_column(String(34))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    # Where the registry data came from: demo, manual, or a provider name
    registry_source: Mapped[str | None] = mapped_column(String(32))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        str_enum(VerificationStatus), default=VerificationStatus.none
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="organization")
    seller_profile: Mapped["SellerProfile | None"] = relationship(back_populates="organization")


class UserIdentity(UUIDPk, Timestamped, Base):
    """A way to sign in: e-mail address, Google account or Telegram account. One user may have several."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[AuthProvider] = mapped_column(str_enum(AuthProvider))
    external_id: Mapped[str] = mapped_column(String(255))  # e-mail, Google sub, Telegram id
    email: Mapped[str | None] = mapped_column(String(320))
    username: Mapped[str | None] = mapped_column(String(255))


class LoginCode(UUIDPk, Base):
    """One-time code sent by e-mail. Only a hash is stored."""

    __tablename__ = "login_codes"

    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(UUIDPk, Timestamped, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role))
    # Buyer staff: max order total they may place alone; null = no limit
    order_limit_amd: Mapped[int | None] = mapped_column(Amd)
    can_negotiate: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class Address(UUIDPk, Timestamped, Base):
    __tablename__ = "addresses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(String(64))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    delivery_notes: Mapped[str | None] = mapped_column(Text)
    # Who receives the goods at this address (a manager, a chef): shown to the seller and on the delivery note
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    # Removed addresses stay for old orders and documents, they just leave the list
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SellerProfile(Timestamped, Base):
    __tablename__ = "seller_profiles"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    description: Mapped[dict | None] = mapped_column(I18nText)
    min_order_amd: Mapped[int] = mapped_column(Amd, default=0)
    # Orders placed before cutoff_time are delivered after lead_days
    cutoff_time: Mapped[time] = mapped_column(Time, default=time(16, 0))
    lead_days: Mapped[int] = mapped_column(SmallInteger, default=1)
    delivery_fee_amd: Mapped[int] = mapped_column(Amd, default=0)
    free_delivery_from_amd: Mapped[int | None] = mapped_column(Amd)
    # Below the minimum the buyer may still order, paying this delivery fee; None: the minimum is a must
    small_order_fee_amd: Mapped[int | None] = mapped_column(Amd)
    # Delivery window on the delivery day, cut into slots of slot_hours (0: no slots)
    window_from: Mapped[time] = mapped_column(Time, default=time(9, 0), server_default="09:00")
    window_to: Mapped[time] = mapped_column(Time, default=time(18, 0), server_default="18:00")
    slot_hours: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # Urgent delivery the same day: ordered before express_until, delivered within express_hours, for a fee
    express_fee_amd: Mapped[int | None] = mapped_column(Amd)
    express_until: Mapped[time] = mapped_column(Time, default=time(12, 0), server_default="12:00")
    express_hours: Mapped[int] = mapped_column(SmallInteger, default=3, server_default="3")
    express_min_amd: Mapped[int] = mapped_column(Amd, default=0, server_default="0")
    offer_response_hours: Mapped[int] = mapped_column(SmallInteger, default=24)
    logo_url: Mapped[str | None] = mapped_column(String(500))

    organization: Mapped[Organization] = relationship(back_populates="seller_profile")


class DeliveryZone(UUIDPk, Timestamped, Base):
    __tablename__ = "delivery_zones"
    __table_args__ = (UniqueConstraint("seller_id", "district"),)

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    district: Mapped[str] = mapped_column(String(64))
    # ISO weekdays 1..7
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=lambda: [1, 2, 3, 4, 5, 6])


class Invite(UUIDPk, Timestamped, Base):
    """A seller's standing link/QR (/s/<code>), also the supplier's public page. Buyers who join through it
    become the seller's own clients (0% commission). Reusable; a new code revokes the old one."""

    __tablename__ = "invites"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(32), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consent(UUIDPk, Base):
    """A user accepted a version of a legal document. Kept forever: it is the proof of acceptance."""

    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("user_id", "document", "version"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The company being registered, when accepted on sign-up
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    document: Mapped[LegalDoc] = mapped_column(str_enum(LegalDoc))
    version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
