"""The Stripe side of the shop: the webhook that confirms payments and the
customer-facing checks around it.

Two paths can mark an order paid, and both are safe to run in any order:
the webhook (Stripe's signed word for it) and POST /confirm (what the order
page calls when the customer returns from paying, so the page doesn't have
to wait for the webhook). The transition itself is idempotent and the
webhook row-locks, so exactly one of them sends the receipt.

The webhook also listens for money the *dashboard* moves: a refund issued
by hand at Stripe (the documented fallback when a gateway refund fails)
syncs the order's payment status here, and a dispute rings the desk. A
delayed payment that fails (Klarna, SEPA) cancels its order through the
expiry path's own guards the moment Stripe reports the failure.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .. import stripe_gateway
from ..database import SessionLocal, utcnow
from ..models import Order, OrderEvent, OrderStatus, PaymentMethod, PaymentStatus
from ..notifications import notify_admin
from ..routers.orders import (
    _approved_money_rules,
    _cancel_order,
    _get_order_for,
    _late_cancel_fee,
    _lock_for_cancellation,
    _serialize,
    mark_order_paid,
    queue_status_email,
    refund_payment_on_cancelled_order,
)
from ..schemas import OrderDetailOut
from ..security import CurrentUser, DbSession

logger = logging.getLogger("bakery.payments")

router = APIRouter(prefix="/api/payments", tags=["payments"])

# How often the reconciliation sweep below inspects the order book.
SWEEP_INTERVAL_SECONDS = 15 * 60


def _drain(background: BackgroundTasks) -> None:
    """Run tasks queued on a BackgroundTasks no response will carry — the
    sweep reconciles outside a request, so it sends its own receipts."""
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)


def reconcile_stale_card_orders() -> None:
    """Self-heal for webhook outages.

    A card order still pending well past its pay-by window means Stripe's
    word never landed — endpoint moved in the dashboard, a domain cutover
    missed, retries given up. Each one is checked against the gateway
    directly: paid orders get their paid transition and receipt, expired
    ones cancel with the stock returned, the same outcomes the webhook
    would have delivered. Stripe being unreachable is not an error here —
    the next round tries again.
    """
    if not stripe_gateway.configured():
        return
    # The pay-by window plus slack, so a session Stripe is still closing is
    # left for its own events.
    cutoff = utcnow() - timedelta(
        seconds=stripe_gateway.SESSION_LIFETIME_SECONDS + 10 * 60
    )
    db = SessionLocal()
    try:
        stale = db.scalars(
            select(Order)
            .where(
                Order.payment_method == PaymentMethod.CARD.value,
                Order.payment_status == PaymentStatus.PENDING.value,
                Order.status == OrderStatus.PENDING.value,
                Order.stripe_session_id.is_not(None),
                Order.created_at < cutoff,
            )
        ).all()
        for order in stale:
            try:
                session = stripe_gateway.retrieve_session(order.stripe_session_id)
            except stripe_gateway.GatewayUnavailable:
                logger.warning(
                    "reconciliation sweep: Stripe unavailable — resuming on the next round"
                )
                db.rollback()
                return
            try:
                if session.get("payment_status") == "paid":
                    background = BackgroundTasks()
                    locked = db.scalar(
                        select(Order)
                        .where(Order.id == order.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if locked is None:
                        continue  # the row vanished mid-sweep
                    if mark_order_paid(
                        locked,
                        db,
                        background,
                        payment_intent=session.get("payment_intent"),
                    ):
                        logger.info(
                            "order %s marked paid by the reconciliation sweep (the webhook never landed)",
                            order.order_number,
                        )
                    _drain(background)
                elif session.get("status") == "expired":
                    # The expiry webhook's own guards, reused verbatim. The
                    # customer email rides the sweep's own drain — no
                    # response will carry these tasks.
                    expiry_background = BackgroundTasks()
                    _cancel_expired_session(
                        {"id": order.stripe_session_id}, db, expiry_background
                    )
                    _drain(expiry_background)
            except Exception:
                logger.exception(
                    "reconciliation sweep: order %s could not be reconciled",
                    order.order_number,
                )
                db.rollback()
    finally:
        db.close()


def _order_for_session(session: dict, db) -> Order | None:
    """The order a webhook session belongs to — by the session id stored at
    checkout, with the event's metadata as the fallback."""
    order = db.scalar(select(Order).where(Order.stripe_session_id == session.get("id")))
    if order is None:
        metadata = session.get("metadata") or {}
        try:
            order_id = int(metadata.get("order_id") or 0)
        except (TypeError, ValueError):
            order_id = 0
        if order_id:
            order = db.get(Order, order_id)
    return order


def _record_completed_payment(session: dict, background: BackgroundTasks, db) -> None:
    if session.get("payment_status") != "paid":
        # Delayed payment methods (bank debit and friends) may still be
        # processing — their own success event will land the payment.
        return
    order = _order_for_session(session, db)
    if order is None:
        logger.warning("Stripe payment for unknown session %s — no order touched", session.get("id"))
        return
    # Lock the row so the webhook and the /confirm check can't both run the
    # pending -> paid transition, and refresh it in place — the identity map
    # would otherwise keep the first, unlocked load's stale attributes
    # (SQLite ignores FOR UPDATE; Postgres doesn't).
    order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        return  # the row vanished between the lookup and the lock
    _land_payment(order, session, background, db)


def _land_payment(order: Order, session: dict, background: BackgroundTasks, db) -> None:
    """Decide what a completed payment means for a locked, freshly read
    order. Every paid session ends in exactly one of these — never in
    silence, because silence here is money the book doesn't know about:

    * the order was cancelled before the money landed → sent straight back;
    * the order is waiting for its payment → marked paid, receipt sent;
    * the order already holds this very payment → a replay, nothing to do;
    * the order already holds a *different* payment → a second charge for
      the same box (an old pay link paid as well as a fresh one) → the
      extra one is refunded in full and the desk is told.
    """
    intent = session.get("payment_intent")
    if order.status == OrderStatus.CANCELLED.value:
        if refund_payment_on_cancelled_order(order, db, background, payment_intent=intent):
            logger.warning(
                "order %s: payment landed after cancellation (session %s) — sent back",
                order.order_number,
                session.get("id"),
            )
            return
    elif mark_order_paid(order, db, background, payment_intent=intent):
        logger.info("order %s paid via Stripe session %s", order.order_number, session.get("id"))
        return
    _settle_extra_payment(order, session, background, db)


def _settle_extra_payment(order: Order, session: dict, background: BackgroundTasks, db) -> None:
    """A paid session arrived for an order whose payment is already on the
    book. The same payment arriving again (Stripe retries, the confirm
    check racing the webhook) is the ordinary case and changes nothing;
    a different payment intent is real extra money and never stays."""
    intent = str(session.get("payment_intent") or "")
    known = order.stripe_payment_intent_id
    if not intent or intent == known:
        return
    # The timeline is the ledger of extra payments already handled — a
    # replayed event must not refund (or announce) the same one twice.
    marker = f"({intent})"
    if db.scalar(
        select(OrderEvent.id)
        .where(OrderEvent.order_id == order.id, OrderEvent.comment.contains(marker, autoescape=True))
        .limit(1)
    ):
        return
    link = f"/admin/orders?search={order.order_number}"

    if (
        known is None
        and order.payment_status == PaymentStatus.PAID.value
        and order.status != OrderStatus.CANCELLED.value
    ):
        # Marked paid by hand at the desk before any card payment landed.
        # This is the first money Stripe actually holds for the order: keep
        # its trail so a later cancellation can refund it, and let a person
        # decide whether the customer has now paid twice (cash and card).
        order.stripe_payment_intent_id = intent
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment=f"Card payment received {marker} — the order was already marked paid at the desk",
            )
        )
        notify_admin(
            db,
            kind="order",
            title=f"Card payment on order {order.order_number}, already marked paid",
            body="Check whether this order was paid twice — once at the desk and once by card.",
            link=link,
            background=background,
        )
        db.commit()
        logger.warning(
            "order %s: card payment %s landed on an order already marked paid by hand",
            order.order_number,
            intent,
        )
        return

    amount = session.get("amount_total")
    if type(amount) is not int or amount <= 0:
        # Every session for an order is minted from the same stored total.
        amount = stripe_gateway.cents(float(order.total))
    try:
        stripe_gateway.refund(intent, amount)
    except stripe_gateway.GatewayUnavailable:
        outcome = "refund could not be sent — refund it from the Stripe dashboard"
        title = f"Refund needed — order {order.order_number} was charged twice"
    else:
        outcome = "refunded in full automatically"
        title = f"Order {order.order_number} was charged twice — extra payment refunded"
    db.add(
        OrderEvent(
            order_id=order.id,
            status=order.status,
            comment=f"A second payment arrived for this order {marker} — {outcome}",
        )
    )
    notify_admin(
        db,
        kind="order",
        title=title,
        body="An older pay link was paid as well as the current one.",
        link=link,
        background=background,
    )
    db.commit()
    logger.warning(
        "order %s: second payment %s — %s", order.order_number, intent, outcome
    )


def _cancel_expired_session(
    session: dict,
    db,
    background: BackgroundTasks | None = None,
    *,
    comment: str = "Payment window expired — cancelled automatically",
) -> None:
    """The session ended unpaid — its window closed, or a delayed payment
    (Klarna, SEPA) failed after the redirect — so cancel the order and put
    the stock back so the case doesn't leak reserved boxes. The `comment`
    names which of the two happened, for the timeline's sake."""
    order = _order_for_session(session, db)
    if order is None:
        return
    # Lock the row before judging it, so the webhook and the sweep can't
    # both pass the guards and return the stock twice. The guards are the
    # locked re-read — refreshed in place, so they judge the database's
    # values, not the first, unlocked load's (SQLite ignores FOR UPDATE;
    # Postgres doesn't).
    order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        return  # the row vanished between the lookup and the lock
    if order.status == OrderStatus.CANCELLED.value:
        return
    if order.stripe_session_id and session.get("id") != order.stripe_session_id:
        return  # a newer pay link superseded this session
    if order.payment_status != PaymentStatus.PENDING.value:
        return  # paid after all (raced with the completed event) — leave it
    # The session is already over — there is no pay page left to close.
    _cancel_order(order, db, by_customer=False, comment=comment, close_pay_page=False)
    db.commit()
    if background is not None:
        # They asked for the bake and walked away from the pay page — the
        # honest email says the window closed and the box went back, so
        # they're never left wondering where the order went.
        queue_status_email(order, background)
    logger.info("order %s cancelled: its Stripe session expired unpaid", order.order_number)


def _order_for_intent(charge: dict, db) -> Order | None:
    """The order a charge belongs to, by the payment intent the checkout
    session left behind. None means Stripe knows a charge this book
    doesn't — logged, never guessed."""
    intent = str(charge.get("payment_intent") or "")
    if not intent:
        return None
    return db.scalar(select(Order).where(Order.stripe_payment_intent_id == intent))


def _record_external_refund(charge: dict, db) -> None:
    """A refund issued from the Stripe dashboard landed on the wire — sync
    the book to match.

    The app refunds through the gateway itself (idempotently), so its own
    refunds arrive here twice: the order already says refunded/partially
    refunded and this is a no-op. What this catches is the human path —
    the timeline's own fallback advice ("retry it from the Stripe
    dashboard") and any support refund — which without this handler would
    leave an order claiming money back it has already returned.
    """
    order = _order_for_intent(charge, db)
    if order is None:
        logger.warning(
            "Stripe refund for unknown payment intent %s — no order touched",
            charge.get("payment_intent"),
        )
        return
    order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        return
    if order.payment_status not in (
        PaymentStatus.PAID.value,
        PaymentStatus.PARTIALLY_REFUNDED.value,
    ):
        return  # nothing collected, or already fully recorded
    try:
        refunded = int(charge.get("amount_refunded") or 0)
        charged = int(charge.get("amount") or 0)
    except (TypeError, ValueError):
        return  # unreadable amounts are logged, never guessed from
    if charged <= 0:
        return
    new_status = (
        PaymentStatus.REFUNDED.value if refunded >= charged else PaymentStatus.PARTIALLY_REFUNDED.value
    )
    if new_status == order.payment_status:
        return  # already in the book — the app's own refund, arriving twice
    order.payment_status = new_status
    word = "refunded" if new_status == PaymentStatus.REFUNDED.value else "partially refunded"
    db.add(
        OrderEvent(
            order_id=order.id,
            status=order.status,
            comment=f"Refund recorded from the Stripe dashboard — payment marked {word}",
        )
    )
    db.commit()
    logger.info("order %s payment marked %s by a dashboard refund", order.order_number, word)


def _record_dispute(charge: dict, background: BackgroundTasks, db) -> None:
    """A customer disputed a charge with their bank — the desk must hear
    this the moment it happens, not at the next reconciliation.

    Disputes change nothing here by themselves (Stripe holds the money and
    the case is answered in Stripe's dashboard within their deadline); the
    bell, the email and the timeline entry exist so the deadline is never
    discovered after it passed."""
    order = _order_for_intent(charge, db)
    if order is None:
        logger.warning(
            "dispute on unknown payment intent %s", charge.get("payment_intent")
        )
        notify_admin(
            db,
            kind="order",
            title="Card dispute opened — no matching order found",
            body=f"Payment intent {charge.get('payment_intent')}",
            background=background,
        )
        db.commit()
        return
    db.add(
        OrderEvent(
            order_id=order.id,
            status=order.status,
            comment="Card dispute opened — answer it in the Stripe dashboard before its deadline",
        )
    )
    notify_admin(
        db,
        kind="order",
        title=f"Card dispute on order {order.order_number}",
        body="Answer it in the Stripe dashboard — deadlines are short and evidence is everything",
        link=f"/admin/orders?search={order.order_number}",
        background=background,
    )
    db.commit()


@router.post("/webhook")
async def stripe_webhook(request: Request, background: BackgroundTasks, db: DbSession):
    """Stripe's signed word that a payment landed (or its window closed).

    Public by design — the signature is the credential. It reads the raw
    body untouched: signature verification needs the exact bytes Stripe
    sent, so nothing parses the payload before it verifies.
    """
    payload = await request.body()
    try:
        event = stripe_gateway.verify_webhook(
            payload, request.headers.get("stripe-signature", "")
        )
    except stripe_gateway.BadSignature as exc:
        raise HTTPException(status_code=400, detail=f"Webhook rejected: {exc}") from None

    obj = event.get("data", {}).get("object")
    if not isinstance(obj, dict):
        obj = {}  # a malformed event is ignored, never a 500
    event_type = event.get("type")
    # DB work off the event loop — the endpoint is async only for the raw body.
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await run_in_threadpool(_record_completed_payment, obj, background, db)
    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_expired"):
        await run_in_threadpool(_cancel_expired_session, obj, db, background)
    elif event_type == "checkout.session.async_payment_failed":
        # A delayed payment failed after the redirect. The expiry path's own
        # guards decide (a paid order stays paid, a newer pay link wins), so
        # the box goes back the moment Stripe says the money won't come —
        # not hours later at the session's own expiry.
        await run_in_threadpool(
            _cancel_expired_session,
            obj,
            db,
            background,
            comment="Payment failed — cancelled automatically",
        )
    elif event_type == "charge.refunded":
        await run_in_threadpool(_record_external_refund, obj, db)
    elif event_type == "charge.dispute.created":
        await run_in_threadpool(_record_dispute, obj, background, db)
    else:
        logger.info("ignoring Stripe event %s", event_type)
    return {"received": True}


@router.post("/confirm/{order_id}", response_model=OrderDetailOut)
def confirm_payment(order_id: int, user: CurrentUser, db: DbSession, background: BackgroundTasks):
    """The customer is back from Stripe: reconcile the order with the
    gateway right now, so what they read next is true even when the
    webhook is still in flight."""
    order = _get_order_for(order_id, user, db)
    # A cancelled order is checked too: its pay page may have outlived it,
    # and money that landed there must be sent back, not left unseen until
    # (or unless) the webhook arrives.
    if order.stripe_session_id and order.payment_status == PaymentStatus.PENDING.value:
        try:
            session = stripe_gateway.retrieve_session(order.stripe_session_id)
        except stripe_gateway.GatewayUnavailable:
            raise HTTPException(
                status_code=503,
                detail="The payment provider didn't answer — check back in a moment",
            )
        if session.get("payment_status") == "paid":
            # The webhook may have landed while retrieve_session was on the
            # wire — judge on a locked, freshly refreshed row, the same
            # re-read the webhook itself takes, so exactly one of the two
            # records the payment and sends the receipt.
            order = _lock_for_cancellation(order, db)
            if order is None:
                raise HTTPException(status_code=404, detail="Order not found")
            if order.status == OrderStatus.CANCELLED.value:
                refund_payment_on_cancelled_order(
                    order, db, background, payment_intent=session.get("payment_intent")
                )
            else:
                mark_order_paid(order, db, background, payment_intent=session.get("payment_intent"))
    return _serialize(order, detailed=True, cancel_fee_preview=_late_cancel_fee(order, db))


@router.post("/{order_id}/session", response_model=OrderDetailOut, status_code=status.HTTP_201_CREATED)
def new_payment_session(
    order_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
    background: BackgroundTasks,
):
    """A fresh pay link for an order that was placed but never paid — the
    card failed or they walked away mid-checkout; they shouldn't have to
    rebuild the basket from scratch.

    One order, one live pay page: the link being replaced is asked about
    first (it may have been paid a moment ago, the webhook still on the
    wire — then the order is simply marked paid and no second link is
    minted), and once the fresh one is on the order the old page is closed
    so it can never take a second payment for the same box.
    """
    order = _get_order_for(order_id, user, db)
    if order.payment_method != PaymentMethod.CARD.value:
        raise HTTPException(status_code=400, detail="This order isn't a card order")
    if order.status == OrderStatus.CANCELLED.value or order.payment_status != PaymentStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="This order has no payment to complete")
    # The same fail-closed gate as checkout: while the shop's delivery
    # pricing is unapproved, no new Stripe session is minted for any order,
    # even one placed when the rules were live. The stored totals are never
    # recalculated — this only decides whether charging may resume.
    _approved_money_rules()

    superseded = order.stripe_session_id
    # Whether the old page should be closed afterwards: yes unless Stripe
    # itself says it is already over.
    superseded_open = bool(superseded)
    if superseded:
        try:
            previous = stripe_gateway.retrieve_session(superseded)
        except stripe_gateway.GatewayUnavailable:
            # Unreadable — a session minted under other keys, or Stripe
            # blinking. Not a reason to strand the order unpayable: the
            # fresh link below fails on its own if Stripe is really down,
            # and the webhook's guard covers a late payment on the old page.
            previous = {}
        if previous.get("payment_status") == "paid":
            # Already paid on the link they are asking to replace. Land the
            # payment now (locked, exactly as the webhook would) and hand
            # back the paid order — with no payment_url, because there is
            # nothing left to pay.
            locked = _lock_for_cancellation(order, db)
            if locked is None:
                raise HTTPException(status_code=404, detail="Order not found")
            if locked.status == OrderStatus.CANCELLED.value:
                refund_payment_on_cancelled_order(
                    locked, db, background, payment_intent=previous.get("payment_intent")
                )
            else:
                mark_order_paid(locked, db, background, payment_intent=previous.get("payment_intent"))
            response.status_code = status.HTTP_200_OK
            return _serialize(
                locked, detailed=True, cancel_fee_preview=_late_cancel_fee(locked, db)
            )
        superseded_open = previous.get("status") in (None, "open")

    try:
        session = stripe_gateway.create_checkout_session(
            order, str(request.base_url).rstrip("/")
        )
    except stripe_gateway.InvalidOrderCurrency:
        # A historical order whose stored currency can't be charged must be
        # refused untouched — never cancelled, never relabelled. The desk
        # settles it by hand; the customer is told honestly.
        raise HTTPException(
            status_code=400,
            detail="This order's currency can no longer be charged — we'll contact you to settle it",
        )
    except stripe_gateway.GatewayUnavailable:
        raise HTTPException(
            status_code=503,
            detail="The payment provider didn't answer — please try again",
        )
    order.stripe_session_id = session["id"]
    db.commit()
    db.refresh(order)
    # Only now — with the fresh link committed — is the old page closed.
    # The order matters: closing it first would let Stripe's expiry event
    # arrive while the order still pointed at that session, and the expiry
    # path would cancel the very order being paid. Committed first, the
    # event finds a superseded session and is ignored. Best effort: if the
    # old page cannot be closed and is paid anyway, the webhook refunds the
    # second payment (_settle_extra_payment).
    if superseded_open:
        stripe_gateway.expire_session(superseded)
    return _serialize(
        order, detailed=True, payment_url=session["url"], cancel_fee_preview=_late_cancel_fee(order, db)
    )
