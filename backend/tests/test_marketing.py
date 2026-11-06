"""The manual-classification desk, the GDPR marketing desk and the
personalised-voucher desk — every acceptance criterion §3, §4 and §9 of the
release brief own.

The suite shares the process with test_api.py / test_costing.py: whichever
module imports the app package first creates and seeds `test_bakehouse.db`;
this file boots another client against the same database (idempotent) and
never removes the file. Nothing here sends real mail (SMTP_HOST stays unset,
so send_email logs instead — asserted, not assumed), touches Stripe, or
writes production: every user, voucher and campaign lives in the throwaway
database pytest owns.

Determinism notes: the other suites never grant marketing consent, classify
a customer, mint a voucher or write a campaign, so this file's audience and
cap assertions are exact in both run modes (standalone and full suite).
Redemption-gate tests seed their Coupon rows directly in the book — the
gates under test live in orders._load_coupon, not the mint endpoint, and
direct seeding keeps them independent of the costing-book cap.
"""

import logging
import os
import sys
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

# Same values test_api.py sets — harmless when both files load, required
# when this file is imported first.
os.environ["DATABASE_URL"] = "sqlite:///./test_bakehouse.db"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["LOGIN_RATE_LIMIT"] = "1000"
os.environ["REGISTER_RATE_LIMIT"] = "1000"
os.environ["FORGOT_RATE_LIMIT"] = "1000"
os.environ["OTP_RATE_LIMIT"] = "1000"
os.environ["QUOTE_RATE_LIMIT"] = "1000"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_gateway"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"
os.environ["TAX_RATE"] = "0.05"
os.environ["SHIPPING_FEE"] = "3.50"
os.environ["FREE_SHIPPING_THRESHOLD"] = "30.00"
os.environ["VIP_FREE_DELIVERY_THRESHOLD"] = "15.00"

# Only the first test module into the process owns the database file.
if "app" not in sys.modules:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"test_bakehouse.db{suffix}")
        except FileNotFoundError:
            pass

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import create_app, pricing  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, utcnow  # noqa: E402
from app.models import Coupon, Product, ProductCostRecord, Suppression, User  # noqa: E402

client = TestClient(create_app())
client.__enter__()

ADMIN_EMAIL = "admin@djinisbakehouse.com"
ADMIN_PASSWORD = "Admin123!"
# This file's private email space — the demo dataset never uses this domain,
# so search-scoped roll assertions stay exact. (A registrable name: the
# schema rejects reserved TLDs like .test.)
DOMAIN = "@mktsuite.example.com"


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(email: str, password: str = "Passw0rd!") -> str:
    client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "first_name": "Test", "last_name": "Person"},
    )
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def register_with_id(email: str) -> tuple[str, int]:
    """A registered customer's (token, id) — the id every desk write needs."""
    token = register_and_login(email)
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    return token, me["id"]


def admin_headers() -> dict:
    response = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return auth_header(response.json()["access_token"])


def set_classification(user_id: int, value, headers=None):
    payload = {"classification": value}
    return client.put(
        f"/api/admin/users/{user_id}/classification",
        json=payload,
        headers=headers or admin_headers(),
    )


def roll(**params) -> dict:
    """One page of the Users roll, with the status checked so a failed
    listing says its actual error instead of keying into a detail body."""
    response = client.get("/api/admin/users", params=params, headers=admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


def roll_row(user_id: int, **params) -> dict | None:
    """The customer's row from the Users roll, or None if filtered out."""
    page = roll(**params)
    return next((row for row in page["items"] if row["id"] == user_id), None)


def grant_consent(user_id: int, source="counter card", evidence="signed card 2026-11-01"):
    response = client.put(
        f"/api/admin/users/{user_id}/marketing-consent",
        json={"granted": True, "source": source, "evidence": evidence},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def seed_coupon(**fields) -> Coupon:
    """A coupon row written straight into the book — the _load_coupon gates
    under test are engine-level, and direct seeding keeps them independent
    of the costing-book cap the mint endpoint consults."""
    db = SessionLocal()
    try:
        coupon = Coupon(code=fields.pop("code"), percent_off=fields.pop("percent_off", 0), **fields)
        db.add(coupon)
        db.commit()
        db.refresh(coupon)
        return coupon
    finally:
        db.close()


def seed_orders(user_id: int, count: int) -> None:
    """Kept (delivered) orders straight into the book — the loyalty tier's
    own measure, without spending the catalog's stock on it."""
    from app.models import Order

    db = SessionLocal()
    try:
        for n in range(count):
            db.add(
                Order(
                    order_number=f"DJ-MKT-{user_id}-{n}",
                    user_id=user_id,
                    status="delivered",
                    payment_method="cash_on_delivery",
                    payment_status="paid",
                    subtotal=10.00,
                    total=10.00,
                    currency="eur",
                    recipient="Loyalty Fixture",
                    line1="1 Fixture Row",
                    city="Bonn",
                    state="NW",
                    postal_code="53111",
                    country="DE",
                )
            )
        db.commit()
    finally:
        db.close()


def user_row(user_id: int) -> User:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        db.expunge(user)
        return user
    finally:
        db.close()


def marketing_token_of(user_id: int) -> str | None:
    return user_row(user_id).marketing_token


# ==========================================================================
# §3 — the manual classification desk
# ==========================================================================


def test_classification_is_admin_only():
    """No token 401s; a customer 403s even on their own account — the desk's
    word is the admin's to give, never the customer's (users cannot
    self-classify, and the ledger records only admin hands)."""
    _, customer_id = register_with_id(f"cls.self{DOMAIN}")
    other_token, _ = register_with_id(f"cls.other{DOMAIN}")

    no_token = client.put(
        f"/api/admin/users/{customer_id}/classification", json={"classification": "vip"}
    )
    assert no_token.status_code == 401

    as_customer = client.put(
        f"/api/admin/users/{customer_id}/classification",
        json={"classification": "vip"},
        headers=auth_header(other_token),
    )
    assert as_customer.status_code == 403
    assert client.get(
        f"/api/admin/users/{customer_id}/classification-history",
        headers=auth_header(other_token),
    ).status_code == 403
    assert user_row(customer_id).classification is None


def test_classification_rejects_unknown_values_and_users():
    _, customer_id = register_with_id(f"cls.bad{DOMAIN}")
    headers = admin_headers()
    # An invented state never reaches the database — the schema knows four.
    assert set_classification(customer_id, "whale", headers).status_code == 422
    assert set_classification(999999, "vip", headers).status_code == 404
    assert client.get(
        "/api/admin/users/999999/classification-history", headers=headers
    ).status_code == 404
    assert user_row(customer_id).classification is None


def test_classification_roundtrip_through_every_state_with_ledger():
    """All five states — vip, loyal, lapsed, regular, cleared — and the
    ledger narrating every hop (who, from what, to what, newest first)."""
    _, customer_id = register_with_id(f"cls.round{DOMAIN}")
    hops = ["vip", "loyal", "lapsed", "regular", None]

    for state in hops:
        response = set_classification(customer_id, state)
        assert response.status_code == 200, response.text

    assert user_row(customer_id).classification is None  # the clear landed
    history = client.get(
        f"/api/admin/users/{customer_id}/classification-history", headers=admin_headers()
    ).json()
    # Newest first: the ledger reads like a story told backwards.
    assert [event["to_classification"] for event in history] == hops[::-1]
    assert [event["from_classification"] for event in history] == hops[:-1][::-1] + [None]
    assert all(event["admin_name"] for event in history)  # the admin's name, snapshotted


def test_identical_classification_write_is_a_noop():
    """The ledger records changes, not echoes — setting what is already set
    writes no event, so the history stays meaningful."""
    _, customer_id = register_with_id(f"cls.noop{DOMAIN}")
    assert set_classification(customer_id, "vip").status_code == 200
    assert set_classification(customer_id, "vip").status_code == 200

    history = client.get(
        f"/api/admin/users/{customer_id}/classification-history", headers=admin_headers()
    ).json()
    assert len(history) == 1


def test_manual_classification_is_independent_of_measured_loyalty():
    """The desk's word and the measured tier are two different columns with
    two different writers: three kept orders make this customer loyal by
    measurement while the desk calls them vip — each filter finds them by
    its own measure and never the other's."""
    token, customer_id = register_with_id(f"cls.indep{DOMAIN}")
    seed_orders(customer_id, 3)
    assert set_classification(customer_id, "vip").status_code == 200

    row = roll_row(customer_id, search=f"cls.indep{DOMAIN}")
    assert row is not None
    assert row["loyalty"] == "loyal"  # measured, untouched by the write
    assert row["classification"] == "vip"  # the desk's word, untouched by orders

    assert roll_row(customer_id, search=f"cls.indep{DOMAIN}", tier="loyal") is not None
    assert roll_row(customer_id, search=f"cls.indep{DOMAIN}", tier="vip") is None
    assert roll_row(customer_id, search=f"cls.indep{DOMAIN}", classification="vip") is not None
    assert roll_row(customer_id, search=f"cls.indep{DOMAIN}", classification="loyal") is None
    assert roll_row(customer_id, search=f"cls.indep{DOMAIN}", classification="none") is None


def test_users_roll_filters_and_paginates_by_classification():
    """Seven fresh customers — three vip, two loyal, two unclassified —
    filtered, counted and paginated by the desk's word, combined with the
    search box the roll already had."""
    cohort = []
    plan = ["vip"] * 3 + ["loyal"] * 2 + [None] * 2
    for index, state in enumerate(plan):
        _, user_id = register_with_id(f"cls.c{index}{DOMAIN}")
        if state:
            assert set_classification(user_id, state).status_code == 200
        cohort.append(user_id)

    vip = roll(classification="vip", search="cls.c", per_page=50)
    assert sorted(row["id"] for row in vip["items"]) == sorted(cohort[:3])
    assert vip["classification_counts"].get("vip", 0) >= 3

    none_rows = roll(classification="none", search="cls.c", per_page=50)
    assert sorted(row["id"] for row in none_rows["items"]) == sorted(cohort[5:])

    # Pagination rides on top of the search: five to a shelf, two on the
    # next, and the cohort is exactly seven ("cls.c" matches nobody else).
    page_one = roll(search="cls.c", per_page=5)
    page_two = roll(search="cls.c", per_page=5, page=2)
    assert len(page_one["items"]) == 5
    assert len(page_two["items"]) == 2
    assert page_one["pagination"]["total_items"] == 7


def test_the_roll_survives_deleted_accounts():
    """A GDPR shell row must not 500 the desk: the deleted account's
    placeholder address is schema-valid (an `.invalid` suffix is not, and
    one such row used to break every Users listing)."""
    token, customer_id = register_with_id(f"cls.gone{DOMAIN}")
    set_classification(customer_id, "regular")
    gone = client.post(
        "/api/auth/me/delete",
        json={"password": "Passw0rd!"},
        headers=auth_header(token),
    )
    assert gone.status_code in (200, 204), gone.text

    # The whole roll still lists — shell included, valid email and all.
    page = roll(per_page=50)
    shell = next(row for row in page["items"] if row["id"] == customer_id)
    assert shell["email"].endswith("@deleted.djinisbakehouse.com")
    assert shell["classification"] is None  # the desk's word went too
    assert shell["is_active"] is False


# ==========================================================================
# §4 — consent, campaigns, the suppression list
# ==========================================================================


def test_consent_grant_needs_source_and_evidence():
    """GDPR Art. 7 demonstrability: an account is never consent. A grant
    without a source and evidence is refused; with them it records all
    three fields and leaves the person off the suppression list."""
    _, customer_id = register_with_id(f"consent.grant{DOMAIN}")

    refused = client.put(
        f"/api/admin/users/{customer_id}/marketing-consent",
        json={"granted": True, "source": "counter card"},
        headers=admin_headers(),
    )
    assert refused.status_code == 422

    state = grant_consent(customer_id)
    assert state["marketing_consent_at"] is not None
    assert state["marketing_consent_source"] == "counter card"
    assert state["suppressed"] is False


def test_withdrawal_suppresses_and_fresh_consent_lifts():
    """Withdrawal clears the record AND suppresses the address (nothing
    marketing mails it again); a freshly recorded consent is the one thing
    that lifts a suppression."""
    _, customer_id = register_with_id(f"consent.cycle{DOMAIN}")
    grant_consent(customer_id)

    withdrawn = client.put(
        f"/api/admin/users/{customer_id}/marketing-consent",
        json={"granted": False},
        headers=admin_headers(),
    ).json()
    assert withdrawn["marketing_consent_at"] is None
    assert withdrawn["suppressed"] is True
    assert withdrawn["suppression_reason"] == "withdrawn"

    listing = client.get("/api/admin/marketing/suppression", headers=admin_headers()).json()
    assert any(row["reason"] == "withdrawn" for row in listing)

    regranted = grant_consent(customer_id, source="re-signed card", evidence="photo of card")
    assert regranted["suppressed"] is False  # the only way out is a new consent


def test_campaign_preview_splits_the_audience_by_consent():
    """A fresh account is NOT an opt-in: no consent, no recipients. The
    preview names the holds with their reasons before anything exists."""
    _, quiet_id = register_with_id(f"mkt.silent{DOMAIN}")
    _, loud_id = register_with_id(f"mkt.audience{DOMAIN}")
    grant_consent(loud_id)

    preview = client.post(
        "/api/admin/marketing/campaigns/preview",
        json={"name": "November news", "subject": "News", "body": "Hello {{first_name}}"},
        headers=admin_headers(),
    ).json()
    # Exactly the three consenters this file recorded by this point — an
    # account alone (the other suites' users, the demo dataset, the quiet
    # account below) is never an opt-in.
    assert preview["recipients"] == 3
    held = {entry["name"] for entry in preview["held"]}
    assert user_row(quiet_id).full_name in held
    reasons = {entry["reason"] for entry in preview["held"]}
    assert "no marketing consent" in reasons
    # The quiet account's hold is about consent, not suppression.
    assert user_row(quiet_id).marketing_consent_at is None


def test_campaign_send_is_real_in_the_book_but_never_real_mail(caplog):
    """The full send path against the mock provider: one delivery row per
    person (holds included, with reasons), the background sender marks them
    sent, the counts true up — and because SMTP_HOST is unset, the email
    lands in the log, not on the network."""
    _, sendy_id = register_with_id(f"mkt.send{DOMAIN}")
    grant_consent(sendy_id)
    set_classification(sendy_id, "vip")
    # Three vip customers from the roll tests have no consent — the segment
    # names them, the permission gate holds them.
    headers = admin_headers()

    created = client.post(
        "/api/admin/marketing/campaigns",
        json={
            "name": "Vip November",
            "subject": "For our vip regulars",
            "body": "Hi {{first_name}}, new bakes this week.",
            "segment": {"classifications": ["vip"]},
        },
        headers=headers,
    ).json()
    assert created["status"] == "draft"

    with caplog.at_level(logging.INFO, logger="bakery"):
        sent = client.post(f"/api/admin/marketing/campaigns/{created['id']}/send", headers=headers)
    assert sent.status_code == 200, sent.text

    detail = client.get(
        f"/api/admin/marketing/campaigns/{created['id']}", headers=headers
    ).json()
    assert detail["status"] == "sent"
    delivered = [row for row in detail["deliveries"] if row["status"] == "sent"]
    held = [row for row in detail["deliveries"] if row["status"] == "suppressed"]
    assert [row["email"] for row in delivered] == [f"mkt.send{DOMAIN}"]
    assert held, "the unconsenting vip customers are recorded as held, not silently dropped"
    assert all(row["reason"] == "no marketing consent" for row in held)
    assert detail["sent_count"] == 1
    assert detail["suppressed_count"] == len(held)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"email to mkt.send{DOMAIN}" in logged
    assert "SMTP_HOST is not set" in logged  # the mock provider, proven
    # Personalisation and the unsubscribe footer both made it into the copy.
    assert "Hi Test," in logged  # {{first_name}} replaced
    assert "/unsubscribe?token=" in logged

    # A sent campaign never sends twice.
    assert client.post(
        f"/api/admin/marketing/campaigns/{created['id']}/send", headers=headers
    ).status_code == 409


def test_unsubscribe_rotates_the_token_and_suppresses():
    """The footer link works without a login: consent cleared, address
    suppressed, token rotated so the spent link dies — and the endpoint
    never confirms which tokens exist."""
    _, customer_id = register_with_id(f"unsub.walk{DOMAIN}")
    grant_consent(customer_id)
    token = marketing_token_of(customer_id)
    assert token

    done = client.post("/api/marketing/unsubscribe", json={"token": token})
    assert done.status_code == 200
    assert done.json()["first_name"] == "Test"

    after = user_row(customer_id)
    assert after.marketing_consent_at is None
    assert after.marketing_token != token  # the old link is dead
    db = SessionLocal()
    try:
        row = db.scalar(select(Suppression).where(Suppression.email == after.email))
        assert row is not None and row.reason == "unsubscribed"
    finally:
        db.close()

    # The spent token, a made-up token: same answer, no oracle.
    assert client.post("/api/marketing/unsubscribe", json={"token": token}).status_code == 404
    assert client.post(
        "/api/marketing/unsubscribe", json={"token": "x" * 43}
    ).status_code == 404

    # Suppression holds the address out of every later audience — proven on
    # a message addressed to them alone, so the hold is unambiguous.
    alone = client.post(
        "/api/admin/marketing/campaigns/preview",
        json={
            "name": "After unsub", "subject": "s", "body": "b",
            "recipient_user_id": customer_id,
        },
        headers=admin_headers(),
    ).json()
    assert alone["recipients"] == 0
    assert [entry["reason"] for entry in alone["held"]] == ["suppressed (unsubscribed or withdrawn)"]


def test_transactional_mail_ignores_marketing_permission(caplog):
    """The GDPR separation, exercised end to end: the customer above said
    no to marketing (suppressed, consent gone) and their order receipt
    still arrives — receipts ride the transactional path suppression never
    touches."""
    token = client.post(
        "/api/auth/login",
        json={"email": f"unsub.walk{DOMAIN}", "password": "Passw0rd!"},
    ).json()["access_token"]
    croissant = client.get("/api/products/butter-croissant").json()

    with caplog.at_level(logging.INFO, logger="bakery"):
        placed = client.post(
            "/api/orders",
            json={
                "items": [{"product_id": croissant["id"], "quantity": 1}],
                "new_address": {
                    "label": "Home",
                    "recipient": "Test Person",
                    "phone": "555-0101",
                    "line1": "1 Lane",
                    "city": "Bonn",
                    "state": "NW",
                    "postal_code": "53111",
                    "country": "DE",
                },
                "payment_method": "cash_on_delivery",
                "note": "cash test",
            },
            headers=auth_header(token),
        )
    assert placed.status_code == 201, placed.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"email to unsub.walk{DOMAIN}" in logged  # the receipt went out


# ==========================================================================
# §4 — personalised vouchers
# ==========================================================================


def test_voucher_cap_is_fail_closed_without_costing_data():
    """The engine's brake, proven pure: a database with products but no
    cost records yields no cap, and no cap means no voucher — never a
    guessed floor."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app import costing

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Product(name="Loaf", slug="loaf-1", price=3.5))
        db.commit()
        assert costing.max_safe_voucher_discount(db, "eur") is None
    finally:
        db.close()
        engine.dispose()


def test_voucher_caps_come_from_the_costing_book():
    """A complete EUR cost record sets the cap: the thinnest margin among
    costed products, floored to whole percents and to the cent on the fixed
    side. Minting above the cap is refused for both kinds; the codes are
    unique and DJ- shaped; one discount kind exactly."""
    headers = admin_headers()
    # A €10.00 bake costing €5.40 to make: floor price 5.40/0.60 = €9.00,
    # so this product alone allows 10 % or €1.00 off.
    product = client.post(
        "/api/admin/products",
        json={"name": "Voucher Cap Loaf", "price": 10.00, "stock": 5, "category_id": 1},
        headers=headers,
    ).json()
    db = SessionLocal()
    try:
        db.add(
            ProductCostRecord(
                product_id=product["id"],
                trigger="initial",
                currency="eur",
                total_cost=Decimal("5.40"),
                is_complete=True,
            )
        )
        db.commit()
    finally:
        db.close()

    overview = client.get("/api/admin/marketing/overview", headers=headers).json()
    # Earlier suites may have costed other products: the cap is the minimum,
    # so ours bounds it from above and the overview must agree exactly.
    assert overview["voucher_percent_cap"] <= 10
    assert overview["voucher_fixed_cap"] <= 1.00
    assert overview["costed_products"] >= 1

    _, customer_id = register_with_id(f"vch.cap{DOMAIN}")
    cap_percent = overview["voucher_percent_cap"]
    cap_fixed = round(overview["voucher_fixed_cap"], 2)

    # Exactly one discount kind — both or neither is a schema refusal.
    both = client.post(
        "/api/admin/marketing/vouchers",
        json={"user_id": customer_id, "percent_off": 5, "amount_off": 1.0},
        headers=headers,
    )
    assert both.status_code == 422

    # Above the cap: refused for percent and fixed alike (the margin floor
    # is not a suggestion), fail-closed even when no cap exists at all.
    assert client.post(
        "/api/admin/marketing/vouchers",
        json={"user_id": customer_id, "percent_off": cap_percent + 1},
        headers=headers,
    ).status_code == 422
    assert client.post(
        "/api/admin/marketing/vouchers",
        json={"user_id": customer_id, "amount_off": round(cap_fixed + 0.01, 2)},
        headers=headers,
    ).status_code == 422

    # Within the cap: minted, unique, always one-per-customer and personal.
    # (A book whose thinnest margin leaves no whole percent or no half euro
    # of headroom mints nothing — the lockout is the honest answer there.)
    minted_codes = set()
    percent_mint = client.post(
        "/api/admin/marketing/vouchers",
        json={"user_id": customer_id, "percent_off": cap_percent or 1},
        headers=headers,
    )
    fixed_mint = client.post(
        "/api/admin/marketing/vouchers",
        json={"user_id": customer_id, "amount_off": 0.5},
        headers=headers,
    )
    for minted, mintable in (
        (percent_mint, cap_percent >= 1),
        (fixed_mint, cap_fixed >= 0.5),
    ):
        if mintable:
            assert minted.status_code == 201, minted.text
            body = minted.json()
            assert body["code"].startswith("DJ-")
            assert body["restricted_to_user_id"] == customer_id
            assert body["once_per_customer"] is True
            minted_codes.add(body["code"])
        else:
            assert minted.status_code == 422
    if cap_percent >= 1 and cap_fixed >= 0.5:
        assert len(minted_codes) == 2  # uniqueness, not a shared code


def test_a_voucher_is_private_to_its_customer():
    """The restriction gate: for a guest or anyone else the code simply
    does not exist (404, not 403 — the board never leaks that it might);
    for its owner it prices the basket. The public board never lists it."""
    owner_token, owner_id = register_with_id(f"vch.owner{DOMAIN}")
    other_token, _ = register_with_id(f"vch.other{DOMAIN}")
    coupon = seed_coupon(
        code="DJ-PRIVATE", percent_off=10, fixed_amount_off=None,
        restricted_to_user_id=owner_id, once_per_customer=True,
    )
    loaf_id, loaf_price = _first_product()

    def quote(token=None):
        return client.post(
            "/api/orders/quote",
            json={"items": [{"product_id": loaf_id, "quantity": 1}], "coupon_code": coupon.code},
            headers=auth_header(token) if token else None,
        )

    assert quote().status_code == 404  # guest
    assert quote(other_token).status_code == 404  # another customer
    owner = quote(owner_token).json()
    assert owner["coupon_code"] == coupon.code
    assert owner["coupon_percent_off"] == 10
    assert owner["coupon_fixed_amount"] is None
    assert owner["discount_amount"] == round(loaf_price * 10 / 100, 2)

    board = client.get("/api/coupons", headers=auth_header(owner_token)).json()
    assert all(row["code"] != coupon.code for row in board)


def test_voucher_expiry_and_redemption_cap():
    """Expired codes are spent, whoever asks; a total-redemption cap counts
    kept orders and closes the code when reached; the once-per-customer
    flag survives a real placement."""
    owner_token, owner_id = register_with_id(f"vch.exp{DOMAIN}")
    capped_token, capped_id = register_with_id(f"vch.maxr{DOMAIN}")
    once_token, once_id = register_with_id(f"vch.once{DOMAIN}")
    loaf_id, _ = _first_product()

    expired = seed_coupon(
        code="DJ-LATE", percent_off=5, restricted_to_user_id=owner_id,
        expires_at=utcnow() - timedelta(hours=1),
    )
    response = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": loaf_id, "quantity": 1}], "coupon_code": expired.code},
        headers=auth_header(owner_token),
    )
    assert response.status_code == 404
    assert "expired" in response.json()["detail"]

    # One kept order already references the capped code: it is fully
    # redeemed, its owner included.
    capped = seed_coupon(
        code="DJ-FULL", percent_off=5, restricted_to_user_id=capped_id,
        max_redemptions=1,
    )
    db = SessionLocal()
    try:
        from app.models import Order

        db.add(
            Order(
                order_number="DJ-MKT-CAP", user_id=capped_id, status="delivered",
                payment_method="cash_on_delivery", payment_status="paid",
                subtotal=10, total=10, currency="eur", coupon_code=capped.code,
                recipient="Cap Test", line1="1", city="Bonn", state="NW",
                postal_code="1", country="DE",
            )
        )
        db.commit()
    finally:
        db.close()
    response = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": loaf_id, "quantity": 1}], "coupon_code": capped.code},
        headers=auth_header(capped_token),
    )
    assert response.status_code == 404
    assert "fully redeemed" in response.json()["detail"]

    # once_per_customer: valid at the quote, spent after a real placement.
    once = seed_coupon(
        code="DJ-ONCE", percent_off=5, restricted_to_user_id=once_id,
        once_per_customer=True,
    )
    items = [{"product_id": loaf_id, "quantity": 1}]
    placed = client.post(
        "/api/orders",
        json={
            "items": items, "coupon_code": once.code,
            "new_address": {
                "label": "Home", "recipient": "Once Test", "phone": "555-0102",
                "line1": "2 Lane", "city": "Bonn", "state": "NW",
                "postal_code": "53111", "country": "DE",
            },
            "payment_method": "cash_on_delivery",
        },
        headers=auth_header(once_token),
    )
    assert placed.status_code == 201, placed.text
    assert placed.json()["coupon_code"] == once.code
    response = client.post(
        "/api/orders/quote",
        json={"items": items, "coupon_code": once.code},
        headers=auth_header(once_token),
    )
    assert response.status_code == 404
    assert "already been used" in response.json()["detail"]


def test_the_public_board_hides_expired_and_spent_codes():
    """The voucher board mirrors what checkout would refuse: a code past
    its expiry or over its redemption cap is not shown, a live one is.
    (Seeded straight into the book — the board's filters, not the promotions
    desk's create shape, are what this pins.)"""
    from app.models import Order

    seed_coupon(code="MKTBOARD5", percent_off=5)
    seed_coupon(code="MKTBOARDEXP", percent_off=5, expires_at=utcnow() - timedelta(days=1))
    seed_coupon(code="MKTBOARDFULL", percent_off=5, max_redemptions=1)
    db = SessionLocal()
    try:
        db.add(
            Order(
                order_number="DJ-MKT-BOARD", user_id=live_user_id(), status="delivered",
                payment_method="cash_on_delivery", payment_status="paid",
                subtotal=10, total=10, currency="eur", coupon_code="MKTBOARDFULL",
                recipient="Board Fixture", line1="1", city="Bonn", state="NW",
                postal_code="1", country="DE",
            )
        )
        db.commit()
    finally:
        db.close()

    codes = {row["code"] for row in client.get("/api/coupons").json()}
    assert "MKTBOARD5" in codes
    assert "MKTBOARDEXP" not in codes
    assert "MKTBOARDFULL" not in codes


def test_voucher_email_needs_consent_like_any_marketing(caplog):
    """A voucher email is marketing whatever it's worth: no consent, no
    send (422, nothing created). With consent it goes out as a
    single-recipient campaign through the same ledger."""
    _, customer_id = register_with_id(f"vch.mail{DOMAIN}")
    coupon = seed_coupon(
        code="DJ-MAIL10", percent_off=10, restricted_to_user_id=customer_id,
        expires_at=utcnow() + timedelta(days=30),
    )
    headers = admin_headers()

    refused = client.post(
        f"/api/admin/marketing/vouchers/{coupon.id}/send", headers=headers
    )
    assert refused.status_code == 422
    assert "consent" in refused.json()["detail"]
    # The refusal rolled the draft back — no voucher campaign was left behind.
    campaigns = client.get("/api/admin/marketing/campaigns", headers=headers).json()
    assert all(row["name"] != f"Voucher {coupon.code}" for row in campaigns)

    grant_consent(customer_id)
    with caplog.at_level(logging.INFO, logger="bakery"):
        sent = client.post(f"/api/admin/marketing/vouchers/{coupon.id}/send", headers=headers)
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["recipient_user_id"] == customer_id
    assert body["status"] == "sending"  # the background sender is queued, not yet run

    detail = client.get(f"/api/admin/marketing/campaigns/{body['id']}", headers=headers).json()
    assert [row["status"] for row in detail["deliveries"]] == ["sent"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"email to vch.mail{DOMAIN}" in logged
    assert coupon.code in logged  # the code itself rode the email


# ==========================================================================
# §9 — the fixed-value voucher through the pricing service
# ==========================================================================


def test_fixed_voucher_exclusive_math_is_pinned():
    """Whole-euro off the subtotal, never past it; the register rate still
    applies to what remains. Hand-computed both ways."""
    fixed = SimpleNamespace(percent_off=0, fixed_amount_off=Decimal("3.00"))
    totals = pricing.price_basket_exclusive(14.00, fixed, 30.00, 0.05, 3.50)
    assert totals.discount == 3.00
    assert totals.taxable == 11.00
    assert totals.tax == 0.55
    assert totals.shipping == 3.50
    assert totals.total == 15.05

    clamped = SimpleNamespace(percent_off=0, fixed_amount_off=Decimal("3.00"))
    totals = pricing.price_basket_exclusive(2.50, clamped, 30.00, 0.05, 3.50)
    assert totals.discount == 2.50  # never past the subtotal
    assert totals.taxable == 0.0
    assert totals.total == 3.50


def test_fixed_voucher_quote_exclusive_end_to_end():
    """The storefront's own view: one €10.00 bake, €1.00 personal voucher —
    discount 1.00, tax on 9.00, delivery below the €30 line, and the fixed
    amount (not a percent) riding the quote."""
    owner_token, owner_id = register_with_id(f"vch.quote{DOMAIN}")
    loaf = client.post(
        "/api/admin/products",
        json={"name": "Voucher Quote Loaf", "price": 10.00, "stock": 5, "category_id": 1},
        headers=admin_headers(),
    ).json()
    coupon = seed_coupon(
        code="DJ-FIXED1", percent_off=0, fixed_amount_off=Decimal("1.00"),
        restricted_to_user_id=owner_id, once_per_customer=True,
    )
    quote = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": loaf["id"], "quantity": 1}], "coupon_code": coupon.code},
        headers=auth_header(owner_token),
    ).json()
    assert quote["tax_included"] is False
    assert quote["subtotal"] == 10.00
    assert quote["discount_amount"] == 1.00
    assert quote["coupon_fixed_amount"] == 1.0
    assert quote["coupon_percent_off"] is None
    assert quote["tax"] == 0.45  # 5 % of 9.00
    assert quote["shipping_fee"] == 3.50
    assert quote["total"] == 12.95


def _vat_fixture_pair():
    """Two approved bakes — €7.00 at 7 %, €5.00 at 19 % — the same fixture
    shape the inclusive suite uses."""
    headers = admin_headers()
    seven = client.post(
        "/api/admin/products",
        json={"name": "Mkt Fixture Seven", "price": 7.00, "stock": 40, "category_id": 1},
        headers=headers,
    ).json()
    nineteen = client.post(
        "/api/admin/products",
        json={"name": "Mkt Fixture Nineteen", "price": 5.00, "stock": 40, "category_id": 1},
        headers=headers,
    ).json()
    for product, category in ((seven, "FOOD_REDUCED"), (nineteen, "BEVERAGE_STANDARD")):
        response = client.put(
            f"/api/admin/products/{product['id']}/vat",
            json={"vat_category": category, "vat_review_status": "approved",
                  "milk_content_percent": None},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    return seven, nineteen


def test_fixed_voucher_inclusive_quote_single_rate(monkeypatch):
    """€14.00 gross at 7 % with a €5.00 voucher: the discount lands on the
    line, VAT is re-extracted from the €9.00 that remains (net 8.41,
    VAT 0.59) and delivery €3.50 carries 0.23 — payable €12.50, VAT 0.82."""
    monkeypatch.setattr(get_settings(), "tax_mode", "inclusive", raising=True)
    seven, _ = _vat_fixture_pair()
    owner_token, owner_id = register_with_id(f"inc.fixed{DOMAIN}")
    coupon = seed_coupon(
        code="DJ-INCL5", percent_off=0, fixed_amount_off=Decimal("5.00"),
        restricted_to_user_id=owner_id,
    )
    quote = client.post(
        "/api/orders/quote",
        json={"items": [{"product_id": seven["id"], "quantity": 2}], "coupon_code": coupon.code},
        headers=auth_header(owner_token),
    ).json()
    assert quote["tax_included"] is True
    assert quote["subtotal"] == 14.00
    assert quote["discount_amount"] == 5.00
    assert quote["coupon_fixed_amount"] == 5.0
    assert quote["shipping_fee"] == 3.50
    assert quote["tax"] == 0.82
    assert quote["total"] == 12.50


def test_fixed_voucher_inclusive_splits_by_gross_weight():
    """The engine's split itself: a €1.00 voucher across €7.00@7 and
    €5.00@19 shares 58/42 cents (largest remainder to the 19 % line), the
    pool never exceeds the shelf, and a voucher larger than the basket
    discounts exactly the basket."""
    seven, nineteen = _vat_fixture_pair()
    db = SessionLocal()
    try:
        p7 = db.get(Product, seven["id"])
        p19 = db.get(Product, nineteen["id"])
        one_euro = SimpleNamespace(percent_off=0, fixed_amount_off=Decimal("1.00"))
        basket = pricing.price_basket_inclusive(db, {p7: 1, p19: 1}, one_euro, Decimal("3.50"))
        by_id = {line.product_id: line for line in basket.lines}
        assert by_id[p7.id].discount_amount == Decimal("0.58")
        assert by_id[p19.id].discount_amount == Decimal("0.42")
        assert basket.discount_total == Decimal("1.00")
        assert basket.total_payable == Decimal("14.50")  # 12.00 − 1.00 + 3.50

        huge = SimpleNamespace(percent_off=0, fixed_amount_off=Decimal("20.00"))
        basket = pricing.price_basket_inclusive(db, {p7: 1, p19: 1}, huge, Decimal("3.50"))
        assert basket.discount_total == Decimal("12.00")  # clamped to the shelf
        assert basket.total_payable == Decimal("3.50")   # only delivery left
        assert all(line.gross_after_discount == 0 for line in basket.lines)
    finally:
        db.close()


def _first_product() -> tuple[int, float]:
    """A purchasable bake from the catalog (id, price)."""
    product = client.get("/api/products/butter-croissant").json()
    return product["id"], float(product["current_price"])


def live_user_id() -> int:
    """Any existing customer account, for order rows that only need a
    legitimate owner (the board fixture above)."""
    db = SessionLocal()
    try:
        from sqlalchemy import func as sa_func

        return db.scalar(
            select(sa_func.max(User.id)).where(User.role == "customer")
        ) or 1
    finally:
        db.close()
