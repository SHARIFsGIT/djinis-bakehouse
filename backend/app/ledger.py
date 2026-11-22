"""The payment ledger's own rules — the one place that writes
payment_transactions, payment_refunds and payment_events.

The ledger records; it never talks to a provider. Routers and
payment_ops decide what happened (asking Stripe or PayPal), then say so
here, and every change of state leaves a payment_events row behind it —
who knows what a payment's story was is whoever reads that table.

Nothing here commits: each function works inside the caller's transaction,
so a ledger entry exists if and only if the order change it describes does.
Sessions run with autoflush off, hence the explicit flushes before a read
that must see this transaction's own writes.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .database import utcnow
from .models import (
    OPEN_TRANSACTION_STATUSES,
    PAYMENT_PROVIDERS,
    SETTLED_TRANSACTION_STATUSES,
    Order,
    PaymentEvent,
    PaymentMethod,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """An exact two-place Decimal — floats never reach a ledger column."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def cents(value) -> int:
    return int(money(value) * 100)


# --------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------

def audit(
    db,
    *,
    provider: str,
    event_type: str,
    order_id: int | None = None,
    transaction: PaymentTransaction | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: str | None = None,
) -> None:
    """One line in the payment audit trail (no provider event id — this is
    the shop writing down what it did)."""
    db.add(
        PaymentEvent(
            provider=provider,
            event_type=event_type[:80],
            order_id=order_id if order_id is not None else (transaction.order_id if transaction else None),
            transaction_id=transaction.id if transaction is not None else None,
            from_status=from_status,
            to_status=to_status,
            detail=(detail or None) and detail[:500],
        )
    )


def claim_event(db, provider: str, event_id: str | None, event_type: str | None) -> bool:
    """Put a provider's event on record — True if it is new, False if this
    exact event was already accepted (providers redeliver; an event must
    never act twice).

    The row joins the caller's transaction: if handling the event fails and
    rolls back, the claim rolls back with it and the provider's retry is
    treated as new — at-least-once delivery, exactly-once effect. Two
    deliveries racing each other are settled by the unique constraint: the
    loser's insert is refused and it stands down.

    An event with no id cannot be deduplicated here; the handlers' own
    state guards (they act only on a pending order) still hold for it.
    """
    if not event_id:
        return True
    already = db.scalar(
        select(PaymentEvent.id).where(
            PaymentEvent.provider == provider, PaymentEvent.event_id == event_id
        )
    )
    if already is not None:
        return False
    db.add(
        PaymentEvent(
            provider=provider, event_id=event_id[:255], event_type=str(event_type or "unknown")[:80]
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    return True


def link_event(db, provider: str, event_id: str | None, *, order_id=None, transaction=None, detail=None) -> None:
    """Attach what a claimed event turned out to concern — so the trail
    reads "this webhook → this order", not just "a webhook arrived"."""
    if not event_id:
        return
    row = db.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == provider, PaymentEvent.event_id == event_id
        )
    )
    if row is None:
        return
    if order_id is not None:
        row.order_id = order_id
    if transaction is not None:
        row.transaction_id = transaction.id
        row.order_id = row.order_id or transaction.order_id
    if detail:
        row.detail = detail[:500]


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

def find(db, provider: str, *, reference: str | None = None, payment_id: str | None = None):
    """A transaction by the provider's own id for its pay page (reference)
    or for the money (payment_id)."""
    query = select(PaymentTransaction).where(PaymentTransaction.provider == provider)
    if reference is not None:
        query = query.where(PaymentTransaction.provider_reference == reference)
    elif payment_id is not None:
        query = query.where(PaymentTransaction.provider_payment_id == payment_id)
    else:
        return None
    return db.scalar(query)


def for_order(db, order_id: int) -> list[PaymentTransaction]:
    db.flush()
    return list(
        db.scalars(
            select(PaymentTransaction)
            .where(PaymentTransaction.order_id == order_id)
            .order_by(PaymentTransaction.id)
        )
    )


def open_for_order(db, order_id: int, provider: str | None = None) -> PaymentTransaction | None:
    """The newest pay page of this order that could still take money."""
    for tx in reversed(for_order(db, order_id)):
        if tx.status in OPEN_TRANSACTION_STATUSES and (provider is None or tx.provider == provider):
            return tx
    return None


def money_transaction(db, order: Order) -> PaymentTransaction | None:
    """The transaction that holds this order's money — the one a refund
    goes back against. For a Stripe order that is the row carrying the
    order's own payment intent; otherwise the newest settled row that is
    not already fully refunded (an extra payment that was sent straight
    back is settled too, but it holds nothing)."""
    return pick_money_transaction(order, for_order(db, order.id))


def pick_money_transaction(order: Order, rows) -> PaymentTransaction | None:
    """money_transaction's rule, over ledger rows the caller already holds
    (oldest first) — for a report that reads every order at once."""
    settled = [tx for tx in rows if tx.status in SETTLED_TRANSACTION_STATUSES]
    if order.payment_method == PaymentMethod.CARD.value and order.stripe_payment_intent_id:
        for tx in settled:
            if tx.provider_payment_id == order.stripe_payment_intent_id:
                return tx
        return None
    for tx in reversed(settled):
        if tx.status != TransactionStatus.REFUNDED.value:
            return tx
    return settled[-1] if settled else None


def story_transaction(db, order: Order) -> PaymentTransaction | None:
    """The transaction that says where this order's payment stands: the one
    holding its money; failing that, the pay page still open; failing that,
    the last one that closed."""
    held = money_transaction(db, order)
    if held is not None:
        return held
    rows = for_order(db, order.id)
    for tx in reversed(rows):
        if tx.status in OPEN_TRANSACTION_STATUSES:
            return tx
    return rows[-1] if rows else None


def open_transaction(
    db, order: Order, *, provider: str, reference: str, checkout_url: str | None = None
) -> PaymentTransaction:
    """Record that a pay page now exists for this order. Idempotent on the
    provider's reference — opening the same page twice is one row."""
    db.flush()
    tx = find(db, provider, reference=reference)
    if tx is not None:
        if checkout_url and not tx.checkout_url:
            tx.checkout_url = checkout_url
        return tx
    tx = PaymentTransaction(
        order_id=order.id,
        provider=provider,
        provider_reference=reference,
        amount=money(order.total),
        currency=str(order.currency or "").lower(),
        status=TransactionStatus.PENDING.value,
        refunded_amount=Decimal("0.00"),
        checkout_url=checkout_url,
    )
    db.add(tx)
    db.flush()
    audit(
        db,
        provider=provider,
        event_type="pay page opened",
        transaction=tx,
        to_status=tx.status,
        detail=f"{money(order.total)} {tx.currency}",
    )
    return tx


def set_status(
    db, tx: PaymentTransaction, to_status: str, *, event_type: str, detail: str | None = None
) -> bool:
    """Move a transaction, and write the move down. False when it was
    already there (a replayed event changes nothing and logs nothing)."""
    if tx.status == to_status:
        return False
    audit(
        db,
        provider=tx.provider,
        event_type=event_type,
        transaction=tx,
        from_status=tx.status,
        to_status=to_status,
        detail=detail,
    )
    tx.status = to_status
    return True


def settle(
    db,
    order: Order,
    *,
    provider: str,
    reference: str | None,
    payment_id: str | None,
    method_type: str | None = None,
) -> PaymentTransaction:
    """The provider says this pay page was paid. Finds (or, for an order
    placed before the ledger existed, creates) its row and marks it
    succeeded, with the provider's id for the money itself.

    A row already settled is left exactly as it stands — a replayed
    "paid" must never turn a refunded transaction back into a held one.
    """
    db.flush()
    tx = find(db, provider, payment_id=payment_id) if payment_id else None
    if tx is None and reference:
        tx = find(db, provider, reference=reference)
    if tx is None:
        tx = open_transaction(
            db, order, provider=provider, reference=reference or f"order-{order.id}-{payment_id}"
        )
    if method_type and not tx.method_type:
        tx.method_type = method_type[:40]
    if tx.status in SETTLED_TRANSACTION_STATUSES:
        return tx
    if payment_id and not tx.provider_payment_id:
        tx.provider_payment_id = payment_id
    tx.paid_at = tx.paid_at or utcnow()
    tx.failure_reason = None
    set_status(
        db, tx, TransactionStatus.SUCCEEDED.value, event_type="payment received",
        detail=payment_id,
    )
    return tx


def mark_processing(db, tx: PaymentTransaction, *, detail: str | None = None) -> None:
    """The customer has done their part; the provider is still settling
    (a bank debit, a pay-later decision, a held capture)."""
    if tx.status == TransactionStatus.PENDING.value:
        set_status(db, tx, TransactionStatus.PROCESSING.value, event_type="payment processing", detail=detail)


def close_open(
    db, order: Order, to_status: str, *, keep_reference: str | None = None, detail: str | None = None
) -> int:
    """Close every pay page of this order that could still take money —
    the order was cancelled, its window lapsed, or a newer link replaced
    them. `keep_reference` spares the one page that is meant to stay."""
    closed = 0
    for tx in for_order(db, order.id):
        if tx.status in OPEN_TRANSACTION_STATUSES and tx.provider_reference != keep_reference:
            if set_status(db, tx, to_status, event_type=f"pay page {to_status}", detail=detail):
                closed += 1
    return closed


def close_one(db, provider: str, reference: str, to_status: str, *, detail: str | None = None) -> None:
    tx = find(db, provider, reference=reference)
    if tx is not None and tx.status in OPEN_TRANSACTION_STATUSES:
        if to_status == TransactionStatus.FAILED.value and detail:
            tx.failure_reason = detail[:255]
        set_status(db, tx, to_status, event_type=f"pay page {to_status}", detail=detail)


def adopt_order_payment(db, order: Order) -> PaymentTransaction | None:
    """A ledger row for a Stripe payment that predates the ledger.

    Orders paid before these tables existed carry their trail on the order
    itself (the session and payment intent columns). The first time such
    an order needs a transaction — a refund, a dispute, the desk opening
    its payment history — one is written from exactly what the order
    already says. Nothing is invented: no intent on the order, no row.
    """
    if order.payment_method != PaymentMethod.CARD.value or not order.stripe_payment_intent_id:
        return None
    db.flush()
    existing = find(db, "stripe", payment_id=order.stripe_payment_intent_id)
    if existing is not None:
        return existing
    total = money(order.total)
    kept_fee = money(order.cancel_fee or 0)
    if order.payment_status == PaymentStatus.REFUNDED.value:
        status, refunded = TransactionStatus.REFUNDED.value, total
    elif order.payment_status == PaymentStatus.PARTIALLY_REFUNDED.value:
        status, refunded = TransactionStatus.PARTIALLY_REFUNDED.value, max(total - kept_fee, Decimal("0.00"))
    else:
        status, refunded = TransactionStatus.SUCCEEDED.value, Decimal("0.00")
    reference = order.stripe_session_id or f"order-{order.id}-{order.stripe_payment_intent_id}"
    tx = find(db, "stripe", reference=reference)
    if tx is None:
        tx = PaymentTransaction(
            order_id=order.id,
            provider="stripe",
            provider_reference=reference,
            amount=total,
            currency=str(order.currency or "").lower(),
        )
        db.add(tx)
    tx.provider_payment_id = order.stripe_payment_intent_id
    tx.status = status
    tx.refunded_amount = refunded
    tx.paid_at = tx.paid_at or order.updated_at
    db.flush()
    audit(
        db,
        provider="stripe",
        event_type="ledger row written from the order's own record",
        transaction=tx,
        to_status=status,
        detail="payment predates the ledger",
    )
    return tx


# --------------------------------------------------------------------------
# Refunds and disputes
# --------------------------------------------------------------------------

def confirmed_refund_total(db, tx: PaymentTransaction) -> Decimal:
    db.flush()
    total = db.scalar(
        select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
            PaymentRefund.transaction_id == tx.id, PaymentRefund.status == "succeeded"
        )
    )
    return money(total)


def pending_refund_total(db, tx: PaymentTransaction) -> Decimal:
    db.flush()
    total = db.scalar(
        select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
            PaymentRefund.transaction_id == tx.id, PaymentRefund.status == "pending"
        )
    )
    return money(total)


def provider_refund_count(db, tx: PaymentTransaction) -> int:
    """Refunds the provider is known to hold for this payment — confirmed
    or still on their way. Failed attempts are not among them."""
    db.flush()
    return int(
        db.scalar(
            select(func.count(PaymentRefund.id)).where(
                PaymentRefund.transaction_id == tx.id,
                PaymentRefund.status.in_(("succeeded", "pending")),
            )
        )
        or 0
    )


def refundable(db, tx: PaymentTransaction) -> Decimal:
    """What can still be sent back: the payment less every refund that is
    confirmed or still on its way."""
    remaining = money(tx.amount) - confirmed_refund_total(db, tx) - pending_refund_total(db, tx)
    return max(remaining, Decimal("0.00"))


def apply_refunds(db, tx: PaymentTransaction) -> None:
    """Bring the transaction's refunded total — and with it, its status —
    in line with the refunds the provider has confirmed. Only confirmed
    refunds count: a pending or failed one moves nothing."""
    total = confirmed_refund_total(db, tx)
    tx.refunded_amount = total
    if tx.status in (
        TransactionStatus.SUCCEEDED.value,
        TransactionStatus.PARTIALLY_REFUNDED.value,
        TransactionStatus.REFUNDED.value,
    ):
        if total >= money(tx.amount):
            target = TransactionStatus.REFUNDED.value
        elif total > 0:
            target = TransactionStatus.PARTIALLY_REFUNDED.value
        else:
            target = TransactionStatus.SUCCEEDED.value
        set_status(db, tx, target, event_type="refund total changed", detail=f"refunded {total}")


def record_refund(
    db,
    tx: PaymentTransaction,
    *,
    amount,
    status: str,
    initiated_by: str,
    provider_refund_id: str | None = None,
    reason: str | None = None,
    failure_reason: str | None = None,
    admin_user_id: int | None = None,
) -> PaymentRefund:
    """Write a refund down as the provider reported it. Idempotent on the
    provider's refund id: hearing about the same refund again updates the
    one row (a pending refund confirming, say) instead of adding a second.
    """
    db.flush()
    row = None
    if provider_refund_id:
        row = db.scalar(
            select(PaymentRefund).where(
                PaymentRefund.provider == tx.provider,
                PaymentRefund.provider_refund_id == provider_refund_id,
            )
        )
    if row is not None and row.transaction_id != tx.id:
        # A provider's refund id belongs to exactly one payment. If the book
        # already holds this id against another, something upstream is
        # wrong — the existing record is left exactly as it stands and the
        # oddity is written down, rather than one payment's refund being
        # quietly rewritten as another's.
        audit(
            db,
            provider=tx.provider,
            event_type="refund id already booked on another payment",
            transaction=tx,
            detail=f"{provider_refund_id} is recorded on transaction #{row.transaction_id}",
        )
        return row
    if row is None:
        row = PaymentRefund(
            transaction_id=tx.id,
            order_id=tx.order_id,
            provider=tx.provider,
            provider_refund_id=provider_refund_id,
            amount=money(amount),
            currency=tx.currency,
            status=status,
            reason=(reason or None) and reason[:255],
            failure_reason=(failure_reason or None) and failure_reason[:255],
            initiated_by=initiated_by[:150],
            admin_user_id=admin_user_id,
        )
        db.add(row)
        change = f"refund {status}"
    else:
        change = f"refund {row.status} -> {status}" if row.status != status else None
        row.status = status
        if failure_reason:
            row.failure_reason = failure_reason[:255]
    db.flush()
    if change:
        audit(
            db,
            provider=tx.provider,
            event_type=change,
            transaction=tx,
            detail=f"{money(amount)} {tx.currency} — {initiated_by}"
            + (f" — {failure_reason}" if failure_reason else ""),
        )
    apply_refunds(db, tx)
    return row


def confirm_pending_refunds(db, tx: PaymentTransaction) -> int:
    """The provider has confirmed the refunds that were still on their way
    for this payment — mark each one succeeded and re-total."""
    db.flush()
    rows = list(
        db.scalars(
            select(PaymentRefund).where(
                PaymentRefund.transaction_id == tx.id, PaymentRefund.status == "pending"
            )
        )
    )
    for row in rows:
        row.status = "succeeded"
        audit(
            db,
            provider=tx.provider,
            event_type="refund pending -> succeeded",
            transaction=tx,
            detail=f"{money(row.amount)} {tx.currency} — {row.initiated_by}",
        )
    if rows:
        apply_refunds(db, tx)
    return len(rows)


def order_payment_status(tx: PaymentTransaction) -> str:
    """The order's four-word payment summary, as this transaction's
    confirmed refunds leave it."""
    refunded = money(tx.refunded_amount or 0)
    if refunded >= money(tx.amount):
        return PaymentStatus.REFUNDED.value
    if refunded > 0:
        return PaymentStatus.PARTIALLY_REFUNDED.value
    return PaymentStatus.PAID.value


def provider_for(order: Order) -> str | None:
    """The provider behind this order's way of paying — None for cash."""
    return PAYMENT_PROVIDERS.get(order.payment_method)
