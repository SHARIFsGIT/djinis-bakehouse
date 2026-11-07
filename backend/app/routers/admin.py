"""Admin-only endpoints: dashboard stats, product CRUD, orders, coupons and users."""

import csv
import io
import re
import uuid
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select, update

from ..config import get_settings
from ..database import utcnow
from ..email import bulk_reply_email, send_email, send_restock_emails
from ..models import (
    DEFAULT_ANNOUNCEMENT,
    DEFAULT_HERO_IMAGE,
    DEFAULT_MARQUEE,
    DEFAULT_TODAY_NOTE,
    AdminNotification,
    BulkEnquiry,
    BulkEnquiryStatus,
    BulkReply,
    Category,
    ComingSoonItem,
    ClassificationEvent,
    Coupon,
    LoyaltyVoucher,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    Product,
    ProductVatEvent,
    Recipe,
    RestockAlert,
    Review,
    Testimonial,
    User,
    UserRole,
    get_store_setting,
)
from ..schemas import (
    AdminCustomerOut,
    AdminNotificationList,
    AdminNotificationOut,
    AdminProductCard,
    AdminProductDetail,
    AdminProductListResponse,
    AdminReviewCounts,
    AdminReviewList,
    AdminReviewOut,
    AdminStatsOut,
    AdminUserPage,
    AdminVatUpdateRequest,
    BulkEnquiryOut,
    BulkEnquiryUpdate,
    BulkReplyCreate,
    CategoryCreateRequest,
    CategoryOut,
    ClassificationEventOut,
    ClassificationUpdateRequest,
    ComingSoonCreate,
    ComingSoonOut,
    ComingSoonUpdate,
    CouponCreateRequest,
    CouponOut,
    CouponUpdateRequest,
    CurrencyStatsOut,
    DayRevenueOut,
    LoyaltyVouchersOut,
    OrderDetailOut,
    OrderListResponse,
    OrderStatusUpdateRequest,
    PaginationOut,
    PaymentStatusUpdateRequest,
    ProductCostSummaryOut,
    ProductCreateRequest,
    ProductUpdateRequest,
    ReviewModerationRequest,
    StoreSettingsOut,
    StoreSettingsUpdate,
    TestimonialCreate,
    TestimonialOut,
    TestimonialUpdate,
    UserAdminUpdateRequest,
    UserOut,
    VoucherLinkRequest,
)
from ..loyalty import loyalty_tier as _loyalty
from ..security import CurrentAdmin, DbSession
from ..serialize import product_to_card, review_summary, verified_buyers
from .. import costing
from ..vat import REVIEW_STATUS_APPROVED, preview_rate, validate_classification
from ..uploads import MAX_PRODUCT_PHOTO_BYTES, save_photo
from .orders import (
    _cancel_order,
    _late_cancel_fee,
    _lock_for_cancellation,
    _serialize,
    queue_status_email,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# A brand colour is exactly "#rrggbb" — six hex digits, nothing else.
_HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")


def _slugify(name: str, db, table) -> str:
    """A URL-safe, unique slug for any slugged table (products, categories)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    slug = base
    while db.scalar(select(table).where(table.slug == slug)):
        slug = f"{base}-{uuid.uuid4().hex[:6]}"
    return slug


# --------------------------------------------------------------------------
# Dashboard stats
# --------------------------------------------------------------------------

@router.get("/stats", response_model=AdminStatsOut)
def stats(admin: CurrentAdmin, db: DbSession):
    orders = db.scalars(select(Order).where(Order.status != OrderStatus.CANCELLED.value)).all()
    customers = db.scalar(select(func.count(User.id)).where(User.role == UserRole.CUSTOMER.value))

    # Money is bucketed by the currency each order was placed in — dollars
    # never sum with euros. An order with no readable currency keeps its own
    # bucket rather than being guessed into one.
    buckets: dict[str, dict] = {}
    for o in orders:
        code = str(o.currency or "").strip().lower() or "unknown"
        bucket = buckets.setdefault(
            code, {"total": 0.0, "paid": 0.0, "refunded": 0.0, "count": 0}
        )
        amount = float(o.total)
        bucket["total"] += amount
        if o.payment_status in (PaymentStatus.PAID.value, PaymentStatus.REFUNDED.value):
            bucket["paid"] += amount
        if o.payment_status in (
            PaymentStatus.REFUNDED.value,
            PaymentStatus.PARTIALLY_REFUNDED.value,
        ):
            # What actually went back: the total less whatever slice of a
            # late cancellation the shop kept.
            bucket["refunded"] += amount - float(o.cancel_fee or 0)
        bucket["count"] += 1

    # The headline figures describe the shop's active currency only; every
    # currency's own slice (active first, rest alphabetical) rides alongside.
    active = str(get_settings().stripe_currency or "").strip().lower()
    ordered_codes = sorted(buckets, key=lambda c: (c != active, c))
    by_currency = [
        CurrencyStatsOut(
            currency=code,
            total_revenue=round(buckets[code]["total"], 2),
            paid_revenue=round(buckets[code]["paid"], 2),
            refunded_revenue=round(buckets[code]["refunded"], 2),
            order_count=buckets[code]["count"],
            avg_order_value=round(buckets[code]["total"] / buckets[code]["count"], 2),
        )
        for code in ordered_codes
    ]
    active_bucket = buckets.get(active, {"total": 0.0, "count": 0})

    by_status = {s.value: 0 for s in OrderStatus}
    for o in db.scalars(select(Order)):
        by_status[o.status] = by_status.get(o.status, 0) + 1

    top_rows = (
        db.query(OrderItem.product_name, func.sum(OrderItem.quantity).label("qty"))
        .join(Order)
        .where(Order.status != OrderStatus.CANCELLED.value)
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
        .all()
    )

    recent = db.scalars(select(Order).order_by(Order.created_at.desc()).limit(5)).all()
    low_stock_products = db.scalars(
        select(Product).where(Product.stock <= 5, Product.is_available.is_(True)).limit(8)
    ).all()
    low_ratings = review_summary(db, [p.id for p in low_stock_products])

    # The trading chart: the active currency's kept orders, one bar per day,
    # zeros included, so quiet days read as quiet rather than missing. The
    # window rides UTC because created_at does — a local-clock "today" would
    # drift a bar near midnight. Bucketed from the kept orders already
    # loaded above: portable across SQLite and PostgreSQL, where date
    # arithmetic in SQL speaks different dialects.
    series_start = utcnow().date() - timedelta(days=29)
    by_day: dict[date, tuple[float, int]] = {}
    for o in orders:
        if str(o.currency or "").strip().lower() != active:
            continue
        day = o.created_at.date()
        if day < series_start:
            continue
        revenue, count = by_day.get(day, (0.0, 0))
        by_day[day] = (revenue + float(o.total), count + 1)
    revenue_series = []
    for offset in range(30):
        day = series_start + timedelta(days=offset)
        revenue, count = by_day.get(day, (0.0, 0))
        revenue_series.append(DayRevenueOut(date=day, revenue=round(revenue, 2), orders=count))

    return AdminStatsOut(
        total_revenue=round(active_bucket["total"], 2),
        paid_revenue=round(buckets.get(active, {}).get("paid", 0.0), 2),
        order_count=len(orders),
        customer_count=customers or 0,
        avg_order_value=round(active_bucket["total"] / active_bucket["count"], 2)
        if active_bucket["count"]
        else 0.0,
        pending_orders=by_status.get(OrderStatus.PENDING.value, 0),
        orders_by_status=by_status,
        top_products=[{"name": name, "quantity": int(qty)} for name, qty in top_rows],
        recent_orders=[_serialize(o) for o in recent],
        low_stock=[product_to_card(p, low_ratings) for p in low_stock_products],
        # Fresh catering leads the Orders desk shouldn't miss
        new_enquiries=db.scalar(
            select(func.count(BulkEnquiry.id)).where(
                BulkEnquiry.status == BulkEnquiryStatus.NEW.value
            )
        )
        or 0,
        revenue_by_currency=by_currency,
        revenue_series=revenue_series,
    )


# --------------------------------------------------------------------------
# The baker's bell (desk notifications)
# --------------------------------------------------------------------------

@router.get("/notifications", response_model=AdminNotificationList)
def list_notifications(
    admin: CurrentAdmin,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=50),
    unread_only: bool = False,
):
    """The desk's pings, newest first, with the unread tally for the badge."""
    query = select(AdminNotification).order_by(AdminNotification.created_at.desc())
    if unread_only:
        query = query.where(AdminNotification.is_read.is_(False))
    items = db.scalars(query.limit(limit)).all()
    unread = db.scalar(
        select(func.count(AdminNotification.id)).where(AdminNotification.is_read.is_(False))
    )
    return AdminNotificationList(items=items, unread_count=unread or 0)


@router.post("/notifications/{notification_id}/read", response_model=AdminNotificationOut)
def mark_notification_read(notification_id: int, admin: CurrentAdmin, db: DbSession):
    notification = db.get(AdminNotification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification


@router.post("/notifications/read-all")
def mark_all_notifications_read(admin: CurrentAdmin, db: DbSession):
    db.execute(
        update(AdminNotification).where(AdminNotification.is_read.is_(False)).values(is_read=True)
    )
    db.commit()
    return {"message": "All caught up", "unread_count": 0}


# --------------------------------------------------------------------------
# Shop settings
# --------------------------------------------------------------------------

@router.get("/settings", response_model=StoreSettingsOut)
def get_shop_settings(admin: CurrentAdmin, db: DbSession):
    settings = get_store_setting(db)
    # NULL (never set) shows as the shipped default so the form starts full
    settings.announcement = (
        settings.announcement if settings.announcement is not None else DEFAULT_ANNOUNCEMENT
    )
    settings.today_note = (
        settings.today_note if settings.today_note is not None else DEFAULT_TODAY_NOTE
    )
    settings.hero_image = settings.hero_image or DEFAULT_HERO_IMAGE
    settings.marquee_items = (
        settings.marquee_items if settings.marquee_items is not None else DEFAULT_MARQUEE
    )
    return settings


@router.put("/settings", response_model=StoreSettingsOut)
def update_shop_settings(payload: StoreSettingsUpdate, admin: CurrentAdmin, db: DbSession):
    settings = get_store_setting(db)
    if payload.late_cancel_fee_percent is not None:
        settings.late_cancel_fee_percent = payload.late_cancel_fee_percent
    if payload.announcement is not None:
        settings.announcement = payload.announcement.strip()
    if payload.today_note is not None:
        settings.today_note = payload.today_note.strip()
    if payload.hero_image is not None:
        # An empty value clears the choice — the shipped photo comes back
        settings.hero_image = payload.hero_image.strip() or None
    if payload.marquee_items is not None:
        # Empty clears the strip on purpose — the band hides until refilled
        settings.marquee_items = payload.marquee_items.strip()
    if payload.brand_color is not None:
        # The brand colour: "#rrggbb", or empty to return to the shipped
        # burgundy. Anything half-formed is refused in plain words rather
        # than silently painting the shop a colour the baker never picked.
        value = payload.brand_color.strip().lower()
        if value and not _HEX_COLOR.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail="Pick a colour as a six-digit hex code, like #7f2230",
            )
        settings.brand_color = value or None
    for field in ("contact_address", "contact_phone", "contact_email", "opening_hours"):
        value = getattr(payload, field)
        if value is not None:
            if field == "contact_email" and value.strip() and "@" not in value:
                raise HTTPException(
                    status_code=400,
                    detail="That contact email doesn't look right — check the spelling",
                )
            # Empty clears the line — an unset detail never shows a placeholder
            setattr(settings, field, value.strip() or None)
    db.commit()
    db.refresh(settings)
    settings.hero_image = settings.hero_image or DEFAULT_HERO_IMAGE
    settings.marquee_items = (
        settings.marquee_items if settings.marquee_items is not None else DEFAULT_MARQUEE
    )
    return settings


# --------------------------------------------------------------------------
# Order management
# --------------------------------------------------------------------------

@router.get("/orders", response_model=OrderListResponse)
def all_orders(
    admin: CurrentAdmin,
    db: DbSession,
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=15, ge=1, le=50),
):
    query = select(Order)
    if status_filter:
        query = query.where(Order.status == status_filter.value)
    if search:
        term = f"%{search.strip()}%"
        query = query.join(User).where(
            or_(
                Order.order_number.ilike(term),
                User.email.ilike(term),
                User.first_name.ilike(term),
                User.last_name.ilike(term),
            )
        )

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    orders = db.scalars(
        query.order_by(Order.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()

    return OrderListResponse(
        orders=[_serialize(o) for o in orders],
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.put("/orders/{order_id}/status", response_model=OrderDetailOut)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdateRequest,
    admin: CurrentAdmin,
    db: DbSession,
    background: BackgroundTasks,
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # The status dial is the console's cancel path — run the full
    # cancellation (stock back, refund settled), free of the late fee.
    if payload.status == OrderStatus.CANCELLED:
        # Judge under the lock: the customer's cancel click or the expiry
        # sweep may have landed a moment earlier.
        order = _lock_for_cancellation(order, db)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.status == OrderStatus.CANCELLED.value:
            raise HTTPException(status_code=400, detail="Order is already cancelled")
        _cancel_order(order, db, by_customer=False, comment=payload.comment)
        db.commit()
        queue_status_email(order, background, comment=payload.comment)
        db.refresh(order)
        return _serialize(order, detailed=True, cancel_fee_preview=_late_cancel_fee(order, db))

    if order.status == OrderStatus.CANCELLED.value:
        raise HTTPException(
            status_code=400,
            detail="A cancelled order cannot be reopened — place a new order instead",
        )

    old_status = order.status
    order.status = payload.status.value
    if payload.status == OrderStatus.DELIVERED:
        if order.payment_method == "cash_on_delivery" and order.payment_status == PaymentStatus.PENDING.value:
            order.payment_status = PaymentStatus.PAID.value
    db.add(
        OrderEvent(
            order_id=order.id,
            status=payload.status.value,
            comment=payload.comment or f"Status changed from {old_status} to {payload.status.value}",
        )
    )
    db.commit()
    # Every dial the desk makes is news the customer asked for when they
    # ordered — the mailbox hears it in the same breath.
    queue_status_email(order, background, comment=payload.comment)
    db.refresh(order)
    return _serialize(order, detailed=True, cancel_fee_preview=_late_cancel_fee(order, db))


@router.put("/orders/{order_id}/payment", response_model=OrderDetailOut)
def update_payment_status(
    order_id: int, payload: PaymentStatusUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.payment_status = payload.payment_status
    db.add(
        OrderEvent(order_id=order.id, status=order.status, comment=f"Payment marked {payload.payment_status}")
    )
    db.commit()
    db.refresh(order)
    return _serialize(order, detailed=True)


@router.get("/orders/export")
def export_orders(admin: CurrentAdmin, db: DbSession):
    """CSV dump of every order — for bookkeeping or a spreadsheet."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["order_number", "date", "customer", "email", "status", "payment_method",
         "payment_status", "coupon_code", "subtotal", "discount", "tax", "shipping", "total",
         "currency"]
    )
    for order in db.scalars(select(Order).order_by(Order.created_at.desc())):
        writer.writerow(
            [
                order.order_number,
                order.created_at.strftime("%Y-%m-%d %H:%M"),
                order.user.full_name if order.user else "",
                order.user.email if order.user else "",
                order.status,
                order.payment_method,
                order.payment_status,
                order.coupon_code or "",
                float(order.subtotal),
                float(order.discount_amount or 0),
                float(order.tax),
                float(order.shipping_fee),
                float(order.total),
                # Every money column above reads in this code — bookkeeping
                # must never have to guess which side of a cutover a row is
                # from. A blank reads "unknown", the same label the stats
                # buckets use, so a row always reconciles with its totals.
                (order.currency or "").strip() or "unknown",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bakehouse-orders.csv"},
    )


# --------------------------------------------------------------------------
# Category management
# --------------------------------------------------------------------------

@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreateRequest, admin: CurrentAdmin, db: DbSession):
    """Add a shelf on the fly — the product form offers this while typing a
    new bake, so the baker never has to leave the flow to sort categories
    first. New shelves sort after the existing ones."""
    name = payload.name.strip()
    if db.scalar(select(Category).where(func.lower(Category.name) == name.lower())):
        raise HTTPException(status_code=409, detail="That shelf already exists")

    sort_order = (db.scalar(select(func.max(Category.sort_order))) or 0) + 1
    category = Category(
        name=name,
        slug=_slugify(name, db, Category),
        image_url=payload.image_url,
        sort_order=sort_order,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return CategoryOut(
        id=category.id,
        name=category.name,
        slug=category.slug,
        description=category.description,
        image_url=category.image_url,
        product_count=0,
    )


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, admin: CurrentAdmin, db: DbSession):
    """Retire a shelf — any shelf, occupied or not. Its bakes stay on the
    case (moved off the shelf, awaiting a new one), so retiring a label
    never loses a bake or a past order's line items."""
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Shelf not found")
    count = db.scalar(
        select(func.count(Product.id)).where(Product.category_id == category_id)
    )
    if count:
        db.execute(
            update(Product)
            .where(Product.category_id == category_id)
            .values(category_id=None)
        )
    name = category.name
    db.delete(category)
    db.commit()
    message = f"“{name}” shelf removed"
    if count:
        message += f" — {count} {'bake' if count == 1 else 'bakes'} left off-shelf"
    return {"message": message}


# --------------------------------------------------------------------------
# Product management
# --------------------------------------------------------------------------

def _admin_card(product: Product, db: DbSession) -> AdminProductCard:
    """The desk's view of a product: the public card plus the VAT
    classification and the provisional rate the current configuration
    implies for it (None when the classification implies no rate), plus
    the Phase 2 derived cost status."""
    preview = preview_rate(product.vat_category, product.milk_content_percent, db)
    settings = get_settings()
    cost_status, _summary = costing.product_cost_summary(
        db, product, settings.stripe_currency, settings.tax_mode
    )
    return AdminProductCard(
        **product_to_card(product).model_dump(),
        vat_category=product.vat_category,
        vat_review_status=product.vat_review_status or "pending",
        milk_content_percent=(
            float(product.milk_content_percent)
            if product.milk_content_percent is not None
            else None
        ),
        vat_rate_preview=float(preview) if preview is not None else None,
        cost_status=cost_status,
    )


@router.get("/products", response_model=AdminProductListResponse)
def all_products(
    admin: CurrentAdmin,
    db: DbSession,
    search: str | None = None,
    category_id: int | None = Query(default=None),
    needs_vat_review: bool = Query(default=False),
    needs_cost_review: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=15, ge=1, le=100),
):
    query = select(Product)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(Product.name.ilike(term), Product.slug.ilike(term)))
    # tapping a shelf on the desk shows just that shelf's bakes
    if category_id is not None:
        query = query.where(Product.category_id == category_id)
    # the tax-review rail: everything not yet signed off for the euro cutover
    if needs_vat_review:
        query = query.where(Product.vat_review_status != REVIEW_STATUS_APPROVED)
    # the cost-review rail: recipe recorded but cost missing, incomplete,
    # or running on overdue inputs (derived at read — never stored state)
    if needs_cost_review:
        needing = costing.products_needing_cost_review(db)
        query = query.where(Product.id.in_(needing or {0}))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    products = db.scalars(
        query.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()

    return AdminProductListResponse(
        products=[_admin_card(p, db) for p in products],
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.get("/products/{product_id}", response_model=AdminProductDetail)
def admin_product_detail(product_id: int, admin: CurrentAdmin, db: DbSession):
    """Full product payload for the admin edit form (works for hidden products too)."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    events = db.scalars(
        select(ProductVatEvent)
        .where(ProductVatEvent.product_id == product.id)
        .order_by(ProductVatEvent.created_at.desc(), ProductVatEvent.id.desc())
        .limit(20)
    ).all()
    settings = get_settings()
    _status, cost_summary = costing.product_cost_summary(
        db, product, settings.stripe_currency, settings.tax_mode
    )
    has_recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id)) is not None
    return AdminProductDetail(
        **_admin_card(product, db).model_dump(),
        description=product.description,
        prep_time_minutes=product.prep_time_minutes,
        ingredients=product.ingredients,
        allergens=product.allergens,
        created_at=product.created_at,
        vat_events=events,
        # the full cost picture exists only once costing has started —
        # no recipe means there is nothing to summarize yet
        cost=ProductCostSummaryOut(**cost_summary) if has_recipe else None,
    )


@router.post("/products", response_model=AdminProductCard, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreateRequest, admin: CurrentAdmin, db: DbSession):
    product = Product(
        name=payload.name.strip(),
        slug=_slugify(payload.name, db, Product),
        description=payload.description,
        price=payload.price,
        sale_price=payload.sale_price,
        category_id=payload.category_id,
        is_featured=payload.is_featured,
        is_available=payload.is_available,
        stock=payload.stock,
        prep_time_minutes=payload.prep_time_minutes,
        image_url=payload.image_url,
        ingredients=payload.ingredients,
        allergens=payload.allergens,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _admin_card(product, db)


@router.put("/products/{product_id}", response_model=AdminProductCard)
def update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    admin: CurrentAdmin,
    db: DbSession,
    background: BackgroundTasks,
    request: Request,
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    was_sold_out = product.stock <= 0 or not product.is_available

    data = payload.model_dump()
    data["name"] = data["name"].strip()
    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    _answer_restock_alerts(product, was_sold_out, db, background, request)
    db.refresh(product)
    return _admin_card(product, db)


def _answer_restock_alerts(
    product: Product, was_sold_out: bool, db: DbSession, background: BackgroundTasks, request: Request
) -> None:
    """The moment a watched bake is buyable again, every waiting shopper
    hears — once. The asks are claimed and cleared in the same transaction
    that restocked the shelf, so a second save while the emails are in
    flight can never answer the same ask twice."""
    if not was_sold_out or product.stock <= 0 or not product.is_available:
        return
    alerts = [
        {"email": email}
        for email in db.scalars(
            select(RestockAlert.email).where(RestockAlert.product_id == product.id)
        )
    ]
    if not alerts:
        return
    db.execute(delete(RestockAlert).where(RestockAlert.product_id == product.id))
    db.commit()
    product_url = f"{str(request.base_url).rstrip('/')}/products/{product.slug}"
    background.add_task(send_restock_emails, alerts, product_url, product.name)


@router.put("/products/{product_id}/vat", response_model=AdminProductCard)
def update_product_vat(
    product_id: int, payload: AdminVatUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    """The one writer for a product's VAT classification: replaces the trio,
    refuses incoherent combinations, and records every actual change — who,
    when, from what, to what. An approval here is the shop's internal
    sign-off, never a substitute for the tax adviser's classification."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        validate_classification(
            payload.vat_category, payload.vat_review_status, payload.milk_content_percent
        )
    except ValueError as reason:
        raise HTTPException(status_code=400, detail=str(reason)) from None

    before = (product.vat_category, product.vat_review_status, product.milk_content_percent)
    after = (payload.vat_category, payload.vat_review_status, payload.milk_content_percent)
    if before != after:
        db.add(
            ProductVatEvent(
                product_id=product.id,
                admin_user_id=admin.id,
                admin_name=admin.full_name,
                from_category=before[0],
                to_category=after[0],
                from_status=before[1],
                to_status=after[1],
                from_milk_content=before[2],
                to_milk_content=after[2],
            )
        )
        product.vat_category, product.vat_review_status, product.milk_content_percent = after
        db.commit()
        db.refresh(product)
    return _admin_card(product, db)


@router.delete("/products/{product_id}")
def delete_product(product_id: int, admin: CurrentAdmin, db: DbSession):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    # Waiting restock asks go with the bake — cleared here rather than by the
    # FK cascade alone, so it also holds on the dev database, where SQLite
    # runs without foreign keys enforced.
    db.execute(delete(RestockAlert).where(RestockAlert.product_id == product_id))
    db.delete(product)
    db.commit()
    return {"message": "Product deleted"}


@router.post("/uploads/product-photo")
async def upload_product_photo(photo: UploadFile, admin: CurrentAdmin):
    """Store a photo picked in the product form and hand back its URL.

    Decoupled from the product save so it works the same whether the baker is
    editing an existing bake or adding a new one — the form holds the URL and
    persists it with the rest of the record.
    """
    url = await save_photo(photo, folder="products", max_bytes=MAX_PRODUCT_PHOTO_BYTES)
    return {"url": url}


@router.post("/uploads/hero-photo")
async def upload_hero_photo(photo: UploadFile, admin: CurrentAdmin):
    """The photo above the today note on the home page (Settings tab)."""
    url = await save_photo(photo, folder="site", max_bytes=MAX_PRODUCT_PHOTO_BYTES)
    return {"url": url}


# --------------------------------------------------------------------------
# Review moderation
# --------------------------------------------------------------------------

def _admin_review(review: Review, verified: bool) -> AdminReviewOut:
    return AdminReviewOut(
        id=review.id,
        product_id=review.product_id,
        product_name=review.product.name,
        product_slug=review.product.slug,
        product_image=review.product.image_url,
        author_name=review.user.full_name if review.user else None,
        author_email=review.user.email if review.user else None,
        rating=review.rating,
        comment=review.comment,
        verified_purchase=verified,
        is_approved=review.is_approved,
        created_at=review.created_at,
    )


@router.get("/reviews", response_model=AdminReviewList)
def all_reviews(
    admin: CurrentAdmin,
    db: DbSession,
    status_filter: str = Query(default="all", alias="status", pattern="^(live|hidden|all)$"),
):
    """Every review as the desk sees it: whose, on what, live or taken down —
    with the shelf tallies so the tabs can count themselves."""
    query = select(Review).order_by(Review.created_at.desc())
    if status_filter == "live":
        query = query.where(Review.is_approved.is_(True))
    elif status_filter == "hidden":
        query = query.where(Review.is_approved.is_(False))
    reviews = db.scalars(query).all()

    # Verified-purchase flags in one pass per product, not one query per row.
    by_product: dict[int, list[int]] = {}
    for review in reviews:
        by_product.setdefault(review.product_id, []).append(review.user_id)
    verified: set[tuple[int, int]] = set()
    for product_id, user_ids in by_product.items():
        verified.update((product_id, uid) for uid in verified_buyers(db, product_id, user_ids))

    tallies = dict(
        db.execute(
            select(Review.is_approved, func.count(Review.id)).group_by(Review.is_approved)
        ).all()
    )
    live_count = tallies.get(True, 0)
    hidden_count = tallies.get(False, 0)
    live_average = db.scalar(
        select(func.avg(Review.rating)).where(Review.is_approved.is_(True))
    )
    counts = AdminReviewCounts(
        live=live_count,
        hidden=hidden_count,
        all=live_count + hidden_count,
        live_average=round(live_average, 1) if live_average is not None else None,
    )
    items = [_admin_review(r, (r.product_id, r.user_id) in verified) for r in reviews]
    return AdminReviewList(items=items, counts=counts)


@router.put("/reviews/{review_id}", response_model=AdminReviewOut)
def moderate_review(
    review_id: int, payload: ReviewModerationRequest, admin: CurrentAdmin, db: DbSession
):
    """Put a review on the wall or take it down. The reviewer keeps their
    copy (their own view explains what happened); only the public wall and
    the averages change."""
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    review.is_approved = payload.is_approved
    db.commit()
    db.refresh(review)
    verified = review.user_id in verified_buyers(db, review.product_id, [review.user_id])
    return _admin_review(review, verified)


@router.delete("/reviews/{review_id}")
def delete_review_admin(review_id: int, admin: CurrentAdmin, db: DbSession):
    """Remove a review outright — spam and abuse. Hiding is the gentler,
    reversible tool; this one is for the bin."""
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------

@router.get("/coupons", response_model=list[CouponOut])
def all_coupons(admin: CurrentAdmin, db: DbSession):
    """The coupon tin, each code with its honest usage count: kept orders
    that carried it, derived in one aggregate pass — cancelled orders never
    count as redemptions, and nothing is stored to go stale."""
    coupons = list(db.scalars(select(Coupon).order_by(Coupon.created_at.desc())))
    used: dict[str, int] = dict(
        db.execute(
            select(Order.coupon_code, func.count(Order.id))
            .where(
                Order.coupon_code.is_not(None),
                Order.status != OrderStatus.CANCELLED.value,
            )
            .group_by(Order.coupon_code)
        ).all()
    )
    # Personalised vouchers name their owner at the marketing desk — a bare
    # user id would leave the voucher shelf faceless.
    owners = {
        u.id: u.full_name
        for u in db.scalars(
            select(User).where(User.id.in_(
                {c.restricted_to_user_id for c in coupons if c.restricted_to_user_id}
            ))
        )
    }
    return [
        CouponOut.model_validate(c).model_copy(
            update={
                "redemptions": used.get(c.code, 0),
                "restricted_to_name": owners.get(c.restricted_to_user_id),
            }
        )
        for c in coupons
    ]


@router.post("/coupons", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
def create_coupon(payload: CouponCreateRequest, admin: CurrentAdmin, db: DbSession):
    code = payload.code.strip().upper()
    if db.scalar(select(Coupon).where(Coupon.code == code)):
        raise HTTPException(status_code=409, detail="A coupon with this code already exists")
    coupon = Coupon(
        code=code,
        percent_off=payload.percent_off,
        description=payload.description,
        min_subtotal=payload.min_subtotal or None,
        # A threshold typed at the desk is chosen in today's currency — the
        # act of saving it is the approval a pre-cutover numeral never had.
        min_subtotal_approved=(payload.min_subtotal or 0) > 0,
        once_per_customer=payload.once_per_customer,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.put("/coupons/{coupon_id}", response_model=CouponOut)
def update_coupon(coupon_id: int, payload: CouponUpdateRequest, admin: CurrentAdmin, db: DbSession):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    updates = payload.model_dump(exclude_unset=True)
    # Re-saving a minimum from the desk (re)approves it as a euro threshold —
    # the reviewed escape hatch for coupons carrying pre-cutover USD numerals.
    # Clearing it to None takes the question away entirely.
    if "min_subtotal" in updates:
        coupon.min_subtotal = updates.pop("min_subtotal") or None
        coupon.min_subtotal_approved = (coupon.min_subtotal or 0) > 0
    for field, value in updates.items():
        setattr(coupon, field, value)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.delete("/coupons/{coupon_id}")
def delete_coupon(coupon_id: int, admin: CurrentAdmin, db: DbSession):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    # Any tier handing this code out goes back to "no code chosen" — cleared
    # here rather than by the FK cascade alone, so it holds on the dev
    # database too, where SQLite runs without foreign keys enforced.
    db.execute(delete(LoyaltyVoucher).where(LoyaltyVoucher.coupon_id == coupon_id))
    db.delete(coupon)
    db.commit()
    return {"message": "Coupon deleted"}


# --------------------------------------------------------------------------
# Loyalty vouchers — which coupon each tier of regulars gets
# --------------------------------------------------------------------------

VOUCHER_TIERS = ("vip", "loyal")


def _tier_vouchers(db: DbSession) -> dict[str, Coupon | None]:
    """The coupon each tier hands out today; a tier with nothing chosen
    sits at None rather than missing from the map."""
    linked = {v.tier: v.coupon for v in db.scalars(select(LoyaltyVoucher))}
    return {tier: linked.get(tier) for tier in VOUCHER_TIERS}


@router.get("/vat/report")
def vat_classification_report(admin: CurrentAdmin, db: DbSession):
    """The classification inventory the owner reviews before inclusive
    checkout opens: every purchasable bake's VAT standing, the rates in
    force, the shipping-VAT configuration, and the exact blockers that
    would keep inclusive checkout closed today. Read-only, derived at read
    time, no legal determination — a classification is the owner's call at
    the desk (with the audit trail it leaves behind)."""
    from ..models import VatRate

    products = db.scalars(
        select(Product).where(Product.is_available.is_(True)).order_by(Product.name)
    ).all()
    rows = []
    for p in products:
        preview = preview_rate(p.vat_category, p.milk_content_percent, db)
        priceable = (
            p.vat_review_status == REVIEW_STATUS_APPROVED and preview is not None
        )
        rows.append(
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "vat_category": p.vat_category,
                "review_status": p.vat_review_status or "pending",
                "rate_preview": float(preview) if preview is not None else None,
                "priceable": priceable,
            }
        )
    blockers = [r["name"] for r in rows if not r["priceable"]]
    rates = [
        {"code": code, "rate": float(rate)}
        for code, rate in db.execute(select(VatRate.code, VatRate.rate)).all()
    ]
    settings = get_settings()
    return {
        "tax_mode": settings.tax_mode,
        "checkout_ready_for_inclusive": not blockers,
        "counts": {
            "total": len(rows),
            "priceable": len(rows) - len(blockers),
            "blocked_or_unclassified": len(blockers),
        },
        "blockers": blockers,
        "rates_in_force": rates,
        "shipping_vat": {
            "follows_product_vat": True,
            "mixed_cart_allocation": "proportional (largest-remainder, cent-exact)",
        },
        "delivery": {
            "standard_fee": settings.shipping_fee,
            "free_threshold": settings.free_shipping_threshold,
            "vip_free_threshold": settings.vip_free_delivery_threshold,
        },
        "products": rows,
    }


@router.get("/loyalty-vouchers", response_model=LoyaltyVouchersOut)
def get_loyalty_vouchers(admin: CurrentAdmin, db: DbSession):
    """The code a VIP earns and the code a loyal regular earns — so the
    roll's gift buttons know what to hand over."""
    return LoyaltyVouchersOut(**_tier_vouchers(db))


@router.put("/loyalty-vouchers/{tier}", response_model=LoyaltyVouchersOut)
def link_loyalty_voucher(
    tier: str, payload: VoucherLinkRequest, admin: CurrentAdmin, db: DbSession
):
    """Choose (or clear) a tier's code. Coupons are minted on the Promotions
    desk; this only decides which tier gets which."""
    if tier not in VOUCHER_TIERS:
        raise HTTPException(status_code=404, detail="Unknown loyalty tier")
    if payload.coupon_id is not None and db.get(Coupon, payload.coupon_id) is None:
        raise HTTPException(status_code=404, detail="Coupon not found")

    voucher = db.get(LoyaltyVoucher, tier)
    if payload.coupon_id is None:
        if voucher:
            db.delete(voucher)
    elif voucher:
        voucher.coupon_id = payload.coupon_id
    else:
        db.add(LoyaltyVoucher(tier=tier, coupon_id=payload.coupon_id))
    db.commit()
    return LoyaltyVouchersOut(**_tier_vouchers(db))


# --------------------------------------------------------------------------
# Bulk (event & catering) enquiries
# --------------------------------------------------------------------------

@router.get("/bulk-orders", response_model=list[BulkEnquiryOut])
def all_bulk_enquiries(
    admin: CurrentAdmin,
    db: DbSession,
    status_filter: BulkEnquiryStatus | None = Query(default=None, alias="status"),
):
    """Catering leads, newest first — the enquiry rail under Orders."""
    query = select(BulkEnquiry)
    if status_filter:
        query = query.where(BulkEnquiry.status == status_filter.value)
    return list(db.scalars(query.order_by(BulkEnquiry.created_at.desc())))


@router.put("/bulk-orders/{enquiry_id}", response_model=BulkEnquiryOut)
def update_bulk_enquiry(
    enquiry_id: int, payload: BulkEnquiryUpdate, admin: CurrentAdmin, db: DbSession
):
    """Move a lead along (new → contacted → quoted → confirmed/declined) and
    keep the baker's own notes beside it."""
    enquiry = db.get(BulkEnquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    if payload.status is not None:
        enquiry.status = payload.status.value
    if payload.admin_note is not None:
        enquiry.admin_note = payload.admin_note
    db.commit()
    db.refresh(enquiry)
    return enquiry


@router.delete("/bulk-orders/{enquiry_id}")
def delete_bulk_enquiry(enquiry_id: int, admin: CurrentAdmin, db: DbSession):
    """For spam and test entries — real history is archived by declining."""
    enquiry = db.get(BulkEnquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    db.delete(enquiry)
    db.commit()
    return {"message": "Enquiry deleted"}


@router.post("/bulk-orders/{enquiry_id}/reply", response_model=BulkEnquiryOut, status_code=status.HTTP_201_CREATED)
def reply_to_bulk_enquiry(
    enquiry_id: int,
    payload: BulkReplyCreate,
    admin: CurrentAdmin,
    db: DbSession,
    background: BackgroundTasks,
):
    """Answer a lead by email straight from the drawer.

    The message is stored on the enquiry (the whole conversation reads in
    one place) and emailed to the enquirer. A first reply to a brand-new
    lead moves it to "contacted" — she did contact them.
    """
    enquiry = db.get(BulkEnquiry, enquiry_id)
    if enquiry is None:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    reply = BulkReply(enquiry_id=enquiry.id, message=payload.message)
    db.add(reply)
    if enquiry.status == BulkEnquiryStatus.NEW.value:
        enquiry.status = BulkEnquiryStatus.CONTACTED.value
    db.commit()
    db.refresh(enquiry)

    # Plain values only — the task runs after this session has closed.
    subject, html, text = bulk_reply_email(
        {
            "name": enquiry.name,
            "message": reply.message,
            "event_date": f"{enquiry.event_date:%d %B %Y}",
            "guests": enquiry.guests,
        }
    )
    background.add_task(send_email, enquiry.email, subject, html, text)
    return enquiry


@router.get("/bulk-orders/export")
def export_bulk_enquiries(admin: CurrentAdmin, db: DbSession):
    """CSV of every catering lead — for the ledger or a spreadsheet."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["received", "name", "email", "phone", "event_date", "event_type", "guests", "status", "wants", "note", "admin_note"]
    )
    for e in db.scalars(select(BulkEnquiry).order_by(BulkEnquiry.created_at.desc())):
        writer.writerow(
            [
                e.created_at.strftime("%Y-%m-%d %H:%M"),
                e.name,
                e.email,
                e.phone or "",
                e.event_date.isoformat(),
                e.event_type,
                e.guests,
                e.status,
                (e.items_note or "").replace("\n", " "),
                (e.message or "").replace("\n", " "),
                (e.admin_note or "").replace("\n", " "),
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename=bakehouse-bulk-enquiries.csv"'},
    )


# --------------------------------------------------------------------------
# Testimonials (home page "Word around the neighborhood")
# --------------------------------------------------------------------------

@router.get("/testimonials", response_model=list[TestimonialOut])
def all_testimonials(admin: CurrentAdmin, db: DbSession):
    """Every testimonial, active or shelved, in display order."""
    return list(
        db.scalars(
            select(Testimonial).order_by(Testimonial.sort_order, Testimonial.created_at.desc())
        )
    )


@router.post("/testimonials", response_model=TestimonialOut, status_code=status.HTTP_201_CREATED)
def create_testimonial(payload: TestimonialCreate, admin: CurrentAdmin, db: DbSession):
    """Add a word from a customer — copy one off a product review, or type a
    note someone left at the counter."""
    testimonial = Testimonial(
        name=payload.name.strip(),
        role=(payload.role or None),
        quote=payload.quote.strip(),
        avatar_url=payload.avatar_url or None,
        rating=payload.rating,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(testimonial)
    db.commit()
    db.refresh(testimonial)
    return testimonial


@router.put("/testimonials/{testimonial_id}", response_model=TestimonialOut)
def update_testimonial(
    testimonial_id: int, payload: TestimonialUpdate, admin: CurrentAdmin, db: DbSession
):
    testimonial = db.get(Testimonial, testimonial_id)
    if testimonial is None:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    data = payload.model_dump(exclude_unset=True)
    for field in ("name",):
        if data.get(field):
            data[field] = data[field].strip()
    if "quote" in data and data["quote"]:
        data["quote"] = data["quote"].strip()
    if "avatar_url" in data:
        data["avatar_url"] = data["avatar_url"] or None
    for field, value in data.items():
        setattr(testimonial, field, value)
    db.commit()
    db.refresh(testimonial)
    return testimonial


@router.delete("/testimonials/{testimonial_id}")
def delete_testimonial(testimonial_id: int, admin: CurrentAdmin, db: DbSession):
    testimonial = db.get(Testimonial, testimonial_id)
    if testimonial is None:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    db.delete(testimonial)
    db.commit()
    return {"message": "Testimonial deleted"}


# --------------------------------------------------------------------------
# Coming soon (home page look-ahead band)
# --------------------------------------------------------------------------

@router.get("/coming-soon", response_model=list[ComingSoonOut])
def all_coming_soon(admin: CurrentAdmin, db: DbSession):
    return list(
        db.scalars(
            select(ComingSoonItem).order_by(ComingSoonItem.sort_order, ComingSoonItem.created_at.desc())
        )
    )


@router.post("/coming-soon", response_model=ComingSoonOut, status_code=status.HTTP_201_CREATED)
def create_coming_soon(payload: ComingSoonCreate, admin: CurrentAdmin, db: DbSession):
    item = ComingSoonItem(
        title=payload.title.strip(),
        description=payload.description,
        image_url=payload.image_url or None,
        eta_label=(payload.eta_label or None),
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/coming-soon/{item_id}", response_model=ComingSoonOut)
def update_coming_soon(
    item_id: int, payload: ComingSoonUpdate, admin: CurrentAdmin, db: DbSession
):
    item = db.get(ComingSoonItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Coming-soon item not found")
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"]:
        data["title"] = data["title"].strip()
    if "image_url" in data:
        data["image_url"] = data["image_url"] or None
    if "eta_label" in data:
        data["eta_label"] = data["eta_label"] or None
    for field, value in data.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/coming-soon/{item_id}")
def delete_coming_soon(item_id: int, admin: CurrentAdmin, db: DbSession):
    item = db.get(ComingSoonItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Coming-soon item not found")
    db.delete(item)
    db.commit()
    return {"message": "Coming-soon item deleted"}


# --------------------------------------------------------------------------
# User management — the roll, with loyalty the shop can act on
# --------------------------------------------------------------------------

# The tier definition lives in app/loyalty.py (_loyalty above), shared with
# the delivery rules — a VIP at checkout is a VIP on this roll, by the same
# measure. The spend-based VIP route needs an approved EUR threshold
# (VIP_SPEND_THRESHOLD); the old $150 figure is not an approved EUR value,
# so until the owner sets one, tiers ride on order counts alone.


def _customer_roll(db: DbSession) -> list[AdminCustomerOut]:
    """Every customer with their measured loyalty — the full roll, newest
    members first. One aggregate pass for everyone, never a query per row;
    the page below slices it however it needs.

    Spend is grouped per currency because dollars and euros must never sum
    into one "lifetime" number: the headline lifetime_spend is what the
    customer has spent in the shop's *active* currency, and the full split
    rides in lifetime_spend_by_currency. The loyalty tier judges the active
    currency's spend too — though the spend-based VIP route stays off until
    an EUR threshold is approved (VIP_SPEND_THRESHOLD) — while the order
    count and last visit span everything, being plain facts."""
    active = str(get_settings().stripe_currency or "").strip().lower()
    users = list(db.scalars(select(User).order_by(User.created_at.desc())))
    spend_by_user: dict[int, dict[str, float]] = {}
    facts: dict[int, tuple[int, object]] = {}
    for row in db.execute(
        select(
            Order.user_id.label("user_id"),
            Order.currency.label("currency"),
            func.count(Order.id).label("orders"),
            func.sum(Order.total).label("spend"),
            func.max(Order.created_at).label("last"),
        )
        .where(Order.status != OrderStatus.CANCELLED.value)
        .group_by(Order.user_id, Order.currency)
    ):
        code = str(row.currency or "").strip().lower() or "unknown"
        spend_by_user.setdefault(row.user_id, {})[code] = round(float(row.spend or 0), 2)
        count, last = facts.get(row.user_id, (0, None))
        facts[row.user_id] = (
            count + int(row.orders),
            row.last if last is None else max(last, row.last),
        )

    roll = []
    for user in users:
        customer = AdminCustomerOut.model_validate(user)
        if user.id in spend_by_user:
            split = spend_by_user[user.id]
            count, last = facts[user.id]
            customer = customer.model_copy(
                update={
                    "order_count": count,
                    "lifetime_spend": split.get(active, 0.0),
                    "lifetime_spend_by_currency": dict(sorted(split.items())),
                    "last_order_at": last,
                    "loyalty": _loyalty(count, split.get(active, 0.0), last),
                }
            )
        roll.append(customer)
    return roll


# How the roll can be ordered — heaviest/newest first. The "last" key pairs
# a has-visited flag with the date, so never-ordered customers sink to the
# bottom without ever comparing None to a date.
_USER_SORTS = {
    "spend": lambda c: c.lifetime_spend,
    "orders": lambda c: c.order_count,
    "last": lambda c: (c.last_order_at is not None, c.last_order_at),
    "joined": lambda c: c.created_at,
}


@router.get("/users", response_model=AdminUserPage)
def all_users(
    admin: CurrentAdmin,
    db: DbSession,
    tier: str | None = Query(None, pattern="^(vip|loyal|lapsed|regular)$"),
    classification: str | None = Query(
        None, pattern="^(vip|loyal|lapsed|regular|none)$"
    ),
    sort: str = Query("spend", pattern="^(spend|orders|last|joined)$"),
    search: str | None = Query(None, min_length=1, max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=5, le=50),
):
    """The regulars' roll, one shelf at a time: tier- and
    classification-filtered, searched, ordered and paginated — with the
    whole roll's counts alongside, so the chips count loyal hands from any
    page and any search. `tier` filters the measured loyalty; `classification`
    filters the desk's manual word (none = customers without one)."""
    roll = _customer_roll(db)
    tier_counts = Counter(c.loyalty for c in roll)
    classification_counts = Counter(
        c.classification for c in roll if c.classification is not None
    )
    if tier:
        roll = [c for c in roll if c.loyalty == tier]
    if classification:
        if classification == "none":
            roll = [c for c in roll if c.classification is None]
        else:
            roll = [c for c in roll if c.classification == classification]
    if search:
        # Matched against name, email or phone — the things the baker
        # actually knows about a regular. Numbers compare digit-for-digit,
        # so "555-0142" finds however the number was typed or stored.
        needle = search.strip().lower()
        needle_digits = "".join(ch for ch in needle if ch.isdigit())
        roll = [
            c
            for c in roll
            if needle in c.email.lower()
            or needle in f"{c.first_name} {c.last_name}".lower()
            or (needle_digits and needle_digits in (c.phone or ""))
        ]
    roll.sort(key=_USER_SORTS.get(sort, _USER_SORTS["spend"]), reverse=True)
    total = len(roll)
    start = (page - 1) * per_page
    return AdminUserPage(
        items=roll[start : start + per_page],
        tier_counts=dict(tier_counts),
        classification_counts=dict(classification_counts),
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserAdminUpdateRequest, admin: CurrentAdmin, db: DbSession):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and payload.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    # The shop must never end up with zero active admins — that locks
    # everyone out of the desk, with no way to promote anyone back.
    if user.role == UserRole.ADMIN.value and (
        (payload.role is not None and payload.role != UserRole.ADMIN) or payload.is_active is False
    ):
        active_admins = db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.ADMIN.value, User.is_active.is_(True)
            )
        )
        if active_admins <= 1:
            raise HTTPException(
                status_code=400,
                detail="That would leave the shop with no active admin. "
                "Promote another admin first, then make this change.",
            )

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.role is not None:
        user.role = payload.role.value
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}/classification", response_model=UserOut)
def set_classification(
    user_id: int, payload: ClassificationUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    """Set, change or clear the desk's manual classification for one
    customer. Strictly separate from the measured loyalty tier — this write
    never touches order data and nothing recalculate: it is the baker's own
    word, and every change lands in the classification ledger."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    previous = user.classification
    value = payload.classification  # None clears
    if previous == value:
        # An identical write is a no-op — the ledger records changes, not
        # echoes, so the history stays meaningful.
        return user
    user.classification = value
    db.add(
        ClassificationEvent(
            user_id=user.id,
            admin_user_id=admin.id,
            admin_name=admin.full_name,
            from_classification=previous,
            to_classification=value,
        )
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("/users/{user_id}/classification-history", response_model=list[ClassificationEventOut])
def classification_history(user_id: int, admin: CurrentAdmin, db: DbSession):
    """The classification ledger for one customer, newest first: who set
    what, from what, and when."""
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return list(
        db.scalars(
            select(ClassificationEvent)
            .where(ClassificationEvent.user_id == user_id)
            .order_by(ClassificationEvent.created_at.desc(), ClassificationEvent.id.desc())
        )
    )
