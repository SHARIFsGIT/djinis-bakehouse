"""What the shop asks of a payment provider, whichever provider it is.

Two operations live here — opening a pay page and sending money back —
because they are the two the order code needs without caring whether
Stripe or PayPal is on the other end. Each asks the right gateway, then
writes exactly what the gateway said into the ledger (app/ledger.py).

The rule this module exists to keep: the shop never *says* money moved
because it asked for it to. A refund is "succeeded" on the provider's word,
"pending" while the provider is still deciding, and "failed" — on the
record, with the reason — when it could not be sent.
"""

import calendar
import logging
from decimal import Decimal

from . import ledger, paypal_gateway, stripe_gateway
from .config import get_settings
from .models import Order, PaymentMethod, PaymentRefund, PaymentStatus, PaymentTransaction

logger = logging.getLogger("bakery.payments")

PROVIDER_LABELS = {"stripe": "Stripe", "paypal": "PayPal"}

# How an order's payment reads in a sentence ("paid by card", "paid with PayPal").
PAID_PHRASE = {"stripe": "paid by card", "paypal": "paid with PayPal"}

# The methods a provider can report, as (name on a receipt, phrase in a
# sentence). Stripe's page offers whatever the account has switched on, so
# the method is read back from the provider rather than assumed to be a card.
METHOD_WORDS = {
    "card": ("Card", "paid by card"),
    "link": ("Link", "paid with Link"),
    "klarna": ("Klarna", "paid with Klarna"),
    "paypal": ("PayPal", "paid with PayPal"),
    "sepa_debit": ("SEPA Direct Debit", "paid by SEPA Direct Debit"),
    "ideal": ("iDEAL", "paid with iDEAL"),
    "bancontact": ("Bancontact", "paid with Bancontact"),
    "eps": ("EPS", "paid with EPS"),
    "p24": ("Przelewy24", "paid with Przelewy24"),
    "revolut_pay": ("Revolut Pay", "paid with Revolut Pay"),
    "amazon_pay": ("Amazon Pay", "paid with Amazon Pay"),
}
WALLET_NAMES = {
    "apple_pay": "Apple Pay",
    "google_pay": "Google Pay",
    "samsung_pay": "Samsung Pay",
    "link": "Link",
}


def method_label(method_type: str | None) -> str | None:
    """A recorded method as a receipt names it — "Card (Apple Pay)",
    "Klarna" — or None when none is on record (the caller then falls back
    to the order's own way of paying)."""
    if not method_type:
        return None
    kind, _, wallet = method_type.partition(":")
    if kind not in METHOD_WORDS:
        return "Online payment"
    label = METHOD_WORDS[kind][0]
    if wallet in WALLET_NAMES:
        label += f" ({WALLET_NAMES[wallet]})"
    return label


def paid_phrase(provider: str, method_type: str | None) -> str:
    """ "paid by card", "paid with Klarna"… for the order's timeline. With
    no method on record the provider's long-standing phrase stands."""
    kind = (method_type or "").partition(":")[0]
    if kind in METHOD_WORDS:
        return METHOD_WORDS[kind][1]
    if method_type:
        return "paid online"
    return PAID_PHRASE.get(provider, "paid online")

# The least each provider will take online, in the shop's currency. Stripe
# refuses a charge under fifty cents; PayPal takes any positive amount. An
# order below the line is told so before it is placed, instead of being
# placed, refused by the provider and cancelled.
MIN_ONLINE_TOTAL = {"stripe": Decimal("0.50"), "paypal": Decimal("0.01")}


class PayPageUnavailable(Exception):
    """The provider could not open a pay page — nothing was charged."""


class UnchargeableCurrency(Exception):
    """The order's own currency cannot be charged; the order is left
    untouched rather than relabelled (see stripe_gateway.order_currency)."""


def available(method: str) -> bool:
    """Whether the shop can genuinely take this way of paying right now —
    a rail with no keys behind it is never offered and never accepted."""
    if method == PaymentMethod.CARD.value:
        return stripe_gateway.configured()
    if method == PaymentMethod.PAYPAL.value:
        return paypal_gateway.configured()
    return method == PaymentMethod.CASH_ON_DELIVERY.value


def test_modes() -> dict[str, bool]:
    """Which online rails are a rehearsal right now (Stripe test keys, the
    PayPal sandbox), by way of paying. The storefront says so on exactly
    the option it is true of — a real card is declined on a test key, and a
    customer deserves to know why — and never on one that is live: the two
    providers go live separately."""
    return {
        PaymentMethod.CARD.value: stripe_gateway.mode() == "test",
        PaymentMethod.PAYPAL.value: paypal_gateway.configured() and paypal_gateway.environment() != "live",
    }


def test_mode() -> bool:
    """True while any configured online rail is a rehearsal."""
    return any(test_modes().values())


def public_base(request) -> str:
    """The address providers send the customer back to: the shop's own
    configured public address when there is one, else the address this
    request arrived on (as it always was)."""
    configured = get_settings().public_base_url.strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def open_pay_page(db, order: Order, base_url: str) -> str:
    """Open a pay page for this order with its own provider and put it in
    the ledger. Returns the address to send the customer to.

    Raises PayPageUnavailable when the provider could not be reached or
    refused, UnchargeableCurrency when the order's currency can't be
    charged. Either way no money moved and nothing was written.
    """
    provider = ledger.provider_for(order)
    if provider == "stripe":
        try:
            session = stripe_gateway.create_checkout_session(order, base_url)
        except stripe_gateway.InvalidOrderCurrency:
            raise UnchargeableCurrency from None
        except stripe_gateway.GatewayUnavailable:
            raise PayPageUnavailable from None
        order.stripe_session_id = session["id"]
        ledger.open_transaction(
            db, order, provider="stripe", reference=session["id"], checkout_url=session["url"]
        )
        return session["url"]
    if provider == "paypal":
        # Each pay link of an order is its own PayPal order; the attempt
        # number keeps PayPal's idempotency key distinct per link while a
        # retry of the *same* attempt still collapses onto its first answer.
        # The key also carries the second the order was placed: PayPal
        # remembers keys per merchant account, not per database, and an
        # order id alone repeats wherever the same credentials meet another
        # database (a rehearsal copy, a rebuilt one) — where it would hand
        # this customer some other order's pay page.
        attempt = 1 + sum(1 for tx in ledger.for_order(db, order.id) if tx.provider == "paypal")
        placed = calendar.timegm(order.created_at.timetuple())
        try:
            created = paypal_gateway.create_order(
                order, base_url, request_id=f"bh-{order.id}-{placed}-{attempt}"
            )
        except paypal_gateway.InvalidOrderCurrency:
            raise UnchargeableCurrency from None
        except paypal_gateway.GatewayUnavailable:
            raise PayPageUnavailable from None
        ledger.open_transaction(
            db, order, provider="paypal", reference=created["id"], checkout_url=created["url"]
        )
        return created["url"]
    raise ValueError(f"{order.payment_method} orders have no pay page")


def refund_state(provider: str, raw: str | None) -> str:
    """A provider's refund status in the ledger's three words."""
    word = str(raw or "").strip().lower()
    if provider == "stripe":
        if word in ("", "succeeded"):
            return "succeeded"
        if word in ("pending", "requires_action"):
            return "pending"
        return "failed"
    if word == "completed":
        return "succeeded"
    if word in ("failed", "cancelled", "canceled"):
        return "failed"
    return "pending"  # PENDING, or an answer the shop can't read yet — wait for the webhook


def sync_order_payment_status(order: Order, tx: PaymentTransaction) -> None:
    """Let the order's payment summary follow the confirmed refunds on the
    transaction that holds its money."""
    if order.payment_status in (
        PaymentStatus.PAID.value,
        PaymentStatus.PARTIALLY_REFUNDED.value,
        PaymentStatus.REFUNDED.value,
    ):
        order.payment_status = ledger.order_payment_status(tx)


def issue_refund(
    db,
    order: Order,
    tx: PaymentTransaction,
    amount_cents: int,
    *,
    reason: str | None,
    initiated_by: str,
    admin_user_id: int | None = None,
    sync_order: bool = True,
) -> PaymentRefund:
    """Ask the provider to send `amount_cents` back against this
    transaction, and record what it answered.

    Never raises for a provider failure: the attempt is written down as a
    failed refund (with the reason) and handed back, so the desk sees what
    was tried — the caller decides what to tell whom. Raises ValueError
    only for a request that makes no sense (nothing, or more than is left).
    """
    if amount_cents <= 0:
        raise ValueError("a refund needs an amount above zero")
    remaining = ledger.cents(ledger.refundable(db, tx))
    if amount_cents > remaining:
        raise ValueError(
            f"only {ledger.money(Decimal(remaining) / 100)} of this payment is left to refund"
        )
    amount = ledger.money(Decimal(amount_cents) / 100)
    label = PROVIDER_LABELS.get(tx.provider, tx.provider)
    common = {
        "amount": amount,
        "reason": reason,
        "initiated_by": initiated_by,
        "admin_user_id": admin_user_id,
    }
    if not tx.provider_payment_id:
        return ledger.record_refund(
            db, tx, status="failed",
            failure_reason=f"no {label} payment on record to refund against", **common,
        )
    # How many refunds the provider already holds for this payment. It
    # tells a deliberate second refund of the same amount (a new key) apart
    # from the same request arriving twice, or retried after a timeout
    # (the same key — the provider answers with the refund it already made).
    # Failed attempts don't count: one that may have gone through after all
    # must be retried under the key it was first sent with.
    sequence = ledger.provider_refund_count(db, tx)
    try:
        if tx.provider == "stripe":
            if sequence:
                result = stripe_gateway.refund(tx.provider_payment_id, amount_cents, sequence) or {}
            else:
                result = stripe_gateway.refund(tx.provider_payment_id, amount_cents) or {}
        else:
            # Keyed on PayPal's own capture id — unique everywhere, unlike
            # a row id — so a key can only ever mean a refund of THIS money.
            result = paypal_gateway.refund_capture(
                tx.provider_payment_id,
                amount_cents,
                tx.currency,
                request_id=f"refund-{tx.provider_payment_id}-{sequence}-{amount_cents}",
            ) or {}
    except (stripe_gateway.GatewayUnavailable, paypal_gateway.GatewayUnavailable) as exc:
        logger.warning(
            "refund of %s on order %s (%s) could not be sent: %s",
            amount, order.order_number, label, exc,
        )
        return ledger.record_refund(
            db, tx, status="failed", failure_reason=f"{label} did not accept the refund", **common
        )
    state = refund_state(tx.provider, result.get("status"))
    row = ledger.record_refund(
        db, tx, status=state, provider_refund_id=result.get("id"),
        failure_reason=f"{label} reported the refund as failed" if state == "failed" else None,
        **common,
    )
    if row.transaction_id != tx.id:
        # The provider answered with a refund the book already holds against
        # a different payment, so it cannot be booked as this one's. Nothing
        # is claimed: the attempt goes down as failed, for a person to look
        # up at the provider before anyone tries again.
        logger.error(
            "refund on order %s: %s answered with refund %s, already booked on transaction %s",
            order.order_number, label, result.get("id"), row.transaction_id,
        )
        return ledger.record_refund(
            db, tx, status="failed",
            failure_reason=(
                f"{label} answered with a refund already recorded on another payment "
                f"— check the {label} dashboard"
            ),
            **common,
        )
    if sync_order:
        sync_order_payment_status(order, tx)
    return row
