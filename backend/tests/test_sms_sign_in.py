"""Text sign-in through Bird Verify, end to end through the API — with a
stand-in Bird (tests/bird_fake.py), never the real one.

The promises pinned here:

* a number is trusted for text sign-in only after its owner proves it with
  a code, and then only exactly that number: a changed number loses it,
  and changing back does not bring it back;
* admins never sign in by text, not even a customer promoted later;
* a session is issued only on Bird's "verified";
* unknown, admin, unconfirmed and untextable numbers all get one answer,
  and none of them is texted or checked;
* the shop's own cooldown, five tries and ceilings stand in front of Bird;
* nothing about a code, a whole number or the key reaches the logs;
* password sign-in is untouched.

Rides on test_api.py's client (imported, so the app boots once whichever
file pytest reaches first).
"""

import logging
from datetime import timedelta

import pytest

import bird_fake
from bird_fake import CODE, confirm
from test_api import admin_headers, auth_header, client

from app import bird_verify, phones
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import User, UserRole
from app.routers import auth as auth_router

PASSWORD = "Sourdough-42"
SAME_ANSWER = (
    "If that number belongs to an account and has been confirmed for text sign-in, "
    "a code is on its way — it works for 10 minutes."
)
REFUSED = "That code is wrong or has expired — check it, or ask for a new one."


def open_account(n: int, *, email: str | None = None, typed: str | None = None):
    """A customer signed up with a phone number of their own (unique per
    test). Returns (the sign-up answer, the number as E.164, as typed)."""
    typed = typed or f"0176 559 {n:05d}"
    body = {"phone": typed, "password": PASSWORD, "first_name": f"Texter{n}"}
    if email:
        body["email"] = email
    made = client.post("/api/auth/register", json=body)
    assert made.status_code == 201, made.text
    return made.json(), phones.canonical(typed), typed


def ask(phone: str):
    return client.post("/api/auth/phone/request", json={"phone": phone})


def sign_in(phone: str, code: str = CODE):
    return client.post("/api/auth/phone/verify", json={"phone": phone, "code": code})


def stored(user_id: int) -> User:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        db.expunge(user)
        return user


@pytest.fixture
def bird(monkeypatch):
    return bird_fake.enable(monkeypatch, client)


# --------------------------------------------------------------------------
# On only where Bird is
# --------------------------------------------------------------------------

def test_without_bird_there_is_no_text_sign_in_anywhere(monkeypatch):
    account, _e164, typed = open_account(1)
    for env in ("dev", "production"):
        monkeypatch.setattr(get_settings(), "app_env", env)
        assert client.get("/api/store").json()["phone_login"] is False
        assert ask(typed).status_code == 404
        assert sign_in(typed).status_code == 404
        headers = auth_header(account["access_token"])
        assert client.post("/api/auth/me/phone/confirm", headers=headers).status_code == 404
        assert client.post(
            "/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=headers
        ).status_code == 404


def test_a_key_for_another_region_keeps_text_sign_in_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "bird_api_key", "bk_us1_" + "testonly" * 4)
    monkeypatch.setattr(get_settings(), "bird_api_base", "https://eu1.platform.bird.com")
    assert client.get("/api/store").json()["phone_login"] is False
    assert ask("0176 559 00002").status_code == 404


def test_the_store_offers_text_sign_in_once_bird_is_there(bird):
    assert client.get("/api/store").json()["phone_login"] is True


# --------------------------------------------------------------------------
# Confirming a number
# --------------------------------------------------------------------------

def test_no_number_is_trusted_until_its_owner_proves_it(bird):
    account, _e164, typed = open_account(3)
    assert account["user"]["phone_verified"] is False
    assert ask(typed).json() == {"message": SAME_ANSWER}
    assert sign_in(typed).status_code == 400
    assert bird.sent == [] and bird.checked == []  # never texted, never checked


def test_confirm_a_number_then_sign_in_with_a_texted_code(bird):
    account, e164, typed = open_account(4, email="texter-4@example.com")
    headers = auth_header(account["access_token"])

    started = client.post("/api/auth/me/phone/confirm", headers=headers)
    assert started.status_code == 200
    assert started.json()["phone_verified"] is False
    assert "dev_code" not in started.json() and CODE not in started.text
    assert started.json()["message"].startswith("A code is on its way to your number ending in")
    assert bird.sent == [e164]

    wrong = client.post("/api/auth/me/phone/confirm/check", json={"code": "000000"}, headers=headers)
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "That code isn't right — 4 tries left."
    right = client.post("/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=headers)
    assert right.status_code == 200 and right.json()["phone_verified"] is True
    assert client.get("/api/auth/me", headers=headers).json()["phone_verified"] is True
    on_file = stored(account["user"]["id"])
    assert on_file.phone_verified_number == e164 and on_file.phone_verified_at is not None

    # Now the number signs in by text — however it is typed, the same E.164
    # number goes to Bird for the code and for the check.
    assert ask("+49 (0)176 559 00004").json() == {"message": SAME_ANSWER}
    assert bird.sent == [e164, e164]
    signed_in = sign_in("0049 176 559 00004")
    assert signed_in.status_code == 200
    assert bird.checked[-1] == e164
    assert signed_in.json()["user"]["first_name"] == "Texter4"
    me = client.get("/api/auth/me", headers=auth_header(signed_in.json()["access_token"]))
    assert me.status_code == 200
    # A code works once.
    assert sign_in(typed).status_code == 400


def test_the_confirm_check_needs_a_code_that_was_sent(bird):
    account, _e164, _typed = open_account(5)
    headers = auth_header(account["access_token"])
    unasked = client.post("/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=headers)
    assert unasked.status_code == 400
    assert unasked.json()["detail"] == "That code can't be used any more — send a new one."
    assert bird.checked == []  # nothing was open, so Bird was not asked


def test_an_untextable_number_cannot_be_confirmed(bird):
    account, _e164, typed = open_account(6, typed="030 1234 5606")  # a Berlin landline
    started = client.post("/api/auth/me/phone/confirm", headers=auth_header(account["access_token"]))
    assert started.status_code == 400
    assert "German mobile numbers" in started.json()["detail"]
    assert bird.sent == []


def test_a_confirmed_number_is_not_texted_again_to_confirm_it(bird):
    account, _e164, _typed = open_account(7)
    confirm(client, account["access_token"])
    again = client.post("/api/auth/me/phone/confirm", headers=auth_header(account["access_token"]))
    assert again.json() == {"message": "Your number is already confirmed.", "phone_verified": True}
    assert len(bird.sent) == 1


# --------------------------------------------------------------------------
# A changed number is a new number
# --------------------------------------------------------------------------

def test_changing_the_number_drops_the_confirmation(bird):
    account, e164, typed = open_account(8)
    headers = auth_header(account["access_token"])
    confirm(client, account["access_token"])

    # The same number, spelled another way, is not a change.
    same = client.put("/api/auth/me", headers=headers, json={"phone": "+49 (0)176 559 00008"})
    assert same.json()["phone_verified"] is True

    moved = client.put("/api/auth/me", headers=headers, json={"phone": "0176 559 00009"})
    assert moved.status_code == 200 and moved.json()["phone_verified"] is False
    on_file = stored(account["user"]["id"])
    assert on_file.phone_verified_number is None and on_file.phone_verified_at is None
    sent = len(bird.sent)
    assert ask("0176 559 00009").json() == {"message": SAME_ANSWER}
    assert len(bird.sent) == sent  # the new number is not trusted yet

    # Moving back does not bring the old proof back.
    back = client.put("/api/auth/me", headers=headers, json={"phone": typed})
    assert back.json()["phone_verified"] is False
    assert ask(typed).json() == {"message": SAME_ANSWER}
    assert len(bird.sent) == sent
    assert sign_in(typed).status_code == 400


# --------------------------------------------------------------------------
# Admins: never by text
# --------------------------------------------------------------------------

def test_an_admin_never_signs_in_by_text(bird):
    admin = admin_headers()
    try:
        assert client.put("/api/auth/me", headers=admin, json={"phone": "0176 559 00010"}).status_code == 200
        started = client.post("/api/auth/me/phone/confirm", headers=admin)
        assert started.status_code == 403
        assert "password" in started.json()["detail"]
        assert client.post(
            "/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=admin
        ).status_code == 403
        assert ask("0176 559 00010").json() == {"message": SAME_ANSWER}
        assert sign_in("0176 559 00010").status_code == 400
        assert bird.sent == [] and bird.checked == []
    finally:
        client.put("/api/auth/me", headers=admin, json={"phone": ""})


def test_a_customer_promoted_to_admin_loses_text_sign_in(bird):
    account, _e164, typed = open_account(11)
    confirm(client, account["access_token"])
    with SessionLocal() as db:
        db.get(User, account["user"]["id"]).role = UserRole.ADMIN.value
        db.commit()
    try:
        sent, checked = len(bird.sent), len(bird.checked)
        assert ask(typed).json() == {"message": SAME_ANSWER}
        assert sign_in(typed).status_code == 400
        assert (len(bird.sent), len(bird.checked)) == (sent, checked)
    finally:
        with SessionLocal() as db:
            db.get(User, account["user"]["id"]).role = UserRole.CUSTOMER.value
            db.commit()


# --------------------------------------------------------------------------
# One answer for every number
# --------------------------------------------------------------------------

def test_unknown_admin_unconfirmed_and_untextable_numbers_read_the_same(bird):
    confirmed, e164, confirmed_typed = open_account(12)
    confirm(client, confirmed["access_token"])
    _unconfirmed, _x, unconfirmed_typed = open_account(13)
    _landline, _y, landline_typed = open_account(14, typed="030 1234 5614")
    admin = admin_headers()
    try:
        client.put("/api/auth/me", headers=admin, json={"phone": "0176 559 00015"})
        others = ["0176 559 00099", "0176 559 00015", unconfirmed_typed, landline_typed]
        sent = len(bird.sent)
        answers = [ask(number) for number in [*others, confirmed_typed]]
        assert {a.status_code for a in answers} == {200}
        assert all(a.json() == {"message": SAME_ANSWER} for a in answers)
        assert bird.sent[sent:] == [e164]  # only the confirmed customer's number

        checked = len(bird.checked)
        refusals = [sign_in(number) for number in others]
        assert {r.status_code for r in refusals} == {400}
        assert all(r.json() == {"detail": REFUSED} for r in refusals)
        assert len(bird.checked) == checked  # none of them reached Bird
    finally:
        client.put("/api/auth/me", headers=admin, json={"phone": ""})


def test_a_number_two_old_accounts_share_is_never_texted(bird):
    e164 = phones.canonical("0176 559 00016")
    with SessionLocal() as db:
        for twin in ("a", "b"):
            db.add(
                User(
                    email=f"twin-{twin}-16@example.com",
                    password_hash="x",
                    first_name="Twin",
                    last_name=twin,
                    phone=e164,
                    phone_verified_number=e164,
                    phone_verified_at=utcnow(),
                )
            )
        db.commit()
    assert ask(e164).json() == {"message": SAME_ANSWER}
    assert sign_in(e164).status_code == 400
    assert bird.sent == [] and bird.checked == []


def test_a_deactivated_customer_is_not_texted(bird):
    account, _e164, typed = open_account(17)
    confirm(client, account["access_token"])
    with SessionLocal() as db:
        db.get(User, account["user"]["id"]).is_active = False
        db.commit()
    sent = len(bird.sent)
    assert ask(typed).json() == {"message": SAME_ANSWER}
    assert sign_in(typed).status_code == 400
    assert len(bird.sent) == sent


# --------------------------------------------------------------------------
# The shop's own brakes, in front of Bird's
# --------------------------------------------------------------------------

def test_cooldown_then_hourly_then_daily_ceilings(monkeypatch):
    bird = bird_fake.enable(monkeypatch, client, hour=3, day=4)
    account, e164, typed = open_account(18)
    confirm(client, account["access_token"])  # send 1 (the window closes once confirmed)
    ask(typed)  # send 2
    ask(typed)  # inside the minute: nothing
    assert bird.sent.count(e164) == 2

    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)
    ask(typed)  # send 3
    ask(typed)  # the hour's ceiling (3): nothing
    assert bird.sent.count(e164) == 3

    # An hour later the day's ceiling (4) still holds.
    monkeypatch.setitem(client.app.state.limiters, "otp_number", auth_router_limiter(5, 3600.0))
    ask(typed)  # send 4
    ask(typed)  # the day's ceiling: nothing
    assert bird.sent.count(e164) == 4
    assert ask(typed).json() == {"message": SAME_ANSWER}  # the answer never changes


def test_the_shop_wide_ceiling_stops_all_texts_for_the_day(monkeypatch, caplog):
    bird = bird_fake.enable(monkeypatch, client, shop=3)
    first, first_e164, first_typed = open_account(19)
    second, second_e164, second_typed = open_account(20)
    confirm(client, first["access_token"])  # 1
    confirm(client, second["access_token"])  # 2
    ask(first_typed)  # 3
    with caplog.at_level(logging.ERROR, logger="bakery.auth"):
        assert ask(second_typed).json() == {"message": SAME_ANSWER}
    assert bird.sent == [first_e164, second_e164, first_e164]
    assert "SMS_DAILY_LIMIT" in caplog.text
    over = client.post("/api/auth/me/phone/confirm", headers=auth_header(open_account(21)[0]["access_token"]))
    assert over.status_code == 429


def test_five_wrong_codes_end_the_window(monkeypatch):
    bird = bird_fake.enable(monkeypatch, client)
    account, e164, typed = open_account(22)
    confirm(client, account["access_token"])
    ask(typed)
    checked = len(bird.checked)
    for guess in ("111111", "222222", "333333", "444444", "555555"):
        assert sign_in(typed, guess).json() == {"detail": REFUSED}
    assert sign_in(typed, CODE).status_code == 400  # over: even the right code
    assert len(bird.checked) - checked == 5  # the sixth never reached Bird

    # A fresh code starts afresh.
    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)
    ask(typed)
    assert sign_in(typed, CODE).status_code == 200


def test_the_shop_counts_tries_even_if_bird_would_allow_more(monkeypatch):
    bird = bird_fake.enable(monkeypatch, client)
    account, _e164, typed = open_account(23)
    confirm(client, account["access_token"])
    ask(typed)
    monkeypatch.setattr(bird_verify, "check", lambda phone, code: bird.checked.append(phone) or bird_verify.Checked(bird_verify.WRONG, 9))
    checked = len(bird.checked)
    for _ in range(7):
        assert sign_in(typed, "999999").status_code == 400
    assert len(bird.checked) - checked == auth_router.PHONE_OTP_MAX_ATTEMPTS


def test_an_expired_window_never_reaches_bird(bird):
    account, _e164, typed = open_account(24)
    confirm(client, account["access_token"])
    ask(typed)
    with SessionLocal() as db:
        db.get(User, account["user"]["id"]).phone_otp_expires = utcnow() - timedelta(seconds=1)
        db.commit()
    checked = len(bird.checked)
    assert sign_in(typed).json() == {"detail": REFUSED}
    assert len(bird.checked) == checked


def test_bird_saying_expired_is_said_plainly_when_confirming(bird, monkeypatch):
    account, _e164, _typed = open_account(25)
    headers = auth_header(account["access_token"])
    client.post("/api/auth/me/phone/confirm", headers=headers)
    monkeypatch.setattr(bird_verify, "check", lambda phone, code: bird_verify.Checked(bird_verify.EXPIRED))
    late = client.post("/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=headers)
    assert late.status_code == 400
    assert late.json()["detail"] == "That code has expired — send a new one."
    assert client.get("/api/auth/me", headers=headers).json()["phone_verified"] is False


# --------------------------------------------------------------------------
# Bird down, or holding back: nobody signed in, nothing confirmed
# --------------------------------------------------------------------------

def test_a_bird_outage_signs_nobody_in(bird, monkeypatch, caplog):
    account, _e164, typed = open_account(26)
    confirm(client, account["access_token"])
    ask(typed)

    bird.fail_check = bird_verify.Unavailable("Bird did not answer")
    down = sign_in(typed)
    assert down.status_code == 503
    assert down.json()["detail"] == "We couldn't check the code right now — please try again in a moment."
    assert "access_token" not in down.json()

    # A send that fails after the answer has gone: the same answer, a log line.
    bird.fail_check = None
    bird.fail_start = bird_verify.Unavailable("Bird did not answer")
    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)
    with caplog.at_level(logging.ERROR, logger="bakery.sms"):
        assert ask(typed).json() == {"message": SAME_ANSWER}
    assert "not sent" in caplog.text

    # Confirming during an outage says so, and leaves no window open.
    other, _e, _t = open_account(27)
    headers = auth_header(other["access_token"])
    refused = client.post("/api/auth/me/phone/confirm", headers=headers)
    assert refused.status_code == 503
    assert stored(other["user"]["id"]).phone_otp_expires is None


def test_bird_holding_back_reads_like_the_shops_own_brake(bird):
    account, _e164, typed = open_account(28)
    confirm(client, account["access_token"])
    ask(typed)
    bird.fail_check = bird_verify.TooMany("slow down")
    held = sign_in(typed)
    assert held.status_code == 429
    assert held.json()["detail"] == "Too many attempts. Try again in a minute."


def test_bird_refusing_a_number_when_confirming(bird):
    account, _e164, _typed = open_account(29)
    bird.fail_start = bird_verify.Refused("not a usable recipient")
    refused = client.post("/api/auth/me/phone/confirm", headers=auth_header(account["access_token"]))
    assert refused.status_code == 400
    assert refused.json()["detail"] == "That number can't receive a text code — check it in your profile."
    assert stored(account["user"]["id"]).phone_otp_expires is None


# --------------------------------------------------------------------------
# Untouched: password sign-in. Kept honest: the logs, the export, deletion
# --------------------------------------------------------------------------

def test_password_sign_in_is_untouched(bird):
    account, _e164, typed = open_account(30, email="texter-30@example.com")
    confirm(client, account["access_token"])
    assert client.post("/api/auth/login", json={"email": "texter-30@example.com", "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"phone": typed, "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"phone": typed, "password": "not-it"}).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": "admin@djinisbakehouse.com", "password": "Admin123!"}
    ).status_code == 200


def test_nothing_about_a_code_a_whole_number_or_the_key_is_logged(bird, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    account, e164, typed = open_account(31)
    headers = auth_header(account["access_token"])
    # From here on: the text flows. (Signing up rings the desk by email, and
    # with no mail server a dev box logs that email whole, number included —
    # production logs only its recipient and subject, app/email.py.)
    caplog.clear()
    client.post("/api/auth/me/phone/confirm", headers=headers)
    client.post("/api/auth/me/phone/confirm/check", json={"code": "135791"}, headers=headers)
    client.post("/api/auth/me/phone/confirm/check", json={"code": CODE}, headers=headers)
    ask(typed)
    sign_in(typed, "864209")
    sign_in(typed, CODE)
    monkeypatch.setattr(auth_router, "PHONE_RESEND_SECONDS", 0)
    bird.fail_start = bird_verify.Unavailable("Bird did not answer")
    ask(typed)
    bird.fail_start = None
    ask(typed)
    bird.fail_check = bird_verify.Unavailable("Bird did not answer")
    sign_in(typed, "102938")

    logged = caplog.text + "".join(
        f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records
    )
    for secret in (
        CODE, "135791", "864209", "102938",
        e164, e164[1:], "017655900031", typed,
        bird_fake.FAKE_KEY,
    ):
        assert secret not in logged, secret


def test_the_data_export_says_when_the_number_was_confirmed(bird):
    confirmed, _e164, _typed = open_account(32)
    confirm(client, confirmed["access_token"])
    exported = client.get("/api/auth/me/export", headers=auth_header(confirmed["access_token"])).json()
    assert exported["account"]["phone_verified_at"]
    unconfirmed, _x, _y = open_account(33)
    exported = client.get("/api/auth/me/export", headers=auth_header(unconfirmed["access_token"])).json()
    assert exported["account"]["phone_verified_at"] is None


def test_closing_the_account_forgets_the_confirmed_number(bird):
    account, _e164, _typed = open_account(34, email="texter-34@example.com")
    confirm(client, account["access_token"])
    closed = client.post(
        "/api/auth/me/delete", json={"password": PASSWORD}, headers=auth_header(account["access_token"])
    )
    assert closed.status_code == 200, closed.text
    gone = stored(account["user"]["id"])
    assert gone.phone is None and gone.phone_verified_number is None and gone.phone_verified_at is None


def auth_router_limiter(limit: int, window: float):
    from app import SlidingWindowLimiter

    return SlidingWindowLimiter(limit, window=window)
