"""The shop's single VAT brain — the euro migration's tax engine.

All money math here is ``decimal.Decimal``, rounded half-up to the cent;
floats never touch a tax figure. The module cannot classify a product by
itself: it turns an *admin-made* classification into a rate and refuses to
guess when the classification is missing, unapproved, or parked for the
tax adviser. Checkout keeps its legacy tax-exclusive arithmetic until the
inclusive cutover phase wires this engine in — nothing here is called by
a router yet.

The statutory percentages live in the ``vat_rates`` table (seeded at boot,
effective-dated), never in code. Which product may use which rate stays
provisional until a German tax adviser confirms each one.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from .models import VatRate

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

# Anlage 2 nr. 35: a milk-mixed drink needs at least 75 % milk in the
# finished beverage for the reduced rate. The percentage must measure
# cow's milk — plant-based substitutes never count toward it.
MILK_CONTENT_THRESHOLD = Decimal("75.00")


class VatCategory(str, enum.Enum):
    """Provisional classification of what a product *is* for VAT."""

    FOOD_REDUCED = "FOOD_REDUCED"  # bakery food via Anlage 2 — 7 % once cleared
    BEVERAGE_STANDARD = "BEVERAGE_STANDARD"  # drinks incl. juices — 19 %
    MILK_DRINK = "MILK_DRINK"  # milk & milk-mixed drinks — rate rides on milk content
    SMOOTHIE_REVIEW = "SMOOTHIE_REVIEW"  # parked for the tax adviser
    SPECIAL_REVIEW = "SPECIAL_REVIEW"  # parked (e.g. chocolate-covered fine bakes)


# Rate kinds the vat_rates table stores rows for. MILK_DRINK is not here:
# it resolves to either kind depending on the drink's milk content.
RATE_REDUCED = "REDUCED"
RATE_STANDARD = "STANDARD"

_DIRECT_RATE_KINDS = {
    VatCategory.FOOD_REDUCED: RATE_REDUCED,
    VatCategory.BEVERAGE_STANDARD: RATE_STANDARD,
}

# Categories that never resolve to a rate on their own — they exist so an
# uncertain product has somewhere to sit until the adviser decides.
REVIEW_CATEGORIES = frozenset({VatCategory.SMOOTHIE_REVIEW, VatCategory.SPECIAL_REVIEW})

REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_BLOCKED = "blocked"
REVIEW_STATUSES = frozenset(
    {REVIEW_STATUS_PENDING, REVIEW_STATUS_APPROVED, REVIEW_STATUS_BLOCKED}
)


class VatError(ValueError):
    """A product's VAT treatment is not usable as asked."""


class UnclassifiedProductError(VatError):
    """No usable classification: category missing, parked for review, or a
    MILK_DRINK without its cow's-milk percentage."""


class UnapprovedProductError(VatError):
    """Classified, but the approved sign-off is missing or the product is
    blocked — nothing goes on a VAT-inclusive price list without it."""


class InvalidVatCategoryError(VatError):
    """A category string outside the provisional set."""


class VatRateNotConfiguredError(VatError):
    """No effective-dated rate row covers the date asked about."""


def _dec(value) -> Decimal:
    """Decimal without float artifacts: pass through Decimals, stringify
    everything else (Numeric columns, JSON floats, test literals)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_cents(amount) -> Decimal:
    """Explicit cent rounding, half-up — the only rounding the engine does."""
    return _dec(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def rate_kind_for(category, milk_content_percent=None) -> str:
    """Classification rules, in one pure place: which rate *kind* a product's
    category entitles it to. Raises rather than guesses — an unclassified or
    review-parked product, or a milk drink without its cow's-milk percentage,
    never silently lands on a rate."""
    if category is None:
        raise UnclassifiedProductError("no VAT category is set")
    if not isinstance(category, VatCategory):
        try:
            category = VatCategory(category)
        except ValueError:
            raise InvalidVatCategoryError(f"unknown VAT category {category!r}") from None
    if category in REVIEW_CATEGORIES:
        raise UnclassifiedProductError(
            f"{category.value} is parked for tax-adviser review — it has no rate yet"
        )
    if category == VatCategory.MILK_DRINK:
        if milk_content_percent is None:
            raise UnclassifiedProductError(
                "a MILK_DRINK needs its cow's-milk percentage before it can be priced"
            )
        milk = _dec(milk_content_percent)
        return RATE_REDUCED if milk >= MILK_CONTENT_THRESHOLD else RATE_STANDARD
    return _DIRECT_RATE_KINDS[category]


def effective_rate(rate_kind: str, on_date: date, db) -> Decimal:
    """The percentage in force for a rate kind on a date — the newest row
    whose valid_from has arrived. Effective-dated, so a rate change is a new
    row and history never moves under an old order."""
    row = db.scalar(
        select(VatRate)
        .where(VatRate.code == rate_kind, VatRate.valid_from <= on_date)
        .order_by(VatRate.valid_from.desc())
        .limit(1)
    )
    if row is None:
        raise VatRateNotConfiguredError(
            f"no {rate_kind} VAT rate is in force on {on_date.isoformat()}"
        )
    return _dec(row.rate)


def resolve_product_rate(product, db, on_date: date | None = None) -> Decimal:
    """The full chain for one product: classification → approval → effective
    percentage. The gate VAT-inclusive checkout will price through; every
    failure is an explicit refusal, never a fallback rate."""
    name = getattr(product, "name", type(product).__name__)
    review_status = getattr(product, "vat_review_status", None)
    if review_status != REVIEW_STATUS_APPROVED:
        raise UnapprovedProductError(
            f"{name} is not approved for VAT-inclusive pricing "
            f"(review status: {review_status or 'unknown'})"
        )
    kind = rate_kind_for(
        getattr(product, "vat_category", None),
        getattr(product, "milk_content_percent", None),
    )
    return effective_rate(kind, on_date or date.today(), db)


def validate_classification(
    vat_category: str | None,
    vat_review_status: str,
    milk_content_percent=None,
) -> None:
    """Coherence rules for a classification write, in one pure place — the
    admin desk and any future importer refuse the same nonsense. Raises
    ValueError with a plain reason; the API layer wraps it in a 400."""
    if vat_review_status not in REVIEW_STATUSES:
        raise ValueError(
            f"review status must be pending, approved or blocked — not {vat_review_status!r}"
        )
    if vat_category is not None:
        try:
            VatCategory(vat_category)
        except ValueError:
            known = ", ".join(category.value for category in VatCategory)
            raise ValueError(
                f"unknown VAT category {vat_category!r} — use one of: {known}"
            ) from None
    if vat_category == VatCategory.MILK_DRINK.value and milk_content_percent is None:
        raise ValueError(
            "a MILK_DRINK needs its cow's-milk percentage (0–100) before it can be saved"
        )
    if milk_content_percent is not None and vat_category != VatCategory.MILK_DRINK.value:
        raise ValueError("a milk percentage only belongs to a MILK_DRINK")
    if vat_review_status == REVIEW_STATUS_APPROVED:
        if vat_category is None:
            raise ValueError(
                "assign a VAT category first — an unclassified product has no rate to approve"
            )
        if vat_category in {category.value for category in REVIEW_CATEGORIES}:
            raise ValueError(
                f"{vat_category} is a parking category for the tax adviser — move the "
                "product to a final category before approving"
            )


def preview_rate(vat_category, milk_content_percent, db, on_date: date | None = None):
    """The rate the current configuration implies for a classification — a
    desk preview that deliberately ignores the approval gate. None when the
    classification implies no rate. Provisional by design: it is the rate
    table talking, never a legal determination."""
    try:
        kind = rate_kind_for(vat_category, milk_content_percent)
        return effective_rate(kind, on_date or date.today(), db)
    except VatError:
        return None


def extract_vat(gross, rate) -> tuple[Decimal, Decimal]:
    """VAT-inclusive: split a gross amount into (net, vat), cent-rounded
    half-up. vat is the remainder, so net + vat always reassembles gross
    exactly."""
    gross_dec, rate_dec = _dec(gross), _dec(rate)
    net = round_cents(gross_dec * HUNDRED / (HUNDRED + rate_dec))
    return net, gross_dec - net


def add_vat(net, rate) -> tuple[Decimal, Decimal]:
    """VAT-exclusive: add tax on top of a net amount — the interface the
    legacy checkout arithmetic mirrors. Returns (gross, vat)."""
    net_dec, rate_dec = _dec(net), _dec(rate)
    vat = round_cents(net_dec * rate_dec / HUNDRED)
    return net_dec + vat, vat


@dataclass(frozen=True)
class VatLine:
    """One order line's frozen tax figures — what the OrderItem snapshot
    columns will hold, computed once at checkout and never recomputed."""

    vat_category: str
    vat_rate: Decimal
    gross_unit_price: Decimal
    net_unit_price: Decimal
    gross_line_total: Decimal
    net_line_total: Decimal
    vat_amount: Decimal


def price_line_inclusive(product, quantity: int, db, on_date: date | None = None) -> VatLine:
    """Price one order line from a gross shelf price: resolve the product's
    rate, then extract net/VAT per unit and per line. Unit and line figures
    are rounded independently — the line is the authoritative one (net_line
    + vat_amount == gross_line_total to the cent)."""
    rate = resolve_product_rate(product, db, on_date)
    gross_unit = round_cents(getattr(product, "current_price"))
    gross_line = round_cents(gross_unit * quantity)
    net_unit, _ = extract_vat(gross_unit, rate)
    net_line, vat = extract_vat(gross_line, rate)
    return VatLine(
        vat_category=getattr(product, "vat_category"),
        vat_rate=rate,
        gross_unit_price=gross_unit,
        net_unit_price=net_unit,
        gross_line_total=gross_line,
        net_line_total=net_line,
        vat_amount=vat,
    )


@dataclass(frozen=True)
class RateSummary:
    """Per-rate totals for one order — the shape of an OrderTaxLine row."""

    vat_rate: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


def summarize_by_rate(lines) -> list[RateSummary]:
    """Group priced lines per distinct rate, summing already-rounded line
    values — group sums are never re-rounded, so an order's VAT always
    reconciles with its lines to the cent."""
    groups: dict[Decimal, list[Decimal]] = {}
    for line in lines:
        bucket = groups.setdefault(line.vat_rate, [Decimal("0.00")] * 3)
        bucket[0] += line.net_line_total
        bucket[1] += line.vat_amount
        bucket[2] += line.gross_line_total
    return [
        RateSummary(vat_rate=rate, net_amount=net, vat_amount=vat, gross_amount=gross)
        for rate, (net, vat, gross) in sorted(groups.items())
    ]
