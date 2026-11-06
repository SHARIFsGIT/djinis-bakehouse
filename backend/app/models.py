"""SQLAlchemy models for Djini's Bakehouse.

Statuses and roles are stored as plain strings (portable across SQLite and
server databases); valid values are enforced at the API layer by Pydantic.
"""

import enum
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
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
    # Phone sign-in: a one-shot texted code (digest only, so a stolen
    # database can't sign anyone in) plus its attempt ledger.
    phone_otp_hash: Mapped[str | None] = mapped_column(String(64))
    phone_otp_expires: Mapped[datetime | None] = mapped_column(DateTime)
    phone_otp_created_at: Mapped[datetime | None] = mapped_column(DateTime)
    phone_otp_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # "Continue with Facebook": Facebook's user id for an account linked
    # through the Login dialog. None = the customer signs in another way.
    facebook_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # One-time code handed to the SPA after a social callback (60 s, single
    # use, digest only) — real tokens never ride in a URL.
    login_code_hash: Mapped[str | None] = mapped_column(String(64))
    login_code_expires: Mapped[datetime | None] = mapped_column(DateTime)
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
    # VAT classification (the euro migration's tax foundation). The category
    # vocabulary lives in app/vat.py and is provisional until a German tax
    # adviser confirms each product; NULL = never classified, which refuses
    # VAT-inclusive pricing rather than guessing a rate.
    vat_category: Mapped[str | None] = mapped_column(String(20))
    # pending (the default) | approved | blocked — nothing goes on a
    # VAT-inclusive price list without the approved sign-off.
    vat_review_status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending"
    )
    # Cow's-milk share of the finished drink, in percent — a MILK_DRINK's
    # ticket to the reduced rate at >= 75 (Anlage 2 nr. 35). Plant-based
    # substitutes never count toward this figure.
    milk_content_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
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
    # Powers the "add €X more to unlock" nudge on the basket page.
    min_subtotal: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # A minimum recorded before the 2026-11-05 EUR cutover is a USD numeral;
    # the application cannot tell it from a deliberately chosen euro figure,
    # so it stays False (the coupon's minimum unshown, the code unappliable)
    # until an administrator re-saves the coupon from the Promotions desk —
    # that save is the approval. Rows minted through the desk carry True.
    min_subtotal_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("FALSE")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # One-and-done per customer — the welcome voucher's small print.
    # "Used" means a standing order carried the code; cancelling that order
    # hands the code back.
    once_per_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoyaltyVoucher(Base):
    """Which coupon a loyalty tier hands its regulars — VIPs one code, loyals
    another. The codes themselves are ordinary coupons minted on the
    Promotions desk; this table only records who gets which."""

    __tablename__ = "loyalty_vouchers"

    tier: Mapped[str] = mapped_column(String(10), primary_key=True)  # vip | loyal
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    coupon: Mapped[Coupon] = relationship()


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


class RestockAlert(Base):
    """A shopper's ask to be emailed the moment a sold-out bake returns.
    One live ask per email per product; the ask disappears with the email
    that answers it (and with the bake it watches)."""

    __tablename__ = "restock_alerts"
    __table_args__ = (UniqueConstraint("product_id", "email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
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
    # Required from the form since Nov 2026 (quotes move faster by phone);
    # the column itself stays nullable because ALTER COLUMN isn't portable
    # across SQLite and PostgreSQL — the API layer enforces it instead.
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

    # What the baker wrote back — every reply is emailed to the enquirer
    # and kept here so the whole conversation reads in one place.
    replies: Mapped[list["BulkReply"]] = relationship(
        back_populates="enquiry", cascade="all, delete-orphan", order_by="BulkReply.created_at"
    )


class BulkReply(Base):
    """One emailed answer from the baker to a catering lead."""

    __tablename__ = "bulk_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    enquiry_id: Mapped[int] = mapped_column(
        ForeignKey("bulk_enquiries.id", ondelete="CASCADE"), index=True
    )
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    enquiry: Mapped[BulkEnquiry] = relationship(back_populates="replies")


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
    # The money this order was placed in — a 3-letter code. New orders are
    # EUR only (2026-11-05 owner decision); rows that say "usd" are history
    # and stay exactly as placed. Amounts are never converted: the code is
    # how every Numeric column above should be read.
    currency: Mapped[str] = mapped_column(String(3), default="eur")
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

    # The Stripe trail on card orders: the checkout session that collected
    # the money (the webhook's key back to this row) and the payment intent
    # to refund against later. Both stay null on cash-on-delivery orders.
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), index=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    timeline: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.created_at"
    )
    # The per-rate VAT totals written at checkout in VAT-inclusive mode;
    # empty on every order placed before that cutover.
    tax_lines: Mapped[list["OrderTaxLine"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderTaxLine.vat_rate"
    )

    @property
    def can_be_cancelled(self) -> bool:
        return self.status in {s.value for s in CANCELLABLE_STATUSES}


class VatRate(Base):
    """A statutory VAT percentage, in force from a date (§ 12 Abs. 2 UStG:
    7 % reduced for Anlage 2 goods, 19 % standard otherwise). Reference
    data seeded at boot — deciding which *product* may use which rate is a
    per-product classification (Product.vat_category), never a rate row."""

    __tablename__ = "vat_rates"

    # REDUCED | STANDARD — the kinds app/vat.py resolves categories into
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric(5, 2))  # percent, e.g. 7.00 / 19.00


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
    # VAT snapshot, frozen at checkout once the shop prices VAT-inclusive
    # (app/vat.py price_line_inclusive). The gross pair mirror unit_price /
    # subtotal at write time; all seven stay NULL on every order placed
    # before that cutover — historical tax figures are never back-filled
    # by guessing.
    vat_category: Mapped[str | None] = mapped_column(String(20))
    vat_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    net_unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    gross_unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    net_line_total: Mapped[float | None] = mapped_column(Numeric(10, 2))
    vat_amount: Mapped[float | None] = mapped_column(Numeric(10, 2))
    gross_line_total: Mapped[float | None] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    # The live product, for the payment page's thumbnails. The snapshot
    # above keeps the line intact when the product is deleted (product_id
    # is SET NULL → this relationship simply becomes None).
    product: Mapped["Product | None"] = relationship()


class OrderEvent(Base):
    """Timeline entry for every order status change."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="timeline")


class OrderTaxLine(Base):
    """Per-order, per-rate VAT totals — the atom a VAT report aggregates.
    One row per distinct rate on the order, written once at checkout in
    VAT-inclusive mode (app/vat.py summarize_by_rate) and never recomputed."""

    __tablename__ = "order_tax_lines"
    __table_args__ = (UniqueConstraint("order_id", "vat_rate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    vat_rate: Mapped[float] = mapped_column(Numeric(5, 2))  # percent, e.g. 7.00 / 19.00
    net_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    vat_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    gross_amount: Mapped[float] = mapped_column(Numeric(10, 2))


class ProductVatEvent(Base):
    """The classification's own bake log: who touched a product's VAT
    standing, when, and exactly what changed. Records internal sign-offs —
    it never replaces the tax adviser's classification."""

    __tablename__ = "product_vat_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    # Who made the change — the id links to the account (and survives its
    # deletion as NULL), the name is the snapshot the ledger keeps.
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    admin_name: Mapped[str] = mapped_column(String(150))
    from_category: Mapped[str | None] = mapped_column(String(20))
    to_category: Mapped[str | None] = mapped_column(String(20))
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    from_milk_content: Mapped[float | None] = mapped_column(Numeric(5, 2))
    to_milk_content: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


# Storefront copy the admin can rewrite — these are the out-of-the-box lines.
# No delivery threshold is promised: the free-delivery threshold is a
# currency-bound figure pending owner approval as an EUR value, so the
# default copy must not quote one (the baker can write her own once set).
DEFAULT_ANNOUNCEMENT = "WELCOME10 takes 10% off your first order"
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
    # Real-world contact lines for the footer — set from the Settings tab.
    # NULL/empty means "not filled in yet", so nothing false ships publicly.
    contact_address: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    # One line per opening-hours row, free text ("Tue – Fri: 7:00 – 18:00").
    opening_hours: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def get_store_setting(db) -> StoreSetting:
    """The one settings row, created with defaults on first touch."""
    row = db.get(StoreSetting, 1)
    if row is None:
        row = StoreSetting(id=1, late_cancel_fee_percent=10.0)
        db.add(row)
        db.commit()
    return row


# ---------------------------------------------------------------------------
# Costing foundation (spec v1.2 §21, Phase 2). Nine new tables, all additive:
# built by create_all, starting empty — the owner enters every figure. No
# column is added to any existing table.
# ---------------------------------------------------------------------------

class IngredientCategory(Base):
    """An ingredient grouping that also carries the D18 review cadence its
    members inherit (dairy moves weekly → 30 days; dried goods → 180)."""

    __tablename__ = "ingredient_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # D18: the default review interval for members without their own override.
    review_interval_days: Mapped[int] = mapped_column(Integer, default=90)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ingredients: Mapped[list["Ingredient"]] = relationship(back_populates="category")


class Ingredient(Base):
    """One purchased input, with the figures costing reads. The current
    state lives here; every change to the purchase figures lands in the
    append-only ingredient_price_events ledger."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredient_categories.id", ondelete="RESTRICT"), index=True
    )
    # Canonical unit (app/costing.py vocabulary) the purchase figures read in.
    unit: Mapped[str] = mapped_column(String(20))
    purchase_qty: Mapped[float] = mapped_column(Numeric(12, 4))
    purchase_price: Mapped[float] = mapped_column(Numeric(10, 2))
    # Derived rate (price / qty), stored for costing — a rate, not an amount:
    # per-gram costs need more than cent precision (25 kg @ €37.50 → 1.50/kg).
    effective_cost_per_unit: Mapped[float] = mapped_column(Numeric(12, 6))
    currency: Mapped[str] = mapped_column(String(3), default="eur")
    # The only mass↔volume bridge costing will ever use — an explicitly
    # recorded, ingredient-specific figure. Never a silent density guess.
    density_g_per_ml: Mapped[float | None] = mapped_column(Numeric(10, 4))
    # §12 provenance — every estimate says where it came from and when to
    # trust it again. Estimates are labelled, never "verified".
    source: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(6), default="medium")
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reviewed: Mapped[date | None] = mapped_column(Date)
    next_reviewed: Mapped[date | None] = mapped_column(Date)  # computed, stored
    # Explicit per-ingredient override (wins over the category, then 90).
    review_interval_days: Mapped[int | None] = mapped_column(Integer)
    # Free text only — invoice/receipt reference; never credentials (§26).
    invoice_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    category: Mapped[IngredientCategory | None] = relationship(back_populates="ingredients")
    price_events: Mapped[list["IngredientPriceEvent"]] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan"
    )


class IngredientPriceEvent(Base):
    """The ingredient price ledger: append-only, written only on an actual
    change to the purchase figures or their provenance, with the actor and
    the full before/after snapshot (D5's estimate→actual replacement trail)."""

    __tablename__ = "ingredient_price_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), index=True
    )
    # Who made the change — the id links to the account (surviving its
    # deletion as NULL), the name is the snapshot the ledger keeps.
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    admin_name: Mapped[str] = mapped_column(String(150))
    prev_purchase_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))
    new_purchase_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))
    prev_purchase_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    new_purchase_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    prev_effective_cost_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 6))
    new_effective_cost_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 6))
    prev_unit: Mapped[str | None] = mapped_column(String(20))
    new_unit: Mapped[str | None] = mapped_column(String(20))
    prev_currency: Mapped[str | None] = mapped_column(String(3))
    new_currency: Mapped[str | None] = mapped_column(String(3))
    prev_is_estimate: Mapped[bool | None] = mapped_column(Boolean)
    new_is_estimate: Mapped[bool | None] = mapped_column(Boolean)
    prev_source: Mapped[str | None] = mapped_column(Text)
    new_source: Mapped[str | None] = mapped_column(Text)
    prev_confidence: Mapped[str | None] = mapped_column(String(6))
    new_confidence: Mapped[str | None] = mapped_column(String(6))
    # The date the new figures apply from (the write date; D5 effective-dating).
    effective_from: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ingredient: Mapped[Ingredient] = relationship(back_populates="price_events")


class Recipe(Base):
    """The costing truth for one product (1:1) — distinct from the
    customer-facing products.ingredients statement. Quantities, losses,
    labour split and yield all live here with their own provenance."""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Hands-on minutes (labour, D3) — unattended time is recorded but never
    # costs labour; any justified unattended cost belongs in overhead (§9).
    hands_on_minutes: Mapped[float] = mapped_column(Numeric(7, 2))
    unattended_minutes: Mapped[float] = mapped_column(Numeric(7, 2), default=0)
    expected_batch_yield: Mapped[int] = mapped_column(Integer)
    # A1: the recipe-level *estimated* batch overhead — a placeholder that a
    # closed batch's actual replaces for future costings; never both at once.
    batch_overhead_estimate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    source: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(6), default="medium")
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reviewed: Mapped[date | None] = mapped_column(Date)
    next_reviewed: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items: Mapped[list["RecipeItem"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeItem.id"
    )


class RecipeItem(Base):
    """One ingredient line of a recipe: usable quantity, its own unit and its
    own preparation loss. There is deliberately no global loss percentage
    anywhere — 0 % is a valid, explicit entry (D6)."""

    __tablename__ = "recipe_items"
    __table_args__ = (UniqueConstraint("recipe_id", "ingredient_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), index=True
    )
    usable_qty: Mapped[float] = mapped_column(Numeric(12, 4))
    unit: Mapped[str] = mapped_column(String(20))
    loss_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    note: Mapped[str | None] = mapped_column(String(255))

    recipe: Mapped[Recipe] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship()


class ProductionBatch(Base):
    """One production run: expected vs actual sellable yield, estimated vs
    actual batch overhead, actual usage notes. Closed batches are history —
    immutable (§8), and their actuals feed future costings (A1)."""

    __tablename__ = "production_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    produced_at: Mapped[date] = mapped_column(Date)
    # Snapshot of the recipe's figure at creation — the recipe may change
    # later; the batch record may not.
    expected_yield: Mapped[int] = mapped_column(Integer)
    actual_yield: Mapped[int | None] = mapped_column(Integer)
    batch_overhead_estimated: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    # Required at close (explicit zero allowed — never a guess).
    batch_overhead_actual: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # AC-8 ledger 2: newline list of the cost categories this batch charges.
    overhead_categories: Mapped[str | None] = mapped_column(Text)
    usage_notes: Mapped[str | None] = mapped_column(Text)
    # open | closed — plain string, Pydantic-enforced at the API layer.
    status: Mapped[str] = mapped_column(String(6), default="open")
    # (actual − expected) / expected × 100, stored once at close (−8.33).
    yield_variance_pct: Mapped[float | None] = mapped_column(Numeric(8, 2))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OverheadMonth(Base):
    """The fixed-overhead ledger, one row per month (D8/§10B): the forecast
    total and the expected monthly production hours define the hourly rate;
    month-end actuals are recorded beside the forecast, never over it."""

    __tablename__ = "overhead_months"

    id: Mapped[int] = mapped_column(primary_key=True)
    # First of the month; unique — one forecast per month.
    month: Mapped[date] = mapped_column(Date, unique=True)
    fixed_overhead_total: Mapped[float] = mapped_column(Numeric(10, 2))
    forecast_hours: Mapped[float] = mapped_column(Numeric(7, 2))
    currency: Mapped[str] = mapped_column(String(3), default="eur")
    # AC-8 ledger 1: newline list of the categories this pool includes.
    included_categories: Mapped[str | None] = mapped_column(Text)
    actual_total: Mapped[float | None] = mapped_column(Numeric(10, 2))
    actual_hours: Mapped[float | None] = mapped_column(Numeric(7, 2))
    variance_note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(6), default="medium")
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LabourRate(Base):
    """The centrally managed labour rate (D3): effective-dated rows in the
    vat_rates pattern — a change is a new dated row, history never moves.
    The table starts empty; until a row exists, costing falls back to the
    single named default in app/costing.py (€20.00/h, source
    "default_fallback"), snapshotted into every cost record either way."""

    __tablename__ = "labour_rates"

    valid_from: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="eur")
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProductCostRecord(Base):
    """One cost computation, append-only — the current cost of a product is
    its latest record (§12). The breakdown freezes every input and figure the
    calculation used (units, losses, rate version, overhead mode, yield
    source; Decimals as strings), so any historical decision can be
    reconstructed exactly. Incomplete results are stored as data: total NULL
    plus machine-readable missing reasons — never a guessed figure."""

    __tablename__ = "product_cost_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # ingredient_price_change | recipe_change | batch_closed |
    # overhead_month_change | labour_rate_change | initial
    trigger: Mapped[str] = mapped_column(String(30))
    # NULL only on incomplete records whose inputs mixed currencies.
    currency: Mapped[str | None] = mapped_column(String(3))
    # NULL while incomplete — an incomplete cost is never a number.
    total_cost: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # Machine-readable reason codes, e.g. ["no_overhead_month", "mixed_currencies"].
    missing: Mapped[list | None] = mapped_column(JSON)
    breakdown: Mapped[dict | None] = mapped_column(JSON)
