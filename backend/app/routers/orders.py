"""Customer orders: checkout, listing, detail and cancellation.

The cart lives client-side; the server re-validates every line item
(price, availability, stock) at checkout so tampering is impossible.
Card orders are placed unpaid and only become paid when Stripe says so
(webhook or confirm check — see routers/payments.py).
"""

import random
import re
from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from .. import delivery, ledger, payment_ops, pricing, shoptime, stripe_gateway
from ..config import get_settings
from ..database import utcnow
from ..email import send_order_receipt, send_order_status_email
from ..money import money
from ..loyalty import loyalty_tier
from ..notifications import notify_admin
from ..models import (
    PAYMENT_PROVIDERS,
    Address,
    CheckoutAttempt,
    Coupon,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    OrderTaxLine,
    PaymentMethod,
    PaymentStatus,
    Product,
    TransactionStatus,
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
    QuoteOut,
    QuoteRequest,
)
from ..security import CurrentUser, DbSession, OptionalUser, account_brake, client_key
from ..serialize import find_matching_address

router = APIRouter(prefix="/api/orders", tags=["orders"])
settings = get_settings()


def _generate_order_number(db) -> str:
    today = shoptime.shop_today().strftime("%Y%m%d")
    while True:
        number = f"DJ-{today}-{random.randint(1000, 9999)}"
        if not db.scalar(select(Order).where(Order.order_number == number)):
            return number


def _charge_currency() -> str:
    """The currency new orders are placed in — the configured charge
    currency, validated and normalized before anything is written. An
    unusable setting (typo, unset, a currency the payment pipeline doesn't
    speak) pauses checkout entirely, cash included: an order stamped with a
    currency nothing can charge or report would poison the book, and every
    fail-closed door downstream refuses to guess one."""
    code = str(settings.stripe_currency or "").strip().lower()
    if code not in stripe_gateway.SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=503,
            detail="The shop's payment currency isn't configured correctly — "
            "checkout is paused. Please try again soon.",
        )
    return code


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


def _load_coupon(db, code: str, user: User | None = None, *, lock: bool = False) -> Coupon:
    """`lock` is placement's alone: the coupon row is held FOR UPDATE until
    the order commits, so two checkouts racing on one code queue up here
    and the second one counts the first one's order — a one-per-customer
    or capped voucher cannot be spent twice at once (SQLite ignores FOR
    UPDATE; Postgres doesn't). Previews and quotes never lock."""
    query = select(Coupon).where(Coupon.code == code.strip().upper())
    if lock:
        query = query.with_for_update()
    coupon = db.scalar(query)
    if coupon is None or not coupon.is_active:
        raise HTTPException(status_code=404, detail="This coupon code is not valid")
    if coupon.once_per_customer and user is not None and _coupon_used_by(db, coupon.code, user.id):
        raise HTTPException(
            status_code=404,
            detail=f"{coupon.code} is a one-per-customer code and has already been used",
        )
    # A voucher minted for one customer works for that account alone — for
    # anyone else (or a guest) it simply does not exist. Preview, quote and
    # placement all funnel through here, so one gate covers all.
    if coupon.restricted_to_user_id is not None and (
        user is None or user.id != coupon.restricted_to_user_id
    ):
        raise HTTPException(status_code=404, detail="This coupon code is not valid")
    # Personalised vouchers expire; an expired code is spent, whoever asks.
    if coupon.expires_at is not None and coupon.expires_at <= utcnow():
        raise HTTPException(
            status_code=404,
            detail=f"{coupon.code} has expired and is no longer valid",
        )
    # A total-redemption cap across all customers (None = unlimited).
    if coupon.max_redemptions is not None:
        redeemed = (
            db.scalar(
                select(func.count(Order.id)).where(
                    Order.coupon_code == coupon.code,
                    Order.status != OrderStatus.CANCELLED.value,
                )
            )
            or 0
        )
        if redeemed >= coupon.max_redemptions:
            raise HTTPException(
                status_code=404,
                detail=f"{coupon.code} has been fully redeemed",
            )
    # A minimum recorded before the 2026-11-05 EUR cutover is a USD numeral
    # the app cannot re-read as euros, so it must not be spent (nor nudged
    # toward) until an administrator re-saves the coupon from the Promotions
    # desk — that save re-stamps the approval. Every coupon path (preview,
    # quote, placement) funnels through here, so one gate covers all.
    if coupon.min_subtotal and not coupon.min_subtotal_approved:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{coupon.code}'s minimum spend is being reviewed — "
                "please try again soon"
            ),
        )
    return coupon


def _line_refused(product_id: int, detail: str, available: int) -> HTTPException:
    """A basket line the shelf can't honour (400). The words are for the
    customer; the two headers name the line and what is left of it, so the
    basket page can point at the row and offer the fix — remove it, or take
    what is left — instead of leaving the shopper to work out which bake
    was meant."""
    return HTTPException(
        status_code=400,
        detail=detail,
        headers={
            "X-Basket-Product": str(product_id),
            "X-Basket-Available": str(max(int(available or 0), 0)),
        },
    )


def _short_shelf(product_id: int, name: str, left: int | None) -> HTTPException:
    """More was asked for than the shelf holds."""
    left = max(int(left or 0), 0)
    detail = f"Only {left} left of {name}" if left else f"{name} is sold out"
    return _line_refused(product_id, detail, left)


def _resolve_basket(db, items) -> tuple[dict[int, int], dict[int, Product]]:
    """Validate a client basket against the catalog and hand back the merged
    quantities plus the products that carry them. Checkout and the basket
    quote ask the exact same questions, so a quote can never promise what
    placement would refuse: same availability rule, same stock message."""
    merged: dict[int, int] = {}
    for item in items:
        merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity

    products = db.scalars(
        select(Product).where(Product.id.in_(merged.keys()), Product.is_available.is_(True))
    ).all()
    by_id = {p.id: p for p in products}

    for product_id, quantity in merged.items():
        product = by_id.get(product_id)
        if product is None:
            # A bake the desk took off the shelf reads by its name; an id
            # the catalog has never heard of can only be named by number.
            name = db.scalar(select(Product.name).where(Product.id == product_id))
            detail = (
                f"{name} is not available right now"
                if name
                else f"Product {product_id} is not available"
            )
            raise _line_refused(product_id, detail, 0)
        if product.stock < quantity:
            raise _short_shelf(product_id, product.name, product.stock)
    return merged, by_id


def _basket_coupon(
    db, code: str | None, user: User | None, subtotal: float, *, lock: bool = False
) -> Coupon | None:
    """Load a coupon the way a basket spends one: valid (and, for a signed-in
    shopper, not one-per-customer-spent) and unlocked by this subtotal."""
    if not code:
        return None
    coupon = _load_coupon(db, code, user, lock=lock)
    if coupon.min_subtotal and subtotal < float(coupon.min_subtotal):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{coupon.code} needs a basket of at least "
                f"{money(float(coupon.min_subtotal), settings.stripe_currency)}"
            ),
        )
    return coupon


def _approved_money_rules() -> tuple[float | None, float, float]:
    """The currency-bound money rules a basket may be charged by, or a 503.

    Delivery pricing (SHIPPING_FEE, FREE_SHIPPING_THRESHOLD) carries the
    owner's approved EUR figures since 2026-11-06 (€2.99 / €25.00, set in
    the environment); while either is unset — a fresh deployment, a
    half-finished config — no basket can be priced or placed: checkout
    fails closed rather than charge unapproved figures. TAX_RATE is the
    approved exclusive-mode register rate (7 %) — unset is simply no tax
    line, it pauses nothing by itself, and nothing labels it as VAT.
    """
    if settings.shipping_fee is None or settings.free_shipping_threshold is None:
        raise HTTPException(
            status_code=503,
            detail="Checkout is paused — the shop's delivery pricing is being "
            "finalised. Please try again soon.",
        )
    tax_rate = settings.tax_rate if settings.tax_rate is not None else 0.0
    return tax_rate, float(settings.shipping_fee), float(settings.free_shipping_threshold)


def _delivery_threshold(db, user: User | None) -> float:
    """The free-delivery threshold that applies to *this* shopper.

    Everyone earns free delivery from the standard threshold; a VIP regular
    (app/loyalty.py's definition — ten kept orders and a visit inside the
    window) earns it from the smaller VIP threshold instead, when one is
    configured. The tier is measured server-side from the order book, never
    taken from the request.
    """
    _tax_rate, _shipping_fee, standard = _approved_money_rules()
    vip_threshold = settings.vip_free_delivery_threshold
    if user is None or vip_threshold is None:
        return standard
    count, last = db.execute(
        select(func.count(Order.id), func.max(Order.created_at)).where(
            Order.user_id == user.id, Order.status != OrderStatus.CANCELLED.value
        )
    ).one()
    if loyalty_tier(int(count or 0), 0.0, last) == "vip":
        return float(vip_threshold)
    return standard


def _price_basket(db, merged: dict[int, int], by_id: dict, coupon: Coupon | None, user: User | None):
    """Price a validated basket through the one shared service (app/pricing)
    — the mode's own recipe runs, and both the quote and placement get the
    same figures from the same call. Inclusive mode raises
    ProductNotVatReady (mapped to a 503 naming the bake) for the first
    product without an approved classification."""
    tax_rate, shipping_fee, _standard = _approved_money_rules()
    free_threshold = _delivery_threshold(db, user)
    basket = {by_id[pid]: qty for pid, qty in merged.items()}
    try:
        return pricing.price_basket(
            db, basket, coupon, free_threshold, tax_rate=tax_rate, shipping_fee=shipping_fee
        )
    except pricing.ProductNotVatReady as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Checkout is paused — {exc.product_name} has no approved VAT class yet. "
                "Please try again soon."
            ),
        ) from None


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
        currency=order.currency,
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
    # The per-rate VAT ledger on the order is the inclusive mode's mark:
    # this order's figures carry their VAT inside.
    data.tax_included = bool(order.tax_lines)
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
        **_payment_overview(order),
    )


def _payment_overview(order: Order) -> dict:
    """Which provider took this order's money and how much of it has gone
    back — read fresh from the ledger, so the page a customer (or the
    desk) sees right after a refund already says so."""
    provider = ledger.provider_for(order)
    db = object_session(order)
    if provider is None or db is None:
        return {
            "payment_provider": provider,
            "refunded_amount": 0.0,
            "payment_state": None,
            "payment_label": None,
        }
    tx = ledger.money_transaction(db, order)
    story = tx or ledger.story_transaction(db, order)
    return {
        "payment_provider": provider,
        "refunded_amount": float(tx.refunded_amount or 0) if tx is not None else 0.0,
        "payment_state": story.status if story is not None else None,
        "payment_label": payment_ops.method_label(tx.method_type) if tx is not None else None,
    }


def _receipt_dict(order: Order) -> dict:
    """The plain-values receipt payload. Background tasks run after the
    request's session has closed, so they must never reach back into the
    ORM for more — everything they need travels in this dict."""
    return {
        "email": order.user.email,
        "first_name": order.user.first_name,
        "order_number": order.order_number,
        "placed_at": shoptime.stamp(order.created_at),
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
        # What was actually used on the provider's page, when it said —
        # "Klarna", "Card (Apple Pay)" — so the receipt never guesses.
        "payment_label": _payment_overview(order)["payment_label"],
        "payment_status": order.payment_status,
        "currency": order.currency,
        # The per-rate ledger on the order marks VAT-inclusive figures —
        # the receipt then says "included" instead of adding a tax line.
        "tax_included": bool(order.tax_lines),
    }


def _status_update_dict(order: Order, comment: str | None = None) -> dict:
    """The plain-values payload for a status-change email — same rules as
    the receipt: everything the background task needs, nothing ORM-shaped."""
    return {
        "email": order.user.email,
        "first_name": order.user.first_name,
        "order_number": order.order_number,
        "status": order.status,
        "comment": comment,
        "total": float(order.total),
        "currency": order.currency,
    }


def queue_status_email(
    order: Order, background: BackgroundTasks, comment: str | None = None
) -> None:
    """Tell the customer their order moved — every caller's single seam.

    A dial back to "pending" is the one move that says nothing new to the
    customer, so it stays quiet; every other status (cancellations
    included) deserves the mailbox's word.
    """
    if order.status == OrderStatus.PENDING.value:
        return
    background.add_task(send_order_status_email, _status_update_dict(order, comment))


def mark_order_paid(
    order: Order,
    db,
    background: BackgroundTasks,
    *,
    payment_intent: str | None = None,
    provider: str = "stripe",
    reference: str | None = None,
    payment_id: str | None = None,
    method_type: str | None = None,
) -> bool:
    """A completed payment landed on this order — through Stripe (the
    session `reference`, the `payment_intent`) or PayPal (the PayPal order
    `reference`, the capture `payment_id`). `method_type` is what the
    customer actually used there, when the provider said.

    Idempotent — it only acts while the order is still pending, so the
    webhook and the customer-facing confirm check can race safely and the
    receipt goes out exactly once. The order's flip to paid and the
    ledger's "succeeded" are one transaction: neither exists without the
    other.
    """
    if order.payment_status != PaymentStatus.PENDING.value:
        return False
    order.payment_status = PaymentStatus.PAID.value
    if provider == "stripe":
        if payment_intent:
            order.stripe_payment_intent_id = payment_intent
        reference = reference or order.stripe_session_id
        payment_id = payment_intent or payment_id
    elif provider == "paypal":
        method_type = method_type or "paypal"
    ledger.settle(
        db, order, provider=provider, reference=reference, payment_id=payment_id,
        method_type=method_type,
    )
    db.add(
        OrderEvent(
            order_id=order.id,
            status=order.status,
            comment=f"Payment received — {payment_ops.paid_phrase(provider, method_type)}",
        )
    )
    db.commit()
    db.refresh(order)
    # The receipt travels with the payment, not the placement — by now it
    # can honestly say "paid".
    background.add_task(send_order_receipt, _receipt_dict(order))
    return True


def refund_payment_on_cancelled_order(
    order: Order,
    db,
    background: BackgroundTasks,
    *,
    payment_intent: str | None = None,
    provider: str = "stripe",
    reference: str | None = None,
    payment_id: str | None = None,
    method_type: str | None = None,
) -> bool:
    """A payment landed on an order that was already cancelled.

    The pay page outlived the order — a tab left open while the customer
    (or the desk) cancelled, and Stripe could not close it in time. The
    money must never just sit there as "paid" on a box nobody will bake:
    the payment is recorded (it happened), sent straight back less any
    late-cancellation fee the cancellation itself already recorded, written
    on the timeline in plain words, and the desk hears about it either way.
    No receipt goes out — there is nothing to receipt.

    Idempotent like mark_order_paid: it acts only while the payment is
    still pending, so a replayed webhook and the customer's confirm check
    cannot refund twice (the refund's own idempotency key is the second
    lock on that door).
    """
    if order.payment_status != PaymentStatus.PENDING.value:
        return False
    order.payment_status = PaymentStatus.PAID.value
    if provider == "stripe":
        if payment_intent:
            order.stripe_payment_intent_id = payment_intent
        reference = reference or order.stripe_session_id
        payment_id = payment_intent or payment_id
    elif provider == "paypal":
        method_type = method_type or "paypal"
    tx = ledger.settle(
        db, order, provider=provider, reference=reference, payment_id=payment_id,
        method_type=method_type,
    )
    label = payment_ops.PROVIDER_LABELS.get(provider, provider)
    fee = float(order.cancel_fee or 0)
    if tx.provider_payment_id:
        refund_note = _refund_paid_order(
            order, fee, db, initiated_by="late payment on a cancelled order"
        )
    else:
        # Nothing to refund against — never claim a refund that cannot have
        # been sent. The order honestly stays "paid" until the desk settles it.
        refund_note = f"refund could not be sent — refund it from the {label} dashboard"

    if refund_note and refund_note.startswith(REFUND_ON_ITS_WAY):
        # Accepted by the provider, not yet confirmed (a bank method): the
        # desk has nothing to do, and nobody is told the money has arrived.
        text = f"Payment arrived after the order was cancelled — {refund_note}"
        title = f"Late payment on cancelled order {order.order_number} — refund on its way"
        body = f"{label} accepted the refund and has yet to confirm it."
    elif refund_note:
        text = f"Payment arrived after the order was cancelled — {refund_note}"
        title = f"Refund needed — payment landed on cancelled order {order.order_number}"
        body = (
            "The automatic refund could not be sent — send it from the order's payment "
            f"panel, or from the {label} dashboard."
        )
    elif fee:
        text = (
            "Payment arrived after the order was cancelled — refunded automatically, "
            f"less the late-cancellation fee of {money(fee, order.currency)}"
        )
        title = f"Late payment on cancelled order {order.order_number} — refunded automatically"
        body = f"{money(fee, order.currency)} late-cancellation fee kept; the rest went back through {label}."
    else:
        text = "Payment arrived after the order was cancelled — refunded in full automatically"
        title = f"Late payment on cancelled order {order.order_number} — refunded automatically"
        body = f"The full amount went back through {label}."
    db.add(OrderEvent(order_id=order.id, status=order.status, comment=text))
    notify_admin(
        db,
        kind="order",
        title=title,
        body=body,
        link=f"/admin/orders?search={order.order_number}",
        background=background,
    )
    db.commit()
    db.refresh(order)
    if not refund_note:
        # The customer paid and deserves to hear where the money went — the
        # cancellation card, with the one line that matters. A refund that
        # could not be sent promises nothing here; the desk settles it.
        queue_status_email(
            order,
            background,
            comment=(
                "Your payment arrived after this order was cancelled, so it has "
                + (
                    "been sent back to your PayPal account"
                    if provider == "paypal"
                    else "been sent back to your card or account"
                )
                + (", less the late-cancellation fee." if fee else ".")
            ),
        )
    return True


@router.post("", response_model=OrderDetailOut, status_code=status.HTTP_201_CREATED)
def checkout(
    payload: CheckoutRequest,
    user: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    # Cash on delivery is always real. The online rails each need their
    # provider's keys — until those exist there is no honest option to
    # offer, so the request is refused before any side effects.
    if not payment_ops.available(payload.payment_method.value):
        raise HTTPException(
            status_code=400,
            detail=(
                "PayPal isn't set up yet — please choose another way to pay"
                if payload.payment_method == PaymentMethod.PAYPAL
                else "Online payments aren't set up yet — please choose cash on delivery"
            ),
        )

    # One press of the pay button, one order. The browser names each
    # checkout attempt; the same name arriving again (a double click, a
    # retry after a dropped connection) is answered with the order that
    # press already placed — never a second order, never a second charge.
    if idempotency_key is not None:
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="Idempotency-Key is not usable")
        placed = _replayed_order(db, user, idempotency_key)
        if placed is not None:
            response.status_code = status.HTTP_200_OK
            return _serialize(placed, detailed=True, payment_url=_resume_url(placed, db))

    # A brake no shopper ever meets (a dozen orders an hour), there for the
    # script that would otherwise reserve the whole shelf with unpaid orders
    # and bury the desk in pings. After the replay check on purpose: the
    # same press arriving again is not a new order.
    account_brake(
        request, "orders", user,
        "That's a lot of orders in one hour — give it a little while, or call the bakery.",
    )

    # The charge currency every figure this order stores will be read in —
    # validated up front so no row is ever written with money the pipeline
    # can't charge or report.
    charge_currency = _charge_currency()

    # Resolve delivery address (existing id, an already-known doorstep, or a
    # genuinely new one)
    if payload.address_id:
        address = db.scalar(
            select(Address).where(Address.id == payload.address_id, Address.user_id == user.id)
        )
        if address is None:
            raise HTTPException(status_code=400, detail="Invalid address_id")
    else:
        data = payload.new_address.model_dump()
        address = find_matching_address(db, user.id, data)
        if address is None:
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
        elif data["is_default"] and not address.is_default:
            # A re-typed doorstep that already exists — deliver there, and
            # honour the default tick while we have it.
            for other in db.scalars(
                select(Address).where(Address.user_id == user.id, Address.is_default.is_(True))
            ):
                other.is_default = False
            address.is_default = True

    # The van has a range. An address outside the baker's delivery area is
    # refused here — before stock moves or a payment page opens — rather
    # than placed, paid for, and then cancelled and refunded by hand.
    area = delivery.parse_area(get_store_setting(db).delivery_postal_codes)
    if not delivery.delivers_to(address.postal_code, area):
        raise HTTPException(
            status_code=400,
            detail=f"Sorry — we don't deliver to {address.postal_code} yet. "
            "Our delivery area is listed at checkout.",
        )

    # Validate items against the catalog — the server is the source of truth
    merged, by_id = _resolve_basket(db, payload.items)

    # "Today" is the shop's own date, not the server's (app/shoptime.py).
    today = shoptime.shop_today()
    if payload.delivery_date and payload.delivery_date < today:
        raise HTTPException(status_code=400, detail="Delivery date cannot be in the past")
    if payload.delivery_date and payload.delivery_date > today + timedelta(days=30):
        raise HTTPException(status_code=400, detail="Delivery date is too far ahead")

    subtotal = round(sum(by_id[pid].current_price * qty for pid, qty in merged.items()), 2)
    # Locked: the code's redemption checks and this order's own write must
    # be one uninterrupted judgement (see _load_coupon).
    coupon = _basket_coupon(db, payload.coupon_code, user, subtotal, lock=True)
    priced = _price_basket(db, merged, by_id, coupon, user)
    inclusive = isinstance(priced, pricing.InclusiveBasket)

    # A provider cannot take less than its own minimum (and nothing at all
    # for an order a voucher has already paid for). Said here, before the
    # order exists — not by placing it, being refused and cancelling it.
    provider = PAYMENT_PROVIDERS.get(payload.payment_method.value)
    if provider and Decimal(str(priced.total)) < payment_ops.MIN_ONLINE_TOTAL[provider]:
        if priced.total <= 0:
            detail = (
                "There is nothing left to pay on this order — "
                "choose cash on delivery to place it"
            )
        else:
            floor = money(float(payment_ops.MIN_ONLINE_TOTAL[provider]), charge_currency)
            detail = (
                f"{payment_ops.PROVIDER_LABELS[provider]} can't take less than {floor} — "
                "choose another way to pay for this order"
            )
        raise HTTPException(status_code=400, detail=detail)

    # Both rails start pending: cash settles at the door, card settles when
    # Stripe confirms the payment.
    payment_status = PaymentStatus.PENDING.value

    order = Order(
        order_number=_generate_order_number(db),
        user_id=user.id,
        status=OrderStatus.PENDING.value,
        payment_method=payload.payment_method.value,
        payment_status=payment_status,
        # The charge currency in force when the order is placed — the same
        # validated code every Stripe session for it will be created with.
        currency=charge_currency,
        # Both modes fill these from the one priced basket: in inclusive
        # mode `tax` is the VAT already inside the gross figures, and the
        # per-line/per-rate snapshots below carry the full breakdown.
        subtotal=priced.subtotal,
        discount_amount=priced.discount,
        coupon_code=coupon.code if coupon else None,
        tax=priced.tax,
        shipping_fee=priced.shipping,
        total=priced.total,
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
    if idempotency_key is not None:
        db.add(
            CheckoutAttempt(user_id=user.id, idempotency_key=idempotency_key, order_id=order.id)
        )

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
            # The uncommitted order and address roll back with this raise.
            # Say what is on the shelf *now*: the figure read before the
            # race is the very one that just turned out to be wrong.
            left = db.scalar(select(Product.stock).where(Product.id == product_id))
            raise _short_shelf(product_id, product.name, left)
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                product_slug=product.slug,
                unit_price=product.current_price,
                quantity=quantity,
                subtotal=round(product.current_price * quantity, 2),
                # Inclusive mode: the line's frozen VAT snapshot, written
                # once here and never recomputed (the shelf price is gross;
                # unit figures mirror it, line figures carry the coupon).
                **(
                    {
                        "vat_category": priced_line.vat_category,
                        "vat_rate": float(priced_line.vat_rate),
                        "net_unit_price": float(priced_line.net_unit_price),
                        "gross_unit_price": float(priced_line.gross_unit_price),
                        "net_line_total": float(priced_line.net_line_total),
                        "vat_amount": float(priced_line.vat_amount),
                        "gross_line_total": float(priced_line.gross_after_discount),
                    }
                    if inclusive
                    and (priced_line := next(
                        pl for pl in priced.lines if pl.product_id == product_id
                    ))
                    else {}
                ),
            )
        )

    if inclusive:
        # The order's per-rate VAT ledger: products plus the delivery slice
        # allocated to each rate — one row per rate, frozen at checkout.
        for row in priced.rate_rows:
            db.add(
                OrderTaxLine(
                    order_id=order.id,
                    vat_rate=float(row.vat_rate),
                    net_amount=float(row.net_amount),
                    vat_amount=float(row.vat_amount),
                    gross_amount=float(row.gross_amount),
                )
            )

    db.add(OrderEvent(order_id=order.id, status=OrderStatus.PENDING.value, comment="Order placed"))
    # Same transaction as the order itself — the bell can't announce a sale
    # that didn't happen.
    notify_admin(
        db,
        kind="order",
        title=f"New order {order.order_number} — {money(priced.total, order.currency)}",
        body=f"{user.full_name} · {sum(merged.values())} items · "
        f"{payload.payment_method.value.replace('_', ' ')}",
        link=f"/admin/orders?search={order.order_number}",
        background=background,
    )
    try:
        db.commit()
    except IntegrityError:
        # The same press arrived twice at once and the other request got
        # there first (the unique key on checkout_attempts is the referee).
        # Everything this request wrote — order, stock, desk ping — is
        # undone; the answer is the order the winner placed.
        db.rollback()
        background.tasks.clear()
        placed = _replayed_order(db, user, idempotency_key) if idempotency_key else None
        if placed is None:
            raise
        response.status_code = status.HTTP_200_OK
        return _serialize(placed, detailed=True, payment_url=_resume_url(placed, db))
    db.refresh(order)

    # An online order now needs its provider's pay page. If the provider
    # can't be reached, the order is cancelled on the spot — no order may
    # sit in the book with no way to pay it. An unchargeable currency is the
    # same dead end (the gateways refuse to guess one), and the
    # freshly-placed order is rolled back the same way.
    payment_url = None
    if provider:
        try:
            payment_url = payment_ops.open_pay_page(db, order, payment_ops.public_base(request))
        except (payment_ops.PayPageUnavailable, payment_ops.UnchargeableCurrency):
            _cancel_order(
                order,
                db,
                by_customer=False,
                comment="Payment provider unreachable — cancelled automatically",
            )
            # The press is forgotten with the order it placed: trying again
            # must place a fresh order, not be handed this cancelled one.
            for attempt in db.scalars(
                select(CheckoutAttempt).where(CheckoutAttempt.order_id == order.id)
            ):
                db.delete(attempt)
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="The payment provider didn't answer — nothing was charged. Please try again.",
            )
        db.commit()

    # The receipt every order earns as proof of purchase — cash orders get
    # theirs at placement (the bill to settle); online orders get theirs
    # when the payment lands, when it can honestly say "paid".
    if not provider:
        background.add_task(send_order_receipt, _receipt_dict(order))

    return _serialize(order, detailed=True, payment_url=payment_url)


# What a checkout attempt may call itself: long enough not to collide,
# short and plain enough to store and log.
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")


def _replayed_order(db, user: User, key: str) -> Order | None:
    """The order this user's earlier press under the same key placed."""
    attempt = db.scalar(
        select(CheckoutAttempt).where(
            CheckoutAttempt.user_id == user.id, CheckoutAttempt.idempotency_key == key
        )
    )
    return db.get(Order, attempt.order_id) if attempt is not None else None


def _resume_url(order: Order, db) -> str | None:
    """Where to send the customer to finish paying an order that already
    exists — its open pay page, if it still has one."""
    if (
        order.status == OrderStatus.CANCELLED.value
        or order.payment_status != PaymentStatus.PENDING.value
    ):
        return None
    tx = ledger.open_for_order(db, order.id)
    return tx.checkout_url if tx is not None else None


@router.post("/quote", response_model=QuoteOut)
def basket_quote(
    payload: QuoteRequest,
    user: OptionalUser,
    db: DbSession,
    request: Request,
):
    """Price a basket exactly as checkout would charge it — same catalog,
    same coupon rules, same delivery threshold, same cent rounding — while
    touching nothing: no order, no stock movement, no Stripe session, no
    email. The storefront's tallies come from here so the browser holds no
    arithmetic of its own; placement then re-derives every figure
    independently, so a displayed quote is never authorization to charge.

    Open to guests (a basket can be browsed before login, like the voucher
    board); a signed-in shopper additionally gets the one-per-customer
    coupon check, since their identity is known.
    """
    # A public pricing endpoint is a free lookup — keep it from being
    # hammered, but looser than the write endpoints: it stores nothing.
    limiter = request.app.state.limiters["quote"]
    if not limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429, detail="That's a lot of price checks — try again in a moment."
        )

    merged, by_id = _resolve_basket(db, payload.items)
    subtotal = round(sum(by_id[pid].current_price * qty for pid, qty in merged.items()), 2)
    coupon = _basket_coupon(db, payload.coupon_code, user, subtotal)
    totals = _price_basket(db, merged, by_id, coupon, user)
    tax_included = isinstance(totals, pricing.InclusiveBasket)

    lines = []
    for product_id, quantity in merged.items():
        product = by_id[product_id]
        lines.append(
            {
                "product_id": product_id,
                "name": product.name,
                "slug": product.slug,
                "unit_price": product.current_price,
                "quantity": quantity,
                "line_total": round(product.current_price * quantity, 2),
            }
        )

    return QuoteOut(
        items=lines,
        subtotal=subtotal,
        discount_amount=totals.discount,
        coupon_code=coupon.code if coupon else None,
        coupon_percent_off=(
            coupon.percent_off if coupon and coupon.fixed_amount_off is None else None
        ),
        coupon_fixed_amount=(
            float(coupon.fixed_amount_off) if coupon and coupon.fixed_amount_off is not None else None
        ),
        tax=totals.tax,
        shipping_fee=totals.shipping,
        total=totals.total,
        # The currency the eventual order will be charged in — checkout
        # records this same validated code on every order it writes, so a
        # quote never promises a currency placement would refuse.
        currency=_charge_currency(),
        # The free-delivery line as it applies to THIS shopper (a VIP
        # regular's smaller threshold when one is configured) — the meter on
        # the basket page draws itself from here, never from a guess.
        free_delivery_threshold=_delivery_threshold(db, user),
        # True when the figures above are VAT-inclusive (the German B2C
        # mode): the tax line is part of them, not added on top — the
        # storefront relabels its tax row accordingly.
        tax_included=tax_included,
    )


@router.get("/coupons/{code}", response_model=CouponPreviewOut)
def check_coupon(code: str, user: CurrentUser, db: DbSession):
    """Preview a coupon while the customer is at the checkout screen."""
    coupon = _load_coupon(db, code, user)
    return CouponPreviewOut(
        code=coupon.code,
        percent_off=coupon.percent_off,
        description=coupon.description,
        min_subtotal=float(coupon.min_subtotal) if coupon.min_subtotal else None,
        min_subtotal_approved=coupon.min_subtotal_approved,
        fixed_amount_off=(
            float(coupon.fixed_amount_off) if coupon.fixed_amount_off is not None else None
        ),
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


def _lock_for_cancellation(order: Order, db) -> Order | None:
    """The order re-read under a row lock — every cancellation path takes
    this before judging, so no two of them (a customer's click, the admin
    desk's dial, the expiry webhook, the sweep) can pass their guards on
    the same order at the same time (SQLite ignores FOR UPDATE; Postgres
    doesn't). The desk's ordinary status dial takes it too: an unlocked
    dial could otherwise overwrite a cancellation that landed a heartbeat
    earlier and put a refunded, restocked order back on the bake list.
    populate_existing is the point of the re-read: the identity map would
    otherwise hand back the first, unlocked load's stale attributes. None
    means the row vanished between the two reads."""
    return db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@router.get("/{order_id}", response_model=OrderDetailOut)
def order_detail(order_id: int, user: CurrentUser, db: DbSession):
    order = _get_order_for(order_id, user, db)
    return _serialize(order, detailed=True, cancel_fee_preview=_late_cancel_fee(order, db))


# How a timeline note opens when the provider accepted a refund it has not
# confirmed yet — one constant, because a caller tells it from a failure by it.
REFUND_ON_ITS_WAY = "refund is on its way"


def _refund_paid_order(
    order: Order, fee: float, db, *, initiated_by: str = "order cancellation"
) -> str | None:
    """Settle the money side of cancelling a paid order. Returns a note for
    the timeline when the settlement needs a human's eye, else None.

    A payment taken online goes back the way it came — through Stripe or
    PayPal — for whatever of it is still held, less any kept fee; the
    ledger records the provider's own answer, and the order's payment
    status follows only a refund the provider has confirmed. Cash refunds
    are handed back in person, so only the status flips. A card order with
    no payment intent is demo history from the old simulated branch — no
    money ever moved.

    One asymmetry is documented, not hidden: a provider's refund cannot be
    rolled back with this transaction. If the commit fails after the
    provider accepted, the order honestly stays "paid" while the customer
    holds the money — the refund's idempotency key keeps any retry from
    paying twice, and the reconciliation report names the mismatch.
    """
    tx = None
    if ledger.provider_for(order):
        tx = ledger.money_transaction(db, order) or ledger.adopt_order_payment(db, order)

    if tx is None:
        if order.payment_status != PaymentStatus.PAID.value:
            return None  # nothing collected, nothing owed
        if stripe_gateway.cents(float(order.total)) - stripe_gateway.cents(fee) <= 0:
            return None  # the kept fee is the whole total — nothing goes back
        order.payment_status = (
            PaymentStatus.PARTIALLY_REFUNDED.value if fee else PaymentStatus.REFUNDED.value
        )
        return None

    if order.payment_status not in (
        PaymentStatus.PAID.value,
        PaymentStatus.PARTIALLY_REFUNDED.value,
    ):
        return None  # nothing collected, or everything already returned
    refund_cents = ledger.cents(ledger.refundable(db, tx)) - stripe_gateway.cents(fee)
    if refund_cents <= 0:
        # The kept fee covers all that is left (a 100 % late-cancellation
        # setting): nothing goes back, so the payment simply stands — no
        # zero-amount refund for a provider to refuse, no refund claimed.
        return None
    refund = payment_ops.issue_refund(
        db, order, tx, refund_cents, reason="Order cancelled", initiated_by=initiated_by
    )
    label = payment_ops.PROVIDER_LABELS.get(tx.provider, tx.provider)
    if refund.status == "failed":
        # The cancellation stands; the money doesn't. Say so on the
        # timeline rather than promising a refund that hasn't happened.
        return (
            "refund could not be sent — retry it from this order's payment panel, "
            f"or the {label} dashboard"
        )
    if refund.status == "pending":
        return f"{REFUND_ON_ITS_WAY} — {label} has yet to confirm it"
    return None


def _close_pay_page(order: Order, db) -> None:
    """A card order cancelled before its money landed still has a live pay
    page at Stripe — close it, so nobody can pay for a box that will never
    be baked. Best effort by design: when Stripe can't close it (paid a
    heartbeat ago, already lapsed, unreachable), the payment webhook's own
    guard sends back whatever still lands (refund_payment_on_cancelled_order).

    PayPal has no page to close: it only ever takes money when the shop
    asks it to capture, and a cancelled order is never captured. Either
    way the ledger stops counting the page as one that may still be paid."""
    if order.payment_status != PaymentStatus.PENDING.value:
        return
    if order.payment_method == PaymentMethod.CARD.value and order.stripe_session_id:
        stripe_gateway.expire_session(order.stripe_session_id)
    ledger.close_open(db, order, TransactionStatus.CANCELLED.value, detail="order cancelled")


def _cancel_order(
    order: Order,
    db,
    *,
    by_customer: bool,
    comment: str | None = None,
    close_pay_page: bool = True,
) -> None:
    """One cancellation path for every caller: flip the status, settle the
    money, put the stock back and log the event.

    Once the ovens have started the shop keeps a slice of the total — the
    percent is the admin's to tune (Settings, back of house). Bakery-side
    cancellations are always free.

    `close_pay_page` is False only where the Stripe session is already
    known to be over (its own expiry or failed-payment event brought us
    here) — there is no page left to close.
    """
    fee = _late_cancel_fee(order, db) if by_customer else 0.0

    order.status = OrderStatus.CANCELLED.value
    order.cancel_fee = fee
    refund_note = _refund_paid_order(order, fee, db)
    if close_pay_page:
        _close_pay_page(order, db)

    # Return reserved stock — an additive UPDATE against the current
    # database value, never a number computed from a loaded ORM object,
    # so no two cancellations can read-modify-write past each other. A
    # deleted product matches no rows: the old `if product:` guard, restated.
    for item in order.items:
        if item.product_id:
            db.execute(
                update(Product)
                .where(Product.id == item.product_id)
                .values(stock=Product.stock + item.quantity)
            )

    text = comment if comment is not None else (
        "Cancelled by customer" if by_customer else "Cancelled by bakery"
    )
    if fee:
        text += (
            f" — late-cancellation fee of {money(fee, order.currency)} kept "
            "(baking had started)"
        )
    if refund_note:
        text += f" — {refund_note}"

    db.add(OrderEvent(order_id=order.id, status=OrderStatus.CANCELLED.value, comment=text))


@router.post("/{order_id}/cancel", response_model=OrderDetailOut)
def cancel_order(order_id: int, user: CurrentUser, db: DbSession, background: BackgroundTasks):
    order = _get_order_for(order_id, user, db)
    # Judge under the lock: the admin desk's dial or the expiry sweep may
    # have cancelled this order a moment before the click landed.
    order = _lock_for_cancellation(order, db)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.can_be_cancelled:
        raise HTTPException(
            status_code=400,
            detail="This order can no longer be cancelled — contact the bakery",
        )
    _cancel_order(order, db, by_customer=order.user_id == user.id)
    db.commit()
    # The confirmation the mailbox deserves: cancelled, fee (if any) and
    # refund state are all on the order page this email points home to.
    queue_status_email(order, background)
    db.refresh(order)
    return _serialize(order, detailed=True)
