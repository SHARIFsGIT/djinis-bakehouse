"""Phone numbers as a way to sign in.

A customer types their number the way they say it — "0176 555 0100" at home,
"+49 176 555 0100" abroad, "0049…" off an old business card — and all of
those are one number. Two rules keep that true without rewriting anything
already stored:

* `canonical` is the one spelling a number is *stored* under from now on:
  a "+", the country code, the rest ("+491765550100"). It is also the only
  spelling a text message can be sent to.
* `spellings` is every form the same number may *already* be stored under —
  accounts opened before this module kept whatever was typed — so a lookup
  finds the account whichever way either side wrote it, and a uniqueness
  check cannot be walked around by retyping the number differently.

No carrier database, no "is this a real number" verdict: the shop's own
country code (PHONE_DEFAULT_COUNTRY_CODE) is assumed for a number written
the national way, and a number from anywhere else needs its "+".
"""

import re

from .config import get_settings

# Where a text code may go: a German mobile number in E.164 — +49, then 15x,
# 16x or 17x. Germany is the one destination switched on in the shop's Bird
# Verify workspace, and a landline can't take a text: a code sent there
# would be billed and never read.
_SMS_NUMBER = re.compile(r"\+491[5-7]\d{8,9}")


def _digits(value: str) -> tuple[bool, str]:
    cleaned = (value or "").strip()
    return cleaned.startswith("+"), "".join(ch for ch in cleaned if ch.isdigit())


def canonical(value: str, country_code: str | None = None) -> str:
    """`value` as it is stored: "+<country><number>" whenever that can be
    told ("+49…", "0049…", or the national "0176…"); otherwise the bare
    digits, exactly as normalize_phone has always kept them.

    The national trunk 0 written behind the shop's own country code —
    "+49 (0)176 …", "+49 0176 …" — is dropped: it is never dialled from
    abroad, and kept, it makes a number nothing can be texted to (E.164 has
    no "+490…")."""
    cc = country_code or get_settings().phone_default_country_code
    plus, digits = _digits(value)
    if not digits:
        return ""
    if plus:
        number = "+" + digits
    elif digits.startswith("00") and len(digits) > 4:
        number = "+" + digits[2:]
    elif digits.startswith("0") and cc:
        number = "+" + cc + digits.lstrip("0")
    else:
        return digits
    if cc and number.startswith("+" + cc + "0"):
        number = "+" + cc + number[len(cc) + 1 :].lstrip("0")
    return number


def spellings(value: str, country_code: str | None = None) -> list[str]:
    """Every way the number in `value` may already be on file."""
    cc = country_code or get_settings().phone_default_country_code
    plus, digits = _digits(value)
    if not digits:
        return []
    found = {("+" if plus else "") + digits, canonical(value, cc)}
    international = canonical(value, cc)
    if not international.startswith("+"):
        # Bare digits with no hint: they may be the whole international
        # number minus its "+", or a national number minus its "0".
        found.add("+" + digits)
        if cc:
            found.add("+" + cc + digits)
            found.add("0" + digits)
            if digits.startswith(cc):
                found.add("0" + digits[len(cc):])
    else:
        bare = international[1:]
        found.add(bare)
        found.add("00" + bare)
        if cc and bare.startswith(cc):
            national = bare[len(cc):]
            found.add("0" + national)
            found.add(national)
            # How canonical() stored "+49 (0)176 …" before the trunk 0 was
            # dropped (2026-11-23): such an account is still found.
            found.add("+" + cc + "0" + national)
    return sorted(s for s in found if s.strip("+"))


def sms_number(value: str | None) -> str | None:
    """The number in `value` as a text code would be sent to it — E.164, a
    German mobile — or None when no text should go there (a landline, a
    number from abroad, bare digits that don't say their country)."""
    if not value:
        return None
    number = canonical(value)
    return number if _SMS_NUMBER.fullmatch(number) else None


def masked(number: str | None) -> str:
    """A number as a log line may show it: the country and network prefix
    and the last two digits ("+49176*****00") — never the whole of it."""
    text = str(number or "")
    if len(text) < 9:
        return "*" * len(text)
    return text[:6] + "*" * (len(text) - 8) + text[-2:]
