"""Where the shop delivers.

A bakery's van has a range. The baker lists the postal codes it reaches in
Settings — exact codes ("10115") or whole areas ("101*") — and checkout
refuses an address outside them before an order is placed or a payment
taken, instead of leaving the desk to cancel and refund it afterwards.

An empty list means no limit at all, which is how the shop behaved before
this existed: nothing changes until the baker writes a list.
"""

import re

# One entry: a postal code as people write them (letters, digits, a hyphen),
# optionally ending in * for "everything that starts like this".
_ENTRY = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,11}\*?$")
MAX_ENTRIES = 2000
# No country writes an all-digit postal code longer than this (seven, in
# Japan and Israel) — a longer run of digits typed with spaces is a list.
_LONGEST_NUMERIC_CODE = 7


def normalize_code(value) -> str:
    """A postal code as it is compared: no spaces, upper case — "sw1a 1aa"
    and "SW1A1AA" are the same doorstep."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def parse_area(raw: str | None) -> list[str]:
    """The baker's list as entries — separated by commas, semicolons or new
    lines, duplicates dropped, order kept.

    Spaces are read the way they are meant. "10115 10117" is two codes and
    "101* 102*" two areas — more digits than any one postal code has, or an
    asterisk before the end, can only be a list — while "12 043",
    "SW1A 1AA" and "1011 AB" are each one code written with its own space."""
    entries: list[str] = []
    for piece in re.split(r"[,;\n]+", raw or ""):
        tokens = piece.split()
        several = len(tokens) > 1 and (
            any(token.endswith("*") for token in tokens[:-1])
            or (
                all(re.fullmatch(r"\d+\*?", token) for token in tokens)
                and sum(len(token.rstrip("*")) for token in tokens) > _LONGEST_NUMERIC_CODE
            )
        )
        for part in tokens if several else [piece]:
            entry = normalize_code(part)
            if entry and entry not in entries:
                entries.append(entry)
    return entries


def invalid_entries(entries: list[str]) -> list[str]:
    """Entries that are not a postal code or an area — named back to the
    baker rather than stored and silently never matching."""
    return [entry for entry in entries if not _ENTRY.fullmatch(entry)]


def delivers_to(postal_code, entries: list[str]) -> bool:
    """Whether an address with this postal code is inside the area. With no
    entries there is no limit."""
    if not entries:
        return True
    code = normalize_code(postal_code)
    if not code:
        return False
    return any(
        code.startswith(entry[:-1]) if entry.endswith("*") else code == entry
        for entry in entries
    )
