"""The Stripe seam.

Every gateway call — creating a checkout session, checking one, refunding,
verifying a webhook signature — passes through this module, so the rest of
the app never imports the Stripe SDK and the test suite can swap the whole
gateway for a fake instead of mocking the wire.

Money crosses exactly one boundary here: Stripe speaks integer minor units
(cents), never floats, so every amount goes through cents() on its way out.
"""

import json
import logging
import time

import stripe

from .config import get_settings

logger = logging.getLogger("bakery.payments")

# How long a checkout link stays payable. When it lapses, Stripe sends the
# expiry webhook and the order cancels itself with the stock returned.
SESSION_LIFETIME_SECONDS = 30 * 60

# Dressing for Stripe's hosted page, drawn from the storefront's own tokens:
# the cream paper and burgundy brand colour, and "lora" — the closest of
# Stripe's fixed Checkout fonts to the site's Fraunces display face. The
# page layout itself stays Stripe's; these are the knobs their API allows.
BRAND_BACKGROUND_COLOR = "#f6f1ee"
BRAND_BUTTON_COLOR = "#7f2230"
BRAND_FONT_FAMILY = "lora"

# The two copy slots the hosted page offers: a line under the pay button
# and one on the confirmation screen.
PAY_BUTTON_NOTE = (
    "Fresh from the oven — your bake is reserved the moment this payment lands."
)
AFTER_PAYMENT_NOTE = (
    "Thank you! Your receipt is on its way to your inbox, and your order "
    "page follows every step from oven to door."
)


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


def _tax_label(settings) -> str:
    """The tax row's name, matching the storefront's checkout ("Tax (8.75%)")."""
    percent = f"{settings.tax_rate * 100:.2f}".rstrip("0").rstrip(".")
    return f"Tax ({percent}%)"


def _item_photo(item, base_url: str) -> str | None:
    """The product photo as an absolute URL for the hosted page's thumbnail.

    Stripe fetches the image itself when the session is created, so it must
    be publicly reachable. A photo problem must never be able to break
    payment — hence the guard and the None.
    """
    try:
        product = getattr(item, "product", None)
        url = product.image_url if product is not None else None
    except Exception:
        return None
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return base_url.rstrip("/") + url


def _line_items(order, base_url: str, settings) -> list[dict]:
    """The hosted page's order summary: one line per product, then delivery
    and tax — the same rows the shop's own checkout shows, so the customer
    meets no surprises on the payment page. Delivery is sent even at 0
    (Stripe renders a zero line as free, like the storefront's FREE row);
    tax only when there is any."""
    lines = []
    for item in order.items:
        product_data = {"name": item.product_name}
        photo = _item_photo(item, base_url)
        if photo:
            product_data["images"] = [photo]
        lines.append(
            {
                "price_data": {
                    "currency": settings.stripe_currency,
                    "product_data": product_data,
                    "unit_amount": cents(float(item.unit_price)),
                },
                "quantity": item.quantity,
            }
        )
    lines.append(
        {
            "price_data": {
                "currency": settings.stripe_currency,
                "product_data": {"name": "Delivery"},
                "unit_amount": cents(float(order.shipping_fee)),
            },
            "quantity": 1,
        }
    )
    if float(order.tax) > 0:
        lines.append(
            {
                "price_data": {
                    "currency": settings.stripe_currency,
                    "product_data": {"name": _tax_label(settings)},
                    "unit_amount": cents(float(order.tax)),
                },
                "quantity": 1,
            }
        )
    return lines


def create_checkout_session(order, base_url: str) -> dict:
    """One hosted Stripe Checkout page for a whole order.

    The card details live on Stripe's page, never on this server, and
    Stripe handles 3-D Secure / SCA. The page mirrors the shop's own
    checkout — every product on its own line (photo included), delivery
    and tax rows, the coupon shown as a real discount, the bakery's cream
    and burgundy palette — yet it can never disagree with the invoice: the
    lines sum to subtotal + tax + delivery in integer cents and the
    discount comes off as a Stripe coupon, all straight from the stored
    order.

    Returns {"id", "url"}; raises GatewayUnavailable on any failure.
    """
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key
    discount_amount = float(order.discount_amount or 0)
    try:
        discounts = None
        if discount_amount > 0:
            # Stripe has no negative line items, so a coupon discount rides
            # as a one-off Coupon object — the hosted page then shows
            # "CODE −$x.xx" in Stripe's own dressing.
            coupon = stripe.Coupon.create(
                amount_off=cents(discount_amount),
                currency=settings.stripe_currency,
                duration="once",
                name=order.coupon_code or "Discount",
                metadata={"order_number": order.order_number},
            )
            discounts = [{"coupon": coupon.id}]
        session_kwargs = {
            "mode": "payment",
            "success_url": f"{base_url}/account/orders/{order.id}?placed=1&stripe=return",
            "cancel_url": f"{base_url}/account/orders/{order.id}?stripe=cancelled",
            # The order number, visible in Stripe's dashboard and exports
            "client_reference_id": order.order_number,
            "customer_email": order.user.email,
            "expires_at": int(time.time()) + SESSION_LIFETIME_SECONDS,
            "line_items": _line_items(order, base_url, settings),
            "branding_settings": {
                "background_color": BRAND_BACKGROUND_COLOR,
                "button_color": BRAND_BUTTON_COLOR,
                "border_style": "rounded",
                "font_family": BRAND_FONT_FAMILY,
                "display_name": settings.bakery_name,
            },
            "custom_text": {
                "submit": {"message": PAY_BUTTON_NOTE},
                "after_submit": {"message": AFTER_PAYMENT_NOTE},
            },
            "metadata": {"order_id": str(order.id)},
            "payment_intent_data": {
                "description": f"Order {order.order_number}",
                "metadata": {"order_id": str(order.id)},
            },
        }
        if discounts:
            session_kwargs["discounts"] = discounts
        session = stripe.checkout.Session.create(**session_kwargs)
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
        # SDK objects refuse dict access on purpose — their API fields may
        # collide with dict method names — so nothing SDK-shaped leaves here.
        data = session.to_dict()
    except Exception:
        logger.exception("retrieving checkout session %s failed", session_id)
        raise GatewayUnavailable("Stripe did not answer") from None
    return {
        "status": data.get("status"),
        "payment_status": data.get("payment_status"),
        "payment_intent": data.get("payment_intent"),
        "url": data.get("url"),
    }


def refund(payment_intent: str, amount_cents: int) -> None:
    """Send money back to the card, less any amount the shop keeps.

    The idempotency key is deterministic — the same refund attempted again
    (a repeated cancel click racing the lock, a retry after an uncertain
    outcome) collapses onto the original refund at Stripe instead of
    issuing a second one, while a genuinely different amount gets its own
    key and proceeds. Raises GatewayUnavailable on failure — the caller
    decides how honest to be about a refund that hasn't actually happened.
    """
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key
    try:
        stripe.Refund.create(
            payment_intent=payment_intent,
            amount=amount_cents,
            idempotency_key=f"refund:{payment_intent}:{amount_cents}",
        )
    except Exception:
        logger.exception("refunding payment intent %s failed", payment_intent)
        raise GatewayUnavailable("Stripe did not answer") from None


def verify_webhook(payload: bytes, signature: str) -> dict:
    """Check the signature, then hand back the event as a plain dict.

    The signature IS the credential on this endpoint, so without a
    configured secret nothing is accepted at all. construct_event verifies
    the exact raw bytes — that check is the whole point of calling it — but
    the Event object it returns deliberately refuses dict access
    (.get() raises), which the app's handler speaks. The same verified
    bytes are parsed again here, so the event the app sees is plain JSON
    and identical to what Stripe signed.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BadSignature("STRIPE_WEBHOOK_SECRET is not set")
    try:
        stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise ValueError("the event is not a JSON object")
    except Exception:
        raise BadSignature("webhook signature verification failed") from None
    return event
