"""Phase 2 costing foundation — every acceptance criterion the phase owns.

The suite shares the process with test_api.py: whichever file imports the
app package first creates and seeds `test_bakehouse.db`; this file boots a
second client against the same database (idempotent — the staging runs
proved the second boot) and never removes the file, because the first
client holds it open. State accumulates across the file on purpose: the
append-only history tests depend on earlier writes staying put.

Two stripes of tests live here. The engine stripe calls costing.py's pure
functions and `compute_product_cost` against a private in-memory database,
so missing-input reasons are tested without disturbing the shared dataset.
The API stripe drives the admin endpoints end to end — including the
401/403/200 authorization matrix, the append-only ledgers, and the
price-immutability guarantee (AC-38).
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

# Same values test_api.py sets — harmless when both files load, required
# when this file is imported first.
os.environ["DATABASE_URL"] = "sqlite:///./test_bakehouse.db"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["LOGIN_RATE_LIMIT"] = "1000"
os.environ["REGISTER_RATE_LIMIT"] = "1000"
os.environ["FORGOT_RATE_LIMIT"] = "1000"
os.environ["OTP_RATE_LIMIT"] = "1000"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_gateway"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"

# Only the first test module into the process owns the database file; a
# second one must not yank it away from the client already holding it.
if "app" not in sys.modules:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(f"test_bakehouse.db{suffix}")
        except FileNotFoundError:
            pass

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app import costing  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Ingredient,
    OverheadMonth,
    Product,
    ProductCostRecord,
    ProductionBatch,
    Recipe,
    RecipeItem,
)

client = TestClient(create_app())
client.__enter__()

TODAY = date.today()
CURRENT_MONTH = TODAY.replace(day=1)
ADMIN_EMAIL = "admin@djinisbakehouse.com"
ADMIN_PASSWORD = "Admin123!"

# Cross-test state: ids this file's tests create, in the order they run.
STATE: dict = {}


def admin_headers() -> dict:
    if "admin" not in STATE:
        response = client.post(
            "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, response.text
        STATE["admin"] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return STATE["admin"]


def customer_headers() -> dict:
    if "customer" not in STATE:
        client.post(
            "/api/auth/register",
            json={
                "email": "costing.shopper@example.com",
                "password": "Shopper123!",
                "first_name": "Costing",
                "last_name": "Shopper",
            },
        )
        response = client.post(
            "/api/auth/login",
            json={"email": "costing.shopper@example.com", "password": "Shopper123!"},
        )
        assert response.status_code == 200
        STATE["customer"] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return STATE["customer"]


def seeded_product_ids(count: int) -> list[int]:
    if "products" not in STATE:
        listing = client.get("/api/products", params={"per_page": 40, "sort": "name"}).json()
        STATE["products"] = [p["id"] for p in listing["products"]]
    return STATE["products"][:count]


def ensure_overhead_month(month: str, total: float, hours: float, **extra) -> dict:
    """Create the month once; a 409 (already there) returns the existing row."""
    payload = {"month": month, "fixed_overhead_total": total, "forecast_hours": hours, **extra}
    response = client.post("/api/admin/costing/overhead-months", json=payload, headers=admin_headers())
    if response.status_code == 409:
        months = client.get("/api/admin/costing/overhead-months", headers=admin_headers()).json()
        return next(m for m in months if m["month"] == month[:7] + "-01")
    assert response.status_code == 201, response.text
    return response.json()


def cost_records_for(product_id: int) -> list[ProductCostRecord]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ProductCostRecord)
                .where(ProductCostRecord.product_id == product_id)
                .order_by(ProductCostRecord.id)
            )
        )


def record_snapshot(record: ProductCostRecord) -> dict:
    return {
        "trigger": record.trigger,
        "currency": record.currency,
        "total_cost": record.total_cost,
        "is_complete": record.is_complete,
        "missing": record.missing,
        "breakdown": record.breakdown,
    }


def make_ingredient(name: str, unit: str, qty: float, price: float, **extra) -> dict:
    payload = {"name": name, "unit": unit, "purchase_qty": qty, "purchase_price": price, **extra}
    response = client.post("/api/admin/costing/ingredients", json=payload, headers=admin_headers())
    assert response.status_code == 201, response.text
    return response.json()


def put_recipe(product_id: int, payload: dict) -> dict:
    response = client.put(
        f"/api/admin/costing/products/{product_id}/recipe", json=payload, headers=admin_headers()
    )
    assert response.status_code == 200, response.text
    return response.json()


def latest_record_dict(product_id: int) -> dict:
    records = cost_records_for(product_id)
    assert records, f"no cost records for product {product_id}"
    last = records[-1]
    return {
        "id": last.id,
        "trigger": last.trigger,
        "currency": last.currency,
        "total_cost": last.total_cost,
        "is_complete": last.is_complete,
        "missing": last.missing or [],
        "breakdown": last.breakdown or {},
    }


# ---------------------------------------------------------------------------
# The engine: pure arithmetic (AC-3, AC-4, §9, §10)
# ---------------------------------------------------------------------------

def test_purchase_qty_scaling_ac4():
    # §7/AC-4: 1000 g at 5 % loss → 1052.63 g purchased, line cost
    # 1.5789… €, one cent rounding only at the end.
    purchased = costing.purchase_qty(Decimal("1000"), Decimal("5"))
    assert purchased == Decimal("1052.631578947368421052631579")
    line = purchased / Decimal("1000") * Decimal("1.50")
    assert line == Decimal("1.578947368421052631578947368")
    assert costing.round_cents(line) == Decimal("1.58")
    # The 500 g case carries 0.7894… (not the 1000 g figure) → €0.79.
    half = costing.purchase_qty(Decimal("500"), Decimal("5")) / Decimal("1000") * Decimal("1.50")
    assert half.quantize(Decimal("0.0001")) == Decimal("0.7895")
    assert costing.round_cents(half) == Decimal("0.79")
    # Zero loss is explicit and exact.
    assert costing.purchase_qty(Decimal("500"), 0) == Decimal("500")
    try:
        costing.purchase_qty(Decimal("500"), Decimal("100"))
    except costing.CostingError:
        pass
    else:
        raise AssertionError("100 % loss must be rejected")


def test_effective_rate_ac3():
    # §6/AC-3: 25 kg at €37.50 → €1.50/kg, exact.
    rate = costing.effective_rate(Decimal("37.50"), Decimal("25"))
    assert rate == Decimal("1.50")
    assert isinstance(rate, Decimal)


def test_unit_conversion_rules():
    assert costing.canonical_unit("Kilograms") == "kg"
    assert costing.canonical_unit("gr") == "g"
    assert costing.canonical_unit("litres") == "l"
    assert costing.unit_dimension("ml") == "volume"
    # Same dimension: exact factors.
    assert costing.convert_qty(Decimal("1.5"), "kg", "g") == Decimal("1500")
    assert costing.convert_qty(Decimal("250"), "g", "kg") == Decimal("0.25")
    assert costing.convert_qty(Decimal("2"), "l", "ml") == Decimal("2000")
    # Mass ↔ volume only with the ingredient's recorded density.
    try:
        costing.convert_qty(Decimal("500"), "g", "ml")
    except costing.MismatchedUnitsError:
        pass
    else:
        raise AssertionError("g→ml without a density must be refused")
    assert costing.convert_qty(Decimal("515"), "g", "ml", Decimal("1.03")) == Decimal("500")
    assert costing.convert_qty(Decimal("500"), "ml", "g", Decimal("1.03")) == Decimal("515")
    # Count never converts.
    try:
        costing.convert_qty(Decimal("2"), "each", "g")
    except costing.MismatchedUnitsError:
        pass
    else:
        raise AssertionError("each→g must be refused")
    try:
        costing.canonical_unit("cups")
    except costing.UnknownUnitError:
        pass
    else:
        raise AssertionError("'cups' is outside the vocabulary")


def test_labour_cost_examples():
    # §9: hands-on minutes / 60 × €20 — 12 → 4.00, 45 → 15.00, 7.5 → 2.50.
    assert costing.labour_cost(Decimal("12"), Decimal("20")) == Decimal("4.00")
    assert costing.labour_cost(Decimal("45"), Decimal("20")) == Decimal("15.00")
    assert costing.labour_cost(Decimal("7.5"), Decimal("20")) == Decimal("2.50")


def test_fixed_overhead_rate_ac7():
    # §10B: €800 / 160 h → €5.00/h; a 24-minute run → €2.00.
    rate = costing.fixed_overhead_rate(Decimal("800"), Decimal("160"))
    assert rate == Decimal("5")
    assert costing.fixed_overhead_for_minutes(Decimal("24"), rate) == Decimal("2.00")
    try:
        costing.fixed_overhead_rate(Decimal("800"), Decimal("0"))
    except costing.CostingError:
        pass
    else:
        raise AssertionError("zero forecast hours must be rejected")


def test_review_scheduling_math_ac36():
    # §13: 2026-01-01 + 90 days → 2026-04-01; 30 days → 2026-01-31.
    assert costing.next_review_date(date(2026, 1, 1), 90) == date(2026, 4, 1)
    assert costing.next_review_date(date(2026, 1, 1), 30) == date(2026, 1, 31)
    # §12: overdue means now ≥ next_reviewed.
    assert costing.is_overdue(TODAY, TODAY)
    assert not costing.is_overdue(TODAY + timedelta(days=1), TODAY)
    assert not costing.is_overdue(None, TODAY)


def test_margin_floor_arithmetic():
    # §4: margin = (net − cost) / net; floor price = cost / 0.60.
    margin = costing.margin(Decimal("5.00"), Decimal("2.38"))
    assert margin.quantize(Decimal("0.0001")) == Decimal("0.5240")
    floor = costing.min_net_price(Decimal("2.38"))
    assert floor.quantize(Decimal("0.0001")) == Decimal("3.9667")
    assert costing.round_cents(floor) == Decimal("3.97")
    assert costing.overhead_overlap("Rent\nInsurance", "insurance\nFlour") == ["insurance"]
    assert costing.overhead_overlap(None, "Rent") == []


# ---------------------------------------------------------------------------
# The engine: compute_product_cost against a private in-memory database
# ---------------------------------------------------------------------------

def _isolated_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _iso_product(session, name="Iso Bun", price=5.00):
    product = Product(name=name, slug=name.lower().replace(" ", "-"), price=price)
    session.add(product)
    session.flush()
    return product


def _iso_recipe(session, product, items, minutes=10, yield_units=10, overhead=Decimal("0")):
    recipe = Recipe(
        product_id=product.id,
        hands_on_minutes=minutes,
        expected_batch_yield=yield_units,
        batch_overhead_estimate=overhead,
    )
    session.add(recipe)
    session.flush()
    for ingredient, qty, loss in items:
        session.add(
            RecipeItem(
                recipe_id=recipe.id,
                ingredient_id=ingredient.id,
                usable_qty=qty,
                unit=ingredient.unit,
                loss_pct=loss,
            )
        )
    session.flush()
    return recipe


def test_incomplete_reasons_are_data():
    session = _isolated_session()
    product = _iso_product(session)

    # No recipe at all.
    result = costing.compute_product_cost(session, product)
    assert not result.is_complete and result.missing == ["no_recipe"]

    # Recipe with no lines.
    _iso_recipe(session, product, [])
    result = costing.compute_product_cost(session, product)
    assert result.missing == ["no_recipe_lines"]

    # Everything but an overhead month.
    flour = Ingredient(
        name="Iso flour", unit="kg", purchase_qty=25, purchase_price=Decimal("37.50"),
        effective_cost_per_unit=Decimal("1.50"), currency="eur",
    )
    session.add(flour)
    session.flush()
    recipe = session.scalar(select(Recipe))
    session.add(
        RecipeItem(recipe_id=recipe.id, ingredient_id=flour.id, usable_qty=1000, unit="g", loss_pct=0)
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.missing == ["no_overhead_month"]

    # A month with a non-positive forecast is invalid, not guessed around.
    session.add(
        OverheadMonth(
            month=CURRENT_MONTH - timedelta(days=31),
            fixed_overhead_total=Decimal("800"),
            forecast_hours=0,
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.missing == ["invalid_forecast_hours"]

    # A zero expected yield is invalid (the invalid month stays in force).
    bad_yield = _iso_product(session, "Iso bad yield")
    _iso_recipe(session, bad_yield, [(flour, 1000, 0)], yield_units=0)
    session.flush()
    result = costing.compute_product_cost(session, bad_yield)
    assert "invalid_yield" in result.missing

    # A valid current month puts a real rate behind costing; mixed input
    # currencies are still an honest incomplete, never a conversion.
    session.add(
        OverheadMonth(
            month=CURRENT_MONTH, fixed_overhead_total=Decimal("800"), forecast_hours=160
        )
    )
    usd_sugar = Ingredient(
        name="Iso sugar", unit="kg", purchase_qty=1, purchase_price=Decimal("2"),
        effective_cost_per_unit=Decimal("2.00"), currency="usd",
    )
    session.add(usd_sugar)
    session.flush()
    mixed = _iso_product(session, "Iso mixed")
    mixed_recipe = _iso_recipe(session, mixed, [(flour, 1000, 0)])
    session.add(
        RecipeItem(
            recipe_id=mixed_recipe.id, ingredient_id=usd_sugar.id,
            usable_qty=100, unit="g", loss_pct=0,
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, mixed)
    assert result.missing == ["mixed_currencies"]
    assert result.currency is None and result.total_cost is None


def test_actuals_selector_refuses_unsuitable_batches():
    """A1: only a genuinely completed batch — closed, positive actual yield,
    actual overhead recorded — feeds actual costing. Anything else falls
    back to the recipe estimate, never silently."""
    session = _isolated_session()
    session.add(
        OverheadMonth(month=CURRENT_MONTH, fixed_overhead_total=Decimal("800"), forecast_hours=160)
    )
    flour = Ingredient(
        name="Iso flour", unit="kg", purchase_qty=25, purchase_price=Decimal("37.50"),
        effective_cost_per_unit=Decimal("1.50"), currency="eur",
    )
    session.add(flour)
    session.flush()
    product = _iso_product(session)
    # Quantities here are in the ingredient's own purchase unit (the
    # helper records lines that way): 1 kg of flour at €1.50/kg.
    _iso_recipe(session, product, [(flour, 1, 0)], minutes=0, yield_units=24)

    # An open batch carrying actuals anyway — not closed, must be ignored.
    session.add(
        ProductionBatch(
            product_id=product.id, produced_at=TODAY, expected_yield=24,
            actual_yield=22, batch_overhead_actual=Decimal("3"), status="open",
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.breakdown["batch_overhead"]["mode"] == "estimated"

    # A closed batch with zero actual sellable yield — unsuitable.
    session.add(
        ProductionBatch(
            product_id=product.id, produced_at=TODAY, expected_yield=24,
            actual_yield=0, batch_overhead_actual=Decimal("3"), status="closed",
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.breakdown["batch_overhead"]["mode"] == "estimated"

    # A closed batch missing its actual overhead — unsuitable.
    session.add(
        ProductionBatch(
            product_id=product.id, produced_at=TODAY, expected_yield=24,
            actual_yield=22, status="closed",
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.breakdown["batch_overhead"]["mode"] == "estimated"

    # The genuine article — closed, positive yield, actual overhead — and
    # the latest one wins (produced_at desc, id desc tie-break).
    session.add(
        ProductionBatch(
            product_id=product.id, produced_at=TODAY, expected_yield=24,
            actual_yield=22, batch_overhead_actual=Decimal("2.64"), status="closed",
        )
    )
    session.add(
        ProductionBatch(
            product_id=product.id, produced_at=TODAY + timedelta(days=1),
            expected_yield=24, actual_yield=20, batch_overhead_actual=Decimal("5"),
            status="closed",
        )
    )
    session.flush()
    result = costing.compute_product_cost(session, product)
    assert result.breakdown["batch_overhead"]["mode"] == "actual"
    assert Decimal(result.breakdown["batch_overhead"]["amount"]) == Decimal("5")
    assert result.breakdown["yield"]["units"] == 20
    # Estimated and actual overhead are never added together: with lines
    # 1.50 + labour 0 + actual 5.00 over 20 units → 0.325 → €0.33.
    assert result.total_cost == Decimal("0.33")


def test_breakdown_money_is_exact_strings():
    """The reconstruction snapshot stores decimals as exact strings —
    never floats — so history can be replayed without artifacts."""
    session = _isolated_session()
    session.add(
        OverheadMonth(month=CURRENT_MONTH, fixed_overhead_total=Decimal("800"), forecast_hours=160)
    )
    flour = Ingredient(
        name="Iso flour", unit="kg", purchase_qty=25, purchase_price=Decimal("37.50"),
        effective_cost_per_unit=Decimal("1.50"), currency="eur",
    )
    session.add(flour)
    session.flush()
    product = _iso_product(session)
    _iso_recipe(session, product, [(flour, Decimal("17.6"), 0)], minutes=0, yield_units=24)
    result = costing.compute_product_cost(session, product)
    assert result.is_complete
    line_cost = result.breakdown["lines"][0]["line_cost"]
    assert isinstance(line_cost, str)  # exact decimal text, never a float
    assert Decimal(line_cost) == Decimal("26.4")
    assert result.breakdown["labour"]["rate"] == "20.00"
    assert result.breakdown["total_cost"] == "1.10"


# ---------------------------------------------------------------------------
# Authorization — no new mechanism, the existing admin gate
# ---------------------------------------------------------------------------

def test_costing_requires_admin():
    representative = [
        ("get", "/api/admin/costing/ingredients"),
        ("get", "/api/admin/costing/cost-review"),
        ("get", "/api/admin/costing/overhead-months"),
        ("get", "/api/admin/costing/labour-rate"),
    ]
    for method, url in representative:
        unauthenticated = getattr(client, method)(url)
        assert unauthenticated.status_code == 401, f"{url}: {unauthenticated.status_code}"
        forbidden = getattr(client, method)(url, headers=customer_headers())
        assert forbidden.status_code == 403, f"{url}: {forbidden.status_code}"
        allowed = getattr(client, method)(url, headers=admin_headers())
        assert allowed.status_code == 200, f"{url}: {allowed.status_code}"
    # AC-2: the new tables start empty and the labour rate begins at the
    # labelled default fallback — no seeded fake data anywhere.
    assert client.get("/api/admin/costing/ingredients", headers=admin_headers()).json()[
        "ingredients"
    ] == []
    assert client.get("/api/admin/costing/labour-rate", headers=admin_headers()).json()[
        "history"
    ] == []
    # Writes too.
    assert client.post(
        "/api/admin/costing/ingredient-categories", json={"name": "Nope"}
    ).status_code == 401
    assert client.post(
        "/api/admin/costing/ingredient-categories",
        json={"name": "Nope"},
        headers=customer_headers(),
    ).status_code == 403


# ---------------------------------------------------------------------------
# Ingredient categories and ingredients
# ---------------------------------------------------------------------------

def test_category_crud_and_intervals():
    headers = admin_headers()
    created = client.post(
        "/api/admin/costing/ingredient-categories",
        json={"name": "Cost Flours", "review_interval_days": 30},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    category = created.json()
    STATE["flour_category_id"] = category["id"]
    assert category["review_interval_days"] == 30 and category["ingredient_count"] == 0

    # Case-insensitive duplicate refused.
    assert client.post(
        "/api/admin/costing/ingredient-categories",
        json={"name": "cost flours"},
        headers=headers,
    ).status_code == 409

    listed = client.get("/api/admin/costing/ingredient-categories", headers=headers).json()
    assert any(c["name"] == "Cost Flours" for c in listed)

    updated = client.put(
        f"/api/admin/costing/ingredient-categories/{category['id']}",
        json={"review_interval_days": 45},
        headers=headers,
    )
    assert updated.status_code == 200 and updated.json()["review_interval_days"] == 45

    # A category with members cannot be deleted; an empty one can.
    flour = make_ingredient(
        "Cost Flour 25kg", "kg", 25, 37.50,
        category_id=category["id"], currency="EUR",
    )
    STATE["flour_id"] = flour["id"]
    assert client.delete(
        f"/api/admin/costing/ingredient-categories/{category['id']}", headers=headers
    ).status_code == 409
    spare = client.post(
        "/api/admin/costing/ingredient-categories", json={"name": "Cost Spare"}, headers=headers
    ).json()
    assert client.delete(
        f"/api/admin/costing/ingredient-categories/{spare['id']}", headers=headers
    ).status_code == 204


def test_ingredient_crud_review_and_filters():
    headers = admin_headers()
    flour_id = STATE["flour_id"]

    # AC-3: effective cost derived at creation, unit alias canonicalized,
    # currency lowered.
    flour = client.get(f"/api/admin/costing/ingredients/{flour_id}", headers=headers).json()
    assert flour["unit"] == "kg"
    assert flour["currency"] == "eur"
    assert flour["effective_cost_per_unit"] == 1.5
    assert flour["is_estimate"] is True
    assert flour["effective_interval_days"] == 45  # category interval in force

    # Reviewing confirms the figures and starts the review clock.
    reviewed = client.post(
        f"/api/admin/costing/ingredients/{flour_id}/review",
        json={"reviewed_on": "2026-01-01"},
        headers=headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["is_estimate"] is False
    assert body["last_reviewed"] == "2026-01-01"
    assert body["next_reviewed"] == "2026-02-15"  # 2026-01-01 + 45 days
    assert body["overdue"] is True  # well past due today

    # The overdue filter finds it; the due-soon window does not.
    overdue_list = client.get(
        "/api/admin/costing/ingredients", params={"overdue": True}, headers=headers
    ).json()
    assert any(i["id"] == flour_id for i in overdue_list["ingredients"])
    due_list = client.get(
        "/api/admin/costing/ingredients", params={"due_within_days": 30}, headers=headers
    ).json()
    assert not any(i["id"] == flour_id for i in due_list["ingredients"])

    # Search narrows by name.
    search = client.get(
        "/api/admin/costing/ingredients", params={"search": "cost flour"}, headers=headers
    ).json()
    assert all("Cost Flour" in i["name"] for i in search["ingredients"])

    # No price event exists yet — nothing has changed since creation.
    events = client.get(
        f"/api/admin/costing/ingredients/{flour_id}/price-events", headers=headers
    ).json()
    assert events == []

    # A standalone ingredient deletes cleanly; one used by a recipe does
    # not (covered in the recipe tests by construction).
    deletable = make_ingredient("Cost Deletable", "each", 100, 20)
    gone = client.delete(
        f"/api/admin/costing/ingredients/{deletable['id']}", headers=headers
    )
    assert gone.status_code == 204


def test_recipe_validation_and_missing():
    headers = admin_headers()
    product_id = seeded_product_ids(1)[0]
    STATE["ac5_product_id"] = product_id

    assert client.get(
        f"/api/admin/costing/products/{product_id}/recipe", headers=headers
    ).status_code == 404

    dup = client.put(
        f"/api/admin/costing/products/{product_id}/recipe",
        json={
            "expected_batch_yield": 24,
            "items": [
                {"ingredient_id": STATE["flour_id"], "usable_qty": 1000, "unit": "g"},
                {"ingredient_id": STATE["flour_id"], "usable_qty": 500, "unit": "g"},
            ],
        },
        headers=headers,
    )
    assert dup.status_code == 400

    unknown = client.put(
        f"/api/admin/costing/products/{product_id}/recipe",
        json={"expected_batch_yield": 24, "items": [{"ingredient_id": 999999, "usable_qty": 1, "unit": "g"}]},
        headers=headers,
    )
    assert unknown.status_code == 400

    # A line whose unit cannot convert to the ingredient's purchase unit
    # (grams into "each") is refused up front.
    each_thing = make_ingredient("Cost Each Thing", "each", 10, 5)
    unconvertible = client.put(
        f"/api/admin/costing/products/{product_id}/recipe",
        json={"expected_batch_yield": 24, "items": [{"ingredient_id": each_thing["id"], "usable_qty": 100, "unit": "g"}]},
        headers=headers,
    )
    assert unconvertible.status_code == 400


def test_ac5_end_to_end_cost_and_batch_history():
    """The §8 croissant story end to end: €26.40 of flour over 24 expected
    units → €1.10; close the batch at 22 sellable units → €1.20 with the
    variance stored at −8.33 %, and the older record untouched."""
    headers = admin_headers()
    product_id = STATE["ac5_product_id"]
    ensure_overhead_month(CURRENT_MONTH.isoformat(), 800, 160)

    with SessionLocal() as session:
        before_price = session.get(Product, product_id).price
        before_sale = session.get(Product, product_id).sale_price

    recipe = put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "unattended_minutes": 0,
            "expected_batch_yield": 24,
            "batch_overhead_estimate": 0,
            "items": [{"ingredient_id": STATE["flour_id"], "usable_qty": 17600, "unit": "g", "loss_pct": 0}],
        },
    )
    assert recipe["expected_batch_yield"] == 24
    assert recipe["next_reviewed"] == (TODAY + timedelta(days=90)).isoformat()

    # The recipe write itself produced the first cost record.
    first = latest_record_dict(product_id)
    assert first["trigger"] == "recipe_change"
    assert first["is_complete"] and first["currency"] == "eur"
    assert Decimal(str(first["total_cost"])) == Decimal("1.10")
    assert first["breakdown"]["batch_overhead"]["mode"] == "estimated"
    assert first["breakdown"]["yield"] == {"units": 24, "source": "expected", "batch_id": None}
    # The D3 fallback is labelled as itself.
    assert first["breakdown"]["labour"]["rate"] == "20.00"
    assert first["breakdown"]["labour"]["rate_source"] == "default_fallback"
    first_snapshot = record_snapshot(cost_records_for(product_id)[-1])

    # A batch started from the recipe's promise, closed with actuals.
    batch = client.post(
        "/api/admin/costing/batches",
        json={"product_id": product_id},
        headers=headers,
    )
    assert batch.status_code == 201, batch.text
    batch_body = batch.json()
    assert batch_body["expected_yield"] == 24  # defaulted from the recipe
    assert batch_body["batch_overhead_estimated"] == 0
    assert batch_body["status"] == "open"

    closed = client.post(
        f"/api/admin/costing/batches/{batch_body['id']}/close",
        json={"actual_yield": 22, "batch_overhead_actual": 0},
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    closed_body = closed.json()
    assert closed_body["status"] == "closed"
    assert closed_body["yield_variance_pct"] == -8.33
    assert closed_body["closed_at"] is not None
    STATE["ac5_batch_id"] = batch_body["id"]

    # The fresh record uses the actuals — and only the actuals.
    second = latest_record_dict(product_id)
    assert second["trigger"] == "batch_closed"
    assert Decimal(str(second["total_cost"])) == Decimal("1.20")
    assert second["breakdown"]["batch_overhead"]["mode"] == "actual"
    assert second["breakdown"]["yield"]["units"] == 22
    assert second["breakdown"]["yield"]["batch_id"] == batch_body["id"]

    # The first record is history: byte-identical after all of this.
    first_records = [r for r in cost_records_for(product_id) if r.id == first["id"]]
    assert record_snapshot(first_records[0]) == first_snapshot

    # Closed batches are immutable.
    assert client.put(
        f"/api/admin/costing/batches/{batch_body['id']}",
        json={"usage_notes": "too late"},
        headers=headers,
    ).status_code == 409
    assert client.post(
        f"/api/admin/costing/batches/{batch_body['id']}/close",
        json={"actual_yield": 20, "batch_overhead_actual": 0},
        headers=headers,
    ).status_code == 409

    # AC-38: the product's prices never moved.
    with SessionLocal() as session:
        assert session.get(Product, product_id).price == before_price
        assert session.get(Product, product_id).sale_price == before_sale


def test_ac6_batch_overhead_component_and_modes():
    """§10A: €4.40 of batch overhead over 22 units → €0.20 per unit; the
    estimate feeds costing until a genuine close replaces it."""
    headers = admin_headers()
    product_id = seeded_product_ids(6)[1]
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 22,
            "batch_overhead_estimate": 4.40,
            "items": [{"ingredient_id": STATE["flour_id"], "usable_qty": 0.1, "unit": "g", "loss_pct": 0}],
        },
    )
    record = latest_record_dict(product_id)
    assert Decimal(str(record["total_cost"])) == Decimal("0.20")
    overhead = record["breakdown"]["batch_overhead"]
    assert overhead["mode"] == "estimated"
    assert Decimal(overhead["amount"]) == Decimal("4.40")

    # Close at the same figures → actual mode, same component.
    batch = client.post(
        "/api/admin/costing/batches", json={"product_id": product_id}, headers=headers
    ).json()
    client.post(
        f"/api/admin/costing/batches/{batch['id']}/close",
        json={"actual_yield": 22, "batch_overhead_actual": 4.40},
        headers=headers,
    )
    record = latest_record_dict(product_id)
    assert record["breakdown"]["batch_overhead"]["mode"] == "actual"
    assert Decimal(record["breakdown"]["batch_overhead"]["amount"]) == Decimal("4.40")
    assert Decimal(str(record["total_cost"])) == Decimal("0.20")

    # A different actual replaces the estimate — never adds to it.
    batch2 = client.post(
        "/api/admin/costing/batches",
        json={"product_id": product_id, "produced_at": (TODAY + timedelta(days=1)).isoformat()},
        headers=headers,
    ).json()
    client.post(
        f"/api/admin/costing/batches/{batch2['id']}/close",
        json={"actual_yield": 22, "batch_overhead_actual": 2.20},
        headers=headers,
    )
    record = latest_record_dict(product_id)
    assert Decimal(record["breakdown"]["batch_overhead"]["amount"]) == Decimal("2.20")
    assert Decimal(str(record["total_cost"])) == Decimal("0.10")


def test_ac7_fixed_overhead_allocation_and_month_end_actuals():
    """§10B inside a cost: 24 hands-on minutes at €5.00/h allocate €2.00;
    recording month-end actuals changes nothing the forecast promised."""
    headers = admin_headers()
    product_id = seeded_product_ids(6)[2]
    zero_cost = make_ingredient("Cost Zero Filler", "g", 1000, 0)
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 24,
            "expected_batch_yield": 100,
            "batch_overhead_estimate": 0,
            "items": [{"ingredient_id": zero_cost["id"], "usable_qty": 1, "unit": "g", "loss_pct": 0}],
        },
    )
    record = latest_record_dict(product_id)
    fixed = record["breakdown"]["fixed_overhead"]
    assert Decimal(fixed["rate_per_hour"]) == Decimal("5")
    assert Decimal(fixed["allocation"]) == Decimal("2.00")
    assert fixed["month"] == CURRENT_MONTH.isoformat()
    # Labour on the same minutes: 24/60 × €20 = €8.00 → €0.08/unit; fixed
    # €2.00 → €0.02/unit; total €0.10.
    assert Decimal(record["breakdown"]["labour"]["cost"]) == Decimal("8.00")
    assert Decimal(str(record["total_cost"])) == Decimal("0.10")

    # Month-end actuals: the write succeeds, nothing recomputes.
    months = client.get("/api/admin/costing/overhead-months", headers=headers).json()
    month_id = next(m["id"] for m in months if m["month"] == CURRENT_MONTH.isoformat())
    records_before = len(cost_records_for(product_id))
    updated = client.put(
        f"/api/admin/costing/overhead-months/{month_id}",
        json={"actual_total": 850, "actual_hours": 170, "variance_note": "heating"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["actual_total"] == 850
    assert updated.json()["forecast_hours"] == 160  # the forecast stands
    assert len(cost_records_for(product_id)) == records_before

    # But a forecast change is a costing change: every costed product
    # gets a fresh record.
    bumped = client.put(
        f"/api/admin/costing/overhead-months/{month_id}",
        json={"fixed_overhead_total": 800, "forecast_hours": 80},
        headers=headers,
    )
    assert bumped.status_code == 200
    assert len(cost_records_for(product_id)) == records_before + 1
    assert latest_record_dict(product_id)["trigger"] == "overhead_month_change"
    assert latest_record_dict(product_id)["breakdown"]["fixed_overhead"]["rate_per_hour"] == "10"
    # Restore the shared month so later tests see €5.00/h.
    client.put(
        f"/api/admin/costing/overhead-months/{month_id}",
        json={"fixed_overhead_total": 800, "forecast_hours": 160},
        headers=headers,
    )


def test_ac8_overhead_overlap_warnings_never_block():
    headers = admin_headers()
    product_id = seeded_product_ids(6)[3]
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 10,
            "batch_overhead_estimate": 3,
            "items": [{"ingredient_id": STATE["flour_id"], "usable_qty": 1, "unit": "g", "loss_pct": 0}],
        },
    )
    batch = client.post(
        "/api/admin/costing/batches",
        json={"product_id": product_id, "overhead_categories": "Insurance\nPackaging"},
        headers=headers,
    ).json()

    months = client.get("/api/admin/costing/overhead-months", headers=headers).json()
    month_id = next(m["id"] for m in months if m["month"] == CURRENT_MONTH.isoformat())
    response = client.put(
        f"/api/admin/costing/overhead-months/{month_id}",
        json={"included_categories": "Rent\nInsurance"},
        headers=headers,
    )
    assert response.status_code == 200  # the warning never blocks the write
    warnings = response.json()["overlap_warnings"]
    match = next(
        w for w in warnings if w["batch_id"] == batch["id"]
    )
    assert match["overlapping_categories"] == ["insurance"]

    review = client.get("/api/admin/costing/cost-review", headers=headers).json()
    assert any(
        w["batch_id"] == batch["id"] and "insurance" in w["overlapping_categories"]
        for w in review["overlap_warnings"]
    )


def test_density_based_conversion():
    """A mass line against a liquid purchase unit crosses through the
    ingredient's recorded density — 515 g of cream at 1.03 g/ml is 500 ml
    → 0.5 l at €1.20/l → €0.60 exactly."""
    headers = admin_headers()
    product_id = seeded_product_ids(6)[4]
    cream = make_ingredient(
        "Cost Cream", "l", 1, 1.20, density_g_per_ml=1.03, currency="eur"
    )
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 1,
            "batch_overhead_estimate": 0,
            "items": [{"ingredient_id": cream["id"], "usable_qty": 515, "unit": "g", "loss_pct": 0}],
        },
    )
    record = latest_record_dict(product_id)
    line = record["breakdown"]["lines"][0]
    assert Decimal(line["qty_in_purchase_unit"]) == Decimal("0.5")
    assert Decimal(line["line_cost"]) == Decimal("0.60")
    assert Decimal(str(record["total_cost"])) == Decimal("0.60")

    # A density that goes away later turns the same line incomplete — the
    # write-time guard only sees what is true at recipe time.
    was_dense = make_ingredient("Cost Cream Lost Density", "l", 1, 1.20, density_g_per_ml=1.03)
    product2 = seeded_product_ids(20)[5]
    put_recipe(
        product2,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 1,
            "items": [{"ingredient_id": was_dense["id"], "usable_qty": 515, "unit": "g", "loss_pct": 0}],
        },
    )
    assert latest_record_dict(product2)["is_complete"]
    stripped = client.put(
        f"/api/admin/costing/ingredients/{was_dense['id']}",
        json={"density_g_per_ml": None},
        headers=headers,
    )
    assert stripped.status_code == 200 and stripped.json()["density_g_per_ml"] is None
    record2 = latest_record_dict(product2)
    assert not record2["is_complete"]
    assert record2["missing"] == ["unit_mismatch:Cost Cream Lost Density"]
    # A recipe whose line never converted is refused at write time.
    never = make_ingredient("Cost Cream Never Dense", "l", 1, 1.20)
    refused = client.put(
        f"/api/admin/costing/products/{product2}/recipe",
        json={
            "hands_on_minutes": 0,
            "expected_batch_yield": 1,
            "items": [{"ingredient_id": never["id"], "usable_qty": 515, "unit": "g", "loss_pct": 0}],
        },
        headers=headers,
    )
    assert refused.status_code == 400


def test_per_line_wastage():
    """D6: loss lives on each line — two identically-priced flours at 5 %
    and 0 % buy different purchase quantities in one recipe (an ingredient
    appears once per recipe, so the contrast needs a twin)."""
    product_id = seeded_product_ids(20)[6]
    twin = make_ingredient("Cost Flour Twin", "kg", 25, 37.50)
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 10,
            "items": [
                {"ingredient_id": STATE["flour_id"], "usable_qty": 1000, "unit": "g", "loss_pct": 5},
                {"ingredient_id": twin["id"], "usable_qty": 1000, "unit": "g", "loss_pct": 0},
            ],
        },
    )
    record = latest_record_dict(product_id)
    lines = record["breakdown"]["lines"]
    assert lines[0]["purchase_qty"].startswith("1052.6315789")
    assert lines[0]["line_cost"].startswith("1.5789")
    assert Decimal(lines[1]["purchase_qty"]) == Decimal("1000")
    assert Decimal(lines[1]["line_cost"]) == Decimal("1.5")
    # One rounding at the very end: (1.5789… + 1.5) / 10 = 0.30789… → €0.31.
    assert Decimal(str(record["total_cost"])) == Decimal("0.31")


def test_ingredient_price_change_recalculates_ac37():
    """A price edit appends the price event and the dependent cost records
    in one transaction — immediately, whatever the review dates say."""
    headers = admin_headers()
    product_id = seeded_product_ids(20)[7]

    sugar = make_ingredient("Cost Sugar", "kg", 10, 20.00)  # €2.00/kg
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 10,
            "items": [{"ingredient_id": sugar["id"], "usable_qty": 2000, "unit": "g", "loss_pct": 0}],
        },
    )
    assert Decimal(str(latest_record_dict(product_id)["total_cost"])) == Decimal("0.40")

    records_before = len(cost_records_for(product_id))
    changed = client.put(
        f"/api/admin/costing/ingredients/{sugar['id']}",
        json={"purchase_price": 25.00},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["effective_cost_per_unit"] == 2.5

    # The price event carries full provenance.
    events = client.get(
        f"/api/admin/costing/ingredients/{sugar['id']}/price-events", headers=headers
    ).json()
    assert len(events) == 1
    event = events[0]
    assert event["prev_purchase_price"] == 20.0
    assert event["new_purchase_price"] == 25.0
    assert event["prev_effective_cost_per_unit"] == 2.0
    assert event["new_effective_cost_per_unit"] == 2.5
    assert event["admin_name"] and event["effective_from"] == TODAY.isoformat()

    # The dependent cost was recomputed — same transaction, new record.
    records = cost_records_for(product_id)
    assert len(records) == records_before + 1
    assert records[-1].trigger == "ingredient_price_change"
    assert Decimal(str(records[-1].total_cost)) == Decimal("0.50")

    # A no-op edit writes neither event nor record.
    noop_count = len(records)
    client.put(
        f"/api/admin/costing/ingredients/{sugar['id']}",
        json={"purchase_price": 25.00},
        headers=headers,
    )
    assert len(cost_records_for(product_id)) == noop_count
    assert len(client.get(
        f"/api/admin/costing/ingredients/{sugar['id']}/price-events", headers=headers
    ).json()) == 1


def test_unit_change_records_event_and_preserves_history():
    headers = admin_headers()
    product_id = seeded_product_ids(20)[8]
    grain = make_ingredient("Cost Grain", "kg", 1, 4.00)  # €4.00/kg
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 4,
            "items": [{"ingredient_id": grain["id"], "usable_qty": 1000, "unit": "g", "loss_pct": 0}],
        },
    )
    old = latest_record_dict(product_id)
    assert Decimal(str(old["total_cost"])) == Decimal("1.00")
    old_breakdown = old["breakdown"]

    # Repurchase in grams: same money, new per-unit basis.
    changed = client.put(
        f"/api/admin/costing/ingredients/{grain['id']}",
        json={"unit": "g", "purchase_qty": 1000, "purchase_price": 4.00},
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["unit"] == "g"
    assert changed.json()["effective_cost_per_unit"] == 0.004

    events = client.get(
        f"/api/admin/costing/ingredients/{grain['id']}/price-events", headers=headers
    ).json()
    assert events[0]["prev_unit"] == "kg" and events[0]["new_unit"] == "g"
    assert events[0]["prev_purchase_qty"] == 1.0
    assert events[0]["new_purchase_qty"] == 1000.0

    # The old record still speaks in kilograms — history keeps its units.
    records = cost_records_for(product_id)
    assert records[0].breakdown["lines"][0]["ingredient_unit"] == "kg"
    assert records[0].breakdown == old_breakdown
    # The fresh one converts under the new basis and lands on the same
    # money — a unit change is a re-expression, not a repricing.
    assert Decimal(str(latest_record_dict(product_id)["total_cost"])) == Decimal("1.00")


def test_currency_mismatch_is_not_computable_a2():
    headers = admin_headers()
    # Since the cutover the costing book and the prices both speak EUR, so
    # the AC-5 record computes real margins out of the box — the EUR costing
    # the shop actually runs on.
    settings = get_settings()
    assert settings.stripe_currency == "eur"
    review = client.get("/api/admin/costing/cost-review", headers=headers).json()
    entry = next(e for e in review["overdue"] + review["upcoming"]
                 if e["product_id"] == STATE["ac5_product_id"])
    margins = entry["cost"]["margins"]["regular"]
    assert margins["status"] in ("ok", "flagged")
    assert Decimal(str(margins["min_net"])) == costing.round_cents(
        costing.min_net_price(Decimal("1.20"))
    )

    detail = client.get(
        f"/api/admin/products/{STATE['ac5_product_id']}", headers=headers
    ).json()
    assert detail["cost"]["margins"]["regular"]["status"] in ("ok", "flagged")
    assert margins["net_price"] == detail["price"]

    # A relapse to the old charge currency must not reprice the book: EUR
    # costs against USD prices are not computable — margins refuse, naming
    # both currencies (restored before the test ends).
    original = settings.stripe_currency
    try:
        settings.stripe_currency = "usd"
        review = client.get("/api/admin/costing/cost-review", headers=headers).json()
        entry = next(e for e in review["overdue"] + review["upcoming"]
                     if e["product_id"] == STATE["ac5_product_id"])
        assert entry["cost_status"] == "not_computable"
        regular = entry["cost"]["margins"]["regular"]
        assert regular["status"] == "not_computable"
        assert regular["reason"]["code"] == "currency_mismatch"
        assert regular["reason"]["cost_currency"] == "eur"
        assert regular["reason"]["price_currency"] == "usd"

        # On the admin card the same state shows as not_computable.
        detail = client.get(
            f"/api/admin/products/{STATE['ac5_product_id']}", headers=headers
        ).json()
        assert detail["cost_status"] == "not_computable"
        assert detail["cost"]["margins"]["regular"]["reason"]["code"] == "currency_mismatch"
    finally:
        settings.stripe_currency = original


def test_the_costing_book_refuses_new_non_eur_writes():
    """EUR-only since the cutover: ingredients, overhead months and labour
    rates can no longer be written in any other currency — the API refuses
    with the book's own message rather than letting a second currency in.
    (Existing rows keep their stored currency; only new writes are gated.)"""
    headers = admin_headers()

    ingredient = client.post(
        "/api/admin/costing/ingredients",
        json={"name": "Dollar sugar", "unit": "kg", "purchase_qty": 1,
              "purchase_price": 2.00, "currency": "usd"},
        headers=headers,
    )
    assert ingredient.status_code == 422
    assert "EUR-only" in ingredient.json()["detail"][0]["msg"]

    month = client.post(
        "/api/admin/costing/overhead-months",
        json={"month": "2027-01-01", "fixed_overhead_total": 800.0,
              "forecast_hours": 160, "currency": "usd"},
        headers=headers,
    )
    assert month.status_code == 422
    assert "EUR-only" in month.json()["detail"][0]["msg"]

    rate = client.put(
        "/api/admin/costing/labour-rate",
        json={"valid_from": "2027-01-01", "rate": 22.00, "currency": "usd"},
        headers=headers,
    )
    assert rate.status_code == 422
    assert "EUR-only" in rate.json()["detail"][0]["msg"]

    # and the default write needs no currency at all — euro is implied
    made = make_ingredient("Implicit euro flour", "kg", 1, 1.50)
    assert made["currency"] == "eur"


def test_labour_rate_effective_dating():
    """A5/D3: the default fallback until an effective-dated row exists;
    every change appends; snapshots never rewrite."""
    headers = admin_headers()
    status = client.get("/api/admin/costing/labour-rate", headers=headers).json()
    assert status["source"] == "default_fallback"
    assert status["rate"] == 20.0
    assert status["history"] == []

    product_id = seeded_product_ids(20)[10]
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 60,
            "expected_batch_yield": 10,
            "items": [{"ingredient_id": STATE["flour_id"], "usable_qty": 1, "unit": "g", "loss_pct": 0}],
        },
    )
    baseline = latest_record_dict(product_id)
    assert baseline["breakdown"]["labour"]["rate_source"] == "default_fallback"
    assert baseline["breakdown"]["labour"]["rate"] == "20.00"
    baseline_breakdown = baseline["breakdown"]

    # A rate effective today: costing uses it from now on.
    set_today = client.put(
        "/api/admin/costing/labour-rate",
        json={"valid_from": TODAY.isoformat(), "rate": 22.00, "source": "bakehouse raise"},
        headers=headers,
    )
    assert set_today.status_code == 201, set_today.text
    assert set_today.json()["source"] == "table"
    assert set_today.json()["rate"] == 22.0
    assert len(set_today.json()["history"]) == 1

    fresh = latest_record_dict(product_id)
    assert fresh["trigger"] == "labour_rate_change"
    assert Decimal(fresh["breakdown"]["labour"]["rate"]) == Decimal("22")
    assert fresh["breakdown"]["labour"]["rate_source"] == "table"
    assert fresh["breakdown"]["labour"]["valid_from"] == TODAY.isoformat()
    # 60 min at €22 = €22.00 over 10 units.
    assert Decimal(fresh["breakdown"]["labour"]["cost"]) == Decimal("22")

    # The baseline snapshot kept the fallback — history did not rewrite.
    records = cost_records_for(product_id)
    assert records[0].breakdown == baseline_breakdown

    # A future-dated rate changes nothing yet.
    future = TODAY + timedelta(days=30)
    records_before = len(cost_records_for(product_id))
    set_future = client.put(
        "/api/admin/costing/labour-rate",
        json={"valid_from": future.isoformat(), "rate": 25.00},
        headers=headers,
    )
    assert set_future.status_code == 201
    assert len(cost_records_for(product_id)) == records_before
    status = client.get("/api/admin/costing/labour-rate", headers=headers).json()
    assert status["rate"] == 22.0  # today still resolves to the earlier row
    assert len(status["history"]) == 2

    # A duplicate effective date is refused — rates are append-only.
    assert client.put(
        "/api/admin/costing/labour-rate",
        json={"valid_from": future.isoformat(), "rate": 30.00},
        headers=headers,
    ).status_code == 409


def test_intervals_via_api_ac36():
    headers = admin_headers()
    cat = client.post(
        "/api/admin/costing/ingredient-categories",
        json={"name": "Cost Spices", "review_interval_days": 30},
        headers=headers,
    ).json()
    spice = make_ingredient(
        "Cost Cinnamon", "g", 500, 6.00, category_id=cat["id"]
    )
    # Category interval applies (no override): 2026-08-01 + 30 → 2026-08-31.
    reviewed = client.post(
        f"/api/admin/costing/ingredients/{spice['id']}/review",
        json={"reviewed_on": "2026-08-01"},
        headers=headers,
    ).json()
    assert reviewed["next_reviewed"] == "2026-08-31"
    assert reviewed["effective_interval_days"] == 30

    # A per-ingredient override wins.
    overridden = client.put(
        f"/api/admin/costing/ingredients/{spice['id']}",
        json={"review_interval_days": 7},
        headers=headers,
    ).json()
    assert overridden["effective_interval_days"] == 7
    assert overridden["next_reviewed"] == "2026-08-08"  # re-derived from last_reviewed

    # Removing the override falls back to the category — and a category
    # change moves every member without an override.
    client.put(
        f"/api/admin/costing/ingredients/{spice['id']}",
        json={"review_interval_days": None},
        headers=headers,
    )
    client.put(
        f"/api/admin/costing/ingredient-categories/{cat['id']}",
        json={"review_interval_days": 10},
        headers=headers,
    )
    spice_now = client.get(
        f"/api/admin/costing/ingredients/{spice['id']}", headers=headers
    ).json()
    assert spice_now["effective_interval_days"] == 10
    assert spice_now["next_reviewed"] == "2026-08-11"


def test_cost_review_desk():
    headers = admin_headers()
    review = client.get("/api/admin/costing/cost-review", headers=headers).json()
    assert review["window_days"] == 14
    assert isinstance(review["overdue"], list) and isinstance(review["upcoming"], list)

    # The backdated-flour recipes are on the overdue rail with the reason named.
    flour_users = {e["product_id"] for e in review["overdue"]}
    assert STATE["ac5_product_id"] in flour_users

    # A fresh review inside the window lands on the upcoming rail.
    fresh_cat = client.post(
        "/api/admin/costing/ingredient-categories",
        json={"name": "Cost Fresh"},
        headers=headers,
    ).json()
    fresh = make_ingredient("Cost Fresh Sugar", "kg", 10, 20, category_id=fresh_cat["id"])
    product_id = seeded_product_ids(20)[11]
    put_recipe(
        product_id,
        {
            "hands_on_minutes": 0,
            "expected_batch_yield": 10,
            "items": [{"ingredient_id": fresh["id"], "usable_qty": 10, "unit": "g", "loss_pct": 0}],
        },
    )
    reviewed = client.post(
        f"/api/admin/costing/ingredients/{fresh['id']}/review",
        json={},
        headers=headers,
    ).json()
    assert reviewed["overdue"] is False
    # The recipe itself is due in 90 days; nothing falls inside 14 yet.
    review = client.get("/api/admin/costing/cost-review", headers=headers).json()
    assert product_id not in {e["product_id"] for e in review["overdue"]}


def test_regression_admin_card_and_public_catalog():
    """Phase 1/1.1 behaviour intact: public cards carry no costing (or
    admin VAT) fields; admin cards carry the derived cost status; the
    cost-review rail filters the admin product list."""
    public = client.get("/api/products", params={"per_page": 5}).json()["products"][0]
    assert "cost_status" not in public
    assert "vat_category" not in public and "vat_rate_preview" not in public

    headers = admin_headers()
    # A product with no recipe at all is not costed — and not on the rail
    # (costing hasn't started; that is a state, not a review need).
    bare = seeded_product_ids(25)[-1]
    detail = client.get(f"/api/admin/products/{bare}", headers=headers).json()
    assert detail["cost_status"] == "not_costed"
    assert detail["cost"] is None

    listed = client.get(
        "/api/admin/products", params={"needs_cost_review": True}, headers=headers
    ).json()
    listed_ids = {p["id"] for p in listed["products"]}
    assert STATE["ac5_product_id"] in listed_ids  # overdue flour inputs
    assert bare not in listed_ids  # no recipe → not on the rail


def test_no_global_wastage_constant():
    """D6: wastage is per recipe line only. The module must never grow a
    shop-wide loss percentage."""
    import inspect

    source = inspect.getsource(costing)
    for banned in ("GLOBAL_LOSS", "UNIVERSAL_LOSS", "SHOP_LOSS", "DEFAULT_LOSS"):
        assert banned not in source, banned
    # And the recipe line's default loss is an explicit zero.
    from app.models import RecipeItem as _Item

    assert float(_Item.__table__.c.loss_pct.default.arg) == 0.0


# ---------------------------------------------------------------------------
# MAJOR-1 regression — re-upserting a recipe that retains a line
#
# The Phase 2 review found the upsert deleting the old lines and inserting
# the replacements in one flush; SQLAlchemy orders INSERTs before DELETEs,
# so a retained (recipe_id, ingredient_id) pair collided with its own
# still-pending predecessor and the edit died with a 500. These tests
# prove every editing shape now works and fails safe.
# ---------------------------------------------------------------------------


def _major1_recipe(product_id: int, *lines: dict) -> dict:
    payload = {
        "hands_on_minutes": 0,
        "expected_batch_yield": 10,
        "batch_overhead_estimate": 0,
        "items": [dict(line) for line in lines],
    }
    return put_recipe(product_id, payload)


def _major1_lines(product_id: int) -> list[dict]:
    """The recipe's lines read straight back from the database."""
    with SessionLocal() as session:
        recipe = session.scalar(select(Recipe).where(Recipe.product_id == product_id))
        assert recipe is not None, "the recipe shell must survive every edit"
        return [
            {"ingredient_id": item.ingredient_id, "usable_qty": float(item.usable_qty), "unit": item.unit}
            for item in session.scalars(
                select(RecipeItem)
                .where(RecipeItem.recipe_id == recipe.id)
                .order_by(RecipeItem.ingredient_id)
            )
        ]


def test_major1_first_recipe_create_succeeds():
    # Self-sufficient inputs so this section also passes under -k major1:
    # the €800/160 h month (€5.00/h) and two priced ingredients.
    ensure_overhead_month(CURRENT_MONTH.isoformat(), 800, 160)
    product_id = seeded_product_ids(25)[12]
    STATE["m1_product_id"] = product_id
    # €4.00/kg butter: 10 kg purchased at €40.00.
    STATE["m1_butter"] = make_ingredient("Cost Butter", "kg", 10, 40.00)
    butter_id = STATE["m1_butter"]["id"]

    recipe = _major1_recipe(product_id, {"ingredient_id": butter_id, "usable_qty": 500, "unit": "g", "loss_pct": 0})

    assert [i["ingredient_id"] for i in recipe["items"]] == [butter_id]
    assert _major1_lines(product_id) == [
        {"ingredient_id": butter_id, "usable_qty": 500.0, "unit": "g"}
    ]
    # 0.5 kg × €4.00 over a 10-unit batch → €0.20 per unit.
    record = latest_record_dict(product_id)
    assert record["is_complete"] and Decimal(str(record["total_cost"])) == Decimal("0.20")


def test_major1_update_retaining_ingredient_succeeds():
    """The defect itself: the very same line re-submitted against a recipe
    that already carries it (this PUT was the 500)."""
    product_id = STATE["m1_product_id"]
    butter_id = STATE["m1_butter"]["id"]
    records_before = [record_snapshot(r) for r in cost_records_for(product_id)]

    recipe = _major1_recipe(product_id, {"ingredient_id": butter_id, "usable_qty": 500, "unit": "g", "loss_pct": 0})

    assert [i["ingredient_id"] for i in recipe["items"]] == [butter_id]
    assert _major1_lines(product_id) == [
        {"ingredient_id": butter_id, "usable_qty": 500.0, "unit": "g"}
    ]
    # History kept, one fresh record appended by the same write.
    records = cost_records_for(product_id)
    assert [record_snapshot(r) for r in records[:-1]] == records_before
    assert Decimal(str(records[-1].total_cost)) == Decimal("0.20")


def test_major1_retained_quantity_change_recalculates():
    product_id = STATE["m1_product_id"]
    butter_id = STATE["m1_butter"]["id"]

    _major1_recipe(product_id, {"ingredient_id": butter_id, "usable_qty": 1500, "unit": "g", "loss_pct": 0})

    record = latest_record_dict(product_id)
    line = record["breakdown"]["lines"][0]
    assert Decimal(line["qty_in_purchase_unit"]) == Decimal("1.5")
    assert Decimal(line["line_cost"]) == Decimal("6")
    # 1.5 kg × €4.00 = €6.00 over 10 units → €0.60.
    assert Decimal(str(record["total_cost"])) == Decimal("0.60")
    assert _major1_lines(product_id)[0]["usable_qty"] == 1500.0


def test_major1_replacing_ingredient_succeeds():
    product_id = STATE["m1_product_id"]
    # €2.00/kg sugar: 10 kg purchased at €20.00.
    STATE["m1_sugar"] = make_ingredient("Cost Cane Sugar", "kg", 10, 20.00)
    sugar_id = STATE["m1_sugar"]["id"]

    recipe = _major1_recipe(product_id, {"ingredient_id": sugar_id, "usable_qty": 1500, "unit": "g", "loss_pct": 0})

    assert [i["ingredient_id"] for i in recipe["items"]] == [sugar_id]
    # 1.5 kg × €2.00 = €3.00 over 10 units → €0.30.
    record = latest_record_dict(product_id)
    assert record["is_complete"] and Decimal(str(record["total_cost"])) == Decimal("0.30")


def test_major1_clearing_lines_documented_behaviour():
    """RecipeUpsertRequest documents `items` may be empty (clearing a
    recipe): the shell stays, the lines go, and the follow-up cost record
    is honestly incomplete with the reason named."""
    headers = admin_headers()
    product_id = STATE["m1_product_id"]

    recipe = _major1_recipe(product_id)

    assert recipe["items"] == []
    assert _major1_lines(product_id) == []
    record = latest_record_dict(product_id)
    assert record["is_complete"] is False
    assert record["missing"] == ["no_recipe_lines"]
    # The shell is still there and still editable.
    assert client.get(
        f"/api/admin/costing/products/{product_id}/recipe", headers=headers
    ).status_code == 200


def test_major1_failed_update_rolls_back_cleanly():
    """A failure after the replacement lines were flushed (here: the cost
    recalculation blowing up) must leave the recipe and its cost history
    exactly as they were — no partial edit, no orphan record. The app's
    request middleware turns the error into a clean 500."""
    from unittest.mock import patch

    headers = admin_headers()
    product_id = STATE["m1_product_id"]
    butter_id = STATE["m1_butter"]["id"]
    sugar_id = STATE["m1_sugar"]["id"]
    _major1_recipe(product_id, {"ingredient_id": butter_id, "usable_qty": 500, "unit": "g", "loss_pct": 0})
    lines_before = _major1_lines(product_id)
    records_before = [record_snapshot(r) for r in cost_records_for(product_id)]

    with patch.object(
        costing, "refresh_cost", side_effect=RuntimeError("simulated failure mid-update")
    ):
        failed = client.put(
            f"/api/admin/costing/products/{product_id}/recipe",
            json={
                "hands_on_minutes": 0,
                "expected_batch_yield": 20,
                "batch_overhead_estimate": 0,
                "items": [{"ingredient_id": sugar_id, "usable_qty": 1500, "unit": "g", "loss_pct": 0}],
            },
            headers=headers,
        )
    assert failed.status_code == 500

    # Nothing from the failed attempt survived: same lines, same history.
    assert _major1_lines(product_id) == lines_before
    assert [record_snapshot(r) for r in cost_records_for(product_id)] == records_before
    # …and the very next edit still works.
    _major1_recipe(product_id, {"ingredient_id": butter_id, "usable_qty": 500, "unit": "g", "loss_pct": 0})


def test_major1_repeated_updates_create_no_duplicates():
    product_id = STATE["m1_product_id"]
    butter_line = {"ingredient_id": STATE["m1_butter"]["id"], "usable_qty": 500, "unit": "g", "loss_pct": 0}
    sugar_line = {"ingredient_id": STATE["m1_sugar"]["id"], "usable_qty": 1500, "unit": "g", "loss_pct": 0}
    single, double = [butter_line], [butter_line, sugar_line]

    for shape in (single, double, single, single, double, double, single):
        _major1_recipe(product_id, *shape)
        lines = _major1_lines(product_id)
        assert len(lines) == len(shape)
        assert len({line["ingredient_id"] for line in lines}) == len(lines)

    # €0.50/unit on the two-line recipe (2.00 + 3.00 over 10).
    _major1_recipe(product_id, butter_line, sugar_line)
    assert Decimal(str(latest_record_dict(product_id)["total_cost"])) == Decimal("0.50")
