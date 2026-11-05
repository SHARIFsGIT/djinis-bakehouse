"""Pydantic request/response schemas."""

import enum
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .models import BulkEnquiryStatus, OrderStatus, PaymentMethod, UserRole
from .vat import VatCategory


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def normalize_phone(value: str | None, *, required: bool = False) -> str | None:
    """Store phone numbers the one way they'll be compared: digits with a
    kept leading + — so "+49 176 555-0100" and "+491765550100" are the same
    account at sign-in, but two differently-formatted spellings can never
    sneak past a uniqueness check. Empty clears the field (None on the wire
    still means "leave it alone")."""
    if value is None:
        return None
    cleaned = value.strip()
    plus = cleaned.startswith("+")
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits:
        if required:
            raise ValueError("value should have 7 to 15 digits")
        return ""
    if not 7 <= len(digits) <= 15:
        raise ValueError("value should have 7 to 15 digits, plus spaces/dashes if you like")
    return ("+" if plus else "") + digits


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    first_name: str = Field(min_length=1, max_length=100)
    # Optional so the signup form can be one "Your name" field — plenty of
    # customers have a single name to give.
    last_name: str = Field(default="", max_length=100)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return normalize_phone(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=8, max_length=72)


class PhoneLoginRequest(BaseModel):
    """Ask for a texted sign-in code — no password involved."""

    phone: str = Field(min_length=7, max_length=30)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_phone(value, required=True)


class PhoneLoginVerify(BaseModel):
    """The six-digit code from the text, traded straight for tokens."""

    phone: str = Field(min_length=7, max_length=30)
    code: str = Field(pattern=r"^\d{6}$")

    @field_validator("phone")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_phone(value, required=True)


class LoginCodeExchange(BaseModel):
    """The one-time code a social callback handed the browser."""

    code: str = Field(min_length=10)


class UserOut(ORMModel):
    id: int
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None
    avatar_url: str | None = None
    role: UserRole
    is_active: bool
    created_at: datetime


class UserUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return normalize_phone(value)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------

class AddressBase(BaseModel):
    label: str = Field(default="Home", max_length=50)
    recipient: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    line1: str = Field(min_length=1, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(default="USA", max_length=100)
    is_default: bool = False


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    """Partial by design — only the fields sent are changed."""

    label: str | None = Field(default=None, max_length=50)
    recipient: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    line1: str | None = Field(default=None, min_length=1, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, min_length=1, max_length=100)
    postal_code: str | None = Field(default=None, min_length=1, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    is_default: bool | None = None


class AddressOut(ORMModel):
    id: int
    label: str
    recipient: str
    phone: str | None
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool
    created_at: datetime


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

class CategoryOut(ORMModel):
    id: int
    name: str
    slug: str
    description: str | None
    image_url: str | None = None
    product_count: int = 0


class CategoryCreateRequest(BaseModel):
    """A new shelf, named on the spot from the product form."""

    name: str = Field(min_length=1, max_length=100)
    image_url: str | None = Field(default=None, max_length=255)


class ReviewOut(ORMModel):
    id: int
    rating: int
    comment: str | None
    created_at: datetime
    author_name: str | None = None
    author_id: int | None = None
    # True when the reviewer actually bought this bake (non-cancelled order)
    verified_purchase: bool = False
    # False when the baker took the review down; the field travels so the
    # author's own copy can explain itself ("hidden by the shop").
    is_approved: bool = True


class ProductCard(ORMModel):
    id: int
    name: str
    slug: str
    price: float
    sale_price: float | None
    current_price: float
    is_on_sale: bool
    discount_percentage: int
    is_featured: bool
    is_available: bool
    stock: int
    image_url: str | None = None
    category_name: str | None = None
    category_slug: str | None = None
    average_rating: float = 0.0
    review_count: int = 0


class ProductDetail(ProductCard):
    description: str | None
    prep_time_minutes: int | None
    ingredients: str | None
    allergens: str | None
    created_at: datetime
    reviews: list[ReviewOut] = []
    related: list[ProductCard] = []


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class PaginationOut(BaseModel):
    page: int
    per_page: int
    total_items: int
    total_pages: int


class ProductListResponse(BaseModel):
    products: list[ProductCard]
    pagination: PaginationOut


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

class OrderItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=99)


class CheckoutRequest(BaseModel):
    items: list[OrderItemRequest] = Field(min_length=1)
    address_id: int | None = None
    new_address: AddressCreate | None = None
    payment_method: PaymentMethod
    delivery_date: date | None = None
    coupon_code: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_address(self):
        if not self.address_id and not self.new_address:
            raise ValueError("Provide address_id or new_address")
        return self


class QuoteRequest(BaseModel):
    """A basket as the shopper holds it, priced without buying it: ids and
    quantities only. The browser never gets to name a price — every figure
    in the response comes from the catalog, the coupon table and the shop's
    money rules, exactly as checkout will charge them."""

    items: list[OrderItemRequest] = Field(min_length=1)
    coupon_code: str | None = Field(default=None, max_length=50)


class QuoteItemOut(BaseModel):
    product_id: int
    name: str
    slug: str
    unit_price: float
    quantity: int
    line_total: float


class QuoteOut(BaseModel):
    """The server-authoritative basket tally the storefront displays. Every
    amount is rounded to the cent by the same recipe the invoice uses, and
    `currency` is the currency the eventual order will be charged in."""

    items: list[QuoteItemOut]
    subtotal: float
    discount_amount: float = 0
    coupon_code: str | None = None
    coupon_percent_off: int | None = None
    tax: float
    shipping_fee: float
    total: float
    # EUR since the 2026-11-05 owner decision — filled from the validated
    # charge currency at runtime, so the default is documentation, not logic.
    currency: str = "eur"


class OrderItemOut(ORMModel):
    id: int
    product_id: int | None
    product_name: str
    product_slug: str | None = None
    unit_price: float
    quantity: int
    subtotal: float


class OrderEventOut(ORMModel):
    id: int
    status: OrderStatus
    comment: str | None
    created_at: datetime


class OrderOut(ORMModel):
    id: int
    order_number: str
    status: OrderStatus
    payment_method: PaymentMethod
    payment_status: str
    subtotal: float
    discount_amount: float = 0
    coupon_code: str | None = None
    tax: float
    shipping_fee: float
    total: float
    cancel_fee: float = 0
    # The money this order was placed in; every amount above is in it.
    # Historical rows still say "usd" — the code is read off each order.
    currency: str = "eur"
    delivery_date: date | None
    note: str | None
    recipient: str
    phone: str | None
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    created_at: datetime
    can_be_cancelled: bool
    item_count: int = 0


class OrderDetailOut(OrderOut):
    items: list[OrderItemOut] = []
    timeline: list[OrderEventOut] = []
    customer_name: str | None = None
    customer_email: str | None = None
    # What a cancellation right now would cost (non-zero only once baking
    # has started and the admin keeps a late-cancel fee) — shown pre-cancel.
    cancellation_fee: float = 0
    # Stripe's hosted checkout page, set on the checkout response for card
    # orders (and on a fresh pay link) — null everywhere else.
    payment_url: str | None = None


class OrderListResponse(BaseModel):
    orders: list[OrderOut]
    pagination: PaginationOut


class StoreSettingsOut(ORMModel):
    late_cancel_fee_percent: float
    announcement: str
    today_note: str
    hero_image: str
    marquee_items: str
    contact_address: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    opening_hours: str | None = None
    updated_at: datetime


class StoreSettingsUpdate(BaseModel):
    late_cancel_fee_percent: float | None = Field(default=None, ge=0, le=100)
    announcement: str | None = Field(default=None, max_length=255)
    today_note: str | None = Field(default=None, max_length=255)
    hero_image: str | None = Field(default=None, max_length=255)
    marquee_items: str | None = Field(default=None, max_length=1000)
    contact_address: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=255)
    opening_hours: str | None = Field(default=None, max_length=500)


class StorefrontOut(BaseModel):
    """Public shop copy — the announcement bar and the hero 'today' chip.
    demo_accounts tells the login page whether the one-tap demo logins exist
    on this deployment (dev yes, a real production database no). The contact
    lines feed the footer; None means the baker hasn't filled them in yet.
    card_payments tells checkout whether the online-card option exists (it
    appears the moment STRIPE_SECRET_KEY is configured), and
    facebook_login / phone_login tell it which extra sign-in rails are live
    — a rail with no keys behind it never shows a customer a dead button."""
    announcement: str
    today_note: str
    hero_image: str
    marquee: list[str] = []
    demo_accounts: bool = False
    card_payments: bool = False
    facebook_login: bool = False
    phone_login: bool = False
    contact_address: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    opening_hours: str | None = None
    # The shop's money rules, straight from the environment — the storefront's
    # basket arithmetic, labels and currency symbol consume these instead of
    # keeping a second copy, so what a customer estimates can never drift from
    # the invoice the server computes. The delivery figures are None while
    # their EUR values await owner approval (checkout is paused then — see
    # routers/orders._approved_money_rules); a None tax rate means no tax line
    # (VAT treatment pending professional review), never a 0%-VAT claim.
    # currency is None when the configured value isn't one the gateway can
    # charge (checkout is 503-paused then) — the storefront keeps its own
    # euro default rather than labelling prices in an unchargeable symbol.
    currency: str | None = "eur"
    tax_rate: float | None = None
    shipping_fee: float | None = None
    free_shipping_threshold: float | None = None


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    price: float = Field(gt=0)
    sale_price: float | None = Field(default=None, gt=0)
    category_id: int | None = None
    is_featured: bool = False
    is_available: bool = True
    stock: int = Field(default=0, ge=0)
    prep_time_minutes: int | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=255)
    ingredients: str | None = None
    allergens: str | None = None

    @model_validator(mode="after")
    def sale_below_price(self):
        if self.sale_price is not None and self.sale_price >= self.price:
            raise ValueError("sale_price must be lower than price")
        return self


class ProductUpdateRequest(ProductCreateRequest):
    pass


# The admin desk's VAT workflow (the euro migration's classification phase).
# Public catalog schemas stay untouched — these fields ride only on admin
# responses, and the only writer is the dedicated classification endpoint.

class AdminVatEventOut(ORMModel):
    """One entry in a product's classification log: who, when, from what, to what."""

    admin_name: str
    from_category: str | None
    to_category: str | None
    from_status: str | None
    to_status: str
    from_milk_content: float | None
    to_milk_content: float | None
    created_at: datetime


class _AdminVatFields(BaseModel):
    vat_category: str | None = None
    vat_review_status: str = "pending"
    milk_content_percent: float | None = None
    # The rate the current configuration implies for this classification —
    # a desk preview that ignores the approval gate. Provisional by design;
    # never a legal determination.
    vat_rate_preview: float | None = None


# Defined here (before the admin card composes them) because the admin
# product detail carries the full cost summary alongside its VAT state.
class MarginEvaluationOut(BaseModel):
    status: Literal["ok", "flagged", "not_computable", "not_set"]
    margin: float | None = None
    min_net: float | None = None
    net_price: float | None = None
    reason: dict | None = None


class ProductCostSummaryOut(BaseModel):
    """The derived cost state of one product, read-side only."""

    total_cost: float | None
    currency: str | None
    computed_at: datetime | None
    is_complete: bool
    missing: list[str] = []
    margins: dict[str, MarginEvaluationOut] = {}
    inputs_overdue: bool = False


class _AdminCostFields(BaseModel):
    """Cost state on the admin product card — a flag plus the headline
    figure; the full picture is the cost-review desk and the detail view.
    Public cards never carry any of this."""

    cost_status: str = "not_costed"


class AdminProductCard(_AdminVatFields, _AdminCostFields, ProductCard):
    pass


class AdminProductDetail(_AdminVatFields, _AdminCostFields, ProductDetail):
    vat_events: list[AdminVatEventOut] = []
    cost: ProductCostSummaryOut | None = None


class AdminProductListResponse(BaseModel):
    products: list[AdminProductCard]
    pagination: PaginationOut


class AdminVatUpdateRequest(BaseModel):
    """A classification write: the full trio, replacing what was there.
    Lives on its own endpoint (not ProductUpdateRequest) so an ordinary
    product edit can never silently wipe an approval — the main PUT is a
    full-replace of the fields it knows."""

    vat_category: str | None = None
    vat_review_status: Literal["pending", "approved", "blocked"]
    milk_content_percent: float | None = Field(default=None, ge=0, le=100)

    @field_validator("vat_category")
    @classmethod
    def known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in {category.value for category in VatCategory}:
            known = ", ".join(category.value for category in VatCategory)
            raise ValueError(f"unknown VAT category '{value}' — use one of: {known}")
        return value


class CouponCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    percent_off: int = Field(ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)
    # 0 or None = no minimum; a threshold here is what the basket nudges toward.
    min_subtotal: float | None = Field(default=None, ge=0)
    # The welcome-voucher rule: redeemable once per customer.
    once_per_customer: bool = False


class CouponUpdateRequest(BaseModel):
    percent_off: int | None = Field(default=None, ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)
    min_subtotal: float | None = Field(default=None, ge=0)
    is_active: bool | None = None
    once_per_customer: bool | None = None


class CouponOut(ORMModel):
    id: int
    code: str
    percent_off: int
    description: str | None
    min_subtotal: float | None = None
    # Pre-cutover minimums are USD numerals the app cannot honestly show as
    # euro thresholds; False is the promotions desk's "pending review" badge.
    min_subtotal_approved: bool = False
    is_active: bool
    once_per_customer: bool
    created_at: datetime


class CouponPreviewOut(BaseModel):
    """A coupon as a shopper sees it — no ids, no admin bookkeeping, just
    the deal. The checkout's code check and the basket's voucher list both
    speak this one shape."""

    code: str
    percent_off: int
    description: str | None
    min_subtotal: float | None = None
    # Shopper-facing responses never carry the approval flag itself — the
    # validator below simply withdraws an unapproved minimum from view.
    min_subtotal_approved: bool = False

    @model_validator(mode="after")
    def _hide_unapproved_minimum(self):
        # A minimum recorded before the 2026-11-05 EUR cutover is a dollar
        # numeral wearing a euro sign. Until an administrator re-approves
        # it, shoppers see the saving with no threshold at all rather than
        # a figure nobody approved (checkout refuses the code outright).
        if self.min_subtotal and not self.min_subtotal_approved:
            self.min_subtotal = None
        return self


class OrderStatusUpdateRequest(BaseModel):
    status: OrderStatus
    comment: str | None = None


class PaymentStatusUpdateRequest(BaseModel):
    payment_status: str = Field(pattern="^(pending|paid|refunded)$")


class UserAdminUpdateRequest(BaseModel):
    is_active: bool | None = None
    role: UserRole | None = None


class CurrencyStatsOut(BaseModel):
    """One currency's slice of the order book. Dollars and euros are never
    summed into one number — each keeps its own revenue, refunds and average,
    so the dashboard can't manufacture a total that never existed."""

    currency: str
    total_revenue: float
    paid_revenue: float
    refunded_revenue: float
    order_count: int
    avg_order_value: float


class AdminStatsOut(BaseModel):
    """The desk's morning page. The headline money figures describe the
    shop's *active* currency (settings.stripe_currency) only — for an
    all-USD book, exactly today's numbers. Every currency's own figures
    ride in revenue_by_currency; no field anywhere converts or combines
    currencies."""

    total_revenue: float = Field(
        description="All-time order value in the active currency only — never a cross-currency sum."
    )
    paid_revenue: float = Field(
        description="Value of paid and since-refunded orders in the active currency only."
    )
    order_count: int
    customer_count: int
    avg_order_value: float = Field(
        description="Active-currency revenue ÷ active-currency order count — "
        "per-currency arithmetic, never a mixed mean."
    )
    pending_orders: int
    orders_by_status: dict[str, int]
    top_products: list[dict]
    recent_orders: list[OrderOut]
    low_stock: list[ProductCard]
    new_enquiries: int = 0
    revenue_by_currency: list[CurrencyStatsOut] = Field(
        default=[],
        description="The complete currency-separated breakdown, one entry per "
        "currency in the book (active first, then alphabetical; unreadable "
        "codes keep their own 'unknown' bucket).",
    )


class AdminCustomerOut(UserOut):
    """A customer as the desk sees them: who they are, plus what the shop can
    measure — kept orders (cancelled never count), lifetime spend, the last
    visit, and the loyalty tier those earn. Spend never mixes currencies:
    lifetime_spend is the shop's active currency only, and the full split
    rides in lifetime_spend_by_currency."""

    order_count: int = 0
    lifetime_spend: float = Field(
        default=0,
        description="Kept-order spend in the shop's active currency only — never a cross-currency sum.",
    )
    lifetime_spend_by_currency: dict[str, float] = Field(
        default={},
        description="The complete per-currency split of that spend; keys are "
        "currency codes, values are each currency's own total.",
    )
    last_order_at: datetime | None = None
    loyalty: str = "regular"


class AdminUserPage(BaseModel):
    """One shelf of the roll: the page itself, the whole roll's tier counts
    (so the filter chips stay honest from any page), and where this page
    sits in it."""

    items: list[AdminCustomerOut]
    tier_counts: dict[str, int]
    pagination: PaginationOut


class VoucherLinkRequest(BaseModel):
    """Point a loyalty tier at one of the shop's coupons — or clear the
    tier's code with None."""

    coupon_id: int | None


class LoyaltyVouchersOut(BaseModel):
    """The coupon each loyalty tier hands its regulars, if one is chosen."""

    vip: CouponOut | None = None
    loyal: CouponOut | None = None


# --------------------------------------------------------------------------
# Review moderation + the baker's bell
# --------------------------------------------------------------------------

class ReviewModerationRequest(BaseModel):
    is_approved: bool


class AdminReviewOut(ORMModel):
    """One review as the desk sees it: whose, on what, and whether it's live."""

    id: int
    product_id: int
    product_name: str
    product_slug: str
    product_image: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    rating: int
    comment: str | None
    verified_purchase: bool = False
    is_approved: bool
    created_at: datetime


class AdminReviewCounts(BaseModel):
    """The shelf tallies for the tabs, plus the average the wall shows."""

    live: int
    hidden: int
    all: int
    live_average: float | None = None


class AdminReviewList(BaseModel):
    items: list[AdminReviewOut]
    counts: AdminReviewCounts


class AdminNotificationOut(ORMModel):
    id: int
    kind: str
    title: str
    body: str | None
    link: str | None
    is_read: bool
    created_at: datetime


class AdminNotificationList(BaseModel):
    items: list[AdminNotificationOut]
    unread_count: int


class SortOption(str, enum.Enum):
    NEWEST = "newest"
    NAME = "name"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING = "rating"


# --------------------------------------------------------------------------
# Newsletter
# --------------------------------------------------------------------------

class NewsletterRequest(BaseModel):
    email: EmailStr
    source: str | None = Field(default=None, max_length=50)


class NewsletterResponse(BaseModel):
    email: EmailStr
    already_subscribed: bool = False


class SubscriberOut(ORMModel):
    id: int
    email: EmailStr
    source: str | None
    created_at: datetime


# --------------------------------------------------------------------------
# Bulk (event & catering) enquiries
# --------------------------------------------------------------------------

class BulkEnquiryCreate(BaseModel):
    """Public catering lead from the home page's "Bulk order" button."""

    name: str = Field(min_length=1, max_length=150)
    email: EmailStr
    # Quotes move faster by phone, so the number is required.
    phone: str = Field(min_length=3, max_length=30)
    event_date: date
    event_type: str = Field(pattern="^(birthday|wedding|corporate|holiday|other)$")
    guests: int = Field(ge=1, le=2000)
    items_note: str | None = Field(default=None, max_length=2000)
    message: str | None = Field(default=None, max_length=2000)


class BulkEnquiryUpdate(BaseModel):
    """The baker's side: where the quote stands and her own notes."""

    status: BulkEnquiryStatus | None = None
    admin_note: str | None = Field(default=None, max_length=2000)


class BulkReplyCreate(BaseModel):
    """An answer emailed to the enquirer from the enquiry drawer."""

    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the reply needs something to say")
        return value


class BulkReplyOut(ORMModel):
    id: int
    enquiry_id: int
    message: str
    created_at: datetime


class BulkEnquiryOut(ORMModel):
    id: int
    name: str
    email: EmailStr
    # Nullable on the wire for the sake of rows created before the number
    # became required — new enquiries always carry one.
    phone: str | None
    event_date: date
    event_type: str
    guests: int
    items_note: str | None
    message: str | None
    status: BulkEnquiryStatus
    admin_note: str | None
    replies: list[BulkReplyOut] = []
    created_at: datetime


# --------------------------------------------------------------------------
# Home page content the baker curates: testimonials and coming-soon items
# --------------------------------------------------------------------------

class TestimonialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    role: str | None = Field(default=None, max_length=100)
    quote: str = Field(min_length=5, max_length=1000)
    avatar_url: str | None = Field(default=None, max_length=255)
    rating: int | None = Field(default=None, ge=1, le=5)
    is_active: bool = True
    sort_order: int = 0


class TestimonialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    role: str | None = Field(default=None, max_length=100)
    quote: str | None = Field(default=None, min_length=5, max_length=1000)
    avatar_url: str | None = Field(default=None, max_length=255)
    rating: int | None = Field(default=None, ge=1, le=5)
    is_active: bool | None = None
    sort_order: int | None = None


class TestimonialOut(ORMModel):
    id: int
    name: str
    role: str | None
    quote: str
    avatar_url: str | None
    rating: int | None
    is_active: bool
    sort_order: int
    created_at: datetime


class ComingSoonCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=255)
    eta_label: str | None = Field(default=None, max_length=100)
    is_active: bool = True
    sort_order: int = 0


class ComingSoonUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=255)
    eta_label: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    sort_order: int | None = None


class ComingSoonOut(ORMModel):
    id: int
    title: str
    description: str | None
    image_url: str | None
    eta_label: str | None
    is_active: bool
    sort_order: int
    created_at: datetime


# The Phase 2 costing foundation (spec §21, plan §8). API-only surfaces for
# the admin desk — no frontend exists yet, and none of these fields ride on
# public catalog responses. Money is float only at the Pydantic boundary;
# every stored figure is Decimal, and the JSON breakdown preserves exact
# decimal strings.

class IngredientCategoryOut(ORMModel):
    id: int
    name: str
    review_interval_days: int
    ingredient_count: int = 0
    created_at: datetime


class IngredientCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    review_interval_days: int = Field(default=90, ge=1, le=3650)


class IngredientCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)


class IngredientOut(ORMModel):
    id: int
    name: str
    category_id: int | None
    category_name: str | None = None
    unit: str
    purchase_qty: float
    purchase_price: float
    effective_cost_per_unit: float | None
    currency: str
    density_g_per_ml: float | None = None
    source: str | None = None
    confidence: str = "medium"
    is_estimate: bool = True
    last_reviewed: date | None = None
    next_reviewed: date | None = None
    review_interval_days: int | None = None
    effective_interval_days: int | None = None
    overdue: bool = False
    invoice_ref: str | None = None
    created_at: datetime
    updated_at: datetime


def _require_eur_currency(value: str | None) -> str | None:
    """The costing book is EUR-only (2026-11-05 owner decision): a figure in
    any other currency is refused with a validation error — never converted,
    never relabelled. None (an update leaving the field alone) passes."""
    if value is None:
        return None
    code = value.strip().lower()
    if code != "eur":
        raise ValueError("the costing book is EUR-only — record the figure in euro (eur)")
    return code


class IngredientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    category_id: int | None = None
    unit: str = Field(min_length=1, max_length=20)
    purchase_qty: float = Field(gt=0)
    purchase_price: float = Field(ge=0)
    currency: str = Field(default="eur", min_length=3, max_length=3)
    density_g_per_ml: float | None = Field(default=None, gt=0)
    source: str | None = Field(default=None, max_length=1000)
    confidence: Literal["high", "medium", "low"] = "medium"
    invoice_ref: str | None = Field(default=None, max_length=255)
    last_reviewed: date | None = None
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)

    _eur_only = field_validator("currency")(_require_eur_currency)


class IngredientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    category_id: int | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    purchase_qty: float | None = Field(default=None, gt=0)
    purchase_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    density_g_per_ml: float | None = Field(default=None, gt=0)
    source: str | None = Field(default=None, max_length=1000)
    confidence: Literal["high", "medium", "low"] | None = None
    invoice_ref: str | None = Field(default=None, max_length=255)
    # Set is_estimate to False only through the review endpoint — that is
    # the act of confirming figures; a plain edit keeps its estimate flag.
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)

    _eur_only = field_validator("currency")(_require_eur_currency)


class IngredientReviewRequest(BaseModel):
    """Confirming the recorded figures as reviewed (is_estimate → False,
    last_reviewed → the review date, next_reviewed advanced)."""

    reviewed_on: date | None = None  # defaults to today


class IngredientPriceEventOut(ORMModel):
    """One append-only price-change entry: full prev/new provenance."""

    ingredient_id: int
    admin_user_id: int | None
    admin_name: str
    prev_purchase_qty: float | None
    new_purchase_qty: float | None
    prev_purchase_price: float | None
    new_purchase_price: float | None
    prev_effective_cost_per_unit: float | None
    new_effective_cost_per_unit: float | None
    prev_unit: str | None
    new_unit: str | None
    prev_currency: str | None
    new_currency: str | None
    prev_is_estimate: bool | None
    new_is_estimate: bool | None
    prev_source: str | None
    new_source: str | None
    prev_confidence: str | None
    new_confidence: str | None
    effective_from: date
    created_at: datetime


class RecipeItemIn(BaseModel):
    ingredient_id: int
    usable_qty: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    loss_pct: float = Field(default=0, ge=0, lt=100)
    note: str | None = Field(default=None, max_length=255)


class RecipeItemOut(RecipeItemIn):
    id: int
    ingredient_name: str | None = None
    ingredient_unit: str | None = None


class RecipeUpsertRequest(BaseModel):
    """Full-replace upsert of a product's recipe. `items` may be empty
    (clearing a recipe); each ingredient may appear once."""

    hands_on_minutes: float = Field(default=0, ge=0)
    unattended_minutes: float = Field(default=0, ge=0)
    expected_batch_yield: int = Field(gt=0)
    batch_overhead_estimate: float = Field(default=0, ge=0)
    source: str | None = Field(default=None, max_length=1000)
    confidence: Literal["high", "medium", "low"] = "medium"
    notes: str | None = Field(default=None, max_length=4000)
    last_reviewed: date | None = None
    items: list[RecipeItemIn] = []


class RecipeOut(ORMModel):
    id: int
    product_id: int
    hands_on_minutes: float
    unattended_minutes: float
    expected_batch_yield: int
    batch_overhead_estimate: float
    source: str | None
    confidence: str
    is_estimate: bool
    last_reviewed: date | None
    next_reviewed: date | None
    inputs_overdue: bool = False
    notes: str | None
    items: list[RecipeItemOut] = []
    created_at: datetime
    updated_at: datetime


class ProductionBatchOut(ORMModel):
    id: int
    product_id: int
    produced_at: date
    expected_yield: int
    actual_yield: int | None
    batch_overhead_estimated: float
    batch_overhead_actual: float | None
    overhead_categories: str | None
    usage_notes: str | None
    status: str
    yield_variance_pct: float | None
    closed_at: datetime | None
    created_at: datetime


class ProductionBatchCreate(BaseModel):
    product_id: int
    produced_at: date | None = None  # defaults to today
    # Omitted values fall back to the product's recipe — the desk starts a
    # batch from what the recipe promises.
    expected_yield: int | None = Field(default=None, gt=0)
    batch_overhead_estimated: float | None = Field(default=None, ge=0)
    overhead_categories: str | None = Field(default=None, max_length=2000)
    usage_notes: str | None = Field(default=None, max_length=4000)


class ProductionBatchUpdate(BaseModel):
    produced_at: date | None = None
    expected_yield: int | None = Field(default=None, gt=0)
    batch_overhead_estimated: float | None = Field(default=None, ge=0)
    overhead_categories: str | None = Field(default=None, max_length=2000)
    usage_notes: str | None = Field(default=None, max_length=4000)


class ProductionBatchClose(BaseModel):
    actual_yield: int = Field(ge=0)
    batch_overhead_actual: float = Field(ge=0)
    usage_notes: str | None = Field(default=None, max_length=4000)


class OverheadMonthOut(ORMModel):
    id: int
    month: date
    fixed_overhead_total: float
    forecast_hours: float
    currency: str
    included_categories: str | None
    actual_total: float | None = None
    actual_hours: float | None = None
    variance_note: str | None = None
    source: str | None
    confidence: str
    is_estimate: bool
    created_at: datetime
    updated_at: datetime


class OverheadMonthCreate(BaseModel):
    month: date
    fixed_overhead_total: float = Field(ge=0)
    forecast_hours: float = Field(gt=0)
    currency: str = Field(default="eur", min_length=3, max_length=3)
    included_categories: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=1000)
    confidence: Literal["high", "medium", "low"] = "medium"

    _eur_only = field_validator("currency")(_require_eur_currency)


class OverheadMonthUpdate(BaseModel):
    fixed_overhead_total: float | None = Field(default=None, ge=0)
    forecast_hours: float | None = Field(default=None, gt=0)
    included_categories: str | None = Field(default=None, max_length=2000)
    actual_total: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    variance_note: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=1000)
    confidence: Literal["high", "medium", "low"] | None = None


class OverheadMonthSaveOut(OverheadMonthOut):
    """A month write's response: the row plus the AC-8 overlap warnings the
    desk should see (never blocking)."""

    overlap_warnings: list[dict] = []


class LabourRateOut(ORMModel):
    valid_from: date
    rate: float
    currency: str
    source: str | None
    created_at: datetime


class LabourRateSetRequest(BaseModel):
    """A new effective-dated rate. Appends a row (history is never edited);
    valid_from must not collide with an existing row."""

    valid_from: date
    rate: float = Field(gt=0)
    currency: str = Field(default="eur", min_length=3, max_length=3)
    source: str | None = Field(default=None, max_length=1000)

    _eur_only = field_validator("currency")(_require_eur_currency)


class LabourRateStatusOut(BaseModel):
    """What costing resolves right now, plus the full effective-dated
    history. `source` says whether the table or the D3 default fallback
    supplied the current figure."""

    rate: float
    currency: str
    source: str  # "table" | "default_fallback"
    valid_from: date | None = None
    history: list[LabourRateOut] = []


class IngredientListResponse(BaseModel):
    ingredients: list[IngredientOut]
    pagination: PaginationOut


class CostReviewEntryOut(BaseModel):
    """One product on the cost-review desk: what it is, its cost state,
    and what review work it needs."""

    product_id: int
    product_name: str
    cost_status: str
    cost: ProductCostSummaryOut | None = None
    reason: str | None = None  # "no_cost_record" | "incomplete" | "inputs_overdue" | "scheduled"
    next_reviewed: date | None = None
    overdue_on: date | None = None


class CostReviewOut(BaseModel):
    overdue: list[CostReviewEntryOut] = []
    upcoming: list[CostReviewEntryOut] = []
    window_days: int
    overlap_warnings: list[dict] = []
