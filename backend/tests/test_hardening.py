"""The 2026-11-22 audit's security hardening, pinned.

Each block names the hole it closes:

* CSV exports ran customer-typed text as spreadsheet formulas;
* two email templates trusted a customer's name as HTML;
* a production box without SMTP wrote reset links into its log;
* the phone sign-in rail answered (and could be guessed at) on a production
  deployment that had no way to send a text, and never capped codes per
  number;
* uploads believed the Content-Type header instead of the file;
* the rate limiter's table of addresses only ever grew.

Rides on test_api.py's client (imported, so the app boots once whichever
file pytest reaches first). Rows the public forms would rate-limit are
seeded straight into the book — the exports are what's under test here.
"""

import csv
import io
import logging
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import test_api as api
from test_api import admin_headers, auth_header, checkout_payload, client

from app import SlidingWindowLimiter, email as email_module, security
from app.config import get_settings
from app.database import SessionLocal
from app.models import BulkEnquiry, Subscriber
from app.routers import auth as auth_router
from app.serialize import csv_safe


def _rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.text)))


# --------------------------------------------------------------------------
# CSV exports: text a spreadsheet shows, never runs
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "typed, exported",
    [
        ('=HYPERLINK("http://evil.example","open")', "'=HYPERLINK(\"http://evil.example\",\"open\")"),
        ("+49 176 555 0100", "'+49 176 555 0100"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1:A9)", "'@SUM(A1:A9)"),
        ("\tcmd", "'\tcmd"),
        ("Amara Chowdhury", "Amara Chowdhury"),  # ordinary text is left alone
        ("a=b", "a=b"),  # only a LEADING trigger matters
        ("", ""),
        (None, ""),
    ],
)
def test_csv_safe_defuses_leading_formula_characters(typed, exported):
    assert csv_safe(typed) == exported


def test_bulk_enquiry_export_cannot_carry_a_formula():
    """Anyone can fill in the public catering form — its text must reach
    the baker's spreadsheet as text."""
    db = SessionLocal()
    try:
        db.add(
            BulkEnquiry(
                name='=HYPERLINK("http://evil.example","Click for quote")',
                email="planner@csvaudit.example.com",
                phone="+491765550100",
                event_date=date.today() + timedelta(days=20),
                event_type="wedding",
                guests=80,
                items_note="@SUM(1+1)",
                message="+1+1 and a second line\nhere",
            )
        )
        db.commit()
    finally:
        db.close()

    export = client.get("/api/admin/bulk-orders/export", headers=admin_headers())
    assert export.status_code == 200
    # a well-formed header — the stray quote used to ride into the filename
    assert export.headers["content-disposition"] == 'attachment; filename="bakehouse-bulk-enquiries.csv"'

    header, *rows = _rows(export)
    row = dict(zip(header, next(r for r in rows if "planner@csvaudit.example.com" in r)))
    assert row["name"] == "'=HYPERLINK(\"http://evil.example\",\"Click for quote\")"
    assert row["phone"] == "'+491765550100"
    assert row["wants"] == "'@SUM(1+1)"
    assert row["note"] == "'+1+1 and a second line here"
    # nothing customer-typed in this row opens with a formula character
    assert not any(cell[:1] in ("=", "+", "-", "@") for cell in row.values())


def test_order_export_defuses_a_customers_name():
    token = api.register_and_login("formula@csvaudit.example.com", "Formula123!")
    client.put(
        "/api/auth/me",
        json={"first_name": "=1+1", "last_name": "Tester"},
        headers=auth_header(token),
    )
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    placed = client.post(
        "/api/orders",
        json=checkout_payload(payment_method="cash_on_delivery"),
        headers=auth_header(token),
    )
    assert placed.status_code == 201

    header, *rows = _rows(client.get("/api/admin/orders/export", headers=admin_headers()))
    row = dict(zip(header, next(r for r in rows if r[0] == placed.json()["order_number"])))
    assert row["customer"] == "'=1+1 Tester"
    assert row["email"] == "formula@csvaudit.example.com"
    # amounts stay numbers — the defence is for text columns only
    assert float(row["total"]) == placed.json()["total"]


def test_newsletter_export_defuses_address_and_source():
    db = SessionLocal()
    try:
        db.add(Subscriber(email="+promo@csvaudit.example.com", source="=footer"))
        db.commit()
    finally:
        db.close()

    header, *rows = _rows(client.get("/api/admin/newsletter/export", headers=admin_headers()))
    row = dict(zip(header, next(r for r in rows if "promo@csvaudit.example.com" in r[0])))
    assert row["email"] == "'+promo@csvaudit.example.com"
    assert row["source"] == "'=footer"


# --------------------------------------------------------------------------
# Email: a name is text, a subject is one line, a production log is not a mailbox
# --------------------------------------------------------------------------

def test_reset_email_escapes_the_customers_name():
    """Registration does not prove an address is yours — so a name typed at
    signup must never arrive in someone's inbox as live markup."""
    hostile = '<a href="https://evil.example">Verify your account</a>'
    _subject, html, text = email_module.reset_password_email(
        hostile, "https://shop.example/reset-password?token=abc", 30
    )
    assert hostile not in html
    assert "&lt;a href=&quot;https://evil.example&quot;&gt;" in html
    assert hostile in text  # the plain-text part is plain text — nothing to escape


def test_receipt_email_escapes_names_and_labels():
    receipt = {
        "email": "someone@example.com",
        "first_name": "<img src=x onerror=alert(1)>",
        "order_number": "DJ-20261122-0001",
        "placed_at": "Nov 22, 2026, 09:00 AM",
        "items": [{"name": "Salt & <b>Pepper</b> Loaf", "quantity": 2, "subtotal": 9.0}],
        "subtotal": 9.0,
        "discount": 0.9,
        "coupon_code": "WELCOME10",
        "tax": 0.57,
        "shipping": 2.99,
        "total": 11.66,
        "payment_method": "cash_on_delivery",
        "payment_status": "pending",
        "currency": "eur",
        "tax_included": False,
    }
    _subject, html, _text = email_module.order_receipt_email(receipt)
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<b>Pepper</b>" not in html
    assert "Salt &amp; &lt;b&gt;Pepper&lt;/b&gt; Loaf × 2" in html
    # the layout's own markup is untouched
    assert "<table" in html and "€11.66" in html


class _FakeSMTP:
    """Just enough of smtplib.SMTP to catch what would have been sent."""

    sent: list = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        _FakeSMTP.sent.append(message)


def test_a_line_break_in_a_subject_no_longer_loses_the_email(monkeypatch):
    """A customer's name rides in desk-alert subjects. A line break in it
    used to make the email library refuse the header — outside the send's
    own error handling — so the alert was simply never sent."""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []

    email_module.send_admin_notification(
        "desk@example.com", "New customer — Eve\r\nBcc: attacker@evil.example", "eve@example.com", "/admin/users"
    )

    assert len(_FakeSMTP.sent) == 1
    subject = _FakeSMTP.sent[0]["Subject"]
    assert "\n" not in subject and "\r" not in subject
    assert subject.startswith("New customer — Eve Bcc: attacker@evil.example")
    assert _FakeSMTP.sent[0]["Bcc"] is None  # one header, never a smuggled second


def test_production_never_logs_an_email_body(monkeypatch, caplog):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "")
    secret_link = "https://shop.example/reset-password?token=THE-SECRET-TOKEN"

    monkeypatch.setattr(settings, "app_env", "production")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        email_module.send_email("someone@example.com", "Reset your password", "<p>html</p>", secret_link)
    logged = "\n".join(caplog.messages)
    assert "Reset your password" in logged  # the fact of it is still on record
    assert "THE-SECRET-TOKEN" not in logged

    # dev keeps the convenience it always had — the link is the whole point there
    caplog.clear()
    monkeypatch.setattr(settings, "app_env", "dev")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        email_module.send_email("someone@example.com", "Reset your password", "<p>html</p>", secret_link)
    assert "THE-SECRET-TOKEN" in "\n".join(caplog.messages)


# --------------------------------------------------------------------------
# Phone sign-in: no rail without a way to text, and a ceiling per number
# --------------------------------------------------------------------------

def test_phone_rail_is_shut_in_production_without_twilio(monkeypatch):
    """A code nobody can receive is only a six-digit door for guessing."""
    api.register_and_login("phone-shut@hardening.example.com", "PhoneShut1!")
    monkeypatch.setattr(get_settings(), "app_env", "production")

    asked = client.post("/api/auth/phone/request", json={"phone": "+491765550301"})
    assert asked.status_code == 404
    guessed = client.post(
        "/api/auth/phone/verify", json={"phone": "+491765550301", "code": "123456"}
    )
    assert guessed.status_code == 404
    # ...and the storefront was already not offering it
    assert client.get("/api/store").json()["phone_login"] is False


def test_a_number_gets_only_so_many_codes_an_hour(monkeypatch):
    phone = "+491765550302"
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "phone-ceiling@hardening.example.com",
            "password": "Ceiling123!",
            "first_name": "Cee",
            "phone": phone,
        },
    )
    assert registered.status_code == 201
    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)  # past the one-a-minute throttle

    codes = []
    for _ in range(auth_router.PHONE_CODES_PER_HOUR):
        answer = client.post("/api/auth/phone/request", json={"phone": phone}).json()
        codes.append(answer["dev_code"])

    over = client.post("/api/auth/phone/request", json={"phone": phone})
    assert over.status_code == 200  # the same calm answer as ever — nothing to probe
    assert "dev_code" not in over.json()
    # no new code was minted: the last one issued is still the live one
    signed_in = client.post("/api/auth/phone/verify", json={"phone": phone, "code": codes[-1]})
    assert signed_in.status_code == 200


# --------------------------------------------------------------------------
# Uploads: the file's own bytes decide what it is
# --------------------------------------------------------------------------

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
WEBP_HEAD = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def test_a_file_only_named_an_image_is_refused():
    token = api.register_and_login("uploader@hardening.example.com", "Uploader1!")
    headers = auth_header(token)

    page = b"<html><script>alert(document.cookie)</script></html>"
    refused = client.post(
        "/api/auth/me/avatar", headers=headers, files={"photo": ("me.png", page, "image/png")}
    )
    assert refused.status_code == 415
    assert client.get("/api/auth/me", headers=headers).json()["avatar_url"] is None

    empty = client.post(
        "/api/auth/me/avatar", headers=headers, files={"photo": ("me.png", b"", "image/png")}
    )
    assert empty.status_code == 415


@pytest.mark.parametrize(
    "payload, declared, extension",
    [
        (JPEG_HEAD, "image/png", ".jpg"),  # the bytes win over the label
        (WEBP_HEAD, "image/webp", ".webp"),
        (api.TINY_PNG, "image/jpeg", ".png"),
    ],
)
def test_the_saved_extension_follows_the_bytes(payload, declared, extension):
    token = api.register_and_login("uploader2@hardening.example.com", "Uploader2!")
    headers = auth_header(token)

    saved = client.post(
        "/api/auth/me/avatar", headers=headers, files={"photo": ("photo.bin", payload, declared)}
    )
    assert saved.status_code == 200
    assert saved.json()["avatar_url"].endswith(extension)
    assert api._uploaded_file(saved.json()["avatar_url"]).is_file()

    # leave no test artefact behind
    assert client.delete("/api/auth/me/avatar", headers=headers).status_code == 200


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------

def test_a_basket_has_a_line_ceiling():
    product = api.get_product("butter-croissant")
    mountain = [{"product_id": product["id"], "quantity": 1}] * 101

    quoted = client.post("/api/orders/quote", json={"items": mountain})
    assert quoted.status_code == 422

    placed = client.post(
        "/api/orders",
        json=checkout_payload(items=mountain, payment_method="cash_on_delivery"),
        headers=auth_header(api.register_and_login("mountain@hardening.example.com", "Mountain1!")),
    )
    assert placed.status_code == 422

    # a hundred lines is still a basket (they merge into one)
    fine = client.post("/api/orders/quote", json={"items": mountain[:100]})
    assert fine.status_code in (200, 400)  # priced, or honestly out of stock — never a validation error


def test_an_address_edit_cannot_blank_the_country():
    headers = auth_header(api.register_and_login("address@hardening.example.com", "Address123!"))
    created = client.post(
        "/api/auth/addresses",
        headers=headers,
        json={
            "label": "Home",
            "recipient": "Addy Ress",
            "line1": "Backstraße 1",
            "city": "Berlin",
            "state": "Berlin",
            "postal_code": "10115",
            "country": "Germany",
        },
    )
    assert created.status_code == 201
    address_id = created.json()["id"]

    blanked = client.put(f"/api/auth/addresses/{address_id}", headers=headers, json={"country": ""})
    assert blanked.status_code == 422
    moved = client.put(f"/api/auth/addresses/{address_id}", headers=headers, json={"country": "Austria"})
    assert moved.status_code == 200 and moved.json()["country"] == "Austria"


def test_request_ids_are_echoed_only_when_id_shaped():
    kept = client.get("/api/health", headers={"X-Request-ID": "trace-123.abc_DEF"})
    assert kept.headers["x-request-id"] == "trace-123.abc_DEF"

    for junk in ("x" * 200, "two words", "semi;colon"):
        replaced = client.get("/api/health", headers={"X-Request-ID": junk})
        minted = replaced.headers["x-request-id"]
        assert minted != junk
        assert len(minted) == 12 and all(c in "0123456789abcdef" for c in minted)


# --------------------------------------------------------------------------
# The rate limiter
# --------------------------------------------------------------------------

def test_the_limiter_forgets_addresses_that_went_quiet(monkeypatch):
    import app as app_package

    clock = {"now": 1000.0}
    monkeypatch.setattr(app_package, "monotonic", lambda: clock["now"])
    limiter = SlidingWindowLimiter(2, window=60.0)

    # the rule itself is unchanged: two a minute, the third waits
    assert limiter.allow("regular") and limiter.allow("regular")
    assert not limiter.allow("regular")

    # a caller inventing a new address for every request...
    for n in range(limiter.PRUNE_EVERY - 4):
        limiter.allow(f"invented-{n}")
    assert len(limiter.hits) > 1000

    # ...leaves nothing behind once its window has passed
    clock["now"] += 61
    assert limiter.allow("regular")  # this call is the sweep's turn
    assert set(limiter.hits) == {"regular"}


def test_client_key_prefers_the_edges_own_header_when_configured(monkeypatch):
    from starlette.datastructures import Headers

    def request(headers: dict):
        # Real request headers: names match whatever their case
        return SimpleNamespace(headers=Headers(headers), client=SimpleNamespace(host="10.0.0.9"))

    forged = {"x-forwarded-for": "1.2.3.4, 203.0.113.7", "cf-connecting-ip": "203.0.113.7"}

    # default: the long-standing behaviour, first forwarded hop
    monkeypatch.setattr(security.settings, "client_ip_header", "")
    assert security.client_key(request(forged)) == "1.2.3.4"

    # configured: the header the edge itself sets is the visitor
    monkeypatch.setattr(security.settings, "client_ip_header", "CF-Connecting-IP")
    assert security.client_key(request(forged)) == "203.0.113.7"
    # ...and where that header is absent nothing breaks — the old rule answers
    assert security.client_key(request({"x-forwarded-for": "198.51.100.2"})) == "198.51.100.2"
    assert security.client_key(request({})) == "10.0.0.9"


# --------------------------------------------------------------------------
# Personal vouchers keep the terms they were minted under
# --------------------------------------------------------------------------

def _seed_personal_voucher(code: str) -> tuple[int, int]:
    """A voucher restricted to one customer, written straight into the book
    (the mint endpoint's margin cap needs costing data this file has none
    of — and the guards under test live on the *edit* paths)."""
    from sqlalchemy import select

    from app.models import Coupon, User

    api.register_and_login("voucher-owner@hardening.example.com", "VoucherOwner1!")
    db = SessionLocal()
    try:
        owner = db.scalar(select(User).where(User.email == "voucher-owner@hardening.example.com"))
        coupon = Coupon(
            code=code,
            percent_off=10,
            description="A thank-you",
            is_active=True,
            once_per_customer=True,
            restricted_to_user_id=owner.id,
        )
        db.add(coupon)
        db.commit()
        return coupon.id, owner.id
    finally:
        db.close()


def test_a_personal_vouchers_terms_cannot_be_edited_past_the_marketing_cap():
    """The Marketing desk mints these under a margin brake; the general
    coupon edit used to let the percent be raised to anything afterwards."""
    coupon_id, _owner = _seed_personal_voucher("PERSONALGUARD1")
    admin = admin_headers()

    raised = client.put(f"/api/admin/coupons/{coupon_id}", json={"percent_off": 90}, headers=admin)
    assert raised.status_code == 400
    assert "personal voucher" in raised.json()["detail"]

    # pausing and resuming are still the desk's to do
    paused = client.put(f"/api/admin/coupons/{coupon_id}", json={"is_active": False}, headers=admin)
    assert paused.status_code == 200
    assert paused.json()["is_active"] is False and paused.json()["percent_off"] == 10


def test_a_personal_voucher_cannot_become_a_tiers_code():
    coupon_id, _owner = _seed_personal_voucher("PERSONALGUARD2")

    linked = client.put(
        "/api/admin/loyalty-vouchers/vip", json={"coupon_id": coupon_id}, headers=admin_headers()
    )
    assert linked.status_code == 400
    assert "shop-wide" in linked.json()["detail"]


# --------------------------------------------------------------------------
# The desk's payment label says what it is
# --------------------------------------------------------------------------

def test_the_desks_payment_label_is_never_mistaken_for_money_moving():
    """The label buttons only rewrite the book. Where a provider holds the
    order's money they are refused outright — "refunded" there has to be a
    refund the provider confirmed (the real refund has its own endpoint,
    pinned in test_payment_ledger.py). Where the label is all there is —
    a cash order — it works as it always did."""
    headers = auth_header(api.register_and_login("relabel@hardening.example.com", "Relabel123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    admin = admin_headers()

    card = client.post("/api/orders", json=checkout_payload(), headers=headers).json()
    api.pay_order_via_webhook(card["id"])
    refunds_before = len([c for c in api.gateway_calls if c[0] == "refund"])
    marked = client.put(
        f"/api/admin/orders/{card['id']}/payment", json={"payment_status": "refunded"}, headers=admin
    )
    assert marked.status_code == 400
    assert "Use Refund to send money back" in marked.json()["detail"]
    still = client.get(f"/api/orders/{card['id']}", headers=headers).json()
    assert still["payment_status"] == "paid"  # the label did not move
    assert len([c for c in api.gateway_calls if c[0] == "refund"]) == refunds_before

    # a cash order's label IS the whole truth — its wording is unchanged
    cash = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    settled = client.put(
        f"/api/admin/orders/{cash['id']}/payment", json={"payment_status": "paid"}, headers=admin
    ).json()
    assert settled["timeline"][-1]["comment"] == "Payment marked Paid"


# --------------------------------------------------------------------------
# The app shell: a policy for its scripts, and no shell for a missing asset
# --------------------------------------------------------------------------

def test_the_policy_admits_exactly_the_inline_scripts_the_build_carries(tmp_path):
    import base64
    import hashlib

    from app import _content_security_policy

    inline = b"\r\n      try { document.documentElement.dataset.x = '1'; } catch (e) {}\r\n    "
    page = tmp_path / "index.html"
    page.write_bytes(
        b"<!doctype html><html><head><script>" + inline + b"</script>"
        b'<script type="module" crossorigin src="/assets/index-abc.js"></script>'
        b"</head><body></body></html>"
    )
    policy = _content_security_policy(page)

    # hashed as the browser parses it — CRLF already folded to LF
    as_parsed = inline.replace(b"\r\n", b"\n")
    expected = base64.b64encode(hashlib.sha256(as_parsed).digest()).decode()
    assert f"script-src 'self' 'sha256-{expected}'" in policy
    assert policy.count("sha256-") == 1  # the src= script needs no hash
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'"):
        assert directive in policy


def test_the_shell_carries_the_policy_and_a_missing_asset_is_a_404():
    shell = client.get("/account/orders")
    if shell.status_code == 404:
        pytest.skip("no frontend build on this machine (frontend/dist) — nothing serves the shell")
    assert shell.headers["content-type"].startswith("text/html")
    assert "script-src 'self'" in shell.headers["content-security-policy"]
    assert shell.headers["cache-control"] == "no-cache"

    # A chunk name from before a deploy: the old answer was this same HTML
    # shell with a 200 — handed to a browser that asked for JavaScript.
    stale = client.get("/assets/Costing-0ldHash0.js")
    assert stale.status_code == 404
    assert stale.headers["content-type"].startswith("application/json")
    # ...while the API and the policy stay out of each other's way
    assert "content-security-policy" not in client.get("/api/health").headers


# --------------------------------------------------------------------------
# Editing a product must not put sold boxes back on the shelf
# --------------------------------------------------------------------------

def _product_form(product: dict, **changes) -> dict:
    """The full-replace body the desk's product form sends."""
    body = {
        "name": product["name"],
        "description": product.get("description"),
        "price": product["price"],
        "sale_price": product["sale_price"],
        "is_featured": product["is_featured"],
        "is_available": product["is_available"],
        "stock": product["stock"],
        "prep_time_minutes": product.get("prep_time_minutes"),
        "image_url": product["image_url"],
        "ingredients": product.get("ingredients"),
        "allergens": product.get("allergens"),
    }
    body.update(changes)
    return body


def test_a_product_edit_keeps_sales_made_while_the_form_was_open():
    """The form opens showing 20; two boxes sell; the baker fixes a typo
    and saves. The old save wrote 20 straight back — two boxes the shop no
    longer has. With the form's opening count aboard, only the baker's own
    change is applied."""
    admin = admin_headers()
    created = client.post(
        "/api/admin/products",
        headers=admin,
        json={"name": "Stock Race Loaf", "price": 4.0, "stock": 20},
    ).json()
    opened = client.get(f"/api/admin/products/{created['id']}", headers=admin).json()
    assert opened["stock"] == 20

    # ...while the form sits open, a customer buys two
    shopper = auth_header(api.register_and_login("stock-race@hardening.example.com", "StockRace1!"))
    bought = client.post(
        "/api/orders",
        headers=shopper,
        json=checkout_payload(
            payment_method="cash_on_delivery",
            items=[{"product_id": created["id"], "quantity": 2}],
        ),
    )
    assert bought.status_code == 201

    # the baker only reworded the description
    saved = client.put(
        f"/api/admin/products/{created['id']}",
        headers=admin,
        json=_product_form(opened, description="Now with a better description", stock_was=20),
    )
    assert saved.status_code == 200
    assert saved.json()["stock"] == 18  # the two sold boxes stay sold

    # a deliberate restock is applied as the change it is: 18 on the shelf,
    # the form (opened at 18) says 30 → twelve more
    restocked = client.put(
        f"/api/admin/products/{created['id']}",
        headers=admin,
        json=_product_form(saved.json() | opened | {"stock": 18}, stock=30, stock_was=18),
    )
    assert restocked.json()["stock"] == 30

    # taking more off than the shelf holds never goes below empty
    emptied = client.put(
        f"/api/admin/products/{created['id']}",
        headers=admin,
        json=_product_form(opened, stock=0, stock_was=50),
    )
    assert emptied.json()["stock"] == 0

    # without the opening count the field is the absolute number it always was
    absolute = client.put(
        f"/api/admin/products/{created['id']}", headers=admin, json=_product_form(opened, stock=7)
    )
    assert absolute.json()["stock"] == 7
