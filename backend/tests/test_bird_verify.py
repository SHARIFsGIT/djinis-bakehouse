"""The Bird Verify gateway on its own, against a recorded wire (never Bird):
the exact requests Bird's API reference documents, every answer it
documents, and a closed door for everything else. Plus the phone-number
rules texts depend on, and the boot-time columns that remember a confirmed
number.

References: https://bird.com/docs/api/reference/create-verification and
https://bird.com/docs/api/reference/create-verification-check.

Rides on test_api.py's client (imported, so the app boots once whichever
file pytest reaches first).
"""

import io
import json
import logging
import socket
import sqlite3
import sys
import urllib.error
import urllib.request

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import test_api  # noqa: F401 - boots the app with the suite's settings first

from app import bird_verify, phones
from app.config import get_settings
from app.database import Base
from app.models import User

KEY = "bk_eu1_" + "unittest" * 4  # shaped like a workspace key; not a real one
NUMBER = "+491765550100"
CODE = "246810"
BASE = "https://eu1.platform.bird.com"


class _Answer:
    def __init__(self, status: int, raw: bytes):
        self.status = status
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Wire:
    """Stands in for urlopen: records each request, answers from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": {name.lower(): value for name, value in request.header_items()},
                "body": json.loads(request.data.decode()),
                "timeout": timeout,
            }
        )
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(raw))
        return _Answer(status, raw)


@pytest.fixture
def bird_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "bird_api_key", KEY)
    monkeypatch.setattr(get_settings(), "bird_api_base", BASE)


def wire(monkeypatch, *answers) -> Wire:
    recorded = Wire(*answers)
    monkeypatch.setattr(bird_verify, "_urlopen", recorded)
    return recorded


def verification(status="pending", channel="sms", to=NUMBER):
    """A verification object as the reference documents it."""
    return {
        "id": "vrf_01testtesttesttesttesttest",
        "status": status,
        "reason": None,
        "to": {"phone_number": to},
        "channels": [{"channel": channel}],
        "last_channel": channel,
        "expires_at": "2026-11-23T10:10:00Z",
        "verified_at": None,
        "created_at": "2026-11-23T10:00:00Z",
        "updated_at": "2026-11-23T10:00:00Z",
    }


def checked(success, reason=None, status="pending", remaining=None, to=NUMBER):
    return {
        "success": success,
        "reason": reason,
        "verification": verification(status=status, to=to),
        "attempts_remaining": remaining,
    }


def envelope(code="E01001", name="ValidationError"):
    """Bird's error envelope. Its message repeats the number on purpose:
    the gateway must not log it."""
    return {
        "error": {
            "type": "validation_error",
            "code": code,
            "name": name,
            "message": f"something about {NUMBER}",
            "request_id": "req_01testtesttest",
        }
    }


# --------------------------------------------------------------------------
# The requests, exactly as documented
# --------------------------------------------------------------------------

def test_a_code_is_asked_for_by_sms_only_exactly_as_documented(bird_on, monkeypatch):
    recorded = wire(monkeypatch, (200, verification()))
    bird_verify.start(NUMBER)
    (sent,) = recorded.requests
    assert sent["method"] == "POST"
    assert sent["url"] == f"{BASE}/v1/verify/verifications"
    assert sent["headers"]["authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["headers"]["accept"] == "application/json"
    assert sent["body"] == {
        "to": {"phone_number": NUMBER},
        "options": {"channels": ["sms"], "code_length": 6},
    }
    assert sent["timeout"] == bird_verify.REQUEST_TIMEOUT_SECONDS == 10


def test_a_code_is_checked_exactly_as_documented(bird_on, monkeypatch):
    recorded = wire(monkeypatch, (200, checked(True, status="verified")))
    assert bird_verify.check(NUMBER, CODE).outcome == bird_verify.VERIFIED
    (sent,) = recorded.requests
    assert sent["method"] == "POST"
    assert sent["url"] == f"{BASE}/v1/verify/verifications/check"
    assert sent["headers"]["authorization"] == f"Bearer {KEY}"
    assert sent["body"] == {"to": {"phone_number": NUMBER}, "code": CODE}
    assert sent["timeout"] == 10


# --------------------------------------------------------------------------
# Checking: every documented answer, and a closed door for the rest
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "answer, outcome, remaining",
    [
        ((200, checked(True, status="verified")), bird_verify.VERIFIED, None),
        ((200, checked(False, "incorrect_code", "pending", remaining=4)), bird_verify.WRONG, 4),
        ((200, checked(False, "a_reason_added_later", "pending")), bird_verify.WRONG, None),
        ((200, checked(False, "expired", "expired")), bird_verify.EXPIRED, None),
        ((200, checked(False, "attempts_exhausted", "failed")), bird_verify.EXHAUSTED, None),
        ((200, checked(False, None, "canceled")), bird_verify.GONE, None),
        ((200, checked(False, None, "blocked")), bird_verify.GONE, None),
        ((404, envelope("E13000", "VerificationNotFound")), bird_verify.GONE, None),
        ((422, envelope()), bird_verify.GONE, None),
    ],
)
def test_every_documented_check_answer(bird_on, monkeypatch, answer, outcome, remaining):
    wire(monkeypatch, answer)
    result = bird_verify.check(NUMBER, CODE)
    assert result.outcome == outcome
    assert result.attempts_remaining == remaining


@pytest.mark.parametrize(
    "answer",
    [
        (200, checked(True, status="pending")),  # "success" without a verified verification
        (200, {"success": True}),  # no verification at all
        (200, {"verification": verification("verified")}),  # no success flag
        (200, checked(True, status="verified", to="+491765550199")),  # someone else's number
        (200, b"<html>not json</html>"),
        (200, b"[1, 2, 3]"),
        (401, envelope("E02001", "Unauthorized")),
        (403, envelope("E02035", "InsufficientScope")),
        (421, envelope("E01010", "MisdirectedRequest")),
        (402, envelope("E03001", "WalletInsufficientBalance")),
        (500, envelope("E09999", "InternalError")),
        (503, b""),
        socket.timeout("timed out"),
        urllib.error.URLError("no route"),
    ],
)
def test_anything_else_from_a_check_confirms_nothing(bird_on, monkeypatch, answer):
    wire(monkeypatch, answer)
    with pytest.raises(bird_verify.Unavailable):
        bird_verify.check(NUMBER, CODE)


def test_a_redirect_is_never_followed_and_the_key_never_travels(bird_on):
    """urllib would re-send a redirected POST, Authorization header and all,
    to wherever the redirect points. The gateway's opener refuses: asked
    once, answered with the 3xx, nothing sent anywhere else. (A local
    server on 127.0.0.1 stands in for a host that redirects — not Bird.)"""
    import http.server
    import threading

    asked = []

    class Redirecting(http.server.BaseHTTPRequestHandler):
        def _note(self):
            asked.append((self.command, self.path, self.headers.get("Authorization")))

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))  # else Windows resets the connection
            self._note()
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/elsewhere")
            self.end_headers()

        def do_GET(self):
            self._note()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirecting)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/verify/verifications",
            data=b"{}",
            headers={"Authorization": f"Bearer {KEY}"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as answered:
            bird_verify._OPENER.open(request, timeout=5)
        assert answered.value.code == 302
        assert asked == [("POST", "/v1/verify/verifications", f"Bearer {KEY}")]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("call", ["start", "check"])
def test_a_redirect_from_bird_confirms_and_sends_nothing(bird_on, monkeypatch, call):
    redirect = urllib.error.HTTPError(f"{BASE}/x", 302, "Found", {}, io.BytesIO(b""))
    wire(monkeypatch, redirect)
    with pytest.raises(bird_verify.Unavailable):
        if call == "start":
            bird_verify.start(NUMBER)
        else:
            bird_verify.check(NUMBER, CODE)


def test_checking_too_quickly_is_bird_holding_back(bird_on, monkeypatch):
    wire(monkeypatch, (429, envelope("E01020", "RateLimited")))
    with pytest.raises(bird_verify.TooMany):
        bird_verify.check(NUMBER, CODE)


# --------------------------------------------------------------------------
# Starting: SMS and nothing else
# --------------------------------------------------------------------------

def test_a_pending_sms_verification_is_a_code_on_its_way(bird_on, monkeypatch):
    wire(monkeypatch, (200, verification()))
    assert bird_verify.start(NUMBER) is None


@pytest.mark.parametrize(
    "answer, raised",
    [
        ((422, envelope()), bird_verify.Refused),  # not a usable recipient / no SMS route
        ((200, verification(status="blocked")), bird_verify.Refused),  # fraud controls
        ((200, verification(status="failed")), bird_verify.Refused),
        ((429, envelope("E01020", "RateLimited")), bird_verify.TooMany),  # 5 sends an hour
        ((200, verification(channel="whatsapp")), bird_verify.Unavailable),  # not what was asked
        ((200, verification(status="verified")), bird_verify.Unavailable),
        ((200, verification(status="a_state_added_later")), bird_verify.Unavailable),
        ((200, verification(to="+491765550199")), bird_verify.Unavailable),
        ((200, {"status": "pending"}), bird_verify.Unavailable),  # no recipient, no plan
        ((401, envelope("E02001", "Unauthorized")), bird_verify.Unavailable),
        ((421, envelope("E01010", "MisdirectedRequest")), bird_verify.Unavailable),
        ((500, envelope("E09999", "InternalError")), bird_verify.Unavailable),
        (socket.timeout("timed out"), bird_verify.Unavailable),
    ],
)
def test_a_start_that_is_not_a_pending_sms_is_never_taken_as_one(bird_on, monkeypatch, answer, raised):
    wire(monkeypatch, answer)
    with pytest.raises(raised):
        bird_verify.start(NUMBER)


def test_a_plan_that_falls_back_to_whatsapp_is_refused(bird_on, monkeypatch):
    plan = verification()
    plan["channels"] = [{"channel": "sms"}, {"channel": "whatsapp"}]
    wire(monkeypatch, (200, plan))
    with pytest.raises(bird_verify.Unavailable):
        bird_verify.start(NUMBER)


@pytest.mark.parametrize(
    "failure",
    [
        (422, envelope()),
        (429, envelope("E01020", "RateLimited")),
        (500, envelope("E09999", "InternalError")),
        socket.timeout("timed out"),
    ],
)
def test_sending_in_the_background_never_raises(bird_on, monkeypatch, failure):
    wire(monkeypatch, failure)
    bird_verify.send_quietly(NUMBER)  # logged, not raised


# --------------------------------------------------------------------------
# Configuration: a workspace key, for the right region
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key, base, on",
    [
        ("", BASE, False),
        (KEY, BASE, True),
        (KEY, BASE + "/", True),
        ("bk_us1_" + "unittest" * 4, BASE, False),  # a US key answers only at us1
        ("bk_us1_" + "unittest" * 4, "https://us1.platform.bird.com", True),
        ("bm_eu1_" + "unittest" * 4, BASE, False),  # a master key needs a workspace header
        ("not-a-bird-key", BASE, False),
        (KEY, "https://eu1.platform.bird.com.example.org", False),
        (KEY, "http://eu1.platform.bird.com", False),
    ],
)
def test_text_sign_in_needs_a_workspace_key_for_its_own_region(monkeypatch, key, base, on):
    monkeypatch.setattr(get_settings(), "bird_api_key", key)
    monkeypatch.setattr(get_settings(), "bird_api_base", base)
    assert bird_verify.configured() is on
    reason = bird_verify.misconfigured()
    assert (reason is None) == (on or not key)
    if key:
        assert key not in (reason or "")  # the boot log never names the key


def test_unconfigured_never_touches_the_wire(monkeypatch):
    monkeypatch.setattr(get_settings(), "bird_api_key", "")
    recorded = wire(monkeypatch)
    with pytest.raises(bird_verify.Unavailable):
        bird_verify.start(NUMBER)
    with pytest.raises(bird_verify.Unavailable):
        bird_verify.check(NUMBER, CODE)
    assert recorded.requests == []


# --------------------------------------------------------------------------
# Logs: never the key, the code or the whole number
# --------------------------------------------------------------------------

def test_the_logs_never_carry_the_key_a_code_or_a_whole_number(bird_on, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    answers = [
        (200, verification()),
        (422, envelope()),
        (429, envelope("E01020", "RateLimited")),
        (401, envelope("E02001", "Unauthorized")),
        (421, envelope("E01010", "MisdirectedRequest")),
        (500, envelope("E09999", "InternalError")),
        socket.timeout("timed out"),
        (200, verification(channel="whatsapp")),
        (200, checked(True, status="verified")),
        (200, checked(False, "incorrect_code", "pending", remaining=3)),
        (404, envelope("E13000", "VerificationNotFound")),
        (200, checked(True, status="pending")),
        (200, b"not json"),
    ]
    wire(monkeypatch, *answers)
    for _ in range(8):
        bird_verify.send_quietly(NUMBER)
    for _ in range(5):
        try:
            bird_verify.check(NUMBER, CODE)
        except bird_verify.Unavailable:
            pass
    logged = caplog.text + "".join(
        f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records
    )
    assert logged  # it did say things...
    for secret in (KEY, CODE, NUMBER, NUMBER[1:], "01765550100", "1765550100"):
        assert secret not in logged, secret
    assert phones.masked(NUMBER) in logged  # ...with the number masked
    assert "E13000" in logged and "req_01testtesttest" in logged  # what Bird's docs say to log


# --------------------------------------------------------------------------
# Phone numbers: E.164 for texts
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "typed",
    ["+49 (0)176 5550100", "+49 0176 5550100", "0049 (0) 176 5550100", "0176 5550100", "+491765550100"],
)
def test_the_national_zero_never_survives_behind_the_country_code(typed):
    assert phones.canonical(typed, "49") == "+491765550100"


def test_an_account_stored_with_the_old_trunk_zero_is_still_found():
    # what canonical() kept for "+49 (0)176 …" before today
    assert "+4901765550100" in phones.spellings("0176 5550100", "49")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0176 5550100", "+491765550100"),
        ("+49 (0)152 12345678", "+4915212345678"),
        ("+49 160 1234567", "+491601234567"),
        ("030 1234567", None),  # a Berlin landline can't take a text
        ("+49 900 1234567", None),  # a premium line
        ("+44 7700 900123", None),  # abroad: not a destination the shop texts
        ("1765550100", None),  # bare digits don't say their country
        ("", None),
        (None, None),
    ],
)
def test_only_german_mobile_numbers_are_texted(value, expected):
    assert phones.sms_number(value) == expected


def test_a_masked_number_never_shows_the_whole_of_it():
    assert phones.masked("+491765550100") == "+49176*****00"
    assert "5550" not in phones.masked("+491765550100")
    assert phones.masked("") == ""
    assert phones.masked("+4912") == "*****"


# --------------------------------------------------------------------------
# The boot-time columns: added once, nullable, nothing trusted
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35), reason="needs SQLite's DROP COLUMN to build the old shape"
)
def test_boot_adds_the_two_columns_and_trusts_no_number(tmp_path, monkeypatch):
    """A database from before this change: the users table without the two
    columns, a customer with a number on file. The boot adds the columns —
    empty for everyone — and a second boot changes nothing."""
    app_package = sys.modules["app"]
    old = create_engine(f"sqlite:///{(tmp_path / 'before.db').as_posix()}")
    Base.metadata.create_all(old)
    with Session(old) as db:
        db.add(
            User(
                email="before@example.com",
                password_hash="x",
                first_name="Before",
                last_name="",
                phone="+491765550100",
            )
        )
        db.commit()
    with old.begin() as conn:
        conn.execute(text("ALTER TABLE users DROP COLUMN phone_verified_number"))
        conn.execute(text("ALTER TABLE users DROP COLUMN phone_verified_at"))
    assert "phone_verified_number" not in {c["name"] for c in inspect(old).get_columns("users")}

    monkeypatch.setattr(app_package, "engine", old)
    app_package._upgrade_schema()
    app_package._upgrade_schema()  # a second boot: nothing left to add, nothing fails

    users = {c["name"]: c for c in inspect(old).get_columns("users")}
    assert users["phone_verified_number"]["nullable"] and users["phone_verified_at"]["nullable"]
    assert "method_type" not in users  # belongs to payment_transactions, and is there
    assert "method_type" in {c["name"] for c in inspect(old).get_columns("payment_transactions")}
    with old.connect() as conn:
        row = conn.execute(
            text("SELECT phone, phone_verified_number, phone_verified_at FROM users")
        ).one()
    assert row == ("+491765550100", None, None)  # on file, and not trusted
    old.dispose()
