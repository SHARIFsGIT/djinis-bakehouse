"""Customer orders: checkout, listing, detail and cancellation.

The cart lives client-side; the server re-validates every line item
(price, availability, stock) at checkout so tampering is impossible.
Card orders are placed unpaid and only become paid when Stripe says so
(webhook or confirm check — see routers/payments.py).
"""

import random
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import func, select, update

from .. import stripe_gateway
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


def _coupon_used_by(db, code: str, user_id: int) -> bool:
    """A once-per-customer code counts as used while the customer has any
    standing order that carried it — cancelling that order hands the code
    back."""
    return (
        db.scalar(
            select(Order.id)
            .where(
                Order.user_id == user_id,
                Order.coupon_code == code,
                Order.status != OrderStatus.CANCELLED.value,
            )
            .limit(1)
        )
        is not None
    )


def _load_coupon(db, code: str, user: User | None = None) -> Coupon:
    coupon = db.scalar(select(Coupon).where(Coupon.code == code.strip().upper()))
    if coupon is None or not coupon.is_active:
        raise HTTPException(status_code=404, detail="This coupon code is not valid")
    if coupon.once_per_customer and user is not None and _coupon_used_by(db, coupon.code, user.id):
        raise HTTPException(
            status_code=404,
            detail=f"{coupon.code} is a one-per-customer code and has already been used",
        )
    return coupon


def _late_cancel_fee(order: Order, db) -> float:
    """What cancelling this order costs right now (0 unless it's baking)."""
    if order.status != OrderStatus.BAKING.value or not order.can_be_cancelled:
        return 0.0
    percent = float(get_store_setting(db).late_cancel_fee_percent)
    return round(float(order.total) * percent / 100, 2)


def _serialize(
    order: Order, detailed: bool = False, cancel_fee_preview: float = 0.0, payment_url: str | None = None
) -> OrderOut | OrderDetailOut:
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
        payment_url=payment_url,
    )


def _receipt_dict(order: Order) -> dict:
    """The plain-values receipt payload. Background tasks run after the
    request's session has closed, so they must never reach back into the
    ORM for more — everything they need travels in this dict."""
    return {
        "email": order.user.email,
        "first_name": order.user.first_name,
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


def mark_order_paid(
    order: Order, db, background: BackgroundTasks, *, payment_intent: str | None = None
) -> bool:
    """A completed Stripe payment landed on this order.

    Idempotent — it only acts while the order is still pending, so the
    webhook and the customer-facing confirm check can race safely and the
    receipt goes out exactly once.
    """
    if order.payment_status != PaymentStatus.PENDING.value:
        return False
    order.payment_status = PaymentStatus.PAID.value
    if payment_intent:
        order.stripe_payment_intent_id = payment_intent
    db.add(
        OrderEvent(
            order_id=order.id, status=order.status, comment="Payment received — paid by card"
        )
    )
    db.commit()
    db.refresh(order)
    # The card receipt travels with the payment, not the placement — by
    # now it can honestly say "paid".
    background.add_task(send_order_receipt, _receipt_dict(order))
    return True


@router.post("", response_model=OrderDetailOut, status_code=status.HTTP_201_CREATED)
def checkout(
    payload: CheckoutRequest,
    user: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
    request: Request,
):
    # Cash on delivery is always real. Card orders run through Stripe —
    # until STRIPE_SECRET_KEY is configured there is no honest card option
    # to offer, so the request is refused before any side effects.
    if payload.payment_method == PaymentMethod.CARD and not stripe_gateway.configured():
        raise HTTPException(
            status_code=400,
            detail="Online payments aren't set up yet — please choose cash on delivery",
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

    coupon = (
        _load_coupon(db, payload.coupon_code, user) if payload.coupon_code else None
    )
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

    # Both rails start pending: cash settles at the door, card settles when
    # Stripe confirms the payment.
    payment_status = PaymentStatus.PENDING.value

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
        # Atomic decrement: two shoppers reaching the last box at once must
        # not both take it — the availability check above is advisory under
        # concurrency, this is the one that counts.
        result = db.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock >= quantity)
            .values(stock=Product.stock - quantity)
        )
        if result.rowcount == 0:
            # The uncommitted order and address roll back with this raise
            raise HTTPException(
                status_code=400,
                detail=f"Only {product.stock} left of {product.name}",
            )
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
        background=background,
    )
    db.commit()
    db.refresh(order)

    # Card orders now need a hosted Stripe page to pay at. If Stripe can't
    # be reached, the order is cancelled on the spot — no order may sit in
    # the book with no way to pay it.
    payment_url = None
    if payload.payment_method == PaymentMethod.CARD:
        try:
            session = stripe_gateway.create_checkout_session(
                order, str(request.base_url).rstrip("/")
            )
        except stripe_gateway.GatewayUnavailable:
            _cancel_order(
                order,
                db,
                by_customer=False,
                comment="Payment provider unreachable — cancelled automatically",
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="The payment provider didn't answer — nothing was charged. Please try again.",
            )
        order.stripe_session_id = session["id"]
        db.commit()
        payment_url = session["url"]

    # The receipt every order earns as proof of purchase — cash orders get
    # theirs at placement (the bill to settle); card orders get theirs when
    # the payment lands, when it can honestly say "paid".
    if payload.payment_method != PaymentMethod.CARD:
        background.add_task(send_order_receipt, _receipt_dict(order))

    return _serialize(order, detailed=True, payment_url=payment_url)


@router.get("/coupons/{code}", response_model=CouponPreviewOut)
def check_coupon(code: str, user: CurrentUser, db: DbSession):
    """Preview a coupon while the customer is at the checkout screen."""
    coupon = _load_coupon(db, code, user)
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


def _refund_paid_order(order: Order, fee: float) -> str | None:
    """Settle the money side of cancelling a paid order. Returns a note for
    the timeline when the settlement needs a human's eye, else None.

    Card refunds go back to the card through Stripe (the total less any
    kept fee); cash refunds are handed back in person or by transfer, so
    only the status flips. A card order with no payment intent is demo
    history from the old simulated branch — no money ever moved.
    """
    if order.payment_status != PaymentStatus.PAID.value:
        return None  # nothing collected, nothing owed
    refunded_status = (
        PaymentStatus.PARTIALLY_REFUNDED.value if fee else PaymentStatus.REFUNDED.value
    )
    if order.payment_method == PaymentMethod.CARD.value and order.stripe_payment_intent_id:
        try:
            stripe_gateway.refund(
                order.stripe_payment_intent_id,
                stripe_gateway.cents(float(order.total)) - stripe_gateway.cents(fee),
            )
        except stripe_gateway.GatewayUnavailable:
            # The cancellation stands; the money doesn't. Say so on the
            # timeline rather than promising a refund that hasn't happened.
            return "refund could not be sent — retry it from the Stripe dashboard"
    order.payment_status = refunded_status
    return None


def _cancel_order(order: Order, db, *, by_customer: bool, comment: str | None = None) -> None:
    """One cancellation path for every caller: flip the status, settle the
    money, put the stock back and log the event.

    Once the ovens have started the shop keeps a slice of the total — the
    percent is the admin's to tune (Settings, back of house). Bakery-side
    cancellations are always free.
    """
    fee = _late_cancel_fee(order, db) if by_customer else 0.0

    order.status = OrderStatus.CANCELLED.value
    order.cancel_fee = fee
    refund_note = _refund_paid_order(order, fee)

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
    if refund_note:
        text += f" — {refund_note}"

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
