"""The desk's view of the money: each order's payment history, real refunds
through the provider that took the payment, and the reconciliation report.

One rule runs through all of it — a refund is something a provider does,
never something the desk declares. The refund button here asks Stripe or
PayPal to send the money and records exactly what they answered; the
order only reads "refunded" once the provider has confirmed it.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from .. import ledger, payment_ops, paypal_gateway, stripe_gateway
from ..config import get_settings
from ..database import utcnow
from ..email import send_refund_email
from ..models import (
    OPEN_TRANSACTION_STATUSES,
    PAYMENT_PROVIDERS,
    SETTLED_TRANSACTION_STATUSES,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentEvent,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)
from ..money import money
from ..schemas import (
    OrderPaymentsOut,
    PaymentEventOut,
    PaymentProviderStatusOut,
    PaymentRefundOut,
    PaymentTransactionOut,
    ReconciliationIssueOut,
    ReconciliationOut,
    RefundRequest,
)
from ..security import CurrentAdmin, DbSession
from .orders import _lock_for_cancellation
from .payments import PAY_WINDOW, reconcile_order

logger = logging.getLogger("bakery.payments")

router = APIRouter(prefix="/api/admin", tags=["admin payments"])


def _transaction_out(db, tx: PaymentTransaction) -> PaymentTransactionOut:
    held = tx.status in SETTLED_TRANSACTION_STATUSES
    return PaymentTransactionOut(
        id=tx.id,
        provider=tx.provider,
        provider_reference=tx.provider_reference,
        provider_payment_id=tx.provider_payment_id,
        method_label=payment_ops.method_label(tx.method_type),
        amount=float(tx.amount),
        currency=tx.currency,
        status=tx.status,
        refunded_amount=float(tx.refunded_amount or 0),
        refundable_amount=float(ledger.refundable(db, tx)) if held else 0.0,
        dispute_status=tx.dispute_status,
        failure_reason=tx.failure_reason,
        paid_at=tx.paid_at,
        created_at=tx.created_at,
        updated_at=tx.updated_at,
        refunds=[
            PaymentRefundOut.model_validate(refund)
            for refund in db.scalars(
                select(PaymentRefund)
                .where(PaymentRefund.transaction_id == tx.id)
                .order_by(PaymentRefund.id)
            )
        ],
    )


def _payments_out(db, order: Order, sync_outcome: str | None = None) -> OrderPaymentsOut:
    transactions = ledger.for_order(db, order.id)
    holding = ledger.money_transaction(db, order)
    events = db.scalars(
        select(PaymentEvent)
        .where(PaymentEvent.order_id == order.id)
        .order_by(PaymentEvent.id.desc())
        .limit(60)
    ).all()
    return OrderPaymentsOut(
        order_id=order.id,
        order_number=order.order_number,
        payment_method=order.payment_method,
        payment_status=order.payment_status,
        provider=ledger.provider_for(order),
        total=float(order.total),
        currency=order.currency,
        paid_amount=float(holding.amount) if holding is not None else 0.0,
        refunded_amount=float(holding.refunded_amount or 0) if holding is not None else 0.0,
        refundable_amount=float(ledger.refundable(db, holding)) if holding is not None else 0.0,
        transactions=[_transaction_out(db, tx) for tx in transactions],
        events=[PaymentEventOut.model_validate(event) for event in events],
        sync_outcome=sync_outcome,
    )


def _order_or_404(db, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/orders/{order_id}/payments", response_model=OrderPaymentsOut)
def order_payments(order_id: int, admin: CurrentAdmin, db: DbSession):
    """An order's whole payment story: every pay page it was handed, the
    one that holds its money, each refund and dispute, and the audit trail
    of what changed when. A payment from before the ledger existed gets
    its row written here, from the order's own record, the first time the
    desk looks."""
    order = _order_or_404(db, order_id)
    if ledger.provider_for(order) and ledger.money_transaction(db, order) is None:
        if ledger.adopt_order_payment(db, order) is not None:
            db.commit()
    return _payments_out(db, order)


@router.post("/orders/{order_id}/refunds", response_model=OrderPaymentsOut)
def refund_order(
    order_id: int,
    payload: RefundRequest,
    admin: CurrentAdmin,
    db: DbSession,
    background: BackgroundTasks,
):
    """Send money back to the customer through the provider that took it —
    all that is still held, or the stated part.

    The order's payment status changes only on the provider's confirmation.
    A refund the provider refused is still written down (so the desk can
    see what was tried) and answered with a 502 and the reason; one the
    provider accepted but has yet to settle shows as pending until its
    webhook confirms it.
    """
    order = _order_or_404(db, order_id)
    # Locked like every other write to an order's money: the provider's
    # refund webhook will arrive for this very row within the second.
    order = _lock_for_cancellation(order, db)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if ledger.provider_for(order) is None:
        raise HTTPException(
            status_code=400,
            detail="This order wasn't paid online — there is no provider to refund it through",
        )
    tx = ledger.money_transaction(db, order) or ledger.adopt_order_payment(db, order)
    if tx is None or not tx.provider_payment_id:
        raise HTTPException(
            status_code=400,
            detail="No online payment is on record for this order — nothing can be refunded",
        )
    label = payment_ops.PROVIDER_LABELS.get(tx.provider, tx.provider)
    remaining = ledger.refundable(db, tx)
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="This payment has already been refunded in full")
    amount = ledger.money(payload.amount) if payload.amount is not None else remaining
    if amount > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Only {money(float(remaining), order.currency)} of this payment is left to refund",
        )
    reason = (payload.reason or "").strip() or None
    refund = payment_ops.issue_refund(
        db,
        order,
        tx,
        ledger.cents(amount),
        reason=reason,
        initiated_by=admin.full_name,
        admin_user_id=admin.id,
    )
    amount_text = money(float(amount), order.currency)
    if refund.status == "succeeded":
        comment = f"Refund of {amount_text} sent back through {label}"
    elif refund.status == "pending":
        comment = f"Refund of {amount_text} requested — {label} has yet to confirm it"
    else:
        comment = f"Refund of {amount_text} could not be sent — {refund.failure_reason}"
    if reason and refund.status != "failed":
        comment += f" ({reason})"
    db.add(OrderEvent(order_id=order.id, status=order.status, comment=comment))
    db.commit()
    db.refresh(order)
    logger.info(
        "order %s: refund of %s through %s by admin #%s — %s",
        order.order_number, amount, label, admin.id, refund.status,
    )
    if refund.status == "failed":
        raise HTTPException(
            status_code=502,
            detail=f"{label} did not accept the refund — nothing was sent back. "
            "Try again, or refund it in the provider's own dashboard.",
        )
    if refund.status == "succeeded" and order.user is not None and order.user.is_active:
        background.add_task(
            send_refund_email,
            {
                "email": order.user.email,
                "first_name": order.user.first_name,
                "order_number": order.order_number,
                "amount": float(amount),
                "currency": order.currency,
                "provider": tx.provider,
                "full": order.payment_status == PaymentStatus.REFUNDED.value,
            },
        )
    return _payments_out(db, order)


@router.post("/orders/{order_id}/payments/sync", response_model=OrderPaymentsOut)
def sync_order_payment(
    order_id: int, admin: CurrentAdmin, db: DbSession, background: BackgroundTasks
):
    """Ask the provider about this order right now and land whatever is
    true — the desk's own door to the reconciliation the sweep runs. This
    is how a payment whose webhook never arrived is brought onto the book:
    by the provider's word, not by relabelling the order."""
    order = _order_or_404(db, order_id)
    if ledger.provider_for(order) is None:
        raise HTTPException(status_code=400, detail="This order wasn't paid online")
    if not payment_ops.available(order.payment_method):
        raise HTTPException(
            status_code=400, detail="This order's payment provider isn't configured on this deployment"
        )
    try:
        outcome = reconcile_order(order, background, db)
    except (stripe_gateway.GatewayUnavailable, paypal_gateway.GatewayUnavailable):
        raise HTTPException(
            status_code=503, detail="The payment provider didn't answer — try again in a moment"
        )
    db.commit()
    db.refresh(order)
    words = {
        "paid": "The provider confirms the payment — the order is marked paid.",
        "refunded": "A payment had landed on this cancelled order — it was sent back.",
        "processing": "The customer has paid — the provider is still settling it.",
        "awaiting": "The provider has not received a payment for this order yet.",
        "expired": "The pay page lapsed unpaid.",
        "declined": "The provider declined the customer's funding source.",
        "failed": "The provider reports the payment as failed.",
        "nothing to do": "Nothing to check — this order is not waiting for a payment.",
        "nothing open": "This order has no open pay page to ask about.",
    }
    return _payments_out(db, order, sync_outcome=words.get(outcome, outcome))


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

def _provider_statuses() -> list[PaymentProviderStatusOut]:
    settings = get_settings()
    return [
        PaymentProviderStatusOut(
            provider="stripe",
            configured=stripe_gateway.configured(),
            mode=stripe_gateway.mode(),
            webhook_ready=bool(settings.stripe_webhook_secret),
        ),
        PaymentProviderStatusOut(
            provider="paypal",
            configured=paypal_gateway.configured(),
            mode=paypal_gateway.environment() if paypal_gateway.configured() else None,
            webhook_ready=paypal_gateway.webhook_ready(),
        ),
    ]


@router.get("/payments/reconciliation", response_model=ReconciliationOut)
def payment_reconciliation(admin: CurrentAdmin, db: DbSession):
    """The payment book checked against itself: everything that does not
    add up, each with what to do about it. Read-only and provider-free —
    it reads the ledger the webhooks and the sweep keep, so it is safe to
    open at any time; the per-order "check with the provider" action is
    where a provider is actually asked.
    """
    issues: list[ReconciliationIssueOut] = []
    now = utcnow()

    def add(kind, severity, order, detail, suggestion, provider=None):
        issues.append(
            ReconciliationIssueOut(
                kind=kind,
                severity=severity,
                order_id=order.id if order is not None else None,
                order_number=order.order_number if order is not None else None,
                provider=provider or (ledger.provider_for(order) if order is not None else None),
                detail=detail,
                suggestion=suggestion,
            )
        )

    online = list(PAYMENT_PROVIDERS)

    # 1. Orders still waiting for a payment long after their pay window.
    for order in db.scalars(
        select(Order).where(
            Order.payment_method.in_(online),
            Order.payment_status == PaymentStatus.PENDING.value,
            Order.status != OrderStatus.CANCELLED.value,
            Order.created_at < now - PAY_WINDOW,
        )
    ):
        in_flight = ledger.open_for_order(db, order.id)
        if in_flight is not None and in_flight.status == TransactionStatus.PROCESSING.value:
            # Not stuck — the customer has paid with something that settles
            # later, and the provider has yet to say how it ended.
            add(
                "processing_payment", "watch", order,
                "The customer has paid; the provider is still settling it "
                "(a bank debit, or a payment PayPal is reviewing).",
                "Nothing to do yet — the provider confirms or fails it on its own. "
                "Hold the bake until it is marked paid.",
                provider=in_flight.provider,
            )
            continue
        add(
            "stuck_pending", "action", order,
            "Still waiting for its payment well past the pay window.",
            "Check with the provider — it is either paid there or should be cancelled.",
        )

    transactions = list(db.scalars(select(PaymentTransaction).order_by(PaymentTransaction.id)))
    by_order: dict[int, list[PaymentTransaction]] = {}
    for tx in transactions:
        by_order.setdefault(tx.order_id, []).append(tx)
    orders = {
        order.id: order
        for order in db.scalars(select(Order).where(Order.id.in_(list(by_order) or [0])))
    }

    for order_id, rows in by_order.items():
        order = orders.get(order_id)
        if order is None:
            continue
        holding = [tx for tx in rows if tx.status in SETTLED_TRANSACTION_STATUSES]
        still_held = [tx for tx in holding if ledger.refundable(db, tx) > 0]

        # 2. More than one payment still held for one order.
        if len(still_held) > 1:
            add(
                "duplicate_payment", "action", order,
                f"{len(still_held)} payments are held for this one order.",
                "Refund the extra payment from the order's payment panel.",
            )
        for tx in holding:
            left = ledger.refundable(db, tx)
            # 3. Money still held for an order that was cancelled.
            if order.status == OrderStatus.CANCELLED.value and left > ledger.money(order.cancel_fee or 0):
                owed = left - ledger.money(order.cancel_fee or 0)
                add(
                    "held_on_cancelled", "action", order,
                    f"{money(float(owed), order.currency)} is still held for this cancelled order.",
                    "Refund it from the order's payment panel.",
                    provider=tx.provider,
                )
            # 4. The provider charged something other than the order's total.
            if ledger.money(tx.amount) != ledger.money(order.total):
                add(
                    "amount_mismatch", "watch", order,
                    f"Paid {money(float(tx.amount), tx.currency)} against an order total of "
                    f"{money(float(order.total), order.currency)}.",
                    "Compare with the provider's dashboard before doing anything else.",
                    provider=tx.provider,
                )
            # 5. A dispute nobody has answered yet.
            if tx.dispute_status == "open":
                add(
                    "open_dispute", "action", order,
                    "The customer's bank or PayPal has opened a dispute on this payment.",
                    "Answer it in the provider's dashboard before its deadline.",
                    provider=tx.provider,
                )
        # 6. The order's payment summary disagrees with its ledger.
        money_tx = ledger.money_transaction(db, order)
        if (
            money_tx is not None
            and money_tx.status != TransactionStatus.DISPUTED.value
            and order.payment_status != ledger.order_payment_status(money_tx)
        ):
            add(
                "status_drift", "action", order,
                f"The order says “{order.payment_status.replace('_', ' ')}”; its ledger says "
                f"“{ledger.order_payment_status(money_tx).replace('_', ' ')}”.",
                "Check with the provider, then correct whichever is wrong.",
                provider=money_tx.provider,
            )
        # 7. A pay page still open on an order that no longer wants paying.
        if order.status == OrderStatus.CANCELLED.value and any(
            tx.status in OPEN_TRANSACTION_STATUSES for tx in rows
        ):
            add(
                "open_page_on_cancelled", "watch", order,
                "A pay page is still marked open for this cancelled order.",
                "Check with the provider — if it was paid there, the money is sent back.",
            )

    # 8. Online orders that say "paid" with no provider payment behind them.
    for order in db.scalars(
        select(Order).where(
            Order.payment_method.in_(online),
            Order.payment_status.in_(
                [PaymentStatus.PAID.value, PaymentStatus.PARTIALLY_REFUNDED.value]
            ),
        )
    ):
        if order.stripe_payment_intent_id or ledger.money_transaction(db, order) is not None:
            continue
        add(
            "paid_without_provider", "watch", order,
            "Marked paid, but no provider payment is on record — it was labelled by hand.",
            "Confirm the money really arrived (cash, bank transfer) or reset the label.",
        )

    # 9. Refunds that failed, or have been pending for days.
    for refund in db.scalars(
        select(PaymentRefund).where(
            PaymentRefund.status.in_(["failed", "pending"]),
            PaymentRefund.created_at > now - timedelta(days=45),
        )
    ):
        order = db.get(Order, refund.order_id)
        amount = money(float(refund.amount), refund.currency)
        if refund.status == "failed":
            tx = db.get(PaymentTransaction, refund.transaction_id)
            if tx is not None and ledger.refundable(db, tx) <= Decimal("0.00"):
                continue  # everything has gone back since — nothing is owed
            if db.scalar(
                select(PaymentRefund.id)
                .where(
                    PaymentRefund.transaction_id == refund.transaction_id,
                    PaymentRefund.id > refund.id,
                    PaymentRefund.status.in_(["succeeded", "pending"]),
                )
                .limit(1)
            ):
                continue  # a later attempt on the same payment went through
            add(
                "failed_refund", "action", order,
                f"A refund of {amount} could not be sent ({refund.failure_reason or 'no reason given'}).",
                "Try the refund again from the order's payment panel.",
                provider=refund.provider,
            )
        elif refund.created_at < now - timedelta(days=5):
            add(
                "slow_refund", "watch", order,
                f"A refund of {amount} has been waiting for the provider's confirmation for days.",
                "Look it up in the provider's dashboard.",
                provider=refund.provider,
            )

    issues.sort(key=lambda issue: (issue.severity != "action", issue.order_id or 0))
    return ReconciliationOut(checked_at=now, providers=_provider_statuses(), issues=issues)
