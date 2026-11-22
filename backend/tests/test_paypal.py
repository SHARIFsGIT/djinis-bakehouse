"""PayPal Checkout, end to end through the shop's own API.

The whole gateway is swapped for a fake that behaves like PayPal's order
lifecycle (created → approved by the customer → captured by the shop), so
no test talks to PayPal; the real module's requests are pinned separately
in test_paypal_gateway.py.

The property everything here leans on: PayPal never takes money on its
own. The customer only *approves* on PayPal's page — the shop asks for the
capture. So the questions worth pinning are who may trigger that capture,
for which order, and how often.
"""

import datetime
import json
import logging
from decimal import Decimal

import pytest
from sqlalchemy import select

import test_api as api
from test_api import admin_headers, auth_header, checkout_payload, client

from app import paypal_gateway
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Order, PaymentEvent
from app.routers.payments import reconcile_stale_card_orders

# --------------------------------------------------------------------------
# The PayPal fake
# --------------------------------------------------------------------------

paypal_orders: dict[str, dict] = {}
paypal_calls: list[tuple] = []
GENUINE = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.paypal.test/cert",
    "paypal-transmission-id": "tx-1",
    "paypal-transmission-sig": "genuine",
    "paypal-transmission-time": "2026-11-22T10:00:00Z",
}


def fake_create_order(order, base_url, request_id):
    paypal_id = f"PP{order.id}N{len(paypal_calls)}"
    paypal_calls.append(("create_order", order.id, request_id))
    paypal_orders[paypal_id] = {
        "id": paypal_id,
        "status": "PAYER_ACTION_REQUIRED",
        "custom_id": str(order.id),
        "amount": Decimal(str(order.total)),
        "currency": order.currency,
        "capture_id": None,
        "capture_status": None,
        "capture_amount": None,
        "capture_currency": None,
        "url": f"https://www.sandbox.paypal.test/checkoutnow?token={paypal_id}",
        "return_base": base_url,
    }
    return {"id": paypal_id, "status": "PAYER_ACTION_REQUIRED", "url": paypal_orders[paypal_id]["url"]}


def _public(order: dict) -> dict:
    return {k: v for k, v in order.items() if k not in ("return_base", "on_capture")}


def fake_get_order(paypal_id):
    paypal_calls.append(("get_order", paypal_id))
    if paypal_id not in paypal_orders:
        raise paypal_gateway.GatewayUnavailable("unknown order")
    return _public(paypal_orders[paypal_id])


def fake_capture_order(paypal_id):
    paypal_calls.append(("capture_order", paypal_id))
    order = paypal_orders[paypal_id]
    if order["status"] == "COMPLETED":
        return _public(order)  # PayPal's idempotent answer to a second capture
    if order["status"] != "APPROVED":
        raise paypal_gateway.PayPalRefused(422, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED", "not yet")
    outcome = order.get("on_capture", "COMPLETED")
    if outcome == "INSTRUMENT_DECLINED":
        raise paypal_gateway.PayPalRefused(422, "UNPROCESSABLE_ENTITY", "INSTRUMENT_DECLINED", "declined")
    order.update(
        status="COMPLETED",
        capture_id=f"CAP-{paypal_id}",
        capture_status=outcome,
        capture_amount=order.get("captures_as", order["amount"]),
        capture_currency=order["currency"],
    )
    return _public(order)


def fake_refund_capture(capture_id, amount_cents, currency, request_id):
    paypal_calls.append(("refund", capture_id, amount_cents, currency, request_id))
    return {"id": f"REF-{len(paypal_calls)}", "status": "COMPLETED"}


def fake_verify_webhook(headers, raw_body):
    if not get_settings().paypal_webhook_id:
        raise paypal_gateway.BadSignature("PAYPAL_WEBHOOK_ID is not set")
    if headers.get("paypal-transmission-sig") != "genuine":
        raise paypal_gateway.BadSignature("PayPal says the webhook is not genuine")
    return json.loads(raw_body)


paypal_gateway.create_order = fake_create_order
paypal_gateway.get_order = fake_get_order
paypal_gateway.capture_order = fake_capture_order
paypal_gateway.refund_capture = fake_refund_capture
paypal_gateway.verify_webhook = fake_verify_webhook


@pytest.fixture(autouse=True)
def paypal_configured(monkeypatch):
    """PayPal exists for the tests in this file only — everywhere else in
    the suite it is as unconfigured as on a shop that never set it up."""
    settings = get_settings()
    monkeypatch.setattr(settings, "paypal_client_id", "sandbox-client")
    monkeypatch.setattr(settings, "paypal_client_secret", "sandbox-secret")
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-SANDBOX-1")
    monkeypatch.setattr(settings, "paypal_environment", "sandbox")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _customer(name: str) -> dict:
    return auth_header(api.register_and_login(f"{name}@paypal.example.com", "PayPal123!"))


def _place(headers: dict, **overrides) -> dict:
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    response = client.post(
        "/api/orders", json=checkout_payload(payment_method="paypal", **overrides), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _paypal_id(order_id: int) -> str:
    """The newest PayPal order minted for a shop order."""
    ids = [pid for pid, o in paypal_orders.items() if o["custom_id"] == str(order_id)]
    assert ids, f"no PayPal order was created for order {order_id}"
    return ids[-1]


def _approve(order_id: int) -> str:
    """The customer says yes on PayPal's page."""
    paypal_id = _paypal_id(order_id)
    paypal_orders[paypal_id]["status"] = "APPROVED"
    return paypal_id


def _confirm(order_id: int, headers: dict):
    return client.post(f"/api/payments/confirm/{order_id}", headers=headers)


def _webhook(event_type: str, resource: dict, event_id: str | None = None, headers: dict | None = None):
    event = {"event_type": event_type, "resource": resource}
    if event_id:
        event["id"] = event_id
    return client.post(
        "/api/payments/paypal/webhook", json=event, headers=GENUINE if headers is None else headers
    )


def _detail(order_id: int, headers: dict) -> dict:
    return client.get(f"/api/orders/{order_id}", headers=headers).json()


def _payments(order_id: int) -> dict:
    return client.get(f"/api/admin/orders/{order_id}/payments", headers=admin_headers()).json()


def _captures(paypal_id: str) -> int:
    return len([c for c in paypal_calls if c == ("capture_order", paypal_id)])


def _paid(name: str) -> tuple[dict, dict, str]:
    headers = _customer(name)
    order = _place(headers)
    paypal_id = _approve(order["id"])
    assert _confirm(order["id"], headers).json()["payment_status"] == "paid"
    return order, headers, paypal_id


def _cents(amount: float) -> int:
    return int(round(amount * 100))


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def test_the_paypal_option_exists_only_while_its_credentials_do(monkeypatch):
    store = client.get("/api/store").json()
    assert store["paypal_payments"] is True
    assert store["payments_test_mode"] is True  # sandbox PayPal, test-key Stripe: a rehearsal
    assert (store["card_test_mode"], store["paypal_test_mode"]) == (True, True)

    # the two providers go live separately — each option says only what is true of it
    test_key = get_settings().stripe_secret_key
    monkeypatch.setattr(get_settings(), "paypal_environment", "live")
    live_paypal = client.get("/api/store").json()
    assert (live_paypal["card_test_mode"], live_paypal["paypal_test_mode"]) == (True, False)
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_live_notarealkey")
    all_live = client.get("/api/store").json()
    assert (all_live["card_test_mode"], all_live["paypal_test_mode"]) == (False, False)
    assert all_live["payments_test_mode"] is False
    monkeypatch.setattr(get_settings(), "paypal_environment", "sandbox")
    mixed = client.get("/api/store").json()
    assert (mixed["card_test_mode"], mixed["paypal_test_mode"]) == (False, True)
    monkeypatch.setattr(get_settings(), "stripe_secret_key", test_key)

    monkeypatch.setattr(get_settings(), "paypal_client_secret", "")
    assert client.get("/api/store").json()["paypal_payments"] is False
    headers = _customer("no-paypal")
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    stock = api.get_product("butter-croissant")["stock"]
    refused = client.post(
        "/api/orders", json=checkout_payload(payment_method="paypal"), headers=headers
    )
    assert refused.status_code == 400
    assert "PayPal isn't set up yet" in refused.json()["detail"]
    assert api.get_product("butter-croissant")["stock"] == stock  # refused before any side effect


# --------------------------------------------------------------------------
# The happy path, and who gets to trigger the capture
# --------------------------------------------------------------------------

def test_a_paypal_order_is_placed_unpaid_and_captured_when_the_customer_returns(caplog):
    headers = _customer("happy")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        order = _place(headers)
        paypal_id = _paypal_id(order["id"])

        assert order["payment_method"] == "paypal" and order["payment_status"] == "pending"
        assert order["payment_url"] == paypal_orders[paypal_id]["url"]  # PayPal's own approval page
        assert order["payment_provider"] == "paypal"
        (tx,) = _payments(order["id"])["transactions"]
        assert (tx["provider"], tx["provider_reference"], tx["status"]) == ("paypal", paypal_id, "pending")
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert f"Receipt {order['order_number']}" not in logged  # nothing to receipt yet

        # back from PayPal *without* having approved: nothing is captured
        early = _confirm(order["id"], headers).json()
        assert early["payment_status"] == "pending" and _captures(paypal_id) == 0

        _approve(order["id"])
        paid = _confirm(order["id"], headers).json()

    assert paid["payment_status"] == "paid"
    assert paid["timeline"][-1]["comment"] == "Payment received — paid with PayPal"
    (tx,) = _payments(order["id"])["transactions"]
    assert tx["status"] == "succeeded" and tx["provider_payment_id"] == f"CAP-{paypal_id}"
    assert tx["method_label"] == "PayPal" and paid["payment_label"] == "PayPal"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {order['order_number']}" in logged
    assert "Paid by PayPal — this email is your proof of purchase" in logged

    # the page refreshed, the customer clicked again: one capture, one receipt
    again = _confirm(order["id"], headers).json()
    assert again["payment_status"] == "paid"
    assert _captures(paypal_id) == 1
    assert len([e for e in again["timeline"] if "Payment received" in e["comment"]]) == 1


def test_an_approved_order_is_captured_by_webhook_when_the_customer_never_returns():
    headers = _customer("closed-the-tab")
    order = _place(headers)
    paypal_id = _approve(order["id"])

    delivered = _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}, event_id=f"WH-APPROVED-{order['id']}")
    assert delivered.status_code == 200
    assert _detail(order["id"], headers)["payment_status"] == "paid"

    # PayPal repeating the event, and the capture's own event arriving too
    _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}, event_id=f"WH-APPROVED-{order['id']}")
    _webhook(
        "PAYMENT.CAPTURE.COMPLETED",
        {"id": f"CAP-{paypal_id}", "status": "COMPLETED",
         "amount": {"value": f"{order['total']:.2f}", "currency_code": "EUR"},
         "supplementary_data": {"related_ids": {"order_id": paypal_id}}},
        event_id=f"WH-CAPTURED-{order['id']}",
    )
    detail = _detail(order["id"], headers)
    assert _captures(paypal_id) == 1
    assert len([e for e in detail["timeline"] if "Payment received" in e["comment"]]) == 1
    db = SessionLocal()
    try:
        claims = db.scalars(
            select(PaymentEvent).where(PaymentEvent.event_id == f"WH-APPROVED-{order['id']}")
        ).all()
        assert len(claims) == 1 and claims[0].order_id == order["id"]
    finally:
        db.close()


def test_a_webhook_nobody_can_vouch_for_is_refused(monkeypatch):
    headers = _customer("forged")
    order = _place(headers)
    paypal_id = _approve(order["id"])

    forged = _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}, headers={**GENUINE, "paypal-transmission-sig": "forged"})
    assert forged.status_code == 400 and "rejected" in forged.json()["detail"].lower()
    unsigned = _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}, headers={})
    assert unsigned.status_code == 400

    # without a registered webhook id nothing can be verified, so nothing is accepted
    monkeypatch.setattr(get_settings(), "paypal_webhook_id", "")
    assert _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}).status_code == 400

    assert _detail(order["id"], headers)["payment_status"] == "pending"
    assert _captures(paypal_id) == 0


def test_a_cancelled_order_is_never_captured():
    """The guarantee PayPal's two-step flow gives, held from every door:
    the return page, the approval webhook and the pay-again button."""
    headers = _customer("cancelled-then-approved")
    order = _place(headers)
    paypal_id = _paypal_id(order["id"])
    stock_placed = api.get_product("butter-croissant")["stock"]

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["status"] == "cancelled"
    assert api.get_product("butter-croissant")["stock"] == stock_placed + 2
    assert _payments(order["id"])["transactions"][0]["status"] == "cancelled"

    _approve(order["id"])  # the PayPal tab was still open
    assert _confirm(order["id"], headers).json()["payment_status"] == "pending"
    assert _webhook("CHECKOUT.ORDER.APPROVED", {"id": paypal_id}).status_code == 200
    assert client.post(f"/api/payments/{order['id']}/session", headers=headers).status_code == 400

    assert _captures(paypal_id) == 0  # no money was ever taken
    assert _detail(order["id"], headers)["payment_status"] == "pending"


def test_paypal_unreachable_at_checkout_cancels_the_order_and_frees_the_stock(monkeypatch):
    headers = _customer("paypal-down")
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    stock = api.get_product("butter-croissant")["stock"]

    def down(order, base_url, request_id):
        raise paypal_gateway.GatewayUnavailable("PayPal did not answer")

    monkeypatch.setattr(paypal_gateway, "create_order", down)
    failed = client.post("/api/orders", json=checkout_payload(payment_method="paypal"), headers=headers)

    assert failed.status_code == 503 and "nothing was charged" in failed.json()["detail"]
    assert api.get_product("butter-croissant")["stock"] == stock
    listing = client.get("/api/orders", headers=headers).json()["orders"]
    assert [o["status"] for o in listing] == ["cancelled"]


# --------------------------------------------------------------------------
# Pay again: one PayPal page at a time
# --------------------------------------------------------------------------

def test_complete_payment_hands_back_the_page_the_customer_already_has():
    headers = _customer("same-page")
    order = _place(headers)
    paypal_id = _paypal_id(order["id"])
    created = len([c for c in paypal_calls if c[0] == "create_order"])

    resumed = client.post(f"/api/payments/{order['id']}/session", headers=headers)

    assert resumed.status_code == 200  # nothing new was minted
    assert resumed.json()["payment_url"] == paypal_orders[paypal_id]["url"]
    assert len([c for c in paypal_calls if c[0] == "create_order"]) == created

    # approved in the meantime: the same button captures instead of redirecting
    _approve(order["id"])
    captured = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert captured.status_code == 200
    assert captured.json()["payment_status"] == "paid" and captured.json()["payment_url"] is None


def test_a_lapsed_paypal_page_is_replaced_and_the_old_one_can_no_longer_charge():
    headers = _customer("replaced-page")
    order = _place(headers)
    old = _paypal_id(order["id"])
    paypal_orders[old]["status"] = "VOIDED"  # PayPal let it lapse

    fresh = client.post(f"/api/payments/{order['id']}/session", headers=headers)

    assert fresh.status_code == 201
    new = _paypal_id(order["id"])
    assert new != old and fresh.json()["payment_url"] == paypal_orders[new]["url"]
    # each link is its own PayPal order under its own idempotency key
    keys = [c[2] for c in paypal_calls if c[0] == "create_order" and c[1] == order["id"]]
    assert len(keys) == 2 and len(set(keys)) == 2
    statuses = {tx["provider_reference"]: tx["status"] for tx in _payments(order["id"])["transactions"]}
    assert statuses == {old: "expired", new: "pending"}

    # the old page, somehow approved after all, is not the shop's to capture
    paypal_orders[old]["status"] = "APPROVED"
    assert _webhook("CHECKOUT.ORDER.APPROVED", {"id": old}).status_code == 200
    assert _captures(old) == 0
    assert _detail(order["id"], headers)["payment_status"] == "pending"

    # the current one is
    _approve(order["id"])
    assert _confirm(order["id"], headers).json()["payment_status"] == "paid"
    assert _captures(new) == 1


# --------------------------------------------------------------------------
# Captures that are not a clean yes
# --------------------------------------------------------------------------

def test_a_declined_funding_source_leaves_the_order_open_to_pay():
    headers = _customer("declined")
    order = _place(headers)
    paypal_id = _approve(order["id"])
    paypal_orders[paypal_id]["on_capture"] = "INSTRUMENT_DECLINED"

    declined = _confirm(order["id"], headers)

    assert declined.status_code == 200
    assert declined.json()["payment_status"] == "pending" and declined.json()["status"] == "pending"
    (tx,) = _payments(order["id"])["transactions"]
    assert tx["status"] == "pending" and "funding source" in tx["failure_reason"]

    # they pick another funding source on the same PayPal page, and it works
    paypal_orders[paypal_id]["on_capture"] = "COMPLETED"
    assert _confirm(order["id"], headers).json()["payment_status"] == "paid"


def test_a_held_capture_is_processing_until_paypal_says_completed():
    headers = _customer("held")
    order = _place(headers)
    paypal_id = _approve(order["id"])
    paypal_orders[paypal_id]["on_capture"] = "PENDING"

    held = _confirm(order["id"], headers).json()
    assert held["payment_status"] == "pending"  # not paid on PayPal's "maybe"
    (tx,) = _payments(order["id"])["transactions"]
    assert tx["status"] == "processing" and tx["provider_payment_id"] == f"CAP-{paypal_id}"

    _webhook(
        "PAYMENT.CAPTURE.COMPLETED",
        {"id": f"CAP-{paypal_id}", "status": "COMPLETED",
         "amount": {"value": f"{order['total']:.2f}", "currency_code": "EUR"}},
    )
    assert _detail(order["id"], headers)["payment_status"] == "paid"
    assert _payments(order["id"])["transactions"][0]["status"] == "succeeded"


def test_a_capture_for_the_wrong_amount_rings_the_desk():
    headers = _customer("wrong-amount")
    order = _place(headers)
    paypal_id = _approve(order["id"])
    paypal_orders[paypal_id]["captures_as"] = Decimal("1.00")

    _confirm(order["id"], headers)

    titles = [
        n["title"]
        for n in client.get("/api/admin/notifications", params={"limit": 50}, headers=admin_headers()).json()["items"]
    ]
    assert f"Payment amount mismatch on order {order['order_number']}" in titles
    assert "check this payment" in _detail(order["id"], headers)["timeline"][-1]["comment"]


# --------------------------------------------------------------------------
# Refunds and disputes
# --------------------------------------------------------------------------

def test_a_refund_from_the_desk_goes_back_through_paypal():
    order, headers, paypal_id = _paid("refund")
    capture = f"CAP-{paypal_id}"

    partial = client.post(
        f"/api/admin/orders/{order['id']}/refunds", json={"amount": 3.0, "reason": "Late delivery"},
        headers=admin_headers(),
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["payment_status"] == "partially_refunded"
    call = next(c for c in paypal_calls if c[0] == "refund" and c[1] == capture)
    assert call[2:4] == (300, "eur") and call[4]  # cents, the capture's currency, an idempotency key
    refund = partial.json()["transactions"][0]["refunds"][0]
    assert refund["status"] == "succeeded" and refund["provider_refund_id"]
    assert "Refund of €3.00 sent back through PayPal" in _detail(order["id"], headers)["timeline"][-1]["comment"]

    # PayPal's own event for that refund confirms it — it is not a second one
    _webhook(
        "PAYMENT.CAPTURE.REFUNDED",
        {"id": refund["provider_refund_id"], "status": "COMPLETED",
         "amount": {"value": "3.00", "currency_code": "EUR"},
         "links": [{"rel": "up", "href": f"https://api.paypal.test/v2/payments/captures/{capture}"}]},
    )
    book = _payments(order["id"])
    assert book["refunded_amount"] == 3.0 and len(book["transactions"][0]["refunds"]) == 1

    # a refund made in PayPal's own dashboard is new to the book
    _webhook(
        "PAYMENT.CAPTURE.REFUNDED",
        {"id": "REF-DASHBOARD-1", "status": "COMPLETED",
         "amount": {"value": "2.00", "currency_code": "EUR"},
         "links": [{"rel": "up", "href": f"https://api.paypal.test/v2/payments/captures/{capture}"}]},
    )
    book = _payments(order["id"])
    assert book["refunded_amount"] == 5.0
    assert book["transactions"][0]["refunds"][-1]["initiated_by"] == "PayPal dashboard"
    assert "Refund recorded from PayPal" in _detail(order["id"], headers)["timeline"][-1]["comment"]


def test_cancelling_a_paid_paypal_order_refunds_it_through_paypal():
    order, headers, paypal_id = _paid("cancel-paid")

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()

    assert cancelled["status"] == "cancelled" and cancelled["payment_status"] == "refunded"
    call = next(c for c in paypal_calls if c[0] == "refund" and c[1] == f"CAP-{paypal_id}")
    assert call[2] == _cents(order["total"])
    assert _payments(order["id"])["transactions"][0]["status"] == "refunded"


def test_a_refund_paypal_refuses_keeps_the_book_honest(monkeypatch):
    order, headers, _paypal = _paid("refund-refused")

    def refused(capture_id, amount_cents, currency, request_id):
        raise paypal_gateway.PayPalRefused(422, "UNPROCESSABLE_ENTITY", "CAPTURE_FULLY_REFUNDED", "no")

    monkeypatch.setattr(paypal_gateway, "refund_capture", refused)
    attempt = client.post(
        f"/api/admin/orders/{order['id']}/refunds", json={"amount": 2.0}, headers=admin_headers()
    )
    assert attempt.status_code == 502 and "PayPal did not accept the refund" in attempt.json()["detail"]
    book = _payments(order["id"])
    assert book["payment_status"] == "paid"
    assert book["transactions"][0]["refunds"][0]["status"] == "failed"

    # and on cancellation the timeline says where to finish the job
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["payment_status"] == "paid"
    note = cancelled["timeline"][-1]["comment"]
    assert "refund could not be sent" in note and "PayPal dashboard" in note


def test_a_paypal_dispute_is_flagged_and_a_lost_one_is_booked():
    order, headers, paypal_id = _paid("dispute")
    capture = f"CAP-{paypal_id}"
    resource = {
        "dispute_id": f"PP-D-{order['id']}",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "disputed_transactions": [{"seller_transaction_id": capture}],
        "dispute_amount": {"value": f"{order['total']:.2f}", "currency_code": "EUR"},
    }
    assert _webhook("CUSTOMER.DISPUTE.CREATED", resource).status_code == 200
    tx = _payments(order["id"])["transactions"][0]
    assert (tx["status"], tx["dispute_status"]) == ("disputed", "open")
    assert "Resolution Centre" in _detail(order["id"], headers)["timeline"][-1]["comment"]

    _webhook(
        "CUSTOMER.DISPUTE.RESOLVED",
        dict(resource, dispute_outcome={"outcome_code": "RESOLVED_BUYER_FAVOUR"}),
    )
    book = _payments(order["id"])
    assert book["payment_status"] == "refunded"
    assert book["transactions"][0]["dispute_status"] == "lost"


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

def _age(order_ids: list[int], minutes: int = 120) -> None:
    db = SessionLocal()
    try:
        stale = utcnow() - datetime.timedelta(minutes=minutes)
        db.query(Order).filter(Order.id.in_(order_ids)).update({"created_at": stale}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_the_sweep_captures_an_approved_order_and_lapses_an_abandoned_one(caplog):
    headers = _customer("sweep")
    approved = _place(headers)
    abandoned = _place(headers)
    _approve(approved["id"])
    stock = api.get_product("butter-croissant")["stock"]
    _age([approved["id"], abandoned["id"]])

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        reconcile_stale_card_orders()

    healed = _detail(approved["id"], headers)
    assert healed["payment_status"] == "paid"
    lapsed = _detail(abandoned["id"], headers)
    assert lapsed["status"] == "cancelled" and "expired" in lapsed["timeline"][-1]["comment"]
    assert api.get_product("butter-croissant")["stock"] == stock + 2  # only the lapsed box went back
    assert _payments(abandoned["id"])["transactions"][0]["status"] == "expired"
    # both customers heard: a receipt for one, the cancellation for the other
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {approved['order_number']}" in logged
    assert f"order {abandoned['order_number']}" in logged

    # a second round finds nothing left to do
    captures = len([c for c in paypal_calls if c[0] == "capture_order"])
    reconcile_stale_card_orders()
    assert len([c for c in paypal_calls if c[0] == "capture_order"]) == captures


def test_paypal_being_unreachable_never_cancels_an_order(monkeypatch):
    headers = _customer("sweep-down")
    order = _place(headers)
    _age([order["id"]])

    def down(paypal_id):
        raise paypal_gateway.GatewayUnavailable("PayPal did not answer")

    monkeypatch.setattr(paypal_gateway, "get_order", down)
    reconcile_stale_card_orders()

    assert _detail(order["id"], headers)["status"] == "pending"  # left for the next round
    assert _confirm(order["id"], headers).status_code == 503


# --------------------------------------------------------------------------
# When PayPal says no, or no longer knows the order
# --------------------------------------------------------------------------

def test_a_capture_paypal_flatly_refuses_closes_the_page_and_a_fresh_one_can_be_opened(monkeypatch):
    headers = _customer("refused")
    order = _place(headers)
    refused_id = _approve(order["id"])
    real_capture = paypal_gateway.capture_order

    def refuse(paypal_id):
        paypal_calls.append(("capture_order", paypal_id))
        raise paypal_gateway.PayPalRefused(422, "UNPROCESSABLE_ENTITY", "TRANSACTION_REFUSED", "no")

    monkeypatch.setattr(paypal_gateway, "capture_order", refuse)
    back = _confirm(order["id"], headers)

    assert back.status_code == 200  # an answer, not "the provider didn't answer"
    assert (back.json()["payment_status"], back.json()["payment_state"]) == ("pending", "failed")
    (tx,) = _payments(order["id"])["transactions"]
    assert tx["status"] == "failed" and "TRANSACTION_REFUSED" in tx["failure_reason"]

    # the customer tries again: a new PayPal order, never the refused one
    monkeypatch.setattr(paypal_gateway, "capture_order", real_capture)
    fresh = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert fresh.status_code == 201
    new_id = _paypal_id(order["id"])
    assert new_id != refused_id and fresh.json()["payment_url"] == paypal_orders[new_id]["url"]
    _approve(order["id"])
    assert _confirm(order["id"], headers).json()["payment_status"] == "paid"
    assert _captures(new_id) == 1


def test_a_refusal_never_buries_a_capture_that_went_through(monkeypatch):
    """PayPal answers the capture with an error, yet holds the money (a
    retry crossing a first attempt that had landed). The order's own state
    at PayPal is what gets believed."""
    headers = _customer("refused-but-captured")
    order = _place(headers)
    paypal_id = _approve(order["id"])

    def captured_then_refused(paypal_id_):
        paypal_calls.append(("capture_order", paypal_id_))
        target = paypal_orders[paypal_id_]
        target.update(
            status="COMPLETED", capture_id=f"CAP-{paypal_id_}", capture_status="COMPLETED",
            capture_amount=target["amount"], capture_currency=target["currency"],
        )
        raise paypal_gateway.PayPalRefused(422, "UNPROCESSABLE_ENTITY", "ORDER_COMPLETED_OR_VOIDED", "no")

    monkeypatch.setattr(paypal_gateway, "capture_order", captured_then_refused)
    back = _confirm(order["id"], headers).json()

    assert back["payment_status"] == "paid"
    (tx,) = _payments(order["id"])["transactions"]
    assert tx["status"] == "succeeded" and tx["provider_payment_id"] == f"CAP-{paypal_id}"


def test_a_rate_limit_is_not_a_verdict_on_the_payment(monkeypatch):
    headers = _customer("rate-limited")
    order = _place(headers)
    _approve(order["id"])
    real_capture = paypal_gateway.capture_order

    def slow_down(paypal_id):
        raise paypal_gateway.PayPalRefused(429, "RATE_LIMIT_REACHED", "", "slow down")

    monkeypatch.setattr(paypal_gateway, "capture_order", slow_down)
    assert _confirm(order["id"], headers).status_code == 503
    assert _payments(order["id"])["transactions"][0]["status"] == "pending"  # the page stays open

    monkeypatch.setattr(paypal_gateway, "capture_order", real_capture)
    assert _confirm(order["id"], headers).json()["payment_status"] == "paid"


def test_an_order_paypal_no_longer_knows_is_a_lapsed_page_not_an_outage(monkeypatch):
    headers = _customer("forgotten")
    order = _place(headers)
    abandoned = _place(headers)
    forgotten = {_paypal_id(order["id"]), _paypal_id(abandoned["id"])}
    real_get = paypal_gateway.get_order

    def get(paypal_id):
        if paypal_id in forgotten:
            raise paypal_gateway.PayPalRefused(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID", "gone")
        return real_get(paypal_id)

    monkeypatch.setattr(paypal_gateway, "get_order", get)

    back = _confirm(order["id"], headers)
    assert back.status_code == 200 and back.json()["payment_state"] == "expired"

    # "complete payment" simply opens a new PayPal page
    fresh = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert fresh.status_code == 201 and _paypal_id(order["id"]) not in forgotten

    # and an order nobody returns to lapses on the sweep instead of hanging forever
    _age([abandoned["id"]])
    reconcile_stale_card_orders()
    assert _detail(abandoned["id"], headers)["status"] == "cancelled"
    assert _payments(abandoned["id"])["transactions"][0]["status"] == "expired"
    assert _detail(order["id"], headers)["status"] == "pending"  # the one being paid is untouched


def test_a_declined_order_nobody_returns_to_lapses_with_its_window():
    headers = _customer("declined-and-left")
    order = _place(headers)
    paypal_id = _approve(order["id"])
    paypal_orders[paypal_id]["on_capture"] = "INSTRUMENT_DECLINED"
    assert _confirm(order["id"], headers).json()["payment_status"] == "pending"
    stock = api.get_product("butter-croissant")["stock"]

    _age([order["id"]])
    reconcile_stale_card_orders()

    lapsed = _detail(order["id"], headers)
    assert lapsed["status"] == "cancelled" and lapsed["payment_status"] == "pending"
    assert api.get_product("butter-croissant")["stock"] == stock + 2
    assert _captures(paypal_id) == 2  # asked on return and by the sweep — never charged


def test_a_capture_paypal_is_holding_shows_as_processing_and_blocks_a_second_page():
    headers = _customer("held-page")
    order = _place(headers)
    paypal_id = _approve(order["id"])
    paypal_orders[paypal_id]["on_capture"] = "PENDING"

    held = _confirm(order["id"], headers).json()
    assert (held["payment_status"], held["payment_state"]) == ("pending", "processing")

    created = len([c for c in paypal_calls if c[0] == "create_order"])
    again = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert again.status_code == 200 and again.json()["payment_url"] is None
    assert len([c for c in paypal_calls if c[0] == "create_order"]) == created


def test_every_event_the_shop_asks_paypal_for_is_one_it_acts_on(caplog):
    with caplog.at_level(logging.INFO, logger="bakery.payments"):
        for event_type in paypal_gateway.WEBHOOK_EVENTS:
            assert _webhook(event_type, {}).status_code == 200, event_type
    ignored = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ignoring PayPal event")]
    assert ignored == []
    with caplog.at_level(logging.INFO, logger="bakery.payments"):
        assert _webhook("BILLING.PLAN.CREATED", {}).status_code == 200
    assert any("ignoring PayPal event BILLING.PLAN.CREATED" in r.getMessage() for r in caplog.records)


def test_the_keys_paypal_dedupes_on_cannot_collide_with_another_databases(monkeypatch):
    """PayPal remembers idempotency keys per merchant account, not per
    database. A key built from a bare row id would repeat wherever the same
    credentials meet a second database — and hand one customer another
    order's pay page, or book a refund that never left."""
    order, _headers, paypal_id = _paid("unique-keys")

    create_key = next(c[2] for c in paypal_calls if c[0] == "create_order" and c[1] == order["id"])
    db = SessionLocal()
    try:
        placed = db.get(Order, order["id"]).created_at
    finally:
        db.close()
    import calendar

    # the order's id AND the second it was placed — two databases never share both
    assert create_key == f"bh-{order['id']}-{calendar.timegm(placed.timetuple())}-1"

    assert client.post(
        f"/api/admin/orders/{order['id']}/refunds", json={"amount": 1.0}, headers=admin_headers()
    ).status_code == 200
    refund_key = next(c[4] for c in paypal_calls if c[0] == "refund" and c[1] == f"CAP-{paypal_id}")
    # keyed on PayPal's own capture id, which exists nowhere else
    assert refund_key == f"refund-CAP-{paypal_id}-0-100"


def test_a_paypal_refund_still_on_its_way_is_confirmed_by_paypals_own_event(monkeypatch, caplog):
    order, headers, paypal_id = _paid("refund-pending")
    monkeypatch.setattr(
        paypal_gateway, "refund_capture",
        lambda capture_id, cents, currency, request_id: {"id": "REF-ECHECK-1", "status": "PENDING"},
    )

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        asked = client.post(
            f"/api/admin/orders/{order['id']}/refunds", json={"amount": 3.0}, headers=admin_headers()
        )
    assert asked.status_code == 200
    assert asked.json()["payment_status"] == "paid" and asked.json()["refunded_amount"] == 0
    assert "Refund sent" not in caplog.text

    event = {
        "id": "REF-ECHECK-1", "status": "COMPLETED", "amount": {"value": "3.00", "currency_code": "EUR"},
        "links": [{"rel": "up", "href": f"https://api.paypal.test/v2/payments/captures/CAP-{paypal_id}"}],
    }
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        assert _webhook("PAYMENT.CAPTURE.REFUNDED", event).status_code == 200
        assert _webhook("PAYMENT.CAPTURE.REFUNDED", event).status_code == 200  # redelivered
    assert caplog.text.count(f"Refund sent — order {order['order_number']}") == 1

    mine = _detail(order["id"], headers)
    assert (mine["payment_status"], mine["refunded_amount"]) == ("partially_refunded", 3.0)
    assert mine["timeline"][-1]["comment"] == "PayPal confirmed the refund — payment marked partially refunded"
    assert len(_payments(order["id"])["transactions"][0]["refunds"]) == 1  # confirmed, not doubled


def test_a_paypal_order_is_in_the_bookkeeping_export_under_its_capture_id():
    import csv
    import io

    order, _headers, paypal_id = _paid("export-row")
    response = client.get("/api/admin/orders/export", headers=admin_headers())
    header, *rows = list(csv.reader(io.StringIO(response.text)))
    row = dict(zip(header, next(r for r in rows if r[0] == order["order_number"])))
    assert (row["payment_method"], row["payment_provider"], row["payment_reference"]) == (
        "paypal", "paypal", f"CAP-{paypal_id}",
    )
