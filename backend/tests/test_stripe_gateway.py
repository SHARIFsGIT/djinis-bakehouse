"""The real gateway's checkout-page builder and webhook seam, proven
without the wire.

test_api.py swaps the whole stripe_gateway module for a fake so the API
suite never talks to Stripe; this file loads a private second copy of the
module (as part of the app package, so its relative config import works)
and runs the real create_checkout_session against a stub SDK. What matters
here is the hosted page's shape: itemised lines that sum to the invoice to
the cent, the coupon as a real discount, the bakery branding, the copy
slots — and that nothing about a photo can break a payment.

The webhook half pins a contract, not just code: genuinely signed payloads
go through the real SDK verification (offline — it is pure HMAC) and must
come back as the plain JSON the handler speaks. The production bug this
guards against: the SDK's Event object refuses .get() by design, so a
hand-written-dict handler 500ed on every live delivery while the fake,
which returns dicts, kept the whole suite green.
"""

import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "stripe_gateway.py"
BASE_URL = "https://shop.test"


class StubSession:
    """Shaped like the SDK's Session on purpose: no .get() — StripeObject
    refuses dict method names — only the documented conversions."""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        raise AttributeError(
            f"'get' is a dict method, but a {type(self).__name__} is not a dict"
        )

    def to_dict(self, recursive: bool = True, for_json: bool = False) -> dict:
        return self._data


class StubStripe:
    """Records every gateway call; no request leaves the process."""

    api_key = None
    raise_on_create = False
    raise_on_retrieve = False

    def __init__(self):
        self.coupons: list[dict] = []
        self.sessions: list[dict] = []
        self.retrieved: list[str] = []
        self.retrieve_result = StubSession(
            {
                "id": "cs_test_page",
                "status": "complete",
                "payment_status": "paid",
                "payment_intent": "pi_test_1",
                "url": "https://checkout.stripe.test/cs_test_page",
            }
        )
        self.expired: list[str] = []
        self.raise_on_expire = False
        self.checkout = SimpleNamespace(
            Session=SimpleNamespace(
                create=self._session, retrieve=self._retrieve, expire=self._expire
            )
        )
        self.Coupon = SimpleNamespace(create=self._coupon)
        self.refunds: list[dict] = []
        self.refund_status: str | None = None
        self.Refund = SimpleNamespace(create=self._refund)

    def _coupon(self, **kwargs):
        self.coupons.append(kwargs)
        return SimpleNamespace(id=f"coupon_{len(self.coupons)}")

    def _session(self, **kwargs):
        if self.raise_on_create:
            raise RuntimeError("stripe down")
        self.sessions.append(kwargs)
        return SimpleNamespace(id="cs_test_page", url="https://checkout.stripe.test/cs_test_page")

    def _retrieve(self, session_id: str):
        if self.raise_on_retrieve:
            raise RuntimeError("stripe down")
        self.retrieved.append(session_id)
        return self.retrieve_result

    def _expire(self, session_id: str):
        if self.raise_on_expire:
            # what Stripe answers for a session that is already paid or lapsed
            raise RuntimeError("Only Checkout Sessions with a status of open can be expired")
        self.expired.append(session_id)

    def _refund(self, **kwargs):
        self.refunds.append(kwargs)
        refund = SimpleNamespace(id=f"re_{len(self.refunds)}")
        if self.refund_status is not None:
            refund.status = self.refund_status
        return refund


def load_real_gateway(stub: StubStripe | None = None):
    spec = importlib.util.spec_from_file_location("app._stripe_gateway_real", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if stub is not None:
        # Only the API-calling tests swap the SDK — webhook verification
        # needs the real stripe.Webhook, which runs offline.
        module.stripe = stub
    return module


def make_order(
    *,
    unit_price=3.5,
    quantity=4,
    discount=0.0,
    coupon_code=None,
    shipping=3.50,
    tax=0.70,
    photo="/static/products/croissant.jpg",
    currency="eur",
):
    """An order shaped like the API's own output (croissants by default),
    totals pre-computed with the shop's money math."""
    product = SimpleNamespace(image_url=photo) if photo else None
    item = SimpleNamespace(
        product_name="Butter Croissant",
        unit_price=unit_price,
        quantity=quantity,
        subtotal=round(unit_price * quantity, 2),
        product=product,
    )
    subtotal = round(unit_price * quantity, 2)
    taxable = round(subtotal - discount, 2)
    return SimpleNamespace(
        id=7,
        order_number="DB-TEST01",
        items=[item],
        subtotal=subtotal,
        discount_amount=discount,
        coupon_code=coupon_code,
        tax=tax,
        shipping_fee=shipping,
        total=round(taxable + tax + shipping, 2),
        user=SimpleNamespace(email="taster@example.com"),
        currency=currency,
    )


def line_total_cents(session_kwargs) -> int:
    """Σ unit_amount × quantity across every line, in integer cents."""
    return sum(
        line["price_data"]["unit_amount"] * line["quantity"]
        for line in session_kwargs["line_items"]
    )


def test_lines_sum_to_the_invoice_and_the_page_is_dressed(monkeypatch) -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    # An approved 5% rate gives the tax row its storefront-matching name.
    monkeypatch.setattr(gateway.get_settings(), "tax_rate", 0.05, raising=True)
    order = make_order()  # 14.00 + 0.70 tax + 3.50 delivery = 18.20

    result = gateway.create_checkout_session(order, BASE_URL)

    assert result == {"id": "cs_test_page", "url": "https://checkout.stripe.test/cs_test_page"}
    assert len(stub.sessions) == 1
    kwargs = stub.sessions[0]

    # The page can never disagree with the invoice: lines are the stored
    # subtotal + tax + delivery, in integer cents.
    assert line_total_cents(kwargs) == gateway.cents(float(order.total)) == 1820

    # One line per product, with its photo as an absolute URL.
    croissant = kwargs["line_items"][0]
    assert croissant["price_data"]["product_data"]["name"] == "Butter Croissant"
    assert croissant["price_data"]["unit_amount"] == 350
    assert croissant["quantity"] == 4
    assert croissant["price_data"]["product_data"]["images"] == [
        f"{BASE_URL}/static/products/croissant.jpg"
    ]
    # Then delivery and tax rows, named like the storefront's checkout.
    names = [line["price_data"]["product_data"]["name"] for line in kwargs["line_items"]]
    assert names == ["Butter Croissant", "Delivery", "Tax (5%)"]
    delivery = kwargs["line_items"][1]
    assert delivery["price_data"]["unit_amount"] == 350

    # The bakery's palette, a serif face, and the two copy slots.
    assert kwargs["branding_settings"] == {
        "background_color": "#f6f1ee",
        "button_color": "#7f2230",
        "border_style": "rounded",
        "font_family": "lora",
        "display_name": "Djini's Bakehouse",
    }
    assert kwargs["custom_text"]["submit"]["message"]
    assert kwargs["custom_text"]["after_submit"]["message"]

    # Reconciliation and routing, unchanged from the simple era.
    assert kwargs["client_reference_id"] == "DB-TEST01"
    assert kwargs["customer_email"] == "taster@example.com"
    assert kwargs["metadata"] == {"order_id": "7"}
    assert kwargs["payment_intent_data"]["description"] == "Order DB-TEST01"
    assert kwargs["success_url"] == f"{BASE_URL}/account/orders/7?placed=1&stripe=return"
    assert kwargs["cancel_url"] == f"{BASE_URL}/account/orders/7?stripe=cancelled"
    assert 0 < kwargs["expires_at"] - int(time.time()) <= gateway.SESSION_LIFETIME_SECONDS


def test_coupon_discount_rides_as_a_real_stripe_discount() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    # A 60.00 basket — free delivery territory — with 10% off: 60.00 − 6.00,
    # tax on 54.00, total 58.73.
    order = make_order(
        unit_price=15.0, discount=6.0, coupon_code="WELCOME10", shipping=0.0, tax=4.73
    )

    gateway.create_checkout_session(order, BASE_URL)

    kwargs = stub.sessions[0]
    # Delivery shows even when free — a zero line renders as free.
    delivery = kwargs["line_items"][1]
    assert delivery["price_data"]["unit_amount"] == 0
    # One one-off coupon for exactly the stored discount, named with the code.
    assert stub.coupons == [
        {
            "amount_off": 600,
            "currency": kwargs["line_items"][0]["price_data"]["currency"],
            "duration": "once",
            "name": "WELCOME10",
            "metadata": {"order_number": "DB-TEST01"},
        }
    ]
    assert kwargs["discounts"] == [{"coupon": "coupon_1"}]
    # Lines minus the discount still equal the invoice: 6473 − 600 = 58.73.
    assert line_total_cents(kwargs) == 6473
    assert line_total_cents(kwargs) - 600 == gateway.cents(float(order.total)) == 5873


def test_no_coupon_no_discounts_key() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.create_checkout_session(make_order(), BASE_URL)

    assert stub.coupons == []
    assert "discounts" not in stub.sessions[0]


def test_photo_troubles_never_break_payment() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    # No product behind the line (deleted since), no photo at all, an
    # already-absolute URL, and a lookup that explodes — all fine, all
    # session-creating, only the thumbnail changes.
    for photo_setup in (
        SimpleNamespace(product=None, image_url=None),  # product gone
        SimpleNamespace(product=SimpleNamespace(image_url=None)),  # no photo
        SimpleNamespace(product=SimpleNamespace(image_url="https://cdn.test/c.jpg")),
    ):
        order = make_order()
        order.items[0].product = photo_setup
        gateway.create_checkout_session(order, BASE_URL)

    class Exploding:
        @property
        def product(self):
            raise RuntimeError("detached instance")

    order = make_order()
    order.items[0] = SimpleNamespace(
        product_name="Butter Croissant", unit_price=3.5, quantity=4, product=Exploding()
    )
    result = gateway.create_checkout_session(order, BASE_URL)
    assert result["id"] == "cs_test_page"
    assert "images" not in stub.sessions[-1]["line_items"][0]["price_data"]["product_data"]


def test_a_silent_stripe_becomes_gateway_unavailable() -> None:
    stub = StubStripe()
    stub.raise_on_create = True
    gateway = load_real_gateway(stub)

    with pytest.raises(gateway.GatewayUnavailable):
        gateway.create_checkout_session(make_order(), BASE_URL)
    assert stub.sessions == []


# ── Currency safety: the order's money, not the setting's ─────────────────


def test_every_line_speaks_the_currency_the_order_was_placed_in() -> None:
    """Phase 1's core seam, after the 2026-11-05 cutover: the currency
    rides on the order itself — and the shop's one charge currency is
    euro, so every line of the hosted page speaks it."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.create_checkout_session(make_order(), BASE_URL)  # eur

    currencies = [
        {line["price_data"]["currency"] for line in kwargs["line_items"]}
        for kwargs in stub.sessions
    ]
    assert currencies == [{"eur"}]


def test_an_orders_currency_beats_the_global_setting(monkeypatch) -> None:
    """The order's own money, not the setting's: flipping the configured
    currency cannot relabel the order on the payment page. (usd can't
    legitimately be configured any more — EUR-only since the cutover —
    which is exactly why this seam matters: a relapsed or mis-set knob
    still can't redraw an order's money.)"""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    monkeypatch.setattr(gateway.get_settings(), "stripe_currency", "usd", raising=True)

    gateway.create_checkout_session(make_order(), BASE_URL)  # placed as eur

    assert {line["price_data"]["currency"] for line in stub.sessions[0]["line_items"]} == {"eur"}


def test_coupon_discounts_ride_in_the_orders_currency() -> None:
    """The discount is money in the same currency as the lines it comes off
    — a euro order's coupon is a euro coupon, to the cent."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    order = make_order(
        unit_price=15.0,
        discount=6.0,
        coupon_code="WELCOME10",
        shipping=0.0,
        tax=4.73,
        currency="eur",
    )

    gateway.create_checkout_session(order, BASE_URL)

    assert stub.coupons[0]["currency"] == "eur"
    assert stub.coupons[0]["amount_off"] == 600
    assert {line["price_data"]["currency"] for line in stub.sessions[0]["line_items"]} == {"eur"}


def test_an_order_without_a_chargeable_currency_is_refused_not_guessed() -> None:
    """Missing, blank and unsupported codes all refuse before Stripe is
    touched. The gateway never falls back to the shop's current currency:
    guessing would charge an order in money it wasn't placed in. USD is on
    the list since the EUR-only cutover — dollar history stays readable
    everywhere, but it can never be charged again."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    for bad in (None, "", "   ", "usd", "USD", "gbp", "bitcoin"):
        with pytest.raises(gateway.InvalidOrderCurrency):
            gateway.create_checkout_session(make_order(currency=bad), BASE_URL)

    assert stub.sessions == []
    assert stub.coupons == []


def test_the_tax_row_never_prints_an_unapproved_rate(monkeypatch) -> None:
    """With no approved tax rate (None while VAT treatment is pending), a
    taxed order's row is named plainly — the page must not print a
    percentage the shop can't stand behind, and must not crash either."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    monkeypatch.setattr(gateway.get_settings(), "tax_rate", None, raising=True)

    gateway.create_checkout_session(make_order(tax=0.70), BASE_URL)

    names = [
        line["price_data"]["product_data"]["name"] for line in stub.sessions[0]["line_items"]
    ]
    assert names == ["Butter Croissant", "Delivery", "Tax"]


def test_upper_case_currency_codes_normalize() -> None:
    """ISO codes are case-insensitive — " EUR " stored on an order is still
    a euro order, normalized rather than refused."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.create_checkout_session(make_order(currency=" EUR "), BASE_URL)

    assert {line["price_data"]["currency"] for line in stub.sessions[0]["line_items"]} == {"eur"}


def test_refunds_carry_no_currency_they_follow_their_payment_intent() -> None:
    """Stripe refunds follow the payment intent's currency by its own rule;
    the gateway passes none, so nothing can ever disagree with the currency
    the intent — built from its order's validated currency — was charged in."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.refund("pi_test_1", 500)

    (call,) = stub.refunds
    assert "currency" not in call
    assert call["payment_intent"] == "pi_test_1"
    assert call["amount"] == 500


def test_refund_carries_a_deterministic_idempotency_key() -> None:
    """A retried refund must collapse onto the original at Stripe instead
    of issuing a second one: the key is built from the payment intent and
    the amount, so the same logical refund keeps one key while a genuinely
    different amount gets its own."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.refund("pi_test_1", 500)
    gateway.refund("pi_test_1", 500)  # the retry after an uncertain outcome
    gateway.refund("pi_test_1", 700)  # a different, legitimate amount

    keys = [call["idempotency_key"] for call in stub.refunds]
    assert keys == ["refund:pi_test_1:500", "refund:pi_test_1:500", "refund:pi_test_1:700"]
    assert [call["amount"] for call in stub.refunds] == [500, 500, 700]
    assert all(call["payment_intent"] == "pi_test_1" for call in stub.refunds)


# ── The webhook seam: verified bytes in, plain JSON out ──────────────────

WEBHOOK_SECRET = "whsec_unit_tests"


def signed_header(
    payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None
) -> str:
    """A real Stripe-Signature header: t=…,v1=HMAC-SHA256(secret, "t.payload")."""
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def completed_event_payload() -> bytes:
    return json.dumps(
        {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_page",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_1",
                    "metadata": {"order_id": "7"},
                }
            },
        }
    ).encode()


def test_verify_webhook_hands_back_plain_json(monkeypatch) -> None:
    gateway = load_real_gateway()  # the real stripe.Webhook — offline, pure HMAC
    monkeypatch.setattr(gateway.get_settings(), "stripe_webhook_secret", WEBHOOK_SECRET)
    payload = completed_event_payload()

    event = gateway.verify_webhook(payload, signed_header(payload))

    # Exactly the access the live handler does — the calls that 500ed in
    # production when the event was an SDK object.
    assert isinstance(event, dict)
    assert event.get("type") == "checkout.session.completed"
    session = event.get("data", {}).get("object")
    assert isinstance(session, dict)
    assert session.get("metadata", {}).get("order_id") == "7"
    assert session["payment_intent"] == "pi_test_1"


def test_verify_webhook_rejects_everything_not_provably_from_stripe(monkeypatch) -> None:
    gateway = load_real_gateway()
    monkeypatch.setattr(gateway.get_settings(), "stripe_webhook_secret", WEBHOOK_SECRET)
    payload = completed_event_payload()
    array_payload = json.dumps(["not", "an", "event"]).encode()

    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(payload, signed_header(payload, secret="whsec_someone_elses"))
    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(payload, "")  # no header at all
    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(payload, "v1=deadbeef")  # unparseable header
    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(  # replayed from an hour ago
            payload, signed_header(payload, timestamp=int(time.time()) - 3600)
        )
    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(b"not even json", signed_header(b"not even json"))
    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(array_payload, signed_header(array_payload))


def test_verify_webhook_without_a_configured_secret_accepts_nothing(monkeypatch) -> None:
    gateway = load_real_gateway()
    monkeypatch.setattr(gateway.get_settings(), "stripe_webhook_secret", "")
    payload = completed_event_payload()

    with pytest.raises(gateway.BadSignature):
        gateway.verify_webhook(payload, signed_header(payload))


def test_retrieve_session_returns_plain_values() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    session = gateway.retrieve_session("cs_test_page")

    assert stub.retrieved == ["cs_test_page"]
    assert session == {
        "status": "complete",
        "payment_status": "paid",
        "payment_intent": "pi_test_1",
        "url": "https://checkout.stripe.test/cs_test_page",
        # what the page charged rides along for reconciliation — absent
        # from this sparse stub, so both read None rather than raising
        "amount_total": None,
        "currency": None,
    }

    # ...and when Stripe does say what it charged, the figures come through
    stub.retrieve_result = StubSession(
        {"id": "cs_test_page", "status": "complete", "payment_status": "paid",
         "payment_intent": "pi_test_1", "amount_total": 1820, "currency": "eur"}
    )
    charged = gateway.retrieve_session("cs_test_page")
    assert (charged["amount_total"], charged["currency"]) == (1820, "eur")

    # A sparse session — still open, nothing paid yet — yields Nones, never
    # an attribute error; an unreachable Stripe stays GatewayUnavailable.
    stub.retrieve_result = StubSession({"id": "cs_test_page", "status": "open"})
    assert gateway.retrieve_session("cs_test_page") == {
        "status": "open",
        "payment_status": None,
        "payment_intent": None,
        "url": None,
        "amount_total": None,
        "currency": None,
    }
    stub.raise_on_retrieve = True
    with pytest.raises(gateway.GatewayUnavailable):
        gateway.retrieve_session("cs_test_page")


# ── Which payment methods the hosted page offers ────────────────────────
#
# These replace the tests that pinned a static `payment_method_types` list.
# The SDK the shop runs (16.x, API 2026-10-28) no longer has that parameter
# on session creation: Stripe computes the eligible methods itself, and a
# shop may only *filter* that set (allowed_payment_method_types) or exclude
# from it. Sending the old list is exactly what these tests now forbid.

def test_by_default_stripe_chooses_the_payment_methods(monkeypatch) -> None:
    """No list, no filter: every method switched on in the Stripe dashboard
    that suits the customer and device — cards, wallets, bank methods —
    without the shop hardcoding (and so possibly mis-asking for) any."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    for unset in ("automatic", "", "  ", "Automatic"):
        monkeypatch.setattr(gateway.get_settings(), "stripe_payment_methods", unset, raising=True)
        assert gateway.allowed_payment_methods() is None

    monkeypatch.setattr(gateway.get_settings(), "stripe_payment_methods", "automatic", raising=True)
    gateway.create_checkout_session(make_order(), BASE_URL)
    sent = stub.sessions[0]
    assert "payment_method_types" not in sent  # the retired parameter is never sent
    assert "allowed_payment_method_types" not in sent
    assert "excluded_payment_method_types" not in sent


def test_an_explicit_list_only_filters_stripes_own_set(monkeypatch) -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    monkeypatch.setattr(
        gateway.get_settings(), "stripe_payment_methods", "paypal,card,klarna,card", raising=True
    )
    assert gateway.allowed_payment_methods() == ["card", "paypal", "klarna"]  # card first, no repeats

    gateway.create_checkout_session(make_order(), BASE_URL)
    sent = stub.sessions[0]
    assert sent["allowed_payment_method_types"] == ["card", "paypal", "klarna"]
    assert "payment_method_types" not in sent


def test_unknown_method_names_drop_out_and_never_reach_stripe(monkeypatch) -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    # a typo and a method Stripe retired — with one real name among them
    monkeypatch.setattr(
        gateway.get_settings(), "stripe_payment_methods", "crypto,giropay,sepa_debit", raising=True
    )
    assert gateway.allowed_payment_methods() == ["sepa_debit"]

    # nothing usable left: Stripe's automatic set, never an empty filter
    monkeypatch.setattr(
        gateway.get_settings(), "stripe_payment_methods", "crypto,giropay", raising=True
    )
    assert gateway.allowed_payment_methods() is None
    gateway.create_checkout_session(make_order(), BASE_URL)
    assert "allowed_payment_method_types" not in stub.sessions[0]


def test_stripes_page_never_shows_a_second_paypal(monkeypatch) -> None:
    """With PayPal configured as its own provider, the shop's checkout has
    a PayPal button already — Stripe's page must not offer another."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    settings = gateway.get_settings()
    monkeypatch.setattr(settings, "paypal_client_id", "client", raising=True)
    monkeypatch.setattr(settings, "paypal_client_secret", "secret", raising=True)

    monkeypatch.setattr(settings, "stripe_payment_methods", "automatic", raising=True)
    gateway.create_checkout_session(make_order(), BASE_URL)
    assert stub.sessions[0]["excluded_payment_method_types"] == ["paypal"]

    monkeypatch.setattr(settings, "stripe_payment_methods", "card,paypal,klarna", raising=True)
    gateway.create_checkout_session(make_order(), BASE_URL)
    assert stub.sessions[1]["allowed_payment_method_types"] == ["card", "klarna"]
    assert "excluded_payment_method_types" not in stub.sessions[1]


def test_closing_a_session_reports_whether_stripe_closed_it() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    assert gateway.expire_session("cs_test_page") is True
    assert stub.expired == ["cs_test_page"]

    # already paid or lapsed: not an error, just "may still hold money"
    stub.raise_on_expire = True
    assert gateway.expire_session("cs_test_page") is False


def test_a_refund_hands_back_stripes_own_id_and_status() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    assert gateway.refund("pi_test_1", 500) == {"id": "re_1", "status": "succeeded"}

    stub.refund_status = "pending"  # a bank method still settling
    assert gateway.refund("pi_test_1", 700) == {"id": "re_2", "status": "pending"}


def test_a_deliberate_further_refund_gets_its_own_idempotency_key() -> None:
    """The first refund on a payment keeps the long-standing key. A second
    one of the same amount is a new decision, not a retry — without its
    own key Stripe would answer with the first refund and send nothing."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.refund("pi_test_1", 500)
    gateway.refund("pi_test_1", 500, 1)
    gateway.refund("pi_test_1", 500, 1)  # ...whose own retry still collapses

    assert [call["idempotency_key"] for call in stub.refunds] == [
        "refund:pi_test_1:500",
        "refund:pi_test_1:500:1",
        "refund:pi_test_1:500:1",
    ]


def test_the_key_names_the_mode_it_would_charge_in(monkeypatch) -> None:
    gateway = load_real_gateway(StubStripe())
    settings = gateway.get_settings()
    for key, expected in (
        ("", None),
        ("sk_test_abc", "test"),
        ("rk_test_abc", "test"),
        ("sk_live_abc", "live"),
        ("rk_live_abc", "live"),
    ):
        monkeypatch.setattr(settings, "stripe_secret_key", key, raising=True)
        assert gateway.mode() == expected


def test_inclusive_mode_never_adds_a_second_tax_row(monkeypatch) -> None:
    """VAT-inclusive orders carry their tax inside every line — a separate
    tax row would charge it twice, so the hosted page must not show one.
    The order here is genuinely inclusive-shaped: €14.00 gross lines (their
    €0.92 VAT inside) + €3.50 delivery = €17.50 payable."""
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    monkeypatch.setattr(gateway.get_settings(), "tax_mode", "inclusive", raising=True)

    item = SimpleNamespace(product=None)  # no photo on purpose
    order = SimpleNamespace(
        id=9,
        order_number="DB-INCL01",
        items=[SimpleNamespace(product_name="Butter Croissant", unit_price=3.50, quantity=4, product=item)],
        subtotal=14.00,
        discount_amount=0.0,
        coupon_code=None,
        tax=1.12,           # the VAT inside the figures (products + delivery)
        shipping_fee=3.50,
        total=17.50,
        user=SimpleNamespace(email="taster@example.com"),
        currency="eur",
    )

    gateway.create_checkout_session(order, BASE_URL)
    kwargs = stub.sessions[0]
    names = [line["price_data"]["product_data"]["name"] for line in kwargs["line_items"]]
    assert names == ["Butter Croissant", "Delivery"]  # no Tax row
    # and the lines still sum to the invoice, VAT inside
    assert line_total_cents(kwargs) == gateway.cents(float(order.total)) == 1750
