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
    # How each connection was secured: ("starttls" | "implicit", verified?)
    secured: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.port = port
        if context is not None:  # SMTP_SSL: TLS from the first byte
            _FakeSMTP.secured.append(("implicit", context.verify_mode.name == "CERT_REQUIRED"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        _FakeSMTP.secured.append(
            ("starttls", context is not None and context.verify_mode.name == "CERT_REQUIRED")
        )

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


# --------------------------------------------------------------------------
# Mail: what "sent" means, and who is never written to
# --------------------------------------------------------------------------

def test_the_mail_server_is_always_asked_for_a_certificate_on_either_port(monkeypatch):
    """Port 587 upgrades with STARTTLS, port 465 speaks TLS from the first
    byte — and on both the server's certificate is checked, or whoever sits
    in between could read the shop's mail password."""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", _FakeSMTP)
    _FakeSMTP.sent, _FakeSMTP.secured = [], []

    monkeypatch.setattr(settings, "smtp_port", 587)
    assert email_module.send_email("a@example.com", "Hello", "<p>hi</p>", "hi") is True
    monkeypatch.setattr(settings, "smtp_port", 465)
    assert email_module.send_email("a@example.com", "Hello", "<p>hi</p>", "hi") is True

    assert _FakeSMTP.secured == [("starttls", True), ("implicit", True)]
    assert len(_FakeSMTP.sent) == 2


def test_send_email_says_whether_the_message_left(monkeypatch, caplog):
    settings = get_settings()

    class _Refusing(_FakeSMTP):
        def send_message(self, message):
            raise OSError("550 mailbox unavailable")

    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _Refusing)
    with caplog.at_level(logging.ERROR, logger="bakery.email"):
        assert email_module.send_email("a@example.com", "Hello", "<p>hi</p>", "hi") is False
    assert "sending email to a@example.com failed" in caplog.text  # loud, but never raised

    # with no mail server at all (dev), the log is the delivery
    monkeypatch.setattr(settings, "smtp_host", "")
    assert email_module.send_email("a@example.com", "Hello", "<p>hi</p>", "hi") is True


def test_nothing_is_ever_mailed_to_a_closed_accounts_parked_address(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []
    parked = f"deleted-account-7@{email_module.DELETED_ACCOUNT_DOMAIN}"

    assert email_module.send_email(parked, "Your order moved", "<p>x</p>", "x") is False
    assert _FakeSMTP.sent == []

    # among several recipients, only the parked one is dropped
    assert email_module.send_email(f"desk@example.com, {parked}", "Desk ping", "<p>x</p>", "x") is True
    assert [m["To"] for m in _FakeSMTP.sent] == ["desk@example.com"]


def test_an_order_update_for_a_closed_account_goes_nowhere(caplog):
    """The customer closed their account while a box was on its way. The
    order is still delivered from its own address snapshot — but there is
    no mailbox left to write to."""
    token = api.register_and_login("closing-mid-order@hardening.example.com", "Closing123!")
    headers = auth_header(token)
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    order = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    ).json()
    assert client.post("/api/auth/me/delete", json={"password": "Closing123!"}, headers=headers).status_code == 200

    with caplog.at_level(logging.INFO, logger="bakery.email"):
        moved = client.put(
            f"/api/admin/orders/{order['id']}/status", json={"status": "confirmed"}, headers=admin_headers()
        )
    assert moved.status_code == 200
    assert "deleted-account-" not in caplog.text.replace("the account behind it was closed", "")
    assert "the account behind it was closed" in caplog.text


# --------------------------------------------------------------------------
# Accounts: the corners
# --------------------------------------------------------------------------

def test_the_shops_only_admin_cannot_close_their_own_account():
    """The Users desk already refuses to leave the shop with no admin. The
    admin's own profile page has a "close my account" button too."""
    headers = admin_headers()
    refused = client.post("/api/auth/me/delete", json={"password": "Admin123!"}, headers=headers)
    assert refused.status_code == 400
    assert "only admin" in refused.json()["detail"]
    # still there, still an admin, still signed in
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["role"], me["is_active"]) == ("admin", True)


def test_a_customer_with_a_single_name_can_still_save_their_profile():
    """Sign-up takes one "Your name" field, so a last name may be empty.
    Saving the profile used to demand one."""
    registered = client.post(
        "/api/auth/register",
        json={"email": "mononym@hardening.example.com", "password": "Mononym123!", "first_name": "Sukarno"},
    )
    assert registered.status_code == 201 and registered.json()["user"]["last_name"] == ""
    headers = auth_header(registered.json()["access_token"])

    saved = client.put(
        "/api/auth/me", json={"first_name": "Sukarno", "last_name": "", "phone": "+49 176 5550199"}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["last_name"], saved.json()["phone"]) == ("", "+491765550199")


def test_an_address_edit_with_a_required_line_sent_as_null_changes_nothing_and_never_500s():
    headers = auth_header(api.register_and_login("null-address@hardening.example.com", "Address123!"))
    created = client.post(
        "/api/auth/addresses",
        json={"recipient": "Null Tester", "line1": "Backstr. 1", "line2": "2. OG", "city": "Berlin",
              "state": "Berlin", "postal_code": "10115", "country": "Germany", "phone": "+49 30 1234"},
        headers=headers,
    ).json()

    edited = client.put(
        f"/api/auth/addresses/{created['id']}",
        json={"line1": None, "city": None, "country": None, "line2": None, "phone": None, "recipient": "Renamed"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["line1"], body["city"], body["country"]) == ("Backstr. 1", "Berlin", "Germany")  # kept
    assert (body["line2"], body["phone"], body["recipient"]) == (None, None, "Renamed")  # cleared / changed


# --------------------------------------------------------------------------
# Links that leave the server
# --------------------------------------------------------------------------

def test_a_reset_link_points_at_the_shops_own_address_not_at_the_request(monkeypatch, caplog):
    """A password-reset link built from the request's Host header can be
    aimed at someone else's site by whoever asks for the reset. With the
    shop's public address configured, the request has no say."""
    api.register_and_login("reset-link@hardening.example.com", "ResetLink123!")
    settings = get_settings()

    monkeypatch.setattr(settings, "public_base_url", "https://shop.example.test/")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        asked = client.post(
            "/api/auth/forgot-password",
            json={"email": "reset-link@hardening.example.com"},
            headers={"host": "evil.example"},
        )
    assert asked.status_code == 200
    assert asked.json()["reset_link"].startswith("https://shop.example.test/reset-password?token=")
    assert "evil.example" not in caplog.text

    # and the sitemap speaks the same address
    sitemap = client.get("/sitemap.xml", headers={"host": "evil.example"}).text
    assert "https://shop.example.test/products" in sitemap and "evil.example" not in sitemap


# --------------------------------------------------------------------------
# Search and catalog
# --------------------------------------------------------------------------

def test_a_percent_sign_in_a_search_is_a_character_not_a_wildcard():
    everything = client.get("/api/products", params={"per_page": 48}).json()["pagination"]["total_items"]
    assert everything > 5
    for odd in ("100%", "_", "%", "50%_"):
        found = client.get("/api/products", params={"search": odd, "per_page": 48}).json()
        assert found["pagination"]["total_items"] == 0, odd  # nothing is literally named that
    # an ordinary word still finds its bakes, in any letter case
    assert client.get("/api/products", params={"search": "CROISSANT"}).json()["pagination"]["total_items"] >= 1
    # the desk's order search follows the same rule
    orders = client.get("/api/admin/orders", params={"search": "%"}, headers=admin_headers()).json()
    assert orders["pagination"]["total_items"] == 0


def test_related_bakes_are_the_same_ones_on_every_visit():
    def related(slug):
        return [p["id"] for p in client.get(f"/api/products/{slug}").json()["related"]]

    # any bake that shares its shelf with others (the suite reshuffles shelves)
    catalog = client.get("/api/products", params={"per_page": 48}).json()["products"]
    slug = next(p["slug"] for p in catalog if len(related(p["slug"])) >= 2)
    first = related(slug)
    for _ in range(3):
        assert related(slug) == first


# --------------------------------------------------------------------------
# Brakes on what one account can do
# --------------------------------------------------------------------------

def test_an_account_cannot_place_orders_or_post_reviews_without_end(monkeypatch):
    """Every order reserves stock and rings the desk; every review emails
    it. A script with one login must run out of road."""
    limiters = client.app.state.limiters
    monkeypatch.setitem(limiters, "orders", SlidingWindowLimiter(2, window=3600.0))
    monkeypatch.setitem(limiters, "reviews", SlidingWindowLimiter(2, window=3600.0))
    headers = auth_header(api.register_and_login("flood@hardening.example.com", "Flood123!"))
    other = auth_header(api.register_and_login("patient@hardening.example.com", "Patient123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    stock = api.get_product("butter-croissant")["stock"]
    cash = checkout_payload(payment_method="cash_on_delivery")

    assert [client.post("/api/orders", json=cash, headers=headers).status_code for _ in range(3)] == [201, 201, 429]
    assert api.get_product("butter-croissant")["stock"] == stock - 4  # the third reserved nothing
    # the brake is the account's own — the next customer is not held up by it
    assert client.post("/api/orders", json=cash, headers=other).status_code == 201

    croissant = api.get_product("butter-croissant")["id"]
    review = {"rating": 5, "comment": "again"}
    codes = [
        client.post(f"/api/products/{croissant}/reviews", json=review, headers=headers).status_code
        for _ in range(3)
    ]
    assert codes == [201, 200, 429]


def test_the_same_press_replayed_does_not_use_up_the_order_budget(monkeypatch):
    monkeypatch.setitem(client.app.state.limiters, "orders", SlidingWindowLimiter(1, window=3600.0))
    headers = auth_header(api.register_and_login("replay-budget@hardening.example.com", "Replay123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    press = {**headers, "Idempotency-Key": "press-budget-0001"}
    cash = checkout_payload(payment_method="cash_on_delivery")

    assert client.post("/api/orders", json=cash, headers=press).status_code == 201
    # the same press arriving again is the same order — not a second one, and not a refusal
    for _ in range(3):
        assert client.post("/api/orders", json=cash, headers=press).status_code == 200


# --------------------------------------------------------------------------
# Settings that arrive blank
# --------------------------------------------------------------------------

def test_a_blank_number_or_switch_in_the_environment_is_the_default_not_a_crash():
    """A dashboard field left empty arrives as "". That once stopped the
    whole shop from booting (SMTP_PORT)."""
    from app.config import Settings

    blank = Settings(
        smtp_port="", tax_rate="", shipping_fee=" ", login_rate_limit="", enable_docs="",
        seed_demo_data="", order_rate_limit="",
    )
    assert (blank.smtp_port, blank.tax_rate, blank.shipping_fee) == (587, None, None)
    assert (blank.login_rate_limit, blank.enable_docs, blank.seed_demo_data) == (10, True, None)
    assert blank.order_rate_limit == 12
    # text settings keep their empty string — that is how mail is switched off
    assert Settings(smtp_host="").smtp_host == ""
    # and a real value is still read as one
    assert Settings(smtp_port="2525", tax_rate="0.19").smtp_port == 2525


# --------------------------------------------------------------------------
# The shop's own clock
# --------------------------------------------------------------------------

def test_the_shops_clock_is_the_shops_not_the_servers(monkeypatch):
    from datetime import datetime

    from app import shoptime

    settings = get_settings()
    monkeypatch.setattr(settings, "shop_timezone", "Europe/Berlin")
    # winter time is UTC+1, summer time UTC+2 — the receipt says which
    assert shoptime.stamp(datetime(2026, 11, 22, 9, 14)) == "Nov 22, 2026, 10:14 AM CET"
    assert shoptime.stamp(datetime(2026, 7, 1, 9, 14)) == "Jul 01, 2026, 11:14 AM CEST"
    # half past midnight in Berlin already belongs to the new day
    assert shoptime.in_shop_time(datetime(2026, 11, 22, 23, 30)).date() == date(2026, 11, 23)

    # a zone this system has never heard of is UTC, said loudly — never a crash
    monkeypatch.setattr(settings, "shop_timezone", "Mars/Olympus_Mons")
    assert shoptime.stamp(datetime(2026, 11, 22, 9, 14)) == "Nov 22, 2026, 09:14 AM UTC"

    # "local" follows the server's own clock, as the shop always did
    monkeypatch.setattr(settings, "shop_timezone", "local")
    assert shoptime.shop_today() == date.today()


def test_a_receipt_states_its_time_in_the_shops_zone(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "shop_timezone", "Europe/Berlin")
    headers = auth_header(api.register_and_login("clock@hardening.example.com", "Clock123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    with caplog.at_level(logging.INFO, logger="bakery.email"):
        placed = client.post(
            "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
        )
    assert placed.status_code == 201
    receipt = caplog.text
    assert f"Receipt {placed.json()['order_number']}" in receipt
    assert " CET" in receipt or " CEST" in receipt  # a time with its zone on it


def test_tomorrow_in_the_shop_is_judged_by_the_shops_date(monkeypatch):
    """A delivery date is refused or accepted by the shop's calendar. Pin
    the shop's "today" a day ahead of this machine's and the same request
    flips from fine to refused."""
    from app import shoptime

    headers = auth_header(api.register_and_login("midnight@hardening.example.com", "Midnight123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    today = date.today()
    payload = checkout_payload(payment_method="cash_on_delivery", delivery_date=today.isoformat())

    assert client.post("/api/orders", json=payload, headers=headers).status_code == 201
    monkeypatch.setattr(shoptime, "shop_today", lambda: today + timedelta(days=1))
    late = client.post("/api/orders", json=payload, headers=headers)
    assert late.status_code == 400 and "past" in late.json()["detail"]


# --------------------------------------------------------------------------
# The legal notice (Impressum)
# --------------------------------------------------------------------------

def _settings(**fields):
    response = client.put("/api/admin/settings", json=fields, headers=admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_the_legal_notice_is_published_only_once_the_baker_writes_one():
    notice = "Djini's Bakehouse\nInhaberin: D. Example\nBackstr. 1, 10115 Berlin\nUSt-IdNr.: DE000000000"
    try:
        assert client.get("/api/store").json()["legal_notice_published"] is False
        assert client.get("/api/legal-notice").json()["text"] is None
        assert "/imprint" not in client.get("/sitemap.xml").text

        saved = _settings(legal_notice=f"  {notice}  ")
        assert saved["legal_notice"] == notice  # as written, edges trimmed

        assert client.get("/api/store").json()["legal_notice_published"] is True
        published = client.get("/api/legal-notice").json()
        assert published["text"] == notice and published["updated_at"]
        assert "/imprint" in client.get("/sitemap.xml").text

        # other settings saved without it leave it exactly where it is
        _settings(today_note="Croffles out at 8, doughnuts at 9")
        assert client.get("/api/legal-notice").json()["text"] == notice
    finally:
        _settings(legal_notice="")
    assert client.get("/api/store").json()["legal_notice_published"] is False

    # the desk's alone to write
    customer = auth_header(api.register_and_login("notice@hardening.example.com", "Notice123!"))
    assert client.put("/api/admin/settings", json={"legal_notice": "x"}, headers=customer).status_code == 403


# --------------------------------------------------------------------------
# The delivery area
# --------------------------------------------------------------------------

def test_postal_codes_and_areas_match_the_way_people_write_them():
    from app import delivery

    area = delivery.parse_area("10115, 101*;12 043\n\n13353 , 10115")
    assert area == ["10115", "101*", "12043", "13353"]  # normalised, de-duplicated, in order
    assert delivery.invalid_entries(area) == []
    for inside in ("10115", " 10119 ", "12043", "12 043", "13353"):
        assert delivery.delivers_to(inside, area), inside
    for outside in ("80331", "1001", "", None, "12044", "20101"):
        assert not delivery.delivers_to(outside, area), outside
    assert delivery.delivers_to("80331", [])  # no list, no limit
    # spaces are read as they are meant: several all-digit codes are a list,
    # a code with letters keeps its own space
    assert delivery.parse_area("10115 10117 102*") == ["10115", "10117", "102*"]
    assert delivery.parse_area("SW1A 1AA; 1011 AB") == ["SW1A1AA", "1011AB"]
    assert delivery.invalid_entries(delivery.parse_area("10115, 10*5, *, Mitte!")) == ["10*5", "*", "MITTE!"]
    assert delivery.delivers_to("sw1a 1aa", delivery.parse_area("SW1A*"))


def test_an_address_outside_the_delivery_area_is_refused_before_anything_moves():
    headers = auth_header(api.register_and_login("far-away@hardening.example.com", "FarAway123!"))
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    stock = api.get_product("butter-croissant")["stock"]

    def order_to(postal_code):
        address = {"recipient": "Far Away", "line1": "Hauptstr. 1", "city": "Somewhere", "state": "DE",
                   "postal_code": postal_code, "country": "Germany"}
        return client.post(
            "/api/orders",
            json=checkout_payload(payment_method="cash_on_delivery", address_id=None, new_address=address),
            headers=headers,
        )

    try:
        assert _settings(delivery_postal_codes="10115, 101*\n12 043")["delivery_postal_codes"] == "10115, 101*, 12043"
        assert client.get("/api/store").json()["delivery_postal_codes"] == ["10115", "101*", "12043"]

        refused = order_to("80331")
        assert refused.status_code == 400
        assert "don't deliver to 80331" in refused.json()["detail"]
        assert api.get_product("butter-croissant")["stock"] == stock  # nothing reserved
        # and the address was not quietly filed in their book either
        assert client.get("/api/auth/addresses", headers=headers).json() == []

        inside = order_to("10119")
        assert inside.status_code == 201
        # an address already in the book is judged the same way
        book = client.post(
            "/api/auth/addresses",
            json={"recipient": "Far Away", "line1": "Fernweg 9", "city": "München", "state": "BY",
                  "postal_code": "80331", "country": "Germany"},
            headers=headers,
        ).json()
        from_book = client.post(
            "/api/orders",
            json=checkout_payload(payment_method="cash_on_delivery", address_id=book["id"], new_address=None),
            headers=headers,
        )
        assert from_book.status_code == 400

        # a list the desk mistyped is named back, not stored
        bad = client.put(
            "/api/admin/settings", json={"delivery_postal_codes": "10115, Mitte!"}, headers=admin_headers()
        )
        assert bad.status_code == 400 and "MITTE!" in bad.json()["detail"]
        assert client.get("/api/store").json()["delivery_postal_codes"] == ["10115", "101*", "12043"]
    finally:
        _settings(delivery_postal_codes="")

    assert client.get("/api/store").json()["delivery_postal_codes"] == []
    assert order_to("80331").status_code == 201  # no list, no limit — as it always was


# --------------------------------------------------------------------------
# The newsletter sign-up list at the desk
# --------------------------------------------------------------------------

def test_the_desk_can_count_and_prune_the_newsletter_list():
    admin = admin_headers()
    before = client.get("/api/admin/marketing/overview", headers=admin).json()["newsletter_subscribers"]
    assert client.post(
        "/api/newsletter", json={"email": "leaving@hardening.example.com", "source": "footer"}
    ).status_code == 201
    assert client.get("/api/admin/marketing/overview", headers=admin).json()["newsletter_subscribers"] == before + 1

    listed = client.get("/api/admin/newsletter", headers=admin).json()
    row = next(s for s in listed if s["email"] == "leaving@hardening.example.com")

    customer = auth_header(api.register_and_login("nosy@hardening.example.com", "Nosy123!"))
    assert client.delete(f"/api/admin/newsletter/{row['id']}", headers=customer).status_code == 403
    assert client.delete(f"/api/admin/newsletter/{row['id']}").status_code == 401

    assert client.delete(f"/api/admin/newsletter/{row['id']}", headers=admin).status_code == 200
    assert client.delete(f"/api/admin/newsletter/{row['id']}", headers=admin).status_code == 404
    assert client.get("/api/admin/marketing/overview", headers=admin).json()["newsletter_subscribers"] == before
    # gone means gone: signing up again is a fresh sign-up, not "already subscribed"
    again = client.post("/api/newsletter", json={"email": "leaving@hardening.example.com", "source": "footer"})
    assert again.status_code == 201 and again.json()["already_subscribed"] is False


# --------------------------------------------------------------------------
# A basket line the shelf can't honour: which one, and what is left
# --------------------------------------------------------------------------

def _shelve(name: str, **fields) -> dict:
    created = client.post(
        "/api/admin/products", headers=admin_headers(), json={"name": name, "price": 3.5, **fields}
    )
    assert created.status_code == 201
    return created.json()


def test_a_refused_basket_line_names_itself_and_what_is_left():
    """The basket page offers the fix — remove the line, or take what is
    left. For that it has to be told which line was refused and how many
    remain, not left to read it out of a sentence."""
    short = _shelve("Three Left Loaf", stock=3)
    empty = _shelve("Gone By Noon Bun", stock=0)
    hidden = _shelve("Resting Recipe Roll", stock=9, is_available=False)

    def quote(product, quantity):
        return client.post(
            "/api/orders/quote", json={"items": [{"product_id": product["id"], "quantity": quantity}]}
        )

    try:
        tight = quote(short, 5)
        assert tight.status_code == 400
        assert tight.json()["detail"] == "Only 3 left of Three Left Loaf"
        assert tight.headers["x-basket-product"] == str(short["id"])
        assert tight.headers["x-basket-available"] == "3"

        sold_out = quote(empty, 1)
        assert sold_out.status_code == 400
        assert sold_out.json()["detail"] == "Gone By Noon Bun is sold out"  # not "Only 0 left of …"
        assert sold_out.headers["x-basket-product"] == str(empty["id"])
        assert sold_out.headers["x-basket-available"] == "0"

        off = quote(hidden, 1)
        assert off.status_code == 400
        # by its name — "Product 41 is not available" told a shopper nothing
        assert off.json()["detail"] == "Resting Recipe Roll is not available right now"
        assert off.headers["x-basket-product"] == str(hidden["id"])
        assert off.headers["x-basket-available"] == "0"

        # a basket that prices carries neither header
        fine = quote(short, 3)
        assert fine.status_code == 200
        assert "x-basket-product" not in fine.headers
    finally:
        for product in (short, empty, hidden):
            client.delete(f"/api/admin/products/{product['id']}", headers=admin_headers())


def test_losing_the_race_for_the_last_ones_is_told_what_is_left_now(monkeypatch):
    """Two shoppers reach for the same shelf. The one who loses used to be
    told the figure read *before* the other took theirs — "Only 4 left"
    to someone who had asked for three."""
    from sqlalchemy import text

    from app.routers import orders as orders_router

    headers = auth_header(api.register_and_login("second-in-line@hardening.example.com", "Second123!"))
    loaf = _shelve("Last Four Loaf", stock=4)
    check = orders_router._resolve_basket

    def someone_else_gets_there_first(db, items):
        resolved = check(db, items)  # passes: four on the shelf, three wanted
        # ...and before the shelf is actually decremented, three of them go.
        # (Plain SQL, so the products already loaded keep their stale count.)
        db.execute(text("UPDATE products SET stock = 1 WHERE id = :id"), {"id": loaf["id"]})
        return resolved

    monkeypatch.setattr(orders_router, "_resolve_basket", someone_else_gets_there_first)
    try:
        lost = client.post(
            "/api/orders",
            json=checkout_payload(
                payment_method="cash_on_delivery", items=[{"product_id": loaf["id"], "quantity": 3}]
            ),
            headers=headers,
        )
        assert lost.status_code == 400
        assert lost.json()["detail"] == "Only 1 left of Last Four Loaf"
        assert lost.headers["x-basket-product"] == str(loaf["id"])
        assert lost.headers["x-basket-available"] == "1"
        monkeypatch.undo()
        # The refusal rolled everything back, the stand-in "other shopper" included.
        assert api.get_product(loaf["slug"])["stock"] == 4
        assert client.get("/api/orders", headers=headers).json()["orders"] == []
    finally:
        monkeypatch.undo()
        client.delete(f"/api/admin/products/{loaf['id']}", headers=admin_headers())


# --------------------------------------------------------------------------
# A required line of only spaces is an empty line
# --------------------------------------------------------------------------

def test_whitespace_is_not_a_name_an_address_or_a_bake():
    """`min_length=1` used to be satisfied by a space, which the handlers
    then trimmed away: a customer called "", an address in the city of " ",
    a bake with no name filed under the slug "item"."""
    nameless = client.post(
        "/api/auth/register",
        json={"email": "spaces@hardening.example.com", "password": "Spaces123!", "first_name": "   "},
    )
    assert nameless.status_code == 422

    made = client.post(
        "/api/auth/register",
        json={
            "email": "padded@hardening.example.com",
            "password": "Padded123!",
            "first_name": "  Amara ",
            "last_name": " Okafor  ",
        },
    )
    assert made.status_code == 201
    headers = auth_header(made.json()["access_token"])
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["first_name"], me["last_name"]) == ("Amara", "Okafor")

    assert client.put("/api/auth/me", json={"first_name": "  "}, headers=headers).status_code == 422
    assert client.get("/api/auth/me", headers=headers).json()["first_name"] == "Amara"

    address = {
        "label": "  ",
        "recipient": " Amara Okafor ",
        "line1": " Backweg 3 ",
        "city": " Berlin ",
        "state": "BE",
        "postal_code": " 10115 ",
        "country": "Germany",
    }
    saved = client.post("/api/auth/addresses", json=address, headers=headers)
    assert saved.status_code == 201
    book = saved.json()
    assert book["label"] == "Home"  # a blank label is "Home", never a row with no name
    assert (book["recipient"], book["line1"], book["city"], book["postal_code"]) == (
        "Amara Okafor", "Backweg 3", "Berlin", "10115",
    )
    for field in ("recipient", "line1", "city", "state", "postal_code", "country"):
        blanked = {**address, field: "   "}
        assert client.post("/api/auth/addresses", json=blanked, headers=headers).status_code == 422, field
        edit = client.put(f"/api/auth/addresses/{book['id']}", json={field: "  "}, headers=headers)
        assert edit.status_code == 422, field
    relabelled = client.put(f"/api/auth/addresses/{book['id']}", json={"label": " "}, headers=headers)
    assert relabelled.status_code == 200 and relabelled.json()["label"] == "Home"
    # nothing above changed the address that was saved
    kept = client.get("/api/auth/addresses", headers=headers).json()
    assert [(a["recipient"], a["city"]) for a in kept] == [("Amara Okafor", "Berlin")]

    # the same at checkout, where an address is typed for the first time
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    nowhere = client.post(
        "/api/orders",
        json=checkout_payload(
            payment_method="cash_on_delivery", new_address={**address, "label": "Home", "city": "  "}
        ),
        headers=headers,
    )
    assert nowhere.status_code == 422

    # and at the desk: a bake or a shelf nobody could name
    admin = admin_headers()
    assert client.post("/api/admin/products", headers=admin, json={"name": "   ", "price": 3.5}).status_code == 422
    assert client.post("/api/admin/categories", headers=admin, json={"name": "  "}).status_code == 422
    # a catering lead from nobody
    lead = client.post(
        "/api/bulk-orders",
        json={
            "name": "  ",
            "email": "nobody@hardening.example.com",
            "phone": "+491765550100",
            "event_date": str(date.today() + timedelta(days=30)),
            "event_type": "birthday",
            "guests": 20,
        },
    )
    assert lead.status_code == 422


# --------------------------------------------------------------------------
# The same sign-up, twice at once
# --------------------------------------------------------------------------

def test_a_sign_up_that_loses_the_race_for_its_email_is_a_409_not_a_500(monkeypatch):
    """Two requests for one address can both pass the "is it taken?" check;
    the unique rule on the column stops the second at its insert. That
    surfaced as a 500 — a double tap on "Create account" looked like the
    shop had broken."""
    from sqlalchemy import func, select

    from app.models import User

    email = "double-tap@hardening.example.com"
    hash_password = auth_router.hash_password

    def the_other_request_lands_first(password):
        # Runs after this request's own check and before its insert.
        db = SessionLocal()
        try:
            db.add(User(email=email, password_hash=hash_password(password), first_name="Double", last_name="Tap"))
            db.commit()
        finally:
            db.close()
        return hash_password(password)

    monkeypatch.setattr(auth_router, "hash_password", the_other_request_lands_first)
    second = client.post(
        "/api/auth/register",
        json={"email": email, "password": "DoubleTap123!", "first_name": "Double", "last_name": "Tap"},
    )
    monkeypatch.undo()
    assert second.status_code == 409
    assert second.json()["detail"] == "An account with this email already exists"

    db = SessionLocal()
    try:
        assert db.scalar(select(func.count(User.id)).where(User.email == email)) == 1
    finally:
        db.close()
    # ...and the account that won is a working one
    assert client.post("/api/auth/login", json={"email": email, "password": "DoubleTap123!"}).status_code == 200
