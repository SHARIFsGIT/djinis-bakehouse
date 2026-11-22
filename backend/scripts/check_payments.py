"""Payment readiness check — READ-ONLY BY DEFAULT, and never with real money.

Answers one question before the shop takes (or keeps taking) payments
online: is everything a real payment depends on actually in place? It reads
the same configuration the app boots with and asks Stripe and PayPal about
themselves — the account, the webhook, the methods on offer — so a wrong
key, a webhook pointing at an old address or a missing event shows up here
instead of as an order that never turns "paid".

The contract this script holds itself to:

  * The default run only READS. It creates, charges, refunds and changes
    nothing — at the providers or in the database.
  * It never prints a secret: no key, no signing secret, no token. Modes,
    yes/no answers and public addresses only.
  * The rehearsal modes (--rehearse, --rehearse-pay, --finish-stripe,
    --finish-paypal) do create things at the providers — and so they run
    ONLY against Stripe test keys and the PayPal sandbox. With live
    credentials each of them refuses outright; there is no override. No
    real payment is ever made, captured or refunded by this file.
  * It is never imported by the app; it runs only when a person runs it.

Usage (from backend/, with the same environment the app runs in):

    python scripts/check_payments.py                 # read-only readiness check
    python scripts/check_payments.py --rehearse      # + create and close a test pay page at each provider
    python scripts/check_payments.py --rehearse-pay  # + leave the test pay pages open, to pay by hand
    python scripts/check_payments.py --finish-stripe cs_test_...   # after paying it with a test card
    python scripts/check_payments.py --finish-paypal 5O1901...     # after approving it with a sandbox buyer

Exit status: 0 when nothing blocks online payments, 1 when something does.
The full go-live procedure is docs/operations/PAYMENTS_GO_LIVE.md.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

# The script lives in backend/scripts/; make `app` importable either way
# it is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, inspect, select  # noqa: E402

from app import paypal_gateway, stripe_gateway  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import engine  # noqa: E402

STRIPE_WEBHOOK_PATH = "/api/payments/webhook"
PAYPAL_WEBHOOK_PATH = "/api/payments/paypal/webhook"
LEDGER_TABLES = ("payment_transactions", "payment_refunds", "payment_events", "checkout_attempts")


class Report:
    """Collects the verdicts and prints them as they are reached."""

    def __init__(self) -> None:
        self.problems = 0
        self.warnings = 0

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def ok(self, text: str) -> None:
        print(f"  [ ok ] {text}")

    def info(self, text: str) -> None:
        print(f"  [info] {text}")

    def warn(self, text: str) -> None:
        self.warnings += 1
        print(f"  [warn] {text}")

    def fail(self, text: str) -> None:
        self.problems += 1
        print(f"  [FAIL] {text}")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# ── Configuration (no network) ─────────────────────────────────────────────

def check_configuration(report: Report, settings) -> None:
    report.section("Configuration")
    production = settings.is_production
    report.info(f"environment: {settings.app_env}")

    pending = [
        name
        for name, value in (
            ("SHIPPING_FEE", settings.shipping_fee),
            ("FREE_SHIPPING_THRESHOLD", settings.free_shipping_threshold),
            ("TAX_RATE", settings.tax_rate),
        )
        if value is None
    ]
    if pending:
        report.fail(
            "checkout is PAUSED - " + ", ".join(pending) + " not approved. No order can be placed "
            "or paid until they are set."
        )
    else:
        report.ok("money rules are approved - checkout is open")

    base = settings.public_base_url.strip()
    if not base:
        note = (
            "PUBLIC_BASE_URL is not set - links in emails and the return from a payment page are "
            "built on whatever address each request arrived on"
        )
        if production:
            report.warn(note + ". Set it to the shop's one public https:// address.")
        else:
            report.info(note + " (fine in development)")
    elif production and not base.lower().startswith("https://"):
        report.fail("PUBLIC_BASE_URL must be an https:// address in production")
    else:
        report.ok(f"customers return to {base}")

    stripe_on, paypal_on = stripe_gateway.configured(), paypal_gateway.configured()
    if not (stripe_on or paypal_on):
        report.fail("no online payment provider is configured - the shop offers cash on delivery only")

    if stripe_on:
        mode = stripe_gateway.mode()
        if production and mode != "live":
            report.warn(
                "Stripe is on TEST keys in production: checkout shows its test-mode notice and "
                "real cards are declined."
            )
        elif not production and mode == "live":
            report.warn("LIVE Stripe keys in a non-production environment - real money can move here.")
        else:
            report.ok(f"Stripe key present - {mode} mode")
        secret = settings.stripe_webhook_secret
        if not secret:
            report.fail(
                "STRIPE_WEBHOOK_SECRET is not set - every Stripe webhook is refused; payments land "
                "only when the customer returns or the periodic check runs, and refunds or "
                "disputes made at Stripe go unseen."
            )
        elif not secret.startswith("whsec_"):
            report.warn("STRIPE_WEBHOOK_SECRET does not look like a signing secret (they start with whsec_)")
        else:
            report.ok("Stripe webhook signing secret is set")
        allowed = stripe_gateway.allowed_payment_methods()
        report.info(
            "payment methods on Stripe's page: "
            + ("automatic - whatever the Stripe dashboard has switched on" if allowed is None else ", ".join(allowed))
        )
    else:
        report.info("Stripe is not configured (no STRIPE_SECRET_KEY) - the card option is hidden")

    if paypal_on:
        environment = paypal_gateway.environment()
        if production and environment != "live":
            report.warn(
                "PayPal is on SANDBOX credentials in production: only sandbox test accounts can pay."
            )
        elif not production and environment == "live":
            report.warn("LIVE PayPal credentials in a non-production environment - real money can move here.")
        else:
            report.ok(f"PayPal credentials present - {environment}")
        if not paypal_gateway.webhook_ready():
            report.fail(
                "PAYPAL_WEBHOOK_ID is not set - every PayPal webhook is refused; refunds and "
                "disputes made at PayPal go unseen."
            )
        else:
            report.ok("PayPal webhook id is set")
    else:
        report.info("PayPal is not configured (no PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET) - the option is hidden")


# ── Stripe (read-only API) ─────────────────────────────────────────────────

def check_stripe(report: Report, settings) -> None:
    if not stripe_gateway.configured():
        return
    report.section("Stripe - asked directly (read-only)")
    try:
        account = stripe_gateway.account_status()
    except stripe_gateway.GatewayUnavailable as exc:
        report.fail(
            f"the account could not be read: {exc}. A wrong or revoked key lands here - and so "
            "does a restricted key without permission to read the account."
        )
        return
    if account.get("charges_enabled"):
        report.ok("the account can take payments (charges enabled)")
    else:
        report.fail(
            "the account cannot take payments yet (charges are not enabled) - finish Stripe's "
            "verification in the dashboard"
        )
    if not account.get("details_submitted"):
        report.warn("Stripe still wants business details - the account's onboarding is incomplete")
    if account.get("payouts_enabled"):
        report.ok("payouts to the bank account are enabled")
    else:
        report.warn("payouts are not enabled - money taken stays in the Stripe balance until they are")
    currency = str(account.get("default_currency") or "").lower()
    report.info(f"account country {account.get('country') or '?'}, default currency {currency.upper() or '?'}")

    try:
        endpoints = stripe_gateway.webhook_endpoints()
    except stripe_gateway.GatewayUnavailable as exc:
        report.warn(f"the webhook endpoints could not be listed ({exc}) - check them in the dashboard")
        endpoints = None
    if endpoints is not None:
        ours = [e for e in endpoints if urlsplit(e["url"] or "").path.rstrip("/") == STRIPE_WEBHOOK_PATH]
        base_host = _host(settings.public_base_url)
        if base_host:
            here = [e for e in ours if _host(e["url"]) == base_host]
            elsewhere = [e for e in ours if _host(e["url"]) != base_host]
            for endpoint in elsewhere:
                report.warn(
                    f"an endpoint delivers to {endpoint['url']} - not this shop's address; if it is "
                    "an old one, events sent there are lost"
                )
            ours = here
        if not ours:
            report.fail(
                f"no webhook endpoint delivers to {settings.public_base_url.rstrip('/') or '<the shop>'}"
                f"{STRIPE_WEBHOOK_PATH} in this mode - create one in the Stripe dashboard"
            )
        if len(ours) > 1:
            report.warn(
                f"{len(ours)} endpoints deliver to this shop - each has its own signing secret and "
                "only one can match STRIPE_WEBHOOK_SECRET; the others' events are refused"
            )
        for endpoint in ours:
            if endpoint["status"] != "enabled":
                report.fail(f"the endpoint {endpoint['url']} is {endpoint['status']}, not enabled")
            events = set(endpoint["enabled_events"])
            missing = [] if "*" in events else [e for e in stripe_gateway.WEBHOOK_EVENTS if e not in events]
            if missing:
                report.fail(f"the endpoint {endpoint['url']} is missing events: " + ", ".join(missing))
            else:
                report.ok(f"the endpoint {endpoint['url']} is enabled and subscribed to every event the shop acts on")
            if "*" not in events:
                extra = [e for e in stripe_gateway.WEBHOOK_EVENTS_OPTIONAL if e not in events]
                if extra:
                    report.info("optional events not subscribed (fine): " + ", ".join(extra))
        if ours:
            report.info(
                "a signing secret can never be read back - make sure STRIPE_WEBHOOK_SECRET is the "
                "secret of THIS endpoint in THIS mode (test and live endpoints have different ones)"
            )

    try:
        on = stripe_gateway.dashboard_payment_methods()
    except stripe_gateway.GatewayUnavailable:
        report.info("the dashboard's payment-method settings could not be read with this key")
        return
    if not on:
        report.warn("no payment method is switched on in the account's default configuration")
        return
    report.info("switched on in the Stripe dashboard: " + ", ".join(on))
    allowed = stripe_gateway.allowed_payment_methods()
    if allowed is not None:
        unavailable = [m for m in allowed if m not in on]
        if unavailable:
            report.warn(
                "STRIPE_PAYMENT_METHODS asks for methods the dashboard has off (they will not show): "
                + ", ".join(unavailable)
            )
    if paypal_gateway.configured() and "paypal" in on:
        report.info("PayPal inside Stripe's page is left out - the shop offers PayPal as its own option")


# ── PayPal (read-only API) ─────────────────────────────────────────────────

def check_paypal(report: Report, settings) -> None:
    if not paypal_gateway.configured():
        return
    environment = paypal_gateway.environment()
    report.section(f"PayPal ({environment}) - asked directly (read-only)")
    try:
        hooks = paypal_gateway.list_webhooks()
    except paypal_gateway.GatewayUnavailable as exc:
        report.fail(
            f"PayPal did not accept the credentials or could not be reached: {exc}. Credentials "
            f"are per environment - sandbox ones are refused by live, and the reverse (this "
            f"deployment says PAYPAL_ENVIRONMENT={environment})."
        )
        return
    report.ok(f"the credentials are accepted by PayPal's {environment} environment")
    if not settings.paypal_webhook_id:
        return  # already reported under Configuration
    hook = next((h for h in hooks if h["id"] == settings.paypal_webhook_id), None)
    if hook is None:
        report.fail(
            "PAYPAL_WEBHOOK_ID does not name a webhook registered for these credentials - every "
            "delivery would fail verification. Registered here: "
            + (", ".join(h["url"] or "?" for h in hooks) or "none")
        )
        return
    path = urlsplit(hook["url"] or "").path.rstrip("/")
    if path != PAYPAL_WEBHOOK_PATH:
        report.fail(f"the webhook delivers to {hook['url']} - it must end in {PAYPAL_WEBHOOK_PATH}")
    else:
        base_host = _host(settings.public_base_url)
        if base_host and _host(hook["url"]) != base_host:
            report.warn(f"the webhook delivers to {hook['url']} - not this shop's public address")
        else:
            report.ok(f"the webhook delivers to {hook['url']}")
    events = set(hook["event_types"])
    missing = [] if "*" in events else [e for e in paypal_gateway.WEBHOOK_EVENTS if e not in events]
    if missing:
        report.fail("the webhook is missing events: " + ", ".join(missing))
    else:
        report.ok("the webhook is subscribed to every event the shop acts on")


# ── The shop's own book (read-only) ────────────────────────────────────────

def check_database(report: Report) -> None:
    report.section("The payment ledger (read-only)")
    try:
        present = set(inspect(engine).get_table_names())
    except Exception as exc:  # the database is the app's to reach, not this script's to fix
        report.warn(f"the database could not be read ({type(exc).__name__}) - skipped")
        return
    missing = [table for table in LEDGER_TABLES if table not in present]
    if missing:
        report.info(
            "ledger tables not created yet (" + ", ".join(missing) + ") - the app creates them at its "
            "first boot on this release; nothing existing is altered"
        )
        return
    from app.models import PaymentEvent, PaymentRefund, PaymentTransaction

    try:
        with engine.connect() as conn:
            transactions = conn.scalar(select(func.count(PaymentTransaction.id)))
            refunds = conn.scalar(select(func.count(PaymentRefund.id)))
            events = conn.scalar(select(func.count(PaymentEvent.id)))
    except Exception as exc:
        report.warn(f"the ledger could not be counted ({type(exc).__name__})")
        return
    report.ok(f"ledger present: {transactions} pay pages, {refunds} refunds, {events} audit entries")
    report.info("what does not add up is listed at the desk: Orders → Payment check")


# ── The shop around the payments (read-only) ───────────────────────────────

def check_shop(report: Report, settings) -> None:
    """What a paying customer meets besides the pay page: a receipt in their
    inbox, a shop that names its operator, a clock that agrees with the
    bakery's. None of it blocks a payment - all of it is worth knowing
    before the first real one."""
    report.section("The shop around the payments (read-only)")

    if settings.smtp_host:
        report.ok(f"outgoing email is on (via {settings.smtp_host})")
    else:
        note = "outgoing email is OFF (no SMTP_HOST) - no receipts, order updates or password resets are sent"
        (report.warn if settings.is_production else report.info)(note)

    from app import shoptime

    zone = settings.shop_timezone.strip()
    if not zone or zone.lower() == "local":
        report.info("SHOP_TIMEZONE is the server's own clock")
    else:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(zone)
        except Exception:
            report.warn(f"SHOP_TIMEZONE {zone!r} is not a time zone this system knows - the shop runs on UTC")
        else:
            report.ok(f"the shop's clock is {zone} - it is {shoptime.shop_now():%Y-%m-%d %H:%M} there")

    try:
        from app import delivery
        from app.models import StoreSetting

        with engine.connect() as conn:
            row = conn.execute(
                select(
                    StoreSetting.legal_notice,
                    StoreSetting.delivery_postal_codes,
                    StoreSetting.contact_email,
                ).limit(1)
            ).first()
    except Exception as exc:  # columns this release adds, on a database it has not booted on yet
        report.info(
            f"the shop's settings could not be read ({type(exc).__name__}) - on a database this "
            "release has not started on yet, that is expected"
        )
        return
    notice, area, contact = row if row is not None else (None, None, None)
    if (notice or "").strip():
        report.ok("a legal notice is published (linked in the footer, at /imprint)")
    else:
        report.warn(
            "no legal notice is published - a shop selling online in Germany/the EU is expected to "
            "name its operator. Admin -> Settings -> Legal notice."
        )
    if not (contact or "").strip():
        report.warn("no contact email is set (Admin -> Settings -> Contact & hours) - the legal pages have nobody to point at")
    entries = delivery.parse_area(area)
    if entries:
        report.ok(f"delivery area: {len(entries)} postal code entries - addresses outside are refused at checkout")
    else:
        report.info("no delivery area is set - orders are accepted for any postal code (Admin -> Settings -> Delivery area)")


# ── Rehearsals — test keys and sandbox ONLY ────────────────────────────────

def _rehearsal_order(settings) -> SimpleNamespace:
    """A one-euro order that exists nowhere but in this process. Its id is 0,
    so no webhook the rehearsal causes can ever match a real order."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    item = SimpleNamespace(product_name="Readiness check", unit_price=1.00, quantity=1, product=None)
    return SimpleNamespace(
        id=0,
        order_number=f"REHEARSAL-{stamp}",
        currency=str(settings.stripe_currency).lower(),
        total=1.00,
        subtotal=1.00,
        discount_amount=0,
        shipping_fee=0,
        tax=0,
        tax_lines=[],
        coupon_code=None,
        items=[item],
        user=SimpleNamespace(email="rehearsal@example.com"),
    )


def _rehearsal_only(report: Report, provider: str) -> bool:
    """True when this provider is safely in rehearsal mode. Anything else -
    live credentials, or none - refuses, with no way round it."""
    if provider == "stripe":
        if not stripe_gateway.configured():
            report.info("Stripe is not configured - nothing to rehearse")
            return False
        if stripe_gateway.mode() != "test":
            report.fail("REFUSED: Stripe is on LIVE keys. Rehearsals run on test keys only - this script never moves real money.")
            return False
        return True
    if not paypal_gateway.configured():
        report.info("PayPal is not configured - nothing to rehearse")
        return False
    if paypal_gateway.environment() != "sandbox":
        report.fail("REFUSED: PayPal is on LIVE credentials. Rehearsals run in the sandbox only - this script never moves real money.")
        return False
    return True


def rehearse(report: Report, settings, *, leave_open: bool) -> None:
    base = settings.public_base_url.strip().rstrip("/") or "https://example.com"
    order = _rehearsal_order(settings)

    report.section("Rehearsal - Stripe test mode")
    if _rehearsal_only(report, "stripe"):
        try:
            created = stripe_gateway.create_checkout_session(order, base)
            state = stripe_gateway.retrieve_session(created["id"])
        except (stripe_gateway.GatewayUnavailable, stripe_gateway.InvalidOrderCurrency) as exc:
            report.fail(f"Stripe refused the pay page the shop builds: {exc} - see the app log for Stripe's own words")
        else:
            report.ok(f"Stripe accepted the pay page ({created['id']})")
            if state.get("amount_total") == 100 and str(state.get("currency")).lower() == order.currency:
                report.ok("it charges exactly what the order says (1.00, in the shop's currency)")
            else:
                report.fail(f"the page charges {state.get('amount_total')} {state.get('currency')} for a 1.00 order")
            if leave_open:
                report.info(f"left open for 30 minutes - pay it with a Stripe TEST card: {created['url']}")
                report.info(f"then run: python scripts/check_payments.py --finish-stripe {created['id']}")
            elif stripe_gateway.expire_session(created["id"]):
                report.ok("and the page was closed again - it can no longer be paid")
            else:
                report.warn("the page could not be closed again (it lapses by itself in 30 minutes)")

    report.section("Rehearsal - PayPal sandbox")
    if _rehearsal_only(report, "paypal"):
        try:
            created = paypal_gateway.create_order(order, base, request_id=f"rehearsal-{order.order_number}")
            state = paypal_gateway.get_order(created["id"])
        except (paypal_gateway.GatewayUnavailable, paypal_gateway.InvalidOrderCurrency) as exc:
            report.fail(f"PayPal refused the order the shop builds: {exc}")
        else:
            report.ok(f"PayPal accepted the order ({created['id']}, {state.get('status')})")
            if str(state.get("amount")) == "1.00" and state.get("currency") == order.currency:
                report.ok("it asks for exactly what the order says (1.00, in the shop's currency)")
            else:
                report.fail(f"the order asks for {state.get('amount')} {state.get('currency')} for a 1.00 order")
            if leave_open:
                report.info(f"approve it with a sandbox BUYER account: {created['url']}")
                report.info(f"then run: python scripts/check_payments.py --finish-paypal {created['id']}")
            else:
                report.info("left unapproved - PayPal takes nothing for an order nobody approves, and it lapses")


def finish_stripe(report: Report, session_id: str) -> None:
    report.section("Rehearsal - Stripe test mode: the paid page")
    if not _rehearsal_only(report, "stripe"):
        return
    if not session_id.startswith("cs_test_"):
        report.fail("REFUSED: that is not a test-mode checkout session id (they start with cs_test_)")
        return
    try:
        state = stripe_gateway.retrieve_session(session_id)
    except stripe_gateway.GatewayUnavailable as exc:
        report.fail(f"the session could not be read: {exc}")
        return
    if state.get("payment_status") != "paid" or not state.get("payment_intent"):
        report.fail(f"the session is not paid yet (status {state.get('status')}, payment {state.get('payment_status')})")
        return
    report.ok("Stripe confirms the test payment")
    report.info(f"paid with: {stripe_gateway.payment_method(state['payment_intent']) or 'Stripe did not say'}")
    try:
        refund = stripe_gateway.refund(state["payment_intent"], int(state.get("amount_total") or 0))
    except stripe_gateway.GatewayUnavailable as exc:
        report.fail(f"the refund was refused: {exc}")
        return
    report.ok(f"refunded in full - Stripe says: {refund.get('status')}")


def finish_paypal(report: Report, paypal_order_id: str) -> None:
    report.section("Rehearsal - PayPal sandbox: the approved order")
    if not _rehearsal_only(report, "paypal"):
        return
    try:
        state = paypal_gateway.get_order(paypal_order_id)
        if state.get("status") not in ("APPROVED", "COMPLETED"):
            report.fail(f"the order is not approved yet (PayPal says {state.get('status')})")
            return
        captured = paypal_gateway.capture_order(paypal_order_id)
    except paypal_gateway.GatewayUnavailable as exc:
        report.fail(f"PayPal refused: {exc}")
        return
    if captured.get("capture_status") != "COMPLETED" or not captured.get("capture_id"):
        report.warn(f"the capture is {captured.get('capture_status')} - not completed; nothing was refunded")
        return
    report.ok(f"captured {captured.get('capture_amount')} {str(captured.get('capture_currency')).upper()}")
    try:
        refund = paypal_gateway.refund_capture(
            captured["capture_id"],
            int(captured["capture_amount"] * 100),
            captured["capture_currency"],
            request_id=f"rehearsal-refund-{paypal_order_id}",
        )
    except paypal_gateway.GatewayUnavailable as exc:
        report.fail(f"the refund was refused: {exc}")
        return
    report.ok(f"refunded in full - PayPal says: {refund.get('status')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that the shop is ready to take payments online. Read-only by default; "
        "the rehearsal options run on Stripe test keys and the PayPal sandbox only.",
    )
    parser.add_argument("--rehearse", action="store_true", help="also create and close a test pay page at each provider")
    parser.add_argument("--rehearse-pay", action="store_true", help="like --rehearse, but leave the test pay pages open to pay by hand")
    parser.add_argument("--finish-stripe", metavar="SESSION_ID", help="confirm and refund a paid test session from --rehearse-pay")
    parser.add_argument("--finish-paypal", metavar="ORDER_ID", help="capture and refund an approved sandbox order from --rehearse-pay")
    args = parser.parse_args()

    # The gateways log their own detail; this script's verdicts are the output.
    logging.getLogger("bakery").setLevel(logging.WARNING)
    # Run in whatever console is to hand: a character it cannot show is
    # written escaped, never allowed to stop the check halfway.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    settings = get_settings()
    report = Report()
    print(f"{settings.bakery_name} - payment readiness check")
    print("Read-only unless a rehearsal option is given; never with real money.")

    if args.finish_stripe or args.finish_paypal:
        if args.finish_stripe:
            finish_stripe(report, args.finish_stripe.strip())
        if args.finish_paypal:
            finish_paypal(report, args.finish_paypal.strip())
    else:
        check_configuration(report, settings)
        check_stripe(report, settings)
        check_paypal(report, settings)
        check_database(report)
        check_shop(report, settings)
        if args.rehearse or args.rehearse_pay:
            rehearse(report, settings, leave_open=args.rehearse_pay)

    print()
    if report.problems:
        print(f"{report.problems} problem(s) block reliable online payments; {report.warnings} warning(s).")
        return 1
    print(f"Nothing blocks online payments. {report.warnings} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
