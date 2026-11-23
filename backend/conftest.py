"""Ensure the backend package is importable no matter where pytest runs from,
and give every test module the same few settings before any of them boots
the app (whichever pytest collects first does)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# The per-account brakes exist for scripts, not for a suite that places
# hundreds of orders and reviews under a handful of logins.
os.environ.setdefault("ORDER_RATE_LIMIT", "100000")
os.environ.setdefault("REVIEW_RATE_LIMIT", "100000")
os.environ.setdefault("PAYMENT_ACTION_RATE_LIMIT", "100000")
# The shop's clock follows this machine's, so "today" in a test is the same
# day the code under test sees (the zone logic itself has its own tests).
os.environ.setdefault("SHOP_TIMEZONE", "local")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _bird_is_never_called(monkeypatch):
    """No test reaches Bird Verify. The gateway's one door to the network is
    shut for every test (tests that check the requests it builds put their
    own recorder in its place), and a test that knocks on the shut door
    fails — even when the gateway, failing closed, swallowed the refusal."""
    knocked = []

    def shut(request, *args, **kwargs):
        knocked.append(getattr(request, "full_url", repr(request)))
        raise OSError("the test suite never calls Bird")

    monkeypatch.setattr("app.bird_verify._urlopen", shut)
    yield
    assert not knocked, f"a test tried to reach Bird: {knocked}"
