"""A stand-in for Bird Verify, for the API tests.

The gateway's two calls (app/bird_verify.start and .check) are swapped for
an in-memory double that keeps Bird's documented rules: one verification
per number, reused when a code is asked for again; five wrong codes end it;
a finished verification answers "gone". The wire itself stays shut for
every test (backend/conftest.py), so nothing here can reach Bird.

Not a test module (no test_ prefix): imported by the ones that need it.
"""

# The suite's settings (a throwaway database, test keys) must be in place
# before anything imports the app — whichever module imports this one, and
# whichever pytest reaches first. test_api.py sets them up.
import test_api  # noqa: F401, I001 - must come before the app

from app import SlidingWindowLimiter, bird_verify
from app.config import get_settings

# Shaped like a workspace key for the EU region — and not a real one.
FAKE_KEY = "bk_eu1_" + "testonly" * 4
# The code the stand-in "texts". The shop under test never sees it either:
# the test types it in, as a customer would.
CODE = "246810"


class FakeBird:
    def __init__(self):
        self.sent = []  # numbers a code was texted to, in order
        self.checked = []  # numbers a code was checked for, in order
        self.open = {}  # number -> {"code", "left", "state"}
        self.fail_start = None  # an exception for start() to raise
        self.fail_check = None  # an exception for check() to raise

    def start(self, phone: str) -> None:
        if self.fail_start is not None:
            raise self.fail_start
        self.sent.append(phone)
        verification = self.open.get(phone)
        if verification is None or verification["state"] != "pending":
            self.open[phone] = {"code": CODE, "left": 5, "state": "pending"}

    def check(self, phone: str, code: str) -> bird_verify.Checked:
        self.checked.append(phone)
        if self.fail_check is not None:
            raise self.fail_check
        verification = self.open.get(phone)
        if verification is None or verification["state"] != "pending":
            return bird_verify.Checked(bird_verify.GONE)
        if code == verification["code"]:
            verification["state"] = "verified"
            return bird_verify.Checked(bird_verify.VERIFIED)
        verification["left"] -= 1
        if verification["left"] <= 0:
            verification["state"] = "failed"
            return bird_verify.Checked(bird_verify.EXHAUSTED)
        return bird_verify.Checked(bird_verify.WRONG, verification["left"])


def enable(monkeypatch, client, *, hour: int = 5, day: int = 10, shop: int = 100) -> FakeBird:
    """Switch text sign-in on with the stand-in, and give this test fresh
    ceilings (the real limiters live as long as the app does)."""
    fake = FakeBird()
    monkeypatch.setattr(get_settings(), "bird_api_key", FAKE_KEY)
    monkeypatch.setattr(get_settings(), "bird_api_base", "https://eu1.platform.bird.com")
    monkeypatch.setattr(bird_verify, "start", fake.start)
    monkeypatch.setattr(bird_verify, "check", fake.check)
    limiters = client.app.state.limiters
    monkeypatch.setitem(limiters, "otp_number", SlidingWindowLimiter(hour, window=3600.0))
    monkeypatch.setitem(limiters, "otp_number_day", SlidingWindowLimiter(day, window=86400.0))
    monkeypatch.setitem(limiters, "otp_shop_day", SlidingWindowLimiter(shop, window=86400.0))
    return fake


def confirm(client, token: str, code: str = CODE) -> dict:
    """Prove the signed-in customer's number the way they would: ask for a
    code, type it in."""
    headers = {"Authorization": f"Bearer {token}"}
    started = client.post("/api/auth/me/phone/confirm", headers=headers)
    assert started.status_code == 200, started.text
    done = client.post("/api/auth/me/phone/confirm/check", json={"code": code}, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["phone_verified"] is True
    return done.json()
