"""SQLAlchemy models for Djini's Bakehouse.

Statuses and roles are stored as plain strings (portable across SQLite and
server databases); valid values are enforced at the API layer by Pydantic.
"""

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, utcnow


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    ADMIN = "admin"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    BAKING = "baking"
    READY = "ready"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    CASH_ON_DELIVERY = "cash_on_delivery"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


# Statuses a customer may still cancel from
CANCELLABLE_STATUSES = {
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.BAKING,
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    # Profile photo served from /static — null falls back to the initial-letter avatar.
    avatar_url: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.CUSTOMER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change; tokens carry the version they were issued at,
    # so raising it instantly invalidates every outstanding access/refresh token.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Password reset: only the SHA-256 digest of the emailed token is kept,
    # so a stolen database can't reset anyone's password.
    reset_token_hash: Mapped[str | None] = mapped_column(String(64))
    reset_token_expires: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    addresses: Mapped[list["Address"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="user")
    reviews: Mapped[list["Review"]] = relationship(back_populates="user")

    @property
    def full_name(self) -> str:
        # Single-word names register with an empty last name — never show
        # the trailing space that leaves behind.
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(50), default="Home")
    recipient: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30))
    line1: Mapped[str] = mapped_column(String(255))
    line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(100), default="USA")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="addresses")

    @property
    def one_line(self) -> str:
        parts = [self.line1]
        if self.line2:
            parts.append(self.line2)
        parts += [self.city, self.state, self.postal_code]
        return ", ".join(parts)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    prep_time_minutes: Mapped[int | None] = mapped_column(Integer)
    ingredients: Mapped[str | None] = mapped_column(Text)
    allergens: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    category: Mapped[Category | None] = relationship(back_populates="products")
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def current_price(self) -> float:
        if self.sale_price is not None and self.sale_price < self.price:
            return float(self.sale_price)
        return float(self.price)

    @property
    def is_on_sale(self) -> bool:
        return self.sale_price is not None and self.sale_price < self.price

    @property
    def discount_percentage(self) -> int:
        if self.is_on_sale:
            return round((1 - float(self.sale_price) / float(self.price)) * 100)
        return 0


class Coupon(Base):
    """Percent-off promotion code applied once per checkout."""

    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    percent_off: Mapped[int] = mapped_column(Integer)  # 1..100
    description: Mapped[str | None] = mapped_column(String(255))
    # Minimum basket subtotal the code applies to (None/0 = no minimum).
    # Powers the "add $X more to unlock" nudge on the basket page.
    min_subtotal: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("product_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    comment: Mapped[str | None] = mapped_column(Text)
    # Reviews go live the moment they're posted; the baker can take one down
    # from the desk without losing it. Hidden reviews stay visible to their
    # author (flagged) so editing still works.
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship(back_populates="reviews")
    user: Mapped[User] = relationship(back_populates="reviews")


class Favorite(Base):
    """A product the customer hearted to come back for later."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship()


class Subscriber(Base):
    """Newsletter signup — one row per email address, however many times sent."""

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source: Mapped[str | None] = mapped_column(String(50))  # footer, checkout, …
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BulkEnquiryStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    QUOTED = "quoted"
    CONFIRMED = "confirmed"
    DECLINED = "declined"


class BulkEnquiry(Base):
    """A catering / event enquiry from the public "Bulk order" page.

    Not an order: it is a lead the baker quotes by phone or email, then
    confirms by hand. Guests can leave one, so nothing here points at users.
    """

    __tablename__ = "bulk_enquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(50))  # birthday, wedding, …
    guests: Mapped[int] = mapped_column(Integer)  # 1..2000
    # What they have in mind — "60 croffles and 3 focaccias", free text.
    items_note: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=BulkEnquiryStatus.NEW.value)
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Testimonial(Base):
    """A word from the neighborhood shown on the home page.

    The baker writes these herself — regulars' kind words, a caterer's
    thank-you — and can quote a real customer review by copying it in.
    """

    __tablename__ = "testimonials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str | None] = mapped_column(String(100))  # "Saturday regular"
    quote: Mapped[str] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[int | None] = mapped_column(Integer)  # 1..5, optional
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ComingSoonItem(Base):
    """Something on its way to the counter — the home page's look-ahead band."""

    __tablename__ = "coming_soon_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(255))
    # A short human line: "From December", "Weekends from the 14th".
    eta_label: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.PENDING.value)
    payment_method: Mapped[str] = mapped_column(String(30))
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING.value)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))
    discount_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(50))
    tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    shipping_fee: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2))
    # Kept by the shop when a customer cancels after baking started (0 otherwise)
    cancel_fee: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    # Address snapshot — orders stay intact even if the address is edited or deleted later
    recipient: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30))
    line1: Mapped[str] = mapped_column(String(255))
    line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    timeline: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.created_at"
    )

    @property
    def can_be_cancelled(self) -> bool:
        return self.status in {s.value for s in CANCELLABLE_STATUSES}


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(String(150))  # snapshot
    product_slug: Mapped[str | None] = mapped_column(String(180))  # snapshot, used by "order again" links
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")


class OrderEvent(Base):
    """Timeline entry for every order status change."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="timeline")


class AdminNotification(Base):
    """A ping on the baker's desk: a new order, a review that changed, a
    catering lead, a new face. Every customer-facing write drops one here in
    its own transaction, so the bell never announces something that didn't
    happen."""

    __tablename__ = "admin_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # order, review, enquiry, customer
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(String(500))
    # Where the bell's click lands (a front-of-house route on the desk)
    link: Mapped[str | None] = mapped_column(String(255))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# Storefront copy the admin can rewrite — these are the out-of-the-box lines
DEFAULT_ANNOUNCEMENT = "Free delivery on boxes over $50 · WELCOME10 takes 10% off your first order"
DEFAULT_TODAY_NOTE = "Croffles out at 8, doughnuts at 9"
DEFAULT_HERO_IMAGE = "/static/site/baker.jpg"
# The home page's scrolling strip — one phrase per line, the baker's to edit.
DEFAULT_MARQUEE = "\n".join(
    [
        "FRESH EVERY MORNING",
        "TWO-DAY CROISSANTS",
        "KETTLE-BOILED BAGELS",
        "STONE-GROUND FLOUR",
        "CULTURED FRENCH BUTTER",
        "WEEKEND DOUGHNUTS",
    ]
)


class StoreSetting(Base):
    """Shop rules and storefront copy the admin tunes (single row, id=1)."""

    __tablename__ = "store_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    late_cancel_fee_percent: Mapped[float] = mapped_column(Numeric(4, 1), default=10.0)
    # Storefront copy the baker can rewrite from the Settings tab; empty
    # strings hide the announcement bar / the hero "today" chip.
    announcement: Mapped[str] = mapped_column(String(255), default=DEFAULT_ANNOUNCEMENT)
    today_note: Mapped[str] = mapped_column(String(255), default=DEFAULT_TODAY_NOTE)
    # Phrases for the home page's scrolling strip, one per line. NULL (never
    # edited) shows the shipped set; an empty string hides the strip.
    marquee_items: Mapped[str | None] = mapped_column(Text)
    # The photo above the today note; NULL/empty falls back to the shipped one.
    hero_image: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def get_store_setting(db) -> StoreSetting:
    """The one settings row, created with defaults on first touch."""
    row = db.get(StoreSetting, 1)
    if row is None:
        row = StoreSetting(id=1, late_cancel_fee_percent=10.0)
        db.add(row)
        db.commit()
    return row
