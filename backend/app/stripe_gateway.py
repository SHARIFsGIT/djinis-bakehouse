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
from .email import shown_email

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


class InvalidOrderCurrency(Exception):
    """The order's stored currency can't be charged, so the gateway refuses
    to touch it. Relabelling historical money — charging an order in the
    shop's *current* currency because its own is missing or unsupported —
    is exactly the accident this exception exists to stop."""


class BadSignature(Exception):
    """The webhook payload failed signature verification."""


# What the gateway will actually bill in. EUR only since the 2026-11-05 owner
# decision — new orders are placed and charged in euro, full stop. USD stays
# readable everywhere (historical orders report and refund in the currency
# they were placed in) but can no longer be *charged*: a checkout attempt
# configured otherwise pauses fail-closed instead of relabelling money.
SUPPORTED_CURRENCIES = ("eur",)

# Payment-method names an explicit STRIPE_PAYMENT_METHODS list may carry. The
# list is only ever a *filter* over what Stripe itself computes as eligible
# for the account, the customer and the device, so nothing here can make an
# unavailable method appear; the vocabulary exists so a typo is dropped with
# a warning instead of failing every session. Apple Pay and Google Pay are
# not entries: they ride on "card" and show wherever the device has them.
KNOWN_PAYMENT_METHODS = frozenset(
    {
        "card",
        "link",
        "klarna",
        "paypal",
        "sepa_debit",
        "ideal",
        "bancontact",
        "eps",
        "p24",
        "revolut_pay",
        "amazon_pay",
    }
)

# The events the shop's webhook endpoint must be subscribed to at Stripe.
# Every one of them is acted on by app/routers/payments.py; an endpoint
# missing one leaves that part of the story to the reconciliation sweep —
# or, for refunds and disputes made at Stripe, unseen.
WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
    "charge.refunded",
    "refund.updated",
    "charge.dispute.created",
    "charge.dispute.closed",
)
# Also understood when they arrive; not every account offers them.
WEBHOOK_EVENTS_OPTIONAL = ("refund.failed", "charge.refund.updated")

# One wait for every call to Stripe. The SDK's own default is 80 seconds —
# far longer than a shopper watching a spinner (or a worker thread holding
# an order's row lock) should ever wait.
REQUEST_TIMEOUT_SECONDS = 20


def order_currency(order) -> str:
    """The currency this order's money moves in — read off the order, never
    off the settings. A missing or unknown code is refused, not guessed: the
    whole point of order.currency is that a dollar order stays a dollar order
    even after the shop starts charging euro."""
    code = str(getattr(order, "currency", "") or "").strip().lower()
    if code not in SUPPORTED_CURRENCIES:
        raise InvalidOrderCurrency(
            f"order {getattr(order, 'order_number', '?')} has an unchargeable currency "
            f"({code or 'none'}); refusing to guess one"
        )
    return code


def configured() -> bool:
    """Card checkout goes live the moment a secret key exists."""
    return bool(get_settings().stripe_secret_key)


def _ready() -> None:
    """Point the SDK at the shop's key and give its calls a sane timeout.
    Called at the top of every gateway function; cheap after the first."""
    stripe.api_key = get_settings().stripe_secret_key
    make_client = getattr(stripe, "new_default_http_client", None)
    if make_client is not None and getattr(stripe, "default_http_client", None) is None:
        try:
            stripe.default_http_client = make_client(timeout=REQUEST_TIMEOUT_SECONDS)
        except Exception:  # an SDK without the knob keeps its own default
            logger.warning("could not set the Stripe request timeout — using the SDK default")


def mode() -> str | None:
    """"live" or "test", read off the configured key's own prefix — None
    when no key is set. The desk shows it, so nobody mistakes a rehearsal
    for a till that takes real money (or the other way round)."""
    key = get_settings().stripe_secret_key
    if not key:
        return None
    return "live" if key.startswith(("sk_live_", "rk_live_")) else "test"


def allowed_payment_methods() -> list[str] | None:
    """The explicit method filter from STRIPE_PAYMENT_METHODS, or None for
    Stripe's own automatic choice (the default, and "automatic"/empty).

    None is not "cards only": it is every method the Stripe account has
    switched on that suits this customer, amount and device — the dashboard
    decides, and nothing unsupported is ever asked for. A list narrows that
    automatic set; names the gateway doesn't know drop out with a warning,
    and a list with nothing usable left is treated as automatic rather than
    sent to Stripe as a refusal.
    """
    settings = get_settings()
    requested = [
        m.strip().lower()
        for m in str(settings.stripe_payment_methods or "").split(",")
        if m.strip()
    ]
    if not requested or requested == ["automatic"]:
        return None
    methods = [m for m in dict.fromkeys(requested) if m in KNOWN_PAYMENT_METHODS]
    dropped = [m for m in requested if m not in KNOWN_PAYMENT_METHODS and m != "automatic"]
    if dropped:
        logger.warning(
            "STRIPE_PAYMENT_METHODS contains unsupported entr%s (%s) — offering %s",
            "y" if len(dropped) == 1 else "ies",
            ", ".join(dropped),
            ", ".join(methods) or "Stripe's automatic set",
        )
    if not methods:
        return None
    # Card first when asked for, so the familiar rail is never buried under
    # wallet options on the hosted page.
    if "card" in methods:
        methods.remove("card")
        methods.insert(0, "card")
    return methods


def _native_paypal_configured(settings) -> bool:
    return bool(settings.paypal_client_id and settings.paypal_client_secret)


def cents(amount: float) -> int:
    """The one float-to-minor-units crossing in the shop."""
    return int(round(amount * 100))


def _tax_label(settings) -> str:
    """The tax row's name, matching the storefront's checkout ("Tax (5%)").
    A shop with no approved rate (None while VAT treatment is pending)
    names the row plainly rather than printing a percentage it can't stand
    behind."""
    if settings.tax_rate is None:
        return "Tax"
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


def _line_items(order, base_url: str, settings, currency: str) -> list[dict]:
    """The hosted page's order summary: one line per product, then delivery
    and tax — the same rows the shop's own checkout shows, so the customer
    meets no surprises on the payment page. Delivery is sent even at 0
    (Stripe renders a zero line as free, like the storefront's FREE row);
    tax only when there is any. Every line speaks the order's own currency,
    so the page can never relabel an order's money."""
    lines = []
    for item in order.items:
        product_data = {"name": item.product_name}
        photo = _item_photo(item, base_url)
        if photo:
            product_data["images"] = [photo]
        lines.append(
            {
                "price_data": {
                    "currency": currency,
                    "product_data": product_data,
                    "unit_amount": cents(float(item.unit_price)),
                },
                "quantity": item.quantity,
            }
        )
    lines.append(
        {
            "price_data": {
                "currency": currency,
                "product_data": {"name": "Delivery"},
                "unit_amount": cents(float(order.shipping_fee)),
            },
            "quantity": 1,
        }
    )
    # Exclusive mode adds the register tax as its own row. Inclusive mode
    # never does — the VAT is already inside every line above, and a second
    # row would charge it twice. Either way the lines sum to the stored
    # order total in integer cents.
    if float(order.tax) > 0 and settings.tax_mode != "inclusive":
        lines.append(
            {
                "price_data": {
                    "currency": currency,
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
    _ready()
    # Validated before anything touches Stripe: the currency every line, the
    # coupon and (through the payment intent it creates) any later refund
    # moves in is the order's own, whatever the shop charges today.
    currency = order_currency(order)
    discount_amount = float(order.discount_amount or 0)
    try:
        discounts = None
        if discount_amount > 0:
            # Stripe has no negative line items, so a coupon discount rides
            # as a one-off Coupon object — the hosted page then shows
            # "CODE −$x.xx" in Stripe's own dressing. Same currency as the
            # lines it discounts, or Stripe would refuse the mismatch anyway.
            coupon = stripe.Coupon.create(
                amount_off=cents(discount_amount),
                currency=currency,
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
            "expires_at": int(time.time()) + SESSION_LIFETIME_SECONDS,
            "line_items": _line_items(order, base_url, settings, currency),
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
        # Prefilled on Stripe's page when the shop has it. An account opened
        # with a phone number alone has no email on file: Stripe then asks
        # for one on its own page, where the receipt is sent from.
        address = shown_email(order.user.email)
        if address:
            session_kwargs["customer_email"] = address
        # Which methods the page offers is Stripe's to compute (the default);
        # an explicit list only filters that set. The old static
        # "payment_method_types" list is deliberately no longer sent: the
        # SDK's pinned API version has dropped it from session creation.
        allowed = allowed_payment_methods()
        if _native_paypal_configured(settings):
            # PayPal is its own button at the shop's checkout — it must not
            # also appear inside Stripe's page as a second, different PayPal.
            if allowed is None:
                session_kwargs["excluded_payment_method_types"] = ["paypal"]
            else:
                allowed = [m for m in allowed if m != "paypal"] or None
        if allowed:
            session_kwargs["allowed_payment_method_types"] = allowed
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
    _ready()
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
        # What the page actually charged — reconciliation compares it with
        # the order's own total instead of assuming they agree.
        "amount_total": data.get("amount_total"),
        "currency": data.get("currency"),
    }


def payment_method(payment_intent: str) -> str | None:
    """What the customer actually paid with on Stripe's page, in Stripe's own
    word — "card", "klarna", "sepa_debit", "link"… A card paid through a
    wallet names the wallet too ("card:apple_pay").

    Wording only: it lets a receipt say "Klarna" instead of guessing "card"
    now that the page offers whatever the account has switched on. So it
    never raises and never stands in a payment's way — None when Stripe
    cannot be asked or does not say.
    """
    _ready()
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent, expand=["latest_charge"])
        data = intent.to_dict()
    except Exception as exc:
        logger.info("could not read the payment method of %s: %s", payment_intent, type(exc).__name__)
        return None
    charge = data.get("latest_charge")
    details = charge.get("payment_method_details") if isinstance(charge, dict) else None
    if not isinstance(details, dict):
        return None
    kind = details.get("type")
    if not isinstance(kind, str) or not kind:
        return None
    if kind == "card":
        card = details.get("card")
        wallet = card.get("wallet") if isinstance(card, dict) else None
        wallet_type = wallet.get("type") if isinstance(wallet, dict) else None
        if isinstance(wallet_type, str) and wallet_type:
            return f"card:{wallet_type}"[:40]
    return kind[:40]


def expire_session(session_id: str) -> bool:
    """Close a checkout session so its pay page can no longer take money.

    An order that is cancelled — or handed a fresh pay link — must not leave
    a live page behind it: a tab still open on the old one would happily
    charge for a box nobody is going to bake, or charge the same order
    twice. True means Stripe closed it. False means it could not be closed:
    already paid, already lapsed, or Stripe unreachable. None of those is an
    error here — the caller treats False as "money may still land" and the
    payment webhook's own guards send back whatever does.
    """
    _ready()
    try:
        stripe.checkout.Session.expire(session_id)
    except Exception as exc:
        # Routine, not exceptional: a session that completed or lapsed a
        # moment ago refuses to expire. One line, no traceback.
        logger.info("checkout session %s could not be closed: %s", session_id, exc)
        return False
    return True


def refund(payment_intent: str, amount_cents: int, sequence: int = 0) -> dict:
    """Send money back to the card, less any amount the shop keeps.

    Refunds follow their payment intent's currency by Stripe's own rule —
    no currency is passed here, none could disagree — and every payment
    intent this gateway creates was built from its order's validated
    currency (see create_checkout_session), so money always returns in the
    currency it arrived in. The amount is the caller's to get right: it is
    the order's own minor units, never converted.

    The idempotency key is deterministic — the same refund attempted again
    (a repeated cancel click racing the lock, a retry after an uncertain
    outcome) collapses onto the original refund at Stripe instead of
    issuing a second one, while a genuinely different amount gets its own
    key and proceeds. Raises GatewayUnavailable on failure — the caller
    decides how honest to be about a refund that hasn't actually happened.

    Returns {"id", "status"} — Stripe's own id for the refund and its word
    on where it stands ("succeeded" for cards; bank methods may answer
    "pending" and confirm later by webhook), so the ledger records what the
    provider said, not what the shop hoped.
    """
    _ready()
    try:
        created = stripe.Refund.create(
            payment_intent=payment_intent,
            amount=amount_cents,
            # `sequence` is how many refunds this payment already has: the
            # first keeps the long-standing key; a deliberate further refund
            # of the same amount gets its own, so it is not mistaken for a
            # retry of the first and swallowed.
            idempotency_key=f"refund:{payment_intent}:{amount_cents}"
            + (f":{sequence}" if sequence else ""),
        )
    except Exception:
        logger.exception("refunding payment intent %s failed", payment_intent)
        raise GatewayUnavailable("Stripe did not answer") from None
    return {
        "id": getattr(created, "id", None),
        "status": getattr(created, "status", None) or "succeeded",
    }


# --------------------------------------------------------------------------
# Read-only diagnostics (scripts/check_payments.py) — nothing here can move
# money or change the account.
# --------------------------------------------------------------------------

def account_status() -> dict:
    """Whether the account behind the key may take payments at all:
    {"charges_enabled", "payouts_enabled", "details_submitted", "country",
    "default_currency"}. Raises GatewayUnavailable when Stripe refuses the
    key or cannot be reached."""
    _ready()
    try:
        data = stripe.Account.retrieve().to_dict()
    except Exception as exc:
        logger.info("reading the Stripe account failed: %s", type(exc).__name__)
        raise GatewayUnavailable(f"Stripe refused or did not answer ({type(exc).__name__})") from None
    return {
        key: data.get(key)
        for key in ("charges_enabled", "payouts_enabled", "details_submitted", "country", "default_currency")
    }


def webhook_endpoints() -> list[dict]:
    """The webhook endpoints registered on the account, as plain values:
    [{"id", "url", "status", "enabled_events"}]. (Their signing secrets are
    never readable after creation — Stripe shows each exactly once.)"""
    _ready()
    try:
        listing = stripe.WebhookEndpoint.list(limit=100).to_dict()
    except Exception as exc:
        logger.info("listing Stripe webhook endpoints failed: %s", type(exc).__name__)
        raise GatewayUnavailable(f"Stripe refused or did not answer ({type(exc).__name__})") from None
    return [
        {
            "id": item.get("id"),
            "url": item.get("url"),
            "status": item.get("status"),
            "enabled_events": list(item.get("enabled_events") or []),
        }
        for item in listing.get("data") or []
        if isinstance(item, dict)
    ]


def dashboard_payment_methods() -> list[str]:
    """The methods switched on in the account's default payment-method
    configuration — what "automatic" can offer at checkout, before Stripe
    narrows it by customer, amount and device."""
    _ready()
    try:
        listing = stripe.PaymentMethodConfiguration.list(limit=20).to_dict()
    except Exception as exc:
        logger.info("listing Stripe payment method configurations failed: %s", type(exc).__name__)
        raise GatewayUnavailable(f"Stripe refused or did not answer ({type(exc).__name__})") from None
    configs = [c for c in listing.get("data") or [] if isinstance(c, dict)]
    chosen = next((c for c in configs if c.get("is_default")), configs[0] if configs else {})
    return sorted(
        name
        for name, value in chosen.items()
        if isinstance(value, dict)
        and value.get("available")
        and (value.get("display_preference") or {}).get("value") == "on"
    )


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
