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
# Card checkout runs through Stripe — the suite swaps the whole gateway for
# a fake (below), but the key must exist so checkout considers it configured.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_gateway"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"

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

    # tax is charged on the discounted amount
    taxable = order["subtotal"] - order["discount_amount"]
    assert abs(order["tax"] - round(taxable * 0.0875, 2)) < 0.01

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
    assert vouchers["FRESHBAKE15"]["min_subtotal"] == 40.0

    # paused codes leave the board
    admin = admin_headers()
    client.put("/api/admin/coupons/1", json={"is_active": False}, headers=admin)
    assert "WELCOME10" not in {v["code"] for v in client.get("/api/coupons").json()}
    client.put("/api/admin/coupons/1", json={"is_active": True}, headers=admin)


def test_coupon_min_subtotal_enforced():
    token = login_customer()
    headers = auth_header(token)

    preview = client.get("/api/orders/coupons/freshbake15", headers=headers).json()
    assert preview["min_subtotal"] == 40.0

    # too small a basket: rejected with the threshold named
    small = checkout_payload(coupon_code="FRESHBAKE15")
    small["items"] = small["items"][:1]
    rejected = client.post("/api/orders", json=small, headers=headers)
    assert rejected.status_code == 400
    assert "40.00" in rejected.json()["detail"]

    # over the threshold it applies like any other code (8 maritozzi ≈ $42)
    maritozzo = get_product("maritozzo")
    order = client.post(
        "/api/orders",
        json=checkout_payload(
            coupon_code="FRESHBAKE15",
            items=[{"product_id": maritozzo["id"], "quantity": 8}],
        ),
        headers=headers,
    ).json()
    assert order["coupon_code"] == "FRESHBAKE15"
    assert order["discount_amount"] == round(order["subtotal"] * 0.15, 2)


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
