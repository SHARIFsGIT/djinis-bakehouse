"""End-to-end API tests covering the main customer and admin flows."""

import os

os.environ["DATABASE_URL"] = "sqlite:///./test_bakehouse.db"
# The whole suite shares one TestClient IP — relax the auth rate limits so
# repeated logins between tests are not throttled.
os.environ["LOGIN_RATE_LIMIT"] = "1000"
os.environ["REGISTER_RATE_LIMIT"] = "1000"
os.environ["FORGOT_RATE_LIMIT"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
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
    assert order["payment_status"] == "paid"  # card pays instantly
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

    # customer buys by card; the bakery moves it into the oven
    order = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
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

    # a zero percent turns the penalty off entirely
    client.put("/api/admin/settings", json={"late_cancel_fee_percent": 0}, headers=admin)
    order2 = client.post("/api/orders", json=checkout_payload(), headers=customer).json()
    client.put(
        f"/api/admin/orders/{order2['id']}/status", json={"status": "baking"}, headers=admin
    )
    cancelled2 = client.post(f"/api/orders/{order2['id']}/cancel", headers=customer).json()
    assert cancelled2["cancel_fee"] == 0
    assert cancelled2["payment_status"] == "refunded"

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
    assert get_product("fresh-bagels")["stock"] == before - 2

    cancelled = client.put(
        f"/api/admin/orders/{order['id']}/status", json={"status": "cancelled"}, headers=admin
    )
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["payment_status"] == "refunded"  # card order, bakery-side cancel is free
    assert body["cancel_fee"] == 0
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
    users = client.get("/api/admin/users", headers=headers).json()
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


def test_bulk_enquiry_flow():
    import datetime

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

    created = client.post("/api/bulk-orders", json=payload)
    assert created.status_code == 201
    enquiry = created.json()
    assert enquiry["status"] == "new"
    assert enquiry["event_date"] == tomorrow.isoformat()

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
