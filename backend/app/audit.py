"""The desk's audit trail: one line per sensitive change made at the baker's
desk — which admin, which account, what changed, when.

It rides on the shop's own logging (the "bakery" loggers the host already
keeps), so there is no table, no migration and no service behind it. Find
the lines by searching the logs for "audit action=".

A line names accounts by id only and carries the setting's own before and
after — a role, an on/off, a classification code. Never a password, token,
link, name, address, phone number, consent source or evidence: nothing a
person typed reaches this log.
"""

import logging
import re
from datetime import timezone

from .database import utcnow

logger = logging.getLogger("bakery.audit")

# Whatever is written as a before/after value is a short code; anything
# else is squashed to one, so a stray value can never forge a second line.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _value(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return _UNSAFE.sub("_", str(getattr(value, "value", value)))[:40] or "none"


def record(action: str, *, admin_id: int, target_id: int, before=None, after=None) -> None:
    """Write one audit line, e.g.
    audit action=user.role_changed admin_id=1 target_id=42 before=customer after=admin at=2026-11-25T01:02:03+00:00
    """
    at = utcnow().replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "audit action=%s admin_id=%d target_id=%d before=%s after=%s at=%s",
        _value(action),
        admin_id,
        target_id,
        _value(before),
        _value(after),
        at,
    )
