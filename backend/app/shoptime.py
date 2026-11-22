"""The shop's own clock.

The database keeps every moment in UTC, and the server it runs on usually
thinks in UTC too — but a bakery lives in one place. "Today", "tomorrow"
and the time printed on a receipt are that place's, never the server's:
an order placed at half past midnight in Berlin belongs to the new day,
and its receipt should say 00:30, not 23:30.

SHOP_TIMEZONE names the place (an IANA zone, "Europe/Berlin" by default).
"local" (or empty) means "whatever the server's own clock says" — the
behaviour the shop had before this module existed. A name the system does
not know falls back to UTC, loudly, rather than stopping anything.
"""

import logging
from datetime import date, datetime, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import get_settings

logger = logging.getLogger("bakery")


@lru_cache
def _zone(name: str) -> tzinfo | None:
    """The zone a setting value names; None means the server's local time."""
    name = name.strip()
    if not name or name.lower() == "local":
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.error("SHOP_TIMEZONE %r is not a time zone this system knows — using UTC", name)
        return timezone.utc


def shop_now() -> datetime:
    """This moment, on the shop's wall clock (timezone-aware)."""
    zone = _zone(get_settings().shop_timezone)
    return datetime.now(zone) if zone is not None else datetime.now().astimezone()


def shop_today() -> date:
    """The shop's calendar date — what "today" means on a delivery date,
    an order number or a catering enquiry."""
    return shop_now().date()


def in_shop_time(moment: datetime) -> datetime:
    """A stored moment (naive UTC, as the database keeps them) as the
    shop's wall clock reads it."""
    aware = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    zone = _zone(get_settings().shop_timezone)
    return aware.astimezone(zone) if zone is not None else aware.astimezone()


def stamp(moment: datetime, fmt: str = "%b %d, %Y, %I:%M %p") -> str:
    """A stored moment written out for a person, in the shop's time — with
    the zone's own short name when it has one ("… 10:14 AM CET"), so a
    time on a receipt is never ambiguous."""
    local = in_shop_time(moment)
    label = local.tzname() or ""
    # Short abbreviations only (CET, CEST, UTC) — some systems hand back a
    # sentence ("W. Europe Standard Time"), which is noise on a receipt.
    return f"{local.strftime(fmt)} {label}".strip() if 0 < len(label) <= 5 else local.strftime(fmt)
