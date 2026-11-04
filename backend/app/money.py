"""The shop's money text — one seam, so every surface that writes a price in
prose (emails, notifications, timeline notes, checkout errors) speaks the
currency the order was actually placed in. The storefront keeps its own
counterpart in frontend/src/lib/format.js; the two agree on symbols and
thousand separators.
"""

_CURRENCY_SYMBOLS = {"usd": "$", "eur": "€"}


def money(amount: float, currency: str = "usd") -> str:
    """"$1,234.56" for a dollar order, "€1,234.56" for a euro one. An unknown
    code falls back to its upper-case self ("gbp 12.00") rather than guessing
    a symbol."""
    code = (currency or "usd").lower()
    symbol = _CURRENCY_SYMBOLS.get(code, f"{code.upper()} ")
    return f"{symbol}{amount:,.2f}"
