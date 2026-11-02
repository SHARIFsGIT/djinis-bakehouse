"""Pydantic request/response schemas."""

import enum
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from .models import BulkEnquiryStatus, OrderStatus, PaymentMethod, UserRole


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)


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


class OrderListResponse(BaseModel):
    orders: list[OrderOut]
    pagination: PaginationOut


class StoreSettingsOut(ORMModel):
    late_cancel_fee_percent: float
    announcement: str
    today_note: str
    hero_image: str
    updated_at: datetime


class StoreSettingsUpdate(BaseModel):
    late_cancel_fee_percent: float | None = Field(default=None, ge=0, le=100)
    announcement: str | None = Field(default=None, max_length=255)
    today_note: str | None = Field(default=None, max_length=255)
    hero_image: str | None = Field(default=None, max_length=255)


class StorefrontOut(BaseModel):
    """Public shop copy — the announcement bar and the hero 'today' chip.
    demo_accounts tells the login page whether the one-tap demo logins exist
    on this deployment (dev yes, a real production database no)."""
    announcement: str
    today_note: str
    hero_image: str
    demo_accounts: bool = False


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


class CouponCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    percent_off: int = Field(ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)
    # 0 or None = no minimum; a threshold here is what the basket nudges toward.
    min_subtotal: float | None = Field(default=None, ge=0)


class CouponUpdateRequest(BaseModel):
    percent_off: int | None = Field(default=None, ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)
    min_subtotal: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CouponOut(ORMModel):
    id: int
    code: str
    percent_off: int
    description: str | None
    min_subtotal: float | None = None
    is_active: bool
    created_at: datetime


class CouponAppliedOut(BaseModel):
    """Public coupon preview returned by the checkout validation endpoint."""

    code: str
    percent_off: int
    description: str | None
    min_subtotal: float | None = None


class VoucherOut(BaseModel):
    """A live voucher, shown on the basket page to tempt a bigger box."""

    code: str
    percent_off: int
    description: str | None
    min_subtotal: float | None = None


class OrderStatusUpdateRequest(BaseModel):
    status: OrderStatus
    comment: str | None = None


class PaymentStatusUpdateRequest(BaseModel):
    payment_status: str = Field(pattern="^(pending|paid|refunded)$")


class UserAdminUpdateRequest(BaseModel):
    is_active: bool | None = None
    role: UserRole | None = None


class AdminStatsOut(BaseModel):
    total_revenue: float
    paid_revenue: float
    order_count: int
    customer_count: int
    avg_order_value: float
    pending_orders: int
    orders_by_status: dict[str, int]
    top_products: list[dict]
    recent_orders: list[OrderOut]
    low_stock: list[ProductCard]


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
    phone: str | None = Field(default=None, max_length=30)
    event_date: date
    event_type: str = Field(pattern="^(birthday|wedding|corporate|holiday|other)$")
    guests: int = Field(ge=1, le=2000)
    items_note: str | None = Field(default=None, max_length=2000)
    message: str | None = Field(default=None, max_length=2000)


class BulkEnquiryUpdate(BaseModel):
    """The baker's side: where the quote stands and her own notes."""

    status: BulkEnquiryStatus | None = None
    admin_note: str | None = Field(default=None, max_length=2000)


class BulkEnquiryOut(ORMModel):
    id: int
    name: str
    email: EmailStr
    phone: str | None
    event_date: date
    event_type: str
    guests: int
    items_note: str | None
    message: str | None
    status: BulkEnquiryStatus
    admin_note: str | None
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
