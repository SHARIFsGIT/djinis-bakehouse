"""The shop's single costing brain — the Phase 2 cost engine.

All money math here is ``decimal.Decimal``; the only rounding is one
half-up cent quantization at the final per-unit total (AC-4's rule:
intermediates like 0.7895 / 1.5789 stay full precision inside the sum).
Floats never touch a cost figure — ``_dec()`` purifies anything numeric
that arrives from outside, exactly as ``app/vat.py`` does.

What this module refuses to do is as much the design as what it does:
it never converts between currencies (a EUR cost against a USD price is
``not_computable``, never a guess — A2), never converts mass to volume
without an explicitly recorded ingredient-specific density (A3), never
applies a global wastage percentage (D6 — every line carries its own
loss, 0 % included), never adds an estimated and an actual batch
overhead for the same batch (A1: exactly one source), and never touches
a selling price (recalculation appends cost records and moves flags
only — AC-38). Incomplete inputs produce *data*, not exceptions: a cost
record with ``total_cost = None`` and machine-readable reasons.

Phase 2 computes and flags. Enforcement (margin floor publication
gates, charm prices, approvals) belongs to Phase 3 and later, behind
their own approvals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from .models import (
    LabourRate,
    OverheadMonth,
    Product,
    ProductCostRecord,
    ProductionBatch,
    Recipe,
    RecipeItem,
)

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
SIXTY = Decimal("60")
QUANT4 = Decimal("0.0001")

# D2: the 40 % margin floor. Phase 2 computes flags against it; Phase 3's
# pricing engine is what enforces it.
MARGIN_FLOOR = Decimal("0.40")
MARGIN_FLOOR_DIVISOR = Decimal("0.60")  # 1 − 0.40 — min_net = cost / 0.60

# D18: the default cost-review interval, in days.
DEFAULT_REVIEW_INTERVAL_DAYS = 90

# D3: the labour-rate fallback, defined exactly once. It applies only
# until the owner records an effective-dated labour_rates row; whichever
# figure a calculation used is snapshotted into its cost record with the
# source label ("default_fallback" or "table") so history never rewrites.
DEFAULT_LABOUR_RATE = Decimal("20.00")
DEFAULT_LABOUR_CURRENCY = "eur"
LABOUR_RATE_SOURCE_TABLE = "table"
LABOUR_RATE_SOURCE_FALLBACK = "default_fallback"

# Cost-record triggers (product_cost_records.trigger).
TRIGGER_INITIAL = "initial"
TRIGGER_INGREDIENT_PRICE_CHANGE = "ingredient_price_change"
TRIGGER_RECIPE_CHANGE = "recipe_change"
TRIGGER_BATCH_CLOSED = "batch_closed"
TRIGGER_OVERHEAD_MONTH_CHANGE = "overhead_month_change"
TRIGGER_LABOUR_RATE_CHANGE = "labour_rate_change"

# Derived cost statuses for the admin surfaces.
COST_STATUS_NOT_COSTED = "not_costed"  # no recipe recorded yet
COST_STATUS_INCOMPLETE = "incomplete"  # recipe, but inputs missing/mixed
COST_STATUS_COSTED = "costed"  # complete, margins fine
COST_STATUS_MARGIN_FLAGGED = "margin_flagged"  # below the 40 % floor (flag only)
COST_STATUS_NOT_COMPUTABLE = "not_computable"  # currency / tax-mode mismatch (A2)

MARGIN_OK = "ok"
MARGIN_FLAGGED = "flagged"
MARGIN_NOT_COMPUTABLE = "not_computable"
MARGIN_NOT_SET = "not_set"


class CostingError(ValueError):
    """A costing input is unusable as asked (the VatError pattern)."""


class UnknownUnitError(CostingError):
    """A unit outside the supported vocabulary."""


class MismatchedUnitsError(CostingError):
    """Units that can never convert into each other (cross dimension, or
    mass↔volume without a recorded ingredient density)."""


# ---------------------------------------------------------------------------
# Units (A3) — the one vocabulary, one conversion table
# ---------------------------------------------------------------------------

MASS = "mass"
VOLUME = "volume"
COUNT = "count"

UNIT_DIMENSIONS = {
    "mg": MASS,
    "g": MASS,
    "kg": MASS,
    "ml": VOLUME,
    "l": VOLUME,
    "each": COUNT,
}

# Spelled-out forms an admin may type; stored canonically.
UNIT_ALIASES = {
    "gram": "g",
    "grams": "g",
    "gr": "g",
    "kilo": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "liter": "l",
    "litre": "l",
    "litres": "l",
    "liters": "l",
    "piece": "each",
    "pcs": "each",
    "item": "each",
    "items": "each",
}

# Within-dimension factors to the dimension's base unit (mg / ml / each).
# Same-dimension conversion is exact Decimal arithmetic — nothing else
# converts without an explicitly recorded density (never a silent guess).
_UNIT_FACTORS = {
    "mg": Decimal("1"),
    "g": Decimal("1000"),
    "kg": Decimal("1000000"),
    "ml": Decimal("1"),
    "l": Decimal("1000"),
    "each": Decimal("1"),
}


def _dec(value) -> Decimal:
    """Decimal without float artifacts: pass through Decimals, stringify
    everything else (Numeric columns, JSON floats, test literals)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_cents(amount) -> Decimal:
    """Explicit cent rounding, half-up — the only rounding costing does,
    and only at the final per-unit total."""
    return _dec(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def canonical_unit(unit) -> str:
    """Lowercase, alias-resolved unit — the form stored everywhere. Raises
    UnknownUnitError for anything outside the vocabulary."""
    key = str(unit or "").strip().lower()
    if key in UNIT_ALIASES:
        key = UNIT_ALIASES[key]
    if key not in UNIT_DIMENSIONS:
        raise UnknownUnitError(
            f"unknown unit {unit!r} — supported: mg, g, kg, ml, l, each "
            "(plus the usual spelled-out forms)"
        )
    return key


def unit_dimension(unit) -> str:
    return UNIT_DIMENSIONS[canonical_unit(unit)]


def convert_qty(qty, from_unit, to_unit, density_g_per_ml=None) -> Decimal:
    """Convert a quantity between units, full precision.

    Same dimension: exact Decimal factor. Mass ↔ volume: only with an
    explicitly recorded, ingredient-specific ``density_g_per_ml`` (A3 —
    never a silent density assumption). Count never converts to anything.
    Anything impossible raises MismatchedUnitsError naming both units.
    """
    source, target = canonical_unit(from_unit), canonical_unit(to_unit)
    amount = _dec(qty)
    if source == target:
        return amount
    source_dim, target_dim = UNIT_DIMENSIONS[source], UNIT_DIMENSIONS[target]
    if source_dim == target_dim:
        return amount * _UNIT_FACTORS[source] / _UNIT_FACTORS[target]
    if {source_dim, target_dim} == {MASS, VOLUME}:
        if density_g_per_ml is None:
            raise MismatchedUnitsError(
                f"cannot convert {source} → {target} without a recorded "
                "ingredient-specific density (density_g_per_ml)"
            )
        density = _dec(density_g_per_ml)
        if density <= 0:
            raise MismatchedUnitsError("density_g_per_ml must be greater than zero")
        # Cross in the density's own units — grams per millilitre — then
        # scale to the target unit. (The within-dimension factors above are
        # relative to mg and ml; the /1000 and *1000 below move between
        # that base and the grams the density speaks in.)
        if source_dim == MASS:
            in_grams = amount * _UNIT_FACTORS[source] / Decimal("1000")
            in_millilitres = in_grams / density
            return in_millilitres / _UNIT_FACTORS[target]
        in_millilitres = amount * _UNIT_FACTORS[source]
        in_grams = in_millilitres * density
        return in_grams * Decimal("1000") / _UNIT_FACTORS[target]
    raise MismatchedUnitsError(
        f"cannot convert {source} → {target}: {source_dim} and {target_dim} "
        "units never convert into each other"
    )


# ---------------------------------------------------------------------------
# Pure cost arithmetic
# ---------------------------------------------------------------------------

def purchase_qty(usable_qty, loss_pct) -> Decimal:
    """The quantity that must actually be purchased so `usable_qty` survives
    preparation loss (§7): usable / (1 − loss). 0 % loss is a valid, explicit
    entry and returns the usable quantity unchanged. Full precision — the
    1052.63 g of AC-4 is this function's output, unrounded."""
    loss = _dec(loss_pct)
    if loss < 0 or loss >= 100:
        raise CostingError(f"loss_pct must be 0–100 (not {loss})")
    usable = _dec(usable_qty)
    return usable * HUNDRED / (HUNDRED - loss)


def effective_rate(purchase_price, purchase_qty_amount) -> Decimal:
    """Derived cost per purchase unit, full precision: price / quantity
    (25 kg @ €37.50 → €1.50/kg — AC-3)."""
    qty = _dec(purchase_qty_amount)
    if qty <= 0:
        raise CostingError("purchase_qty must be greater than zero")
    return _dec(purchase_price) / qty


def labour_cost(minutes, rate=DEFAULT_LABOUR_RATE) -> Decimal:
    """Hands-on minutes / 60 × rate, unrounded (§9: 12 min → 4.00,
    45 → 15.00, 7.5 → 2.50). Unattended time never enters here."""
    return _dec(minutes) / SIXTY * _dec(rate)


def fixed_overhead_rate(fixed_overhead_total, forecast_hours) -> Decimal:
    """€ per expected production hour for the month (§10B: €800 / 160 h →
    €5.00/h — AC-7). Raises on a non-positive forecast rather than guessing."""
    hours = _dec(forecast_hours)
    if hours <= 0:
        raise CostingError("forecast_hours must be greater than zero")
    return _dec(fixed_overhead_total) / hours


def fixed_overhead_for_minutes(minutes, hourly_rate) -> Decimal:
    """The fixed-overhead allocation for a production run's hands-on time
    (§10B: 24 min × €5.00/h → €2.00 — AC-7); divided by the sellable yield
    in the assembly below."""
    return labour_cost(minutes, hourly_rate)  # same arithmetic: hours × rate


def overhead_overlap(month_categories, batch_categories) -> list[str]:
    """Categories present in both overhead ledgers (AC-8) — the double-count
    warning. Inputs are the newline-list Text fields (or iterables); the
    result is a sorted list of shared category names. A warning, never a
    block: the write always lands, the flag always shows."""
    def _as_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return [str(v).strip() for v in value if str(v).strip()]

    month = {c.lower() for c in _as_list(month_categories)}
    batch = {c.lower() for c in _as_list(batch_categories)}
    return sorted(month & batch)


def margin(net, cost) -> Decimal:
    """Gross margin on the net selling price — the only margin definition
    in the system (markup is prohibited vocabulary, §4)."""
    net_dec = _dec(net)
    if net_dec == 0:
        raise CostingError("margin is undefined at a zero net price")
    return (net_dec - _dec(cost)) / net_dec


def min_net_price(cost) -> Decimal:
    """The 40 % margin floor price: cost / 0.60 (§4). Full precision —
    cent-rounding is a presentation concern."""
    return _dec(cost) / MARGIN_FLOOR_DIVISOR


def max_safe_voucher_discount(db, active_currency: str) -> dict | None:
    """The largest discount a voucher may carry without driving any costed
    product below the 40 % margin floor — the marketing desk's brake when
    minting personalised vouchers (the same D2 floor evaluate_margins flags
    shelf prices against; checkout applies one code per basket, so a single
    voucher's bound is the whole combination story).

    The bound comes from the thinnest margin among products whose latest
    cost record is complete and in the shop's active currency: a percent
    voucher takes percent_off off every shelf price it can meet, so the
    worst-margin product sets the ceiling. Returns
    ``{"percent": int, "fixed_amount": Decimal, "costed_products": n}``
    with whole-percent floors (a voucher may not exceed the bound, so the
    percent rounds *down*), or None when no product has usable costing
    data — without costs there is no knowable floor, and the desk refuses
    rather than guess."""
    best_percent: int | None = None
    best_fixed: Decimal | None = None
    costed = 0
    products = db.scalars(select(Product)).all()
    for product in products:
        record = latest_cost_record(db, product.id)
        if record is None or not record.is_complete or record.total_cost is None:
            continue
        if (record.currency or "").lower() != active_currency.lower():
            continue
        costed += 1
        price = _dec(product.current_price)
        floor = min_net_price(record.total_cost)
        safe_frac = (price - floor) / price  # ≤ 0 when already at/below floor
        best_percent = (
            safe_frac
            if best_percent is None
            else min(best_percent, safe_frac)
        )
        headroom = price - floor
        best_fixed = headroom if best_fixed is None else min(best_fixed, headroom)
    if best_percent is None or best_fixed is None:
        return None
    # Floor both bounds — never round past the limit, and a product already
    # at/below the floor means no voucher headroom at all (0, not negative).
    return {
        "percent": max(0, int(best_percent * 100)),
        "fixed_amount": best_fixed if best_fixed > 0 else Decimal("0"),
        "costed_products": costed,
    }


# ---------------------------------------------------------------------------
# Review scheduling (D18)
# ---------------------------------------------------------------------------

def effective_interval_days(ingredient) -> int:
    """The review interval that applies: explicit per-ingredient override →
    category interval → the 90-day default."""
    if ingredient.review_interval_days:
        return int(ingredient.review_interval_days)
    category = ingredient.category
    if category is not None and category.review_interval_days:
        return int(category.review_interval_days)
    return DEFAULT_REVIEW_INTERVAL_DAYS


def next_review_date(last_reviewed, interval_days) -> date:
    """next = last + interval days (§13: 2026-01-01 + 90 → 2026-04-01)."""
    return last_reviewed + timedelta(days=int(interval_days))


def is_overdue(next_reviewed, on_date: date | None = None) -> bool:
    """The backend overdue flag, derived at read (§12): a cost input is
    overdue exactly when now ≥ next_reviewed. No scheduler, no stored state."""
    if next_reviewed is None:
        return False
    return (on_date or date.today()) >= next_reviewed


# ---------------------------------------------------------------------------
# Labour-rate resolution (A5)
# ---------------------------------------------------------------------------

def labour_rate_on(db, on_date: date | None = None):
    """The labour rate in force on a date: the newest labour_rates row whose
    valid_from has arrived, else the D3 default fallback. Returns
    (rate, currency, source, valid_from) — the exact figures to snapshot
    into the cost record, so a later rate change never rewrites history."""
    day = on_date or date.today()
    row = db.scalar(
        select(LabourRate)
        .where(LabourRate.valid_from <= day)
        .order_by(LabourRate.valid_from.desc())
        .limit(1)
    )
    if row is not None:
        return _dec(row.rate), row.currency, LABOUR_RATE_SOURCE_TABLE, row.valid_from
    return DEFAULT_LABOUR_RATE, DEFAULT_LABOUR_CURRENCY, LABOUR_RATE_SOURCE_FALLBACK, None


# ---------------------------------------------------------------------------
# Cost assembly — the one costing path (§22; Phase 6 quotations reuse it)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostResult:
    """One product's computed cost. Incompleteness is data: total_cost is
    None and `missing` carries machine-readable reason codes."""

    is_complete: bool
    currency: str | None
    total_cost: Decimal | None  # per-unit, cent-rounded; None while incomplete
    missing: list[str]
    breakdown: dict[str, Any]  # JSON-safe reconstruction snapshot


def _s(value) -> str | None:
    """Decimal → exact string for the JSON breakdown (never a float)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _incomplete(missing: list[str], breakdown: dict) -> CostResult:
    return CostResult(False, None, None, missing, breakdown)


def latest_closed_batch(db, product_id: int) -> ProductionBatch | None:
    """The A1 actuals selector: the latest *genuinely completed* batch —
    closed, with a positive actual sellable yield and an actual overhead
    figure. Deterministic order: produced_at desc, then id desc. A closed
    batch without valid actuals never qualifies (it is not silently
    selected); with no qualifying batch, costing uses the documented
    recipe estimate instead."""
    return db.scalar(
        select(ProductionBatch)
        .where(
            ProductionBatch.product_id == product_id,
            ProductionBatch.status == "closed",
            ProductionBatch.actual_yield.is_not(None),
            ProductionBatch.actual_yield > 0,
            ProductionBatch.batch_overhead_actual.is_not(None),
        )
        .order_by(ProductionBatch.produced_at.desc(), ProductionBatch.id.desc())
        .limit(1)
    )


def current_overhead_month(db, on_date: date | None = None):
    """The overhead month costing reads: the most recent month that has
    arrived (month ≤ the current month's first day). None before the owner
    records a first month — an honest incomplete, never a guessed rate."""
    current_month = (on_date or date.today()).replace(day=1)
    return db.scalar(
        select(OverheadMonth)
        .where(OverheadMonth.month <= current_month)
        .order_by(OverheadMonth.month.desc())
        .limit(1)
    )


def compute_product_cost(db, product, on_date: date | None = None) -> CostResult:
    """Assemble one product's per-unit cost from the live inputs.

    Composition (§4 + §10, worked in §16): all four components are computed
    for the batch — ingredient lines at purchase quantities (per-line loss),
    labour on hands-on minutes, the batch overhead from exactly one source
    (A1: latest closed batch's actual, else the recipe estimate), and the
    fixed-overhead allocation on the same hands-on hours — then divided by
    the sellable yield (§8: actual of the latest closed batch, else the
    recipe's expected). One half-up cent rounding at the very end.
    """
    today = on_date or date.today()
    recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id))
    if recipe is None:
        return _incomplete(["no_recipe"], {"product_id": product.id, "computed_for": today.isoformat()})
    items = list(
        db.scalars(select(RecipeItem).where(RecipeItem.recipe_id == recipe.id).order_by(RecipeItem.id))
    )
    if not items:
        return _incomplete(
            ["no_recipe_lines"],
            {"product_id": product.id, "recipe_id": recipe.id, "computed_for": today.isoformat()},
        )

    missing: list[str] = []
    currencies: set[str] = set()
    lines_breakdown: list[dict] = []
    ingredients_cost = Decimal("0")

    for item in items:
        ingredient = item.ingredient
        line: dict[str, Any] = {
            "ingredient_id": ingredient.id if ingredient else item.ingredient_id,
            "name": ingredient.name if ingredient else f"ingredient {item.ingredient_id}",
            "usable_qty": _s(item.usable_qty),
            "unit": item.unit,
            "loss_pct": _s(item.loss_pct),
        }
        if ingredient is None:
            missing.append(f"ingredient_missing:{line['name']}")
            line["line_cost"] = None
            lines_breakdown.append(line)
            continue
        currencies.add(ingredient.currency)
        if ingredient.effective_cost_per_unit is None:
            missing.append(f"ingredient_missing_price:{ingredient.name}")
            line.update({"line_cost": None, "reason": "no effective cost per unit recorded"})
            lines_breakdown.append(line)
            continue
        try:
            purchased = purchase_qty(item.usable_qty, item.loss_pct)
            in_purchase_unit = convert_qty(
                purchased, item.unit, ingredient.unit, ingredient.density_g_per_ml
            )
            line_cost = in_purchase_unit * _dec(ingredient.effective_cost_per_unit)
        except CostingError as reason:  # UnknownUnitError/MismatchedUnitsError included
            missing.append(f"unit_mismatch:{ingredient.name}")
            line.update(
                {
                    "ingredient_unit": ingredient.unit,
                    "effective_cost_per_unit": _s(ingredient.effective_cost_per_unit),
                    "line_cost": None,
                    "reason": str(reason),
                }
            )
            lines_breakdown.append(line)
            continue
        ingredients_cost += line_cost
        line.update(
            {
                "purchase_qty": _s(purchased),
                "qty_in_purchase_unit": _s(in_purchase_unit),
                "ingredient_unit": ingredient.unit,
                "effective_cost_per_unit": _s(ingredient.effective_cost_per_unit),
                "currency": ingredient.currency,
                "line_cost": _s(line_cost),
            }
        )
        lines_breakdown.append(line)

    # Labour (A5): resolve the effective rate and snapshot it wholesale.
    rate, labour_currency, rate_source, rate_valid_from = labour_rate_on(db, today)
    currencies.add(labour_currency)
    labour = labour_cost(recipe.hands_on_minutes, rate)
    labour_breakdown = {
        "minutes": _s(recipe.hands_on_minutes),
        "unattended_minutes": _s(recipe.unattended_minutes),
        "rate": _s(rate),
        "currency": labour_currency,
        "rate_source": rate_source,
        "valid_from": rate_valid_from.isoformat() if rate_valid_from else None,
        "cost": _s(labour),
    }

    # Yield + batch overhead — the A1 rule: exactly one source, never both.
    batch = latest_closed_batch(db, product.id)
    if batch is not None:
        yield_units: int | None = batch.actual_yield
        batch_overhead = _dec(batch.batch_overhead_actual)
        overhead_breakdown = {
            "mode": "actual",
            "amount": _s(batch_overhead),
            "batch_id": batch.id,
            "produced_at": batch.produced_at.isoformat(),
        }
        yield_breakdown = {
            "units": yield_units,
            "source": "actual",
            "batch_id": batch.id,
            "expected_yield": batch.expected_yield,
        }
    else:
        yield_units = recipe.expected_batch_yield
        batch_overhead = _dec(recipe.batch_overhead_estimate)
        overhead_breakdown = {"mode": "estimated", "amount": _s(batch_overhead), "source": "recipe_estimate"}
        yield_breakdown = {"units": yield_units, "source": "expected", "batch_id": None}

    # Fixed monthly overhead (§10B), via the current month's forecast hours.
    month = current_overhead_month(db, today)
    fixed_breakdown: dict[str, Any] = {"month": None, "allocation": None}
    if month is None:
        missing.append("no_overhead_month")
    else:
        currencies.add(month.currency)
        try:
            hourly = fixed_overhead_rate(month.fixed_overhead_total, month.forecast_hours)
        except CostingError:
            missing.append("invalid_forecast_hours")
        else:
            allocation = fixed_overhead_for_minutes(recipe.hands_on_minutes, hourly)
            fixed_breakdown = {
                "month": month.month.isoformat(),
                "total": _s(month.fixed_overhead_total),
                "forecast_hours": _s(month.forecast_hours),
                "rate_per_hour": _s(hourly),
                "minutes": _s(recipe.hands_on_minutes),
                "allocation": _s(allocation),
                "currency": month.currency,
            }

    # Currency coherence (A2): the inputs must agree on one currency; a
    # mix is an honest incomplete, never a converted guess.
    currency: str | None = None
    if len(currencies) > 1:
        missing.append("mixed_currencies")
        overhead_breakdown.setdefault("currencies", sorted(currencies))
    elif currencies:
        currency = next(iter(currencies))

    if yield_units is None or yield_units <= 0:
        missing.append("invalid_yield")
        yield_units = None

    breakdown: dict[str, Any] = {
        "product_id": product.id,
        "computed_for": today.isoformat(),
        "lines": lines_breakdown,
        # The partial sum over the computable lines; the per-line reasons
        # in `missing` say which lines dropped out.
        "ingredients_cost": _s(ingredients_cost),
        "labour": labour_breakdown,
        "batch_overhead": overhead_breakdown,
        "fixed_overhead": fixed_breakdown,
        "yield": yield_breakdown,
        "currency": currency,
    }

    if missing:
        breakdown["missing"] = missing
        return _incomplete(missing, breakdown)

    batch_total = ingredients_cost + labour + batch_overhead + _dec(fixed_breakdown["allocation"])
    per_unit = batch_total / Decimal(yield_units)
    total = round_cents(per_unit)
    breakdown.update(
        {
            "batch_total": _s(batch_total),
            "per_unit_unrounded": _s(per_unit),
            "total_cost": _s(total),
        }
    )
    return CostResult(True, currency, total, [], breakdown)


# ---------------------------------------------------------------------------
# Cost records — append-only history
# ---------------------------------------------------------------------------

def latest_cost_record(db, product_id: int) -> ProductCostRecord | None:
    """The product's current cost: its newest computation."""
    return db.scalar(
        select(ProductCostRecord)
        .where(ProductCostRecord.product_id == product_id)
        .order_by(ProductCostRecord.computed_at.desc(), ProductCostRecord.id.desc())
        .limit(1)
    )


def refresh_cost(db, product, trigger: str) -> ProductCostRecord:
    """Compute now and append a cost record. Never raises for incomplete
    inputs — an incomplete record is the honest result — and never touches
    the product's prices (AC-38). The caller owns the transaction."""
    result = compute_product_cost(db, product)
    record = ProductCostRecord(
        product_id=product.id,
        trigger=trigger,
        currency=result.currency,
        total_cost=float(result.total_cost) if result.total_cost is not None else None,
        is_complete=result.is_complete,
        missing=result.missing,
        breakdown=result.breakdown,
    )
    db.add(record)
    return record


def recompute_dependent_costs(db, ingredient, trigger: str = TRIGGER_INGREDIENT_PRICE_CHANGE) -> int:
    """Append a fresh cost record for every product whose recipe uses this
    ingredient — inside the caller's transaction, regardless of review dates
    (D18/AC-37). One bad recipe yields an incomplete record, never an error
    that would make an ingredient price unsaveable."""
    products = list(
        db.scalars(
            select(Product)
            .join(Recipe, Recipe.product_id == Product.id)
            .join(RecipeItem, RecipeItem.recipe_id == Recipe.id)
            .where(RecipeItem.ingredient_id == ingredient.id)
        )
    )
    for product in products:
        refresh_cost(db, product, trigger)
    return len(products)


def recompute_all_costed_products(db, trigger: str) -> int:
    """Refresh every product that has a recipe (overhead-month or
    labour-rate changes affect them all)."""
    products = list(db.scalars(select(Product).join(Recipe, Recipe.product_id == Product.id)))
    for product in products:
        refresh_cost(db, product, trigger)
    return len(products)


# ---------------------------------------------------------------------------
# Margins and computability (A2) — flags only in Phase 2
# ---------------------------------------------------------------------------

def _margin_not_computable(code: str, **extra) -> dict:
    return {"status": MARGIN_NOT_COMPUTABLE, "reason": {"code": code, **extra}}


def evaluate_margins(product, cost_record, active_currency, tax_mode="exclusive") -> dict:
    """Margin flags for the regular and (where set) promotional price,
    against a complete cost record. Pure and derived-at-read.

    A cost in one currency is never compared with a price in another
    (A2): the result is ``not_computable`` with a machine-readable
    ``currency_mismatch`` reason carrying both codes — never a converted
    number. VAT-inclusive netting needs the Phase 4 engine, so an
    inclusive tax mode is equally honest about what it cannot compute.
    """
    if tax_mode != "exclusive":
        # Shelf prices would be gross; netting them needs the VAT engine
        # (Phase 4). Refuse the comparison rather than guess a net.
        return {kind: _margin_not_computable("tax_mode_vat_inclusive") for kind in ("regular", "sale")}
    if cost_record is None or not cost_record.is_complete or cost_record.total_cost is None:
        return {kind: _margin_not_computable("incomplete_cost") for kind in ("regular", "sale")}
    cost_currency = (cost_record.currency or "").lower()
    price_currency = str(active_currency or "").lower()
    if not cost_currency or cost_currency != price_currency:
        return {
            kind: _margin_not_computable(
                "currency_mismatch",
                cost_currency=cost_currency or None,
                price_currency=price_currency or None,
            )
            for kind in ("regular", "sale")
        }

    cost = _dec(cost_record.total_cost)
    floor = min_net_price(cost)
    evaluated: dict = {}
    for kind, price in (("regular", product.price), ("sale", product.sale_price)):
        if price is None:
            evaluated[kind] = {"status": MARGIN_NOT_SET}
            continue
        net = _dec(price)
        if net <= 0:
            evaluated[kind] = _margin_not_computable("invalid_price")
            continue
        evaluated[kind] = {
            "status": MARGIN_FLAGGED if net < floor else MARGIN_OK,
            "margin": float(margin(net, cost).quantize(QUANT4, rounding=ROUND_HALF_UP)),
            "min_net": float(round_cents(floor)),
            "net_price": float(net),
        }
    return evaluated


# ---------------------------------------------------------------------------
# Admin-surface derivation
# ---------------------------------------------------------------------------

def recipe_inputs_overdue(db, recipe: Recipe | None, on_date: date | None = None) -> bool:
    """True when the recipe's own review or any ingredient it uses is past
    its next review date (the §12 derived flag — reminders render it)."""
    if recipe is None:
        return False
    today = on_date or date.today()
    if is_overdue(recipe.next_reviewed, today):
        return True
    for item in recipe.items:
        ingredient = item.ingredient
        if ingredient is not None and is_overdue(ingredient.next_reviewed, today):
            return True
    return False


def product_cost_summary(db, product, active_currency=None, tax_mode="exclusive"):
    """(cost_status, summary dict) for the admin surfaces — derived at read,
    never stored on the product. ``active_currency``/``tax_mode`` come from
    settings at the call site (the shop's selling currency today)."""
    recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id))
    record = latest_cost_record(db, product.id)
    margins = evaluate_margins(
        product,
        record if record is not None and record.is_complete else None,
        active_currency,
        tax_mode,
    )
    overdue = recipe_inputs_overdue(db, recipe)
    if recipe is None:
        cost_status = COST_STATUS_NOT_COSTED
    elif record is None or not record.is_complete:
        cost_status = COST_STATUS_INCOMPLETE
    elif any(m["status"] == MARGIN_NOT_COMPUTABLE for m in margins.values()):
        cost_status = COST_STATUS_NOT_COMPUTABLE
    elif any(m["status"] == MARGIN_FLAGGED for m in margins.values()):
        cost_status = COST_STATUS_MARGIN_FLAGGED
    else:
        cost_status = COST_STATUS_COSTED
    summary = {
        "total_cost": float(record.total_cost) if record is not None and record.total_cost is not None else None,
        "currency": record.currency if record is not None else None,
        "computed_at": record.computed_at if record is not None else None,
        "is_complete": bool(record.is_complete) if record is not None else False,
        "missing": list(record.missing or []) if record is not None else [],
        "margins": margins,
        "inputs_overdue": overdue,
    }
    return cost_status, summary


def products_needing_cost_review(db) -> set[int]:
    """Product ids for the desk's cost-review rail: has a recipe and either
    no cost record yet, an incomplete latest record, or overdue inputs.
    Products without recipes are not on the rail — costing them hasn't been
    started, which is a state, not a review need."""
    needing: set[int] = set()
    for recipe in db.scalars(select(Recipe)):
        record = latest_cost_record(db, recipe.product_id)
        if record is None or not record.is_complete or recipe_inputs_overdue(db, recipe):
            needing.add(recipe.product_id)
    return needing
