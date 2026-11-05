"""The shop's money text — one seam, so every surface that writes a price in
prose (emails, notifications, timeline notes, checkout errors) speaks the
currency the order was actually placed in. The storefront keeps its own
counterpart in frontend/src/lib/format.js; the two agree on symbols and
thousand separators.
"""

_CURRENCY_SYMBOLS = {"usd": "$", "eur": "€"}


def money(amount: float, currency: str | None = "eur") -> str:
    """The shop's default is euro (the 2026-11-05 EUR-only decision), so
    "€1,234.56" unless the caller names the money's own currency — a dollar
    figure passed with its code still renders "$1,234.56". A missing or empty
    code is also euro: the shop's fallback must never silently print dollars.
    An unknown code falls back to its upper-case self ("gbp 12.00") rather
    than guessing a symbol."""
    code = (currency.strip().lower() or "eur") if isinstance(currency, str) else "eur"
    symbol = _CURRENCY_SYMBOLS.get(code, f"{code.upper()} ")
    return f"{symbol}{amount:,.2f}"
