"""The provider side of the shop: the webhooks that confirm payments and
the customer-facing checks around them — for Stripe and for PayPal.

A redirect back from a pay page proves nothing. Money is on the book only
when the provider itself says so, and there are three ways it can: its
signed webhook, the shop asking it directly when the customer returns
(POST /confirm), and the reconciliation sweep asking about orders nobody
has spoken for. All three end in the same idempotent, row-locked
transition, so they can arrive in any order — or twice — and the order is
marked paid, and the receipt sent, exactly once.

Every webhook event is also put on record by its provider id before it is
acted on (app/ledger.claim_event): a redelivery is recognised and dropped,
and what each event did to which order can be read back afterwards.

The two providers differ in one way that shapes this file. Stripe takes
the money itself on its hosted page — so a page that outlives its order
has to be closed, and money that lands anyway has to be sent back. PayPal
only *authorises* on its page; the shop asks for the capture. A cancelled
or superseded PayPal order is therefore simply never captured.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .. import ledger, payment_ops, paypal_gateway, stripe_gateway
from ..database import SessionLocal, utcnow
from ..models import (
    PAYMENT_PROVIDERS,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)
from ..money import money
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

# How long an online order may wait for its payment before the sweep asks
# the provider about it: the pay window plus slack, so a page the provider
# is still closing is left for its own events. PayPal has no expiry the
# shop can set, so this window is also what ends an unapproved PayPal order.
PAY_WINDOW = timedelta(seconds=stripe_gateway.SESSION_LIFETIME_SECONDS + 10 * 60)


def _drain(background: BackgroundTasks) -> None:
    """Run tasks queued on a BackgroundTasks no response will carry — the
    sweep reconciles outside a request, so it sends its own receipts."""
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)


def _order_link(order: Order) -> str:
    return f"/admin/orders?search={order.order_number}"


# --------------------------------------------------------------------------
# Stripe: what a completed session means
# --------------------------------------------------------------------------

def _order_for_session(session: dict, db) -> Order | None:
    """The order a webhook session belongs to — by the session id stored at
    checkout, with the event's metadata as the fallback."""
    session_id = session.get("id")
    # Only a real id may be looked up: a missing one would read as "the
    # order whose session is NULL" — any cash order in the book.
    order = (
        db.scalar(select(Order).where(Order.stripe_session_id == session_id))
        if isinstance(session_id, str) and session_id
        else None
    )
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
        # processing — their own success event will land the payment. The
        # ledger shows the wait for what it is.
        session_id = session.get("id")
        tx = (
            ledger.find(db, "stripe", reference=session_id)
            if isinstance(session_id, str) and session_id
            else None
        )
        if tx is not None:
            ledger.mark_processing(db, tx, detail="awaiting the bank's confirmation")
            db.commit()
        return
    order = _order_for_session(session, db)
    if order is None:
        logger.warning("Stripe payment for unknown session %s — no order touched", session.get("id"))
        return
    # Lock the row so the webhook and the /confirm check can't both run the
    # pending -> paid transition, and refresh it in place — the identity map
    # would otherwise keep the first, unlocked load's stale attributes
    # (SQLite ignores FOR UPDATE; Postgres doesn't).
    order = _lock_for_cancellation(order, db)
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
    reference = session.get("id") or order.stripe_session_id
    if order.status == OrderStatus.CANCELLED.value:
        if refund_payment_on_cancelled_order(
            order, db, background, payment_intent=intent, reference=reference
        ):
            logger.warning(
                "order %s: payment landed after cancellation (session %s) — sent back",
                order.order_number,
                session.get("id"),
            )
            return
    elif mark_order_paid(order, db, background, payment_intent=intent, reference=reference):
        logger.info("order %s paid via Stripe session %s", order.order_number, session.get("id"))
        _flag_amount_mismatch(order, session.get("amount_total"), session.get("currency"), background, db)
        return
    _settle_extra_payment(order, session, background, db)


def _flag_amount_mismatch(order: Order, charged_cents, currency, background, db) -> None:
    """The page charged something other than the order's total. It cannot
    happen while every page is minted from the stored order — which is
    exactly why, if it ever does, a person must hear about it at once."""
    if type(charged_cents) is not int:
        return
    if charged_cents == ledger.cents(order.total) and (
        not currency or str(currency).lower() == str(order.currency).lower()
    ):
        return
    charged = money(charged_cents / 100, str(currency or order.currency))
    db.add(
        OrderEvent(
            order_id=order.id,
            status=order.status,
            comment=f"The provider charged {charged} for an order of "
            f"{money(float(order.total), order.currency)} — check this payment",
        )
    )
    notify_admin(
        db,
        kind="order",
        title=f"Payment amount mismatch on order {order.order_number}",
        body=f"Charged {charged}; the order total is {money(float(order.total), order.currency)}.",
        link=_order_link(order),
        background=background,
    )
    db.commit()
    logger.error("order %s: charged amount differs from the order total", order.order_number)


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
    reference = session.get("id") or f"order-{order.id}-{intent}"

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
        ledger.settle(db, order, provider="stripe", reference=reference, payment_id=intent)
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
            link=_order_link(order),
            background=background,
        )
        db.commit()
        logger.warning(
            "order %s: card payment %s landed on an order already marked paid by hand",
            order.order_number,
            intent,
        )
        return

    # The extra payment gets its own ledger row — held for a moment, then
    # sent back in full — so the order's own payment is never the one the
    # refund is booked against.
    extra = ledger.settle(db, order, provider="stripe", reference=reference, payment_id=intent)
    amount = session.get("amount_total")
    if type(amount) is int and amount > 0:
        extra.amount = ledger.money(Decimal(amount) / 100)
    refund = payment_ops.issue_refund(
        db,
        order,
        extra,
        ledger.cents(ledger.refundable(db, extra)),
        reason="Duplicate payment",
        initiated_by="duplicate payment on a replaced pay link",
        sync_order=False,
    )
    if refund.status == "failed":
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
        link=_order_link(order),
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
    closed_as: str = TransactionStatus.EXPIRED.value,
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
    order = _lock_for_cancellation(order, db)
    if order is None:
        return  # the row vanished between the lookup and the lock
    session_id = session.get("id")
    if session_id:
        # Whatever becomes of the order, this page is over.
        ledger.close_one(db, "stripe", session_id, closed_as, detail=comment)
    if (
        order.status == OrderStatus.CANCELLED.value
        or (order.stripe_session_id and session_id != order.stripe_session_id)
        or order.payment_status != PaymentStatus.PENDING.value
    ):
        # Already cancelled; or a newer pay link superseded this session;
        # or paid after all (raced with the completed event) — leave it.
        db.commit()
        return
    # The session is already over — there is no pay page left to close.
    _cancel_order(order, db, by_customer=False, comment=comment, close_pay_page=False)
    ledger.close_open(db, order, closed_as, detail=comment)
    db.commit()
    if background is not None:
        # They asked for the bake and walked away from the pay page — the
        # honest email says the window closed and the box went back, so
        # they're never left wondering where the order went.
        queue_status_email(order, background)
    logger.info("order %s cancelled: its pay page ended unpaid", order.order_number)


# --------------------------------------------------------------------------
# Stripe: money the dashboard moves
# --------------------------------------------------------------------------

def _order_for_intent(charge: dict, db) -> Order | None:
    """The order a charge belongs to, by the payment intent the checkout
    session left behind — on the order, or on any ledger row (a duplicate
    payment that was refunded has an intent of its own). None means Stripe
    knows a charge this book doesn't — logged, never guessed."""
    intent = str(charge.get("payment_intent") or "")
    if not intent:
        return None
    order = db.scalar(select(Order).where(Order.stripe_payment_intent_id == intent))
    if order is None:
        tx = ledger.find(db, "stripe", payment_id=intent)
        if tx is not None:
            order = db.get(Order, tx.order_id)
    return order


def _record_external_refund(charge: dict, db) -> None:
    """Stripe says money went back on a charge — bring the book in line.

    The app's own refunds are already in the ledger when this arrives
    (same total, nothing to add). What this catches is the human path — a
    refund issued in the Stripe dashboard — which without it would leave
    an order claiming money it has already returned. The ledger makes each
    one visible: whatever Stripe's refunded total exceeds the ledger's by
    is written down as a refund made at Stripe.
    """
    order = _order_for_intent(charge, db)
    if order is None:
        logger.warning(
            "Stripe refund for unknown payment intent %s — no order touched",
            charge.get("payment_intent"),
        )
        return
    order = _lock_for_cancellation(order, db)
    if order is None:
        return
    try:
        refunded = int(charge.get("amount_refunded") or 0)
        charged = int(charge.get("amount") or 0)
    except (TypeError, ValueError):
        return  # unreadable amounts are logged, never guessed from
    if charged <= 0:
        return
    intent = str(charge.get("payment_intent") or "")
    tx = ledger.find(db, "stripe", payment_id=intent) or ledger.adopt_order_payment(db, order)
    if tx is None:
        return
    paid_cents = ledger.cents(tx.amount)
    known = ledger.cents(ledger.confirmed_refund_total(db, tx))
    pending = ledger.cents(ledger.pending_refund_total(db, tx))
    if pending and refunded >= known + pending:
        # Refunds the shop asked for and Stripe had still to confirm are
        # inside this total — this event is their confirmation.
        ledger.confirm_pending_refunds(db, tx)
        known += pending
    # Stripe's own figures decide: a charge refunded in full means whatever
    # was still held has gone back; otherwise the part its total exceeds
    # the ledger's by. Never more than the payment itself.
    if refunded >= charged:
        new_cents = paid_cents - known
    else:
        new_cents = min(refunded - known, paid_cents - known)
    if new_cents > 0:
        ledger.record_refund(
            db,
            tx,
            amount=Decimal(new_cents) / 100,
            status="succeeded",
            initiated_by="Stripe dashboard",
            reason="Refund issued at Stripe",
        )
    elif not pending:
        db.commit()  # nothing new — the app's own refund, arriving as an event
        return
    holds_order_money = intent == (order.stripe_payment_intent_id or "")
    if holds_order_money and order.payment_status in (
        PaymentStatus.PAID.value,
        PaymentStatus.PARTIALLY_REFUNDED.value,
    ):
        payment_ops.sync_order_payment_status(order, tx)
        word = (
            "refunded" if order.payment_status == PaymentStatus.REFUNDED.value else "partially refunded"
        )
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment=f"Refund recorded from the Stripe dashboard — payment marked {word}",
            )
        )
        logger.info("order %s payment marked %s by a dashboard refund", order.order_number, word)
    db.commit()


def _record_refund_update(refund: dict, background: BackgroundTasks, db) -> None:
    """A refund the shop already knows changed its mind at Stripe — most
    importantly, one that *failed* after first being accepted (the bank
    sent it back). The money did not reach the customer: the ledger stops
    counting it, the order stops saying it was refunded, and the desk is
    told, because someone now has to return that money another way."""
    refund_id = str(refund.get("id") or "")
    if not refund_id:
        return
    from ..models import PaymentRefund

    row = db.scalar(
        select(PaymentRefund).where(
            PaymentRefund.provider == "stripe", PaymentRefund.provider_refund_id == refund_id
        )
    )
    if row is None:
        return  # not a refund this book issued or recorded by id
    order = db.get(Order, row.order_id)
    order = _lock_for_cancellation(order, db) if order is not None else None
    tx = db.get(PaymentTransaction, row.transaction_id)
    if order is None or tx is None:
        return
    state = payment_ops.refund_state("stripe", refund.get("status"))
    if state == row.status:
        db.commit()
        return
    was = row.status
    ledger.record_refund(
        db,
        tx,
        amount=row.amount,
        status=state,
        provider_refund_id=refund_id,
        initiated_by=row.initiated_by,
        failure_reason=str(refund.get("failure_reason") or "") or None,
    )
    if tx.provider_payment_id == order.stripe_payment_intent_id:
        payment_ops.sync_order_payment_status(order, tx)
    amount = money(float(row.amount), order.currency)
    if state == "failed":
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment=f"A refund of {amount} failed at Stripe — the money did not go back",
            )
        )
        notify_admin(
            db,
            kind="order",
            title=f"Refund failed on order {order.order_number}",
            body=f"{amount} could not be returned to the customer — settle it another way.",
            link=_order_link(order),
            background=background,
        )
    elif state == "succeeded" and was == "pending":
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment=f"Stripe confirmed the refund of {amount}",
            )
        )
    db.commit()


def _record_dispute(dispute: dict, background: BackgroundTasks, db) -> None:
    """A customer disputed a charge with their bank — the desk must hear
    this the moment it happens, not at the next reconciliation.

    Disputes change nothing here by themselves (Stripe holds the money and
    the case is answered in Stripe's dashboard within their deadline); the
    bell, the email and the timeline entry exist so the deadline is never
    discovered after it passed."""
    order = _order_for_intent(dispute, db)
    if order is None:
        logger.warning(
            "dispute on unknown payment intent %s", dispute.get("payment_intent")
        )
        notify_admin(
            db,
            kind="order",
            title="Card dispute opened — no matching order found",
            body=f"Payment intent {dispute.get('payment_intent')}",
            background=background,
        )
        db.commit()
        return
    tx = ledger.find(db, "stripe", payment_id=str(dispute.get("payment_intent") or ""))
    tx = tx or ledger.adopt_order_payment(db, order)
    if tx is not None:
        tx.dispute_status = "open"
        ledger.set_status(
            db, tx, TransactionStatus.DISPUTED.value, event_type="dispute opened",
            detail=str(dispute.get("reason") or "") or None,
        )
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
        link=_order_link(order),
        background=background,
    )
    db.commit()


def _close_dispute(
    order: Order, tx: PaymentTransaction, *, won: bool, provider_label: str, dispute_id: str | None,
    amount, background: BackgroundTasks, db,
) -> None:
    """A dispute ended. Won: the payment stands again. Lost: the bank has
    taken the money back — the ledger books it as returned (it is), the
    order stops saying it was paid, and the desk is told either way."""
    if tx.dispute_status in ("won", "lost"):
        return  # already closed — a redelivered event
    tx.dispute_status = "won" if won else "lost"
    ledger.set_status(
        db, tx, TransactionStatus.SUCCEEDED.value, event_type=f"dispute {tx.dispute_status}"
    )
    if won:
        ledger.apply_refunds(db, tx)
        comment = f"Dispute closed in the shop's favour — the {provider_label} payment stands"
        title = f"Dispute won on order {order.order_number}"
    else:
        lost = min(ledger.money(amount if amount is not None else tx.amount), ledger.refundable(db, tx))
        if lost > 0:
            ledger.record_refund(
                db,
                tx,
                amount=lost,
                status="succeeded",
                provider_refund_id=dispute_id,
                initiated_by="chargeback (dispute lost)",
                reason="The customer's bank reversed the payment",
            )
        payment_ops.sync_order_payment_status(order, tx)
        comment = (
            f"Dispute lost — the customer's bank took back {money(float(lost), order.currency)}"
        )
        title = f"Dispute lost on order {order.order_number}"
    db.add(OrderEvent(order_id=order.id, status=order.status, comment=comment))
    notify_admin(
        db, kind="order", title=title, body=comment, link=_order_link(order), background=background
    )


def _record_dispute_closed(dispute: dict, background: BackgroundTasks, db) -> None:
    outcome = str(dispute.get("status") or "")
    if outcome not in ("won", "lost"):
        return  # warning_closed, under_review… — nothing the book acts on
    order = _order_for_intent(dispute, db)
    if order is None:
        return
    order = _lock_for_cancellation(order, db)
    tx = ledger.find(db, "stripe", payment_id=str(dispute.get("payment_intent") or ""))
    if order is None or tx is None:
        return
    amount = dispute.get("amount")
    _close_dispute(
        order,
        tx,
        won=outcome == "won",
        provider_label="card",
        dispute_id=str(dispute.get("id") or "") or None,
        amount=(Decimal(amount) / 100) if type(amount) is int else None,
        background=background,
        db=db,
    )
    db.commit()


def _handle_stripe_event(event: dict, background: BackgroundTasks, db) -> None:
    obj = event.get("data", {}).get("object")
    if not isinstance(obj, dict):
        obj = {}  # a malformed event is ignored, never a 500
    event_type = event.get("type")
    event_id = event.get("id") if isinstance(event.get("id"), str) else None
    if not ledger.claim_event(db, "stripe", event_id, event_type):
        logger.info("Stripe event %s (%s) already handled — ignoring the redelivery", event_id, event_type)
        return
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        _record_completed_payment(obj, background, db)
    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_expired"):
        _cancel_expired_session(obj, db, background)
    elif event_type == "checkout.session.async_payment_failed":
        # A delayed payment failed after the redirect. The expiry path's own
        # guards decide (a paid order stays paid, a newer pay link wins), so
        # the box goes back the moment Stripe says the money won't come —
        # not hours later at the session's own expiry.
        _cancel_expired_session(
            obj,
            db,
            background,
            comment="Payment failed — cancelled automatically",
            closed_as=TransactionStatus.FAILED.value,
        )
    elif event_type == "charge.refunded":
        _record_external_refund(obj, db)
    elif event_type in ("refund.updated", "refund.failed", "charge.refund.updated"):
        _record_refund_update(obj, background, db)
    elif event_type == "charge.dispute.created":
        _record_dispute(obj, background, db)
    elif event_type == "charge.dispute.closed":
        _record_dispute_closed(obj, background, db)
    else:
        logger.info("ignoring Stripe event %s", event_type)
    # Events that changed nothing still leave their claim on record.
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

    # DB work off the event loop — the endpoint is async only for the raw body.
    await run_in_threadpool(_handle_stripe_event, event, background, db)
    return {"received": True}


# --------------------------------------------------------------------------
# PayPal
# --------------------------------------------------------------------------

def _is_current_paypal_page(order: Order, tx: PaymentTransaction, db) -> bool:
    """Whether this PayPal order is the one the shop means to be paid: its
    order still waits for money, and no newer pay link replaced it."""
    if order.status == OrderStatus.CANCELLED.value:
        return False
    if order.payment_status != PaymentStatus.PENDING.value:
        return False
    current = ledger.open_for_order(db, order.id, "paypal")
    return current is not None and current.id == tx.id


def _land_paypal(order: Order, tx: PaymentTransaction, remote: dict, background, db) -> str:
    """Put what PayPal says about a (possibly captured) order on the book.
    `order` is locked and freshly read. Returns one word for the caller."""
    capture_status = str(remote.get("capture_status") or "")
    capture_id = remote.get("capture_id")
    if not capture_id:
        return "awaiting"
    if capture_status == "COMPLETED":
        if order.status == OrderStatus.CANCELLED.value:
            # Captured in the same breath the order was cancelled — the
            # only way PayPal money can land on a cancelled order. Back it goes.
            refund_payment_on_cancelled_order(
                order, db, background, provider="paypal",
                reference=tx.provider_reference, payment_id=capture_id,
            )
            return "refunded"
        if mark_order_paid(
            order, db, background, provider="paypal",
            reference=tx.provider_reference, payment_id=capture_id,
        ):
            logger.info("order %s paid via PayPal capture %s", order.order_number, capture_id)
            captured = remote.get("capture_amount")
            _flag_amount_mismatch(
                order,
                ledger.cents(captured) if captured is not None else None,
                remote.get("capture_currency"),
                background,
                db,
            )
        return "paid"
    if capture_status == "PENDING":
        # PayPal is holding the capture (a review, an eCheck). Not paid
        # until PayPal says so — its webhook, or the sweep, finishes this.
        if not tx.provider_payment_id:
            tx.provider_payment_id = capture_id
        ledger.mark_processing(db, tx, detail="PayPal is reviewing the payment")
        db.commit()
        return "processing"
    if capture_status in ("DECLINED", "FAILED"):
        ledger.close_one(
            db, "paypal", tx.provider_reference, TransactionStatus.FAILED.value,
            detail="PayPal declined the payment",
        )
        db.commit()
        return "failed"
    return "awaiting"


def _capture_paypal(order: Order, tx: PaymentTransaction, background, db) -> str:
    """Ask PayPal to take the money the customer approved. Only ever for
    the order's current pay page while it still waits to be paid — which
    is what keeps a cancelled or replaced PayPal order from being charged."""
    if not _is_current_paypal_page(order, tx, db):
        return "not current"
    try:
        remote = paypal_gateway.capture_order(tx.provider_reference)
    except paypal_gateway.PayPalRefused as refusal:
        if refusal.issue in ("ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED"):
            return "awaiting"  # the customer has not finished on PayPal's side
        if refusal.issue == "INSTRUMENT_DECLINED":
            tx.failure_reason = "PayPal could not charge the chosen funding source"
            db.commit()
            return "declined"  # the same PayPal page lets them pick another
        raise
    return _land_paypal(order, tx, remote, background, db)


def _reconcile_paypal(order: Order, background, db) -> str:
    """Ask PayPal where this order's payment stands and land whatever is
    true. `order` is locked and freshly read."""
    tx = ledger.open_for_order(db, order.id, "paypal")
    if tx is None:
        return "nothing open"
    remote = paypal_gateway.get_order(tx.provider_reference)
    state = str(remote.get("status") or "")
    if state == "COMPLETED":
        return _land_paypal(order, tx, remote, background, db)
    if state == "APPROVED":
        return _capture_paypal(order, tx, background, db)
    if state == "VOIDED":
        ledger.close_one(db, "paypal", tx.provider_reference, TransactionStatus.EXPIRED.value)
        db.commit()
        return "expired"
    return "awaiting"


def _paypal_transaction(resource: dict, db, *, capture_id: str | None = None,
                        paypal_order_id: str | None = None) -> PaymentTransaction | None:
    """The ledger row a PayPal webhook is about — by the capture it names,
    by the PayPal order it names, or (a capture event) by the order id
    PayPal lists among the capture's related ids."""
    if capture_id:
        tx = ledger.find(db, "paypal", payment_id=capture_id)
        if tx is not None:
            return tx
    related = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    for reference in (paypal_order_id, related):
        if reference:
            tx = ledger.find(db, "paypal", reference=str(reference))
            if tx is not None:
                return tx
    return None


def _up_link_id(resource: dict) -> str | None:
    """The capture a refund belongs to — PayPal says it with an "up" link."""
    for link in resource.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "up" and link.get("href"):
            return str(link["href"]).rstrip("/").rsplit("/", 1)[-1]
    return None


def _handle_paypal_event(event: dict, background: BackgroundTasks, db) -> None:
    resource = event.get("resource")
    if not isinstance(resource, dict):
        resource = {}
    event_type = str(event.get("event_type") or "")
    event_id = event.get("id") if isinstance(event.get("id"), str) else None
    if not ledger.claim_event(db, "paypal", event_id, event_type):
        logger.info("PayPal event %s (%s) already handled — ignoring the redelivery", event_id, event_type)
        return

    if event_type == "CHECKOUT.ORDER.APPROVED":
        # Approved on PayPal's page; the customer may never come back to
        # the shop's (a closed tab). The capture is the shop's to ask for.
        tx = _paypal_transaction(resource, db, paypal_order_id=str(resource.get("id") or ""))
        order = db.get(Order, tx.order_id) if tx is not None else None
        order = _lock_for_cancellation(order, db) if order is not None else None
        if order is not None:
            ledger.link_event(db, "paypal", event_id, transaction=tx)
            _capture_paypal(order, tx, background, db)

    elif event_type in (
        "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.PENDING",
        "PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED",
    ):
        capture_id = str(resource.get("id") or "")
        tx = _paypal_transaction(resource, db, capture_id=capture_id)
        order = db.get(Order, tx.order_id) if tx is not None else None
        order = _lock_for_cancellation(order, db) if order is not None else None
        if order is None:
            logger.warning("PayPal capture %s matches no order — nothing touched", capture_id)
        else:
            ledger.link_event(db, "paypal", event_id, transaction=tx)
            amount = (resource.get("amount") or {})
            remote = {
                "capture_id": capture_id,
                "capture_status": {
                    "PAYMENT.CAPTURE.COMPLETED": "COMPLETED",
                    "PAYMENT.CAPTURE.PENDING": "PENDING",
                }.get(event_type, "DECLINED"),
                "capture_amount": Decimal(str(amount["value"])) if amount.get("value") else None,
                "capture_currency": str(amount.get("currency_code") or "").lower() or None,
            }
            _land_paypal(order, tx, remote, background, db)

    elif event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        _record_paypal_refund(resource, event_id, background, db)

    elif event_type in ("CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.RESOLVED"):
        _record_paypal_dispute(resource, event_type, event_id, background, db)

    else:
        logger.info("ignoring PayPal event %s", event_type)
    db.commit()


def _record_paypal_refund(resource: dict, event_id, background, db) -> None:
    """PayPal says money went back on a capture. A refund the shop issued
    is already in the ledger under PayPal's id (this confirms it, if it
    was still pending); one made in PayPal's own dashboard is written down
    here for the first time."""
    capture_id = _up_link_id(resource)
    tx = ledger.find(db, "paypal", payment_id=capture_id) if capture_id else None
    order = db.get(Order, tx.order_id) if tx is not None else None
    order = _lock_for_cancellation(order, db) if order is not None else None
    if order is None:
        logger.warning("PayPal refund for unknown capture %s — no order touched", capture_id)
        return
    ledger.link_event(db, "paypal", event_id, transaction=tx)
    refund_id = str(resource.get("id") or "") or None
    amount = (resource.get("amount") or {}).get("value")
    if amount is None:
        return
    from ..models import PaymentRefund

    known = (
        db.scalar(
            select(PaymentRefund).where(
                PaymentRefund.provider == "paypal", PaymentRefund.provider_refund_id == refund_id
            )
        )
        if refund_id
        else None
    )
    was_status = order.payment_status
    ledger.record_refund(
        db,
        tx,
        amount=Decimal(str(amount)),
        status="succeeded",
        provider_refund_id=refund_id,
        initiated_by=known.initiated_by if known is not None else "PayPal dashboard",
        reason=None if known is not None else "Refund issued at PayPal",
    )
    payment_ops.sync_order_payment_status(order, tx)
    if known is None or order.payment_status != was_status:
        word = (
            "refunded" if order.payment_status == PaymentStatus.REFUNDED.value else "partially refunded"
        )
        source = "PayPal confirmed the refund" if known is not None else "Refund recorded from PayPal"
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment=f"{source} — payment marked {word}",
            )
        )


def _record_paypal_dispute(resource: dict, event_type: str, event_id, background, db) -> None:
    disputed = resource.get("disputed_transactions") or [{}]
    capture_id = str((disputed[0] or {}).get("seller_transaction_id") or "")
    tx = ledger.find(db, "paypal", payment_id=capture_id) if capture_id else None
    order = db.get(Order, tx.order_id) if tx is not None else None
    order = _lock_for_cancellation(order, db) if order is not None else None
    if order is None:
        notify_admin(
            db,
            kind="order",
            title="PayPal dispute opened — no matching order found",
            body=f"Dispute {resource.get('dispute_id')} on capture {capture_id or 'unknown'}",
            background=background,
        )
        return
    ledger.link_event(db, "paypal", event_id, transaction=tx)
    if event_type == "CUSTOMER.DISPUTE.CREATED":
        if tx.dispute_status == "open":
            return
        tx.dispute_status = "open"
        ledger.set_status(
            db, tx, TransactionStatus.DISPUTED.value, event_type="dispute opened",
            detail=str(resource.get("reason") or "") or None,
        )
        db.add(
            OrderEvent(
                order_id=order.id,
                status=order.status,
                comment="PayPal dispute opened — answer it in PayPal's Resolution Centre before its deadline",
            )
        )
        notify_admin(
            db,
            kind="order",
            title=f"PayPal dispute on order {order.order_number}",
            body="Answer it in PayPal's Resolution Centre — deadlines are short and evidence is everything",
            link=_order_link(order),
            background=background,
        )
        return
    outcome = str((resource.get("dispute_outcome") or {}).get("outcome_code") or "")
    if not outcome:
        return
    amount = (resource.get("dispute_amount") or {}).get("value")
    _close_dispute(
        order,
        tx,
        won=outcome in ("RESOLVED_SELLER_FAVOUR", "CANCELED_BY_BUYER", "DENIED"),
        provider_label="PayPal",
        dispute_id=str(resource.get("dispute_id") or "") or None,
        amount=Decimal(str(amount)) if amount is not None else None,
        background=background,
        db=db,
    )


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request, background: BackgroundTasks, db: DbSession):
    """PayPal's word on a payment — accepted only once PayPal itself has
    confirmed the delivery is genuine (its verification API is asked about
    the exact bytes and headers that arrived)."""
    payload = await request.body()
    try:
        event = await run_in_threadpool(paypal_gateway.verify_webhook, request.headers, payload)
    except paypal_gateway.BadSignature as exc:
        raise HTTPException(status_code=400, detail=f"Webhook rejected: {exc}") from None
    try:
        await run_in_threadpool(_handle_paypal_event, event, background, db)
    except paypal_gateway.GatewayUnavailable:
        # PayPal could not be asked something this event needed (a capture,
        # say). Nothing was committed — the claim on the event included —
        # so PayPal's retry gets a clean second go.
        raise HTTPException(status_code=503, detail="PayPal did not answer — retry later") from None
    return {"received": True}


# --------------------------------------------------------------------------
# Reconciliation — the shop asking, instead of waiting to be told
# --------------------------------------------------------------------------

def reconcile_order(order: Order, background: BackgroundTasks, db) -> str:
    """Ask this order's provider where its payment stands, right now, and
    land whatever is true: paid, approved-and-capturable, lapsed, or still
    waiting. The single door behind the customer's return page, the desk's
    "check with the provider" button and the sweep.

    Raises the provider gateway's GatewayUnavailable when it can't be
    reached — callers decide whether that is a 503 or a "next round".
    """
    provider = ledger.provider_for(order)
    if provider is None or order.payment_status != PaymentStatus.PENDING.value:
        return "nothing to do"
    if provider == "paypal":
        locked = _lock_for_cancellation(order, db)
        return _reconcile_paypal(locked, background, db) if locked is not None else "gone"
    if not order.stripe_session_id:
        return "nothing open"
    session = stripe_gateway.retrieve_session(order.stripe_session_id)
    if session.get("payment_status") == "paid":
        # The webhook may have landed while retrieve_session was on the
        # wire — judge on a locked, freshly refreshed row, the same re-read
        # the webhook itself takes, so exactly one of the two records the
        # payment and sends the receipt.
        locked = _lock_for_cancellation(order, db)
        if locked is None:
            return "gone"
        _land_payment(
            locked,
            {
                "id": order.stripe_session_id,
                "payment_status": "paid",
                "payment_intent": session.get("payment_intent"),
                "amount_total": session.get("amount_total"),
                "currency": session.get("currency"),
            },
            background,
            db,
        )
        return "paid"
    if session.get("status") == "expired":
        return "expired"
    return "awaiting"


def reconcile_stale_card_orders() -> None:
    """Self-heal for webhook outages — every online rail, despite the name
    (it predates PayPal and other modules call it by it).

    An online order still pending well past its pay-by window means the
    provider's word never landed — endpoint moved in the dashboard, a
    domain cutover missed, retries given up. Each one is checked against
    its provider directly: paid orders get their paid transition and
    receipt, lapsed ones cancel with the stock returned — the same
    outcomes the webhook would have delivered. A provider being
    unreachable is not an error here — the next round tries again.
    """
    if not (stripe_gateway.configured() or paypal_gateway.configured()):
        return
    cutoff = utcnow() - PAY_WINDOW
    db = SessionLocal()
    try:
        stale = db.scalars(
            select(Order)
            .where(
                Order.payment_method.in_(list(PAYMENT_PROVIDERS)),
                Order.payment_status == PaymentStatus.PENDING.value,
                Order.status == OrderStatus.PENDING.value,
                Order.created_at < cutoff,
            )
        ).all()
        for order in stale:
            provider = PAYMENT_PROVIDERS[order.payment_method]
            if provider == "stripe" and not (stripe_gateway.configured() and order.stripe_session_id):
                continue
            if provider == "paypal" and not paypal_gateway.configured():
                continue
            background = BackgroundTasks()
            try:
                outcome = reconcile_order(order, background, db)
            except (stripe_gateway.GatewayUnavailable, paypal_gateway.GatewayUnavailable):
                logger.warning(
                    "reconciliation sweep: %s unavailable — resuming on the next round", provider
                )
                db.rollback()
                if provider == "stripe":
                    return
                continue
            try:
                if outcome == "paid":
                    logger.info(
                        "order %s marked paid by the reconciliation sweep (the webhook never landed)",
                        order.order_number,
                    )
                elif provider == "stripe" and outcome == "expired":
                    # The expiry webhook's own guards, reused verbatim. The
                    # customer email rides the sweep's own drain — no
                    # response will carry these tasks.
                    _cancel_expired_session({"id": order.stripe_session_id}, db, background)
                elif provider == "paypal" and outcome in ("awaiting", "expired", "nothing open", "failed"):
                    # Past the window and PayPal holds no approval: the
                    # order lapses like an expired card session would.
                    locked = _lock_for_cancellation(order, db)
                    if (
                        locked is not None
                        and locked.status == OrderStatus.PENDING.value
                        and locked.payment_status == PaymentStatus.PENDING.value
                    ):
                        _cancel_order(
                            locked, db, by_customer=False,
                            comment="Payment window expired — cancelled automatically",
                            close_pay_page=False,
                        )
                        ledger.close_open(db, locked, TransactionStatus.EXPIRED.value)
                        db.commit()
                        queue_status_email(locked, background)
                _drain(background)
            except Exception:
                logger.exception(
                    "reconciliation sweep: order %s could not be reconciled",
                    order.order_number,
                )
                db.rollback()
    finally:
        db.close()


# --------------------------------------------------------------------------
# The customer's side
# --------------------------------------------------------------------------

@router.post("/confirm/{order_id}", response_model=OrderDetailOut)
def confirm_payment(order_id: int, user: CurrentUser, db: DbSession, background: BackgroundTasks):
    """The customer is back from the pay page: reconcile the order with its
    provider right now, so what they read next is true even when the
    webhook is still in flight. For PayPal this is also where the approved
    payment is captured.

    A cancelled order is checked too: its pay page may have outlived it,
    and money that landed there must be sent back, not left unseen until
    (or unless) the webhook arrives.
    """
    order = _get_order_for(order_id, user, db)
    if order.payment_status == PaymentStatus.PENDING.value and ledger.provider_for(order):
        try:
            reconcile_order(order, background, db)
        except (stripe_gateway.GatewayUnavailable, paypal_gateway.GatewayUnavailable):
            raise HTTPException(
                status_code=503,
                detail="The payment provider didn't answer — check back in a moment",
            )
        order = _get_order_for(order_id, user, db)
        db.refresh(order)
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
    provider = ledger.provider_for(order)
    if provider is None:
        raise HTTPException(status_code=400, detail="This order isn't paid online")
    if order.status == OrderStatus.CANCELLED.value or order.payment_status != PaymentStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="This order has no payment to complete")
    if not payment_ops.available(order.payment_method):
        raise HTTPException(
            status_code=400, detail="This way of paying isn't available right now — please contact the bakery"
        )
    # The same fail-closed gate as checkout: while the shop's delivery
    # pricing is unapproved, no new pay page is minted for any order, even
    # one placed when the rules were live. The stored totals are never
    # recalculated — this only decides whether charging may resume.
    _approved_money_rules()

    def paid_already():
        fresh = _get_order_for(order_id, user, db)
        db.refresh(fresh)
        response.status_code = status.HTTP_200_OK
        return _serialize(fresh, detailed=True, cancel_fee_preview=_late_cancel_fee(fresh, db))

    superseded = order.stripe_session_id if provider == "stripe" else None
    # Whether the old Stripe page should be closed afterwards: yes unless
    # Stripe itself says it is already over.
    superseded_open = bool(superseded)
    if provider == "stripe" and superseded:
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
            _land_payment(
                locked,
                {"id": superseded, "payment_status": "paid",
                 "payment_intent": previous.get("payment_intent"),
                 "amount_total": previous.get("amount_total"),
                 "currency": previous.get("currency")},
                background,
                db,
            )
            return paid_already()
        superseded_open = previous.get("status") in (None, "open")

    if provider == "paypal":
        current = ledger.open_for_order(db, order.id, "paypal")
        if current is not None:
            try:
                locked = _lock_for_cancellation(order, db)
                outcome = _reconcile_paypal(locked, background, db) if locked is not None else "gone"
            except paypal_gateway.GatewayUnavailable:
                raise HTTPException(
                    status_code=503,
                    detail="The payment provider didn't answer — please try again",
                )
            if outcome in ("paid", "processing", "refunded", "gone"):
                return paid_already()
            if outcome in ("awaiting", "declined") and current.checkout_url:
                # The PayPal page they already have is still good — hand it
                # back rather than minting a second PayPal order.
                response.status_code = status.HTTP_200_OK
                order = _get_order_for(order_id, user, db)
                return _serialize(
                    order, detailed=True, payment_url=current.checkout_url,
                    cancel_fee_preview=_late_cancel_fee(order, db),
                )
            order = _get_order_for(order_id, user, db)

    try:
        payment_url = payment_ops.open_pay_page(db, order, payment_ops.public_base(request))
    except payment_ops.UnchargeableCurrency:
        # A historical order whose stored currency can't be charged must be
        # refused untouched — never cancelled, never relabelled. The desk
        # settles it by hand; the customer is told honestly.
        raise HTTPException(
            status_code=400,
            detail="This order's currency can no longer be charged — we'll contact you to settle it",
        )
    except payment_ops.PayPageUnavailable:
        raise HTTPException(
            status_code=503,
            detail="The payment provider didn't answer — please try again",
        )
    # The page just opened is the only one that may still be paid.
    fresh_tx = ledger.open_for_order(db, order.id, provider)
    ledger.close_open(
        db, order, TransactionStatus.CANCELLED.value,
        keep_reference=fresh_tx.provider_reference if fresh_tx is not None else None,
        detail="replaced by a newer pay link",
    )
    db.commit()
    db.refresh(order)
    # Only now — with the fresh link committed — is the old Stripe page
    # closed. The order matters: closing it first would let Stripe's expiry
    # event arrive while the order still pointed at that session, and the
    # expiry path would cancel the very order being paid. Committed first,
    # the event finds a superseded session and is ignored. Best effort: if
    # the old page cannot be closed and is paid anyway, the webhook refunds
    # the second payment (_settle_extra_payment).
    if superseded_open:
        stripe_gateway.expire_session(superseded)
    return _serialize(
        order, detailed=True, payment_url=payment_url, cancel_fee_preview=_late_cancel_fee(order, db)
    )
