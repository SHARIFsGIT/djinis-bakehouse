"""Customer orders: checkout, listing, detail and cancellation.

The cart lives client-side; the server re-validates every line item
(price, availability, stock) at checkout so tampering is impossible.
"""

import random
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import func, select

from ..config import get_settings
from ..email import send_order_receipt
from ..notifications import notify_admin
from ..models import (
    Address,
    Coupon,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    Product,
    User,
    get_store_setting,
)
from ..schemas import (
    CheckoutRequest,
    CouponPreviewOut,
    OrderDetailOut,
    OrderListResponse,
    OrderOut,
    PaginationOut,
)
from ..security import CurrentUser, DbSession

router = APIRouter(prefix="/api/orders", tags=["orders"])
settings = get_settings()


def _generate_order_number(db) -> str:
    today = date.today().strftime("%Y%m%d")
    while True:
        number = f"DJ-{today}-{random.randint(1000, 9999)}"
        if not db.scalar(select(Order).where(Order.order_number == number)):
            return number


def _load_coupon(db, code: str) -> Coupon:
    coupon = db.scalar(select(Coupon).where(Coupon.code == code.strip().upper()))
    if coupon is None or not coupon.is_active:
        raise HTTPException(status_code=404, detail="This coupon code is not valid")
    return coupon


def _late_cancel_fee(order: Order, db) -> float:
    """What cancelling this order costs right now (0 unless it's baking)."""
    if order.status != OrderStatus.BAKING.value or not order.can_be_cancelled:
        return 0.0
    percent = float(get_store_setting(db).late_cancel_fee_percent)
    return round(float(order.total) * percent / 100, 2)


def _serialize(order: Order, detailed: bool = False, cancel_fee_preview: float = 0.0) -> OrderOut | OrderDetailOut:
    data = OrderOut(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        payment_method=order.payment_method,
        payment_status=order.payment_status,
        subtotal=float(order.subtotal),
        discount_amount=float(order.discount_amount or 0),
        coupon_code=order.coupon_code,
        tax=float(order.tax),
        shipping_fee=float(order.shipping_fee),
        total=float(order.total),
        cancel_fee=float(order.cancel_fee or 0),
        delivery_date=order.delivery_date,
        note=order.note,
        recipient=order.recipient,
        phone=order.phone,
        line1=order.line1,
        line2=order.line2,
        city=order.city,
        state=order.state,
        postal_code=order.postal_code,
        country=order.country,
        created_at=order.created_at,
        can_be_cancelled=order.can_be_cancelled,
        item_count=sum(i.quantity for i in order.items),
    )
    if not detailed:
        return data
    return OrderDetailOut(
        **data.model_dump(),
        items=[
            {
                "id": i.id,
                "product_id": i.product_id,
                "product_name": i.product_name,
                "product_slug": i.product_slug,
                "unit_price": float(i.unit_price),
                "quantity": i.quantity,
                "subtotal": float(i.subtotal),
            }
            for i in order.items
        ],
        timeline=[e for e in order.timeline],
        customer_name=order.user.full_name if order.user else None,
        customer_email=order.user.email if order.user else None,
        cancellation_fee=round(cancel_fee_preview, 2),
    )


@router.post("", response_model=OrderDetailOut, status_code=status.HTTP_201_CREATED)
def checkout(payload: CheckoutRequest, user: CurrentUser, db: DbSession, background: BackgroundTasks):
    # Cash on delivery is the only real payment rail. The simulated card
    # branch marks an order paid with no money moving, so it stays behind
    # CARD_PAYMENTS_ENABLED (dev yes, production no) until a gateway lands.
    if payload.payment_method == PaymentMethod.CARD and not settings.card_payments_enabled:
        raise HTTPException(
            status_code=400,
            detail="Card payments aren't available yet — please choose cash on delivery",
        )

    # Resolve delivery address (existing id or a new one created on the fly)
    if payload.address_id:
        address = db.scalar(
            select(Address).where(Address.id == payload.address_id, Address.user_id == user.id)
        )
        if address is None:
            raise HTTPException(status_code=400, detail="Invalid address_id")
    else:
        data = payload.new_address.model_dump()
        has_addresses = db.scalars(select(Address.id).where(Address.user_id == user.id)).first()
        if data["is_default"] and has_addresses:
            # Same rule as the address book: one default at a time.
            for other in db.scalars(
                select(Address).where(Address.user_id == user.id, Address.is_default.is_(True))
            ):
                other.is_default = False
        # A first address becomes the default automatically
        data["is_default"] = data["is_default"] or not has_addresses
        address = Address(user_id=user.id, **data)
        db.add(address)
        db.flush()

    # Validate items against the catalog — the server is the source of truth
    merged: dict[int, int] = {}
    for item in payload.items:
        merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity

    products = db.scalars(
        select(Product).where(Product.id.in_(merged.keys()), Product.is_available.is_(True))
    ).all()
    by_id = {p.id: p for p in products}

    for product_id, quantity in merged.items():
        product = by_id.get(product_id)
        if product is None:
            raise HTTPException(status_code=400, detail=f"Product {product_id} is not available")
        if product.stock < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only {product.stock} left of {product.name}",
            )

    if payload.delivery_date and payload.delivery_date < date.today():
        raise HTTPException(status_code=400, detail="Delivery date cannot be in the past")
    if payload.delivery_date and payload.delivery_date > date.today() + timedelta(days=30):
        raise HTTPException(status_code=400, detail="Delivery date is too far ahead")

    subtotal = round(sum(by_id[pid].current_price * qty for pid, qty in merged.items()), 2)

    coupon = _load_coupon(db, payload.coupon_code) if payload.coupon_code else None
    if coupon and coupon.min_subtotal and subtotal < float(coupon.min_subtotal):
        raise HTTPException(
            status_code=400,
            detail=f"{coupon.code} needs a basket of at least ${float(coupon.min_subtotal):.2f}",
        )
    discount = round(subtotal * coupon.percent_off / 100, 2) if coupon else 0.0
    taxable = round(subtotal - discount, 2)
    tax = round(taxable * settings.tax_rate, 2)
    shipping = 0.0 if subtotal >= settings.free_shipping_threshold else settings.shipping_fee
    total = round(taxable + tax + shipping, 2)

    payment_status = (
        PaymentStatus.PAID.value
        if payload.payment_method == PaymentMethod.CARD
        else PaymentStatus.PENDING.value
    )

    order = Order(
        order_number=_generate_order_number(db),
        user_id=user.id,
        status=OrderStatus.PENDING.value,
        payment_method=payload.payment_method.value,
        payment_status=payment_status,
        subtotal=round(subtotal, 2),
        discount_amount=discount,
        coupon_code=coupon.code if coupon else None,
        tax=tax,
        shipping_fee=shipping,
        total=total,
        delivery_date=payload.delivery_date,
        note=payload.note,
        recipient=address.recipient,
        phone=address.phone,
        line1=address.line1,
        line2=address.line2,
        city=address.city,
        state=address.state,
        postal_code=address.postal_code,
        country=address.country,
    )
    db.add(order)
    db.flush()

    for product_id, quantity in merged.items():
        product = by_id[product_id]
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                product_slug=product.slug,
                unit_price=product.current_price,
                quantity=quantity,
                subtotal=round(product.current_price * quantity, 2),
            )
        )
        product.stock -= quantity

    db.add(OrderEvent(order_id=order.id, status=OrderStatus.PENDING.value, comment="Order placed"))
    # Same transaction as the order itself — the bell can't announce a sale
    # that didn't happen.
    notify_admin(
        db,
        kind="order",
        title=f"New order {order.order_number} — ${total:.2f}",
        body=f"{user.full_name} · {sum(merged.values())} items · "
        f"{payload.payment_method.value.replace('_', ' ')}",
        link=f"/admin/orders?search={order.order_number}",
    )
    db.commit()
    db.refresh(order)

    # The receipt every order earns as proof of purchase. Plain values only —
    # the background task runs after this request's session has closed, so it
    # must never reach back into the ORM for more.
    receipt = {
        "email": user.email,
        "first_name": user.first_name,
        "order_number": order.order_number,
        "placed_at": order.created_at.strftime("%b %d, %Y, %I:%M %p"),
        "items": [
            {"name": i.product_name, "quantity": i.quantity, "subtotal": float(i.subtotal)}
            for i in order.items
        ],
        "subtotal": float(order.subtotal),
        "discount": float(order.discount_amount),
        "coupon_code": order.coupon_code,
        "tax": float(order.tax),
        "shipping": float(order.shipping_fee),
        "total": float(order.total),
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
    }
    background.add_task(send_order_receipt, receipt)

    return _serialize(order, detailed=True)


@router.get("/coupons/{code}", response_model=CouponPreviewOut)
def check_coupon(code: str, user: CurrentUser, db: DbSession):
    """Preview a coupon while the customer is at the checkout screen."""
    coupon = _load_coupon(db, code)
    return CouponPreviewOut(
        code=coupon.code,
        percent_off=coupon.percent_off,
        description=coupon.description,
        min_subtotal=float(coupon.min_subtotal) if coupon.min_subtotal else None,
    )


@router.get("", response_model=OrderListResponse)
def my_orders(
    user: CurrentUser,
    db: DbSession,
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=50),
):
    query = select(Order).where(Order.user_id == user.id)
    if status_filter:
        query = query.where(Order.status == status_filter.value)

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


def _get_order_for(order_id: int, user: User, db) -> Order:
    order = db.get(Order, order_id)
    if order is None or (order.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/{order_id}", response_model=OrderDetailOut)
def order_detail(order_id: int, user: CurrentUser, db: DbSession):
    order = _get_order_for(order_id, user, db)
    return _serialize(order, detailed=True, cancel_fee_preview=_late_cancel_fee(order, db))


def _cancel_order(order: Order, db, *, by_customer: bool, comment: str | None = None) -> None:
    """One cancellation path for every caller: flip the status, settle the
    refund, put the stock back and log the event.

    Once the ovens have started the shop keeps a slice of the total — the
    percent is the admin's to tune (Settings, back of house). Bakery-side
    cancellations are always free.
    """
    fee = _late_cancel_fee(order, db) if by_customer else 0.0

    order.status = OrderStatus.CANCELLED.value
    order.cancel_fee = fee
    if order.payment_status == PaymentStatus.PAID.value:
        order.payment_status = (
            PaymentStatus.PARTIALLY_REFUNDED.value if fee else PaymentStatus.REFUNDED.value
        )

    # Return reserved stock
    for item in order.items:
        if item.product_id:
            product = db.get(Product, item.product_id)
            if product:
                product.stock += item.quantity

    text = comment if comment is not None else (
        "Cancelled by customer" if by_customer else "Cancelled by bakery"
    )
    if fee:
        text += f" — late-cancellation fee of ${fee:.2f} kept (baking had started)"

    db.add(OrderEvent(order_id=order.id, status=OrderStatus.CANCELLED.value, comment=text))


@router.post("/{order_id}/cancel", response_model=OrderDetailOut)
def cancel_order(order_id: int, user: CurrentUser, db: DbSession):
    order = _get_order_for(order_id, user, db)
    if not order.can_be_cancelled:
        raise HTTPException(
            status_code=400,
            detail="This order can no longer be cancelled — contact the bakery",
        )
    _cancel_order(order, db, by_customer=order.user_id == user.id)
    db.commit()
    db.refresh(order)
    return _serialize(order, detailed=True)
