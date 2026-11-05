"""Phase 2 costing endpoints — the admin desk's costing surfaces.

Everything here is admin-only behind the shop's existing auth
(``CurrentAdmin``; no new mechanism), additive to the schema, and
API-only: no frontend consumes these yet. The rules that matter:

* Money writes are append-only — price events and cost records record
  history; nothing rewrites it. A cost recalculation appends a new
  record and never touches the product's selling prices (AC-38).
* Recalculation rides the caller's transaction: a price change and the
  cost records it triggers land together or not at all.
* Warnings are never blocks — an overhead overlap flags double-counting
  risk (AC-8) but the write always succeeds.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from .. import costing
from ..config import get_settings
from ..costing import (
    DEFAULT_REVIEW_INTERVAL_DAYS,
    canonical_unit,
    convert_qty,
    effective_interval_days,
    effective_rate,
    is_overdue,
    labour_rate_on,
    next_review_date,
    overhead_overlap,
)
from ..database import utcnow
from ..models import (
    Ingredient,
    IngredientCategory,
    IngredientPriceEvent,
    LabourRate,
    OverheadMonth,
    Product,
    ProductionBatch,
    Recipe,
    RecipeItem,
)
from ..schemas import (
    CostReviewEntryOut,
    CostReviewOut,
    IngredientCategoryCreate,
    IngredientCategoryOut,
    IngredientCategoryUpdate,
    IngredientCreate,
    IngredientListResponse,
    IngredientOut,
    IngredientPriceEventOut,
    IngredientReviewRequest,
    IngredientUpdate,
    LabourRateSetRequest,
    LabourRateStatusOut,
    OverheadMonthCreate,
    OverheadMonthOut,
    OverheadMonthSaveOut,
    OverheadMonthUpdate,
    PaginationOut,
    ProductionBatchClose,
    ProductionBatchCreate,
    ProductionBatchOut,
    ProductionBatchUpdate,
    ProductCostSummaryOut,
    RecipeItemOut,
    RecipeOut,
    RecipeUpsertRequest,
)
from ..security import CurrentAdmin, DbSession

router = APIRouter(prefix="/api/admin/costing", tags=["costing"])

# The column scale of effective_cost_per_unit — writes are quantized to it
# so SQLite and PostgreSQL store byte-identical figures.
_UNIT_COST_QUANT = Decimal("0.000001")


def _get_or_404(db, model, id_: int, label: str):
    row = db.get(model, id_)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _quantize_unit_cost(value: Decimal) -> float:
    return float(value.quantize(_UNIT_COST_QUANT))


# ---------------------------------------------------------------------------
# Ingredient categories
# ---------------------------------------------------------------------------

def _category_out(category: IngredientCategory, db: DbSession) -> IngredientCategoryOut:
    count = db.scalar(
        select(func.count()).select_from(Ingredient).where(Ingredient.category_id == category.id)
    )
    return IngredientCategoryOut(
        id=category.id,
        name=category.name,
        review_interval_days=category.review_interval_days,
        ingredient_count=count or 0,
        created_at=category.created_at,
    )


@router.get("/ingredient-categories", response_model=list[IngredientCategoryOut])
def list_categories(admin: CurrentAdmin, db: DbSession):
    return [
        _category_out(c, db)
        for c in db.scalars(select(IngredientCategory).order_by(IngredientCategory.name))
    ]


@router.post(
    "/ingredient-categories",
    response_model=IngredientCategoryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_category(payload: IngredientCategoryCreate, admin: CurrentAdmin, db: DbSession):
    name = payload.name.strip()
    dup = db.scalar(select(IngredientCategory).where(func.lower(IngredientCategory.name) == name.lower()))
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"Ingredient category '{name}' already exists")
    category = IngredientCategory(name=name, review_interval_days=payload.review_interval_days)
    db.add(category)
    db.commit()
    db.refresh(category)
    return _category_out(category, db)


@router.put("/ingredient-categories/{category_id}", response_model=IngredientCategoryOut)
def update_category(
    category_id: int, payload: IngredientCategoryUpdate, admin: CurrentAdmin, db: DbSession
):
    category = _get_or_404(db, IngredientCategory, category_id, "Ingredient category")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = data["name"].strip()
        dup = db.scalar(
            select(IngredientCategory).where(
                func.lower(IngredientCategory.name) == name.lower(),
                IngredientCategory.id != category.id,
            )
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"Ingredient category '{name}' already exists")
        category.name = name
    if "review_interval_days" in data and data["review_interval_days"] is not None:
        category.review_interval_days = data["review_interval_days"]
        # Members without their own override inherit the new interval; their
        # next review moves accordingly. Cost figures don't change, so no
        # cost recalculation is triggered by an interval change.
        for ingredient in db.scalars(
            select(Ingredient).where(
                Ingredient.category_id == category.id,
                Ingredient.review_interval_days.is_(None),
                Ingredient.last_reviewed.is_not(None),
            )
        ):
            ingredient.next_reviewed = next_review_date(
                ingredient.last_reviewed, category.review_interval_days
            )
    db.commit()
    db.refresh(category)
    return _category_out(category, db)


@router.delete("/ingredient-categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, admin: CurrentAdmin, db: DbSession):
    category = _get_or_404(db, IngredientCategory, category_id, "Ingredient category")
    members = db.scalar(
        select(func.count()).select_from(Ingredient).where(Ingredient.category_id == category.id)
    )
    if members:
        raise HTTPException(
            status_code=409,
            detail=f"Category '{category.name}' still has {members} ingredient(s); move them first",
        )
    db.delete(category)
    db.commit()


# ---------------------------------------------------------------------------
# Ingredients
# ---------------------------------------------------------------------------

def _ingredient_out(ingredient: Ingredient) -> IngredientOut:
    return IngredientOut(
        id=ingredient.id,
        name=ingredient.name,
        category_id=ingredient.category_id,
        category_name=ingredient.category.name if ingredient.category else None,
        unit=ingredient.unit,
        purchase_qty=ingredient.purchase_qty,
        purchase_price=ingredient.purchase_price,
        effective_cost_per_unit=ingredient.effective_cost_per_unit,
        currency=ingredient.currency,
        density_g_per_ml=ingredient.density_g_per_ml,
        source=ingredient.source,
        confidence=ingredient.confidence,
        is_estimate=ingredient.is_estimate,
        last_reviewed=ingredient.last_reviewed,
        next_reviewed=ingredient.next_reviewed,
        review_interval_days=ingredient.review_interval_days,
        effective_interval_days=effective_interval_days(ingredient),
        overdue=is_overdue(ingredient.next_reviewed),
        invoice_ref=ingredient.invoice_ref,
        created_at=ingredient.created_at,
        updated_at=ingredient.updated_at,
    )


def _resolve_category(db: DbSession, category_id: int | None) -> IngredientCategory | None:
    if category_id is None:
        return None
    return _get_or_404(db, IngredientCategory, category_id, "Ingredient category")


def _reject_duplicate_ingredient(db: DbSession, name: str, exclude_id: int | None = None):
    query = select(Ingredient).where(func.lower(Ingredient.name) == name.lower())
    if exclude_id is not None:
        query = query.where(Ingredient.id != exclude_id)
    if db.scalar(query) is not None:
        raise HTTPException(status_code=409, detail=f"Ingredient '{name}' already exists")


@router.get("/ingredients", response_model=IngredientListResponse)
def list_ingredients(
    admin: CurrentAdmin,
    db: DbSession,
    search: str | None = Query(default=None, min_length=1, max_length=150),
    category_id: int | None = Query(default=None),
    overdue: bool = Query(default=False),
    due_within_days: int | None = Query(default=None, ge=0, le=3650),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=15, ge=1, le=100),
):
    query = select(Ingredient)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(Ingredient.name.ilike(term), Ingredient.invoice_ref.ilike(term)))
    if category_id is not None:
        query = query.where(Ingredient.category_id == category_id)
    ingredients = list(db.scalars(query.order_by(Ingredient.name)))

    # Overdue/due-soon are derived at read (the §12 flag), so they filter
    # after fetch — ingredient counts stay desk-scale.
    today = date.today()
    if overdue:
        ingredients = [i for i in ingredients if is_overdue(i.next_reviewed, today)]
    if due_within_days is not None:
        horizon = today + timedelta(days=due_within_days)
        ingredients = [
            i
            for i in ingredients
            if i.next_reviewed is not None and today <= i.next_reviewed <= horizon
        ]

    total = len(ingredients)
    window = ingredients[(page - 1) * per_page : (page - 1) * per_page + per_page]
    return IngredientListResponse(
        ingredients=[_ingredient_out(i) for i in window],
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.post("/ingredients", response_model=IngredientOut, status_code=status.HTTP_201_CREATED)
def create_ingredient(payload: IngredientCreate, admin: CurrentAdmin, db: DbSession):
    try:
        unit = canonical_unit(payload.unit)
    except costing.UnknownUnitError as reason:
        raise HTTPException(status_code=400, detail=str(reason)) from reason
    category = _resolve_category(db, payload.category_id)
    name = payload.name.strip()
    _reject_duplicate_ingredient(db, name)

    interval = payload.review_interval_days
    next_reviewed = None
    if payload.last_reviewed is not None:
        next_reviewed = next_review_date(
            payload.last_reviewed, interval or DEFAULT_REVIEW_INTERVAL_DAYS
        )

    ingredient = Ingredient(
        name=name,
        category_id=category.id if category else None,
        unit=unit,
        purchase_qty=payload.purchase_qty,
        purchase_price=payload.purchase_price,
        effective_cost_per_unit=_quantize_unit_cost(
            effective_rate(payload.purchase_price, payload.purchase_qty)
        ),
        currency=payload.currency.lower(),
        density_g_per_ml=payload.density_g_per_ml,
        source=payload.source,
        confidence=payload.confidence,
        is_estimate=True,  # figures become confirmed only through review
        last_reviewed=payload.last_reviewed,
        next_reviewed=next_reviewed,
        review_interval_days=interval,
        invoice_ref=payload.invoice_ref,
    )
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    return _ingredient_out(ingredient)


@router.get("/ingredients/{ingredient_id}", response_model=IngredientOut)
def get_ingredient(ingredient_id: int, admin: CurrentAdmin, db: DbSession):
    return _ingredient_out(_get_or_404(db, Ingredient, ingredient_id, "Ingredient"))


# The fields whose change is a price event (a cost-relevant change to the
# recorded figures). Name, category, density and invoice reference are
# desk bookkeeping — they never move money.
_PRICE_EVENT_FIELDS = (
    "purchase_qty",
    "purchase_price",
    "effective_cost_per_unit",
    "unit",
    "currency",
    "is_estimate",
    "source",
    "confidence",
)


def _changed(before, after) -> bool:
    """Numeric comparison through Decimal (float artifacts must not fake a
    change); bools and strings compare directly."""
    if before is None or after is None:
        return before != after
    if isinstance(before, bool) or isinstance(after, bool):
        return before != after
    if isinstance(before, (int, float, Decimal)) and isinstance(after, (int, float, Decimal)):
        return Decimal(str(before)) != Decimal(str(after))
    return before != after


@router.put("/ingredients/{ingredient_id}", response_model=IngredientOut)
def update_ingredient(
    ingredient_id: int, payload: IngredientUpdate, admin: CurrentAdmin, db: DbSession
):
    """A field edit. Changes to the recorded price figures append a price
    event and — when they move the effective cost, its unit, or a density
    that conversions rely on — recompute every dependent product's cost in
    the same transaction."""
    ingredient = _get_or_404(db, Ingredient, ingredient_id, "Ingredient")
    data = payload.model_dump(exclude_unset=True)

    # The pre-change figures, captured before any mutation: the price
    # event's prev_* columns and the change detection both read these.
    before = {"density_g_per_ml": ingredient.density_g_per_ml}
    for field in _PRICE_EVENT_FIELDS:
        before[field] = getattr(ingredient, field)

    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        _reject_duplicate_ingredient(db, name, exclude_id=ingredient.id)
        ingredient.name = name
    if "category_id" in data:
        category = _resolve_category(db, data["category_id"])
        ingredient.category_id = category.id if category else None
    if "currency" in data and data["currency"] is not None:
        ingredient.currency = data["currency"].lower()
    if "unit" in data and data["unit"] is not None:
        try:
            ingredient.unit = canonical_unit(data["unit"])
        except costing.UnknownUnitError as reason:
            raise HTTPException(status_code=400, detail=str(reason)) from reason
    for field in ("density_g_per_ml", "source", "confidence", "invoice_ref", "review_interval_days"):
        if field in data:
            setattr(ingredient, field, data[field])
    if "purchase_qty" in data and data["purchase_qty"] is not None:
        ingredient.purchase_qty = data["purchase_qty"]
    if "purchase_price" in data and data["purchase_price"] is not None:
        ingredient.purchase_price = data["purchase_price"]

    # Effective cost per purchase unit follows the figures it derives from.
    if any(f in data for f in ("purchase_qty", "purchase_price")):
        ingredient.effective_cost_per_unit = _quantize_unit_cost(
            effective_rate(ingredient.purchase_price, ingredient.purchase_qty)
        )

    # Review scheduling: an interval change re-derives next_reviewed for an
    # already-reviewed ingredient.
    if "review_interval_days" in data and ingredient.last_reviewed is not None:
        ingredient.next_reviewed = next_review_date(
            ingredient.last_reviewed, effective_interval_days(ingredient)
        )

    after = {f: getattr(ingredient, f) for f in _PRICE_EVENT_FIELDS}
    figures_changed = any(_changed(before[f], after[f]) for f in _PRICE_EVENT_FIELDS)
    density_changed = "density_g_per_ml" in data and _changed(
        before["density_g_per_ml"], data["density_g_per_ml"]
    )

    # One transaction: the edit, its append-only provenance entry, and the
    # dependent cost recalculation land together or not at all.
    if figures_changed:
        db.add(
            IngredientPriceEvent(
                ingredient_id=ingredient.id,
                admin_user_id=admin.id,
                admin_name=admin.full_name,
                prev_purchase_qty=before["purchase_qty"],
                new_purchase_qty=after["purchase_qty"],
                prev_purchase_price=before["purchase_price"],
                new_purchase_price=after["purchase_price"],
                prev_effective_cost_per_unit=before["effective_cost_per_unit"],
                new_effective_cost_per_unit=after["effective_cost_per_unit"],
                prev_unit=before["unit"],
                new_unit=after["unit"],
                prev_currency=before["currency"],
                new_currency=after["currency"],
                prev_is_estimate=before["is_estimate"],
                new_is_estimate=after["is_estimate"],
                prev_source=before["source"],
                new_source=after["source"],
                prev_confidence=before["confidence"],
                new_confidence=after["confidence"],
                effective_from=date.today(),
            )
        )
    if figures_changed or density_changed:
        costing.recompute_dependent_costs(db, ingredient)
    db.commit()
    db.refresh(ingredient)
    return _ingredient_out(ingredient)


@router.post("/ingredients/{ingredient_id}/review", response_model=IngredientOut)
def review_ingredient(
    ingredient_id: int, payload: IngredientReviewRequest, admin: CurrentAdmin, db: DbSession
):
    """Confirming the recorded figures as reviewed: is_estimate → False,
    last_reviewed → the review date, next_reviewed advanced by the interval
    in force. No figures change here, so there is no price event and no
    cost recalculation — only the review clock moves."""
    ingredient = _get_or_404(db, Ingredient, ingredient_id, "Ingredient")
    reviewed_on = payload.reviewed_on or date.today()
    ingredient.is_estimate = False
    ingredient.last_reviewed = reviewed_on
    ingredient.next_reviewed = next_review_date(reviewed_on, effective_interval_days(ingredient))
    db.commit()
    db.refresh(ingredient)
    return _ingredient_out(ingredient)


@router.get(
    "/ingredients/{ingredient_id}/price-events",
    response_model=list[IngredientPriceEventOut],
)
def list_price_events(ingredient_id: int, admin: CurrentAdmin, db: DbSession):
    """The ingredient's append-only price history: who changed what, when,
    and from which figures."""
    ingredient = _get_or_404(db, Ingredient, ingredient_id, "Ingredient")
    return db.scalars(
        select(IngredientPriceEvent)
        .where(IngredientPriceEvent.ingredient_id == ingredient.id)
        .order_by(IngredientPriceEvent.created_at.desc(), IngredientPriceEvent.id.desc())
    )


@router.delete("/ingredients/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ingredient(ingredient_id: int, admin: CurrentAdmin, db: DbSession):
    ingredient = _get_or_404(db, Ingredient, ingredient_id, "Ingredient")
    dependents = list(
        db.scalars(
            select(Product)
            .join(Recipe, Recipe.product_id == Product.id)
            .join(RecipeItem, RecipeItem.recipe_id == Recipe.id)
            .where(RecipeItem.ingredient_id == ingredient.id)
            .distinct()
        )
    )
    if dependents:
        names = ", ".join(p.name for p in dependents)
        raise HTTPException(
            status_code=409,
            detail=f"Ingredient '{ingredient.name}' is used by recipes for: {names}",
        )
    db.delete(ingredient)  # price events ride along on CASCADE
    db.commit()


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

def _recipe_out(db: DbSession, recipe: Recipe) -> RecipeOut:
    items = [
        RecipeItemOut(
            id=item.id,
            ingredient_id=item.ingredient_id,
            ingredient_name=item.ingredient.name if item.ingredient else None,
            ingredient_unit=item.ingredient.unit if item.ingredient else None,
            usable_qty=item.usable_qty,
            unit=item.unit,
            loss_pct=item.loss_pct,
            note=item.note,
        )
        for item in recipe.items
    ]
    return RecipeOut(
        id=recipe.id,
        product_id=recipe.product_id,
        hands_on_minutes=recipe.hands_on_minutes,
        unattended_minutes=recipe.unattended_minutes,
        expected_batch_yield=recipe.expected_batch_yield,
        batch_overhead_estimate=recipe.batch_overhead_estimate,
        source=recipe.source,
        confidence=recipe.confidence,
        is_estimate=recipe.is_estimate,
        last_reviewed=recipe.last_reviewed,
        next_reviewed=recipe.next_reviewed,
        inputs_overdue=costing.recipe_inputs_overdue(db, recipe),
        notes=recipe.notes,
        items=items,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
    )


@router.get("/products/{product_id}/recipe", response_model=RecipeOut)
def get_recipe(product_id: int, admin: CurrentAdmin, db: DbSession):
    product = _get_or_404(db, Product, product_id, "Product")
    recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id))
    if recipe is None:
        raise HTTPException(status_code=404, detail="No recipe recorded for this product")
    return _recipe_out(db, recipe)


@router.put("/products/{product_id}/recipe", response_model=RecipeOut)
def upsert_recipe(
    product_id: int, payload: RecipeUpsertRequest, admin: CurrentAdmin, db: DbSession
):
    """Full-replace upsert of the product's recipe. Every line's unit must
    be convertible into its ingredient's purchase unit (density included
    when one is recorded) — an impossible conversion is refused here, not
    discovered at costing time. One cost record follows, in the same
    transaction."""
    product = _get_or_404(db, Product, product_id, "Product")

    seen: set[int] = set()
    ingredients: dict[int, Ingredient] = {}
    for item in payload.items:
        if item.ingredient_id in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Ingredient {item.ingredient_id} appears more than once",
            )
        seen.add(item.ingredient_id)
        ingredient = db.get(Ingredient, item.ingredient_id)
        if ingredient is None:
            raise HTTPException(
                status_code=400, detail=f"Ingredient {item.ingredient_id} not found"
            )
        ingredients[item.ingredient_id] = ingredient
        try:
            unit = canonical_unit(item.unit)
            convert_qty(Decimal("1"), unit, ingredient.unit, ingredient.density_g_per_ml)
        except costing.CostingError as reason:
            raise HTTPException(
                status_code=400, detail=f"Ingredient '{ingredient.name}': {reason}"
            ) from reason

    recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id))
    if recipe is None:
        recipe = Recipe(product_id=product.id)
        db.add(recipe)
        recipe.last_reviewed = payload.last_reviewed or date.today()
    else:
        if payload.last_reviewed is not None:
            recipe.last_reviewed = payload.last_reviewed
        elif recipe.last_reviewed is None:
            recipe.last_reviewed = date.today()
        for existing_item in list(recipe.items):
            db.delete(existing_item)
        # The unit of work orders INSERTs before DELETEs within a flush, so
        # a line retained by the edit would collide with its own
        # still-pending predecessor on (recipe_id, ingredient_id). Send the
        # deletions first — same transaction, only their order is pinned —
        # so the replacements insert into a table that no longer holds them.
        db.flush()

    recipe.hands_on_minutes = payload.hands_on_minutes
    recipe.unattended_minutes = payload.unattended_minutes
    recipe.expected_batch_yield = payload.expected_batch_yield
    recipe.batch_overhead_estimate = payload.batch_overhead_estimate
    recipe.source = payload.source
    recipe.confidence = payload.confidence
    recipe.notes = payload.notes
    recipe.next_reviewed = next_review_date(
        recipe.last_reviewed, DEFAULT_REVIEW_INTERVAL_DAYS
    )
    for item in payload.items:
        db.add(
            RecipeItem(
                recipe=recipe,
                ingredient_id=item.ingredient_id,
                usable_qty=item.usable_qty,
                unit=canonical_unit(item.unit),
                loss_pct=item.loss_pct,
                note=item.note,
            )
        )
    # The session does not autoflush, and the computation below reads the
    # recipe and its lines back from the database — flush the replacement
    # so it costs exactly what was just written (old lines gone, new in).
    db.flush()
    costing.refresh_cost(db, product, costing.TRIGGER_RECIPE_CHANGE)
    db.commit()
    db.refresh(recipe)
    return _recipe_out(db, recipe)


# ---------------------------------------------------------------------------
# Production batches
# ---------------------------------------------------------------------------

@router.get("/batches", response_model=list[ProductionBatchOut])
def list_batches(
    admin: CurrentAdmin,
    db: DbSession,
    product_id: int | None = Query(default=None),
    batch_status: str | None = Query(default=None, alias="status", pattern="^(open|closed)$"),
):
    query = select(ProductionBatch)
    if product_id is not None:
        query = query.where(ProductionBatch.product_id == product_id)
    if batch_status is not None:
        query = query.where(ProductionBatch.status == batch_status)
    return db.scalars(
        query.order_by(ProductionBatch.produced_at.desc(), ProductionBatch.id.desc())
    )


@router.post("/batches", response_model=ProductionBatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: ProductionBatchCreate, admin: CurrentAdmin, db: DbSession):
    product = _get_or_404(db, Product, payload.product_id, "Product")
    recipe = db.scalar(select(Recipe).where(Recipe.product_id == product.id))
    expected_yield = payload.expected_yield
    if expected_yield is None:
        if recipe is None:
            raise HTTPException(
                status_code=400,
                detail="expected_yield is required when the product has no recipe",
            )
        expected_yield = recipe.expected_batch_yield
    batch_overhead_estimated = payload.batch_overhead_estimated
    if batch_overhead_estimated is None:
        batch_overhead_estimated = recipe.batch_overhead_estimate if recipe else 0

    batch = ProductionBatch(
        product_id=product.id,
        produced_at=payload.produced_at or date.today(),
        expected_yield=expected_yield,
        batch_overhead_estimated=batch_overhead_estimated,
        overhead_categories=payload.overhead_categories,
        usage_notes=payload.usage_notes,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.put("/batches/{batch_id}", response_model=ProductionBatchOut)
def update_batch(
    batch_id: int, payload: ProductionBatchUpdate, admin: CurrentAdmin, db: DbSession
):
    batch = _get_or_404(db, ProductionBatch, batch_id, "Production batch")
    if batch.status == "closed":
        raise HTTPException(
            status_code=409,
            detail="Closed batches are immutable — their figures feed cost history",
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(batch, field, value)
    db.commit()
    db.refresh(batch)
    return batch


@router.post("/batches/{batch_id}/close", response_model=ProductionBatchOut)
def close_batch(
    batch_id: int, payload: ProductionBatchClose, admin: CurrentAdmin, db: DbSession
):
    """Freeze a batch with its actuals. The yield variance is stored at
    close ((actual − expected) / expected × 100, two decimals); a fresh
    cost record follows in the same transaction when the product has a
    recipe. Closing never edits the batch's earlier figures."""
    batch = _get_or_404(db, ProductionBatch, batch_id, "Production batch")
    if batch.status == "closed":
        raise HTTPException(status_code=409, detail="Batch is already closed")
    if batch.expected_yield is None or batch.expected_yield <= 0:
        raise HTTPException(status_code=400, detail="Batch has no valid expected yield")

    batch.actual_yield = payload.actual_yield
    batch.batch_overhead_actual = payload.batch_overhead_actual
    if payload.usage_notes is not None:
        batch.usage_notes = payload.usage_notes
    variance = (
        (Decimal(payload.actual_yield) - Decimal(batch.expected_yield))
        / Decimal(batch.expected_yield)
        * Decimal(100)
    )
    batch.yield_variance_pct = float(variance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    batch.status = "closed"
    batch.closed_at = utcnow()

    # The actuals selector below reads closed batches from the database;
    # without a flush this batch would still read as open there.
    db.flush()

    product = db.get(Product, batch.product_id)
    recipe = db.scalar(select(Recipe).where(Recipe.product_id == batch.product_id))
    if recipe is not None and product is not None:
        costing.refresh_cost(db, product, costing.TRIGGER_BATCH_CLOSED)
    db.commit()
    db.refresh(batch)
    return batch


# ---------------------------------------------------------------------------
# Fixed monthly overhead
# ---------------------------------------------------------------------------

def _overlap_warnings(db: DbSession, month: OverheadMonth) -> list[dict]:
    """AC-8: categories present both in this month's fixed overhead and in a
    batch's own overhead entries — the double-count warning. A warning,
    never a block."""
    warnings = []
    for batch in db.scalars(
        select(ProductionBatch)
        .where(ProductionBatch.overhead_categories.is_not(None))
        .order_by(ProductionBatch.id)
    ):
        shared = overhead_overlap(month.included_categories, batch.overhead_categories)
        if shared:
            warnings.append(
                {
                    "month": month.month.isoformat(),
                    "batch_id": batch.id,
                    "product_id": batch.product_id,
                    "overlapping_categories": shared,
                    "note": (
                        "These categories also appear in the batch's own overhead — "
                        "counting them in both ledgers would double-charge the batch"
                    ),
                }
            )
    return warnings


def _month_save_out(db: DbSession, month: OverheadMonth) -> OverheadMonthSaveOut:
    return OverheadMonthSaveOut(
        **OverheadMonthOut.model_validate(month).model_dump(),
        overlap_warnings=_overlap_warnings(db, month),
    )


@router.get("/overhead-months", response_model=list[OverheadMonthOut])
def list_overhead_months(admin: CurrentAdmin, db: DbSession):
    return db.scalars(select(OverheadMonth).order_by(OverheadMonth.month.desc()))


@router.post(
    "/overhead-months", response_model=OverheadMonthSaveOut, status_code=status.HTTP_201_CREATED
)
def create_overhead_month(payload: OverheadMonthCreate, admin: CurrentAdmin, db: DbSession):
    month_start = payload.month.replace(day=1)
    dup = db.scalar(select(OverheadMonth).where(OverheadMonth.month == month_start))
    if dup is not None:
        raise HTTPException(
            status_code=409, detail=f"An overhead month for {month_start:%Y-%m} already exists"
        )
    month = OverheadMonth(
        month=month_start,
        fixed_overhead_total=payload.fixed_overhead_total,
        forecast_hours=payload.forecast_hours,
        currency=payload.currency.lower(),
        included_categories=payload.included_categories,
        source=payload.source,
        confidence=payload.confidence,
        is_estimate=True,  # confirmed only through actuals + variance review later
    )
    db.add(month)
    db.flush()
    # A month changes costing only when it becomes the month costing reads
    # (the latest that has arrived); backfilling old history does not.
    current = costing.current_overhead_month(db)
    if current is not None and current.id == month.id:
        costing.recompute_all_costed_products(db, costing.TRIGGER_OVERHEAD_MONTH_CHANGE)
    db.commit()
    db.refresh(month)
    return _month_save_out(db, month)


@router.put("/overhead-months/{month_id}", response_model=OverheadMonthSaveOut)
def update_overhead_month(
    month_id: int, payload: OverheadMonthUpdate, admin: CurrentAdmin, db: DbSession
):
    """Edits the month's figures (the month itself is fixed at creation).
    Only a change to the figures costing reads — the total or the forecast
    hours — recomputes; recording month-end actuals leaves the forecast,
    and every cost computed on it, untouched."""
    month = _get_or_404(db, OverheadMonth, month_id, "Overhead month")
    before_total, before_hours = month.fixed_overhead_total, month.forecast_hours
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(month, field, value)
    figures_changed = _changed(before_total, month.fixed_overhead_total) or _changed(
        before_hours, month.forecast_hours
    )
    db.flush()
    current = costing.current_overhead_month(db)
    if figures_changed and current is not None and current.id == month.id:
        costing.recompute_all_costed_products(db, costing.TRIGGER_OVERHEAD_MONTH_CHANGE)
    db.commit()
    db.refresh(month)
    return _month_save_out(db, month)


# ---------------------------------------------------------------------------
# Labour rate (effective-dated, append-only)
# ---------------------------------------------------------------------------

def _labour_rate_status(db: DbSession) -> LabourRateStatusOut:
    rate, currency, source, valid_from = labour_rate_on(db)
    return LabourRateStatusOut(
        rate=float(rate),
        currency=currency,
        source=source,
        valid_from=valid_from,
        history=list(db.scalars(select(LabourRate).order_by(LabourRate.valid_from.desc()))),
    )


@router.get("/labour-rate", response_model=LabourRateStatusOut)
def get_labour_rate(admin: CurrentAdmin, db: DbSession):
    """What costing resolves right now (and where it came from — the table
    or the default fallback), plus the full effective-dated history."""
    return _labour_rate_status(db)


@router.put(
    "/labour-rate", response_model=LabourRateStatusOut, status_code=status.HTTP_201_CREATED
)
def set_labour_rate(payload: LabourRateSetRequest, admin: CurrentAdmin, db: DbSession):
    """Append a new effective-dated rate. Rates are never edited — a new
    effective date is a new row — so a duplicate valid_from is refused.
    When the new row is the rate costing resolves today, every costed
    product gets a fresh record in the same transaction; a future-dated
    rate changes nothing yet, and old cost records keep their snapshots."""
    if db.get(LabourRate, payload.valid_from) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A labour rate effective {payload.valid_from.isoformat()} already exists "
                "(rates are append-only — record a new effective date instead)"
            ),
        )
    row = LabourRate(
        valid_from=payload.valid_from,
        rate=payload.rate,
        currency=payload.currency.lower(),
        source=payload.source,
    )
    db.add(row)
    db.flush()
    _, _, source, valid_from = labour_rate_on(db)
    if source == costing.LABOUR_RATE_SOURCE_TABLE and valid_from == payload.valid_from:
        costing.recompute_all_costed_products(db, costing.TRIGGER_LABOUR_RATE_CHANGE)
    db.commit()
    db.refresh(row)  # created_at comes from the column default at INSERT
    return _labour_rate_status(db)


# ---------------------------------------------------------------------------
# The cost-review desk
# ---------------------------------------------------------------------------

@router.get("/cost-review", response_model=CostReviewOut)
def cost_review(
    admin: CurrentAdmin,
    db: DbSession,
    window: int = Query(default=14, ge=1, le=365),
):
    """The desk's one view: what is overdue, what falls due within the
    window, and the overhead double-count warnings — all derived at read."""
    settings = get_settings()
    today = date.today()
    horizon = today + timedelta(days=window)

    def earliest_due(recipe: Recipe) -> date | None:
        dates = [recipe.next_reviewed]
        dates += [
            item.ingredient.next_reviewed
            for item in recipe.items
            if item.ingredient is not None and item.ingredient.next_reviewed is not None
        ]
        dates = [d for d in dates if d is not None]
        return min(dates) if dates else None

    overdue: list[CostReviewEntryOut] = []
    upcoming: list[CostReviewEntryOut] = []
    for recipe in db.scalars(select(Recipe)):
        product = db.get(Product, recipe.product_id)
        if product is None:
            continue
        record = costing.latest_cost_record(db, product.id)
        cost_status, summary = costing.product_cost_summary(
            db, product, settings.stripe_currency, settings.tax_mode
        )
        due = earliest_due(recipe)
        inputs_overdue = costing.recipe_inputs_overdue(db, recipe)
        if record is None or not record.is_complete or inputs_overdue:
            reason = (
                "no_cost_record"
                if record is None
                else "incomplete"
                if not record.is_complete
                else "inputs_overdue"
            )
            overdue.append(
                CostReviewEntryOut(
                    product_id=product.id,
                    product_name=product.name,
                    cost_status=cost_status,
                    cost=ProductCostSummaryOut(**summary) if record is not None else None,
                    reason=reason,
                    next_reviewed=due,
                    overdue_on=due,
                )
            )
        elif due is not None and today <= due <= horizon:
            upcoming.append(
                CostReviewEntryOut(
                    product_id=product.id,
                    product_name=product.name,
                    cost_status=cost_status,
                    cost=ProductCostSummaryOut(**summary),
                    reason="scheduled",
                    next_reviewed=due,
                    overdue_on=due,
                )
            )

    overlap_warnings: list[dict] = []
    month = costing.current_overhead_month(db)
    if month is not None:
        overlap_warnings = _overlap_warnings(db, month)

    return CostReviewOut(
        overdue=overdue,
        upcoming=upcoming,
        window_days=window,
        overlap_warnings=overlap_warnings,
    )
