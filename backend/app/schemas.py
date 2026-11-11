import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models import (
    ClientSource,
    DocumentKind,
    DocumentStatus,
    FileKind,
    FileStatus,
    LegalDoc,
    LineDecision,
    Locale,
    NegotiationScope,
    NegotiationStatus,
    OfferKind,
    OfferResponse,
    OrderStatus,
    OrgKind,
    OrgStatus,
    PaymentStatus,
    PriceSource,
    Role,
    Side,
    TaxRegime,
    Unit,
    VenueType,
    VerificationStatus,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DevLoginIn(BaseModel):
    email: str


class OrgOut(ORM):
    id: uuid.UUID
    display_name: str
    is_buyer: bool
    is_seller: bool
    status: OrgStatus
    verification_status: VerificationStatus


class MembershipOut(ORM):
    role: Role
    organization: OrgOut
    buyer_ready: bool = False  # registry extract uploaded or verified: buying is open


class MeOut(ORM):
    id: uuid.UUID
    name: str
    email: str | None
    locale: Locale
    is_platform_admin: bool
    memberships: list[MembershipOut]
    # Legal documents whose current version the user still has to accept
    missing_consents: list[LegalDoc] = []


class CategoryOut(ORM):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    slug: str
    name: dict[str, str]


class TierOut(ORM):
    min_qty: Decimal
    price_amd: int


class SellerBrief(BaseModel):
    id: uuid.UUID
    display_name: str


class ImageOut(BaseModel):
    url: str  # large WebP, relative to the API root
    thumb: str  # small WebP for lists
    shown: bool = True  # False: the seller keeps it but buyers do not see it


class ProductDocOut(BaseModel):
    id: uuid.UUID
    kind: str
    number: str | None
    valid_until: date | None
    name: str | None = None  # original file name (seller only)


class ProductOut(ORM):
    id: uuid.UUID
    seller: SellerBrief | None  # hidden from guests
    category_id: uuid.UUID | None
    name: dict[str, str]
    unit: Unit
    pack_size: str | None
    base_price_amd: int
    vat_included: bool
    min_qty: Decimal
    qty_step: Decimal
    in_stock: bool
    allow_offers: bool
    images: list[ImageOut]
    tiers: list[TierOut]
    has_documents: bool = False  # a certificate or declaration is attached


class ProductDetailOut(ProductOut):
    """The product card: everything about the goods."""

    description: dict[str, str] | None
    category: str | None  # slug
    specs: dict
    documents: list[ProductDocOut]
    is_service: bool
    vat_rate_bp: int
    seller_min_order_amd: int | None = None  # hidden from guests with the seller
    seller_lead_days: int | None = None


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    guest: bool = False


# --- cart and checkout -------------------------------------------------------------------------------------


class AddressOut(ORM):
    id: uuid.UUID
    label: str
    text: str
    district: str | None
    contact_name: str | None = None  # who receives the goods
    contact_phone: str | None = None
    delivery_notes: str | None = None


class AddressIn(BaseModel):
    label: str = ""
    text: str
    district: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    delivery_notes: str | None = None
    lat: float | None = None
    lng: float | None = None


class CartItemIn(BaseModel):
    qty: Decimal
    offer_price_amd: int | None = None
    note: str | None = None


class CartLineOut(BaseModel):
    product_id: uuid.UUID
    name: dict[str, str]
    unit: Unit
    qty: Decimal
    min_qty: Decimal
    qty_step: Decimal
    base_price_amd: int
    unit_price_amd: int
    price_source: PriceSource
    line_total_amd: int
    offer_price_amd: int | None
    offers_allowed: bool
    offer_min_line_amd: int
    offer_hint_bp: int | None = None  # "the seller agrees to up to N% at once", if they share it
    problems: list[str]


class SlotOut(BaseModel):
    start: time
    end: time


class ExpressOut(BaseModel):
    fee_amd: int
    hours: int  # delivered within this many hours
    until: time  # order before this time today
    min_amd: int
    problem: str | None  # why it is impossible now (express_too_late, ...); None: available


class CartGroupOut(BaseModel):
    seller: SellerBrief
    lines: list[CartLineOut]
    subtotal_amd: int
    delivery_fee_amd: int
    total_amd: int
    min_order_amd: int
    missing_amd: int
    free_delivery_from_amd: int | None
    delivery_date: date | None
    offer_response_hours: int  # how long the seller has to answer an offer
    problems: list[str]
    small_order_fee_amd: int | None = None  # delivery price below the minimum; None: the minimum is a must
    slots: list[SlotOut] = []  # delivery slots on delivery_date to pick from; empty: no choice
    express: ExpressOut | None = None  # urgent delivery terms, if the seller offers it


class CartOut(BaseModel):
    groups: list[CartGroupOut]
    total_amd: int
    address: AddressOut | None
    problems: list[dict]
    can_negotiate: bool
    offer_max_rounds: int  # moves per side in one negotiation


class SuggestionOut(BaseModel):
    """Something to add to the cart and why (services/suggestions.py)."""

    kind: str  # fill_min | fill_free_delivery | tier | service | usual | complement
    seller: SellerBrief
    product_id: uuid.UUID
    name: dict[str, str]
    unit: Unit
    price_amd: int  # per unit for this buyer
    qty: Decimal  # the quantity to put in the cart (for "tier": the whole line)
    min_qty: Decimal
    qty_step: Decimal
    gap_amd: int | None = None  # how much is missing to the minimum or to free delivery
    saving_amd: int | None = None  # for "tier": saved on the line at the new quantity
    last_qty: Decimal | None = None  # for "usual"


class OrderOfferIn(BaseModel):
    seller_id: uuid.UUID
    percent_bp: int


class DeliveryIn(BaseModel):
    seller_id: uuid.UUID
    slot_start: time | None = None
    express: bool = False


class CheckoutIn(BaseModel):
    address_id: uuid.UUID | None = None
    deliveries: list[DeliveryIn] = []
    order_offers: list[OrderOfferIn] = []
    note: str | None = None


# --- orders and negotiation ---------------------------------------------------------------------------------


class OrderLineOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID | None
    name_snapshot: dict[str, str]
    unit: Unit
    qty_ordered: Decimal
    qty_confirmed: Decimal | None
    qty_received: Decimal | None
    list_price_amd: int
    final_price_amd: int | None
    price_source: PriceSource
    line_total_amd: int | None


class OfferLineOut(ORM):
    order_line_id: uuid.UUID
    proposed_price_amd: int
    decision: LineDecision | None


class OfferOut(ORM):
    id: uuid.UUID
    from_side: Side
    is_auto: bool
    kind: OfferKind
    percent_bp: int | None
    message: str | None
    response: OfferResponse | None
    created_at: datetime
    lines: list[OfferLineOut]


class NegotiationOut(BaseModel):
    status: NegotiationStatus
    scope: NegotiationScope
    round: int
    expires_at: datetime
    my_turn: bool
    rounds_left: int
    offers: list[OfferOut]


class EventOut(BaseModel):
    type: str
    at: datetime
    auto: bool = False


class ClientOut(BaseModel):
    """The buyer's history with this seller; only the seller sees it."""

    source: ClientSource | None
    orders: int
    accepted_orders: int
    avg_order_amd: int
    first_order_at: datetime | None
    last_order_at: datetime | None
    negotiated_orders: int
    agreed_orders: int
    avg_discount_bp: int


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str
    status: OrderStatus
    my_side: str
    buyer: SellerBrief
    seller: SellerBrief
    delivery_date: date | None
    slot_from: time | None = None
    slot_to: time | None = None
    express: bool = False
    subtotal_amd: int
    discount_amd: int
    delivery_fee_amd: int
    total_amd: int
    payment_status: PaymentStatus
    cancel_reason: str | None
    buyer_note: str | None
    seller_note: str | None
    created_at: datetime
    lines: list[OrderLineOut]
    negotiation: NegotiationOut | None
    allowed_actions: list[str]
    address: AddressOut | None
    # What this side should do next (answer_offer, confirm, deliver, accept_delivery, ...) and by when
    next_step: str | None
    deadline: datetime | None
    saving_amd: int  # won in negotiation: lower line prices + order discount
    events: list[EventOut]
    unread_messages: int = 0
    client: ClientOut | None = None  # seller side only


class OrderListItem(BaseModel):
    id: uuid.UUID
    number: str
    status: OrderStatus
    counterparty: SellerBrief
    total_amd: int
    delivery_date: date | None
    slot_from: time | None = None
    slot_to: time | None = None
    express: bool = False
    created_at: datetime
    lines_count: int
    payment_status: PaymentStatus
    my_side: str
    next_step: str | None
    deadline: datetime | None
    saving_amd: int
    preview: list[dict[str, str]]  # names of the first lines


class OrderPage(BaseModel):
    items: list[OrderListItem]
    total: int


class LineQtyIn(BaseModel):
    order_line_id: uuid.UUID
    qty: Decimal


class ConfirmIn(BaseModel):
    lines: list[LineQtyIn] = []
    note: str | None = None


class AcceptIn(BaseModel):
    lines: list[LineQtyIn] = []


class ReasonIn(BaseModel):
    reason: str | None = None


class LineAnswerIn(BaseModel):
    order_line_id: uuid.UUID
    decision: LineDecision
    price_amd: int | None = None


class OrderAnswerIn(BaseModel):
    decision: LineDecision
    percent_bp: int | None = None


class RespondIn(BaseModel):
    lines: list[LineAnswerIn] = []
    order: OrderAnswerIn | None = None


class AgreementIn(BaseModel):
    order_line_id: uuid.UUID
    days: int = 30


class AgreementOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    price_amd: int
    valid_from: date
    valid_until: date


# --- onboarding, verification, documents ------------------------------------------------------------------


class DevRegisterIn(BaseModel):
    name: str
    email: str


class CompanyOut(BaseModel):
    tin: str
    legal_name: str
    registration_number: str | None
    legal_address: str | None
    director_name: str | None
    tax_regime: TaxRegime | None
    active: bool


class LookupOut(BaseModel):
    tin: str
    found: bool
    already_registered: bool
    provider: str  # "demo" means fictional data, "eregister" the state register's public search
    company: CompanyOut | None
    unavailable: bool = False  # the registry did not answer: fill in by hand


class NewOrgIn(BaseModel):
    roles: list[OrgKind]
    tin: str
    legal_name: str
    display_name: str = ""
    tax_regime: TaxRegime | None = None
    registration_number: str | None = None
    legal_address: str | None = None
    director_name: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    phone: str | None = None
    email: str | None = None
    venue_type: VenueType | None = None
    delivery_address: str | None = None
    district: str | None = None
    accept_terms: bool = False
    invite_code: str | None = None


class AddRoleIn(BaseModel):
    role: OrgKind
    delivery_address: str | None = None
    district: str | None = None


class OrgDetailOut(ORM):
    id: uuid.UUID
    is_buyer: bool
    is_seller: bool
    status: OrgStatus
    legal_name: str
    display_name: str
    tin: str | None
    tax_regime: TaxRegime | None
    registration_number: str | None
    legal_address: str | None
    director_name: str | None
    bank_name: str | None
    bank_account: str | None
    phone: str | None
    email: str | None
    venue_type: VenueType | None
    registry_source: str | None
    verification_status: VerificationStatus
    verified_at: datetime | None
    review_note: str | None


class RequisitesIn(BaseModel):
    display_name: str | None = None
    legal_address: str | None = None
    director_name: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    phone: str | None = None
    email: str | None = None
    registration_number: str | None = None
    tax_regime: TaxRegime | None = None


class OrgFileOut(ORM):
    id: uuid.UUID
    kind: FileKind
    file_name: str
    content_type: str
    size_bytes: int
    status: FileStatus
    expires_on: date | None
    review_note: str | None
    created_at: datetime


class VerificationOut(BaseModel):
    organization: OrgDetailOut
    required: list[FileKind]
    recommended: list[FileKind]
    files: list[OrgFileOut]


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


class DocumentOut(ORM):
    id: uuid.UUID
    kind: DocumentKind
    number: str | None
    external_id: str | None
    status: DocumentStatus
    issued_at: datetime | None
    payload: dict | None


class SrcNumberIn(BaseModel):
    kind: DocumentKind
    number: str


# --- chat, notifications, document register -------------------------------------------------------------


class MessageIn(BaseModel):
    text: str


class MessageOut(ORM):
    id: uuid.UUID
    side: str
    text: str
    at: datetime
    user_id: uuid.UUID | None
    mine: bool = False


class NotificationOut(ORM):
    id: uuid.UUID
    type: str
    order_id: uuid.UUID | None
    payload: dict | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    unread: int


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None  # None = all


class NotifySettings(BaseModel):
    email: bool
    telegram: bool
    email_address: str | None = None
    telegram_linked: bool = False
    telegram_available: bool = False


class NotifySettingsIn(BaseModel):
    email: bool
    telegram: bool


class DocumentRow(BaseModel):
    id: uuid.UUID
    kind: DocumentKind
    number: str | None
    external_id: str | None
    status: DocumentStatus
    issued_at: datetime | None
    order_id: uuid.UUID
    order_number: str
    order_total_amd: int
    counterparty: str


class DocumentPage(BaseModel):
    items: list[DocumentRow]
    total: int
