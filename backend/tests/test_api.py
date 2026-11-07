"""End-to-end API tests covering the main customer and admin flows."""

import os

os.environ["DATABASE_URL"] = "sqlite:///./test_bakehouse.db"
# The developer's backend/.env must not leak in here — the shared fixture
# database expects the dev-style demo dataset, whatever the local .env says
# (SEED_DEMO_DATA=false there since the fresh-start wipe).
os.environ["SEED_DEMO_DATA"] = "true"
# The whole suite shares one TestClient IP — relax the auth rate limits so
# repeated logins between tests are not throttled.
os.environ["LOGIN_RATE_LIMIT"] = "1000"
os.environ["REGISTER_RATE_LIMIT"] = "1000"
os.environ["FORGOT_RATE_LIMIT"] = "1000"
os.environ["OTP_RATE_LIMIT"] = "1000"
# The quote limiter is env-tuned like the auth brakes; the suite fires far
# more price checks in a minute than a shopper ever would.
os.environ["QUOTE_RATE_LIMIT"] = "1000"
# Card checkout runs through Stripe — the suite swaps the whole gateway for
# a fake (below), but the key must exist so checkout considers it configured.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_gateway"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"
# Approved-for-tests EUR money rules — deliberately NOT the old USD numerals
# (8.75% / $5.99 / $50) so no test can pass on relabelled dollars: a 5% tax
# rate, €3.50 delivery, free delivery from €30. Production ships with all
# three unset (checkout paused fail-closed) until the owner approves real
# EUR figures; VIP_SPEND_THRESHOLD stays unset here too, so the loyalty
# tiers in these tests ride on order counts alone.
os.environ["TAX_RATE"] = "0.05"
os.environ["SHIPPING_FEE"] = "3.50"
os.environ["FREE_SHIPPING_THRESHOLD"] = "30.00"
# The VIP regular's smaller free-delivery line (owner-approved €15; the
# suite's standard threshold above is €30, so the VIP window is 15–29.99).
os.environ["VIP_FREE_DELIVERY_THRESHOLD"] = "15.00"

from fastapi.testclient import TestClient  # noqa: E402

from app import create_app, stripe_gateway  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import (  # noqa: E402
    DEFAULT_ANNOUNCEMENT,
    DEFAULT_HERO_IMAGE,
    DEFAULT_TODAY_NOTE,
)


def remove_test_db():
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"test_bakehouse.db{suffix}")
        except FileNotFoundError:
            pass
        except PermissionError:
            # A sister module (test_marketing.py) imported first holds the
            # file open on Windows — and its boot already deleted the stale
            # database and created a fresh one, which is all this removal
            # exists to guarantee. Nothing to do.
            pass


remove_test_db()

# __enter__ keeps the app lifespan (table creation + seed) alive for the
# whole test session instead of just one request.
client = TestClient(create_app())
client.__enter__()

# --------------------------------------------------------------------------
# The Stripe fake — no test ever talks to the real gateway
# --------------------------------------------------------------------------

# Everything the fake gateway was asked to do, for refund assertions.
gateway_calls: list[tuple] = []
# Live session state, shaped like Stripe's answer: session id -> dict.
fake_sessions: dict[str, dict] = {}


def fake_create_checkout_session(order, base_url):
    session_id = f"cs_fake_{order.id}_{len(gateway_calls)}"
    gateway_calls.append(("create_session", order.id, float(order.total)))
    fake_sessions[session_id] = {
        "id": session_id,
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": None,
    }
    return {"id": session_id, "url": f"https://checkout.stripe.test/{session_id}"}


def fake_retrieve_session(session_id):
    session = fake_sessions.get(session_id)
    if session is None:
        raise stripe_gateway.GatewayUnavailable("unknown session")
    return dict(session)


def fake_refund(payment_intent, amount_cents):
    gateway_calls.append(("refund", payment_intent, amount_cents))


def fake_verify_webhook(payload, signature):
    import json

    if not signature:
        raise stripe_gateway.BadSignature("missing signature header")
    return json.loads(payload)


stripe_gateway.create_checkout_session = fake_create_checkout_session
stripe_gateway.retrieve_session = fake_retrieve_session
stripe_gateway.refund = fake_refund
stripe_gateway.verify_webhook = fake_verify_webhook


def last_session_id(order_id: int) -> str:
    """The newest fake session created for an order."""
    ids = [sid for sid in fake_sessions if sid.startswith(f"cs_fake_{order_id}_")]
    assert ids, f"no fake session was created for order {order_id}"
    return sorted(ids, key=lambda s: int(s.rsplit("_", 1)[1]))[-1]


def pay_order_via_webhook(order_id: int) -> None:
    """Deliver Stripe's payment confirmation for an order's latest session."""
    session_id = last_session_id(order_id)
    fake_sessions[session_id].update(
        status="complete", payment_status="paid", payment_intent=f"pi_fake_{order_id}"
    )
    session = dict(fake_sessions[session_id], metadata={"order_id": str(order_id)})
    response = client.post(
        "/api/payments/webhook",
        json={"type": "checkout.session.completed", "data": {"object": session}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert response.status_code == 200


def expire_order_via_webhook(order_id: int) -> None:
    """Deliver the expiry event for an order's latest session."""
    session = {"id": last_session_id(order_id), "metadata": {"order_id": str(order_id)}}
    response = client.post(
        "/api/payments/webhook",
        json={"type": "checkout.session.expired", "data": {"object": session}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert response.status_code == 200


def fail_order_via_webhook(order_id: int) -> None:
    """Deliver Stripe's failed-delayed-payment event for an order's latest
    session (Klarna/SEPA money that never arrived)."""
    session = {"id": last_session_id(order_id), "metadata": {"order_id": str(order_id)}}
    response = client.post(
        "/api/payments/webhook",
        json={"type": "checkout.session.async_payment_failed", "data": {"object": session}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert response.status_code == 200


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(email: str, password: str) -> str:
    client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Test",
            "last_name": "User",
        },
    )
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


CUSTOMER = {"email": "shopper@example.com", "password": "Shopper123!"}


# --------------------------------------------------------------------------
# Health & catalog
# --------------------------------------------------------------------------

def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"


def test_seeded_catalog():
    categories = client.get("/api/products/categories").json()
    assert len(categories) == 3
    assert all("product_count" in c and "image_url" in c for c in categories)

    listing = client.get("/api/products", params={"per_page": 40, "sort": "name"}).json()
    assert listing["pagination"]["total_items"] == 25
    product = listing["products"][0]
    assert product["current_price"] > 0
    assert product["image_url"].startswith("/static/products/")


def test_product_filters_and_search():
    sale = client.get("/api/products", params={"on_sale": True}).json()
    assert all(p["is_on_sale"] for p in sale["products"])

    breads = client.get("/api/products", params={"category": "breads"}).json()
    assert all(p["category_slug"] == "breads" for p in breads["products"])

    search = client.get("/api/products", params={"search": "croissant"}).json()
    assert any("croissant" in p["name"].lower() for p in search["products"])

    priced = client.get(
        "/api/products", params={"min_price": 5, "max_price": 10, "sort": "price_asc"}
    ).json()
    assert priced["products"]
    assert all(5 <= p["current_price"] <= 10 for p in priced["products"])


def test_product_detail():
    detail = client.get("/api/products/butter-croissant")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "Butter Croissant"
    assert body["category_name"] == "Pastries"
    assert body["image_url"] == "/static/products/butter-croissant.jpg"
    assert isinstance(body["related"], list)

    assert client.get("/api/products/does-not-exist").status_code == 404


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def test_register_login_me():
    response = client.post(
        "/api/auth/register",
        json={
            "email": CUSTOMER["email"],
            "password": CUSTOMER["password"],
            "first_name": "Shopper",
            "last_name": "Smith",
        },
    )
    assert response.status_code == 201
    tokens = response.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    # Duplicate email rejected
    dup = client.post(
        "/api/auth/register",
        json={
            "email": CUSTOMER["email"],
            "password": "Whatever123",
            "first_name": "Shopper",
            "last_name": "Smith",
        },
    )
    assert dup.status_code == 409

    # Wrong password rejected
    bad = client.post(
        "/api/auth/login", json={"email": CUSTOMER["email"], "password": "WrongPass1"}
    )
    assert bad.status_code == 401

    me = client.get("/api/auth/me", headers=auth_header(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == CUSTOMER["email"]

    refreshed = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


def test_single_name_registration():
    """The signup form sends one "Your name" field — a lone name is fine."""
    response = client.post(
        "/api/auth/register",
        json={"email": "lone@example.com", "password": "Lone12345!", "first_name": "Cher"},
    )
    assert response.status_code == 201
    me = client.get("/api/auth/me", headers=auth_header(response.json()["access_token"])).json()
    assert me["first_name"] == "Cher"
    assert me["last_name"] == ""


def test_checkout_earns_a_receipt_email(caplog):
    """Every order emails a receipt as proof — until SMTP is set it lands in
    the application log with the full itemised body."""
    import logging

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        order = client.post(
            "/api/orders",
            json=checkout_payload(payment_method="cash_on_delivery"),
            headers=auth_header(login_customer()),
        ).json()

    assert order["payment_status"] == "pending"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {order['order_number']}" in logged
    assert f"order {order['order_number']}" in logged
    assert "Cash on delivery — pay the courier at your door" in logged
    assert "Total" in logged


def test_password_change_revokes_old_tokens():
    email, old_password = "rotate@example.com", "Rotate123!"
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": old_password, "first_name": "Rot", "last_name": "Ate"},
    ).json()
    old_access, old_refresh = registered["access_token"], registered["refresh_token"]

    changed = client.put(
        "/api/auth/me/password",
        json={"current_password": old_password, "new_password": "Rotate456!"},
        headers=auth_header(old_access),
    )
    assert changed.status_code == 200
    # This device stays signed in — fresh tokens come back with the response.
    new_tokens = changed.json()
    assert new_tokens["access_token"] and new_tokens["refresh_token"]

    # The old access token and refresh token are both dead now.
    assert client.get("/api/auth/me", headers=auth_header(old_access)).status_code == 401
    stale_refresh = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert stale_refresh.status_code == 401

    # The new pair works, and the new password is the one that logs in.
    assert client.get("/api/auth/me", headers=auth_header(new_tokens["access_token"])).status_code == 200
    assert client.post("/api/auth/login", json={"email": email, "password": "Rotate456!"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": email, "password": old_password}).status_code == 401


def test_password_reset_flow():
    email, old_password, new_password = (
        "forgetful@example.com",
        "Original123!",
        "Replacement456!",
    )
    registered = client.post(
        "/api/auth/register",
        json={"email": email, "password": old_password, "first_name": "For", "last_name": "Get"},
    ).json()
    old_access = registered["access_token"]

    # Unknown and known emails get the same answer — no account probing.
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    known = client.post("/api/auth/forgot-password", json={"email": email})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json()["message"] == known.json()["message"]
    assert "reset_link" not in unknown.json()

    # Tests run in dev, which hands the link back instead of emailing it.
    link = known.json().get("reset_link", "")
    assert link and "/reset-password?token=" in link
    token = link.split("token=")[1]

    # A made-up token is refused; the real one sets the new password.
    bad = client.post(
        "/api/auth/reset-password", json={"token": "x" * 43, "password": new_password}
    )
    assert bad.status_code == 400

    done = client.post(
        "/api/auth/reset-password", json={"token": token, "password": new_password}
    )
    assert done.status_code == 200

    # Old password is dead, new one logs in, and the pre-reset token died too.
    assert client.post("/api/auth/login", json={"email": email, "password": old_password}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": new_password}).status_code == 200
    assert client.get("/api/auth/me", headers=auth_header(old_access)).status_code == 401

    # Tokens are single-use: redeeming it a second time is refused.
    again = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "Another789!"}
    )
    assert again.status_code == 400


def test_phone_login_flow(monkeypatch, caplog):
    """Phone sign-in: texted one-shot codes, attempt counting, and numbers
    that always store the one way they're compared."""
    import logging

    from app.routers import auth as auth_router

    email, password = "texter@example.com", "Texter123!"
    registered = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Tess",
            "last_name": "Ting",
            "phone": "+49 176 555 0144",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["user"]["phone"] == "+491765550144"  # normalized on the way in

    # A second account can't claim the same number, however it's spelled.
    clash = client.post(
        "/api/auth/register",
        json={
            "email": "copycat@example.com",
            "password": "Copycat123!",
            "first_name": "Copy",
            "phone": "+491765550144",
        },
    )
    assert clash.status_code == 409

    # The dev store shows the phone rail (codes come back with the response).
    assert client.get("/api/store").json()["phone_login"] is True

    # Unknown and known numbers get the same answer — no account probing.
    with caplog.at_level(logging.INFO, logger="bakery.sms"):
        unknown = client.post("/api/auth/phone/request", json={"phone": "+49 176 555 0999"})
        known = client.post("/api/auth/phone/request", json={"phone": "+491765550144"})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json()["message"] == known.json()["message"]
    assert "dev_code" not in unknown.json()
    code = known.json().get("dev_code")
    assert code and "sms to +491765550144" in " ".join(caplog.messages)

    # An immediate repeat is quietly throttled — one text a minute per number.
    assert "dev_code" not in client.post(
        "/api/auth/phone/request", json={"phone": "+491765550144"}
    ).json()

    # A differently-spelled number with the + still finds the account;
    # without the + it is simply not the same number.
    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)  # past the throttle
    code = client.post(
        "/api/auth/phone/request", json={"phone": "+49 (176) 555-0144"}
    ).json()["dev_code"]
    assert code
    assert "dev_code" not in client.post(
        "/api/auth/phone/request", json={"phone": "01765550144"}
    ).json()

    # Wrong codes are refused — the right one signs in, once.
    assert client.post(
        "/api/auth/phone/verify", json={"phone": "+491765550144", "code": "000000"}
    ).status_code == 400
    verified = client.post("/api/auth/phone/verify", json={"phone": "+491765550144", "code": code})
    assert verified.status_code == 200
    assert client.get("/api/auth/me", headers=auth_header(verified.json()["access_token"])).status_code == 200
    assert client.post(
        "/api/auth/phone/verify", json={"phone": "+491765550144", "code": code}
    ).status_code == 400  # the code spent itself

    # The password still works — a texted code is not a password change.
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200

    # Five wrong tries burn a code; a fresh one signs straight back in.
    second = client.post("/api/auth/phone/request", json={"phone": "+491765550144"}).json()
    for guess in ("111111", "222222", "333333", "444444", "555555"):
        refused = client.post(
            "/api/auth/phone/verify", json={"phone": "+491765550144", "code": guess}
        )
        assert refused.status_code == 400
    assert client.post(
        "/api/auth/phone/verify", json={"phone": "+491765550144", "code": second["dev_code"]}
    ).status_code == 400  # burned — even the right code no longer works
    third = client.post("/api/auth/phone/request", json={"phone": "+491765550144"}).json()
    assert client.post(
        "/api/auth/phone/verify", json={"phone": "+491765550144", "code": third["dev_code"]}
    ).status_code == 200

    # Taking a number that belongs to another account is refused in the profile.
    other_token = register_and_login("other@example.com", "Other123!")
    claimed = client.put(
        "/api/auth/me",
        headers=auth_header(other_token),
        json={"phone": "+49 176 555 0144"},
    )
    assert claimed.status_code == 400


def test_facebook_login_flow(monkeypatch):
    """Facebook sign-in: dialog start, callback handshake, one-time exchange."""
    import urllib.parse

    from app import facebook_gateway

    # One fake handshake covers all the profiles the flow needs.
    facebook_profiles = {
        "code-falk": {"id": "fb-777", "name": "Falk Book", "email": "falk@example.com"},
        "code-nomail": {"id": "fb-888", "name": "No Mail"},
        "code-link": {"id": "fb-999", "name": "Link Ed", "email": "linked@example.com"},
    }

    def fake_exchange_code(code, redirect_uri):
        assert code in facebook_profiles
        assert redirect_uri.endswith("/api/auth/facebook/callback")
        return f"token-{code}"

    def fake_profile(access_token):
        return dict(facebook_profiles[access_token[len("token-"):]])

    monkeypatch.setattr(facebook_gateway, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(facebook_gateway, "fetch_profile", fake_profile)

    # The rail is hidden until the app is configured — never a dead button.
    monkeypatch.setattr(facebook_gateway, "configured", lambda: False)
    assert client.get("/api/store").json()["facebook_login"] is False
    monkeypatch.setattr(facebook_gateway, "configured", lambda: True)

    def start_state() -> str:
        url = client.get("/api/auth/facebook/start").json()["url"]
        assert "facebook.com" in url and "dialog/oauth" in url
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

    def callback(code, state):
        return client.get(
            "/api/auth/facebook/callback",
            params={"code": code, "state": state},
            follow_redirects=False,
        )

    # A declined dialog and a forged state are turned away at the door.
    declined = client.get(
        "/api/auth/facebook/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert declined.status_code == 307 and "error=facebook_declined" in declined.headers["location"]
    forged = callback("code-falk", "x" * 40)
    assert forged.status_code == 307 and "error=facebook_state" in forged.headers["location"]
    nomail = callback("code-nomail", start_state())
    assert nomail.status_code == 307 and "error=facebook_no_email" in nomail.headers["location"]

    def exchange_code_from(response) -> str:
        assert response.status_code == 307
        return urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).query)[
            "code"
        ][0]

    # The first handshake creates the account; the code buys one session.
    one_time = exchange_code_from(callback("code-falk", start_state()))
    session = client.post("/api/auth/exchange", json={"code": one_time})
    assert session.status_code == 200
    me = client.get("/api/auth/me", headers=auth_header(session.json()["access_token"])).json()
    assert me["email"] == "falk@example.com" and me["first_name"] == "Falk"
    assert client.post("/api/auth/exchange", json={"code": one_time}).status_code == 400  # single use

    # Signing in again lands on the same account, with a fresh one-time code.
    again = client.post("/api/auth/exchange", json={"code": exchange_code_from(callback("code-falk", start_state()))})
    assert again.status_code == 200
    assert again.json()["user"]["email"] == "falk@example.com"

    # An existing email is linked, not duplicated — the customer keeps their history.
    register_and_login("linked@example.com", "Linked123!")
    linked = client.post("/api/auth/exchange", json={"code": exchange_code_from(callback("code-link", start_state()))})
    assert linked.status_code == 200
    assert linked.json()["user"]["email"] == "linked@example.com"
    # The linked customer's Facebook id is on their one account now, so the
    # email password still works alongside it.
    assert client.post(
        "/api/auth/login", json={"email": "linked@example.com", "password": "Linked123!"}
    ).status_code == 200


def test_addresses():
    token = register_and_login("addr@example.com", "Address123!")
    headers = auth_header(token)

    created = client.post(
        "/api/auth/addresses",
        json={
            "label": "Home",
            "recipient": "Addr User",
            "phone": "555-0100",
            "line1": "123 Baker Street",
            "city": "Sweet Valley",
            "state": "CA",
            "postal_code": "90210",
            "country": "USA",
            "is_default": True,
        },
        headers=headers,
    )
    assert created.status_code == 201
    address_id = created.json()["id"]

    listing = client.get("/api/auth/addresses", headers=headers)
    assert len(listing.json()) == 1
    assert listing.json()[0]["is_default"] is True

    updated = client.put(
        f"/api/auth/addresses/{address_id}", json={**created.json(), "city": "Los Angeles"},
        headers=headers,
    )
    assert updated.json()["city"] == "Los Angeles"

    assert client.delete(f"/api/auth/addresses/{address_id}", headers=headers).status_code == 200
    assert client.get("/api/auth/addresses", headers=headers).json() == []


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------

def test_reviews():
    token = register_and_login("reviewer@example.com", "Reviewer123!")
    headers = auth_header(token)
    product_id = get_product("fresh-bagels")["id"]  # no seeded reviews on this one

    created = client.post(
        f"/api/products/{product_id}/reviews",
        json={"rating": 5, "comment": "Best bagels in town!"},
        headers=headers,
    )
    assert created.status_code == 201

    # Updating own review edits in place (200), never a duplicate
    again = client.post(
        f"/api/products/{product_id}/reviews",
        json={"rating": 4, "comment": "Still great"},
        headers=headers,
    )
    assert again.status_code == 200

    detail = client.get("/api/products/fresh-bagels").json()
    assert detail["review_count"] == 1
    assert detail["average_rating"] == 4.0

    invalid = client.post(
        f"/api/products/{product_id}/reviews", json={"rating": 9}, headers=headers
    )
    assert invalid.status_code == 422


def test_review_verified_purchase_badge():
    """Only reviewers who actually received the bake get the badge."""
    def named_login(email: str, first_name: str) -> str:
        client.post(
            "/api/auth/register",
            json={"email": email, "password": "Bought123!", "first_name": first_name, "last_name": "User"},
        )
        return client.post("/api/auth/login", json={"email": email, "password": "Bought123!"}).json()["access_token"]

    buyer = named_login("bought@example.com", "Bought")
    bystander = named_login("bystander@example.com", "Bystander")
    product = get_product("beef-curry-puff")

    order = client.post(
        "/api/orders",
        json=checkout_payload(items=[{"product_id": product["id"], "quantity": 1}]),
        headers=auth_header(buyer),
    )
    assert order.status_code == 201

    for token, rating in ((buyer, 5), (bystander, 4)):
        created = client.post(
            f"/api/products/{product['id']}/reviews",
            json={"rating": rating, "comment": "Crumb report"},
            headers=auth_header(token),
        )
        assert created.status_code == 201

    detail = client.get(f"/api/products/{product['slug']}").json()
    badges = {r["author_name"]: r["verified_purchase"] for r in detail["reviews"]}
    assert badges["Bought User"] is True
    assert badges["Bystander User"] is False


def test_review_moderation():
    """The baker can take a review off the wall without losing it: the
    public wall and the averages drop it, the author keeps their copy."""
    headers = admin_headers()
    mine = auth_header(register_and_login("moderated@example.com", "Moderated123!"))
    product = get_product("croffle")  # carries two seeded reviews

    seeded = client.get(f"/api/products/{product['slug']}").json()
    seeded_count, seeded_avg = seeded["review_count"], seeded["average_rating"]

    created = client.post(
        f"/api/products/{product['id']}/reviews",
        json={"rating": 1, "comment": "Not my favorite"},
        headers=mine,
    )
    assert created.status_code == 201
    assert created.json()["is_approved"] is True
    review_id = created.json()["id"]

    desk = client.get("/api/admin/reviews", params={"status": "live"}, headers=headers).json()
    row = next(r for r in desk["items"] if r["id"] == review_id)
    assert row["author_email"] == "moderated@example.com"
    assert row["product_slug"] == "croffle"
    assert row["product_image"] == product["image_url"]
    assert desk["counts"]["all"] == desk["counts"]["live"] + desk["counts"]["hidden"]
    live_before = desk["counts"]["live"]

    hidden = client.put(
        f"/api/admin/reviews/{review_id}", json={"is_approved": False}, headers=headers
    )
    assert hidden.status_code == 200
    assert hidden.json()["is_approved"] is False

    # shoppers no longer see it — list, count or average
    public = client.get(f"/api/products/{product['slug']}").json()
    assert public["review_count"] == seeded_count
    assert public["average_rating"] == seeded_avg
    assert all(r["is_approved"] for r in public["reviews"])

    # the author still finds their own copy, flagged
    own = client.get(f"/api/products/{product['slug']}", headers=mine).json()
    flagged = [r for r in own["reviews"] if r["id"] == review_id]
    assert flagged and flagged[0]["is_approved"] is False

    hidden_list = client.get(
        "/api/admin/reviews", params={"status": "hidden"}, headers=headers
    ).json()
    assert any(r["id"] == review_id for r in hidden_list["items"])
    # the tallies follow the shelf
    assert hidden_list["counts"]["live"] == live_before - 1
    assert hidden_list["counts"]["hidden"] >= 1

    restored = client.put(
        f"/api/admin/reviews/{review_id}", json={"is_approved": True}, headers=headers
    )
    assert restored.status_code == 200
    assert client.get(f"/api/products/{product['slug']}").json()["review_count"] == seeded_count + 1

    # the wall is the baker's to manage, never another shopper's
    assert client.put(
        f"/api/admin/reviews/{review_id}", json={"is_approved": False}, headers=mine
    ).status_code == 403


def test_bakers_bell_notifications():
    """Every customer-facing write — order, review (new or changed), catering
    lead, signup — drops one ping on the desk, and only the desk can read it."""
    headers = admin_headers()
    customer = client.post("/api/auth/login", json=CUSTOMER).json()["access_token"]

    placed = client.post("/api/orders", json=checkout_payload(), headers=auth_header(customer))
    assert placed.status_code == 201

    reviewer = register_and_login("bellfan@example.com", "Bellfan123!")
    product = get_product("wool-roll-bread")
    assert client.post(
        f"/api/products/{product['id']}/reviews",
        json={"rating": 5},
        headers=auth_header(reviewer),
    ).status_code == 201
    assert client.post(
        f"/api/products/{product['id']}/reviews",
        json={"rating": 3, "comment": "Hmm, smaller than last time"},
        headers=auth_header(reviewer),
    ).status_code == 200

    import datetime

    lead = client.post(
        "/api/bulk-orders",
        json={
            "name": "Bell Tester",
            "email": "bell@example.com",
            "phone": "+1 503 555 0199",
            "event_date": (datetime.date.today() + datetime.timedelta(days=14)).isoformat(),
            "event_type": "birthday",
            "guests": 20,
        },
    )
    assert lead.status_code == 201

    board = client.get("/api/admin/notifications", headers=headers).json()
    titles = [n["title"] for n in board["items"]]
    digest = " | ".join(titles)
    assert f"New order {placed.json()['order_number']}" in digest
    assert "left a review on Wool Roll Bread" in digest
    assert "changed their review on Wool Roll Bread" in digest
    assert "Bulk enquiry from Bell Tester" in digest
    assert any(t.startswith("New customer") for t in titles)
    assert board["unread_count"] >= 5

    # every enquiry ping lands on the enquiry rail, not the orders tab
    enquiry_links = [n["link"] for n in board["items"] if n["kind"] == "enquiry"]
    assert enquiry_links and all(link == "/admin/orders?tab=bulk" for link in enquiry_links)

    # one ping marks read; read-all clears the badge
    top = board["items"][0]
    assert (
        client.post(f"/api/admin/notifications/{top['id']}/read", headers=headers).status_code
        == 200
    )
    after_one = client.get("/api/admin/notifications", headers=headers).json()
    assert after_one["unread_count"] == board["unread_count"] - 1

    client.post("/api/admin/notifications/read-all", headers=headers)
    cleared = client.get("/api/admin/notifications", headers=headers).json()
    assert cleared["unread_count"] == 0

    # the bell rings for the baker alone
    assert client.get("/api/admin/notifications", headers=auth_header(customer)).status_code == 403


def test_users_loyalty_roll():
    """The roll measures what special vouchers reward — kept orders, spend,
    last visit — and cancelled orders never count towards any of it."""
    headers = admin_headers()

    loyal = auth_header(register_and_login("loyal@example.com", "Loyal123!"))
    vip = auth_header(register_and_login("vip@example.com", "Vipuser123!"))
    ghost = auth_header(register_and_login("ghost@example.com", "Ghost123!"))
    # a regular with a memorable name and phone, for the search shelf
    client.post(
        "/api/auth/register",
        json={
            "email": "colton@example.com",
            "password": "Colton123!",
            "first_name": "Colton",
            "last_name": "Mustard",
            "phone": "555-0142",
        },
    )

    def place(token):
        placed = client.post("/api/orders", json=checkout_payload(), headers=token)
        assert placed.status_code == 201, placed.text()
        return placed.json()

    for _ in range(3):
        place(loyal)
    for _ in range(10):
        place(vip)
    place(ghost)  # kept — the one that should count
    cancelled = place(ghost)
    assert (
        client.post(f"/api/orders/{cancelled['id']}/cancel", headers=ghost).status_code
        == 200
    )

    # the roll comes as shelves: default shelf is ten rows, tier chips count
    # the whole roll from any page
    first = client.get("/api/admin/users", headers=headers).json()
    assert len(first["items"]) <= 10
    assert first["pagination"]["per_page"] == 10
    assert first["pagination"]["page"] == 1

    whole = client.get("/api/admin/users", params={"per_page": 50}, headers=headers).json()
    assert whole["pagination"]["total_items"] == len(whole["items"])
    assert sum(whole["tier_counts"].values()) == len(whole["items"])

    roll = {u["email"]: u for u in whole["items"]}
    assert roll["loyal@example.com"]["order_count"] == 3
    assert roll["loyal@example.com"]["loyalty"] == "loyal"
    assert roll["loyal@example.com"]["last_order_at"] is not None

    vip_row = roll["vip@example.com"]
    assert vip_row["order_count"] == 10
    assert vip_row["loyalty"] == "vip"
    assert vip_row["lifetime_spend"] > 0

    # one of ghost's two orders was cancelled — only the kept one counts
    assert roll["ghost@example.com"]["order_count"] == 1
    assert roll["ghost@example.com"]["loyalty"] == "regular"
    assert roll["admin@djinisbakehouse.com"]["loyalty"] == "regular"

    # a tier shelf holds only that tier, counted from the whole roll
    vip_shelf = client.get(
        "/api/admin/users", params={"tier": "vip", "per_page": 50}, headers=headers
    ).json()
    assert vip_shelf["pagination"]["total_items"] == whole["tier_counts"]["vip"]
    assert {u["loyalty"] for u in vip_shelf["items"]} == {"vip"}

    # and the desk's sort is served, not improvised client-side
    by_orders = client.get(
        "/api/admin/users", params={"sort": "orders", "per_page": 50}, headers=headers
    ).json()["items"]
    counts = [u["order_count"] for u in by_orders]
    assert counts == sorted(counts, reverse=True)

    # search narrows by name, email or phone — and rides with the tier filter
    shelf = client.get(
        "/api/admin/users", params={"search": "mustard", "per_page": 50}, headers=headers
    ).json()
    assert [u["email"] for u in shelf["items"]] == ["colton@example.com"]
    by_phone = client.get(
        "/api/admin/users", params={"search": "555-0142", "per_page": 50}, headers=headers
    ).json()
    assert [u["email"] for u in by_phone["items"]] == ["colton@example.com"]
    nothing = client.get(
        "/api/admin/users", params={"search": "no-such-regular"}, headers=headers
    ).json()
    assert nothing["items"] == [] and nothing["pagination"]["total_items"] == 0
    still_vip = client.get(
        "/api/admin/users", params={"tier": "vip", "search": "example"}, headers=headers
    ).json()
    assert {u["loyalty"] for u in still_vip["items"]} <= {"vip"}

    # the roll is the baker's alone
    assert client.get("/api/admin/users", headers=loyal).status_code == 403


def test_loyalty_vouchers():
    """Each tier of regulars earns its own code: the desk links a coupon to
    vip and loyal, re-points or clears it at will, and retiring the coupon
    clears the link along with it."""
    headers = admin_headers()

    # the codes are the baker's alone to see
    assert client.get("/api/admin/loyalty-vouchers").status_code == 401
    empty = client.get("/api/admin/loyalty-vouchers", headers=headers).json()
    assert empty == {"vip": None, "loyal": None}

    vip_code = client.post(
        "/api/admin/coupons",
        json={"code": "vipgift15", "percent_off": 15},
        headers=headers,
    ).json()
    loyal_code = client.post(
        "/api/admin/coupons",
        json={"code": "loyalthanks", "percent_off": 10, "description": "for the regulars"},
        headers=headers,
    ).json()

    linked = client.put(
        "/api/admin/loyalty-vouchers/vip",
        json={"coupon_id": vip_code["id"]},
        headers=headers,
    ).json()
    assert linked["vip"]["code"] == "VIPGIFT15"
    assert linked["loyal"] is None

    both = client.put(
        "/api/admin/loyalty-vouchers/loyal",
        json={"coupon_id": loyal_code["id"]},
        headers=headers,
    ).json()
    assert both["vip"]["code"] == "VIPGIFT15"
    assert both["loyal"]["percent_off"] == 10

    # re-pointing swaps the code; clearing drops the tier back to None
    swapped = client.put(
        "/api/admin/loyalty-vouchers/vip",
        json={"coupon_id": loyal_code["id"]},
        headers=headers,
    ).json()
    assert swapped["vip"]["code"] == "LOYALTHANKS"
    cleared = client.put(
        "/api/admin/loyalty-vouchers/loyal",
        json={"coupon_id": None},
        headers=headers,
    ).json()
    assert cleared["loyal"] is None

    # only the two voucher tiers exist, and only real coupons
    assert (
        client.put(
            "/api/admin/loyalty-vouchers/regular",
            json={"coupon_id": vip_code["id"]},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/admin/loyalty-vouchers/vip",
            json={"coupon_id": 99999},
            headers=headers,
        ).status_code
        == 404
    )

    # retiring a coupon clears whatever tier was handing it out
    assert (
        client.delete(f"/api/admin/coupons/{loyal_code['id']}", headers=headers).status_code
        == 200
    )
    final = client.get("/api/admin/loyalty-vouchers", headers=headers).json()
    assert final == {"vip": None, "loyal": None}

    # leave the coupon tin as we found it
    client.delete(f"/api/admin/coupons/{vip_code['id']}", headers=headers)


def test_favorites():
    token = register_and_login("heart@example.com", "Heart123!")
    headers = auth_header(token)
    product = get_product("butter-croissant")

    saved = client.put(f"/api/products/{product['id']}/favorite", headers=headers)
    assert saved.status_code == 200
    assert saved.json()["saved"] is True

    # hearting twice keeps exactly one row
    assert client.put(f"/api/products/{product['id']}/favorite", headers=headers).json()["saved"] is True
    listing = client.get("/api/products/favorites", headers=headers).json()
    assert [p["slug"] for p in listing] == ["butter-croissant"]

    assert client.delete(f"/api/products/{product['id']}/favorite", headers=headers).json()["saved"] is False
    assert client.get("/api/products/favorites", headers=headers).json() == []

    # guests can't save; unknown products don't exist
    assert client.get("/api/products/favorites").status_code == 401
    assert client.put("/api/products/99999/favorite", headers=headers).status_code == 404


# --------------------------------------------------------------------------
# Checkout & orders
# --------------------------------------------------------------------------

def get_product(slug: str) -> dict:
    return client.get(f"/api/products/{slug}").json()


def checkout_payload(token_email: str = CUSTOMER["email"], **overrides) -> dict:
    croissant = get_product("butter-croissant")
    curry_puff = get_product("beef-curry-puff")
    payload = {
        "items": [
            {"product_id": croissant["id"], "quantity": 2},
            {"product_id": curry_puff["id"], "quantity": 1},
        ],
        "new_address": {
            "label": "Home",
            "recipient": "Shopper Smith",
            "phone": "555-0100",
            "line1": "123 Baker Street",
            "city": "Sweet Valley",
            "state": "CA",
            "postal_code": "90210",
            "country": "USA",
        },
        "payment_method": "card",
        "note": "Ring the doorbell twice",
    }
    payload.update(overrides)
    return payload


def login_customer() -> str:
    response = client.post("/api/auth/login", json=CUSTOMER)
    return response.json()["access_token"]


def test_checkout_creates_order_and_decrements_stock():
    token = login_customer()
    headers = auth_header(token)

    before = get_product("butter-croissant")["stock"]
    response = client.post("/api/orders", json=checkout_payload(), headers=headers)
    assert response.status_code == 201
    order = response.json()

    assert order["status"] == "pending"
    assert order["payment_status"] == "pending"  # card settles when Stripe says so
    assert order["payment_url"].startswith("https://checkout.stripe.test/")
    assert order["can_be_cancelled"] is True
    assert order["items"][0]["quantity"] == 2

    after = get_product("butter-croissant")["stock"]
    assert after == before - 2

    # totals are internally consistent
    expected_total = round(order["subtotal"] + order["tax"] + order["shipping_fee"], 2)
    assert abs(order["total"] - expected_total) < 0.01


def test_order_requires_authentication():
    response = client.post("/api/orders", json=checkout_payload())
    assert response.status_code == 401


def test_card_checkout_refused_until_stripe_is_configured(monkeypatch):
    """No STRIPE_SECRET_KEY, no card option: the request is refused before
    any address side effects, while cash on delivery still lands."""
    headers = auth_header(register_and_login(CUSTOMER["email"], CUSTOMER["password"]))
    monkeypatch.setattr(stripe_gateway, "configured", lambda: False)

    refused = client.post("/api/orders", json=checkout_payload(), headers=headers)
    assert refused.status_code == 400
    assert "cash on delivery" in refused.json()["detail"]

    placed = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=headers,
    )
    assert placed.status_code == 201
    assert placed.json()["payment_status"] == "pending"


def test_shop_contact_details_reach_the_footer():
    """The baker's real address, phone and hours set in Settings surface on
    the public storefront — and clearing them clears the footer lines."""
    headers = admin_headers()
    saved = client.put(
        "/api/admin/settings",
        json={
            "contact_address": "12 Crumb Lane, Portland",
            "contact_phone": "+1 (503) 555-0100",
            "contact_email": "counter@djinisbakehouse.com",
            "opening_hours": "Tue – Fri: 7:00 – 18:00\nSat: 7:00 – 15:00",
        },
        headers=headers,
    )
    assert saved.status_code == 200

    store = client.get("/api/store").json()
    assert store["contact_address"] == "12 Crumb Lane, Portland"
    assert store["contact_phone"] == "+1 (503) 555-0100"
    assert store["contact_email"] == "counter@djinisbakehouse.com"
    assert store["opening_hours"].startswith("Tue – Fri")

    cleared = client.put(
        "/api/admin/settings",
        json={
            "contact_address": "",
            "contact_phone": "",
            "contact_email": "",
            "opening_hours": "",
        },
        headers=headers,
    )
    assert cleared.status_code == 200
    store = client.get("/api/store").json()
    assert store["contact_address"] is None and store["opening_hours"] is None


def test_stock_validation():
    token = login_customer()
    headers = auth_header(token)
    product = get_product("vol-au-vent")  # seeded with a stock of 4
    response = client.post(
        "/api/orders",
        json=checkout_payload(items=[{"product_id": product["id"], "quantity": 99}]),
        headers=headers,
    )
    assert response.status_code == 400


def test_coupon_at_checkout():
    token = login_customer()
    headers = auth_header(token)

    preview = client.get("/api/orders/coupons/welcome10", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["percent_off"] == 10
    assert client.get("/api/orders/coupons/NOT-A-CODE", headers=headers).status_code == 404

    order = client.post(
        "/api/orders", json=checkout_payload(coupon_code="welcome10"), headers=headers
    ).json()
    assert order["coupon_code"] == "WELCOME10"
    assert order["discount_amount"] == round(order["subtotal"] * 0.10, 2)

    # tax is charged on the discounted amount, at the approved EUR rate
    taxable = order["subtotal"] - order["discount_amount"]
    assert abs(order["tax"] - round(taxable * get_settings().tax_rate, 2)) < 0.01

    # an inactive/unknown code is rejected at checkout too
    bad = client.post(
        "/api/orders", json=checkout_payload(coupon_code="NOT-A-CODE"), headers=headers
    )
    assert bad.status_code == 404


def test_order_listing_and_cancel():
    token = login_customer()
    headers = auth_header(token)

    listing = client.get("/api/orders", headers=headers).json()
    assert listing["pagination"]["total_items"] >= 1

    order_id = listing["orders"][0]["id"]
    detail = client.get(f"/api/orders/{order_id}", headers=headers).json()
    assert detail["timeline"][0]["status"] == "pending"

    # The newest order is a card order still awaiting its payment — let it
    # land, then cancelling refunds it.
    pay_order_via_webhook(order_id)

    stock_before = get_product("butter-croissant")["stock"]
    cancelled = client.post(f"/api/orders/{order_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["payment_status"] == "refunded"

    # cancelling twice fails, stock was restored
    assert client.post(f"/api/orders/{order_id}/cancel", headers=headers).status_code == 400
    assert get_product("butter-croissant")["stock"] == stock_before + 2


def test_late_cancel_fee_when_baking():
    admin = admin_headers()
    customer = auth_header(login_customer())

    # the admin tunes the penalty (default is 10)
    tuned = client.put(
        "/api/admin/settings", json={"late_cancel_fee_percent": 25}, headers=admin
    ).json()
    assert tuned["late_cancel_fee_percent"] == 25

    # customer buys by card and pays; the bakery moves it into the oven
    order = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
    pay_order_via_webhook(order["id"])
    baking = client.put(
        f"/api/admin/orders/{order['id']}/status", json={"status": "baking"}, headers=admin
    )
    assert baking.status_code == 200
    fee = round(order["total"] * 0.25, 2)
    assert baking.json()["cancellation_fee"] == fee

    # the detail shows the cost up-front; cancelling keeps the slice
    detail = client.get(f"/api/orders/{order['id']}", headers=customer).json()
    assert detail["cancellation_fee"] == fee
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=customer).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_fee"] == fee
    assert cancelled["payment_status"] == "partially_refunded"
    assert "late-cancellation fee" in cancelled["timeline"][-1]["comment"]
    # the refund actually went out: the total less the kept fee, in cents
    expected_refund = int(round((order["total"] - fee) * 100))
    assert ("refund", f"pi_fake_{order['id']}", expected_refund) in gateway_calls

    # a zero percent turns the penalty off entirely
    client.put("/api/admin/settings", json={"late_cancel_fee_percent": 0}, headers=admin)
    order2 = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
    pay_order_via_webhook(order2["id"])
    client.put(
        f"/api/admin/orders/{order2['id']}/status", json={"status": "baking"}, headers=admin
    )
    cancelled2 = client.post(f"/api/orders/{order2['id']}/cancel", headers=customer).json()
    assert cancelled2["cancel_fee"] == 0
    assert cancelled2["payment_status"] == "refunded"
    assert ("refund", f"pi_fake_{order2['id']}", int(round(order2["total"] * 100))) in gateway_calls

    # settings are admin-only and the percent is range-checked
    assert client.get("/api/admin/settings").status_code == 401
    assert client.get("/api/admin/settings", headers=customer).status_code == 403
    assert (
        client.put(
            "/api/admin/settings", json={"late_cancel_fee_percent": 250}, headers=admin
        ).status_code
        == 422
    )

    # leave the dial where the shop keeps it
    client.put("/api/admin/settings", json={"late_cancel_fee_percent": 10}, headers=admin)


def test_admin_cancel_via_status_dial():
    """Cancelling from the console runs the full cancellation, not just a
    status swap: stock goes back, a paid order is refunded, and a cancelled
    order can't be quietly resurrected."""
    admin = admin_headers()
    customer = auth_header(login_customer())
    product = get_product("fresh-bagels")

    before = product["stock"]
    order = client.post(
        "/api/orders",
        json=checkout_payload(items=[{"product_id": product["id"], "quantity": 2}]),
        headers=customer,
    ).json()
    pay_order_via_webhook(order["id"])
    assert get_product("fresh-bagels")["stock"] == before - 2

    cancelled = client.put(
        f"/api/admin/orders/{order['id']}/status", json={"status": "cancelled"}, headers=admin
    )
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["payment_status"] == "refunded"  # card order, bakery-side cancel is free
    assert body["cancel_fee"] == 0
    # the full amount went back to the card
    assert ("refund", f"pi_fake_{order['id']}", int(round(order["total"] * 100))) in gateway_calls
    assert get_product("fresh-bagels")["stock"] == before

    # cancelling twice and reopening a cancelled order are both refused
    assert (
        client.put(
            f"/api/admin/orders/{order['id']}/status", json={"status": "cancelled"}, headers=admin
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/admin/orders/{order['id']}/status", json={"status": "baking"}, headers=admin
        ).status_code
        == 400
    )


# --------------------------------------------------------------------------
# Stripe payments
# --------------------------------------------------------------------------

def test_card_payment_lands_via_webhook_and_emails_receipt(caplog):
    """The full card journey: placed unpaid with a Stripe link and no
    receipt yet, then the webhook flips it paid and the receipt — an
    honestly 'paid' one — goes out. Replaying the webhook changes nothing."""
    import logging

    headers = auth_header(login_customer())
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
        assert order["payment_status"] == "pending"
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert f"Receipt {order['order_number']}" not in logged  # not before the money

        pay_order_via_webhook(order["id"])

    paid = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert paid["payment_status"] == "paid"
    assert any("Payment received" in e["comment"] for e in paid["timeline"])
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {order['order_number']}" in logged
    assert "Paid by Card — this email is your proof of purchase" in logged

    # the webhook is idempotent — Stripe retries, the event must not double
    events_before = len(paid["timeline"])
    pay_order_via_webhook(order["id"])
    replayed = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert replayed["payment_status"] == "paid"
    assert len(replayed["timeline"]) == events_before


def test_webhook_without_signature_is_rejected():
    response = client.post(
        "/api/payments/webhook",
        json={"type": "checkout.session.completed", "data": {"object": {}}},
    )
    assert response.status_code == 400
    assert "rejected" in response.json()["detail"].lower()


def test_expired_session_cancels_order_and_restores_stock():
    headers = auth_header(login_customer())
    stock_before = get_product("butter-croissant")["stock"]
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()

    expire_order_via_webhook(order["id"])

    detail = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert detail["status"] == "cancelled"
    assert detail["payment_status"] == "pending"  # nothing was ever collected
    assert "Payment window expired" in detail["timeline"][-1]["comment"]
    assert get_product("butter-croissant")["stock"] == stock_before


def test_confirm_syncs_payment_when_webhook_is_late():
    """Back from Stripe, the order page reconciles with the gateway instead
    of waiting on the webhook."""
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    fake_sessions[last_session_id(order["id"])].update(
        status="complete", payment_status="paid", payment_intent=f"pi_fake_{order['id']}"
    )

    confirmed = client.post(f"/api/payments/confirm/{order['id']}", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["payment_status"] == "paid"

    # a second confirm (page refresh) is a no-op, not an error
    again = client.post(f"/api/payments/confirm/{order['id']}", headers=headers)
    assert again.status_code == 200
    assert again.json()["payment_status"] == "paid"


def test_webhook_accepts_a_genuinely_signed_stripe_event(monkeypatch):
    """The production bug this pins: the fake verify_webhook returns dicts,
    but the real SDK's construct_event hands back an Event object that
    refuses .get() by design — so every live delivery 500ed while the whole
    suite stayed green. Here the endpoint runs the REAL signature
    verification (offline, pure HMAC — no key, no network) against a
    payload signed exactly the way Stripe signs them."""
    import hashlib
    import hmac
    import importlib.util
    import json
    import time
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "app._stripe_gateway_signed",
        Path(__file__).resolve().parents[1] / "app" / "stripe_gateway.py",
    )
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    secret = "whsec_signed_integration"
    monkeypatch.setattr(stripe_gateway, "verify_webhook", fresh.verify_webhook)
    # the fresh copy's exception class must be the one the endpoint catches —
    # in production both come from the same module object
    monkeypatch.setattr(stripe_gateway, "BadSignature", fresh.BadSignature)
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)

    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    payload = json.dumps(
        {
            "id": "evt_signed_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": last_session_id(order["id"]),
                    "payment_status": "paid",
                    "payment_intent": f"pi_signed_{order['id']}",
                    "metadata": {"order_id": str(order["id"])},
                }
            },
        }
    ).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()

    delivered = client.post(
        "/api/payments/webhook",
        content=payload,
        headers={"stripe-signature": f"t={timestamp},v1={signature}"},
    )
    assert delivered.status_code == 200
    assert delivered.json() == {"received": True}
    paid = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert paid["payment_status"] == "paid"
    assert any("Payment received" in e["comment"] for e in paid["timeline"])

    # The same bytes signed with someone else's secret never reach the ledger
    forgery = hmac.new(
        b"whsec_attacker", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    refused = client.post(
        "/api/payments/webhook",
        content=payload,
        headers={"stripe-signature": f"t={timestamp},v1={forgery}"},
    )
    assert refused.status_code == 400
    assert "rejected" in refused.json()["detail"].lower()


def test_webhook_ignores_malformed_events_instead_of_erroring():
    """Shape surprises inside a signed event — no data object, a non-dict
    one, an unknown type like payment_intent.succeeded, not even a type —
    are ignored with a 200. Stripe retries 500 responses for days, so a
    malformed event must never raise."""
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()

    for event in (
        {"id": "evt_1", "type": "checkout.session.completed", "data": {}},
        {"id": "evt_2", "type": "checkout.session.completed", "data": {"object": "not a dict"}},
        {"id": "evt_3", "type": "checkout.session.completed"},  # no data at all
        {"id": "evt_4", "type": "payment_intent.succeeded", "data": {"object": {"id": "pi_1"}}},
        {"id": "evt_5"},  # not even a type
    ):
        response = client.post(
            "/api/payments/webhook",
            json=event,
            headers={"stripe-signature": "t=1,v1=fake"},
        )
        assert response.status_code == 200

    detail = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert detail["status"] == "pending"
    assert detail["payment_status"] == "pending"


def test_pay_again_replaces_session_and_old_expiry_is_ignored():
    """A failed payment shouldn't mean a rebuilt basket: a fresh link is
    issued for the same order, and only the current session's expiry may
    cancel it."""
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    first_session = last_session_id(order["id"])

    fresh = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert fresh.status_code == 201
    assert fresh.json()["payment_url"].startswith("https://checkout.stripe.test/")
    assert last_session_id(order["id"]) != first_session

    # the superseded session's expiry event must not cancel the order
    stale_expiry = client.post(
        "/api/payments/webhook",
        json={
            "type": "checkout.session.expired",
            "data": {"object": {"id": first_session, "metadata": {"order_id": str(order["id"])}}},
        },
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert stale_expiry.status_code == 200
    assert client.get(f"/api/orders/{order['id']}", headers=headers).json()["status"] == "pending"

    # the current session expiring does cancel it
    expire_order_via_webhook(order["id"])
    assert (
        client.get(f"/api/orders/{order['id']}", headers=headers).json()["status"] == "cancelled"
    )


def test_unpaid_card_cancel_promises_nothing():
    """Cancelling a card order that never collected money flips no refund
    status and touches no gateway refund."""
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    refunds_before = [c for c in gateway_calls if c[0] == "refund"]

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["payment_status"] == "pending"
    assert [c for c in gateway_calls if c[0] == "refund"] == refunds_before


def test_refund_failure_keeps_payment_status_honest(monkeypatch):
    """If Stripe can't take the refund, the order still cancels — but it
    must not claim a refund that didn't happen."""
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    pay_order_via_webhook(order["id"])

    def broken_refund(payment_intent, amount_cents):
        raise stripe_gateway.GatewayUnavailable("Stripe is down")

    monkeypatch.setattr(stripe_gateway, "refund", broken_refund)
    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=headers).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["payment_status"] == "paid"  # still the truth
    assert "Stripe dashboard" in cancelled["timeline"][-1]["comment"]


def test_storefront_advertises_card_payments():
    """/api/store tells checkout whether the card option exists — with the
    key configured it's true; with the key gone it's false."""
    assert client.get("/api/store").json()["card_payments"] is True
    # configured() reads settings at call time, so drop the key for a beat
    from app.config import get_settings

    settings = get_settings()
    saved = settings.stripe_secret_key
    settings.stripe_secret_key = ""
    try:
        assert client.get("/api/store").json()["card_payments"] is False
    finally:
        settings.stripe_secret_key = saved


def test_storefront_copy_settings():
    admin = admin_headers()
    customer = auth_header(login_customer())

    # the public shop copy serves the shipped lines
    storefront = client.get("/api/store").json()
    assert "WELCOME10" in storefront["announcement"]
    assert "doughnuts" in storefront["today_note"]

    # the admin rewrites both lines and the storefront picks them up
    updated = client.put(
        "/api/admin/settings",
        json={"announcement": "Sourdough Fridays — 20% off", "today_note": "Cinnamon buns at 7"},
        headers=admin,
    ).json()
    assert updated["announcement"] == "Sourdough Fridays — 20% off"
    storefront = client.get("/api/store").json()
    assert storefront["announcement"] == "Sourdough Fridays — 20% off"
    assert storefront["today_note"] == "Cinnamon buns at 7"

    # a cleared line hides the element instead of falling back to the default
    client.put("/api/admin/settings", json={"announcement": "  ", "today_note": ""}, headers=admin)
    storefront = client.get("/api/store").json()
    assert storefront["announcement"] == ""
    assert storefront["today_note"] == ""

    # the hero photo ships as the default and can be swapped from Settings
    assert client.get("/api/store").json()["hero_image"] == DEFAULT_HERO_IMAGE
    swapped = client.put(
        "/api/admin/settings", json={"hero_image": "/static/uploads/site/new-counter.jpg"}, headers=admin
    ).json()
    assert swapped["hero_image"] == "/static/uploads/site/new-counter.jpg"
    assert client.get("/api/store").json()["hero_image"] == "/static/uploads/site/new-counter.jpg"

    # clearing it falls back to the shipped photo — no hidden state
    cleared = client.put("/api/admin/settings", json={"hero_image": "  "}, headers=admin).json()
    assert cleared["hero_image"] == DEFAULT_HERO_IMAGE
    assert client.get("/api/store").json()["hero_image"] == DEFAULT_HERO_IMAGE

    # the scrolling strip ships with the default phrases and is editable
    storefront = client.get("/api/store").json()
    assert "FRESH EVERY MORNING" in storefront["marquee"]
    swapped = client.put(
        "/api/admin/settings",
        json={"marquee_items": "PUMPKIN SEASON\n\n  SOURDOUGH FRIDAYS  "},
        headers=admin,
    ).json()
    assert swapped["marquee_items"] == "PUMPKIN SEASON\n\n  SOURDOUGH FRIDAYS"
    storefront = client.get("/api/store").json()
    assert storefront["marquee"] == ["PUMPKIN SEASON", "SOURDOUGH FRIDAYS"]

    # a cleared strip hides the band rather than falling back
    client.put("/api/admin/settings", json={"marquee_items": ""}, headers=admin)
    assert client.get("/api/store").json()["marquee"] == []

    # the copy is admin-only
    assert (
        client.put("/api/admin/settings", json={"today_note": "x"}, headers=customer).status_code
        == 403
    )

    # the brand colour ships unset and only ever accepts a real hex code
    assert client.get("/api/store").json()["brand_color"] is None
    coloured = client.put(
        "/api/admin/settings", json={"brand_color": " #1E7D74 "}, headers=admin
    ).json()
    assert coloured["brand_color"] == "#1e7d74"
    assert client.get("/api/store").json()["brand_color"] == "#1e7d74"
    for bad in ("1e7d74", "#1e7dg8", "#1e7d", "red"):
        rejected = client.put(
            "/api/admin/settings", json={"brand_color": bad}, headers=admin
        )
        assert rejected.status_code == 400, bad
    # empty returns the shop to the shipped burgundy
    cleared = client.put("/api/admin/settings", json={"brand_color": "  "}, headers=admin).json()
    assert cleared["brand_color"] is None
    assert client.get("/api/store").json()["brand_color"] is None

    # put the shipped lines back
    client.put(
        "/api/admin/settings",
        json={"announcement": DEFAULT_ANNOUNCEMENT, "today_note": DEFAULT_TODAY_NOTE},
        headers=admin,
    )
    assert "WELCOME10" in client.get("/api/store").json()["announcement"]


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

def admin_headers() -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@djinisbakehouse.com", "password": "Admin123!"},
    )
    assert response.status_code == 200
    return auth_header(response.json()["access_token"])


def test_customer_cannot_access_admin():
    token = login_customer()
    assert client.get("/api/admin/stats", headers=auth_header(token)).status_code == 403


def test_admin_stats_and_order_management():
    headers = admin_headers()

    # ensure at least one active order exists (earlier ones were cancelled)
    customer = client.post("/api/auth/login", json=CUSTOMER).json()["access_token"]
    placed = client.post("/api/orders", json=checkout_payload(), headers=auth_header(customer))
    assert placed.status_code == 201

    stats = client.get("/api/admin/stats", headers=headers).json()
    assert stats["order_count"] >= 1
    assert "total_revenue" in stats and "top_products" in stats

    orders = client.get("/api/admin/orders", headers=headers).json()
    order_id = orders["orders"][0]["id"]

    updated = client.put(
        f"/api/admin/orders/{order_id}/status",
        json={"status": "baking", "comment": "In the oven"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "baking"
    assert len(updated.json()["timeline"]) >= 2


def test_admin_product_crud():
    headers = admin_headers()

    created = client.post(
        "/api/admin/products",
        json={
            "name": "Test Lemon Drizzle Cake",
            "price": 24.0,
            "stock": 10,
            "category_id": 2,
            "is_featured": False,
        },
        headers=headers,
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    assert created.json()["slug"].startswith("test-lemon-drizzle-cake")

    updated = client.put(
        f"/api/admin/products/{product_id}",
        json={
            "name": "Test Lemon Drizzle Cake",
            "price": 26.0,
            "stock": 5,
            "category_id": 2,
            "is_available": True,
        },
        headers=headers,
    )
    assert updated.json()["price"] == 26.0

    deleted = client.delete(f"/api/admin/products/{product_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/products/{created.json()['slug']}").status_code == 404


def test_admin_category_creation():
    headers = admin_headers()

    # the baker can open a new shelf on the fly from the product form
    created = client.post("/api/admin/categories", json={"name": "Savory Pies"}, headers=headers)
    assert created.status_code == 201
    category = created.json()
    assert category["slug"] == "savory-pies"
    assert category["product_count"] == 0

    # the shelf is public immediately, alongside the seeded ones
    names = [c["name"] for c in client.get("/api/products/categories").json()]
    assert "Savory Pies" in names

    # a brand-new shelf has no photo of its own yet
    shelf = next(
        c for c in client.get("/api/products/categories").json() if c["slug"] == "savory-pies"
    )
    assert shelf["image_url"] is None

    # a duplicate name in any casing is refused, not duplicated
    again = client.post("/api/admin/categories", json={"name": "savory pies"}, headers=headers)
    assert again.status_code == 409

    # a product can sit straight onto the new shelf
    product = client.post(
        "/api/admin/products",
        json={
            "name": "Test Chicken Pot Pie",
            "price": 12.0,
            "stock": 4,
            "category_id": category["id"],
            "image_url": "/static/products/butter-croissant.jpg",
        },
        headers=headers,
    )
    assert product.status_code == 201
    assert product.json()["category_slug"] == "savory-pies"

    # …and the storefront lends the shelf its first bake's photo, so the
    # home tiles never sit on a bare letter tile
    shelf = next(
        c for c in client.get("/api/products/categories").json() if c["slug"] == "savory-pies"
    )
    assert shelf["image_url"] == "/static/products/butter-croissant.jpg"

    # customers can't create shelves
    forbidden = client.post(
        "/api/admin/categories", json={"name": "Nope"}, headers=auth_header(login_customer())
    )
    assert forbidden.status_code == 403

    # the desk can ask for one shelf's bakes alone
    filtered = client.get(
        f"/api/admin/products?category_id={category['id']}", headers=headers
    )
    assert filtered.status_code == 200
    rows = filtered.json()["products"]
    assert rows, "the shelf holds a bake, so the filtered list can't be empty"
    assert {r["category_slug"] for r in rows} == {"savory-pies"}

    # an occupied shelf retires too — its bakes come off the shelf,
    # not off the case
    busy = client.delete(f"/api/admin/categories/{category['id']}", headers=headers)
    assert busy.status_code == 200
    leftover = client.get(
        f"/api/admin/products/{product.json()['id']}", headers=headers
    )
    assert leftover.status_code == 200
    assert leftover.json()["category_slug"] is None
    slugs = [c["slug"] for c in client.get("/api/products/categories").json()]
    assert "savory-pies" not in slugs

    # …and the bake itself tidies up for later sweeps
    client.delete(f"/api/admin/products/{product.json()['id']}", headers=headers)


def test_admin_coupon_crud():
    headers = admin_headers()

    created = client.post(
        "/api/admin/coupons",
        json={"code": "Test20", "percent_off": 20, "description": "Test campaign"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["code"] == "TEST20"  # normalised to upper case
    coupon_id = created.json()["id"]

    # duplicate code rejected
    dup = client.post(
        "/api/admin/coupons", json={"code": "TEST20", "percent_off": 30}, headers=headers
    )
    assert dup.status_code == 409

    updated = client.put(
        f"/api/admin/coupons/{coupon_id}", json={"percent_off": 25}, headers=headers
    )
    assert updated.json()["percent_off"] == 25

    listing = client.get("/api/admin/coupons", headers=headers).json()
    assert any(c["code"] == "WELCOME10" for c in listing)  # seeded

    assert client.delete(f"/api/admin/coupons/{coupon_id}", headers=headers).status_code == 200


def test_admin_csv_export():
    headers = admin_headers()
    response = client.get("/api/admin/orders/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    lines = response.text.strip().splitlines()
    assert lines[0].startswith("order_number,date,customer")
    assert len(lines) > 5  # seeded history + orders placed by these tests
    assert any("WELCOME10" in line or "FRESHBAKE15" in line for line in lines)


def test_admin_users():
    headers = admin_headers()
    users = client.get(
        "/api/admin/users", params={"per_page": 50}, headers=headers
    ).json()["items"]
    assert any(u["email"] == CUSTOMER["email"] for u in users)

    customer_id = next(u["id"] for u in users if u["email"] == CUSTOMER["email"])
    updated = client.put(
        f"/api/admin/users/{customer_id}", json={"is_active": False}, headers=headers
    )
    assert updated.json()["is_active"] is False

    # deactivated user can no longer log in
    locked = client.post("/api/auth/login", json=CUSTOMER)
    assert locked.status_code == 403

    client.put(f"/api/admin/users/{customer_id}", json={"is_active": True}, headers=headers)


def test_newsletter():
    first = client.post("/api/newsletter", json={"email": "fan@example.com", "source": "footer"})
    assert first.status_code == 201
    assert first.json() == {"email": "fan@example.com", "already_subscribed": False}

    # re-signing up (any casing) is enthusiasm, not an error
    again = client.post("/api/newsletter", json={"email": "FAN@example.com"})
    assert again.status_code == 200
    assert again.json()["already_subscribed"] is True

    assert client.post("/api/newsletter", json={"email": "not-an-email"}).status_code == 422

    headers = admin_headers()
    listing = client.get("/api/admin/newsletter", headers=headers).json()
    assert any(s["email"] == "fan@example.com" for s in listing)
    assert client.get("/api/admin/newsletter").status_code == 401  # admin only

    export = client.get("/api/admin/newsletter/export", headers=headers)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert export.text.splitlines()[0] == "email,signed_up,source"


# --------------------------------------------------------------------------
# Photo uploads (avatars + product photos)
# --------------------------------------------------------------------------

# 1x1 transparent PNG — the smallest valid image a browser can produce.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _uploaded_file(path: str):
    from pathlib import Path

    return Path("static") / path.removeprefix("/static/")


def test_avatar_upload_and_remove():
    token = register_and_login("snapchat@example.com", "Snapchat123!")

    upload = client.post(
        "/api/auth/me/avatar",
        headers=auth_header(token),
        files={"photo": ("me.png", TINY_PNG, "image/png")},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["avatar_url"].startswith("/static/uploads/avatars/")
    assert _uploaded_file(body["avatar_url"]).is_file()

    # /me reflects it, replacing the photo drops the old file
    assert client.get("/api/auth/me", headers=auth_header(token)).json()["avatar_url"] == body["avatar_url"]
    replaced = client.post(
        "/api/auth/me/avatar",
        headers=auth_header(token),
        files={"photo": ("me.png", TINY_PNG, "image/png")},
    )
    assert replaced.status_code == 200
    assert replaced.json()["avatar_url"] != body["avatar_url"]
    assert not _uploaded_file(body["avatar_url"]).exists()

    removed = client.delete("/api/auth/me/avatar", headers=auth_header(token))
    assert removed.status_code == 200
    assert removed.json()["avatar_url"] is None
    assert not _uploaded_file(replaced.json()["avatar_url"]).exists()

    assert client.post(
        "/api/auth/me/avatar", files={"photo": ("me.txt", b"nope", "text/plain")}
    ).status_code == 401  # anonymous, and would be a 415 anyway


def test_admin_product_photo_upload():
    upload = client.post(
        "/api/admin/uploads/product-photo",
        headers=admin_headers(),
        files={"photo": ("cake.jpg", TINY_PNG, "image/jpeg")},
    )
    assert upload.status_code == 200
    url = upload.json()["url"]
    assert url.startswith("/static/uploads/products/")
    assert _uploaded_file(url).is_file()

    # the URL plugs straight into the product form's save
    create = client.post(
        "/api/admin/products",
        headers=admin_headers(),
        json={"name": "Photo Test Loaf", "price": 4.0, "stock": 3, "image_url": url},
    )
    assert create.status_code == 201
    assert create.json()["image_url"] == url

    # non-images never reach the disk
    rejected = client.post(
        "/api/admin/uploads/product-photo",
        headers=admin_headers(),
        files={"photo": ("notes.txt", b"hello", "text/plain")},
    )
    assert rejected.status_code == 415

    _uploaded_file(url).unlink()  # keep the repo free of test artefacts
    assert client.post(
        "/api/admin/uploads/product-photo",
        files={"photo": ("cake.jpg", TINY_PNG, "image/jpeg")},
    ).status_code == 401


# --------------------------------------------------------------------------
# Voucher board, bulk enquiries and curated home content
# --------------------------------------------------------------------------

def test_public_voucher_board():
    vouchers = {v["code"]: v for v in client.get("/api/coupons").json()}
    assert set(vouchers) >= {"WELCOME10", "FRESHBAKE15"}
    assert vouchers["WELCOME10"]["min_subtotal"] is None
    # FRESHBAKE15's $40 floor came off with the EUR cutover — a dollar
    # figure is not an approved euro minimum, so the code promises none.
    assert vouchers["FRESHBAKE15"]["min_subtotal"] is None

    # paused codes leave the board
    admin = admin_headers()
    client.put("/api/admin/coupons/1", json={"is_active": False}, headers=admin)
    assert "WELCOME10" not in {v["code"] for v in client.get("/api/coupons").json()}
    client.put("/api/admin/coupons/1", json={"is_active": True}, headers=admin)


def test_coupon_min_subtotal_enforced():
    token = login_customer()
    headers = auth_header(token)

    # FRESHBAKE15's $40 floor came off with the EUR cutover, so the floor
    # itself is tested on a coupon that explicitly carries an approved one.
    assert (
        client.get("/api/orders/coupons/freshbake15", headers=headers).json()["min_subtotal"]
        is None
    )

    created = client.post(
        "/api/admin/coupons",
        json={"code": "WEEKEND20", "percent_off": 20, "min_subtotal": 20.0},
        headers=admin_headers(),
    )
    assert created.status_code == 201
    coupon_id = created.json()["id"]

    preview = client.get("/api/orders/coupons/weekend20", headers=headers).json()
    assert preview["min_subtotal"] == 20.0

    # too small a basket: rejected with the threshold named
    small = checkout_payload(coupon_code="WEEKEND20")
    small["items"] = small["items"][:1]
    rejected = client.post("/api/orders", json=small, headers=headers)
    assert rejected.status_code == 400
    assert "20.00" in rejected.json()["detail"]

    # over the threshold it applies like any other code (8 maritozzi ≈ €42)
    maritozzo = get_product("maritozzo")
    order = client.post(
        "/api/orders",
        json=checkout_payload(
            coupon_code="WEEKEND20",
            items=[{"product_id": maritozzo["id"], "quantity": 8}],
        ),
        headers=headers,
    ).json()
    assert order["coupon_code"] == "WEEKEND20"
    assert order["discount_amount"] == round(order["subtotal"] * 0.20, 2)

    client.delete(f"/api/admin/coupons/{coupon_id}", headers=admin_headers())


def test_a_pre_cutover_minimum_stays_unappliable_until_reapproved():
    """A minimum recorded before the EUR cutover is a USD numeral the app
    cannot re-read as euros. The coupon itself survives, but its minimum is
    neither shown nor spendable — board, code check, quote, placement all
    refuse or hide it — until an administrator re-saves it from the
    Promotions desk, and that save is the approval."""
    from app.database import SessionLocal
    from app.models import Coupon as CouponRow

    token = login_customer()
    headers = auth_header(token)
    admin = admin_headers()

    made = client.post(
        "/api/admin/coupons",
        json={"code": "OLDBUNDLE", "percent_off": 15, "min_subtotal": 40.0},
        headers=admin,
    )
    assert made.status_code == 201
    # A threshold typed at the desk is chosen in today's currency: approved.
    assert made.json()["min_subtotal_approved"] is True
    coupon_id = made.json()["id"]

    # Time-travel the row to its pre-cutover shape — a minimum written
    # before the approval column existed (the ALTER ships DEFAULT FALSE).
    db = SessionLocal()
    try:
        db.get(CouponRow, coupon_id).min_subtotal_approved = False
        db.commit()
    finally:
        db.close()

    # The board shows the saving with no threshold at all.
    board = {v["code"]: v for v in client.get("/api/coupons").json()}
    assert board["OLDBUNDLE"]["min_subtotal"] is None

    # The code check says "under review" instead of quoting a $40-as-€40 floor.
    refused = client.get("/api/orders/coupons/oldbundle", headers=headers)
    assert refused.status_code == 400
    assert "minimum spend is being reviewed" in refused.json()["detail"]

    # And the code can't be spent: not quoted, not placed — even by a basket
    # that would clear the old floor comfortably.
    _top_up_stock("butter-croissant")
    croissant = get_product("butter-croissant")
    big_basket = [{"product_id": croissant["id"], "quantity": 30}]
    quote = _quote(big_basket, coupon_code="OLDBUNDLE", token=token)
    assert quote.status_code == 400
    assert "being reviewed" in quote.json()["detail"]
    placed = client.post(
        "/api/orders",
        json=checkout_payload(coupon_code="OLDBUNDLE", items=big_basket),
        headers=headers,
    )
    assert placed.status_code == 400
    assert "being reviewed" in placed.json()["detail"]

    # The admin's re-save is the approval — and the threshold it approves is
    # enforced as euros from then on.
    resaved = client.put(
        f"/api/admin/coupons/{coupon_id}", json={"min_subtotal": 40.0}, headers=admin
    )
    assert resaved.json()["min_subtotal_approved"] is True
    preview = client.get("/api/orders/coupons/oldbundle", headers=headers).json()
    assert preview["min_subtotal"] == 40.0
    small = _quote(
        [{"product_id": croissant["id"], "quantity": 1}], coupon_code="OLDBUNDLE", token=token
    )
    assert small.status_code == 400
    assert small.json()["detail"].startswith("OLDBUNDLE needs a basket of at least €40.00")

    client.delete(f"/api/admin/coupons/{coupon_id}", headers=admin)


def test_bulk_enquiry_flow(caplog):
    import datetime
    import logging

    tomorrow = datetime.date.today() + datetime.timedelta(days=14)
    payload = {
        "name": "Nadia Karim",
        "email": "nadia@example.com",
        "phone": "+1 503 555 0142",
        "event_date": tomorrow.isoformat(),
        "event_type": "wedding",
        "guests": 120,
        "items_note": "120 croffles, 3 garden focaccias, 4 dozen doughnuts",
        "message": "Afternoon tea, needs delivery by 2pm",
    }

    # The phone number is required — quotes move faster by phone.
    missing_phone = client.post("/api/bulk-orders", json={k: v for k, v in payload.items() if k != "phone"})
    assert missing_phone.status_code == 422

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        created = client.post("/api/bulk-orders", json=payload)
    assert created.status_code == 201
    enquiry = created.json()
    assert enquiry["status"] == "new"
    assert enquiry["event_date"] == tomorrow.isoformat()

    # The asker hears the enquiry landed, not just the baker's desk.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "email to nadia@example.com" in logged
    assert "We've got your enquiry" in logged

    # yesterday's event is not something the bakery can bake for
    past = client.post(
        "/api/bulk-orders",
        json={**payload, "event_date": datetime.date.today().isoformat()},
    )
    assert past.status_code == 400

    # the enquiry rail is staff-only
    assert client.get("/api/admin/bulk-orders").status_code == 401
    assert client.get("/api/admin/bulk-orders", headers=auth_header(login_customer())).status_code == 403

    admin = admin_headers()
    listing = client.get("/api/admin/bulk-orders", headers=admin).json()
    assert any(e["id"] == enquiry["id"] for e in listing)

    moved = client.put(
        f"/api/admin/bulk-orders/{enquiry['id']}",
        json={"status": "quoted", "admin_note": "Quoted $480, waiting to hear back"},
        headers=admin,
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "quoted"
    assert moved.json()["admin_note"].startswith("Quoted")

    # the status filter narrows the rail
    fresh = client.get("/api/admin/bulk-orders", params={"status": "new"}, headers=admin).json()
    assert all(e["status"] == "new" for e in fresh)

    assert client.delete(f"/api/admin/bulk-orders/{enquiry['id']}", headers=admin).status_code == 200
    assert client.get("/api/admin/bulk-orders", params={"status": "quoted"}, headers=admin).json() == []


def test_bulk_enquiry_reply(caplog):
    """The baker answers a lead from the drawer: the reply is stored,
    emailed to the enquirer, and a first answer marks the lead contacted."""
    import datetime
    import logging

    tomorrow = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    enquiry = client.post(
        "/api/bulk-orders",
        json={
            "name": "Omar Haddad",
            "email": "omar@example.com",
            "phone": "+49 176 555 0177",
            "event_date": tomorrow,
            "event_type": "corporate",
            "guests": 40,
        },
    ).json()

    # The reply endpoint is staff-only.
    assert (
        client.post(
            f"/api/admin/bulk-orders/{enquiry['id']}/reply", json={"message": "hi"}
        ).status_code
        == 401
    )

    admin = admin_headers()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        replied = client.post(
            f"/api/admin/bulk-orders/{enquiry['id']}/reply",
            json={"message": "We can do 40 boxes for €6.50 each,\ndelivered by 11am. Shall I hold the date?"},
            headers=admin,
        )
    assert replied.status_code == 201
    updated = replied.json()
    assert updated["status"] == "contacted"  # first answer moves it off "new"
    assert [r["message"] for r in updated["replies"]].pop().startswith("We can do")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "email to omar@example.com" in logged
    assert "Re: your enquiry for" in logged

    # The conversation reads back on the rail; an empty reply never sends.
    listing = client.get("/api/admin/bulk-orders", headers=admin).json()
    mine = next(e for e in listing if e["id"] == enquiry["id"])
    assert len(mine["replies"]) == 1
    assert (
        client.post(
            f"/api/admin/bulk-orders/{enquiry['id']}/reply", json={"message": " "}, headers=admin
        ).status_code
        == 422
    )

    # Deleting the enquiry takes its conversation with it.
    assert client.delete(f"/api/admin/bulk-orders/{enquiry['id']}", headers=admin).status_code == 200


def test_home_content_testimonials_and_coming_soon():
    admin = admin_headers()

    # seeded bands are live on the public home page
    testimonials = client.get("/api/testimonials").json()
    assert len(testimonials) >= 2
    assert all(t["quote"] and t["name"] for t in testimonials)
    coming = client.get("/api/coming-soon").json()
    assert len(coming) >= 2

    # the baker adds a word from a customer (e.g. copied from a product review)
    created = client.post(
        "/api/admin/testimonials",
        json={
            "name": "Rafiq Chowdhury",
            "role": "First-time customer",
            "quote": "Ordered for my daughter's birthday and the box stole the show.",
            "rating": 5,
            "sort_order": 99,
        },
        headers=admin,
    )
    assert created.status_code == 201
    tid = created.json()["id"]
    assert any(t["id"] == tid for t in client.get("/api/testimonials").json())

    # shelving one takes it off the home page without deleting it
    client.put(f"/api/admin/testimonials/{tid}", json={"is_active": False}, headers=admin)
    assert all(t["id"] != tid for t in client.get("/api/testimonials").json())
    assert any(t["id"] == tid for t in client.get("/api/admin/testimonials", headers=admin).json())
    assert client.delete(f"/api/admin/testimonials/{tid}", headers=admin).status_code == 200

    # coming-soon works the same way
    item = client.post(
        "/api/admin/coming-soon",
        json={"title": "Hot cross buns", "eta_label": "Easter", "sort_order": 99},
        headers=admin,
    ).json()
    assert any(i["id"] == item["id"] for i in client.get("/api/coming-soon").json())
    updated = client.put(
        f"/api/admin/coming-soon/{item['id']}",
        json={"eta_label": "From Good Friday"},
        headers=admin,
    )
    assert updated.json()["eta_label"] == "From Good Friday"
    assert client.delete(f"/api/admin/coming-soon/{item['id']}", headers=admin).status_code == 200

    # neither endpoint is writable by the public
    assert client.post("/api/admin/testimonials", json={"name": "x", "quote": "hello there"}).status_code == 401


# ----------------------------------------------------------------------
# Production fresh-start seeding: catalog only, one real admin
# ----------------------------------------------------------------------

def _catalog_only_seed_env(tmp_path, monkeypatch, admin_email="owner@example.com", admin_password="OwnerPass123!"):
    """Point the seeder at a throwaway database configured like production."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import seed as seed_module
    from app.config import Settings
    from app.database import Base

    engine = create_engine(f"sqlite:///{(tmp_path / 'prod.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_module, "SessionLocal", session_factory)
    settings = Settings(
        app_env="production",
        secret_key="prod-secret-key-for-tests-only",
        seed_demo_data=False,
        admin_email=admin_email,
        admin_password=admin_password,
    )
    monkeypatch.setattr(seed_module, "get_settings", lambda: settings)
    return seed_module, session_factory


def test_production_seed_catalog_only(tmp_path, monkeypatch):
    from app.models import Category, ComingSoonItem, Coupon, Order, Product, Review, Testimonial, User
    from app.security import verify_password

    seed_module, Session = _catalog_only_seed_env(tmp_path, monkeypatch)
    seed_module.seed_db()

    db = Session()
    try:
        # The shop itself: shelves, bakes, the two promised vouchers.
        assert db.query(Product).count() == 25
        assert db.query(Category).count() == 3
        assert {c.code for c in db.query(Coupon).all()} == {"WELCOME10", "FRESHBAKE15"}

        # Exactly one login — the real baker, not the demo crowd.
        users = db.query(User).all()
        assert [u.email for u in users] == ["owner@example.com"]
        assert users[0].role == "admin"
        assert verify_password("OwnerPass123!", users[0].password_hash)

        # Nothing pretend anywhere.
        assert db.query(Order).count() == 0
        assert db.query(Review).count() == 0
        assert db.query(Testimonial).count() == 0
        assert db.query(ComingSoonItem).count() == 0
    finally:
        db.close()

    # A restart (restore path) re-promises the coupons but never sneaks the
    # demo bands into a production database.
    seed_module.seed_db()
    db = Session()
    try:
        assert db.query(Coupon).count() == 2
        assert db.query(Testimonial).count() == 0
        assert db.query(ComingSoonItem).count() == 0
        assert db.query(User).count() == 1
    finally:
        db.close()


def test_production_seed_requires_admin_credentials(tmp_path, monkeypatch):
    import pytest

    seed_module, _ = _catalog_only_seed_env(tmp_path, monkeypatch, admin_email="", admin_password="")
    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        seed_module.seed_db()


def test_production_refuses_demo_data(tmp_path, monkeypatch):
    import pytest

    from app import seed as seed_module
    from app.config import Settings

    settings = Settings(
        app_env="production",
        secret_key="prod-secret-key-for-tests-only",
        seed_demo_data=True,
    )
    monkeypatch.setattr(seed_module, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="SEED_DEMO_DATA"):
        seed_module.seed_db()


def test_database_url_normalization():
    """Platforms hand out postgres:// URLs; SQLAlchemy 2 needs the psycopg3 dialect."""
    from app.config import Settings

    assert (
        Settings(database_url="postgres://u:p@host/db").resolved_database_url
        == "postgresql+psycopg://u:p@host/db"
    )
    assert (
        Settings(database_url="postgresql://u:p@host/db").resolved_database_url
        == "postgresql+psycopg://u:p@host/db"
    )
    # A relative SQLite path stays anchored to the backend folder.
    assert Settings(database_url="sqlite:///./somewhere.db").resolved_database_url.endswith("/somewhere.db")


# ----------------------------------------------------------------------
# The last admin: the desk must never lock itself out
# ----------------------------------------------------------------------

def test_cannot_demote_the_last_admin():
    """Editing your own account must never leave the shop with no active
    admin — nobody could then reach the desk to promote anyone back."""
    headers = admin_headers()
    me = client.get("/api/auth/me", headers=headers).json()

    demoted = client.put(f"/api/admin/users/{me['id']}", json={"role": "customer"}, headers=headers)
    assert demoted.status_code == 400
    assert "no active admin" in demoted.json()["detail"]
    sidelined = client.put(f"/api/admin/users/{me['id']}", json={"is_active": False}, headers=headers)
    assert sidelined.status_code == 400
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "admin"

    # With a second admin in place, handing the desk over is allowed…
    customer = login_customer()
    customer_id = client.get("/api/auth/me", headers=auth_header(customer)).json()["id"]
    assert client.put(f"/api/admin/users/{customer_id}", json={"role": "admin"}, headers=headers).status_code == 200
    assert client.put(f"/api/admin/users/{me['id']}", json={"role": "customer"}, headers=headers).status_code == 200

    # …and the new admin can hand it straight back.
    new_admin = client.post("/api/auth/login", json=CUSTOMER).json()["access_token"]
    new_headers = auth_header(new_admin)
    assert client.put(f"/api/admin/users/{me['id']}", json={"role": "admin"}, headers=new_headers).status_code == 200
    assert client.put(f"/api/admin/users/{customer_id}", json={"role": "customer"}, headers=new_headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "admin"


def test_boot_restores_the_last_admin(tmp_path, monkeypatch):
    """The production incident itself: the only admin demotes their own
    account to customer. The next boot must promote ADMIN_EMAIL back."""
    from app.models import User

    seed_module, Session = _catalog_only_seed_env(tmp_path, monkeypatch)
    seed_module.seed_db()

    db = Session()
    try:
        db.query(User).filter_by(email="owner@example.com").one().role = "customer"
        db.commit()
    finally:
        db.close()

    seed_module.seed_db()  # next boot

    db = Session()
    try:
        assert db.query(User).filter_by(email="owner@example.com").one().role == "admin"
        assert db.query(User).filter(User.role == "admin").count() == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# The welcome voucher's small print, and the desk ping's email copy
# --------------------------------------------------------------------------

def _register_and_login(email: str, password: str) -> dict:
    """A fresh shopper of the test's own — coupon rules are per customer, so
    a test that spends a code can't share the suite's fixed customer."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Fiona",
            "last_name": "Firsttimer",
        },
    )
    assert response.status_code == 201
    token = client.post("/api/auth/login", json={"email": email, "password": password})
    return auth_header(token.json()["access_token"])


def _top_up_stock(slug: str, extra: int = 40) -> None:
    """The suite bakes its way through the croissants — a late test that
    still needs to place orders refills the case first."""
    from app.database import SessionLocal
    from app.models import Product as ProductRow

    db = SessionLocal()
    try:
        db.query(ProductRow).filter_by(slug=slug).one().stock += extra
        db.commit()
    finally:
        db.close()


def test_welcome_voucher_is_once_per_customer():
    """WELCOME10 follows its own small print: one order per customer. After
    redemption it leaves that shopper's voucher board and stops validating —
    until the order is cancelled, which hands the code back."""
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = _register_and_login("first-timer@example.com", "FirstTimer1!")

    board = client.get("/api/coupons", headers=headers).json()
    assert any(v["code"] == "WELCOME10" for v in board)

    first = client.post(
        "/api/orders", json=checkout_payload(coupon_code="WELCOME10"), headers=headers
    )
    assert first.status_code == 201

    # Used: gone from this shopper's board, and neither the preview nor a
    # second checkout will take it again.
    board = client.get("/api/coupons", headers=headers).json()
    assert all(v["code"] != "WELCOME10" for v in board)
    assert any(v["code"] == "FRESHBAKE15" for v in board)  # other codes stay
    assert client.get("/api/orders/coupons/WELCOME10", headers=headers).status_code == 404
    again = client.post(
        "/api/orders", json=checkout_payload(coupon_code="WELCOME10"), headers=headers
    )
    assert again.status_code == 404, again.json()
    assert "one-per-customer" in again.json()["detail"]

    # A guest still sees the code — only this shopper has used it.
    assert any(v["code"] == "WELCOME10" for v in client.get("/api/coupons").json())

    # Cancelling the order hands the code back.
    assert (
        client.post(f"/api/orders/{first.json()['id']}/cancel", headers=headers).status_code == 200
    )
    assert client.get("/api/orders/coupons/WELCOME10", headers=headers).status_code == 200
    assert any(v["code"] == "WELCOME10" for v in client.get("/api/coupons", headers=headers).json())


def test_admin_sets_and_clears_the_once_per_customer_flag():
    admin = admin_headers()
    minted = client.post(
        "/api/admin/coupons",
        json={"code": "ONCEONLY20", "percent_off": 20, "once_per_customer": True},
        headers=admin,
    )
    assert minted.status_code == 201
    assert minted.json()["once_per_customer"] is True

    listing = client.get("/api/admin/coupons", headers=admin).json()
    welcome = next(c for c in listing if c["code"] == "WELCOME10")
    assert welcome["once_per_customer"] is True  # seeded with its small print
    freshbake = next(c for c in listing if c["code"] == "FRESHBAKE15")
    assert freshbake["once_per_customer"] is False

    cleared = client.put(
        f"/api/admin/coupons/{minted.json()['id']}",
        json={"once_per_customer": False},
        headers=admin,
    )
    assert cleared.json()["once_per_customer"] is False


def test_every_desk_ping_also_lands_in_the_admin_inbox(caplog):
    """The bell is for the desk; the same ping now travels to the active
    admins' inbox — new orders, new customers and catering leads included."""
    import datetime
    import logging

    tomorrow = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        headers = auth_header(login_customer())
        placed = client.post("/api/orders", json=checkout_payload(), headers=headers)
        assert placed.status_code == 201, placed.json()
        client.post("/api/auth/register", json={
            "email": "another-regular@example.com",
            "password": "AnotherRegular1!",
            "first_name": "Ayaan",
            "last_name": "Regular",
        })
        client.post("/api/bulk-orders", json={
            "name": "Party Planner",
            "email": "planner@example.com",
            "phone": "+1 503 555 0177",
            "event_date": tomorrow,
            "event_type": "birthday",
            "guests": 30,
            "items_note": "3 dozen doughnuts",
            "message": "Morning delivery please",
        })

    logged = "\n".join(r.getMessage() for r in caplog.records)
    # every ping's email copy is addressed to the active admins
    assert "email to admin@djinisbakehouse.com" in logged
    assert "New order DJ-" in logged
    assert "New customer — Ayaan Regular" in logged
    assert "Bulk enquiry from Party Planner" in logged
    # and the customers' own mail still goes only to them
    assert "email to another-regular@example.com" not in logged


def test_reconciliation_sweep_heals_a_webhook_outage(caplog):
    """A dead webhook endpoint must not freeze the order book. The sweep
    asks Stripe directly about card orders still pending past their pay-by
    window: one that was actually paid lands (receipt and all), one whose
    window lapsed cancels with the stock returned."""
    import datetime
    import logging

    from app.database import SessionLocal
    from app.models import Order as OrderRow
    from app.routers.payments import reconcile_stale_card_orders

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())

    paid = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    expired = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    assert paid["payment_status"] == "pending" and expired["payment_status"] == "pending"

    # Stripe knows; the webhook never arrives.
    fake_sessions[last_session_id(paid["id"])]["payment_status"] = "paid"
    fake_sessions[last_session_id(paid["id"])]["payment_intent"] = "pi_recovered"
    fake_sessions[last_session_id(expired["id"])]["status"] = "expired"

    stock_before = get_product("butter-croissant")["stock"]

    # Both orders must sit past the pay-by window for the sweep to look.
    db = SessionLocal()
    try:
        stale = datetime.datetime.utcnow() - datetime.timedelta(minutes=60)
        db.query(OrderRow).filter(OrderRow.id.in_([paid["id"], expired["id"]])).update(
            {"created_at": stale}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        reconcile_stale_card_orders()

    healed = client.get(f"/api/orders/{paid['id']}", headers=headers).json()
    assert healed["payment_status"] == "paid"
    closed = client.get(f"/api/orders/{expired['id']}", headers=headers).json()
    assert closed["status"] == "cancelled"
    assert "expired" in closed["timeline"][-1]["comment"]
    # the lapsed order's croissants are back in the case; the paid one's aren't
    assert get_product("butter-croissant")["stock"] == stock_before + 2
    # the drained receipt really sent (log-only SMTP in tests)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"Receipt {paid['order_number']}" in logged

    # A second sweep finds nothing left to do and changes nothing.
    reconcile_stale_card_orders()
    assert client.get(f"/api/orders/{paid['id']}", headers=headers).json()["payment_status"] == "paid"


def test_webhook_and_sweep_converging_on_one_expired_order_return_stock_once():
    """The race the row lock closes: Stripe's expiry webhook and the
    reconciliation sweep judging the same stale order. The second caller
    must re-read the order under the lock, find it cancelled, and return
    nothing — the case must not gain the boxes twice. (SQLite ignores FOR
    UPDATE, so this pins the guards the lock makes authoritative; the
    locking itself is Postgres's to enforce.)"""
    from app.database import SessionLocal
    from app.routers.payments import _cancel_expired_session

    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    session_id = last_session_id(order["id"])

    # the webhook's word lands first
    expire_order_via_webhook(order["id"])
    detail = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert detail["status"] == "cancelled"
    stock_after_cancel = get_product("butter-croissant")["stock"]

    # the sweep round converges on the same session a moment later
    db = SessionLocal()
    try:
        _cancel_expired_session({"id": session_id}, db)
    finally:
        db.close()

    assert get_product("butter-croissant")["stock"] == stock_after_cancel
    after = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    expired_events = [e for e in after["timeline"] if "Payment window expired" in e["comment"]]
    assert len(expired_events) == 1


def test_expiry_event_for_an_already_paid_order_changes_nothing():
    """Paid-after-all beats the clock: an expiry event arriving for a
    session that completed must leave the paid order standing — no
    cancellation, no stock return."""
    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()

    pay_order_via_webhook(order["id"])
    stock_when_paid = get_product("butter-croissant")["stock"]

    expire_order_via_webhook(order["id"])

    after = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert after["payment_status"] == "paid"
    assert after["status"] == "pending"  # awaiting the ovens, not cancelled
    assert get_product("butter-croissant")["stock"] == stock_when_paid


def test_customer_and_admin_cancellation_converge_on_one_refund():
    """Both cancel doors, one outcome. Whichever lands first cancels and
    refunds; the other must find the order cancelled and do nothing — one
    refund call, one cancellation on the timeline, stock back once. (The
    routes re-read the order under FOR UPDATE before judging; SQLite
    ignores FOR UPDATE, so this pins the guard discipline the lock makes
    authoritative — the locking itself is Postgres's to enforce.)"""
    admin = admin_headers()
    customer = auth_header(login_customer())
    _top_up_stock("butter-croissant")

    # the customer's click lands first
    first = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
    pay_order_via_webhook(first["id"])
    stock_when_paid = get_product("butter-croissant")["stock"]

    cancelled = client.post(f"/api/orders/{first['id']}/cancel", headers=customer).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["payment_status"] == "refunded"  # not baking yet → no fee
    stock_after = get_product("butter-croissant")["stock"]
    assert stock_after == stock_when_paid + 2

    # the admin dial arrives a moment later — refused, nothing re-done
    late = client.put(
        f"/api/admin/orders/{first['id']}/status", json={"status": "cancelled"}, headers=admin
    )
    assert late.status_code == 400
    assert get_product("butter-croissant")["stock"] == stock_after
    detail = client.get(f"/api/orders/{first['id']}", headers=customer).json()
    assert len([e for e in detail["timeline"] if e["comment"].startswith("Cancelled")]) == 1
    assert len([c for c in gateway_calls if c[0] == "refund" and c[1] == f"pi_fake_{first['id']}"]) == 1

    # the mirror: the desk lands first, the customer's click is refused
    second = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
    pay_order_via_webhook(second["id"])
    from_desk = client.put(
        f"/api/admin/orders/{second['id']}/status", json={"status": "cancelled"}, headers=admin
    )
    assert from_desk.status_code == 200
    assert client.post(f"/api/orders/{second['id']}/cancel", headers=customer).status_code == 400
    assert len([c for c in gateway_calls if c[0] == "refund" and c[1] == f"pi_fake_{second['id']}"]) == 1


def test_cancellation_restores_every_line_via_additive_update():
    """Stock goes back per line item, straight from the database's own
    arithmetic (UPDATE stock = stock + qty), so a two-product order
    restores both products and nothing is computed from a stale ORM value."""
    customer = auth_header(login_customer())
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    croissant = get_product("butter-croissant")
    puff = get_product("beef-curry-puff")

    order = client.post(
        "/api/orders",
        json=checkout_payload(
            items=[
                {"product_id": croissant["id"], "quantity": 2},
                {"product_id": puff["id"], "quantity": 3},
            ]
        ),
        headers=customer,
    ).json()
    assert get_product("butter-croissant")["stock"] == croissant["stock"] - 2
    assert get_product("beef-curry-puff")["stock"] == puff["stock"] - 3

    cancelled = client.post(f"/api/orders/{order['id']}/cancel", headers=customer)
    assert cancelled.status_code == 200
    assert get_product("butter-croissant")["stock"] == croissant["stock"]
    assert get_product("beef-curry-puff")["stock"] == puff["stock"]


def test_locked_refetch_refreshes_an_already_loaded_order():
    """The locked re-reads must see the database's truth, not the identity
    map's copy. On a repeated select SQLAlchemy returns the already-loaded
    object and by default keeps its stale attributes — populate_existing is
    what makes the guards under the lock judge fresh values, and a row that
    vanished between the two reads comes back as None. The refresh is ORM
    behaviour, so SQLite proves it here; only the FOR UPDATE serialisation
    is Postgres's to enforce."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow
    from app.routers.orders import _lock_for_cancellation

    headers = auth_header(login_customer())
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()

    db = SessionLocal()
    try:
        loaded = db.get(OrderRow, order["id"])
        assert loaded.payment_status == "pending"

        # another request (its own session) changes the row behind us
        other = SessionLocal()
        try:
            other.get(OrderRow, order["id"]).payment_status = "paid"
            other.commit()
        finally:
            other.close()

        # this session's identity map still holds the stale value — even a
        # plain db.get re-reads nothing
        assert db.get(OrderRow, order["id"]).payment_status == "pending"

        # the locked re-fetch refreshes the very same object in place
        refreshed = _lock_for_cancellation(loaded, db)
        assert refreshed is loaded
        assert loaded.payment_status == "paid"  # the database's word, not the cache's

        # and a row that vanished between lookup and lock is None, not a ghost
        other = SessionLocal()
        try:
            other.delete(other.get(OrderRow, order["id"]))
            other.commit()
        finally:
            other.close()
        assert _lock_for_cancellation(loaded, db) is None
    finally:
        db.close()


def test_confirm_after_a_webhook_lands_mid_request_does_not_double_receipt(caplog, monkeypatch):
    """The race the locked re-read closes: the webhook landing while
    confirm's retrieve_session is still on the wire. The fake gateway
    delivers the webhook first — fully committed — then hands confirm a
    paid session; confirm must find the order already paid and record
    nothing more: one payment-received event, one receipt. (The two
    requests run sequentially on SQLite — this proves the stale-read half
    and the guard; the FOR UPDATE serialisation is Postgres's.)"""
    import logging

    headers = auth_header(login_customer())
    _top_up_stock("butter-croissant")
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    order_id = order["id"]

    def webhook_lands_then_retrieve(session_id):
        pay_order_via_webhook(order_id)  # completes fully before confirm resumes
        return dict(fake_sessions[session_id])

    monkeypatch.setattr(stripe_gateway, "retrieve_session", webhook_lands_then_retrieve)

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        confirmed = client.post(f"/api/payments/confirm/{order_id}", headers=headers)

    assert confirmed.status_code == 200
    assert confirmed.json()["payment_status"] == "paid"
    detail = client.get(f"/api/orders/{order_id}", headers=headers).json()
    received = [e for e in detail["timeline"] if e["comment"] == "Payment received — paid by card"]
    assert len(received) == 1
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert logged.count(f"Receipt {order['order_number']}") == 1


def test_orders_remember_the_currency_they_were_placed_in():
    """The foundation of the euro migration: every order carries the charge
    currency in force when it was placed. Since the 2026-11-05 cutover the
    shop charges eur, so that is what new orders say — and a dollar order
    from before the cutover keeps its dollars exactly as placed, whichever
    way the shop charges now."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow

    headers = auth_header(login_customer())
    _top_up_stock("butter-croissant")

    euro = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    assert euro["currency"] == "eur"
    assert client.get(f"/api/orders/{euro['id']}", headers=headers).json()["currency"] == "eur"

    # a pre-cutover order, placed back when the shop charged usd. Checkout
    # can't mint those any more (EUR-only by owner decision), so history is
    # written the honest way: directly, as the migration itself would have.
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=euro["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    # history keeps its own money through every read the shop can do
    assert client.get(f"/api/orders/{euro['id']}", headers=headers).json()["currency"] == "usd"
    admin = client.get("/api/admin/orders", headers=admin_headers()).json()
    by_id = {row["id"]: row["currency"] for row in admin["orders"]}
    assert by_id[euro["id"]] == "usd"

    # and orders placed today still say eur, in the same listing
    newer = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    admin = client.get("/api/admin/orders", headers=admin_headers()).json()
    by_id = {row["id"]: row["currency"] for row in admin["orders"]}
    assert by_id[euro["id"]] == "usd"
    assert by_id[newer["id"]] == "eur"


def test_the_receipt_email_speaks_each_orders_currency():
    """A receipt is written in the money its order was placed in — a dollar
    order keeps its dollar receipt even after the shop starts charging euro."""
    from app.email import order_receipt_email

    def receipt(currency):
        return {
            "email": "taster@example.com",
            "first_name": "Taster",
            "order_number": "DJ-TEST-0001",
            "placed_at": "Nov 04, 2026, 8:00 AM",
            "items": [{"name": "Butter Croissant", "quantity": 2, "subtotal": 7.5}],
            "subtotal": 7.5,
            "discount": 0.0,
            "coupon_code": None,
            "tax": 0.66,
            "shipping": 0.0,
            "total": 8.16,
            "payment_method": "card",
            "payment_status": "paid",
            "currency": currency,
        }

    subject, html, text = order_receipt_email(receipt("usd"))
    assert "$7.50" in text and "$7.50" in html
    assert "€" not in text and "€" not in html

    subject, html, text = order_receipt_email(receipt("eur"))
    assert "€7.50" in text and "€7.50" in html
    assert "$" not in text and "$" not in html


def test_the_public_store_carries_the_shop_money_rules():
    """/api/store is the storefront's single source of the money rules — the
    basket's arithmetic and symbols follow it, so it must mirror the settings
    the server actually charges with."""
    from app.config import get_settings

    store = client.get("/api/store").json()
    settings = get_settings()
    assert store["currency"] == settings.stripe_currency == "eur"
    assert store["tax_rate"] == settings.tax_rate
    assert store["shipping_fee"] == settings.shipping_fee
    assert store["free_shipping_threshold"] == settings.free_shipping_threshold


def test_a_misconfigured_charge_currency_is_not_published_to_the_storefront(monkeypatch):
    """Checkout refuses an unchargeable currency with a 503 (routers/orders.
    _charge_currency) — the storefront must not go on labelling every price
    with the same broken symbol while nothing can be bought. /api/store
    answers None and the frontend keeps its own euro default."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "stripe_currency", "usd", raising=True)
    assert client.get("/api/store").json()["currency"] is None

    monkeypatch.setattr(get_settings(), "stripe_currency", " eur ", raising=True)
    assert client.get("/api/store").json()["currency"] == "eur"


def test_refunding_a_historical_dollar_order_keeps_its_cents():
    """Refunds are minor units of the order's own currency: a dollar order
    paid before the euro cutover is refunded in exactly the dollars it was
    charged — nothing is converted, nothing recomputed."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow

    headers = auth_header(login_customer())
    _top_up_stock("butter-croissant")
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    pay_order_via_webhook(order["id"])

    # it becomes pre-cutover history the honest way: this order was placed
    # and paid back when the shop charged usd, so its row says so
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=order["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    calls_before = len(gateway_calls)  # the recorder spans the whole suite
    cancelled = client.put(
        f"/api/admin/orders/{order['id']}/status",
        json={"status": "cancelled"},
        headers=admin_headers(),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["payment_status"] == "refunded"
    assert cancelled.json()["currency"] == "usd"

    refunds = [call for call in gateway_calls[calls_before:] if call[0] == "refund"]
    assert len(refunds) == 1
    assert refunds[0][2] == round(float(order["total"]) * 100)  # the original usd cents


def test_stats_split_money_by_currency_never_summing_it():
    """A dollar book and a euro book side by side: the headline tiles
    describe the active currency only, and every currency's own revenue,
    average and order count ride in revenue_by_currency. No total anywhere
    adds dollars to euros."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())

    def bucket(code):
        stats = client.get("/api/admin/stats", headers=admin_headers()).json()
        return next((c for c in stats["revenue_by_currency"] if c["currency"] == code), None)

    usd_before = bucket("usd") or {
        "total_revenue": 0.0, "order_count": 0, "refunded_revenue": 0.0
    }
    eur_before = bucket("eur") or {
        "total_revenue": 0.0, "order_count": 0, "refunded_revenue": 0.0
    }

    # The dollar book is history now — pre-cutover orders written directly,
    # the way checkout can no longer produce (EUR-only since 2026-11-05).
    dollar = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=dollar["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    euro = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    assert euro["currency"] == "eur"

    stats = client.get("/api/admin/stats", headers=admin_headers()).json()
    by_code = {c["currency"]: c for c in stats["revenue_by_currency"]}

    # Each currency's bucket grew by exactly its own new order.
    assert by_code["usd"]["total_revenue"] == round(
        usd_before["total_revenue"] + dollar["total"], 2
    )
    assert by_code["usd"]["order_count"] == usd_before["order_count"] + 1
    assert by_code["eur"]["total_revenue"] == round(
        eur_before["total_revenue"] + euro["total"], 2
    )
    assert by_code["eur"]["order_count"] == eur_before["order_count"] + 1
    assert by_code["eur"]["total_revenue"] != by_code["usd"]["total_revenue"]

    # The headline money is the active currency's slice alone, and the
    # average is per-currency arithmetic, never a mixed mean.
    assert stats["total_revenue"] == by_code["eur"]["total_revenue"]
    assert stats["avg_order_value"] == by_code["eur"]["avg_order_value"]
    assert by_code["eur"]["avg_order_value"] == round(
        by_code["eur"]["total_revenue"] / by_code["eur"]["order_count"], 2
    )

    # Counts are facts, not money — they may span currencies.
    assert stats["order_count"] >= by_code["usd"]["order_count"] + by_code["eur"]["order_count"]


def test_lifetime_spend_never_mixes_currencies():
    """A customer who shopped before and after the cutover keeps two honest
    numbers, not one invented sum: lifetime_spend is the active currency's
    spend, and the full split rides alongside. The order count and last
    visit span everything — they're facts, not money."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = _register_and_login("mixed-wallet@example.com", "MixedWallet1!")

    # the pre-cutover dollar purchase, written directly as history
    dollar = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=dollar["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    euro = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()

    page = client.get(
        "/api/admin/users",
        params={"search": "mixed-wallet@example.com"},
        headers=admin_headers(),
    ).json()
    (customer,) = page["items"]
    assert customer["order_count"] == 2
    assert customer["lifetime_spend"] == euro["total"]  # the active currency's slice
    assert customer["lifetime_spend_by_currency"] == {
        "usd": dollar["total"],
        "eur": euro["total"],
    }


def test_checkout_pauses_when_the_charge_currency_is_unusable(monkeypatch):
    """A misconfigured charge currency (typo, unset, unchargeable code)
    pauses checkout entirely — cash included — rather than stamping orders
    with money nothing can charge or report. The quote promises nothing
    either: it prices exactly what placement would refuse. USD counts as
    unchargeable since the EUR-only cutover: a relapse to the old setting
    must pause the shop, never quietly charge dollars again."""
    from app.routers import orders as orders_router

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())
    orders_before = client.get("/api/orders", headers=headers).json()["pagination"]["total_items"]

    for unusable in ("gbp", "usd"):
        monkeypatch.setattr(orders_router.settings, "stripe_currency", unusable, raising=True)

        refused = client.post(
            "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
        )
        assert refused.status_code == 503
        assert "paused" in refused.json()["detail"]

        quote = client.post("/api/orders/quote", json={"items": checkout_payload()["items"]})
        assert quote.status_code == 503

    orders_after = client.get("/api/orders", headers=headers).json()["pagination"]["total_items"]
    assert orders_after == orders_before  # nothing was written


def test_a_new_pay_link_refuses_an_unchargeable_order_currency(monkeypatch):
    """A historical order whose stored currency can't be charged is refused
    untouched — not cancelled, not relabelled, never charged in a currency
    it wasn't placed in. This path runs the real gateway's validation (the
    suite's fake wouldn't refuse anything)."""
    import importlib.util
    from pathlib import Path

    from app.database import SessionLocal
    from app.models import Order as OrderRow

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())
    # Both orders are placed while the fake gateway is in place: the fresh
    # real gateway that arrives below refuses unchargeable currencies, and
    # neither of these should be refused at placement.
    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    dollar = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    sessions_before = len(fake_sessions)

    # The order's stored currency goes bad — a currency the pipeline never
    # supported, exactly the shape a botched cutover would leave behind —
    # and the dollar order becomes pre-cutover history (placed as usd).
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=order["id"]).one().currency = "gbp"
        db.query(OrderRow).filter_by(id=dollar["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    # The real gateway (fresh module, like the signed-webhook test loads it)
    # validates before Stripe is touched; its exception class is patched in
    # so the endpoint's except-clause catches the very class it raises.
    spec = importlib.util.spec_from_file_location(
        "app._stripe_gateway_currency",
        Path(__file__).resolve().parents[1] / "app" / "stripe_gateway.py",
    )
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(stripe_gateway, "create_checkout_session", fresh.create_checkout_session)
    monkeypatch.setattr(stripe_gateway, "InvalidOrderCurrency", fresh.InvalidOrderCurrency)

    refused = client.post(f"/api/payments/{order['id']}/session", headers=headers)
    assert refused.status_code == 400
    assert "can no longer be charged" in refused.json()["detail"]

    detail = client.get(f"/api/orders/{order['id']}", headers=headers).json()
    assert detail["status"] == "pending"  # refused, not cancelled
    assert detail["currency"] == "gbp"  # history isn't rewritten
    assert len(fake_sessions) == sessions_before  # and no session was minted

    # The pre-cutover currency itself: a usd order stays readable everywhere
    # but can never be charged again — its pay link refuses too.
    refused_usd = client.post(f"/api/payments/{dollar['id']}/session", headers=headers)
    assert refused_usd.status_code == 400
    assert "can no longer be charged" in refused_usd.json()["detail"]


def test_pay_links_pause_with_checkout_while_delivery_pricing_is_unapproved(monkeypatch):
    """The pay-link route shares checkout's fail-closed gate: while the
    shop's delivery pricing awaits approved EUR figures, no new Stripe
    session is minted for any order — not even a valid euro pending one.
    The stored totals are never recalculated; approving the rules simply
    lets charging resume."""
    from app.database import SessionLocal
    from app.models import Order as OrderRow
    from app.routers import orders as orders_router

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())
    placed = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    assert placed["payment_status"] == "pending"
    assert placed["payment_url"]  # the fake gateway minted a session at placement
    session_at_placement = last_session_id(placed["id"])

    def sessions_for_order():
        return [sid for sid in fake_sessions if sid.startswith(f"cs_fake_{placed['id']}_")]

    assert len(sessions_for_order()) == 1

    # Checkout's pause (no approved free-delivery threshold) must reach the
    # pay link too — a fresh session would charge unapproved pricing.
    monkeypatch.setattr(orders_router.settings, "free_shipping_threshold", None, raising=True)
    paused = client.post(f"/api/payments/{placed['id']}/session", headers=headers)
    assert paused.status_code == 503
    assert "paused" in paused.json()["detail"]
    assert len(sessions_for_order()) == 1  # nothing was minted

    db = SessionLocal()
    try:
        stored = db.get(OrderRow, placed["id"])
        assert stored.stripe_session_id == session_at_placement  # untouched
        # totals never recalculated (Numeric comes back as Decimal)
        assert float(stored.total) == placed["total"]
    finally:
        db.close()

    # Rules approved again (the suite's threshold): the same order's pay
    # link works and points at a fresh session.
    monkeypatch.setattr(orders_router.settings, "free_shipping_threshold", 30.00, raising=True)
    resumed = client.post(f"/api/payments/{placed['id']}/session", headers=headers)
    assert resumed.status_code == 201
    assert resumed.json()["payment_url"]
    assert len(sessions_for_order()) == 2
    db = SessionLocal()
    try:
        assert db.get(OrderRow, placed["id"]).stripe_session_id != session_at_placement
        assert float(db.get(OrderRow, placed["id"]).total) == placed["total"]
    finally:
        db.close()


def test_the_orders_export_names_each_rows_currency():
    """The bookkeeping CSV says which money each row's figures read in —
    after any cutover, no spreadsheet ever has to guess."""
    import csv
    import io as _io

    from app.database import SessionLocal
    from app.models import Order as OrderRow

    # one pre-cutover dollar order of our own, so this test needs no
    # ordering contract with the currency tests above
    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())
    dollar = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=dollar["id"]).one().currency = "usd"
        db.commit()
    finally:
        db.close()

    response = client.get("/api/admin/orders/export", headers=admin_headers())
    assert response.status_code == 200
    rows = list(csv.reader(_io.StringIO(response.text)))
    header, data = rows[0], rows[1:]
    assert header[-1] == "currency"
    assert data  # the suite has placed orders by now
    currency_column = header.index("currency")
    # Every row names its money — never blank, and honest about what's
    # stored: the deliberately corrupted 'gbp' order from the pay-link test
    # above reports as gbp, visibly not summed with its neighbours.
    codes = {row[currency_column] for row in data}
    assert all(code and code == code.strip() for code in codes)
    assert "eur" in codes
    assert "usd" in codes
    assert "gbp" in codes


def test_the_orders_export_blanks_read_as_unknown():
    """A blank stored currency — the corrupted-row shape — exports as
    "unknown", the same honest label the stats buckets use, never an
    empty cell a spreadsheet would have to guess about."""
    import csv
    import io as _io

    from app.database import SessionLocal
    from app.models import Order as OrderRow

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    headers = auth_header(login_customer())
    order = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()

    db = SessionLocal()
    try:
        db.query(OrderRow).filter_by(id=order["id"]).one().currency = ""
        db.commit()
    finally:
        db.close()

    response = client.get("/api/admin/orders/export", headers=admin_headers())
    assert response.status_code == 200
    rows = list(csv.reader(_io.StringIO(response.text)))
    header, data = rows[0], rows[1:]
    column = header.index("currency")
    row = next(r for r in data if r[0] == order["order_number"])
    assert row[column] == "unknown"  # named, not blank — and never usd/eur


def test_the_currency_column_migrates_a_legacy_sqlite_database(tmp_path, monkeypatch):
    """A database from before the currency column boots through the additive
    migration: the column lands and every existing row reads as euro, the
    shop's one charge currency since the 2026-11-05 cutover. (SQLite proves
    the DDL runs and backfills; the statement itself is pinned to the
    Postgres-safe shape in the test below — the boolean-default lesson from
    2026-11-02.)"""
    import sqlalchemy as sa

    from app import _upgrade_schema

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE orders (id INTEGER PRIMARY KEY, total NUMERIC(10,2))"))
        conn.execute(sa.text("INSERT INTO orders (id, total) VALUES (1, 12.00)"))
        # the boot-time data fixes touch categories, store_settings and
        # users unconditionally
        conn.execute(sa.text("CREATE TABLE categories (id INTEGER PRIMARY KEY, description VARCHAR(255))"))
        conn.execute(sa.text(
            "CREATE TABLE store_settings (id INTEGER PRIMARY KEY, announcement VARCHAR(255))"
        ))
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(sa.text(
            "INSERT INTO users (id, email) VALUES (7, 'deleted-account-7@customers.invalid')"
        ))

    monkeypatch.setattr("app.engine", engine)
    _upgrade_schema()

    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT id, total, currency FROM orders")).fetchall()
        assert rows == [(1, 12.0, "eur")]
        # the poisoned shell address is rewritten to the schema-valid
        # placeholder — one such row must not 500 the Users roll forever
        fixed = conn.execute(sa.text("SELECT email FROM users")).fetchall()
        assert fixed == [("deleted-account-7@deleted.djinisbakehouse.com",)]
    engine.dispose()


def test_the_currency_ddl_keeps_its_postgres_safe_shape():
    """The additive column must stay a VARCHAR with a quoted string default —
    an integer default or bare boolean is exactly the class of statement that
    boots on SQLite and dies on Postgres."""
    from app import _ADDITIVE_COLUMNS

    assert (
        _ADDITIVE_COLUMNS["orders"]["currency"]
        == "ALTER TABLE orders ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'eur'"
    )


def test_checkout_stays_paused_until_delivery_pricing_is_approved(monkeypatch):
    """The EUR cutover leaves the delivery rules unapproved (the old $5.99
    fee / $50 free-delivery figures are NOT approved euro): while either is
    unset, quotes and placement refuse fail-closed — the shop would rather
    pause than charge an invented delivery price. Cash included, and nothing
    is written."""
    from app.routers import orders as orders_router

    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())
    orders_before = client.get("/api/orders", headers=headers).json()["pagination"]["total_items"]

    monkeypatch.setattr(orders_router.settings, "shipping_fee", None, raising=True)
    monkeypatch.setattr(orders_router.settings, "free_shipping_threshold", None, raising=True)

    quote = client.post("/api/orders/quote", json={"items": checkout_payload()["items"]})
    assert quote.status_code == 503
    assert "paused" in quote.json()["detail"]

    refused = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    )
    assert refused.status_code == 503

    # one of the two coming back is still enough to stay paused
    monkeypatch.setattr(orders_router.settings, "shipping_fee", 3.50, raising=True)
    still = client.post("/api/orders/quote", json={"items": checkout_payload()["items"]})
    assert still.status_code == 503

    orders_after = client.get("/api/orders", headers=headers).json()["pagination"]["total_items"]
    assert orders_after == orders_before  # nothing was written


def test_a_paused_checkout_leaves_no_address_behind(monkeypatch):
    """The delivery-pricing 503 fires after the order's new address has
    already been flushed (the order row needs its id) — the session teardown
    is what keeps that address from persisting behind a paused shop. Pinned
    here so a future reorder of the flush and the price check can't start
    orphaning addresses on every refused checkout."""
    from sqlalchemy import func, select
    from app.database import SessionLocal
    from app.models import Address as AddressRow
    from app.routers import orders as orders_router

    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())

    def address_rows():
        db = SessionLocal()
        try:
            return db.scalar(select(func.count()).select_from(AddressRow))
        finally:
            db.close()

    before = address_rows()

    monkeypatch.setattr(orders_router.settings, "free_shipping_threshold", None, raising=True)
    refused = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    )
    assert refused.status_code == 503
    assert address_rows() == before  # the flushed address rolled back with the order


def test_no_tax_is_charged_or_shown_while_no_rate_is_approved(monkeypatch):
    """VAT treatment is pending professional review: with no approved rate,
    orders carry no tax at all — the quote, the order and the receipt simply
    omit it. Nothing may read as a 0%-VAT decision."""
    from app.email import order_receipt_email
    from app.routers import orders as orders_router

    _top_up_stock("butter-croissant")
    headers = auth_header(login_customer())

    monkeypatch.setattr(orders_router.settings, "tax_rate", None, raising=True)

    quote = client.post("/api/orders/quote", json={"items": checkout_payload()["items"]}).json()
    assert quote["tax"] == 0

    order = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    assert order["tax"] == 0

    # the receipt drops the row entirely — a "Tax €0.00" line would read as
    # a 0%-VAT claim the shop hasn't made
    subject, html, text = order_receipt_email(
        {
            "email": "taster@example.com",
            "first_name": "Taster",
            "order_number": "DJ-TEST-0002",
            "placed_at": "Nov 05, 2026, 8:00 AM",
            "items": [{"name": "Butter Croissant", "quantity": 2, "subtotal": 7.5}],
            "subtotal": 7.5,
            "discount": 0.0,
            "coupon_code": None,
            "tax": 0.0,
            "shipping": 3.5,
            "total": 11.0,
            "payment_method": "card",
            "payment_status": "paid",
            "currency": "eur",
        }
    )
    assert "Tax" not in text and "Tax" not in html


def test_money_formats_euro_by_default():
    """The shop's one money formatter defaults to euro — the currency every
    new figure is written in since the cutover — and a missing or blank
    code falls back to euro too, never silently to dollars."""
    from app.money import money

    assert money(7.5) == money(7.5, "eur")
    assert "7.50" in money(7.5) and money(7.5) != money(7.5, "usd")

    # a missing or blank currency must not quietly print a dollar figure
    assert money(7.5, None) == "€7.50"
    assert money(7.5, "") == "€7.50"
    assert money(7.5, "   ") == "€7.50"
    assert money(7.5, "eur") == "€7.50"
    # named history keeps its own symbol
    assert money(7.5, "usd") == "$7.50"


# --------------------------------------------------------------------------
# The VAT foundation (Phase 2B): the engine in app/vat.py, classification
# rules, and the snapshot columns — checkout itself is untouched.
# --------------------------------------------------------------------------

import datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402


def _vat_product(**overrides):
    """A classification stand-in: the engine only reads four attributes off
    a product, so rules can be pinned without touching the shop's catalog."""
    product = SimpleNamespace(
        name="Whatever",
        vat_category=None,
        vat_review_status="approved",
        milk_content_percent=None,
    )
    for key, value in overrides.items():
        setattr(product, key, value)
    return product


def test_vat_math_rounds_cents_half_up():
    """The engine's arithmetic in miniature: Decimal only, cents, half-up —
    1.50 net at 7 % is exactly 0.105 of tax, and half-up makes it 0.11 where
    banker's rounding (and float round()) would say 0.10."""
    from app.vat import add_vat, extract_vat, round_cents

    assert round_cents(Decimal("0.125")) == Decimal("0.13")

    # the Phase 2A worked example's lines, computed the way the engine does
    assert extract_vat(Decimal("6.75"), Decimal("7")) == (Decimal("6.31"), Decimal("0.44"))
    assert extract_vat(Decimal("3.60"), Decimal("7")) == (Decimal("3.36"), Decimal("0.24"))
    assert extract_vat(Decimal("2.25"), Decimal("19")) == (Decimal("1.89"), Decimal("0.36"))

    # net + vat always reassembles the gross to the cent
    net, vat = extract_vat(Decimal("16.50"), Decimal("7"))
    assert net + vat == Decimal("16.50")

    # the exclusive interface — the shape of the legacy register math
    assert add_vat(Decimal("1.50"), Decimal("7")) == (Decimal("1.61"), Decimal("0.11"))
    assert add_vat(Decimal("20.00"), Decimal("8.75")) == (Decimal("21.75"), Decimal("1.75"))


def test_vat_rates_resolve_effectively_dated():
    """Percentages come from the vat_rates table, effective-dated: a rate
    change is a new row, and dates before any row refuse rather than guess.
    The 2026 rows themselves were seeded by the app's own boot."""
    from app.database import SessionLocal
    from app.models import VatRate
    from app.vat import VatRateNotConfiguredError, effective_rate

    db = SessionLocal()
    try:
        assert effective_rate("REDUCED", datetime.date(2026, 6, 1), db) == Decimal("7.00")
        assert effective_rate("STANDARD", datetime.date(2026, 6, 1), db) == Decimal("19.00")
        assert effective_rate("REDUCED", datetime.date.today(), db) == Decimal("7.00")

        with pytest.raises(VatRateNotConfiguredError):
            effective_rate("REDUCED", datetime.date(2025, 12, 31), db)

        # a hypothetical 2027 change: newer row wins from its date, older
        # dates keep the rate that was in force when they were priced
        db.add(VatRate(code="REDUCED", valid_from=datetime.date(2027, 1, 1), rate=Decimal("8.00")))
        db.commit()
        assert effective_rate("REDUCED", datetime.date(2027, 6, 1), db) == Decimal("8.00")
        assert effective_rate("REDUCED", datetime.date(2026, 6, 1), db) == Decimal("7.00")
        db.delete(db.get(VatRate, ("REDUCED", datetime.date(2027, 1, 1))))
        db.commit()
    finally:
        db.close()


def test_unclassified_or_unapproved_products_never_get_a_rate():
    """The gate VAT-inclusive checkout will price through: every way a
    product can fail its classification raises — nothing is priced, let
    alone sold, on a guessed rate."""
    from app.database import SessionLocal
    from app.vat import (
        InvalidVatCategoryError,
        UnclassifiedProductError,
        UnapprovedProductError,
        resolve_product_rate,
    )

    db = SessionLocal()
    try:
        with pytest.raises(UnclassifiedProductError):
            resolve_product_rate(_vat_product(vat_category=None), db)
        with pytest.raises(UnclassifiedProductError):
            resolve_product_rate(_vat_product(vat_category="SMOOTHIE_REVIEW"), db)
        with pytest.raises(UnclassifiedProductError):
            resolve_product_rate(_vat_product(vat_category="SPECIAL_REVIEW"), db)
        with pytest.raises(UnclassifiedProductError, match="cow's-milk"):
            resolve_product_rate(_vat_product(vat_category="MILK_DRINK"), db)
        with pytest.raises(InvalidVatCategoryError):
            resolve_product_rate(_vat_product(vat_category="CAVIAR_LUXURY"), db)

        with pytest.raises(UnapprovedProductError):
            resolve_product_rate(
                _vat_product(vat_category="FOOD_REDUCED", vat_review_status="pending"), db
            )
        with pytest.raises(UnapprovedProductError):
            resolve_product_rate(
                _vat_product(vat_category="FOOD_REDUCED", vat_review_status="blocked"), db
            )

        # the one path that prices: classified, approved, rate in force
        rate = resolve_product_rate(_vat_product(vat_category="FOOD_REDUCED"), db)
        assert rate == Decimal("7.00")
    finally:
        db.close()


def test_milk_content_threshold_decides_the_rate():
    """Anlage 2 nr. 35 in one pure function: at least 75.00 % cow's milk in
    the finished drink buys the reduced rate, anything less pays standard.
    The percentage must measure cow's milk — an oat-based shake's number is
    its cow's-milk content (zero), no matter how much oat drink it holds."""
    from app.vat import RATE_REDUCED, RATE_STANDARD, rate_kind_for

    assert rate_kind_for("MILK_DRINK", Decimal("74.99")) == RATE_STANDARD
    assert rate_kind_for("MILK_DRINK", Decimal("75.00")) == RATE_REDUCED
    assert rate_kind_for("MILK_DRINK", Decimal("75.01")) == RATE_REDUCED

    # plant-based: the cow's-milk share of the finished drink is zero
    assert rate_kind_for("MILK_DRINK", Decimal("0")) == RATE_STANDARD

    # non-milk categories resolve without a milk figure
    assert rate_kind_for("FOOD_REDUCED") == RATE_REDUCED
    assert rate_kind_for("BEVERAGE_STANDARD") == RATE_STANDARD


def test_price_line_inclusive_snapshots_the_full_vat_picture():
    """One line, priced end to end from a gross shelf price: the rate
    resolved, net extracted per unit and per line, and the figures that
    will freeze onto the OrderItem — plus the per-rate summary that feeds
    OrderTaxLine, reconciling to the cent in both groups."""
    from app.database import SessionLocal
    from app.models import Product as ProductRow
    from app.vat import price_line_inclusive, summarize_by_rate

    db = SessionLocal()
    try:
        croissant = db.query(ProductRow).filter_by(slug="butter-croissant").one()
        croissant.vat_category = "FOOD_REDUCED"
        croissant.vat_review_status = "approved"
        shake = ProductRow(
            name="Strawberry Milkshake",
            slug="strawberry-milkshake",
            price=4.00,
            stock=10,
            is_available=True,
            vat_category="MILK_DRINK",
            vat_review_status="approved",
            milk_content_percent=Decimal("80"),
        )
        juice = ProductRow(
            name="Apple Juice",
            slug="apple-juice",
            price=2.50,
            stock=10,
            is_available=True,
            vat_category="BEVERAGE_STANDARD",
            vat_review_status="approved",
        )
        db.add_all([shake, juice])
        db.commit()

        when = datetime.date(2026, 6, 1)
        lines = [
            price_line_inclusive(croissant, 2, db, on_date=when),
            price_line_inclusive(shake, 1, db, on_date=when),
            price_line_inclusive(juice, 1, db, on_date=when),
        ]

        bread, milk, drink = lines
        assert (bread.vat_category, bread.vat_rate) == ("FOOD_REDUCED", Decimal("7.00"))
        assert bread.gross_unit_price == Decimal("3.75")
        assert bread.net_unit_price == Decimal("3.50")  # 3.75 / 1.07 → 3.50
        assert bread.gross_line_total == Decimal("7.50")
        assert bread.net_line_total == Decimal("7.01")  # 7.50 / 1.07 → 7.01
        assert bread.vat_amount == Decimal("0.49")
        assert milk.vat_rate == Decimal("7.00")  # 80 % cow's milk → reduced
        assert milk.net_line_total == Decimal("3.74") and milk.vat_amount == Decimal("0.26")
        assert drink.vat_rate == Decimal("19.00")  # juice as a delivered good
        assert drink.net_line_total == Decimal("2.10") and drink.vat_amount == Decimal("0.40")

        summary = summarize_by_rate(lines)
        assert [(s.vat_rate, s.net_amount, s.vat_amount, s.gross_amount) for s in summary] == [
            (Decimal("7.00"), Decimal("10.75"), Decimal("0.75"), Decimal("11.50")),
            (Decimal("19.00"), Decimal("2.10"), Decimal("0.40"), Decimal("2.50")),
        ]
        for group in summary:  # every bucket reconciles with itself
            assert group.net_amount + group.vat_amount == group.gross_amount
    finally:
        croissant.vat_category = None
        croissant.vat_review_status = "pending"
        db.delete(shake)
        db.delete(juice)
        db.commit()
        db.close()


def test_orders_placed_today_keep_null_vat_snapshots():
    """Phase 2B changes no checkout behaviour: an order placed now is priced
    by the legacy exclusive register math, its lines carry no VAT snapshot,
    and no per-rate rows exist for it. Snapshots only start when the
    inclusive cutover wires the engine in."""
    from app.database import SessionLocal
    from app.models import OrderItem as OrderItemRow
    from app.models import OrderTaxLine

    _top_up_stock("butter-croissant")
    order = client.post("/api/orders", json=checkout_payload(), headers=auth_header(login_customer())).json()

    # the legacy math, exactly as the register has always run it
    taxable = order["subtotal"] - order["discount_amount"]
    assert order["tax"] == round(taxable * get_settings().tax_rate, 2)
    assert order["total"] == round(taxable + order["tax"] + order["shipping_fee"], 2)

    db = SessionLocal()
    try:
        items = db.query(OrderItemRow).filter_by(order_id=order["id"]).all()
        assert items
        for item in items:
            assert item.vat_category is None
            assert item.vat_rate is None
            assert item.net_unit_price is None
            assert item.gross_unit_price is None
            assert item.net_line_total is None
            assert item.vat_amount is None
            assert item.gross_line_total is None
        assert db.query(OrderTaxLine).filter_by(order_id=order["id"]).count() == 0
    finally:
        db.close()


def test_the_vat_columns_migrate_a_legacy_sqlite_database(tmp_path, monkeypatch):
    """A database from before the VAT foundation boots through the additive
    migration: the columns land, every existing product reads as
    unclassified-but-pending, and historical order lines keep NULL snapshots
    — no guessed tax figures. The new tables arrive the way boot always
    delivers them: create_all for the missing, ALTER for the existing."""
    import sqlalchemy as sa

    from app import _upgrade_schema
    from app.database import Base

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy_vat.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR(150), price NUMERIC(10,2))"
        ))
        conn.execute(sa.text("INSERT INTO products (id, name, price) VALUES (1, 'Sourdough', 6.50)"))
        conn.execute(sa.text(
            "CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER, "
            "unit_price NUMERIC(10,2), quantity INTEGER, subtotal NUMERIC(10,2))"
        ))
        conn.execute(sa.text(
            "INSERT INTO order_items (id, order_id, unit_price, quantity, subtotal) "
            "VALUES (1, 1, 6.50, 1, 6.50)"
        ))
        # the boot-time data fixes touch categories unconditionally
        conn.execute(sa.text("CREATE TABLE categories (id INTEGER PRIMARY KEY, description VARCHAR(255))"))

    monkeypatch.setattr("app.engine", engine)
    Base.metadata.create_all(engine)
    _upgrade_schema()

    with engine.begin() as conn:
        products = conn.execute(sa.text(
            "SELECT vat_category, vat_review_status, milk_content_percent FROM products"
        )).fetchall()
        assert products == [(None, "pending", None)]
        items = conn.execute(sa.text(
            "SELECT vat_category, vat_rate, net_unit_price, gross_unit_price, "
            "net_line_total, vat_amount, gross_line_total FROM order_items"
        )).fetchall()
        assert items == [(None, None, None, None, None, None, None)]
    tables = sa.inspect(engine).get_table_names()
    assert "vat_rates" in tables and "order_tax_lines" in tables
    assert "product_vat_events" in tables  # the classification audit log
    engine.dispose()


def test_the_vat_ddl_keeps_its_postgres_safe_shape():
    """Every VAT additive statement must stay bare or carry a quoted string
    default — integer or bare-boolean defaults are the class of statement
    that boots on SQLite and dies on Postgres (the 2026-11-02 lesson)."""
    from app import _ADDITIVE_COLUMNS

    products = _ADDITIVE_COLUMNS["products"]
    assert products["vat_category"] == "ALTER TABLE products ADD COLUMN vat_category VARCHAR(20)"
    assert products["vat_review_status"] == (
        "ALTER TABLE products ADD COLUMN vat_review_status VARCHAR(12) NOT NULL DEFAULT 'pending'"
    )
    assert products["milk_content_percent"] == (
        "ALTER TABLE products ADD COLUMN milk_content_percent NUMERIC(5,2)"
    )

    items = _ADDITIVE_COLUMNS["order_items"]
    expected_types = {
        "vat_category": "VARCHAR(20)",
        "vat_rate": "NUMERIC(5,2)",
        "net_unit_price": "NUMERIC(10,2)",
        "gross_unit_price": "NUMERIC(10,2)",
        "net_line_total": "NUMERIC(10,2)",
        "vat_amount": "NUMERIC(10,2)",
        "gross_line_total": "NUMERIC(10,2)",
    }
    for column, column_type in expected_types.items():
        assert items[column] == (
            f"ALTER TABLE order_items ADD COLUMN {column} {column_type}"
        )


def test_vat_rate_rows_seed_on_every_boot_and_never_overwrite(tmp_path):
    """The statutory percentages are reference data: seeded on a fresh
    database, re-checked on every restart, and a deliberately edited row is
    never clobbered back — the adviser's word always beats the seeder's."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import VatRate
    from app.seed import _ensure_vat_rates

    engine = create_engine(f"sqlite:///{(tmp_path / 'vat_rates.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        _ensure_vat_rates(db)
        _ensure_vat_rates(db)  # a restart finds nothing left to do
        assert db.query(VatRate).count() == 2

        in_force = ("REDUCED", datetime.date(2026, 1, 1))
        db.get(VatRate, in_force).rate = Decimal("5.50")  # a console decision
        db.commit()
        _ensure_vat_rates(db)
        assert db.get(VatRate, in_force).rate == Decimal("5.50")
    finally:
        db.close()
        engine.dispose()


# --------------------------------------------------------------------------
# The VAT classification desk (Phase 2C): admin-only writes, its own audited
# endpoint, and the provisional-rate preview — checkout stays untouched.
# --------------------------------------------------------------------------

UNCLASSIFIED = {"vat_category": None, "vat_review_status": "pending", "milk_content_percent": None}


def _set_vat(product_id: int, payload: dict, headers: dict):
    return client.put(f"/api/admin/products/{product_id}/vat", json=payload, headers=headers)


def test_admin_product_responses_carry_the_vat_classification():
    """Admin sees the classification (and the provisional rate it implies);
    shoppers' catalog keeps exactly the shape it always had."""
    listing = client.get("/api/admin/products", headers=admin_headers()).json()
    product = listing["products"][0]
    assert product["vat_category"] is None  # nothing is auto-classified
    assert product["vat_review_status"] == "pending"
    assert product["milk_content_percent"] is None
    assert product["vat_rate_preview"] is None

    detail = client.get(f"/api/admin/products/{product['id']}", headers=admin_headers()).json()
    assert detail["vat_category"] is None
    assert detail["vat_events"] == []  # fresh product, no classification history

    # the public product page leaks none of it
    public = client.get(f"/api/products/{product['slug']}").json()
    assert "vat_category" not in public
    assert "vat_review_status" not in public
    assert "vat_rate_preview" not in public


def test_admin_assigns_and_reviews_vat_classification():
    """The desk workflow: assign a provisional category, see the rate the
    configuration implies (pending or not), enter milk content, approve."""
    admin = admin_headers()
    croissant = get_product("butter-croissant")
    puff = get_product("beef-curry-puff")
    try:
        assigned = _set_vat(
            croissant["id"],
            {"vat_category": "FOOD_REDUCED", "vat_review_status": "pending"},
            admin,
        )
        assert assigned.status_code == 200, assigned.json()
        assert assigned.json()["vat_rate_preview"] == 7.0  # implied, still pending

        # a milk drink's preview rides on its cow's-milk content
        shake = _set_vat(
            puff["id"],
            {"vat_category": "MILK_DRINK", "vat_review_status": "approved", "milk_content_percent": 80},
            admin,
        )
        assert shake.json()["vat_rate_preview"] == 7.0  # ≥ 75 → reduced
        thinner = _set_vat(
            puff["id"],
            {"vat_category": "MILK_DRINK", "vat_review_status": "approved", "milk_content_percent": 70},
            admin,
        )
        assert thinner.json()["vat_rate_preview"] == 19.0  # under the bar

        # parking categories imply no rate at all
        parked = _set_vat(
            puff["id"],
            {"vat_category": "SMOOTHIE_REVIEW", "vat_review_status": "pending", "milk_content_percent": None},
            admin,
        )
        assert parked.json()["vat_rate_preview"] is None

        # and the approval itself
        approved = _set_vat(
            croissant["id"],
            {"vat_category": "FOOD_REDUCED", "vat_review_status": "approved"},
            admin,
        )
        assert approved.json()["vat_review_status"] == "approved"
        assert approved.json()["vat_rate_preview"] == 7.0
    finally:
        _set_vat(croissant["id"], UNCLASSIFIED, admin)
        _set_vat(puff["id"], UNCLASSIFIED, admin)


def test_vat_classification_validation_refuses_incoherent_writes():
    """Every way a classification write can be nonsense, refused with a
    reason: unknown category or status and out-of-range milk at the schema
    layer (422); incoherent combinations at the rules layer (400)."""
    admin = admin_headers()
    product_id = get_product("butter-croissant")["id"]
    try:
        assert _set_vat(product_id, {"vat_category": "DESSERT_BAR", "vat_review_status": "pending"}, admin).status_code == 422
        assert _set_vat(product_id, {"vat_category": None, "vat_review_status": "done"}, admin).status_code == 422
        assert (
            _set_vat(
                product_id,
                {"vat_category": "MILK_DRINK", "vat_review_status": "pending", "milk_content_percent": -0.5},
                admin,
            ).status_code
            == 422
        )
        assert (
            _set_vat(
                product_id,
                {"vat_category": "MILK_DRINK", "vat_review_status": "pending", "milk_content_percent": 100.01},
                admin,
            ).status_code
            == 422
        )

        # a milk drink without its percentage never saves — pending or not
        missing_milk = _set_vat(product_id, {"vat_category": "MILK_DRINK", "vat_review_status": "pending"}, admin)
        assert missing_milk.status_code == 400
        assert "cow's-milk" in missing_milk.json()["detail"]

        # milk on anything else is meaningless
        stray_milk = _set_vat(
            product_id,
            {"vat_category": "BEVERAGE_STANDARD", "vat_review_status": "pending", "milk_content_percent": 90},
            admin,
        )
        assert stray_milk.status_code == 400
        assert "only belongs to a MILK_DRINK" in stray_milk.json()["detail"]

        # approving an unclassified product or a parked one is refused
        unclassified = _set_vat(product_id, {"vat_category": None, "vat_review_status": "approved"}, admin)
        assert unclassified.status_code == 400
        assert "assign a VAT category first" in unclassified.json()["detail"]
        for parked in ("SMOOTHIE_REVIEW", "SPECIAL_REVIEW"):
            refused = _set_vat(product_id, {"vat_category": parked, "vat_review_status": "approved"}, admin)
            assert refused.status_code == 400
            assert "tax adviser" in refused.json()["detail"]

        # boundaries that are fine: milk at exactly 0 and 100
        assert _set_vat(product_id, {"vat_category": "MILK_DRINK", "vat_review_status": "pending", "milk_content_percent": 0}, admin).status_code == 200
        assert _set_vat(product_id, {"vat_category": "MILK_DRINK", "vat_review_status": "pending", "milk_content_percent": 100}, admin).status_code == 200
    finally:
        _set_vat(product_id, UNCLASSIFIED, admin)


def test_vat_classification_writes_are_admin_only():
    """The classification endpoint opens for the desk and nobody else."""
    from app.database import SessionLocal
    from app.models import ProductVatEvent

    product_id = get_product("butter-croissant")["id"]
    payload = {"vat_category": "FOOD_REDUCED", "vat_review_status": "pending"}

    db = SessionLocal()
    try:
        events_before = db.query(ProductVatEvent).filter_by(product_id=product_id).count()
    finally:
        db.close()

    assert _set_vat(product_id, payload, {}).status_code == 401  # anonymous
    customer = auth_header(login_customer())
    assert _set_vat(product_id, payload, customer).status_code == 403

    # and nothing was written behind the refusals
    detail = client.get(f"/api/admin/products/{product_id}", headers=admin_headers()).json()
    assert detail["vat_category"] is None
    db = SessionLocal()
    try:
        assert db.query(ProductVatEvent).filter_by(product_id=product_id).count() == events_before
    finally:
        db.close()


def test_vat_classification_changes_are_audited():
    """Every real change lands in the log with who, when and the before/
    after; an identical write records nothing; the API's own listing reads
    it back on the product detail. A fresh product of its own, so earlier
    tests' writes on the case can't pad the count."""
    from app.database import SessionLocal
    from app.models import ProductVatEvent

    admin = admin_headers()
    me = client.get("/api/auth/me", headers=admin).json()
    baked = client.post(
        "/api/admin/products",
        json={"name": "Audit Trail Scone", "price": 3.0, "stock": 5},
        headers=admin,
    ).json()
    product_id = baked["id"]

    try:
        _set_vat(product_id, {"vat_category": "FOOD_REDUCED", "vat_review_status": "approved"}, admin)
        events = client.get(f"/api/admin/products/{product_id}", headers=admin).json()["vat_events"]
        assert len(events) == 1
        first = events[0]
        assert first["from_category"] is None and first["to_category"] == "FOOD_REDUCED"
        assert first["from_status"] == "pending" and first["to_status"] == "approved"
        assert first["admin_name"]  # who
        assert first["created_at"]  # when

        # writing the very same state again records nothing
        _set_vat(product_id, {"vat_category": "FOOD_REDUCED", "vat_review_status": "approved"}, admin)
        assert len(client.get(f"/api/admin/products/{product_id}", headers=admin).json()["vat_events"]) == 1

        # the next change carries the previous state as its "from"
        _set_vat(product_id, {"vat_category": "FOOD_REDUCED", "vat_review_status": "pending"}, admin)
        events = client.get(f"/api/admin/products/{product_id}", headers=admin).json()["vat_events"]
        assert len(events) == 2
        assert events[0]["from_status"] == "approved" and events[0]["to_status"] == "pending"

        # the ledger links the change to the admin's account
        db = SessionLocal()
        try:
            rows = db.query(ProductVatEvent).filter_by(product_id=product_id).all()
            assert len(rows) == 2
            assert {row.admin_user_id for row in rows} == {me["id"]}
        finally:
            db.close()
    finally:
        client.delete(f"/api/admin/products/{product_id}", headers=admin)  # events cascade


def test_the_vat_review_filter_lists_uncleared_products():
    """The tax-review rail: with one product signed off, the filter lists
    everyone else and leaves the signed-off one out."""
    admin = admin_headers()
    product_id = get_product("butter-croissant")["id"]
    try:
        _set_vat(product_id, {"vat_category": "FOOD_REDUCED", "vat_review_status": "approved"}, admin)

        rail = client.get("/api/admin/products", params={"needs_vat_review": True}, headers=admin).json()
        assert rail["products"], "the rest of the case is still waiting"
        assert all(p["vat_review_status"] != "approved" for p in rail["products"])
        assert all(p["id"] != product_id for p in rail["products"])

        whole_case = client.get("/api/admin/products", headers=admin).json()
        assert rail["pagination"]["total_items"] < whole_case["pagination"]["total_items"]
    finally:
        _set_vat(product_id, UNCLASSIFIED, admin)


# ---------------------------------------------------------------------------
# Phase 2D — server-side basket quotes
# ---------------------------------------------------------------------------


def _quote(items, coupon_code=None, token=None):
    """POST a basket quote — ids and quantities only, optionally a code."""
    payload = {"items": items}
    if coupon_code:
        payload["coupon_code"] = coupon_code
    return client.post(
        "/api/orders/quote", json=payload, headers=auth_header(token) if token else None
    )


def test_quote_prices_the_basket_from_the_catalog():
    """The quote is the server's own tally, off the live catalog: line prices
    are today's current prices, tax is the shop's flat exclusive rate on the
    discounted subtotal, delivery follows the threshold, and the currency is
    the one orders are charged in."""
    croissant = get_product("butter-croissant")
    puff = get_product("beef-curry-puff")
    response = _quote(
        [
            {"product_id": croissant["id"], "quantity": 2},
            {"product_id": puff["id"], "quantity": 1},
        ]
    )
    assert response.status_code == 200
    quote = response.json()

    line = next(i for i in quote["items"] if i["product_id"] == croissant["id"])
    assert line["unit_price"] == croissant["current_price"]
    assert line["line_total"] == round(croissant["current_price"] * 2, 2)
    assert line["name"] == croissant["name"] and line["quantity"] == 2

    settings = get_settings()
    subtotal = round(croissant["current_price"] * 2 + puff["current_price"], 2)
    assert quote["subtotal"] == subtotal
    assert quote["discount_amount"] == 0 and quote["coupon_code"] is None
    assert quote["tax"] == round(subtotal * settings.tax_rate, 2)
    expected_shipping = (
        0.0 if subtotal >= settings.free_shipping_threshold else settings.shipping_fee
    )
    assert quote["shipping_fee"] == expected_shipping
    assert quote["total"] == round(subtotal + quote["tax"] + quote["shipping_fee"], 2)
    assert quote["currency"] == "eur"


def test_quote_matches_the_placed_order_to_the_cent():
    """The whole point of the endpoint: quote a basket, then buy exactly that
    basket with the same code — every charged figure must land on the quoted
    one, because placement prices it with the same recipe, independently."""
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    croissant = get_product("butter-croissant")
    puff = get_product("beef-curry-puff")
    token = register_and_login("quote-buyer@example.com", "QuoteBuyer123!")
    items = [
        {"product_id": croissant["id"], "quantity": 2},
        {"product_id": puff["id"], "quantity": 1},
    ]

    quoted = _quote(items, coupon_code="WELCOME10", token=token)
    assert quoted.status_code == 200
    quote = quoted.json()
    assert quote["coupon_code"] == "WELCOME10" and quote["coupon_percent_off"] == 10
    assert quote["discount_amount"] == round(quote["subtotal"] * 10 / 100, 2) > 0

    placed = client.post(
        "/api/orders",
        json=checkout_payload(
            items=[{"product_id": i["product_id"], "quantity": i["quantity"]} for i in items],
            coupon_code="WELCOME10",
            payment_method="cash_on_delivery",
        ),
        headers=auth_header(token),
    )
    assert placed.status_code == 201
    order = placed.json()
    for field in ("subtotal", "discount_amount", "tax", "shipping_fee", "total"):
        assert quote[field] == order[field], field
    assert order["coupon_code"] == quote["coupon_code"]
    assert order["currency"] == quote["currency"]

    # The quote runs the same personal coupon rules checkout does — now that
    # the one-per-customer code is spent, quoting it again says so.
    spent = _quote(items, coupon_code="WELCOME10", token=token)
    assert spent.status_code == 404
    assert "already been used" in spent.json()["detail"]


def test_quote_refuses_what_checkout_refuses():
    """Same doorkeeper as placement: nonsense quantities never get priced,
    unknown or unavailable products are refused, and stock caps the basket
    with the message the shopper would get at the last step."""
    # Quantities outside 1..99 (and an empty basket) fail validation outright
    assert _quote([]).status_code == 422
    croissant = get_product("butter-croissant")
    assert _quote([{"product_id": croissant["id"], "quantity": 0}]).status_code == 422
    assert _quote([{"product_id": croissant["id"], "quantity": 100}]).status_code == 422

    # Unknown id → the availability refusal
    unknown = _quote([{"product_id": 999999, "quantity": 1}])
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "Product 999999 is not available"

    admin = admin_headers()
    low_stock = unavailable = None
    try:
        low = client.post(
            "/api/admin/products",
            headers=admin,
            json={"name": "Last Two Buns", "price": 4.5, "stock": 2},
        )
        unavailable = client.post(
            "/api/admin/products",
            headers=admin,
            json={"name": "Off The Shelf Bun", "price": 4.5, "stock": 5, "is_available": False},
        )
        assert low.status_code == 201 and unavailable.status_code == 201
        low_stock, unavailable = low.json()["id"], unavailable.json()["id"]

        tight = _quote([{"product_id": low_stock, "quantity": 3}])
        assert tight.status_code == 400
        assert tight.json()["detail"] == "Only 2 left of Last Two Buns"

        off = _quote([{"product_id": unavailable, "quantity": 1}])
        assert off.status_code == 400
        assert "is not available" in off.json()["detail"]
    finally:
        if low_stock:
            client.delete(f"/api/admin/products/{low_stock}", headers=admin)
        if unavailable:
            client.delete(f"/api/admin/products/{unavailable}", headers=admin)


def test_quote_coupon_rules_mirror_checkout():
    """A code that checkout would refuse is refused at quote time too, with
    the same wording: unknown codes, and live codes the basket hasn't
    unlocked yet."""
    token = register_and_login("voucher-quoter@example.com", "VoucherQuote123!")
    croissant = get_product("butter-croissant")
    one = [{"product_id": croissant["id"], "quantity": 1}]

    bogus = _quote(one, coupon_code="NOSUCHCODE", token=token)
    assert bogus.status_code == 404
    assert bogus.json()["detail"] == "This coupon code is not valid"

    # A code the basket hasn't unlocked yet — checkout's rule, quoted here
    # with the same wording. (FRESHBAKE15 itself no longer carries a
    # minimum: the old $40 floor was a dollar figure, not approved euro.)
    admin = admin_headers()
    made = client.post(
        "/api/admin/coupons",
        json={"code": "EURMIN20", "percent_off": 10, "min_subtotal": 20.0},
        headers=admin,
    )
    assert made.status_code == 201
    locked = _quote(one, coupon_code="EURMIN20", token=token)
    assert locked.status_code == 400
    assert locked.json()["detail"].startswith("EURMIN20 needs a basket of at least")

    # The same code on a big enough basket prices straight through
    _top_up_stock("butter-croissant")
    quantity = int(20 / croissant["current_price"]) + 1
    big = [{"product_id": croissant["id"], "quantity": quantity}]
    earned = _quote(big, coupon_code="EURMIN20", token=token)
    assert earned.status_code == 200
    quote = earned.json()
    subtotal = round(croissant["current_price"] * quantity, 2)
    assert subtotal >= 20 and quote["subtotal"] == subtotal
    assert quote["coupon_code"] == "EURMIN20" and quote["coupon_percent_off"] == 10
    assert quote["discount_amount"] == round(subtotal * 10 / 100, 2)

    client.delete(f"/api/admin/coupons/{made.json()['id']}", headers=admin)


def test_quote_free_and_paid_delivery_and_cent_rounding():
    """Delivery is the threshold's call, judged on the pre-discount subtotal,
    and every figure is rounded to the cent exactly as the invoice will be."""
    settings = get_settings()
    croissant = get_product("butter-croissant")

    small = _quote([{"product_id": croissant["id"], "quantity": 3}]).json()
    assert small["subtotal"] < settings.free_shipping_threshold
    assert small["shipping_fee"] == settings.shipping_fee
    assert small["tax"] == round(small["subtotal"] * settings.tax_rate, 2)
    assert small["total"] == round(small["subtotal"] + small["tax"] + small["shipping_fee"], 2)

    _top_up_stock("butter-croissant")
    quantity = int(settings.free_shipping_threshold / croissant["current_price"]) + 1
    large = _quote([{"product_id": croissant["id"], "quantity": quantity}]).json()
    assert large["subtotal"] >= settings.free_shipping_threshold
    assert large["shipping_fee"] == 0.0
    assert large["total"] == round(large["subtotal"] + large["tax"], 2)


def test_quote_touches_nothing():
    """Quoting is a pure read: no order, no timeline event, no stock movement
    — and no Stripe session can exist without an order to hang it on."""
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import Order as OrderRow
    from app.models import OrderEvent as OrderEventRow
    from app.models import Product as ProductRow

    def snapshot():
        db = SessionLocal()
        try:
            return (
                db.scalar(select(func.count()).select_from(OrderRow)),
                db.scalar(select(func.count()).select_from(OrderEventRow)),
                db.scalar(
                    select(ProductRow.stock).where(ProductRow.slug == "butter-croissant")
                ),
            )
        finally:
            db.close()

    croissant = get_product("butter-croissant")
    before = snapshot()
    response = _quote([{"product_id": croissant["id"], "quantity": 2}])
    assert response.status_code == 200
    assert snapshot() == before


def test_quote_serves_guests_and_rate_limits_bursts():
    """A basket can be priced before login (the voucher board works that way
    too), but the endpoint is public — a burst hits the brake. The brake is
    pinned to a two-shot window for the test (the suite-wide budget is
    raised for all the other price checks)."""
    from app import SlidingWindowLimiter

    croissant = get_product("butter-croissant")
    items = [{"product_id": croissant["id"], "quantity": 1}]

    guest = _quote(items)
    assert guest.status_code == 200
    assert guest.json()["currency"] == "eur"

    limiter = client.app.state.limiters
    real = limiter["quote"]
    limiter["quote"] = SlidingWindowLimiter(2)
    try:
        _quote(items)  # shot 1 of the tiny window
        _quote(items)  # shot 2 — the window is now full
        throttled = _quote(items)
    finally:
        limiter["quote"] = real
    assert throttled.status_code == 429
    assert throttled.json()["detail"] == "That's a lot of price checks — try again in a moment."


# --------------------------------------------------------------------------
# Order emails, back-in-stock asks, crawl files, dashboard refunds, GDPR
# --------------------------------------------------------------------------

def test_admin_status_change_emails_the_customer(caplog):
    """Every dial the desk makes lands in the customer's mailbox too —
    the transactional update a serious shop owes its regulars."""
    import logging

    order = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=auth_header(login_customer()),
    ).json()

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        moved = client.put(
            f"/api/admin/orders/{order['id']}/status",
            json={"status": "baking", "comment": "In the oven"},
            headers=admin_headers(),
        )
    assert moved.status_code == 200
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Your bake is in the oven" in logged
    assert order["order_number"] in logged
    # The desk's own words ride along
    assert "In the oven" in logged


def test_customer_cancel_earns_a_confirmation_email(caplog):
    import logging

    order = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=auth_header(login_customer()),
    ).json()

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        cancelled = client.post(
            f"/api/orders/{order['id']}/cancel", headers=auth_header(login_customer())
        )
    assert cancelled.status_code == 200
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Your order was cancelled" in logged
    assert order["order_number"] in logged


def test_payment_window_expiry_emails_the_customer(caplog):
    import logging

    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        expire_order_via_webhook(placed["id"])
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Your order was cancelled" in logged
    assert placed["order_number"] in logged


def test_failed_async_payment_cancels_and_restores_stock_exactly_once(caplog):
    """A delayed payment that failed (Klarna, SEPA) cancels its order the
    moment Stripe says the money won't come — under its own honest timeline
    word, with the box back on the shelf exactly once however often Stripe
    redelivers, and the customer told where their order went."""
    import logging

    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    croissant = get_product("butter-croissant")
    shelf_before = croissant["stock"]

    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    assert get_product("butter-croissant")["stock"] == shelf_before - 2  # the box left the shelf

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        fail_order_via_webhook(placed["id"])

    detail = client.get(
        f"/api/orders/{placed['id']}", headers=auth_header(login_customer())
    ).json()
    assert detail["status"] == "cancelled"
    assert any("Payment failed" in e["comment"] for e in detail["timeline"])
    assert get_product("butter-croissant")["stock"] == shelf_before  # and went back
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Your order was cancelled" in logged

    # A duplicate delivery of the same failed payment changes nothing at all
    fail_order_via_webhook(placed["id"])
    again = client.get(
        f"/api/orders/{placed['id']}", headers=auth_header(login_customer())
    ).json()
    assert again["timeline"] == detail["timeline"]  # no second cancel event
    assert get_product("butter-croissant")["stock"] == shelf_before  # stock restored once


def test_failed_async_payment_leaves_a_paid_order_alone():
    """The guard, not the event, decides: money that landed stays landed —
    a late (or replayed) failure notice must never cancel a paid order."""
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    pay_order_via_webhook(placed["id"])

    fail_order_via_webhook(placed["id"])  # out-of-order / duplicate delivery

    detail = client.get(
        f"/api/orders/{placed['id']}", headers=auth_header(login_customer())
    ).json()
    assert detail["payment_status"] == "paid"
    assert detail["status"] == "pending"  # untouched — not cancelled


def test_failed_async_payment_after_the_expiry_event_changes_nothing():
    """Stripe's own real sequence can deliver both ends — the failure, then
    the session's expiry (or vice versa). Whichever lands second finds the
    order already cancelled and stays quiet: one cancellation, one restore."""
    _top_up_stock("butter-croissant")
    _top_up_stock("beef-curry-puff")
    croissant = get_product("butter-croissant")
    shelf_before = croissant["stock"]

    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    expire_order_via_webhook(placed["id"])
    cancelled_once = client.get(
        f"/api/orders/{placed['id']}", headers=auth_header(login_customer())
    ).json()
    assert cancelled_once["status"] == "cancelled"
    assert get_product("butter-croissant")["stock"] == shelf_before

    fail_order_via_webhook(placed["id"])  # the second word on the same session

    detail = client.get(
        f"/api/orders/{placed['id']}", headers=auth_header(login_customer())
    ).json()
    assert detail["timeline"] == cancelled_once["timeline"]  # nothing new
    assert get_product("butter-croissant")["stock"] == shelf_before  # restored once, total


def test_restock_alert_signs_up_idempotently():
    croissant = get_product("butter-croissant")
    admin = admin_headers()
    detail = client.get(f"/api/admin/products/{croissant['id']}", headers=admin).json()
    sold_out = dict(detail, stock=0)
    assert client.put(
        f"/api/admin/products/{croissant['id']}", json=sold_out, headers=admin
    ).status_code == 200

    ask = client.post(
        f"/api/products/{croissant['id']}/restock-alert",
        json={"email": "Waiter@example.com"},
    )
    assert ask.status_code == 201
    assert ask.json() == {
        "email": "waiter@example.com",
        "product_id": croissant["id"],
        "already_registered": False,
    }

    # asking twice is enthusiasm, not an error
    again = client.post(
        f"/api/products/{croissant['id']}/restock-alert",
        json={"email": "waiter@example.com"},
    )
    assert again.status_code == 200
    assert again.json()["already_registered"] is True

    # a stocked bake has nothing to wait for
    stocked = dict(detail, stock=12)
    client.put(f"/api/admin/products/{croissant['id']}", json=stocked, headers=admin)
    assert client.post(
        f"/api/products/{croissant['id']}/restock-alert",
        json={"email": "waiter@example.com"},
    ).status_code == 400


def test_refilling_the_shelf_answers_restock_asks_once(caplog):
    import logging

    croissant = get_product("butter-croissant")
    admin = admin_headers()
    detail = client.get(f"/api/admin/products/{croissant['id']}", headers=admin).json()

    client.put(f"/api/admin/products/{croissant['id']}", json=dict(detail, stock=0), headers=admin)
    client.post(
        f"/api/products/{croissant['id']}/restock-alert", json={"email": "early@example.com"}
    )
    client.post(
        f"/api/products/{croissant['id']}/restock-alert", json={"email": "second@example.com"}
    )

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        refilled = client.put(
            f"/api/admin/products/{croissant['id']}",
            json=dict(detail, stock=24),
            headers=admin,
        )
    assert refilled.status_code == 200
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "It's back — Butter Croissant" in logged
    assert "early@example.com" in logged
    assert "second@example.com" in logged

    # a second save while the shelf is stocked answers nobody again
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        client.put(
            f"/api/admin/products/{croissant['id']}", json=dict(detail, stock=30), headers=admin
        )
    assert "It's back" not in "\n".join(r.getMessage() for r in caplog.records)


def test_sitemap_lists_the_shop_and_its_bakes():
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    body = sitemap.text
    assert body.startswith('<?xml version="1.0"')
    assert "<loc>http://testserver/</loc>" in body
    assert "<loc>http://testserver/products/butter-croissant</loc>" in body
    assert "<changefreq>weekly</changefreq>" in body
    assert "<lastmod>" in body  # every bake stamps its last change


def test_robots_opens_the_shop_and_closes_the_back_rooms():
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    body = robots.text
    assert "User-agent: *" in body
    assert "Disallow: /api/" in body
    assert "Disallow: /admin" in body
    assert "Disallow: /account" in body
    assert "Sitemap: http://testserver/sitemap.xml" in body


def test_stats_carry_a_thirty_day_trading_chart():
    client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=auth_header(login_customer()),
    )
    stats = client.get("/api/admin/stats", headers=admin_headers()).json()

    series = stats["revenue_series"]
    assert len(series) == 30
    # every day present, oldest first, ISO dates
    from datetime import date as _date, timedelta as _timedelta

    first, last = _date.fromisoformat(series[0]["date"]), _date.fromisoformat(series[-1]["date"])
    assert last - first == _timedelta(days=29)
    # today's bar carries the freshly placed order
    today = series[-1]
    assert today["orders"] >= 1
    assert today["revenue"] > 0


def test_dashboard_refund_webhook_syncs_the_book():
    """A refund issued by hand in the Stripe dashboard must reach the order —
    the timeline's own fallback advice says to do exactly that."""
    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    pay_order_via_webhook(placed["id"])

    charge = {"payment_intent": f"pi_fake_{placed['id']}", "amount": 1820, "amount_refunded": 1820}
    synced = client.post(
        "/api/payments/webhook",
        json={"type": "charge.refunded", "data": {"object": charge}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert synced.status_code == 200
    detail = client.get(f"/api/orders/{placed['id']}", headers=auth_header(login_customer())).json()
    assert detail["payment_status"] == "refunded"
    assert any("Stripe dashboard" in e["comment"] for e in detail["timeline"])

    # the app's own refund arriving twice changes nothing
    client.post(
        "/api/payments/webhook",
        json={"type": "charge.refunded", "data": {"object": charge}},
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    detail = client.get(f"/api/orders/{placed['id']}", headers=auth_header(login_customer())).json()
    assert sum("Stripe dashboard" in e["comment"] for e in detail["timeline"]) == 1


def test_partial_dashboard_refund_marks_partially_refunded():
    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    pay_order_via_webhook(placed["id"])

    client.post(
        "/api/payments/webhook",
        json={
            "type": "charge.refunded",
            "data": {
                "object": {
                    "payment_intent": f"pi_fake_{placed['id']}",
                    "amount": 1820,
                    "amount_refunded": 500,
                }
            },
        },
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    detail = client.get(f"/api/orders/{placed['id']}", headers=auth_header(login_customer())).json()
    assert detail["payment_status"] == "partially_refunded"


def test_dispute_rings_the_desk():
    placed = client.post(
        "/api/orders", json=checkout_payload(), headers=auth_header(login_customer())
    ).json()
    pay_order_via_webhook(placed["id"])

    rung = client.post(
        "/api/payments/webhook",
        json={
            "type": "charge.dispute.created",
            "data": {"object": {"payment_intent": f"pi_fake_{placed['id']}"}},
        },
        headers={"stripe-signature": "t=1,v1=fake"},
    )
    assert rung.status_code == 200
    notifications = client.get("/api/admin/notifications?limit=10", headers=admin_headers()).json()
    assert any(
        placed["order_number"] in n["title"] and "dispute" in n["title"].lower()
        for n in notifications["items"]
    )
    detail = client.get(f"/api/orders/{placed['id']}", headers=auth_header(login_customer())).json()
    assert any("dispute" in e["comment"].lower() for e in detail["timeline"])


def test_account_export_holds_everything_the_shop_keeps():
    email, password = "exported@example.com", "Export123!"
    token = register_and_login(email, password)
    headers = auth_header(token)
    croissant = get_product("butter-croissant")
    client.put(f"/api/products/{croissant['id']}/favorite", headers=headers)

    order = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=headers,
    ).json()

    export = client.get("/api/auth/me/export", headers=headers).json()
    assert export["account"]["email"] == email
    assert export["account"]["password_storage"] == "bcrypt hash (not exportable)"
    assert export["addresses"][0]["city"] == "Sweet Valley"
    assert [o["order_number"] for o in export["orders"]] == [order["order_number"]]
    assert export["orders"][0]["items"][0]["product_name"] == "Butter Croissant"
    assert croissant["id"] in export["saved_products"]

    # the neighbours' data never rides along
    assert "shopper@example.com" not in str(export)


def test_account_deletion_scrubs_the_person_keeps_the_books():
    email, password = "gone@example.com", "Gone12345!"
    token = register_and_login(email, password)
    headers = auth_header(token)
    croissant = get_product("butter-croissant")

    order = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=headers,
    ).json()
    client.put(f"/api/products/{croissant['id']}/favorite", headers=headers)
    client.post(
        f"/api/products/{croissant['id']}/reviews",
        json={"rating": 5, "comment": "Heavenly"},
        headers=headers,
    )

    # an open tab is not enough — the password is asked at the door
    assert client.post("/api/auth/me/delete", json={"password": "wrong"}, headers=headers).status_code == 400

    deleted = client.post("/api/auth/me/delete", json={"password": password}, headers=headers)
    assert deleted.status_code == 200

    # the session died with the account
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    # the old login no longer opens anything
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 401

    # the invoice survives, belonging to no one in particular
    admin = admin_headers()
    found = client.get(f"/api/admin/orders?search={order['order_number']}", headers=admin).json()
    assert found["pagination"]["total_items"] == 1
    kept = found["orders"][0]
    assert kept["recipient"] == "Shopper Smith"  # the address snapshot is the invoice's own
    # hearts and words are gone — the demo seed's other reviews stay, the
    # deleted customer's own word does not
    listing = client.get("/api/products/butter-croissant").json()
    assert all(r["comment"] != "Heavenly" for r in listing["reviews"])


# --------------------------------------------------------------------------
# Approved delivery rules — VIP threshold, and the threshold's one definition
# --------------------------------------------------------------------------

def _seed_kept_orders(user_id: int, count: int, days_ago: int = 0) -> None:
    """Give a test account `count` kept (delivered) orders, directly in the
    book — the loyalty tier's own measure, without spending the suite's
    stock on ten baskets."""
    from datetime import timedelta

    from app.database import SessionLocal as _Session, utcnow as _utcnow
    from app.models import Order as _Order, OrderEvent as _Event

    db = _Session()
    try:
        placed = _utcnow() - timedelta(days=days_ago)
        for n in range(count):
            order = _Order(
                order_number=f"DJ-TIER-{user_id}-{n}",
                user_id=user_id,
                status="delivered",
                payment_method="cash_on_delivery",
                payment_status="paid",
                subtotal=10.00,
                total=10.00,
                currency="eur",
                recipient="Test Regular",
                line1="1 Fixture Row",
                city="Bonn",
                state="NW",
                postal_code="53111",
                country="DE",
                created_at=placed,
                updated_at=placed,
            )
            db.add(order)
            db.flush()
            db.add(_Event(order_id=order.id, status="delivered", comment="fixture", created_at=placed))
        db.commit()
    finally:
        db.close()


def _basket_reaching(min_value: float, max_value: float | None = None):
    """A basket whose shelf subtotal lands in [min_value, max_value) — built
    one of each cheapest bake until the floor is crossed, so it never
    overfills past the line under test (the suite eats stock all day)."""
    from app.database import SessionLocal as _Session
    from app.models import Product as _Product
    from sqlalchemy import select as _select

    db = _Session()
    try:
        products = db.scalars(
            _select(_Product).where(_Product.is_available.is_(True), _Product.stock > 0).order_by(_Product.price)
        ).all()
    finally:
        db.close()
    items = []
    running = 0.0
    for product in products:
        items.append({"product_id": product.id, "quantity": 1})
        running = round(running + float(product.current_price), 2)
        if running >= min_value and (max_value is None or running < max_value):
            return items
    raise AssertionError(f"the shelf cannot build a basket reaching {min_value}")


def test_vip_earns_free_delivery_from_the_vip_threshold():
    """A VIP regular (ten kept orders, fresh) earns free delivery from the
    smaller VIP line; the very same basket pays for everyone else."""
    from app.loyalty import VIP_ORDERS

    vip_token = register_and_login("vip.delivery@example.com", "VipDel123!")
    vip_id = client.get("/api/auth/me", headers=auth_header(vip_token)).json()["id"]
    _seed_kept_orders(vip_id, VIP_ORDERS)

    # A basket inside the VIP window (€15–€29.99 under the suite's rules)
    items = _basket_reaching(20.0, 29.0)
    vip_quote = client.post("/api/orders/quote", json={"items": items}, headers=auth_header(vip_token))
    assert vip_quote.status_code == 200
    assert vip_quote.json()["shipping_fee"] == 0.0
    assert vip_quote.json()["free_delivery_threshold"] == 15.0

    guest_quote = client.post("/api/orders/quote", json={"items": items})
    assert guest_quote.status_code == 200
    assert guest_quote.json()["shipping_fee"] == 3.50  # below the standard €30 line
    assert guest_quote.json()["free_delivery_threshold"] == 30.0


def test_vip_free_delivery_places_free_at_checkout():
    """The threshold rule reaches the invoice, not just the quote."""
    from app.loyalty import VIP_ORDERS

    vip_token = register_and_login("vip.checkout@example.com", "VipChk123!")
    vip_id = client.get("/api/auth/me", headers=auth_header(vip_token)).json()["id"]
    _seed_kept_orders(vip_id, VIP_ORDERS)

    placed = client.post(
        "/api/orders",
        json=checkout_payload(
            payment_method="cash_on_delivery",
            items=_basket_reaching(20.0, 29.0),
        ),
        headers=auth_header(vip_token),
    )
    assert placed.status_code == 201
    assert placed.json()["shipping_fee"] == 0.0
    assert placed.json()["total"] == placed.json()["subtotal"] + placed.json()["tax"]


def test_a_lapsed_vip_pays_the_standard_threshold():
    """Tiers expire with the visit window — ten orders a season ago is
    'lapsed', and lapsed regulars pay like everyone else."""
    from app.loyalty import LOYAL_WINDOW_DAYS, VIP_ORDERS

    token = register_and_login("lapsed.vip@example.com", "Lapsed123!")
    user_id = client.get("/api/auth/me", headers=auth_header(token)).json()["id"]
    _seed_kept_orders(user_id, VIP_ORDERS, days_ago=LOYAL_WINDOW_DAYS + 10)

    quote = client.post(
        "/api/orders/quote", json={"items": _basket_reaching(20.0, 29.0)}, headers=auth_header(token)
    )
    assert quote.json()["shipping_fee"] == 3.50
    assert quote.json()["free_delivery_threshold"] == 30.0


def test_the_threshold_judges_the_pre_discount_subtotal():
    """The one definition, pinned: the free-delivery line is judged on the
    basket's shelf value — a coupon cannot push a basket over it (or dump
    one under it after the check), and delivery never counts toward it."""
    admin = admin_headers()
    minted = client.post(
        "/api/admin/coupons",
        json={"code": "TESTTHRESH25", "percent_off": 25, "description": "threshold fixture"},
        headers=admin,
    )
    assert minted.status_code == 201

    try:
        items = _basket_reaching(31.0, 39.0)  # over the standard €30 line on shelf value

        bare = client.post("/api/orders/quote", json={"items": items})
        assert bare.json()["shipping_fee"] == 0.0

        # The 25% code drops the payable below the line — delivery stays
        # free, because the shelf value was over it when the basket was judged.
        with_coupon = client.post(
            "/api/orders/quote", json={"items": items, "coupon_code": "TESTTHRESH25"}
        )
        assert with_coupon.status_code == 200
        assert with_coupon.json()["discount_amount"] > 0
        assert with_coupon.json()["subtotal"] - with_coupon.json()["discount_amount"] < 30.0
        assert with_coupon.json()["shipping_fee"] == 0.0

        # And a basket under the line on shelf value pays delivery even with
        # no coupon at all — there is no second, friendlier definition.
        small = client.post("/api/orders/quote", json={"items": _basket_reaching(16.0, 26.0)})
        assert small.json()["shipping_fee"] == 3.50
    finally:
        client.delete(f"/api/admin/coupons/{minted.json()['id']}", headers=admin)


# --------------------------------------------------------------------------
# Shipping VAT allocation — the owner's approved proportional rule, in the
# engine that will price it (pure Decimal, no database needed)
# --------------------------------------------------------------------------

from decimal import Decimal as _Decimal  # noqa: E402

from app.vat import RateSummary as _RateSummary, allocate_shipping as _allocate  # noqa: E402

D = _Decimal


def _summary(rate: str, net: str, vat: str, gross: str):
    return _RateSummary(vat_rate=D(rate), net_amount=D(net), vat_amount=D(vat), gross_amount=D(gross))


def test_shipping_vat_single_rate_carries_the_whole_charge():
    """One rate in the basket: the delivery charge simply carries it — no
    proportional arithmetic, nothing to lose."""
    rows = _allocate("2.99", [_summary("7.00", "100.00", "6.54", "106.54")])
    assert len(rows) == 1
    row = rows[0]
    assert row.vat_rate == D("7.00")
    assert row.gross_amount == D("2.99")
    assert row.net_amount == D("2.79")
    assert row.vat_amount == D("0.20")
    assert row.net_amount + row.vat_amount == row.gross_amount


def test_shipping_vat_mixed_basket_allocates_proportionally_and_reconciles():
    """The approved rule end to end: €2.99 across a €10-net 7 % bucket and a
    €30-net 19 % bucket — portions 0.75/2.24 by largest remainder, each
    split at its own rate, and the cents reassemble exactly (the property a
    refund of the charge depends on)."""
    rows = _allocate(
        "2.99",
        [
            _summary("7.00", "10.00", "0.65", "10.65"),
            _summary("19.00", "30.00", "4.79", "34.79"),
        ],
    )
    by_rate = {row.vat_rate: row for row in rows}
    assert by_rate[D("7.00")].gross_amount == D("0.75")  # 299¢ × 10/40 = 74.75 → 75
    assert by_rate[D("19.00")].gross_amount == D("2.24")  # 299¢ × 30/40 = 224.25 → 224
    for row in rows:
        assert row.net_amount + row.vat_amount == row.gross_amount
    assert sum((row.gross_amount for row in rows), D("0.00")) == D("2.99")
    assert sum((row.net_amount for row in rows), D("0.00")) + sum(
        (row.vat_amount for row in rows), D("0.00")
    ) == D("2.99")


def test_shipping_vat_never_loses_a_cent_on_awkward_splits():
    """One cent across three buckets, and a charge that divides evenly —
    deterministic answers either way, and never a created or lost cent."""
    # 1¢ across three equal buckets: biggest remainder wins it (all tie →
    # the bigger taxable value, then the lower rate).
    rows = _allocate(
        "0.01",
        [
            _summary("7.00", "10.00", "0.65", "10.65"),
            _summary("19.00", "10.00", "1.60", "11.60"),
            _summary("19.00", "9.00", "1.44", "10.44"),
        ],
    )
    assert sum((row.gross_amount for row in rows), D("0.00")) == D("0.01")
    assert len(rows) == 1  # a single cent is a single bucket's to carry

    # €3.00 across a 50/50 basket: exact halves, no remainder to distribute.
    rows = _allocate(
        "3.00",
        [
            _summary("7.00", "50.00", "3.27", "53.27"),
            _summary("19.00", "50.00", "7.98", "57.98"),
        ],
    )
    assert [row.gross_amount for row in rows] == [D("1.50"), D("1.50")]


def test_free_delivery_allocates_no_shipping_vat():
    """Free delivery is €0 of charge — no rows, no tax lines, nothing to
    persist or refund."""
    assert _allocate("0.00", [_summary("7.00", "10.00", "0.65", "10.65")]) == []


def test_shipping_vat_on_a_fully_discounted_basket_stays_deterministic():
    """A 100 % coupon zeroes every net line; the charge then allocates by
    gross value — and a basket with neither net nor gross (impossible at
    checkout, defended anyway) taxes the whole charge at the highest rate."""
    rows = _allocate(
        "2.00",
        [
            _summary("7.00", "0.00", "0.00", "10.00"),
            _summary("19.00", "0.00", "0.00", "30.00"),
        ],
    )
    by_rate = {row.vat_rate: row for row in rows}
    assert by_rate[D("7.00")].gross_amount == D("0.50")  # by gross: 200¢ × 10/40
    assert by_rate[D("19.00")].gross_amount == D("1.50")
    assert sum((row.gross_amount for row in rows), D("0.00")) == D("2.00")

    rows = _allocate(
        "1.00",
        [
            _summary("7.00", "0.00", "0.00", "0.00"),
            _summary("19.00", "0.00", "0.00", "0.00"),
        ],
    )
    assert len(rows) == 1
    assert rows[0].vat_rate == D("19.00")
    assert rows[0].gross_amount == D("1.00")


# --------------------------------------------------------------------------
# The coupon tin's honest usage count
# --------------------------------------------------------------------------

def test_coupon_list_counts_kept_redemptions_only():
    admin = admin_headers()
    minted = client.post(
        "/api/admin/coupons",
        json={"code": "TESTREDEEM10", "percent_off": 10, "description": "redemption fixture"},
        headers=admin,
    )
    assert minted.status_code == 201

    token = register_and_login("redeem.counter@example.com", "Redeem123!")
    headers = auth_header(token)
    try:
        placed = client.post(
            "/api/orders",
            json=checkout_payload(
                payment_method="cash_on_delivery", coupon_code="TESTREDEEM10"
            ),
            headers=headers,
        )
        assert placed.status_code == 201
        cancelled = client.post(
            "/api/orders",
            json=checkout_payload(
                payment_method="cash_on_delivery", coupon_code="TESTREDEEM10"
            ),
            headers=headers,
        )
        assert cancelled.status_code == 201
        assert (
            client.post(f"/api/orders/{cancelled.json()['id']}/cancel", headers=headers).status_code
            == 200
        )

        tin = client.get("/api/admin/coupons", headers=admin).json()
        fixture = next(c for c in tin if c["code"] == "TESTREDEEM10")
        # one kept order carried the code; the cancelled one never counts
        assert fixture["redemptions"] == 1
    finally:
        client.delete(f"/api/admin/coupons/{minted.json()['id']}", headers=admin)


# --------------------------------------------------------------------------
# VAT-inclusive checkout — the German B2C mode, priced through the shared
# service (app/pricing) with hand-computed expected cents
# --------------------------------------------------------------------------

from app.config import get_settings as _get_settings  # noqa: E402
from sqlalchemy import select as _select  # noqa: E402


def _make_vat_fixture_products():
    """Two fresh bakes at controlled prices, classified through the real
    admin desk flow (which exercises the audit trail): €7.00 at 7 %,
    €5.00 at 19 %. The classification is the test's own fixture choice,
    never a legal claim about the bake."""
    admin = admin_headers()
    seven = client.post(
        "/api/admin/products",
        json={"name": "VAT Fixture Seven", "price": 7.00, "stock": 40, "category_id": 1},
        headers=admin,
    ).json()
    nineteen = client.post(
        "/api/admin/products",
        json={"name": "VAT Fixture Nineteen", "price": 5.00, "stock": 40, "category_id": 1},
        headers=admin,
    ).json()
    for product, category in ((seven, "FOOD_REDUCED"), (nineteen, "BEVERAGE_STANDARD")):
        r = client.put(
            f"/api/admin/products/{product['id']}/vat",
            json={"vat_category": category, "vat_review_status": "approved", "milk_content_percent": None},
            headers=admin,
        )
        assert r.status_code == 200, r.text
    return seven, nineteen


def _go_inclusive(monkeypatch):
    monkeypatch.setattr(_get_settings(), "tax_mode", "inclusive", raising=True)


def test_inclusive_quote_prices_a_seven_percent_basket(monkeypatch):
    """€7.00 gross at 7 %: net €6.542→6.54, VAT €0.46; delivery €3.50 all
    at 7 % (single rate carries the whole charge): net €3.27, VAT €0.23.
    Total €10.50, VAT included €0.69 — every figure hand-computed."""
    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    quote = client.post("/api/orders/quote", json={"items": [{"product_id": seven["id"], "quantity": 1}]})
    assert quote.status_code == 200
    q = quote.json()
    assert q["tax_included"] is True
    assert q["subtotal"] == 7.00
    assert q["shipping_fee"] == 3.50      # below the suite's €30 line
    assert q["total"] == 10.50
    assert q["tax"] == 0.69               # 0.46 products + 0.23 delivery


def test_inclusive_quote_prices_a_nineteen_percent_basket(monkeypatch):
    """€5.00 gross at 19 %: net €4.20, VAT €0.80; delivery €3.50 at 19 %:
    net €2.94, VAT €0.56. Total €8.50, VAT included €1.36."""
    _go_inclusive(monkeypatch)
    _, nineteen = _make_vat_fixture_products()
    q = client.post("/api/orders/quote", json={"items": [{"product_id": nineteen["id"], "quantity": 1}]}).json()
    assert q["tax_included"] is True
    assert q["total"] == 8.50
    assert q["tax"] == 1.36


def test_inclusive_mixed_basket_allocates_shipping_proportionally(monkeypatch):
    """The brief's centrepiece. €7.00@7 + €5.00@19, delivery €3.50:

    products: 7 % net 6.54/vat 0.46; 19 % net 4.20/vat 0.80
    delivery by net basis (6.54 : 4.20 of 10.74): 350¢ shares 213.13 and
    136.86 — floors 213/136, the leftover cent to the larger remainder
    (the 19 % bucket): €2.13 (net 1.99, vat 0.14) and €1.37 (net 1.15,
    vat 0.22).
    totals: VAT 0.46+0.80+0.14+0.22 = 1.62; payable 12.00+3.50 = 15.50."""
    _go_inclusive(monkeypatch)
    seven, nineteen = _make_vat_fixture_products()
    q = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": seven["id"], "quantity": 1}, {"product_id": nineteen["id"], "quantity": 1}]},
    ).json()
    assert q["subtotal"] == 12.00
    assert q["shipping_fee"] == 3.50
    assert q["total"] == 15.50
    assert q["tax"] == 1.62
    # the delivered allocation itself is pinned at engine level elsewhere;
    # here the end-to-end figures must reconcile with the parts
    assert q["total"] == round(q["subtotal"] - q["discount_amount"] + q["shipping_fee"], 2)


def test_inclusive_free_delivery_carries_no_shipping_vat(monkeypatch):
    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    # 5 × €7.00 = €35.00 — over the suite's €30 free line
    q = client.post("/api/orders/quote", json={"items": [{"product_id": seven["id"], "quantity": 5}]}).json()
    assert q["shipping_fee"] == 0.0
    assert q["total"] == 35.00
    # VAT is the products' alone: 35 gross at 7 % → net 32.71, vat 2.29
    assert q["tax"] == 2.29


def test_inclusive_vip_earns_the_smaller_line(monkeypatch):
    from app.loyalty import VIP_ORDERS

    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    token = register_and_login("vip.incl@example.com", "VipIncl123!")
    vip_id = client.get("/api/auth/me", headers=auth_header(token)).json()["id"]
    _seed_kept_orders(vip_id, VIP_ORDERS)
    # 3 × €7.00 = €21.00 — under the standard €30, over the VIP €15
    q = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": seven["id"], "quantity": 3}]},
        headers=auth_header(token),
    ).json()
    assert q["free_delivery_threshold"] == 15.0
    assert q["shipping_fee"] == 0.0
    assert q["total"] == 21.00


def test_inclusive_coupon_takes_its_percent_off_gross_per_line(monkeypatch):
    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    admin = admin_headers()
    minted = client.post(
        "/api/admin/coupons", json={"code": "INCL10", "percent_off": 10}, headers=admin
    )
    try:
        q = client.post(
            "/api/orders/quote",
            json={"items": [{"product_id": seven["id"], "quantity": 2}], "coupon_code": "INCL10"},
        ).json()
        # gross 14.00, −10 % per line = 12.60 payable; VAT extracted from the
        # discounted gross: 12.60@7 → net 11.78, vat 0.82; delivery 3.50@7
        # → net 3.27, vat 0.23. Total 16.10, included VAT 1.05.
        assert q["discount_amount"] == 1.40
        assert q["total"] == 16.10
        assert q["tax"] == 1.05
    finally:
        client.delete(f"/api/admin/coupons/{minted.json()['id']}", headers=admin)


def test_inclusive_fully_discounted_basket_pays_only_delivery(monkeypatch):
    _go_inclusive(monkeypatch)
    seven, nineteen = _make_vat_fixture_products()
    admin = admin_headers()
    minted = client.post(
        "/api/admin/coupons", json={"code": "INCL100", "percent_off": 100}, headers=admin
    )
    try:
        q = client.post(
            "/api/orders/quote",
            json={
                "items": [{"product_id": seven["id"], "quantity": 1}, {"product_id": nineteen["id"], "quantity": 1}],
                "coupon_code": "INCL100",
            },
        ).json()
        assert q["discount_amount"] == 12.00
        assert q["total"] == 3.50          # the delivery charge alone
        # every product net is zero: the charge allocates by gross value
        # (7.00 : 5.00 of 12.00 → 205¢/145¢): €2.05@7 (vat 0.13) + €1.45@19
        # (vat 0.23) — included VAT 0.36, hand-computed.
        assert q["tax"] == 0.36
    finally:
        client.delete(f"/api/admin/coupons/{minted.json()['id']}", headers=admin)


def test_inclusive_mode_refuses_unclassified_products(monkeypatch):
    """Fail-closed, per product, by name — quote AND placement."""
    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    croissant = get_product("butter-croissant")  # seeded catalog: unclassified

    quote = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": seven["id"], "quantity": 1}, {"product_id": croissant["id"], "quantity": 1}]},
    )
    assert quote.status_code == 503
    assert "Butter Croissant" in quote.json()["detail"]
    assert "VAT" in quote.json()["detail"]

    stock_before = croissant["stock"]
    placed = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery", items=[{"product_id": croissant["id"], "quantity": 1}]),
        headers=auth_header(login_customer()),
    )
    assert placed.status_code == 503
    assert get_product("butter-croissant")["stock"] == stock_before  # nothing reserved


def test_inclusive_mode_refuses_unapproved_classifications(monkeypatch):
    _go_inclusive(monkeypatch)
    admin = admin_headers()
    product = client.post(
        "/api/admin/products", json={"name": "VAT Fixture Pending", "price": 4.00, "stock": 10}, headers=admin
    ).json()
    # classified but left pending — the desk never approved it
    client.put(
        f"/api/admin/products/{product['id']}/vat",
        json={"vat_category": "FOOD_REDUCED", "vat_review_status": "pending"},
        headers=admin,
    )
    quote = client.post("/api/orders/quote", json={"items": [{"product_id": product["id"], "quantity": 1}]})
    assert quote.status_code == 503
    assert "VAT Fixture Pending" in quote.json()["detail"]


def test_inclusive_checkout_persists_the_full_snapshot(monkeypatch):
    """Quote, invoice, per-line snapshots and the per-rate ledger all agree,
    and the ledger reconciles to the payable to the cent."""
    from app.database import SessionLocal as _S
    from app.models import OrderItem as _OI, OrderTaxLine as _OTL

    _go_inclusive(monkeypatch)
    seven, nineteen = _make_vat_fixture_products()
    items = [{"product_id": seven["id"], "quantity": 1}, {"product_id": nineteen["id"], "quantity": 1}]

    quoted = client.post("/api/orders/quote", json={"items": items}).json()
    placed = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery", items=items),
        headers=auth_header(login_customer()),
    )
    assert placed.status_code == 201
    order = placed.json()
    assert order["tax_included"] is True
    assert order["total"] == quoted["total"] == 15.50
    assert order["tax"] == quoted["tax"] == 1.62

    db = _S()
    try:
        lines = db.scalars(_select(_OI).where(_OI.order_id == order["id"])).all()
        by_name = {row.product_name: row for row in lines}
        assert float(by_name["VAT Fixture Seven"].vat_rate) == 7.0
        assert float(by_name["VAT Fixture Seven"].net_line_total) == 6.54
        assert float(by_name["VAT Fixture Seven"].vat_amount) == 0.46
        assert float(by_name["VAT Fixture Seven"].gross_line_total) == 7.00
        assert float(by_name["VAT Fixture Nineteen"].net_line_total) == 4.20
        assert float(by_name["VAT Fixture Nineteen"].vat_amount) == 0.80

        tax_lines = db.scalars(_select(_OTL).where(_OTL.order_id == order["id"])).all()
        by_rate = {float(t.vat_rate): t for t in tax_lines}
        # products + the delivery slice allocated to each rate
        assert float(by_rate[7.0].net_amount) == round(6.54 + 1.99, 2)
        assert float(by_rate[19.0].net_amount) == round(4.20 + 1.15, 2)
        assert round(float(by_rate[7.0].gross_amount) + float(by_rate[19.0].gross_amount), 2) == 15.50
        assert round(
            float(by_rate[7.0].net_amount) + float(by_rate[7.0].vat_amount)
            + float(by_rate[19.0].net_amount) + float(by_rate[19.0].vat_amount), 2
        ) == 15.50
    finally:
        db.close()


def test_inclusive_snapshots_survive_reclassification_and_refund(monkeypatch):
    """Historical orders are immutable money: reclassify the product, cancel
    the paid order — the stored figures never move."""
    _go_inclusive(monkeypatch)
    seven, _ = _make_vat_fixture_products()
    placed = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery", items=[{"product_id": seven["id"], "quantity": 1}]),
        headers=auth_header(login_customer()),
    ).json()
    order_id = placed["id"]

    # the desk re-classifies the bake afterwards (a legal correction)
    client.put(
        f"/api/admin/products/{seven['id']}/vat",
        json={"vat_category": "BEVERAGE_STANDARD", "vat_review_status": "approved"},
        headers=admin_headers(),
    )
    assert client.post(f"/api/orders/{order_id}/cancel", headers=auth_header(login_customer())).status_code == 200

    detail = client.get(f"/api/orders/{order_id}", headers=auth_header(login_customer())).json()
    assert detail["tax"] == placed["tax"]           # 7 % figures, frozen
    assert detail["total"] == placed["total"]
    assert detail["tax_included"] is True


def test_vat_classification_report_counts_the_catalog_honestly(monkeypatch):
    admin = admin_headers()
    seven, nineteen = _make_vat_fixture_products()
    report = client.get("/api/admin/vat/report", headers=admin).json()

    assert report["tax_mode"] == "exclusive"        # the shipped default
    assert report["counts"]["total"] >= 2
    assert "VAT Fixture Seven" not in report["blockers"]
    assert "Butter Croissant" in report["blockers"]  # the seeded catalog: unclassified
    assert report["checkout_ready_for_inclusive"] is False
    assert {"code": "REDUCED", "rate": 7.0} in report["rates_in_force"]
    assert {"code": "STANDARD", "rate": 19.0} in report["rates_in_force"]
    assert report["shipping_vat"]["follows_product_vat"] is True
