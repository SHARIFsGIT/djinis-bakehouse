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
