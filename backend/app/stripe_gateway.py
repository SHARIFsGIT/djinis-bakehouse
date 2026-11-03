"""The Stripe seam.

Every gateway call — creating a checkout session, checking one, refunding,
verifying a webhook signature — passes through this module, so the rest of
the app never imports the Stripe SDK and the test suite can swap the whole
gateway for a fake instead of mocking the wire.

Money crosses exactly one boundary here: Stripe speaks integer minor units
(cents), never floats, so every amount goes through cents() on its way out.
"""

import logging
import time

import stripe

from .config import get_settings

logger = logging.getLogger("bakery.payments")

# How long a checkout link stays payable. When it lapses, Stripe sends the
# expiry webhook and the order cancels itself with the stock returned.
SESSION_LIFETIME_SECONDS = 30 * 60


class GatewayUnavailable(Exception):
    """Stripe could not be reached or refused the call — no money moved."""


class BadSignature(Exception):
    """The webhook payload failed signature verification."""


def configured() -> bool:
    """Card checkout goes live the moment a secret key exists."""
    return bool(get_settings().stripe_secret_key)


def cents(amount: float) -> int:
    """The one float-to-minor-units crossing in the shop."""
    return int(round(amount * 100))


def create_checkout_session(order, base_url: str) -> dict:
    """One hosted Stripe Checkout page for a whole order.

    The card details live on Stripe's page, never on this server, and
    Stripe handles 3-D Secure / SCA. A single line item carries the exact
    order total — items, coupon, tax and delivery folded in — so the
    payment page can never disagree with the invoice by a cent; the
    itemised receipt travels in the email instead.

    Returns {"id", "url"}; raises GatewayUnavailable on any failure.
    """
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key
    summary = ", ".join(f"{item.quantity} × {item.product_name}" for item in order.items)
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=f"{base_url}/account/orders/{order.id}?placed=1&stripe=return",
            cancel_url=f"{base_url}/account/orders/{order.id}?stripe=cancelled",
            customer_email=order.user.email,
            expires_at=int(time.time()) + SESSION_LIFETIME_SECONDS,
            line_items=[
                {
                    "price_data": {
                        "currency": settings.stripe_currency,
                        "product_data": {
                            "name": f"{settings.bakery_name} order {order.order_number}",
                            "description": summary[:200],
                        },
                        "unit_amount": cents(float(order.total)),
                    },
                    "quantity": 1,
                }
            ],
            metadata={"order_id": str(order.id)},
            payment_intent_data={
                "description": f"Order {order.order_number}",
                "metadata": {"order_id": str(order.id)},
            },
        )
    except Exception:
        logger.exception("creating a checkout session for %s failed", order.order_number)
        raise GatewayUnavailable("Stripe did not answer") from None
    return {"id": session.id, "url": session.url}


def retrieve_session(session_id: str) -> dict:
    """The live state of a checkout session — the synchronous answer while
    the webhook is still in flight."""
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        logger.exception("retrieving checkout session %s failed", session_id)
        raise GatewayUnavailable("Stripe did not answer") from None
    return {
        "status": session.get("status"),
        "payment_status": session.get("payment_status"),
        "payment_intent": session.get("payment_intent"),
        "url": session.get("url"),
    }


def refund(payment_intent: str, amount_cents: int) -> None:
    """Send money back to the card, less any amount the shop keeps.

    Raises GatewayUnavailable on failure — the caller decides how honest to
    be about a refund that hasn't actually happened.
    """
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key
    try:
        stripe.Refund.create(payment_intent=payment_intent, amount=amount_cents)
    except Exception:
        logger.exception("refunding payment intent %s failed", payment_intent)
        raise GatewayUnavailable("Stripe did not answer") from None


def verify_webhook(payload: bytes, signature: str) -> dict:
    """Check the signature, then hand back the parsed event.

    The signature IS the credential on this endpoint, so without a
    configured secret nothing is accepted at all.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BadSignature("STRIPE_WEBHOOK_SECRET is not set")
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except Exception:
        raise BadSignature("webhook signature verification failed") from None
    return event
