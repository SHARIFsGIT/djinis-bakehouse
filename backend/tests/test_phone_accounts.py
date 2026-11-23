"""A phone number as a way in: opening an account with one, logging in with
one, and everything an account without an email must still be able to do —
order, be found at the desk, get its password back.

The promises pinned here:

* one number is one account, however either side spells it;
* an account opened with a phone number alone has no email — not to the
  customer, not to the desk, not to Stripe, and nothing is ever mailed to
  the address parked in its column;
* a phone number is never the *last* way in to be taken away;
* a login that fails says the same thing whether or not the account exists;
* the desk can hand over a reset link, for customers only.

Rides on test_api.py's client (imported, so the app boots once whichever
file pytest reaches first).
"""

from types import SimpleNamespace

import pytest

import bird_fake
from test_api import admin_headers, auth_header, checkout_payload, client

from app import email as email_module, phones
from app.database import SessionLocal
from app.models import User
from app.schemas import UserOut

PASSWORD = "Sourdough-42"


def open_with_phone(phone: str, name: str = "Phone Regular", **extra) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"phone": phone, "password": PASSWORD, "first_name": name, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def log_in_with_phone(phone: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"phone": phone, "password": password})


# --------------------------------------------------------------------------
# The parking domains are the shop's own
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "address",
    ["phone-0123456789abcdef@phone.djinisbakehouse.com", "Deleted-Account-7@Deleted.DjinisBakehouse.com"],
)
def test_nobody_signs_up_with_a_parked_address(address):
    """An address on a parking domain would be an account whose email the
    shop hides or treats as closed — so only the shop ever writes one."""
    response = client.post(
        "/api/auth/register",
        json={"email": address, "password": PASSWORD, "first_name": "Parker"},
    )
    assert response.status_code == 400, response.text
    assert "can't be used" in response.json()["detail"]


def test_a_parked_address_cannot_be_added_to_a_phone_account():
    body = open_with_phone("0176 5550 290")
    response = client.put(
        "/api/auth/me",
        json={"email": "phone-feedfacefeedface@phone.djinisbakehouse.com"},
        headers=auth_header(body["access_token"]),
    )
    assert response.status_code == 400, response.text
    assert "can't be used" in response.json()["detail"]
    me = client.get("/api/auth/me", headers=auth_header(body["access_token"])).json()
    assert me["email"] is None


# --------------------------------------------------------------------------
# One number, however it is written
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "typed",
    ["+49 176 555 0100", "0176 5550100", "0049 176 555-0100", "+49 (176) 555 0100"],
)
def test_the_spellings_of_one_number_are_stored_as_one(typed):
    assert phones.canonical(typed, "49") == "+491765550100"
    assert "+491765550100" in phones.spellings(typed, "49")
    assert "01765550100" in phones.spellings(typed, "49")


def test_a_number_from_abroad_keeps_its_own_country():
    assert phones.canonical("+880 1712-345678", "49") == "+8801712345678"
    assert "01712345678" not in phones.spellings("+880 1712-345678", "49")


def test_bare_digits_are_kept_as_typed_but_still_found():
    # No "+", no leading 0: it is not guessed at when stored…
    assert phones.canonical("1765550100", "49") == "1765550100"
    # …but an account that was stored the long way is still found by it.
    assert "+491765550100" in phones.spellings("1765550100", "49")


# --------------------------------------------------------------------------
# Opening an account with a phone number
# --------------------------------------------------------------------------

def test_an_account_can_be_opened_with_a_phone_number_alone():
    body = open_with_phone("0176 5550 201")
    assert body["user"]["email"] is None
    assert body["user"]["phone"] == "+491765550201"
    me = client.get("/api/auth/me", headers=auth_header(body["access_token"]))
    assert me.status_code == 200 and me.json()["email"] is None


def test_sign_up_needs_an_email_or_a_phone_number():
    response = client.post(
        "/api/auth/register", json={"password": PASSWORD, "first_name": "Nobody"}
    )
    assert response.status_code == 422
    assert "email address or a phone number" in response.text


def test_a_sign_in_number_must_say_its_country():
    response = client.post(
        "/api/auth/register",
        json={"phone": "1765550202", "password": PASSWORD, "first_name": "Vague"},
    )
    assert response.status_code == 422
    assert "start the phone number with 0" in response.text


def test_one_number_is_one_account_however_it_is_spelled():
    open_with_phone("0176 5550 203")
    for again in ("+49 176 5550203", "0049 1765550203", "01765550203"):
        response = client.post(
            "/api/auth/register",
            json={"phone": again, "password": PASSWORD, "first_name": "Twin"},
        )
        assert response.status_code == 409, again
        assert "phone number already exists" in response.json()["detail"]
    # …and an email sign-up cannot carry the number in through the side door.
    response = client.post(
        "/api/auth/register",
        json={
            "email": "twin-203@example.com",
            "phone": "+491765550203",
            "password": PASSWORD,
            "first_name": "Twin",
        },
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Logging in with a phone number
# --------------------------------------------------------------------------

def test_log_in_with_phone_and_password_in_any_spelling():
    open_with_phone("+49 176 5550 204", name="Ayla")
    for typed in ("0176 5550204", "+491765550204", "0049 176 5550 204"):
        response = log_in_with_phone(typed)
        assert response.status_code == 200, typed
        assert response.json()["user"]["first_name"] == "Ayla"


def test_a_failed_phone_login_says_the_same_thing_either_way():
    open_with_phone("0176 5550 205")
    wrong_password = log_in_with_phone("0176 5550 205", "not-the-password")
    nobody = log_in_with_phone("0176 5550 999", PASSWORD)
    assert wrong_password.status_code == nobody.status_code == 401
    assert wrong_password.json() == nobody.json() == {"detail": "Incorrect phone number or password"}


def test_login_takes_one_identifier_not_two_and_not_none():
    both = client.post(
        "/api/auth/login",
        json={"email": "customer@example.com", "phone": "0176 5550 204", "password": PASSWORD},
    )
    neither = client.post("/api/auth/login", json={"password": PASSWORD})
    assert both.status_code == neither.status_code == 422


def test_an_email_account_logs_in_with_its_phone_number_too():
    response = client.post(
        "/api/auth/register",
        json={
            "email": "both-ways-206@example.com",
            "phone": "0176 5550 206",
            "password": PASSWORD,
            "first_name": "Bothways",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["email"] == "both-ways-206@example.com"
    assert response.json()["user"]["phone"] == "+491765550206"
    assert log_in_with_phone("0176 5550 206").status_code == 200
    by_email = client.post(
        "/api/auth/login", json={"email": "both-ways-206@example.com", "password": PASSWORD}
    )
    assert by_email.status_code == 200


def test_a_deactivated_account_is_told_so_on_the_phone_rail_too():
    body = open_with_phone("0176 5550 207")
    update = client.put(
        f"/api/admin/users/{body['user']['id']}", json={"is_active": False}, headers=admin_headers()
    )
    assert update.status_code == 200
    assert log_in_with_phone("0176 5550 207").status_code == 403


# --------------------------------------------------------------------------
# The profile of an account with no email
# --------------------------------------------------------------------------

def test_the_only_way_in_cannot_be_removed_but_an_email_can_be_added():
    body = open_with_phone("0176 5550 208")
    headers = auth_header(body["access_token"])

    stuck = client.put("/api/auth/me", json={"phone": ""}, headers=headers)
    assert stuck.status_code == 400
    assert "Add an email address first" in stuck.json()["detail"]

    added = client.put("/api/auth/me", json={"email": "Late.Bloomer-208@Example.com"}, headers=headers)
    assert added.status_code == 200
    assert added.json()["email"] == "late.bloomer-208@example.com"

    # Now there are two ways in: the number may go…
    assert client.put("/api/auth/me", json={"phone": ""}, headers=headers).status_code == 200
    # …and the address, once on file, is not changed from here.
    changed = client.put("/api/auth/me", json={"email": "someone-else-208@example.com"}, headers=headers)
    assert changed.status_code == 400
    # Sending the same address back (a form that carries it) is not a change.
    same = client.put(
        "/api/auth/me", json={"email": "late.bloomer-208@example.com", "first_name": "Late"}, headers=headers
    )
    assert same.status_code == 200 and same.json()["first_name"] == "Late"


def test_an_address_someone_else_has_cannot_be_added():
    body = open_with_phone("0176 5550 209")
    response = client.put(
        "/api/auth/me", json={"email": "customer@example.com"}, headers=auth_header(body["access_token"])
    )
    assert response.status_code == 400
    assert "Another account already uses this email" in response.json()["detail"]


def test_a_profile_cannot_take_a_number_another_account_signs_in_with():
    open_with_phone("0176 5550 210")
    other = open_with_phone("0176 5550 211")
    response = client.put(
        "/api/auth/me", json={"phone": "+49 176 5550210"}, headers=auth_header(other["access_token"])
    )
    assert response.status_code == 400
    assert "Another account already uses this phone number" in response.json()["detail"]


def test_the_data_export_shows_no_parked_address():
    body = open_with_phone("0176 5550 212")
    export = client.get("/api/auth/me/export", headers=auth_header(body["access_token"]))
    assert export.status_code == 200
    assert export.json()["account"]["email"] is None
    assert email_module.PHONE_ACCOUNT_DOMAIN not in export.text


# --------------------------------------------------------------------------
# Nothing is mailed, shown or handed on
# --------------------------------------------------------------------------

def test_nothing_is_ever_sent_to_a_parked_address():
    parked = f"phone-0123456789abcdef@{email_module.PHONE_ACCOUNT_DOMAIN}"
    assert email_module.send_email(parked, "Your order", "<p>hello</p>", "hello") is False
    assert email_module.shown_email(parked) is None
    assert email_module.shown_email("real@example.com") == "real@example.com"
    # A closed account's parked address is still shown at the desk as before.
    closed = f"deleted-account-7@{email_module.DELETED_ACCOUNT_DOMAIN}"
    assert email_module.shown_email(closed) == closed
    assert email_module.send_email(closed, "x", "<p>x</p>", "x") is False


def test_what_is_serialized_never_carries_the_parked_address():
    shell = SimpleNamespace(
        id=1,
        email=f"phone-0123456789abcdef@{email_module.PHONE_ACCOUNT_DOMAIN}",
        first_name="A",
        last_name="",
        phone="+491765550100",
        avatar_url=None,
        role="customer",
        is_active=True,
        created_at="2026-11-23T08:00:00",
    )
    assert UserOut.model_validate(shell).email is None


def test_a_phone_account_orders_and_the_desk_sees_it_without_an_email():
    body = open_with_phone("0176 5550 213", name="Counter Regular")
    headers = auth_header(body["access_token"])
    placed = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=headers
    )
    assert placed.status_code == 201, placed.text
    order = placed.json()

    desk = admin_headers()
    detail = client.get(f"/api/orders/{order['id']}", headers=desk)  # the drawer's own read
    assert detail.status_code == 200
    assert detail.json()["customer_email"] is None
    assert detail.json()["customer_name"] == "Counter Regular"

    # Found at the desk by the number, in the order rail and on the roll.
    rail = client.get("/api/admin/orders", params={"search": "+491765550213"}, headers=desk)
    assert order["order_number"] in [o["order_number"] for o in rail.json()["orders"]]
    roll = client.get("/api/admin/users", params={"search": "5550213"}, headers=desk)
    assert roll.status_code == 200
    rows = roll.json()["items"]
    assert len(rows) == 1 and rows[0]["email"] is None and rows[0]["phone"] == "+491765550213"
    # A search by a word (the code path that lower-cases every address)
    # does not trip over an account that has none.
    assert client.get("/api/admin/users", params={"search": "regular"}, headers=desk).status_code == 200

    exported = client.get("/api/admin/orders/export", headers=desk)
    assert exported.status_code == 200
    assert email_module.PHONE_ACCOUNT_DOMAIN not in exported.text


def test_marketing_consent_needs_a_mailbox():
    body = open_with_phone("0176 5550 214")
    response = client.put(
        f"/api/admin/users/{body['user']['id']}/marketing-consent",
        json={"granted": True, "source": "counter card", "evidence": "signed card"},
        headers=admin_headers(),
    )
    assert response.status_code == 400
    assert "no email address" in response.json()["detail"]


# --------------------------------------------------------------------------
# Getting back in
# --------------------------------------------------------------------------

def test_the_desk_can_hand_over_a_reset_link():
    body = open_with_phone("0176 5550 215")
    desk = admin_headers()
    made = client.post(f"/api/admin/users/{body['user']['id']}/reset-link", headers=desk)
    assert made.status_code == 200, made.text
    link = made.json()["link"]
    assert made.json()["expires_minutes"] == 30
    token = link.split("token=")[1]

    reset = client.post("/api/auth/reset-password", json={"token": token, "password": "Brand-New-77"})
    assert reset.status_code == 200
    assert log_in_with_phone("0176 5550 215", "Brand-New-77").status_code == 200
    assert log_in_with_phone("0176 5550 215", PASSWORD).status_code == 401
    # One shot.
    again = client.post("/api/auth/reset-password", json={"token": token, "password": "Third-Time-78"})
    assert again.status_code == 400


def test_reset_links_are_for_customers_and_made_by_the_desk_only():
    body = open_with_phone("0176 5550 216")
    desk = admin_headers()
    with SessionLocal() as db:
        admin_id = db.query(User).filter(User.email == "admin@djinisbakehouse.com").one().id
    assert client.post(f"/api/admin/users/{admin_id}/reset-link", headers=desk).status_code == 400
    assert client.post("/api/admin/users/999999/reset-link", headers=desk).status_code == 404
    as_customer = client.post(
        f"/api/admin/users/{body['user']['id']}/reset-link", headers=auth_header(body["access_token"])
    )
    assert as_customer.status_code == 403
    assert client.post(f"/api/admin/users/{body['user']['id']}/reset-link").status_code == 401


def test_a_texted_code_finds_the_account_however_the_number_is_typed(monkeypatch):
    bird = bird_fake.enable(monkeypatch, client)
    body = open_with_phone("+49 176 5550 217", name="Coder")
    bird_fake.confirm(client, body["access_token"])
    asked = client.post("/api/auth/phone/request", json={"phone": "0176 5550217"})
    assert asked.status_code == 200
    assert "dev_code" not in asked.json()  # the shop never holds a code
    assert bird.sent[-1] == "+491765550217"
    signed_in = client.post(
        "/api/auth/phone/verify", json={"phone": "0049 1765550217", "code": bird_fake.CODE}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["first_name"] == "Coder"
    assert bird.checked[-1] == "+491765550217"  # the same E.164 number, both ways
