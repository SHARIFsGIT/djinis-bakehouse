"""The real PayPal gateway, proven without the wire.

test_paypal.py swaps the whole gateway for a fake, so the API suite never
talks to PayPal; this file loads a private copy of the module and runs the
real functions against a stubbed `urlopen`. What is pinned here is what
PayPal would actually receive: which host (sandbox and live are different
PayPals), which credentials, which idempotency keys, an amount that adds
up to the cent — and that a webhook is verified against the exact bytes
that arrived.

No request leaves the process.
"""

import base64
import importlib.util
import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "paypal_gateway.py"
BASE_URL = "https://shop.test"
SANDBOX = "https://api-m.sandbox.paypal.com"
LIVE = "https://api-m.paypal.com"


class Wire:
    """A stand-in for urllib's urlopen: records every request and plays
    back scripted answers (a dict → 200 JSON, an Exception → raised)."""

    def __init__(self):
        self.requests: list[SimpleNamespace] = []
        self.script: list = []
        self.token_requests = 0

    def answer(self, *responses):
        self.script.extend(responses)

    def __call__(self, request, timeout=None):
        body = request.data
        seen = SimpleNamespace(
            method=request.get_method(),
            url=request.full_url,
            headers={k.lower(): v for k, v in request.header_items()},
            body=body,
            timeout=timeout,
        )
        if seen.url.endswith("/v1/oauth2/token"):
            self.token_requests += 1
            seen.is_token = True
            self.requests.append(seen)
            return _Response({"access_token": f"token-{self.token_requests}", "expires_in": 3600})
        seen.is_token = False
        self.requests.append(seen)
        if not self.script:
            raise AssertionError(f"unexpected PayPal request: {seen.method} {seen.url}")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Response(nxt)

    @property
    def calls(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if not r.is_token]


class _Response:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode()

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def refusal(status: int, issue: str = "", name: str = "UNPROCESSABLE_ENTITY") -> urllib.error.HTTPError:
    body = json.dumps({"name": name, "message": "refused", "details": [{"issue": issue}]}).encode()
    return urllib.error.HTTPError("https://api.paypal.test", status, "refused", {}, io.BytesIO(body))


@pytest.fixture
def paypal(monkeypatch):
    """A private copy of the real gateway on a stubbed wire, configured
    with sandbox credentials."""
    spec = importlib.util.spec_from_file_location("app._paypal_gateway_real", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wire = Wire()
    monkeypatch.setattr(module.urllib.request, "urlopen", wire)
    settings = module.get_settings()
    monkeypatch.setattr(settings, "paypal_client_id", "client-abc")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret-xyz")
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-REGISTERED")
    monkeypatch.setattr(settings, "paypal_environment", "sandbox")
    module.wire = wire
    return module


def make_order(**overrides):
    """14.00 of croissants + 0.70 tax + 3.50 delivery = 18.20, tax on top."""
    values = dict(
        id=7,
        order_number="DJ-TEST-7",
        currency="eur",
        total=18.20,
        subtotal=14.00,
        discount_amount=0,
        shipping_fee=3.50,
        tax=0.70,
        tax_lines=[],
        items=[SimpleNamespace(product_name="Butter Croissant", unit_price=3.50, quantity=4)],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


CREATED = {
    "id": "5O190127TN364715T",
    "status": "PAYER_ACTION_REQUIRED",
    "links": [
        {"rel": "self", "href": f"{SANDBOX}/v2/checkout/orders/5O190127TN364715T"},
        {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T"},
    ],
}


def cents(value: str) -> int:
    return int(round(float(value) * 100))


# ── Credentials and environments ─────────────────────────────────────────

def test_the_token_is_asked_for_with_the_shops_credentials_and_reused(paypal):
    paypal.wire.answer(CREATED, {"id": "X", "status": "CREATED"})

    paypal.create_order(make_order(), BASE_URL, "req-1")
    paypal.get_order("5O190127TN364715T")

    token_call = next(r for r in paypal.wire.requests if r.is_token)
    assert token_call.url == f"{SANDBOX}/v1/oauth2/token"
    assert token_call.body == b"grant_type=client_credentials"
    expected = base64.b64encode(b"client-abc:secret-xyz").decode()
    assert token_call.headers["authorization"] == f"Basic {expected}"
    assert paypal.wire.token_requests == 1  # one token served both calls
    assert all(c.headers["authorization"] == "Bearer token-1" for c in paypal.wire.calls)
    assert all(c.timeout == paypal.REQUEST_TIMEOUT_SECONDS for c in paypal.wire.requests)


def test_sandbox_and_live_are_different_paypals(paypal, monkeypatch):
    paypal.wire.answer({"id": "A", "status": "CREATED"}, {"id": "B", "status": "CREATED"})
    paypal.get_order("A")
    assert paypal.wire.calls[-1].url == f"{SANDBOX}/v2/checkout/orders/A"

    # going live changes the host — and never reuses the sandbox token
    monkeypatch.setattr(paypal.get_settings(), "paypal_environment", "live")
    assert paypal.environment() == "live"
    paypal.get_order("B")
    assert paypal.wire.calls[-1].url == f"{LIVE}/v2/checkout/orders/B"
    assert paypal.wire.token_requests == 2
    assert paypal.wire.requests[-2].url == f"{LIVE}/v1/oauth2/token"


def test_credentials_paypal_refuses_are_unavailable_not_a_crash(paypal, monkeypatch):
    def refuse_token(request, timeout=None):
        raise refusal(401, name="invalid_client")

    monkeypatch.setattr(paypal.urllib.request, "urlopen", refuse_token)
    with pytest.raises(paypal.GatewayUnavailable):
        paypal.get_order("A")


def test_configured_needs_both_halves_of_the_credentials(paypal, monkeypatch):
    assert paypal.configured() is True and paypal.webhook_ready() is True
    monkeypatch.setattr(paypal.get_settings(), "paypal_client_secret", "")
    assert paypal.configured() is False


def test_an_environment_that_is_neither_paypal_switches_it_off_without_guessing(paypal, monkeypatch):
    for word in ("production", "Live!", "test"):
        monkeypatch.setattr(paypal.get_settings(), "paypal_environment", word)
        assert paypal.configured() is False, word
        assert "must be 'sandbox' or 'live'" in paypal.misconfigured()
    # spelled with stray case or spaces, it is still the PayPal it names
    monkeypatch.setattr(paypal.get_settings(), "paypal_environment", " LIVE ")
    assert paypal.configured() is True and paypal.environment() == "live"
    assert paypal.misconfigured() is None


def test_an_empty_environment_is_the_rehearsal_default_and_never_stops_the_boot():
    """A dashboard field left blank arrives as "". It must mean "sandbox",
    not a crash at start-up: a PayPal setting cannot take the shop down."""
    from app.config import Settings

    assert Settings(paypal_environment="").paypal_environment == "sandbox"
    assert Settings(paypal_environment="   ").paypal_environment == "sandbox"
    assert Settings(paypal_environment="nonsense").paypal_environment == "nonsense"  # boots; PayPal off


# ── Creating an order ────────────────────────────────────────────────────

def test_the_order_paypal_receives_adds_up_and_points_home(paypal):
    paypal.wire.answer(CREATED)

    result = paypal.create_order(make_order(), BASE_URL, "bakehouse-order-7-1")

    assert result == {
        "id": "5O190127TN364715T",
        "status": "PAYER_ACTION_REQUIRED",
        "url": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
    }
    (call,) = paypal.wire.calls
    assert (call.method, call.url) == ("POST", f"{SANDBOX}/v2/checkout/orders")
    assert call.headers["paypal-request-id"] == "bakehouse-order-7-1"  # PayPal's idempotency key
    body = json.loads(call.body)
    assert body["intent"] == "CAPTURE"
    (unit,) = body["purchase_units"]
    assert unit["custom_id"] == "7"  # the shop's order id rides back on every webhook
    assert "invoice_id" not in unit  # a retried payment needs a second PayPal order
    assert unit["amount"]["currency_code"] == "EUR" and unit["amount"]["value"] == "18.20"

    # the breakdown reconciles with the total to the cent, and so do the lines
    b = unit["amount"]["breakdown"]
    assert cents(b["item_total"]["value"]) + cents(b["shipping"]["value"]) + cents(b["tax_total"]["value"]) == 1820
    assert "discount" not in b
    assert sum(cents(i["unit_amount"]["value"]) * int(i["quantity"]) for i in unit["items"]) == 1400
    assert unit["items"][0]["name"] == "Butter Croissant"

    context = body["payment_source"]["paypal"]["experience_context"]
    assert context["user_action"] == "PAY_NOW"
    assert context["landing_page"] == "LOGIN"  # PayPal's account page, not a second card form
    assert context["shipping_preference"] == "NO_SHIPPING"
    assert context["return_url"] == f"{BASE_URL}/account/orders/7?placed=1&paypal=return"
    assert context["cancel_url"] == f"{BASE_URL}/account/orders/7?paypal=cancelled"


def test_a_coupon_rides_as_paypals_discount_row(paypal):
    paypal.wire.answer(CREATED)
    # 14.00 - 1.40 coupon = 12.60 taxable; + 0.63 tax + 3.50 delivery = 16.73
    paypal.create_order(
        make_order(discount_amount=1.40, tax=0.63, total=16.73), BASE_URL, "req"
    )
    b = json.loads(paypal.wire.calls[0].body)["purchase_units"][0]["amount"]["breakdown"]
    assert b["discount"]["value"] == "1.40"
    total = cents(b["item_total"]["value"]) + cents(b["shipping"]["value"]) + cents(b["tax_total"]["value"])
    assert total - cents(b["discount"]["value"]) == 1673


def test_vat_inclusive_orders_send_no_second_tax_row(paypal):
    """Inclusive prices carry their VAT inside every line (the order's
    per-rate ledger is that mode's mark) — a tax row would charge it twice."""
    paypal.wire.answer(CREATED)
    inclusive = make_order(tax=0.92, total=17.50, tax_lines=[object()])  # 14.00 + 3.50, VAT inside
    paypal.create_order(inclusive, BASE_URL, "req")
    amount = json.loads(paypal.wire.calls[0].body)["purchase_units"][0]["amount"]
    assert amount["value"] == "17.50"
    assert "tax_total" not in amount["breakdown"]


def test_rows_that_do_not_add_up_are_left_out_rather_than_fail_the_payment(paypal):
    paypal.wire.answer(CREATED)
    odd = make_order(total=19.99)  # stored figures that don't reconcile
    paypal.create_order(odd, BASE_URL, "req")
    unit = json.loads(paypal.wire.calls[0].body)["purchase_units"][0]
    assert unit["amount"] == {"currency_code": "EUR", "value": "19.99"}  # the total alone charges
    assert "items" not in unit


def test_an_unchargeable_currency_is_refused_before_paypal_is_asked(paypal):
    for currency in ("usd", "", None, "gbp"):
        with pytest.raises(paypal.InvalidOrderCurrency):
            paypal.create_order(make_order(currency=currency), BASE_URL, "req")
    assert paypal.wire.requests == []  # not even a token


def test_an_answer_without_an_approval_link_is_no_pay_page(paypal):
    paypal.wire.answer({"id": "X", "status": "CREATED", "links": []})
    with pytest.raises(paypal.GatewayUnavailable):
        paypal.create_order(make_order(), BASE_URL, "req")


# ── Capturing ────────────────────────────────────────────────────────────

CAPTURED = {
    "id": "5O190127TN364715T",
    "status": "COMPLETED",
    "purchase_units": [
        {
            "custom_id": "7",
            "amount": {"value": "18.20", "currency_code": "EUR"},
            "payments": {
                "captures": [
                    {"id": "3C679366HH908993F", "status": "COMPLETED",
                     "amount": {"value": "18.20", "currency_code": "EUR"}}
                ]
            },
        }
    ],
}


def test_a_capture_is_idempotent_at_paypal_and_comes_back_as_plain_values(paypal):
    paypal.wire.answer(CAPTURED)

    captured = paypal.capture_order("5O190127TN364715T")

    (call,) = paypal.wire.calls
    assert (call.method, call.url) == ("POST", f"{SANDBOX}/v2/checkout/orders/5O190127TN364715T/capture")
    assert call.headers["paypal-request-id"] == "capture-5O190127TN364715T"
    assert captured["status"] == "COMPLETED"
    assert captured["capture_id"] == "3C679366HH908993F" and captured["capture_status"] == "COMPLETED"
    assert str(captured["capture_amount"]) == "18.20" and captured["capture_currency"] == "eur"
    assert captured["custom_id"] == "7"


def test_already_captured_is_read_back_not_raised(paypal):
    paypal.wire.answer(refusal(422, "ORDER_ALREADY_CAPTURED"), CAPTURED)

    captured = paypal.capture_order("5O190127TN364715T")

    assert captured["capture_id"] == "3C679366HH908993F"
    assert [c.method for c in paypal.wire.calls] == ["POST", "GET"]


def test_paypals_refusals_carry_its_own_reason(paypal):
    paypal.wire.answer(refusal(422, "ORDER_NOT_APPROVED"))
    with pytest.raises(paypal.PayPalRefused) as caught:
        paypal.capture_order("5O190127TN364715T")
    assert caught.value.issue == "ORDER_NOT_APPROVED" and caught.value.status == 422
    assert isinstance(caught.value, paypal.GatewayUnavailable)  # one except clause catches both


def test_silence_and_server_errors_are_unavailable_never_a_refusal(paypal):
    for failure in (refusal(503, name="SERVICE_UNAVAILABLE"), TimeoutError("timed out"), OSError("dns")):
        paypal.wire.answer(failure)
        with pytest.raises(paypal.GatewayUnavailable) as caught:
            paypal.get_order("A")
        assert not isinstance(caught.value, paypal.PayPalRefused)


def test_a_lapsed_token_is_refreshed_once_and_the_call_retried(paypal):
    paypal.wire.answer(refusal(401, name="invalid_token"), {"id": "A", "status": "CREATED"})

    assert paypal.get_order("A")["status"] == "CREATED"

    assert paypal.wire.token_requests == 2
    assert [c.headers["authorization"] for c in paypal.wire.calls] == ["Bearer token-1", "Bearer token-2"]


# ── Refunding ────────────────────────────────────────────────────────────

def test_a_refund_states_its_amount_and_carries_its_key(paypal):
    paypal.wire.answer({"id": "1JU08902781691411", "status": "COMPLETED"})

    result = paypal.refund_capture("3C679366HH908993F", 300, "eur", "bakehouse-refund-9-0-300")

    assert result == {"id": "1JU08902781691411", "status": "COMPLETED"}
    (call,) = paypal.wire.calls
    assert call.url == f"{SANDBOX}/v2/payments/captures/3C679366HH908993F/refund"
    assert call.headers["paypal-request-id"] == "bakehouse-refund-9-0-300"
    assert json.loads(call.body) == {"amount": {"currency_code": "EUR", "value": "3.00"}}


def test_amounts_are_exact_decimal_strings(paypal):
    assert paypal.money_str(18.2) == "18.20"
    assert paypal.money_str("0.105") == "0.11"  # half-up, from the exact value
    assert paypal.money_str(0) == "0.00"


# ── Webhooks ─────────────────────────────────────────────────────────────

HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "paypal-transmission-id": "69cd13f0-d67a-11e5-baa3-778b53f4ae55",
    "paypal-transmission-sig": "lmI95Jx3Y9nhR5SJWlHVIWpg4AgFk7n9bCHSRxbrd8A=",
    "paypal-transmission-time": "2026-11-22T10:00:00Z",
}
# Deliberately not what json.dumps would write: odd spacing, an escaped
# character, keys out of order — exactly the bytes that must survive.
RAW_EVENT = b'{"resource":{"id":"5O190127TN364715T","note":"caf\\u00e9"},  "event_type":"CHECKOUT.ORDER.APPROVED","id":"WH-1"}'


def test_a_webhook_is_verified_against_the_exact_bytes_that_arrived(paypal):
    paypal.wire.answer({"verification_status": "SUCCESS"})

    event = paypal.verify_webhook(HEADERS, RAW_EVENT)

    assert event["id"] == "WH-1" and event["event_type"] == "CHECKOUT.ORDER.APPROVED"
    (call,) = paypal.wire.calls
    assert call.url == f"{SANDBOX}/v1/notifications/verify-webhook-signature"
    assert RAW_EVENT in call.body  # spliced in untouched, not re-serialised
    sent = json.loads(call.body)
    assert sent["webhook_id"] == "WH-REGISTERED"
    assert sent["transmission_sig"] == HEADERS["paypal-transmission-sig"]
    assert sent["cert_url"] == HEADERS["paypal-cert-url"]
    assert sent["webhook_event"]["resource"]["note"] == "café"


def test_only_paypals_own_yes_makes_a_webhook_genuine(paypal, monkeypatch):
    paypal.wire.answer({"verification_status": "FAILURE"})
    with pytest.raises(paypal.BadSignature):
        paypal.verify_webhook(HEADERS, RAW_EVENT)

    # PayPal unreachable: unverifiable is not verified
    paypal.wire.answer(refusal(503))
    with pytest.raises(paypal.BadSignature):
        paypal.verify_webhook(HEADERS, RAW_EVENT)

    calls_so_far = len(paypal.wire.calls)
    # refused without asking PayPal anything:
    with pytest.raises(paypal.BadSignature):  # a signature header missing
        paypal.verify_webhook({k: v for k, v in HEADERS.items() if k != "paypal-transmission-sig"}, RAW_EVENT)
    with pytest.raises(paypal.BadSignature):  # not JSON
        paypal.verify_webhook(HEADERS, b"not json")
    with pytest.raises(paypal.BadSignature):  # JSON, but not an event
        paypal.verify_webhook(HEADERS, b"[1, 2]")
    monkeypatch.setattr(paypal.get_settings(), "paypal_webhook_id", "")
    with pytest.raises(paypal.BadSignature):  # no registered webhook to verify against
        paypal.verify_webhook(HEADERS, RAW_EVENT)
    assert len(paypal.wire.calls) == calls_so_far


def test_a_delivery_signed_from_anywhere_but_paypal_is_never_even_asked_about(paypal):
    for cert_url in (
        "https://evil.example/v1/notifications/certs/CERT-1",
        "https://api.paypal.com.evil.example/certs/CERT-1",  # PayPal's name as a prefix, not the domain
        "http://api.paypal.com/v1/notifications/certs/CERT-1",  # not https
        "https://notpaypal.com/certs/CERT-1",
        "not a url",
    ):
        with pytest.raises(paypal.BadSignature):
            paypal.verify_webhook(dict(HEADERS, **{"paypal-cert-url": cert_url}), RAW_EVENT)
    assert paypal.wire.requests == []  # no token, no verification call — nothing left the shop

    # PayPal's own live and sandbox hosts are asked about as before
    for cert_url in (
        "https://api.paypal.com/v1/notifications/certs/CERT-1",
        "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    ):
        paypal.wire.answer({"verification_status": "SUCCESS"})
        assert paypal.verify_webhook(dict(HEADERS, **{"paypal-cert-url": cert_url}), RAW_EVENT)["id"] == "WH-1"


def test_the_registered_webhooks_come_back_as_plain_values(paypal):
    paypal.wire.answer(
        {"webhooks": [
            {"id": "WH-REGISTERED", "url": "https://shop.test/api/payments/paypal/webhook",
             "event_types": [{"name": "CHECKOUT.ORDER.APPROVED"}, {"name": "PAYMENT.CAPTURE.COMPLETED"}]},
            {"id": "WH-OTHER", "url": "https://elsewhere.test/hook", "event_types": [{"name": "*"}]},
        ]}
    )
    hooks = paypal.list_webhooks()
    assert hooks == [
        {"id": "WH-REGISTERED", "url": "https://shop.test/api/payments/paypal/webhook",
         "event_types": ["CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED"]},
        {"id": "WH-OTHER", "url": "https://elsewhere.test/hook", "event_types": ["*"]},
    ]
    (call,) = paypal.wire.calls
    assert (call.method, call.url) == ("GET", f"{SANDBOX}/v1/notifications/webhooks")


def test_a_call_made_under_a_wrong_environment_is_unavailable_not_a_crash(paypal, monkeypatch):
    monkeypatch.setattr(paypal.get_settings(), "paypal_environment", "production")
    with pytest.raises(paypal.GatewayUnavailable):
        paypal.get_order("A")
    with pytest.raises(paypal.BadSignature):  # a webhook arriving meanwhile is refused, not a 500
        paypal.verify_webhook(HEADERS, RAW_EVENT)
    assert paypal.wire.requests == []
