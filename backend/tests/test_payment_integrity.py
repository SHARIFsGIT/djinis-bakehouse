"""Money that lands where the book isn't looking.

Every test here pins one way a real Stripe payment used to slip past the
order book — found in the 2026-11-22 audit, each reproduced against the old
code before it was fixed:

* a payment completing on an order that had already been cancelled was
  recorded as "paid" on a box nobody would bake — no refund, no alert;
* a fresh pay link left the old one live, so both could be paid and the
  second charge vanished without a trace;
* the desk's status dial could overwrite a cancellation that landed a
  heartbeat earlier.

The suite rides on test_api.py's client and its Stripe fake (imported, so
the app boots once whichever file pytest reaches first). The fake's
expire_session behaves like Stripe's: only an open session can be closed.
"""

import logging

import pytest

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


def _customer(name: str) -> dict:
    """A fresh shopper per test — nothing here leans on another test's book."""
    token = api.register_and_login(f"{name}@payments.example.com", "Integrity1!")
    return auth_header(token)


def _place_card_order(headers: dict) -> dict:
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    response = client.post("/api/orders", json=checkout_payload(), headers=headers)
    assert response.status_code == 201
    return response.json()


def _deliver_paid(order_id: int, session_id: str, intent: str, **extra) -> None:
    """Stripe's signed word that one particular session was paid."""
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=intent
    )
    session = dict(fake_sessions[session_id], metadata={"order_id": str(order_id)}, **extra)
    response = client.post(
        "/api/payments/webhook",
        json={"type": "checkout.session.completed", "data": {"object": session}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert response.status_code == 200


def _refunds_for(intent: str) -> list[tuple]:
    return [c for c in gateway_calls if c[0] == "refund" and c[1] == intent]


def _detail(order_id: int, headers: dict) -> dict:
    return client.get(f"/api/orders/{order_id}", headers=headers).json()


def _desk_titles() -> list[str]:
    listing = client.get(
        "/api/admin/notifications", params={"limit": 50}, headers=admin_headers()
    ).json()
    return [item["title"] for item in listing["items"]]


@pytest.fixture
def late_fee():
    """Tune the late-cancellation percent for one test, then put the desk's
    own setting back."""
    admin = admin_headers()
    before = client.get("/api/admin/settings", headers=admin).json()["late_cancel_fee_percent"]

    def tune(percent: float) -> None:
        saved = client.put(
            "/api/admin/settings", json={"late_cancel_fee_percent": percent}, headers=admin
        )
        assert saved.status_code == 200

    yield tune
    tune(before)


# --------------------------------------------------------------------------
# A cancelled order must not keep a live pay page
# --------------------------------------------------------------------------

def test_cancelling_an_unpaid_card_order_closes_its_pay_page():
    headers = _customer("closes-page")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    assert fake_sessions[session_id]["status"] == "open"

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["payment_status"] == "pending"  # nothing was collected

    assert ("expire_session", session_id) in gateway_calls
    assert fake_sessions[session_id]["status"] == "expired"  # nobody can pay it now


def test_the_desks_cancel_dial_closes_the_pay_page_too():
    headers = _customer("desk-closes-page")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])

    dial = client.put(
        f"/api/admin/orders/{order['id']}/status",
        json={"status": "cancelled", "notify": False},
        headers=admin_headers(),
    )
    assert dial.status_code == 200
    assert fake_sessions[session_id]["status"] == "expired"


def test_a_lapsed_session_is_not_asked_to_close_again():
    """The expiry webhook means the session is already over — the
    cancellation it triggers must not call Stripe to close it."""
    headers = _customer("already-lapsed")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])

    api.expire_order_via_webhook(order["id"])

    assert _detail(order["id"], headers)["status"] == "cancelled"
    assert ("expire_session", session_id) not in gateway_calls


def test_a_paid_order_being_cancelled_has_no_pay_page_to_close():
    headers = _customer("paid-then-cancelled")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    pay_order_via_webhook(order["id"])

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()

    assert cancelled["payment_status"] == "refunded"
    assert ("expire_session", session_id) not in gateway_calls


# --------------------------------------------------------------------------
# Money that lands after the cancellation goes straight back
# --------------------------------------------------------------------------

def test_payment_landing_on_a_cancelled_order_is_refunded_not_kept(caplog):
    """The audit's reproduction: pay page open in another tab, order
    cancelled, payment completes. The old book said "cancelled · paid" and
    emailed a receipt; the money stayed."""
    headers = _customer("late-payer")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    intent = f"pi_late_{order['id']}"

    # Paid at Stripe a heartbeat before the cancel click — too late to
    # close the page, and the webhook is still on the wire.
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=intent
    )
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["payment_status"] == "pending"

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        _deliver_paid(order["id"], session_id, intent)

    detail = _detail(order["id"], headers)
    assert detail["status"] == "cancelled"
    assert detail["payment_status"] == "refunded"  # never "paid" on a dead order
    assert _refunds_for(intent) == [("refund", intent, int(round(order["total"] * 100)))]
    assert (
        detail["timeline"][-1]["comment"]
        == "Payment arrived after the order was cancelled — refunded in full automatically"
    )
    assert f"Late payment on cancelled order {order['order_number']} — refunded automatically" in _desk_titles()

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {order['order_number']}" not in logged  # nothing to receipt
    assert "sent back to your card" in logged  # the customer hears where the money went

    # Stripe retries its events — the same word again changes nothing.
    events_before = len(detail["timeline"])
    _deliver_paid(order["id"], session_id, intent)
    replayed = _detail(order["id"], headers)
    assert replayed["payment_status"] == "refunded"
    assert len(replayed["timeline"]) == events_before
    assert len(_refunds_for(intent)) == 1


def test_a_late_payment_whose_refund_fails_is_reported_not_papered_over(caplog, monkeypatch):
    headers = _customer("late-payer-stripe-down")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    intent = f"pi_late_{order['id']}"
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=intent
    )
    client.post(f"/api/orders/{order['id']}/cancel", headers=headers)

    def broken_refund(payment_intent, amount_cents):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        _deliver_paid(order["id"], session_id, intent)

    detail = _detail(order["id"], headers)
    assert detail["status"] == "cancelled"
    assert detail["payment_status"] == "paid"  # the shop holds the money — say so
    assert "refund could not be sent" in detail["timeline"][-1]["comment"]
    assert f"Refund needed — payment landed on cancelled order {order['order_number']}" in _desk_titles()
    # no email may claim a refund that has not happened
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "sent back to your card" not in logged
    assert f"Receipt {order['order_number']}" not in logged


def test_a_late_payment_keeps_only_the_fee_the_cancellation_recorded(late_fee):
    """Cancelled after the ovens started (the fee is on the order), then
    the payment lands: the customer gets back the total less that fee —
    the same settlement as if the money had arrived first."""
    late_fee(25)
    admin = admin_headers()
    headers = _customer("late-payer-with-fee")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    intent = f"pi_late_{order['id']}"
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=intent
    )

    baking = client.put(
        f"/api/admin/orders/{order['id']}/status",
        json={"status": "baking", "notify": False},
        headers=admin,
    )
    assert baking.status_code == 200
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    fee = round(order["total"] * 0.25, 2)
    assert cancelled["cancel_fee"] == fee

    _deliver_paid(order["id"], session_id, intent)

    detail = _detail(order["id"], headers)
    assert detail["payment_status"] == "partially_refunded"
    expected = int(round(order["total"] * 100)) - int(round(fee * 100))
    assert _refunds_for(intent) == [("refund", intent, expected)]
    assert "less the late-cancellation fee" in detail["timeline"][-1]["comment"]


def test_confirm_sends_back_a_payment_found_on_a_cancelled_order():
    """The webhook is not the only door: a customer landing back on the
    order page (which asks Stripe directly) must trigger the same refund
    even if no webhook ever arrives."""
    headers = _customer("late-payer-no-webhook")
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    intent = f"pi_late_{order['id']}"
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=intent
    )
    client.post(f"/api/orders/{order['id']}/cancel", headers=headers)

    confirmed = client.post(f"/api/payments/confirm/{order['id']}", headers=headers)

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "cancelled"
    assert confirmed.json()["payment_status"] == "refunded"
    assert _refunds_for(intent) == [("refund", intent, int(round(order["total"] * 100)))]

    # and the webhook arriving afterwards finds it settled
    _deliver_paid(order["id"], session_id, intent)
    assert len(_refunds_for(intent)) == 1


def test_confirm_on_a_cancelled_unpaid_order_changes_nothing():
    headers = _customer("cancelled-never-paid")
    order = _place_card_order(headers)
    client.post(f"/api/orders/{order['id']}/cancel", headers=headers)
    refunds_before = len([c for c in gateway_calls if c[0] == "refund"])

    confirmed = client.post(f"/api/payments/confirm/{order['id']}", headers=headers)

    assert confirmed.status_code == 200
    assert confirmed.json()["payment_status"] == "pending"
    assert len([c for c in gateway_calls if c[0] == "refund"]) == refunds_before


# --------------------------------------------------------------------------
# One order, one live pay page — and never two charges
# --------------------------------------------------------------------------

def test_a_fresh_pay_link_closes_the_one_it_replaces():
    headers = _customer("fresh-link")
    order = _place_card_order(headers)
    first = last_session_id(order["id"])

    fresh = client.post(f"/api/payments/{order['id']}/session", headers=headers)

    assert fresh.status_code == 201
    second = last_session_id(order["id"])
    assert second != first
    assert fresh.json()["payment_url"].endswith(second)
    assert fake_sessions[first]["status"] == "expired"  # the old page is shut
    assert fake_sessions[second]["status"] == "open"
    # closed only after the fresh link was on the order — the old session's
    # expiry event must find itself superseded, never cancel the order
    api.client.post(
        "/api/payments/webhook",
        json={
            "type": "checkout.session.expired",
            "data": {"object": {"id": first, "metadata": {"order_id": str(order["id"])}}},
        },
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert _detail(order["id"], headers)["status"] == "pending"


def test_no_fresh_link_is_minted_for_an_order_just_paid_on_the_old_one(caplog):
    """Paid on the first link, webhook still on the wire, customer taps
    "Complete payment" again: the order is landed as paid and handed back
    with nothing to pay — a second link would invite a second charge."""
    headers = _customer("paid-on-old-link")
    order = _place_card_order(headers)
    first = last_session_id(order["id"])
    intent = f"pi_first_{order['id']}"
    fake_sessions[first].update(status="complete", payment_status="paid", payment_intent=intent)
    sessions_before = len(fake_sessions)

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        answer = client.post(f"/api/payments/{order['id']}/session", headers=headers)

    assert answer.status_code == 200  # nothing was created
    body = answer.json()
    assert body["payment_status"] == "paid"
    assert body["payment_url"] is None
    assert len(fake_sessions) == sessions_before
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert logged.count(f"Receipt {order['order_number']}") == 1

    # the webhook arriving afterwards is the same payment — a no-op
    _deliver_paid(order["id"], first, intent)
    detail = _detail(order["id"], headers)
    assert len([e for e in detail["timeline"] if e["comment"] == "Payment received — paid by card"]) == 1
    assert _refunds_for(intent) == []


def test_a_second_payment_on_a_superseded_link_is_refunded():
    """The audit's reproduction: two pay links, both paid. The old book
    recorded one charge and never mentioned the other."""
    headers = _customer("paid-twice")
    order = _place_card_order(headers)
    first = last_session_id(order["id"])
    client.post(f"/api/payments/{order['id']}/session", headers=headers)
    second = last_session_id(order["id"])
    total_cents = int(round(order["total"] * 100))

    _deliver_paid(order["id"], second, "pi_kept")
    assert _detail(order["id"], headers)["payment_status"] == "paid"

    # ...and the old tab's payment got through before Stripe closed it
    _deliver_paid(order["id"], first, "pi_extra", amount_total=total_cents)

    detail = _detail(order["id"], headers)
    assert detail["payment_status"] == "paid"  # the order's own payment stands
    assert _refunds_for("pi_extra") == [("refund", "pi_extra", total_cents)]
    assert _refunds_for("pi_kept") == []
    assert (
        detail["timeline"][-1]["comment"]
        == "A second payment arrived for this order (pi_extra) — refunded in full automatically"
    )
    assert f"Order {order['order_number']} was charged twice — extra payment refunded" in _desk_titles()

    # replayed by Stripe: one refund, one line — never two
    _deliver_paid(order["id"], first, "pi_extra", amount_total=total_cents)
    assert len(_refunds_for("pi_extra")) == 1
    assert len(_detail(order["id"], headers)["timeline"]) == len(detail["timeline"])

    # the kept payment is still the one a cancellation refunds
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["payment_status"] == "refunded"
    assert _refunds_for("pi_kept") == [("refund", "pi_kept", total_cents)]


def test_a_second_payment_whose_refund_fails_rings_the_desk(monkeypatch):
    headers = _customer("paid-twice-stripe-down")
    order = _place_card_order(headers)
    first = last_session_id(order["id"])
    client.post(f"/api/payments/{order['id']}/session", headers=headers)
    second = last_session_id(order["id"])
    _deliver_paid(order["id"], second, "pi_kept_2")

    def broken_refund(payment_intent, amount_cents):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    _deliver_paid(order["id"], first, "pi_extra_2")

    detail = _detail(order["id"], headers)
    assert "(pi_extra_2) — refund could not be sent" in detail["timeline"][-1]["comment"]
    assert f"Refund needed — order {order['order_number']} was charged twice" in _desk_titles()


def test_a_card_payment_on_an_order_marked_paid_by_hand_keeps_its_trail():
    """The desk marked the order paid before any card money landed; the
    Stripe payment that then arrives is the first one Stripe actually
    holds. It is recorded (so a later cancellation can refund it), never
    auto-refunded, and a person is asked to look."""
    headers = _customer("marked-by-hand")
    admin = admin_headers()
    order = _place_card_order(headers)
    session_id = last_session_id(order["id"])
    marked = client.put(
        f"/api/admin/orders/{order['id']}/payment", json={"payment_status": "paid"}, headers=admin
    )
    assert marked.status_code == 200

    _deliver_paid(order["id"], session_id, "pi_by_card")

    detail = _detail(order["id"], headers)
    assert detail["payment_status"] == "paid"
    assert _refunds_for("pi_by_card") == []
    assert "Card payment received (pi_by_card)" in detail["timeline"][-1]["comment"]
    assert f"Card payment on order {order['order_number']}, already marked paid" in _desk_titles()

    # the trail is real: cancelling now refunds that very payment
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["payment_status"] == "refunded"
    assert _refunds_for("pi_by_card") == [("refund", "pi_by_card", int(round(order["total"] * 100)))]


# --------------------------------------------------------------------------
# The desk's dial and the cancellation it must not overwrite
# --------------------------------------------------------------------------

def test_the_status_dial_judges_a_freshly_locked_order(monkeypatch):
    """A customer's cancellation commits between the desk loading the order
    and writing "baking". The dial must re-read under the lock and refuse —
    the old code wrote straight over the cancellation, leaving a refunded,
    restocked order on the bake list. (Sequential on SQLite: this pins the
    re-read and the guard; the FOR UPDATE wait itself is Postgres's.)"""
    from app.routers import admin as admin_router

    headers = _customer("cancels-under-the-dial")
    admin = admin_headers()
    order = _place_card_order(headers)
    pay_order_via_webhook(order["id"])
    real_lock = admin_router._lock_for_cancellation

    def cancellation_lands_then_lock(loaded, db):
        landed = client.post(f"/api/orders/{loaded.id}/cancel", headers=headers)
        assert landed.status_code == 200
        return real_lock(loaded, db)

    monkeypatch.setattr(admin_router, "_lock_for_cancellation", cancellation_lands_then_lock)
    dial = client.put(
        f"/api/admin/orders/{order['id']}/status",
        json={"status": "baking", "notify": False},
        headers=admin,
    )

    assert dial.status_code == 400
    assert "cannot be reopened" in dial.json()["detail"]
    detail = _detail(order["id"], headers)
    assert detail["status"] == "cancelled"
    assert detail["payment_status"] == "refunded"


# --------------------------------------------------------------------------
# A fee that is the whole total
# --------------------------------------------------------------------------

def test_a_full_late_fee_keeps_the_payment_without_a_zero_refund(late_fee):
    """With the late-cancellation fee set to 100 % nothing is owed back.
    The old code still asked Stripe for a 0-cent refund, was refused, and
    told the timeline a refund "could not be sent"."""
    late_fee(100)
    admin = admin_headers()
    headers = _customer("full-fee")
    order = _place_card_order(headers)
    pay_order_via_webhook(order["id"])
    intent = f"pi_fake_{order['id']}"
    client.put(
        f"/api/admin/orders/{order['id']}/status",
        json={"status": "baking", "notify": False},
        headers=admin,
    )

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_fee"] == order["total"]
    assert cancelled["payment_status"] == "paid"  # kept in full — nothing was refunded
    assert _refunds_for(intent) == []
    assert "late-cancellation fee" in cancelled["timeline"][-1]["comment"]
    assert "refund could not be sent" not in cancelled["timeline"][-1]["comment"]
