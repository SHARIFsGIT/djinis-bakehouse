"""Admin-only endpoints: dashboard stats, product CRUD, orders, coupons and users."""

import csv
import io
import re
import uuid

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, update

from ..models import (
    DEFAULT_ANNOUNCEMENT,
    DEFAULT_HERO_IMAGE,
    DEFAULT_MARQUEE,
    DEFAULT_TODAY_NOTE,
    AdminNotification,
    BulkEnquiry,
    BulkEnquiryStatus,
    Category,
    ComingSoonItem,
    Coupon,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    Product,
    Review,
    Testimonial,
    User,
    UserRole,
    get_store_setting,
)
from ..schemas import (
    AdminNotificationList,
    AdminNotificationOut,
    AdminReviewCounts,
    AdminReviewList,
    AdminReviewOut,
    AdminStatsOut,
    BulkEnquiryOut,
    BulkEnquiryUpdate,
    CategoryCreateRequest,
    CategoryOut,
    ComingSoonCreate,
    ComingSoonOut,
    ComingSoonUpdate,
    CouponCreateRequest,
    CouponOut,
    CouponUpdateRequest,
    OrderDetailOut,
    OrderListResponse,
    OrderStatusUpdateRequest,
    PaginationOut,
    PaymentStatusUpdateRequest,
    ProductCard,
    ProductCreateRequest,
    ProductDetail,
    ProductListResponse,
    ProductUpdateRequest,
    ReviewModerationRequest,
    StoreSettingsOut,
    StoreSettingsUpdate,
    TestimonialCreate,
    TestimonialOut,
    TestimonialUpdate,
    UserAdminUpdateRequest,
    UserOut,
)
from ..security import CurrentAdmin, DbSession
from ..serialize import product_to_card, review_summary, verified_buyers
from ..uploads import MAX_PRODUCT_PHOTO_BYTES, save_photo
from .orders import _cancel_order, _late_cancel_fee, _serialize

router = APIRouter(prefix="/api/admin", tags=["admin"])


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

    revenue = sum(float(o.total) for o in orders)
    paid = sum(
        float(o.total)
        for o in orders
        if o.payment_status in (PaymentStatus.PAID.value, PaymentStatus.REFUNDED.value)
    )

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

    return AdminStatsOut(
        total_revenue=round(revenue, 2),
        paid_revenue=round(paid, 2),
        order_count=len(orders),
        customer_count=customers or 0,
        avg_order_value=round(revenue / len(orders), 2) if orders else 0.0,
        pending_orders=by_status.get(OrderStatus.PENDING.value, 0),
        orders_by_status=by_status,
        top_products=[{"name": name, "quantity": int(qty)} for name, qty in top_rows],
        recent_orders=[_serialize(o) for o in recent],
        low_stock=[product_to_card(p, low_ratings) for p in low_stock_products],
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
    order_id: int, payload: OrderStatusUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # The status dial is the console's cancel path — run the full
    # cancellation (stock back, refund settled), free of the late fee.
    if payload.status == OrderStatus.CANCELLED:
        if order.status == OrderStatus.CANCELLED.value:
            raise HTTPException(status_code=400, detail="Order is already cancelled")
        _cancel_order(order, db, by_customer=False, comment=payload.comment)
        db.commit()
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
         "payment_status", "coupon_code", "subtotal", "discount", "tax", "shipping", "total"]
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

@router.get("/products", response_model=ProductListResponse)
def all_products(
    admin: CurrentAdmin,
    db: DbSession,
    search: str | None = None,
    category_id: int | None = Query(default=None),
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

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    products = db.scalars(
        query.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()
    ratings = review_summary(db, [p.id for p in products])

    return ProductListResponse(
        products=[product_to_card(p, ratings) for p in products],
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.get("/products/{product_id}", response_model=ProductDetail)
def admin_product_detail(product_id: int, admin: CurrentAdmin, db: DbSession):
    """Full product payload for the admin edit form (works for hidden products too)."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductDetail(
        **product_to_card(product).model_dump(),
        description=product.description,
        prep_time_minutes=product.prep_time_minutes,
        ingredients=product.ingredients,
        allergens=product.allergens,
        created_at=product.created_at,
    )


@router.post("/products", response_model=ProductCard, status_code=status.HTTP_201_CREATED)
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
    return product_to_card(product)


@router.put("/products/{product_id}", response_model=ProductCard)
def update_product(
    product_id: int, payload: ProductUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    data = payload.model_dump()
    data["name"] = data["name"].strip()
    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product_to_card(product)


@router.delete("/products/{product_id}")
def delete_product(product_id: int, admin: CurrentAdmin, db: DbSession):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
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
    return list(db.scalars(select(Coupon).order_by(Coupon.created_at.desc())))


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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(coupon, field, value)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.delete("/coupons/{coupon_id}")
def delete_coupon(coupon_id: int, admin: CurrentAdmin, db: DbSession):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    db.delete(coupon)
    db.commit()
    return {"message": "Coupon deleted"}


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
# User management
# --------------------------------------------------------------------------

@router.get("/users", response_model=list[UserOut])
def all_users(admin: CurrentAdmin, db: DbSession):
    return list(db.scalars(select(User).order_by(User.created_at.desc())))


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
