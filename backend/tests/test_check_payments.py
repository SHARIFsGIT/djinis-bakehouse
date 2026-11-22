"""Tests for scripts/check_payments.py — the readiness check an owner runs
against real provider accounts.

Two promises matter more than anything it prints, so they are what this
file pins: a rehearsal can never reach a provider that is on live
credentials, and no secret ever appears in the output. Every provider call
is replaced by a recording stub — nothing here talks to Stripe or PayPal.
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_payments.py"

SECRETS = {
    "stripe_secret_key": "sk_test_51SECRETSECRETSECRET",
    "stripe_webhook_secret": "whsec_SIGNINGSECRETVALUE",
    "paypal_client_id": "PAYPAL-CLIENT-ID-VALUE",
    "paypal_client_secret": "PAYPAL-CLIENT-SECRET-VALUE",
    "paypal_webhook_id": "WH-2JR32681AB7495925",
}


@pytest.fixture
def check(monkeypatch):
    """The script, loaded fresh, with both providers configured for
    rehearsal and every gateway call recorded instead of made."""
    spec = importlib.util.spec_from_file_location("check_payments_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = module.get_settings()
    for name, value in SECRETS.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "paypal_environment", "sandbox")
    monkeypatch.setattr(settings, "public_base_url", "https://shop.test")

    calls: list[tuple] = []
    stripe, paypal = module.stripe_gateway, module.paypal_gateway

    def record(name, result):
        def call(*args, **kwargs):
            calls.append((name, *args))
            return result(*args, **kwargs) if callable(result) else result

        return call

    monkeypatch.setattr(stripe, "create_checkout_session", record(
        "stripe.create", {"id": "cs_test_rehearsal", "url": "https://checkout.stripe.test/cs_test_rehearsal"}))
    monkeypatch.setattr(stripe, "retrieve_session", record(
        "stripe.retrieve", {"status": "open", "payment_status": "unpaid", "payment_intent": None,
                            "amount_total": 100, "currency": "eur"}))
    monkeypatch.setattr(stripe, "expire_session", record("stripe.expire", True))
    monkeypatch.setattr(stripe, "refund", record("stripe.refund", {"id": "re_1", "status": "succeeded"}))
    monkeypatch.setattr(stripe, "payment_method", record("stripe.method", "card"))
    monkeypatch.setattr(stripe, "account_status", record(
        "stripe.account", {"charges_enabled": True, "payouts_enabled": True, "details_submitted": True,
                           "country": "DE", "default_currency": "eur"}))
    monkeypatch.setattr(stripe, "webhook_endpoints", record(
        "stripe.endpoints", [{"id": "we_1", "url": "https://shop.test/api/payments/webhook",
                              "status": "enabled", "enabled_events": list(stripe.WEBHOOK_EVENTS)}]))
    monkeypatch.setattr(stripe, "dashboard_payment_methods", record("stripe.methods", ["card", "klarna"]))
    monkeypatch.setattr(paypal, "create_order", record(
        "paypal.create", {"id": "PPREHEARSAL", "status": "PAYER_ACTION_REQUIRED", "url": "https://paypal.test/approve"}))
    monkeypatch.setattr(paypal, "get_order", record(
        "paypal.get", {"id": "PPREHEARSAL", "status": "APPROVED", "amount": Decimal("1.00"), "currency": "eur"}))
    monkeypatch.setattr(paypal, "capture_order", record(
        "paypal.capture", {"capture_id": "CAP1", "capture_status": "COMPLETED",
                           "capture_amount": Decimal("1.00"), "capture_currency": "eur"}))
    monkeypatch.setattr(paypal, "refund_capture", record("paypal.refund", {"id": "REF1", "status": "COMPLETED"}))
    monkeypatch.setattr(paypal, "list_webhooks", record(
        "paypal.webhooks", [{"id": SECRETS["paypal_webhook_id"],
                             "url": "https://shop.test/api/payments/paypal/webhook",
                             "event_types": list(paypal.WEBHOOK_EVENTS)}]))
    module.calls = calls
    module.settings = settings
    return module


def _names(module) -> list[str]:
    return [call[0] for call in module.calls]


# ── The promise: never with real money ───────────────────────────────────

def test_no_rehearsal_reaches_a_provider_that_is_on_live_credentials(check, monkeypatch):
    monkeypatch.setattr(check.settings, "stripe_secret_key", "sk_live_51LIVELIVELIVE")
    monkeypatch.setattr(check.settings, "paypal_environment", "live")

    for leave_open in (False, True):
        report = check.Report()
        check.rehearse(report, check.settings, leave_open=leave_open)
        assert report.problems == 2  # one refusal per provider, said out loud
    report = check.Report()
    check.finish_stripe(report, "cs_test_abc")
    check.finish_paypal(report, "PPORDER")
    assert report.problems == 2

    assert check.calls == []  # nothing was created, read, captured or refunded


def test_a_restricted_live_key_is_live_too(check, monkeypatch):
    monkeypatch.setattr(check.settings, "stripe_secret_key", "rk_live_51RESTRICTED")
    report = check.Report()
    check.finish_stripe(report, "cs_test_abc")
    assert report.problems == 1 and check.calls == []


def test_a_live_session_id_is_refused_even_on_test_keys(check):
    report = check.Report()
    check.finish_stripe(report, "cs_live_a1b2c3")
    assert report.problems == 1 and check.calls == []


def test_the_default_run_only_reads(check, capsys):
    report = check.Report()
    check.check_configuration(report, check.settings)
    check.check_stripe(report, check.settings)
    check.check_paypal(report, check.settings)

    assert report.problems == 0, capsys.readouterr().out
    assert set(_names(check)) == {"stripe.account", "stripe.endpoints", "stripe.methods", "paypal.webhooks"}


# ── What a rehearsal does, when it may ───────────────────────────────────

def test_a_rehearsal_opens_a_one_euro_page_that_matches_no_real_order_and_closes_it(check):
    report = check.Report()
    check.rehearse(report, check.settings, leave_open=False)

    assert report.problems == 0
    assert _names(check) == ["stripe.create", "stripe.retrieve", "stripe.expire", "paypal.create", "paypal.get"]
    order = check.calls[0][1]
    # id 0: whatever webhook the rehearsal causes, no order in the book answers to it
    assert order.id == 0 and order.order_number.startswith("REHEARSAL-")
    assert (order.total, order.currency) == (1.00, "eur")
    assert check.calls[0][2] == "https://shop.test"  # the shop's own address, as configured


def test_a_paid_rehearsal_is_refunded_in_full_at_each_provider(check, monkeypatch):
    monkeypatch.setattr(check.stripe_gateway, "retrieve_session", lambda sid: {
        "status": "complete", "payment_status": "paid", "payment_intent": "pi_test_1",
        "amount_total": 100, "currency": "eur"})
    report = check.Report()
    check.finish_stripe(report, "cs_test_rehearsal")
    check.finish_paypal(report, "PPREHEARSAL")

    assert report.problems == 0
    assert ("stripe.refund", "pi_test_1", 100) in check.calls
    refund = next(c for c in check.calls if c[0] == "paypal.refund")
    assert refund[1:4] == ("CAP1", 100, "eur")


def test_an_unpaid_rehearsal_is_not_refunded(check):
    report = check.Report()
    check.finish_stripe(report, "cs_test_rehearsal")  # the stub session is still unpaid
    assert report.problems == 1
    assert "stripe.refund" not in _names(check)


# ── What it notices ──────────────────────────────────────────────────────

def test_it_names_what_would_stop_a_payment_from_being_confirmed(check, monkeypatch, capsys):
    monkeypatch.setattr(check.settings, "stripe_webhook_secret", "")
    monkeypatch.setattr(check.stripe_gateway, "webhook_endpoints", lambda: [
        {"id": "we_old", "url": "https://old-address.example/api/payments/webhook", "status": "enabled",
         "enabled_events": ["*"]},
        {"id": "we_1", "url": "https://shop.test/api/payments/webhook", "status": "enabled",
         "enabled_events": ["checkout.session.completed"]},
    ])
    monkeypatch.setattr(check.paypal_gateway, "list_webhooks", lambda: [
        {"id": "WH-SOMEONE-ELSES", "url": "https://shop.test/api/payments/paypal/webhook", "event_types": ["*"]}
    ])
    report = check.Report()
    check.check_configuration(report, check.settings)
    check.check_stripe(report, check.settings)
    check.check_paypal(report, check.settings)
    out = capsys.readouterr().out

    assert "STRIPE_WEBHOOK_SECRET is not set" in out
    assert "missing events" in out and "charge.dispute.created" in out
    assert "old-address.example" in out  # an endpoint left pointing at an old address
    assert "PAYPAL_WEBHOOK_ID does not name a webhook" in out
    assert report.problems == 3


def test_a_shop_rehearsing_in_production_is_told_so(check, monkeypatch, capsys):
    monkeypatch.setattr(check.settings, "app_env", "production")
    report = check.Report()
    check.check_configuration(report, check.settings)
    out = capsys.readouterr().out
    assert "TEST keys in production" in out and "SANDBOX credentials in production" in out
    assert report.warnings >= 2 and report.problems == 0


def test_no_secret_is_ever_printed(check, capsys):
    report = check.Report()
    check.check_configuration(report, check.settings)
    check.check_stripe(report, check.settings)
    check.check_paypal(report, check.settings)
    check.rehearse(report, check.settings, leave_open=True)
    out = capsys.readouterr().out
    assert out  # it did say something
    for name in ("stripe_secret_key", "stripe_webhook_secret", "paypal_client_id", "paypal_client_secret"):
        assert SECRETS[name] not in out, name
