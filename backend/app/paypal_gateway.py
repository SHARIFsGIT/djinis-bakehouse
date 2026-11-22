"""The PayPal seam.

Every call to PayPal — asking for a token, creating an order, capturing it,
refunding, verifying a webhook — passes through this module, exactly as
every Stripe call passes through stripe_gateway: the rest of the app never
builds a PayPal URL or reads a PayPal response, and the test suite swaps
these functions for fakes instead of mocking the wire.

PayPal Checkout, the Orders v2 way, with nothing of PayPal's running in the
shop's pages: the server creates the order, the customer approves it on
PayPal's own site, and the server captures the money when they come back.
That last step is the one that matters — PayPal never takes money for an
order the shop did not, itself, ask it to capture. An order that was
cancelled in the meantime is simply never captured.

Stdlib only (urllib), like the Twilio and Facebook gateways: no SDK to
install, no extra dependency to keep in step with a payment API.

Sandbox and live are two different PayPals with two different sets of
credentials. PAYPAL_ENVIRONMENT picks one; credentials for the other are
refused at the first call, so a mix-up fails loudly instead of charging.
"""

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

from .config import get_settings

logger = logging.getLogger("bakery.payments")

API_BASES = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}

# One wait for every call — a shopper is watching a spinner, and a worker
# thread may be holding an order's row lock.
REQUEST_TIMEOUT_SECONDS = 20

# What the shop charges in. The same single-currency rule as the Stripe
# gateway (EUR only, by the owner's 2026-11-05 decision).
SUPPORTED_CURRENCIES = ("eur",)

# The events the shop's webhook must be subscribed to at PayPal — each one
# is acted on by app/routers/payments.py. The capture the customer's return
# (or CHECKOUT.ORDER.APPROVED) triggers is confirmed without any of them;
# refunds and disputes made at PayPal are only ever heard of through these.
WEBHOOK_EVENTS = (
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.DECLINED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.RESOLVED",
)

# PayPal order states in which the customer can still approve the payment.
AWAITING_APPROVAL = ("CREATED", "SAVED", "PAYER_ACTION_REQUIRED")


class GatewayUnavailable(Exception):
    """PayPal could not be reached, or answered with a server error — no
    money moved, and trying again later is reasonable."""


class PayPalRefused(GatewayUnavailable):
    """PayPal understood the request and said no. `issue` is PayPal's own
    machine-readable reason (ORDER_NOT_APPROVED, INSTRUMENT_DECLINED, …) —
    callers branch on it; the message is safe to log, never to show."""

    def __init__(self, status: int, name: str, issue: str, message: str):
        self.status = status
        self.name = name
        self.issue = issue
        super().__init__(f"{status} {name} {issue}: {message}".strip())


class InvalidOrderCurrency(Exception):
    """The order's stored currency can't be charged through this gateway —
    refused, never relabelled (the same guard the Stripe gateway keeps)."""


class BadSignature(Exception):
    """A webhook could not be proven to come from PayPal."""


def configured() -> bool:
    """The PayPal option goes live the moment both credentials exist — and
    the environment they belong to is one of the two PayPals there are. A
    PAYPAL_ENVIRONMENT that is neither is not guessed at: PayPal stays off."""
    settings = get_settings()
    return bool(
        settings.paypal_client_id and settings.paypal_client_secret and environment() in API_BASES
    )


def misconfigured() -> str | None:
    """Why PayPal is off although credentials exist — for the boot log —
    or None when there is nothing to say."""
    settings = get_settings()
    if settings.paypal_client_id and settings.paypal_client_secret and environment() not in API_BASES:
        return (
            f"PAYPAL_ENVIRONMENT is {settings.paypal_environment!r} — it must be 'sandbox' or "
            "'live'. PayPal stays OFF until it is; which PayPal takes the money is never a guess."
        )
    return None


def environment() -> str:
    """"sandbox" or "live" — which PayPal the credentials belong to."""
    return get_settings().paypal_environment.strip().lower()


def webhook_ready() -> bool:
    return bool(get_settings().paypal_webhook_id)


def money_str(amount) -> str:
    """An amount the way PayPal's API reads one: a decimal string with two
    places, rounded half-up from an exact Decimal — never a float."""
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _cents(amount) -> int:
    return int(Decimal(money_str(amount)) * 100)


def order_currency(order) -> str:
    code = str(getattr(order, "currency", "") or "").strip().lower()
    if code not in SUPPORTED_CURRENCIES:
        raise InvalidOrderCurrency(
            f"order {getattr(order, 'order_number', '?')} has an unchargeable currency "
            f"({code or 'none'}); refusing to guess one"
        )
    return code


# --------------------------------------------------------------------------
# The wire
# --------------------------------------------------------------------------

_token_lock = threading.Lock()
_token: dict = {"value": None, "expires": 0.0, "identity": None}


def _base_url() -> str:
    base = API_BASES.get(environment())
    if base is None:
        # Reachable only around configured() — a webhook arriving while the
        # setting is wrong, say. Refused like any other unreachable PayPal.
        raise GatewayUnavailable("PAYPAL_ENVIRONMENT is neither 'sandbox' nor 'live'")
    return base


def _read_error(exc: urllib.error.HTTPError) -> tuple[str, str, str]:
    """PayPal's (name, issue, message) out of an error response — whatever
    shape it came in, something loggable always comes back."""
    try:
        data = json.loads(exc.read().decode("utf-8", "replace") or "{}")
    except (ValueError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    details = data.get("details") or []
    issue = ""
    if details and isinstance(details[0], dict):
        issue = str(details[0].get("issue") or "")
    name = str(data.get("name") or data.get("error") or "")
    message = str(data.get("message") or data.get("error_description") or "")
    return name, issue, message


def _access_token(force: bool = False) -> str:
    """A bearer token for the configured credentials, cached until shortly
    before PayPal says it lapses. The cache is keyed on the environment and
    client id, so changing either can never serve the other's token."""
    settings = get_settings()
    identity = (environment(), settings.paypal_client_id)
    with _token_lock:
        if (
            not force
            and _token["value"]
            and _token["identity"] == identity
            and time.time() < _token["expires"]
        ):
            return _token["value"]
        credentials = base64.b64encode(
            f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode()
        ).decode()
        request = urllib.request.Request(
            f"{_base_url()}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            name, _issue, message = _read_error(exc)
            # Wrong or mismatched credentials (sandbox keys against live, or
            # the reverse) land here — say which PayPal refused them.
            logger.error(
                "PayPal (%s) refused the shop's credentials: %s %s %s",
                environment(), exc.code, name, message,
            )
            raise GatewayUnavailable("PayPal refused the shop's credentials") from None
        except Exception as exc:
            logger.warning("PayPal token request failed (%s)", type(exc).__name__)
            raise GatewayUnavailable("PayPal did not answer") from None
        token = data.get("access_token")
        if not token:
            raise GatewayUnavailable("PayPal answered without a token")
        _token.update(
            value=token,
            identity=identity,
            # a minute of slack, so a token never expires mid-request
            expires=time.time() + max(60, int(data.get("expires_in") or 0) - 60),
        )
        return token


def _call(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    raw_body: bytes | None = None,
    request_id: str | None = None,
) -> dict:
    """One authenticated JSON call. Raises PayPalRefused for a 4xx (with
    PayPal's own issue code) and GatewayUnavailable for everything that
    means "no answer" — so no caller ever handles a raw HTTP error."""
    payload = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None
    )
    for attempt in (1, 2):
        headers = {
            "Authorization": f"Bearer {_access_token(force=attempt == 2)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            # PayPal's idempotency key: the same id returns the first
            # answer instead of doing the thing twice.
            headers["PayPal-Request-Id"] = request_id
        request = urllib.request.Request(
            f"{_base_url()}{path}", data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                text = response.read().decode() or "{}"
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                continue  # the cached token lapsed early — one fresh try
            name, issue, message = _read_error(exc)
            if exc.code >= 500:
                logger.warning("PayPal %s %s answered %s", method, path, exc.code)
                raise GatewayUnavailable("PayPal did not answer") from None
            logger.warning(
                "PayPal refused %s %s: %s %s %s %s", method, path, exc.code, name, issue, message
            )
            raise PayPalRefused(exc.code, name, issue, message) from None
        except GatewayUnavailable:
            raise
        except Exception as exc:  # timeouts, DNS, malformed JSON — one door
            logger.warning("PayPal %s %s failed (%s)", method, path, type(exc).__name__)
            raise GatewayUnavailable("PayPal did not answer") from None
    raise GatewayUnavailable("PayPal did not answer")


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

def _normalize_order(data: dict) -> dict:
    """PayPal's order, reduced to the plain values the shop reads. Amounts
    come back as Decimals, currency codes lower-case like the shop's own."""
    units = data.get("purchase_units") or [{}]
    unit = units[0] if isinstance(units[0], dict) else {}
    amount = unit.get("amount") or {}
    captures = ((unit.get("payments") or {}).get("captures")) or []
    capture = captures[0] if captures and isinstance(captures[0], dict) else {}
    capture_amount = capture.get("amount") or {}
    approve = next(
        (
            link.get("href")
            for link in data.get("links") or []
            if isinstance(link, dict) and link.get("rel") in ("payer-action", "approve")
        ),
        None,
    )

    def _decimal(value):
        try:
            return Decimal(str(value))
        except Exception:
            return None

    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "custom_id": unit.get("custom_id"),
        "amount": _decimal(amount.get("value")) if amount.get("value") is not None else None,
        "currency": str(amount.get("currency_code") or "").lower() or None,
        "capture_id": capture.get("id"),
        "capture_status": capture.get("status"),
        "capture_amount": (
            _decimal(capture_amount.get("value")) if capture_amount.get("value") is not None else None
        ),
        "capture_currency": str(capture_amount.get("currency_code") or "").lower() or None,
        "url": approve,
    }


def _amount_block(order, currency: str) -> tuple[dict, list[dict] | None]:
    """The order's money as PayPal wants it: the total, and — when every
    cent reconciles — the breakdown and line items behind it, so the
    customer reads the same rows on PayPal's page as at the shop.

    The total is the only figure that charges anyone; the breakdown is
    presentation. If the rows don't add up to it exactly (in integer
    cents), they are left out rather than letting PayPal refuse a payment
    over a cosmetic."""
    code = currency.upper()

    def money(value) -> dict:
        return {"currency_code": code, "value": money_str(value)}

    total = {"currency_code": code, "value": money_str(order.total)}
    items = [
        {
            "name": str(item.product_name)[:127],
            "quantity": str(item.quantity),
            "unit_amount": money(item.unit_price),
        }
        for item in order.items
    ]
    item_cents = sum(_cents(item.unit_price) * item.quantity for item in order.items)
    discount_cents = _cents(order.discount_amount or 0)
    shipping_cents = _cents(order.shipping_fee or 0)
    # VAT-inclusive orders carry their tax inside every line (the per-rate
    # ledger on the order is that mode's mark) — a tax row would add it twice.
    tax_cents = 0 if getattr(order, "tax_lines", None) else _cents(order.tax or 0)
    if not items or item_cents + shipping_cents + tax_cents - discount_cents != _cents(order.total):
        return total, None
    breakdown = {
        "item_total": money(Decimal(item_cents) / 100),
        "shipping": money(Decimal(shipping_cents) / 100),
    }
    if tax_cents:
        breakdown["tax_total"] = money(Decimal(tax_cents) / 100)
    if discount_cents:
        breakdown["discount"] = money(Decimal(discount_cents) / 100)
    total["breakdown"] = breakdown
    return total, items


def create_order(order, base_url: str, request_id: str) -> dict:
    """A PayPal order for one shop order, ready for the customer to approve.

    Intent CAPTURE with user action PAY_NOW: what the customer approves on
    PayPal's page is the final amount, and the shop captures it the moment
    they return. `custom_id` carries the shop's own order id back on every
    webhook. (No invoice_id: PayPal can be set to refuse a second order
    under the same one, and a retried payment legitimately needs a second.)

    Returns {"id", "status", "url"} — url is PayPal's approval page.
    Raises GatewayUnavailable (PayPalRefused included) on any failure.
    """
    settings = get_settings()
    currency = order_currency(order)
    amount, items = _amount_block(order, currency)
    unit = {
        "reference_id": order.order_number,
        "custom_id": str(order.id),
        "description": f"Order {order.order_number} — {settings.bakery_name}"[:127],
        "amount": amount,
    }
    if items:
        unit["items"] = items
    body = {
        "intent": "CAPTURE",
        "purchase_units": [unit],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": settings.bakery_name[:127],
                    # The PayPal account page, not PayPal's own card form —
                    # cards are Stripe's job here, and two card forms would
                    # only confuse.
                    "landing_page": "LOGIN",
                    # The shop already holds the delivery address; PayPal
                    # neither asks for one nor swaps in its own.
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": f"{base_url}/account/orders/{order.id}?placed=1&paypal=return",
                    "cancel_url": f"{base_url}/account/orders/{order.id}?paypal=cancelled",
                }
            }
        },
    }
    data = _call("POST", "/v2/checkout/orders", body=body, request_id=request_id)
    normalized = _normalize_order(data)
    if not normalized["id"] or not normalized["url"]:
        logger.error("PayPal created order %s without an approval link", normalized["id"])
        raise GatewayUnavailable("PayPal answered without an approval link")
    return {"id": normalized["id"], "status": normalized["status"], "url": normalized["url"]}


def get_order(paypal_order_id: str) -> dict:
    """The live state of a PayPal order — the shop's direct question, for
    when a webhook is late or never comes."""
    path = f"/v2/checkout/orders/{urllib.parse.quote(paypal_order_id, safe='')}"
    return _normalize_order(_call("GET", path))


def capture_order(paypal_order_id: str) -> dict:
    """Take the money for an approved order. Idempotent at PayPal: the
    request id is derived from the order, so a second capture attempt (the
    return page racing the webhook) returns the first one's result."""
    path = f"/v2/checkout/orders/{urllib.parse.quote(paypal_order_id, safe='')}/capture"
    try:
        data = _call("POST", path, body={}, request_id=f"capture-{paypal_order_id}")
    except PayPalRefused as refusal:
        if refusal.issue == "ORDER_ALREADY_CAPTURED":
            return get_order(paypal_order_id)  # already ours — read what was taken
        raise
    return _normalize_order(data)


def refund_capture(capture_id: str, amount_cents: int, currency: str, request_id: str) -> dict:
    """Send money back against a capture — all of it or part.

    The amount is always stated (never left to PayPal's "refund the rest"
    default), in the capture's own currency, and the request id makes a
    retried refund collapse onto the first instead of paying twice.
    Returns {"id", "status"} — status is PayPal's word (COMPLETED, PENDING,
    FAILED, CANCELLED), so the ledger records what the provider said."""
    path = f"/v2/payments/captures/{urllib.parse.quote(capture_id, safe='')}/refund"
    body = {
        "amount": {
            "currency_code": currency.upper(),
            "value": money_str(Decimal(amount_cents) / 100),
        }
    }
    data = _call("POST", path, body=body, request_id=request_id)
    return {"id": data.get("id"), "status": data.get("status")}


def list_webhooks() -> list[dict]:
    """The webhooks registered for these credentials, as plain values:
    [{"id", "url", "event_types"}]. Read-only — the readiness check uses it
    to see that PAYPAL_WEBHOOK_ID names a real webhook pointing at this shop."""
    data = _call("GET", "/v1/notifications/webhooks")
    return [
        {
            "id": hook.get("id"),
            "url": hook.get("url"),
            "event_types": [
                str(event.get("name"))
                for event in hook.get("event_types") or []
                if isinstance(event, dict) and event.get("name")
            ],
        }
        for hook in data.get("webhooks") or []
        if isinstance(hook, dict)
    ]


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------

_SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def verify_webhook(headers, raw_body: bytes) -> dict:
    """Prove a webhook came from PayPal, then hand back its event.

    PayPal's own verification endpoint is the judge: it is sent the
    transmission headers, the id of the webhook the shop registered, and
    the event exactly as it arrived. "Exactly" is the point — the raw bytes
    are spliced into the request untouched, because re-serialising the JSON
    (a reordered key, a changed escape) makes a genuine event fail.

    Without a configured PAYPAL_WEBHOOK_ID nothing is accepted at all: the
    signature is the only credential this endpoint has.
    """
    settings = get_settings()
    if not settings.paypal_webhook_id:
        raise BadSignature("PAYPAL_WEBHOOK_ID is not set")
    try:
        event = json.loads(raw_body)
    except ValueError:
        raise BadSignature("the webhook body is not JSON") from None
    if not isinstance(event, dict):
        raise BadSignature("the webhook body is not a JSON object")
    envelope = {
        name: (headers.get(header) or "") for name, header in _SIGNATURE_HEADERS.items()
    }
    if not all(envelope.values()):
        raise BadSignature("PayPal's signature headers are missing")
    # PayPal signs with a certificate served from its own domain. A delivery
    # naming any other address is not PayPal's, and is turned away here —
    # before it can cost the shop a call to PayPal for every forged request
    # somebody cares to send.
    cert = urllib.parse.urlsplit(envelope["cert_url"])
    cert_host = (cert.hostname or "").lower()
    if cert.scheme != "https" or not (cert_host == "paypal.com" or cert_host.endswith(".paypal.com")):
        raise BadSignature("the signing certificate is not served by PayPal")
    envelope["webhook_id"] = settings.paypal_webhook_id
    request_body = (
        json.dumps(envelope)[:-1].encode() + b', "webhook_event": ' + raw_body.strip() + b"}"
    )
    try:
        verdict = _call(
            "POST", "/v1/notifications/verify-webhook-signature", raw_body=request_body
        )
    except GatewayUnavailable:
        # Unverifiable is not verified. PayPal retries its deliveries, so a
        # refusal now costs nothing but a later, checkable, redelivery.
        raise BadSignature("PayPal could not be asked to verify the webhook") from None
    if verdict.get("verification_status") != "SUCCESS":
        raise BadSignature("PayPal says the webhook is not genuine")
    return event
