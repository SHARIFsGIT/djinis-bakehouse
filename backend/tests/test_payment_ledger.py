"""The payment ledger, real refunds and reconciliation — through Stripe.

What this file pins, each against the API the storefront and the desk use:

* every pay page, payment, refund and dispute leaves a ledger row and an
  audit trail, in the same transaction as the order change it describes;
* a webhook delivered twice acts once;
* a refund is the provider's doing — the order reads "refunded" only on
  the provider's confirmation, a refused one is on record as failed, and a
  hand label can no longer stand in for one;
* one press of the pay button is one order, however often it arrives;
* the reconciliation report names what does not add up.

Rides on test_api.py's client and its Stripe fake (the app boots once
whichever file pytest reaches first). PayPal has its own file.
"""

import logging

import pytest
from sqlalchemy import select

import test_api as api
from test_api import (
    admin_headers,
    auth_header,
    checkout_payload,
    client,
    fake_sessions,
    gateway_calls,
    last_session_id,
    pay_order_via_webhook,
    stripe_gateway,
)

from app.database import SessionLocal, utcnow
from app.models import CheckoutAttempt, Order, PaymentEvent, PaymentRefund, PaymentTransaction

SIGNED = {"stripe-signature": "t=1,v1=fake"}


def _customer(name: str) -> dict:
    return auth_header(api.register_and_login(f"{name}@ledger.example.com", "Ledger123!"))


def _place(headers: dict, **overrides) -> dict:
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    response = client.post("/api/orders", json=checkout_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _paid_order(name: str) -> tuple[dict, dict]:
    headers = _customer(name)
    order = _place(headers)
    pay_order_via_webhook(order["id"])
    return order, headers


def _payments(order_id: int) -> dict:
    response = client.get(f"/api/admin/orders/{order_id}/payments", headers=admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _refund(order_id: int, **body):
    return client.post(f"/api/admin/orders/{order_id}/refunds", json=body, headers=admin_headers())


def _stripe_event(event_type: str, obj: dict, event_id: str | None = None):
    event = {"type": event_type, "data": {"object": obj}}
    if event_id:
        event["id"] = event_id
    response = client.post("/api/payments/webhook", json=event, headers=SIGNED)
    assert response.status_code == 200, response.text
    return response


def _detail(order_id: int, headers: dict) -> dict:
    return client.get(f"/api/orders/{order_id}", headers=headers).json()


def _cents(amount: float) -> int:
    return int(round(amount * 100))


def _issues_for(order_id: int) -> dict[str, dict]:
    report = client.get("/api/admin/payments/reconciliation", headers=admin_headers())
    assert report.status_code == 200, report.text
    return {i["kind"]: i for i in report.json()["issues"] if i["order_id"] == order_id}


def _desk_titles() -> list[str]:
    listing = client.get(
        "/api/admin/notifications", params={"limit": 50}, headers=admin_headers()
    ).json()
    return [item["title"] for item in listing["items"]]


# --------------------------------------------------------------------------
# The ledger follows the payment
# --------------------------------------------------------------------------

def test_a_card_order_leaves_a_ledger_trail_from_pay_page_to_payment():
    headers = _customer("trail")
    order = _place(headers)
    session_id = last_session_id(order["id"])

    opened = _payments(order["id"])
    (tx,) = opened["transactions"]
    assert (tx["provider"], tx["provider_reference"], tx["status"]) == ("stripe", session_id, "pending")
    assert tx["amount"] == order["total"] and tx["currency"] == "eur"
    assert tx["provider_payment_id"] is None and tx["paid_at"] is None
    assert opened["provider"] == "stripe" and opened["paid_amount"] == 0

    pay_order_via_webhook(order["id"])

    paid = _payments(order["id"])
    (tx,) = paid["transactions"]
    assert tx["status"] == "succeeded"
    assert tx["provider_payment_id"] == f"pi_fake_{order['id']}"
    assert tx["paid_at"] is not None
    assert paid["paid_amount"] == order["total"]
    assert paid["refundable_amount"] == order["total"]
    trail = [e["event_type"] for e in reversed(paid["events"])]
    assert trail[:2] == ["pay page opened", "payment received"]

    # the customer's own view names the provider, and nothing refunded
    mine = _detail(order["id"], headers)
    assert mine["payment_provider"] == "stripe" and mine["refunded_amount"] == 0


def test_payment_history_is_the_desks_alone():
    order, headers = _paid_order("private-history")
    assert client.get(f"/api/admin/orders/{order['id']}/payments").status_code == 401
    assert client.get(f"/api/admin/orders/{order['id']}/payments", headers=headers).status_code == 403
    assert client.post(
        f"/api/admin/orders/{order['id']}/refunds", json={}, headers=headers
    ).status_code == 403
    assert client.get("/api/admin/payments/reconciliation", headers=headers).status_code == 403
    assert client.post(
        f"/api/admin/orders/{order['id']}/payments/sync", headers=headers
    ).status_code == 403
    assert not [c for c in gateway_calls if c[0] == "refund" and c[1] == f"pi_fake_{order['id']}"]


def test_a_webhook_delivered_twice_acts_once():
    """Providers redeliver. A dispute notice is the clearest witness: the
    old handler wrote a timeline line and rang the desk for every copy."""
    order, headers = _paid_order("redelivered")
    dispute = {"payment_intent": f"pi_fake_{order['id']}", "reason": "fraudulent"}

    _stripe_event("charge.dispute.created", dispute, event_id=f"evt_dispute_{order['id']}")
    _stripe_event("charge.dispute.created", dispute, event_id=f"evt_dispute_{order['id']}")

    lines = [e for e in _detail(order["id"], headers)["timeline"] if "dispute" in e["comment"].lower()]
    assert len(lines) == 1
    db = SessionLocal()
    try:
        claims = db.scalars(
            select(PaymentEvent).where(PaymentEvent.event_id == f"evt_dispute_{order['id']}")
        ).all()
        assert len(claims) == 1  # the unique (provider, event id) is the referee
    finally:
        db.close()


def test_an_event_without_a_session_id_touches_no_order():
    """A "paid" session with no id used to be looked up as "the order whose
    session is NULL" — which is every cash order in the book."""
    headers = _customer("no-session-id")
    cash = _place(headers, payment_method="cash_on_delivery")

    _stripe_event("checkout.session.completed", {"payment_status": "paid", "payment_intent": "pi_stray"})

    assert _detail(cash["id"], headers)["payment_status"] == "pending"


# --------------------------------------------------------------------------
# Refunds from the desk
# --------------------------------------------------------------------------

def test_a_partial_then_full_refund_goes_through_stripe_and_onto_the_book(caplog):
    order, headers = _paid_order("refund-me")
    intent = f"pi_fake_{order['id']}"

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        first = _refund(order["id"], amount=5.0, reason="One croissant arrived squashed")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["payment_status"] == "partially_refunded"
    assert body["refunded_amount"] == 5.0
    assert body["refundable_amount"] == round(order["total"] - 5.0, 2)
    (refund,) = body["transactions"][0]["refunds"]
    assert (refund["status"], refund["amount"], refund["reason"]) == (
        "succeeded", 5.0, "One croissant arrived squashed",
    )
    assert refund["initiated_by"] == "Djini Rahman"  # the admin who pressed it
    assert ("refund", intent, 500) in gateway_calls
    # the customer is told — once the provider has confirmed, and how much
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Refund sent — order {order['order_number']}" in logged
    assert "€5.00" in logged and "in part" in logged

    mine = _detail(order["id"], headers)
    assert mine["payment_status"] == "partially_refunded" and mine["refunded_amount"] == 5.0
    # the customer's timeline says what happened — and keeps the desk's note to the desk
    assert mine["timeline"][-1]["comment"] == "Refund of €5.00 sent back through Stripe"

    # a second refund of the same amount is its own decision, not a retry
    second = _refund(order["id"], amount=5.0)
    assert second.status_code == 200
    assert second.json()["refunded_amount"] == 10.0
    assert len(second.json()["transactions"][0]["refunds"]) == 2

    # no amount = everything still held
    rest = _refund(order["id"])
    assert rest.status_code == 200
    assert rest.json()["payment_status"] == "refunded"
    assert rest.json()["refundable_amount"] == 0
    assert ("refund", intent, _cents(order["total"]) - 1000) in gateway_calls

    # ...after which there is nothing left to send
    again = _refund(order["id"], amount=1.0)
    assert again.status_code == 400
    assert "already been refunded in full" in again.json()["detail"]


def test_a_refund_cannot_exceed_what_is_held_or_touch_a_cash_order():
    order, headers = _paid_order("over-refund")
    calls_before = len([c for c in gateway_calls if c[0] == "refund"])

    too_much = _refund(order["id"], amount=order["total"] + 0.01)
    assert too_much.status_code == 400
    assert "left to refund" in too_much.json()["detail"]
    assert _refund(order["id"], amount=0).status_code == 422
    assert _refund(order["id"], amount=-3).status_code == 422

    cash = _place(headers, payment_method="cash_on_delivery")
    refused = _refund(cash["id"], amount=1.0)
    assert refused.status_code == 400
    assert "wasn't paid online" in refused.json()["detail"]

    unpaid = _place(headers)  # a card order nobody has paid yet
    nothing = _refund(unpaid["id"], amount=1.0)
    assert nothing.status_code == 400
    assert len([c for c in gateway_calls if c[0] == "refund"]) == calls_before


def test_a_refund_stripe_refuses_is_on_record_and_changes_nothing(monkeypatch):
    order, headers = _paid_order("refund-refused")

    def broken_refund(payment_intent, amount_cents, sequence=0):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    refused = _refund(order["id"], amount=4.0)

    assert refused.status_code == 502
    assert "nothing was sent back" in refused.json()["detail"]
    book = _payments(order["id"])
    assert book["payment_status"] == "paid" and book["refunded_amount"] == 0
    (attempt,) = book["transactions"][0]["refunds"]
    assert attempt["status"] == "failed" and attempt["failure_reason"]
    assert "could not be sent" in _detail(order["id"], headers)["timeline"][-1]["comment"]
    assert _issues_for(order["id"])["failed_refund"]["severity"] == "action"

    # the desk tries again once Stripe is back — and the failed line clears
    monkeypatch.undo()
    assert _refund(order["id"]).status_code == 200
    assert "failed_refund" not in _issues_for(order["id"])


def test_a_pending_refund_is_not_a_refund_until_stripe_confirms(monkeypatch, caplog):
    """Bank methods answer "pending". The order must keep saying "paid"
    until the provider confirms — and stop saying "refunded" if the bank
    later sends the refund back."""
    order, headers = _paid_order("refund-pending")
    issued = iter(("re_pending_a", "re_pending_b"))
    monkeypatch.setattr(
        stripe_gateway, "refund",
        lambda intent, cents, sequence=0: {"id": next(issued), "status": "pending"},
    )

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        asked = _refund(order["id"], amount=4.0)
    assert asked.status_code == 200
    body = asked.json()
    assert body["payment_status"] == "paid" and body["refunded_amount"] == 0  # nothing confirmed
    assert body["transactions"][0]["refunds"][0]["status"] == "pending"
    assert body["refundable_amount"] == round(order["total"] - 4.0, 2)  # but not refundable twice
    assert "yet to confirm" in _detail(order["id"], headers)["timeline"][-1]["comment"]
    assert "Refund sent" not in "\n".join(r.getMessage() for r in caplog.records)

    _stripe_event("refund.updated", {"id": "re_pending_a", "status": "succeeded"})
    confirmed = _detail(order["id"], headers)
    assert confirmed["payment_status"] == "partially_refunded" and confirmed["refunded_amount"] == 4.0
    assert confirmed["timeline"][-1]["comment"] == "Stripe confirmed the refund of €4.00"

    # a second refund that the bank then bounces
    assert _refund(order["id"], amount=2.0).status_code == 200
    _stripe_event(
        "refund.failed", {"id": "re_pending_b", "status": "failed", "failure_reason": "lost_or_stolen_card"}
    )
    bounced = _detail(order["id"], headers)
    assert bounced["refunded_amount"] == 4.0  # only the confirmed one counts
    assert "failed at Stripe" in bounced["timeline"][-1]["comment"]
    assert f"Refund failed on order {order['order_number']}" in _desk_titles()
    refunds = _payments(order["id"])["transactions"][0]["refunds"]
    assert [r["status"] for r in refunds] == ["succeeded", "failed"]


def test_a_hand_label_cannot_stand_in_for_a_providers_refund():
    order, headers = _paid_order("no-hand-label")
    admin = admin_headers()

    for label in ("refunded", "pending", "paid"):
        relabel = client.put(
            f"/api/admin/orders/{order['id']}/payment", json={"payment_status": label}, headers=admin
        )
        assert relabel.status_code == 400, label
        assert "paid through Stripe" in relabel.json()["detail"]
    assert _detail(order["id"], headers)["payment_status"] == "paid"

    # an online order nothing has paid for can still be marked by hand —
    # and the timeline says that is all it was
    unpaid = _place(headers)
    marked = client.put(
        f"/api/admin/orders/{unpaid['id']}/payment", json={"payment_status": "paid"}, headers=admin
    )
    assert marked.status_code == 200
    assert "no money was moved" in marked.json()["timeline"][-1]["comment"]
    assert "paid_without_provider" in _issues_for(unpaid["id"])


def test_a_dashboard_refund_lands_in_the_ledger_as_one():
    order, headers = _paid_order("dashboard-refund")
    charge = {
        "payment_intent": f"pi_fake_{order['id']}",
        "amount": _cents(order["total"]),
        "amount_refunded": 300,
    }
    _stripe_event("charge.refunded", charge)
    _stripe_event("charge.refunded", charge)  # Stripe repeating itself

    book = _payments(order["id"])
    (refund,) = book["transactions"][0]["refunds"]
    assert (refund["status"], refund["amount"], refund["initiated_by"]) == (
        "succeeded", 3.0, "Stripe dashboard",
    )
    assert book["payment_status"] == "partially_refunded"

    # a second, distinct partial refund at Stripe is seen as its own — the
    # old book could not tell it from a replay of the first
    _stripe_event("charge.refunded", dict(charge, amount_refunded=800))
    refunds = _payments(order["id"])["transactions"][0]["refunds"]
    assert [r["amount"] for r in refunds] == [3.0, 5.0]


def test_cancelling_after_a_partial_refund_returns_only_what_is_left():
    order, headers = _paid_order("partial-then-cancel")
    assert _refund(order["id"], amount=5.0).status_code == 200

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()

    assert cancelled["status"] == "cancelled" and cancelled["payment_status"] == "refunded"
    assert ("refund", f"pi_fake_{order['id']}", _cents(order["total"]) - 500) in gateway_calls
    assert _payments(order["id"])["refundable_amount"] == 0


# --------------------------------------------------------------------------
# Disputes
# --------------------------------------------------------------------------

def test_a_dispute_lost_books_the_money_as_gone_and_one_won_restores_it():
    lost, lost_headers = _paid_order("dispute-lost")
    intent = f"pi_fake_{lost['id']}"
    _stripe_event("charge.dispute.created", {"payment_intent": intent, "reason": "product_not_received"})

    opened = _payments(lost["id"])["transactions"][0]
    assert (opened["status"], opened["dispute_status"]) == ("disputed", "open")
    assert "open_dispute" in _issues_for(lost["id"])

    _stripe_event(
        "charge.dispute.closed",
        {"id": f"dp_{lost['id']}", "payment_intent": intent, "status": "lost", "amount": _cents(lost["total"])},
    )
    closed = _payments(lost["id"])
    assert closed["payment_status"] == "refunded"
    assert closed["transactions"][0]["dispute_status"] == "lost"
    assert closed["transactions"][0]["refunds"][0]["initiated_by"] == "chargeback (dispute lost)"
    assert "Dispute lost" in _detail(lost["id"], lost_headers)["timeline"][-1]["comment"]
    assert "open_dispute" not in _issues_for(lost["id"])

    won, won_headers = _paid_order("dispute-won")
    intent = f"pi_fake_{won['id']}"
    _stripe_event("charge.dispute.created", {"payment_intent": intent})
    _stripe_event("charge.dispute.closed", {"payment_intent": intent, "status": "won"})
    restored = _payments(won["id"])
    assert restored["payment_status"] == "paid"
    assert (restored["transactions"][0]["status"], restored["transactions"][0]["dispute_status"]) == (
        "succeeded", "won",
    )


# --------------------------------------------------------------------------
# One press, one order
# --------------------------------------------------------------------------

def test_the_same_press_arriving_twice_is_one_order():
    headers = _customer("double-press")
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    stock_before = api.get_product("butter-croissant")["stock"]
    press = {**headers, "Idempotency-Key": "press-double-0001"}

    first = client.post("/api/orders", json=checkout_payload(), headers=press)
    again = client.post("/api/orders", json=checkout_payload(), headers=press)

    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["payment_url"] == first.json()["payment_url"]  # the same pay page
    assert api.get_product("butter-croissant")["stock"] == stock_before - 2
    mine = client.get("/api/orders", headers=headers).json()
    assert mine["pagination"]["total_items"] == 1
    assert len([s for s in fake_sessions if s.startswith(f"cs_fake_{first.json()['id']}_")]) == 1

    # a new press is a new order; a key nobody could have minted is refused
    other = client.post(
        "/api/orders", json=checkout_payload(), headers={**headers, "Idempotency-Key": "press-double-0002"}
    )
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
    bad = client.post(
        "/api/orders", json=checkout_payload(), headers={**headers, "Idempotency-Key": "short"}
    )
    assert bad.status_code == 400

    # another customer's identical key is their own press, not this order
    stranger = _customer("double-press-stranger")
    theirs = client.post(
        "/api/orders", json=checkout_payload(), headers={**stranger, "Idempotency-Key": "press-double-0001"}
    )
    assert theirs.status_code == 201 and theirs.json()["id"] != first.json()["id"]


def test_a_press_the_provider_failed_is_forgotten_so_the_retry_places_an_order(monkeypatch):
    headers = _customer("failed-press")
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    press = {**headers, "Idempotency-Key": "press-failed-0001"}
    real_create = stripe_gateway.create_checkout_session

    def stripe_is_down(order, base_url):
        raise stripe_gateway.GatewayUnavailable("Stripe did not answer")

    monkeypatch.setattr(stripe_gateway, "create_checkout_session", stripe_is_down)
    failed = client.post("/api/orders", json=checkout_payload(), headers=press)
    assert failed.status_code == 503

    monkeypatch.setattr(stripe_gateway, "create_checkout_session", real_create)
    retried = client.post("/api/orders", json=checkout_payload(), headers=press)
    assert retried.status_code == 201  # a fresh order, not the cancelled one
    assert retried.json()["status"] == "pending" and retried.json()["payment_url"]

    db = SessionLocal()
    try:
        attempts = db.scalars(
            select(CheckoutAttempt).where(CheckoutAttempt.idempotency_key == "press-failed-0001")
        ).all()
        assert [a.order_id for a in attempts] == [retried.json()["id"]]
    finally:
        db.close()


def test_an_order_too_small_for_the_provider_is_refused_before_it_exists():
    admin = admin_headers()
    for code, percent in (("LEDGERFREE100", 100), ("LEDGERTINY99", 99)):
        made = client.post("/api/admin/coupons", json={"code": code, "percent_off": percent}, headers=admin)
        assert made.status_code in (201, 409)
    headers = _customer("too-small")
    api._top_up_stock("butter-croissant", 60)
    croissant = api.get_product("butter-croissant")
    box = [{"product_id": croissant["id"], "quantity": 8}]  # €30.00 — free delivery
    stock_before = api.get_product("butter-croissant")["stock"]

    nothing = client.post(
        "/api/orders", json=checkout_payload(items=box, coupon_code="LEDGERFREE100"), headers=headers
    )
    assert nothing.status_code == 400
    assert "nothing left to pay" in nothing.json()["detail"]

    tiny = client.post(
        "/api/orders", json=checkout_payload(items=box, coupon_code="LEDGERTINY99"), headers=headers
    )
    assert tiny.status_code == 400
    assert "can't take less than €0.50" in tiny.json()["detail"]
    assert api.get_product("butter-croissant")["stock"] == stock_before  # no order, no stock moved

    # the same free box is a perfectly good cash order
    free = client.post(
        "/api/orders",
        json=checkout_payload(items=box, coupon_code="LEDGERFREE100", payment_method="cash_on_delivery"),
        headers=headers,
    )
    assert free.status_code == 201 and free.json()["total"] == 0


# --------------------------------------------------------------------------
# Reconciliation and recovery
# --------------------------------------------------------------------------

def test_the_report_names_what_does_not_add_up(monkeypatch):
    import datetime

    report = client.get("/api/admin/payments/reconciliation", headers=admin_headers()).json()
    stripe = next(p for p in report["providers"] if p["provider"] == "stripe")
    assert (stripe["configured"], stripe["mode"], stripe["webhook_ready"]) == (True, "test", True)
    assert next(p for p in report["providers"] if p["provider"] == "paypal")["configured"] is False

    # a clean, paid order is not in the report at all
    clean, _ = _paid_order("clean-book")
    assert _issues_for(clean["id"]) == {}

    # 1. an order still waiting long after its pay window
    headers = _customer("stuck")
    stuck = _place(headers)
    db = SessionLocal()
    try:
        db.get(Order, stuck["id"]).created_at = utcnow() - datetime.timedelta(hours=2)
        db.commit()
    finally:
        db.close()
    assert _issues_for(stuck["id"])["stuck_pending"]["severity"] == "action"

    # ...which "check with the provider" resolves by the provider's word
    fake_sessions[last_session_id(stuck["id"])].update(
        status="complete", payment_status="paid", payment_intent=f"pi_sync_{stuck['id']}"
    )
    synced = client.post(f"/api/admin/orders/{stuck['id']}/payments/sync", headers=admin_headers())
    assert synced.status_code == 200
    assert synced.json()["payment_status"] == "paid"
    assert "confirms the payment" in synced.json()["sync_outcome"]
    assert _issues_for(stuck["id"]) == {}

    # 2. money still held for a cancelled order (its refund could not be sent)
    held, held_headers = _paid_order("held")

    def broken_refund(payment_intent, amount_cents, sequence=0):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    client.post(f"/api/orders/{held['id']}/cancel", headers=held_headers)
    monkeypatch.undo()
    issues = _issues_for(held["id"])
    assert issues["held_on_cancelled"]["severity"] == "action"
    assert "failed_refund" in issues
    # the desk's refund button is the recovery
    assert _refund(held["id"]).status_code == 200
    assert _issues_for(held["id"]) == {}


def test_a_payment_from_before_the_ledger_gets_its_row_when_first_needed():
    """Orders paid before these tables existed carry their trail on the
    order itself. The first refund must work for them all the same."""
    order, headers = _paid_order("pre-ledger")
    db = SessionLocal()
    try:  # take the ledger away, as if the payment predated it
        for model in (PaymentEvent, PaymentRefund, PaymentTransaction):
            for row in db.scalars(select(model).where(model.order_id == order["id"])):
                db.delete(row)
        db.commit()
    finally:
        db.close()

    book = _payments(order["id"])
    (tx,) = book["transactions"]
    assert tx["status"] == "succeeded" and tx["provider_payment_id"] == f"pi_fake_{order['id']}"
    assert any("order's own record" in e["event_type"] for e in book["events"])

    refunded = _refund(order["id"], amount=2.5)
    assert refunded.status_code == 200 and refunded.json()["payment_status"] == "partially_refunded"


def test_a_duplicate_payment_is_its_own_ledger_row_refunded_in_full():
    headers = _customer("duplicate-ledger")
    order = _place(headers)
    first = last_session_id(order["id"])
    assert client.post(f"/api/payments/{order['id']}/session", headers=headers).status_code == 201
    second = last_session_id(order["id"])

    def deliver(session_id, intent):
        fake_sessions[session_id].update(status="complete", payment_status="paid", payment_intent=intent)
        _stripe_event(
            "checkout.session.completed",
            dict(fake_sessions[session_id], metadata={"order_id": str(order["id"])},
                 amount_total=_cents(order["total"])),
        )

    deliver(second, "pi_ledger_kept")
    deliver(first, "pi_ledger_extra")

    book = _payments(order["id"])
    rows = {tx["provider_payment_id"]: tx for tx in book["transactions"]}
    assert rows["pi_ledger_kept"]["status"] == "succeeded"
    assert rows["pi_ledger_extra"]["status"] == "refunded"
    assert rows["pi_ledger_extra"]["refunds"][0]["initiated_by"].startswith("duplicate payment")
    assert book["payment_status"] == "paid" and book["refunded_amount"] == 0  # the order's own money stands
    assert "duplicate_payment" not in _issues_for(order["id"])


def test_a_replaced_or_lapsed_pay_page_is_closed_in_the_ledger():
    headers = _customer("closed-pages")
    order = _place(headers)
    first = last_session_id(order["id"])
    client.post(f"/api/payments/{order['id']}/session", headers=headers)
    second = last_session_id(order["id"])

    statuses = {tx["provider_reference"]: tx["status"] for tx in _payments(order["id"])["transactions"]}
    assert statuses == {first: "cancelled", second: "pending"}

    api.expire_order_via_webhook(order["id"])
    statuses = {tx["provider_reference"]: tx["status"] for tx in _payments(order["id"])["transactions"]}
    assert statuses == {first: "cancelled", second: "expired"}
    assert _detail(order["id"], headers)["status"] == "cancelled"


@pytest.mark.parametrize("percent", [25])
def test_the_ledger_and_the_order_agree_after_a_late_fee_cancellation(percent):
    admin = admin_headers()
    before = client.get("/api/admin/settings", headers=admin).json()["late_cancel_fee_percent"]
    client.put("/api/admin/settings", json={"late_cancel_fee_percent": percent}, headers=admin)
    try:
        order, headers = _paid_order("fee-agree")
        client.put(
            f"/api/admin/orders/{order['id']}/status", json={"status": "baking", "notify": False}, headers=admin
        )
        cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
        fee = cancelled["cancel_fee"]
        book = _payments(order["id"])
        assert cancelled["payment_status"] == book["payment_status"] == "partially_refunded"
        assert book["refunded_amount"] == round(order["total"] - fee, 2)
        # what is still held is exactly the fee the shop keeps — not an issue
        assert "held_on_cancelled" not in _issues_for(order["id"])
    finally:
        client.put("/api/admin/settings", json={"late_cancel_fee_percent": before}, headers=admin)


def test_a_refund_id_belongs_to_one_payment_only(monkeypatch):
    """A provider's refund id names one refund on one payment. If an answer
    ever carries an id the book already holds against another payment, it
    is not booked a second time and the first record is left as it stands."""
    first, _ = _paid_order("refund-id-first")
    second, second_headers = _paid_order("refund-id-second")
    monkeypatch.setattr(
        stripe_gateway, "refund",
        lambda intent, cents, sequence=0: {"id": "re_same_id_twice", "status": "succeeded"},
    )

    assert _refund(first["id"], amount=2.0).status_code == 200
    clash = _refund(second["id"], amount=3.0)

    assert clash.status_code == 502
    book = _payments(second["id"])
    assert book["payment_status"] == "paid" and book["refunded_amount"] == 0
    (attempt,) = book["transactions"][0]["refunds"]
    assert attempt["status"] == "failed" and "another payment" in attempt["failure_reason"]
    assert "failed_refund" in _issues_for(second["id"])
    assert _detail(second["id"], second_headers)["payment_status"] == "paid"
    # the refund that id really names is exactly as it was
    (kept,) = _payments(first["id"])["transactions"][0]["refunds"]
    assert (kept["status"], kept["amount"], kept["provider_refund_id"]) == (
        "succeeded", 2.0, "re_same_id_twice",
    )


# --------------------------------------------------------------------------
# Payments that settle later (bank debits and friends)
# --------------------------------------------------------------------------

def _checked_out_with_a_delayed_method(order_id: int) -> str:
    """The customer finished Stripe's page with a method whose money
    arrives later: the session is complete, and not yet paid."""
    session_id = last_session_id(order_id)
    fake_sessions[session_id].update(status="complete", payment_status="unpaid")
    _stripe_event(
        "checkout.session.completed",
        dict(fake_sessions[session_id], metadata={"order_id": str(order_id)}),
    )
    return session_id


def test_a_payment_still_settling_is_processing_not_paid_and_not_payable_twice():
    import datetime

    from app.routers.payments import reconcile_stale_card_orders

    headers = _customer("bank-debit")
    order = _place(headers)
    session_id = _checked_out_with_a_delayed_method(order["id"])

    waiting = _detail(order["id"], headers)
    assert waiting["payment_status"] == "pending"  # not paid on a promise
    assert waiting["payment_state"] == "processing"
    assert _payments(order["id"])["transactions"][0]["status"] == "processing"

    # "complete payment" must not open a second pay page beside the debit
    sessions_before = len([s for s in fake_sessions if s.startswith(f"cs_fake_{order['id']}_")])
    again = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert again.status_code == 200 and again.json()["payment_url"] is None
    assert again.json()["payment_state"] == "processing"
    assert len([s for s in fake_sessions if s.startswith(f"cs_fake_{order['id']}_")]) == sessions_before
    assert fake_sessions[session_id]["status"] == "complete"  # and the page was not expired

    # long past the pay window it is still neither cancelled nor "stuck"
    db = SessionLocal()
    try:
        db.get(Order, order["id"]).created_at = utcnow() - datetime.timedelta(hours=3)
        db.commit()
    finally:
        db.close()
    reconcile_stale_card_orders()
    assert _detail(order["id"], headers)["status"] == "pending"
    issues = _issues_for(order["id"])
    assert "stuck_pending" not in issues
    assert issues["processing_payment"]["severity"] == "watch"
    synced = client.post(f"/api/admin/orders/{order['id']}/payments/sync", headers=admin_headers())
    assert "still settling" in synced.json()["sync_outcome"]

    # the bank confirms: paid, exactly as a card would have been
    fake_sessions[session_id].update(payment_status="paid", payment_intent=f"pi_debit_{order['id']}")
    _stripe_event(
        "checkout.session.async_payment_succeeded",
        dict(fake_sessions[session_id], metadata={"order_id": str(order["id"])}),
    )
    paid = _detail(order["id"], headers)
    assert (paid["payment_status"], paid["payment_state"]) == ("paid", "succeeded")
    assert _issues_for(order["id"]) == {}


def test_a_delayed_payment_found_by_asking_is_processing_too():
    """No webhook at all: the customer returns, the shop asks Stripe, and
    Stripe says "complete, not yet paid"."""
    headers = _customer("bank-debit-asked")
    order = _place(headers)
    fake_sessions[last_session_id(order["id"])].update(status="complete", payment_status="unpaid")

    back = client.post(f"/api/payments/confirm/{order['id']}", headers=headers).json()

    assert (back["payment_status"], back["payment_state"]) == ("pending", "processing")
    again = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert again.status_code == 200 and again.json()["payment_url"] is None


def test_the_customers_view_names_where_the_payment_stands():
    headers = _customer("state-words")
    cash = _place(headers, payment_method="cash_on_delivery")
    assert _detail(cash["id"], headers)["payment_state"] is None  # no provider, no state

    card = _place(headers)
    assert _detail(card["id"], headers)["payment_state"] == "pending"
    api.expire_order_via_webhook(card["id"])
    assert _detail(card["id"], headers)["payment_state"] == "expired"

    paid = _place(headers)
    pay_order_via_webhook(paid["id"])
    assert _detail(paid["id"], headers)["payment_state"] == "succeeded"
    assert _refund(paid["id"], amount=1.0).status_code == 200
    assert _detail(paid["id"], headers)["payment_state"] == "partially_refunded"


def test_a_refund_that_failed_stops_being_flagged_once_a_later_one_goes_through(monkeypatch):
    order, _headers = _paid_order("refund-retried")

    def broken_refund(payment_intent, amount_cents, sequence=0):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    assert _refund(order["id"], amount=2.0).status_code == 502
    assert "failed_refund" in _issues_for(order["id"])

    monkeypatch.undo()
    assert _refund(order["id"], amount=2.0).status_code == 200  # the same part, sent again

    # most of the payment is still held — and that is no longer a problem
    assert _payments(order["id"])["refundable_amount"] == round(order["total"] - 2.0, 2)
    assert "failed_refund" not in _issues_for(order["id"])


# --------------------------------------------------------------------------
# What the customer actually paid with
# --------------------------------------------------------------------------

def test_the_receipt_and_the_book_name_the_method_the_provider_reported(caplog):
    """Stripe's page offers whatever the account has switched on, so "card"
    is no longer a safe assumption — the method is read back from Stripe."""
    headers = _customer("paid-with-klarna")
    order = _place(headers)
    api.fake_payment_methods[f"pi_fake_{order['id']}"] = "klarna"

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        pay_order_via_webhook(order["id"])

    mine = _detail(order["id"], headers)
    assert mine["timeline"][-1]["comment"] == "Payment received — paid with Klarna"
    assert mine["payment_label"] == "Klarna"
    assert _payments(order["id"])["transactions"][0]["method_label"] == "Klarna"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Paid by Klarna — this email is your proof of purchase" in logged

    # a card in a wallet is still a card, and says which wallet
    wallet = _place(headers)
    api.fake_payment_methods[f"pi_fake_{wallet['id']}"] = "card:apple_pay"
    pay_order_via_webhook(wallet["id"])
    paid = _detail(wallet["id"], headers)
    assert paid["timeline"][-1]["comment"] == "Payment received — paid by card"
    assert paid["payment_label"] == "Card (Apple Pay)"

    # a method this shop has no name for is "online", never a guess
    newer = _place(headers)
    api.fake_payment_methods[f"pi_fake_{newer['id']}"] = "some_new_method"
    pay_order_via_webhook(newer["id"])
    named = _detail(newer["id"], headers)
    assert named["timeline"][-1]["comment"] == "Payment received — paid online"
    assert named["payment_label"] == "Online payment"


def test_a_method_stripe_cannot_name_never_holds_the_payment_up():
    headers = _customer("method-unknown")
    order = _place(headers)
    api.fake_payment_methods[f"pi_fake_{order['id']}"] = None  # Stripe could not be asked

    pay_order_via_webhook(order["id"])

    paid = _detail(order["id"], headers)
    assert paid["payment_status"] == "paid"
    assert paid["payment_label"] is None  # the page falls back to the order's own way of paying
    assert _payments(order["id"])["transactions"][0]["method_label"] is None


def test_the_method_is_asked_once_per_landed_payment_and_never_for_an_unpaid_one():
    headers = _customer("method-lookups")
    unpaid = _place(headers)
    before = len(api.method_lookups)
    client.post(f"/api/payments/confirm/{unpaid['id']}", headers=headers)  # still unpaid
    assert len(api.method_lookups) == before

    pay_order_via_webhook(unpaid["id"])
    assert api.method_lookups[before:] == [f"pi_fake_{unpaid['id']}"]


def test_a_webhook_body_is_bounded_before_anything_reads_it():
    huge = b"x" * (1024 * 1024 + 1)
    for path in ("/api/payments/webhook", "/api/payments/paypal/webhook"):
        refused = client.post(path, content=huge, headers={**SIGNED, "content-type": "application/json"})
        assert refused.status_code == 413, path
    # an ordinary event still goes through the same door
    assert client.post(
        "/api/payments/webhook", json={"type": "ping", "data": {"object": {}}}, headers=SIGNED
    ).status_code == 200


def test_every_event_the_shop_asks_stripe_for_is_one_it_acts_on(caplog):
    """The list in the go-live runbook and the readiness check is this one.
    If the handler ever stops acting on an entry, the list is lying."""
    with caplog.at_level(logging.INFO, logger="bakery.payments"):
        for event_type in stripe_gateway.WEBHOOK_EVENTS + stripe_gateway.WEBHOOK_EVENTS_OPTIONAL:
            _stripe_event(event_type, {})
    ignored = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ignoring Stripe event")]
    assert ignored == []
    # and an event that is not on the list is simply acknowledged
    with caplog.at_level(logging.INFO, logger="bakery.payments"):
        _stripe_event("customer.created", {})
    assert any("ignoring Stripe event customer.created" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# Refunds a bank takes its time over
# --------------------------------------------------------------------------

def test_the_customer_hears_of_a_bank_refund_when_it_is_confirmed_not_before(monkeypatch, caplog):
    order, headers = _paid_order("bank-refund-email")
    intent = f"pi_fake_{order['id']}"
    issued = iter(("re_bank_1", "re_bank_2"))
    monkeypatch.setattr(
        stripe_gateway, "refund",
        lambda pi, cents, sequence=0: {"id": next(issued), "status": "pending"},
    )
    number = order["order_number"]

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        assert _refund(order["id"], amount=4.0).status_code == 200
    assert f"Refund sent — order {number}" not in caplog.text  # asked for is not sent

    # Stripe's own word on that refund arrives
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        _stripe_event("refund.updated", {"id": "re_bank_1", "status": "succeeded"})
        _stripe_event("refund.updated", {"id": "re_bank_1", "status": "succeeded"})  # and again
    assert caplog.text.count(f"Refund sent — order {number}") == 1
    assert "€4.00" in caplog.text

    # a second one, confirmed only by the charge's running total
    assert _refund(order["id"], amount=2.0).status_code == 200
    charge = {"payment_intent": intent, "amount": _cents(order["total"]), "amount_refunded": 600}
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        _stripe_event("charge.refunded", charge)
        _stripe_event("charge.refunded", charge)
    assert caplog.text.count(f"Refund sent — order {number}") == 1
    assert "€2.00" in caplog.text
    mine = _detail(order["id"], headers)
    assert mine["refunded_amount"] == 6.0
    # said for what it was — the shop's own refund confirmed, not one made at Stripe
    assert mine["timeline"][-1]["comment"] == "Stripe confirmed the refund — payment marked partially refunded"
    refunds = _payments(order["id"])["transactions"][0]["refunds"]
    assert [(r["status"], r["initiated_by"]) for r in refunds] == [
        ("succeeded", "Djini Rahman"), ("succeeded", "Djini Rahman"),
    ]


# --------------------------------------------------------------------------
# Disputes, once more
# --------------------------------------------------------------------------

def test_a_dispute_notice_repeated_without_an_id_is_still_one_dispute():
    order, headers = _paid_order("dispute-no-id")
    notice = {"payment_intent": f"pi_fake_{order['id']}", "reason": "general"}
    _stripe_event("charge.dispute.created", notice)
    _stripe_event("charge.dispute.created", notice)
    lines = [e for e in _detail(order["id"], headers)["timeline"] if "dispute" in e["comment"].lower()]
    assert len(lines) == 1


def test_a_dispute_on_a_duplicate_payment_never_relabels_the_order_itself():
    """The extra payment was sent straight back and keeps its own ledger
    row. Whatever a bank later does to THAT payment, the order's own money
    — a different payment — is still held, and the order must say so."""
    headers = _customer("dispute-on-extra")
    order = _place(headers)
    first = last_session_id(order["id"])
    client.post(f"/api/payments/{order['id']}/session", headers=headers)
    second = last_session_id(order["id"])
    for session_id, intent in ((second, "pi_dx_kept"), (first, "pi_dx_extra")):
        fake_sessions[session_id].update(status="complete", payment_status="paid", payment_intent=intent)
        _stripe_event(
            "checkout.session.completed",
            dict(fake_sessions[session_id], metadata={"order_id": str(order["id"])},
                 amount_total=_cents(order["total"])),
        )

    _stripe_event("charge.dispute.created", {"payment_intent": "pi_dx_extra"})
    _stripe_event(
        "charge.dispute.closed",
        {"id": "dp_extra", "payment_intent": "pi_dx_extra", "status": "lost", "amount": _cents(order["total"])},
    )

    book = _payments(order["id"])
    rows = {tx["provider_payment_id"]: tx for tx in book["transactions"]}
    assert book["payment_status"] == "paid"  # the order's own payment stands
    assert rows["pi_dx_kept"]["status"] == "succeeded"
    assert (rows["pi_dx_extra"]["status"], rows["pi_dx_extra"]["dispute_status"]) == ("refunded", "lost")
    assert _detail(order["id"], headers)["payment_status"] == "paid"


def test_the_bookkeeping_export_can_be_checked_against_the_providers_own_reports():
    import csv
    import io

    order, headers = _paid_order("export-row")
    assert _refund(order["id"], amount=2.5).status_code == 200
    cash = _place(headers, payment_method="cash_on_delivery")

    response = client.get("/api/admin/orders/export", headers=admin_headers())
    header, *rows = list(csv.reader(io.StringIO(response.text)))
    by_number = {row[0]: dict(zip(header, row)) for row in rows}

    paid = by_number[order["order_number"]]
    assert float(paid["refunded"]) == 2.5 and float(paid["total"]) == order["total"]
    # the provider's own id for the money — what its payout report lists
    assert (paid["payment_provider"], paid["payment_reference"]) == ("stripe", f"pi_fake_{order['id']}")
    assert paid["payment_status"] == "partially_refunded"

    door = by_number[cash["order_number"]]
    assert (float(door["refunded"]), door["payment_provider"], door["payment_reference"]) == (0.0, "", "")
    assert header[-1] == "currency"  # still the last word on every money column


def test_a_customers_own_data_export_includes_how_their_orders_were_paid():
    order, headers = _paid_order("my-data")
    assert _refund(order["id"], amount=1.5, reason="internal note about the box").status_code == 200

    export = client.get("/api/auth/me/export", headers=headers).json()
    (mine,) = [o for o in export["orders"] if o["order_number"] == order["order_number"]]
    (payment,) = mine["payments"]
    assert (payment["provider"], payment["method"], payment["state"]) == ("stripe", "Card", "partially_refunded")
    assert payment["amount"] == order["total"] and payment["refunded_amount"] == 1.5
    assert payment["provider_reference"] == f"pi_fake_{order['id']}"
    assert [(r["amount"], r["status"]) for r in payment["refunds"]] == [(1.5, "succeeded")]
    # the desk's own note and name are the shop's working record, not part of the download
    assert "internal note" not in str(export) and "Djini Rahman" not in str(export)


def test_closing_an_account_forgets_its_checkout_presses_and_keeps_the_books():
    headers = _customer("closing-account")
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    placed = client.post(
        "/api/orders", json=checkout_payload(), headers={**headers, "Idempotency-Key": "press-closing-0001"}
    )
    assert placed.status_code == 201
    pay_order_via_webhook(placed.json()["id"])

    closed = client.post("/api/auth/me/delete", json={"password": "Ledger123!"}, headers=headers)
    assert closed.status_code == 200, closed.text

    db = SessionLocal()
    try:
        assert db.scalars(
            select(CheckoutAttempt).where(CheckoutAttempt.idempotency_key == "press-closing-0001")
        ).all() == []
    finally:
        db.close()
    # the order's payment record — an invoice's other half — is still on the book
    (tx,) = _payments(placed.json()["id"])["transactions"]
    assert tx["status"] == "succeeded"


def test_pay_page_actions_are_braked_per_account(monkeypatch):
    """Every "complete payment" and every "check again" is a call to the
    provider. One account gets a generous handful a minute, not a flood."""
    from app import SlidingWindowLimiter

    monkeypatch.setitem(client.app.state.limiters, "payment_actions", SlidingWindowLimiter(2))
    headers = _customer("pay-flood")
    order = _place(headers)

    codes = [client.post(f"/api/payments/confirm/{order['id']}", headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.post(f"/api/payments/{order['id']}/session", headers=headers).status_code == 429
    # a paid order has nothing to ask the provider — reading it is never braked
    pay_order_via_webhook(order["id"])
    assert client.post(f"/api/payments/confirm/{order['id']}", headers=headers).status_code == 200
