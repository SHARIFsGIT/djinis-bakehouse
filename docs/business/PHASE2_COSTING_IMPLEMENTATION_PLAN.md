# Djini's Bakehouse — Phase 2 Implementation Plan: Costing Foundation + Cost Review (Final, v2)

**Type:** Implementation plan — documentation only. No code, tests, database, configuration or production has been touched; nothing committed, pushed or deployed.
**Date:** 2026-11-05 (finalized after owner review of v1 of this plan)
**Baseline:** spec v1.2 (approved), commit `e5ab209` (v3.14.0, deployed) **plus the uncommitted, independently reviewed Phase 1 + 1.1 working tree** (11 modified files + untracked `docs/`, `Temp/`). Phase 2 builds on that tree and discards, resets, overwrites or reformats nothing in it.
**Phase 2 scope (v1.2 §33):** `costing.py` + §21 tables (ingredients, categories, recipes, batches, overhead); provenance; review scheduling (90-day default, category intervals, immediate recalc-on-price-change, backend). Gate: **AC-2, 3, 4, 5, 6, 7, 8, 9, 36, 37, 38**.

Legend — every statement is tagged:

- **[R]** requirement directly stated in v1.2 (section cited)
- **[F]** existing implementation fact (file:line cited)
- **[A]** owner-approved decision (§1, approved 2026-11-05)
- **[P]** proposed technical decision (mine, awaiting review with this document)

---

## 1. Owner decisions (approved 2026-11-05)

The five open questions of plan v1 are decided. Each requirement from the approval is mapped to its design consequence; the rest of this document implements them.

### A1 — Recipe-level estimated batch overhead (spec gap G1)

| Requirement | Design consequence |
|---|---|
| Optional recipe-level estimated batch overhead | `recipes.batch_overhead_estimate Numeric(10,2) NOT NULL DEFAULT 0` — 0 means "none estimated" [§4.3] |
| Clearly an estimate, never an actual | Every cost record's breakdown stores `batch_overhead: {mode: "estimated"\|"actual", amount, source}` — the mode is frozen per record; the estimate never appears under an "actual" label [§6.3] |
| Source/assumption, confidence, review date | Recipe-level provenance columns cover it (A3); the breakdown snapshot restates the values used [§4.3] |
| Once an actual is recorded, use the actual for that batch | Batch close requires `actual_yield` and `batch_overhead_actual`; the closed batch carries its own actuals permanently [§4.6] |
| Never silently overwrite/relabel historical estimates as actuals | Append-only cost records: a record computed on the estimate keeps `mode:"estimated"` forever; later records switch source, earlier ones are untouched [§11] |
| No double-counting across recipe estimate, batch-specific overhead, fixed monthly | The recipe estimate and batch-specific overhead are **one ledger** (batch-level), entering the cost **exactly once, from exactly one source**: latest closed batch's actual if one exists, else the recipe estimate — never both, never summed. Fixed monthly overhead is the separate second ledger; category overlap between the two ledgers raises the AC-8 warning. Labour is minutes-based and excluded from both ledgers (unattended time is never labour cost, §9) [§6.3, §12] |
| Precise estimate↔actual interaction | For product costing: no closed batch for the product → `estimate / expected_batch_yield`, `mode:"estimated"`; ≥1 closed batch → latest closed batch's `batch_overhead_actual / actual_yield`, `mode:"actual"` ("latest" = greatest `produced_at`, tiebreak greatest `id`). Actuals replace estimates for **future** costings only (AC-6); past records keep their figures [§6.3] |

### A2 — Currency mismatch → `not_computable`

| Requirement | Design consequence |
|---|---|
| Never automatic FX conversion | No exchange-rate code exists anywhere in the codebase [F] and none is added; costing never converts [R §1 "no exchange rates"] |
| Never compare EUR costs with USD prices | `evaluate_margins` returns `not_computable` unless `cost_record.currency == shop active currency`; comparison is skipped, not converted |
| Machine-readable reason | `cost_status="not_computable"` + `margin_reasons: [{code: "currency_mismatch", cost_currency: "eur", price_currency: "usd"}]` on the admin cost summary [§8] |
| Original currency preserved on every relevant record | `ingredients.currency`, `ingredient_price_events.currency` (from/to snapshots), `overhead_months.currency`, `labour_rates.currency`, `product_cost_records.currency` (NULL only for mixed-input incomplete records, which carry the mismatch in `missing`). Product prices read in the shop's active currency; orders keep their own per-order currency [F: models.py:392] |
| Re-evaluate computability only when currencies match | Computability is **derived at read**, never stored — when the cutover flips the active currency to EUR, existing EUR cost records become computable automatically, with no back-fill job and no rewritten rows |

### A3 — Provenance and units

| Requirement | Design consequence |
|---|---|
| Purchase-price provenance on each price event | `ingredient_price_events` snapshots `source`, `confidence`, `is_estimate`, `currency`, `unit` alongside the price figures [§4.2] |
| Recipe-level provenance for recipe assumptions | `recipes` carries `source / confidence / is_estimate / last_reviewed / next_reviewed`; one review covers lines + labour + yield + overhead estimate [§4.3] |
| Explicit validated units and conversion rules | Unit vocabulary, dimensions, permitted conversions and rejections defined in `costing.py` as data, validated at recipe/ingredient save [§6.2] |
| Supported units | **Mass:** `mg`, `g`, `kg` · **Volume:** `ml`, `l` · **Count:** `each` (alias `piece`, `pcs`, `item`). Case-insensitive lookup; canonical form stored. The alias table is a single constant, trivially extendable |
| Permitted conversions | Same dimension only, exact Decimal factors (`1 kg = 1000 g = 1 000 000 mg`; `1 l = 1000 ml`; count units are aliases of `each`, no count↔count factors) |
| Rejection of incompatible units | Cross-dimension (e.g. `g` line against an `each` ingredient) → `MismatchedUnitsError` → 400 at save with both units named; costing re-validates as backstop |
| No silent density conversion | Mass↔volume only via an explicitly recorded, ingredient-specific `ingredients.density_g_per_ml Numeric(10,4) NULL`; absent → refusal with reason `density_not_recorded` (recipe save and costing both refuse). Count↔anything never converts — the owner records the purchase in the dimension the recipe uses |
| Decimal precision & rounding | §12: Decimal end-to-end; factors exact; one half-up cent quantization at the final per-unit total; intermediates unrounded (AC-4's 0.7895/1.5789) |
| Purchase → recipe-use conversion | `effective_cost_per_unit` is per `ingredients.unit`; a line quantity converts to that unit (same dimension or recorded density), then `line_cost = qty × effective_cost_per_unit` at full precision |
| Unit changes vs history | An ingredient `unit` change re-derives the effective rate and therefore writes a price event (unit in from/to snapshot). A recipe-line unit change takes effect via recipe upsert → new cost record. Historical records never recompute; their breakdowns carry the units and figures actually used |

### A4 — Admin UI stays API-only in Phase 2

Phase 2 ships **no frontend**: no routes, no components, no new files under `frontend/` (verified by diff at review). All costing endpoints are admin-only (`CurrentAdmin`, 403 otherwise — tested), schema-validated, and every price-affecting write lands in an audit ledger. The limitation is documented here and in the module docstrings: until Phase 5, ingredient/recipe entry happens through the admin API (curl / scripts / OpenAPI docs at `/api/docs` in dev). No partial UI, no Phase 5 scope pulled forward.

### A5 — Labour rate: centrally managed, versioned

| Requirement | Design consequence |
|---|---|
| No scattered hard-coded rate | One tiny effective-dated table `labour_rates (valid_from Date PK, rate Numeric(10,2), currency String(3) default "eur", source Text?)`, resolved by `labour_rate_on(db, date)` → newest row `valid_from ≤ date`, else the single named fallback `DEFAULT_LABOUR_RATE = Decimal("20.00")` defined once in `costing.py` (D3). The table starts empty [task §4]; the D3 default serves until the owner records a row |
| Rate/version recorded per calculation | Every cost record's breakdown stores `labour: {minutes, rate: "20.00", currency: "eur", rate_source: "table"\|"d3-default", valid_from}` — the exact figure the calculation used |
| Later change never rewrites history | Rate rows are append-only (a change is a new dated row — the `vat_rates` effective-dating pattern [F: models.py:433-444, seed.py:53-61]); historical records keep their snapshots |
| Secured configuration mechanism | `PUT /api/admin/costing/labour-rate` (`CurrentAdmin`) records a new dated row; no DELETE/UPDATE endpoint. This is the whole "settings framework" — one endpoint, one table, nothing general |
| No unnecessary framework | No generic key-value settings store, no env-only knob needing a deploy, no UI |

---

## 2. Scope and boundaries

**In scope [R §33]:** the eight spec tables' subject matter (ingredients, categories, recipes, batches, overhead months), provenance, review scheduling, immediate recalculation, the costing module, admin API, cost history.

**Prohibited in Phase 2 (task §4, all honored by this design):**

| Prohibition | How the design respects it |
|---|---|
| No recommended selling prices; no charm pricing | `min_net_price`/`margin` exist only as pure functions feeding the derived `cost_status`; no recommendation endpoint, no price output anywhere |
| No changes to product prices / sale prices | No Phase 2 write path touches `products.price`/`sale_price`; AC-38 asserts byte-equality after recalculation |
| No price approval, no benchmarking, no quotations | `price_history`, `benchmark_entries`, quote tables are Phase 3/6 — absent here |
| No checkout / Stripe / order-pricing changes | `orders.py`, `payments.py`, `stripe_gateway.py` untouched (file list, §15) |
| No VAT classification / tax-treatment changes | `vat.py` untouched; `tax_mode` unread by anything new |
| No frontend costing screens | §9 (A4) |
| No invented/seeded data | `seed.py` untouched; **every new costing table starts empty** — including `labour_rates` (the D3 default is a code fallback, not a seeded row). The only boot-time schema work is `create_all` |

**Standing constraints preserved:** USD order history immutability, per-order currency, webhook path, additive-only schema, fail-closed gates, single costing path [R §21/§25/§26/§27].

---

## 3. Current architecture and reusable components

| Component | Fact |
|---|---|
| ORM | SQLAlchemy 2.0 `Mapped`/`mapped_column`; plain-string statuses validated by Pydantic at the API layer [F: models.py:1-5] |
| Schema upgrades | `create_all` at boot builds new tables; additive ALTERs for existing tables in `_ADDITIVE_COLUMNS` (string/`BOOLEAN … DEFAULT FALSE` lessons for Postgres) [F: app/__init__.py:29-96, 196-204] |
| Money discipline | `Numeric(10,2)` columns; `vat.py` pure-`Decimal` engine, single half-up `round_cents`, `_dec()` float-purifier [F: vat.py:27, 90-100] |
| Refusal-first engines | typed errors instead of guesses; one pure validation place (`validate_classification`), 400-wrapped in the router [F: vat.py:68-88, 164-200; admin.py:630-635] |
| Audit-ledger pattern | `ProductVatEvent`: append-only, written only on actual change, actor id+name snapshot, from/to values [F: models.py:508-527; admin.py:637-654] |
| Effective-dated reference data | `vat_rates` (code, valid_from, rate) + `_ensure_vat_rates` seeding discipline [F: models.py:433-444; seed.py:47-61] — the pattern `labour_rates` mirrors without seeding |
| Admin auth | `CurrentAdmin`/`DbSession`; single `admin` role; last-admin invariant [F: security.py:127-149; admin.py:1254-1277] |
| Public/admin schema separation | `_AdminVatFields` mixin rides admin responses only; public `ProductCard` untouched [F: schemas.py:501-519] |
| Admin rail pattern | `needs_vat_review` query filter + `_admin_card` extension [F: admin.py:501-516, 519-553] |
| Tests | one shared session `TestClient`, seeded SQLite, `register_and_login`/`auth_header`/`_top_up_stock`, fake Stripe gateway; full-suite runs only [F: test_api.py:1-146, 2318] |
| PostgreSQL staging | `Temp/staging_verify.py`: throwaway cluster, real boot, 19 checks [F: Temp/staging_verify.py] |
| Phase 1/1.1 (uncommitted) | order-currency Stripe sessions, per-currency stats/CSV, formatter hardening [F: orders.py:58-72; admin.py:107-148, 427; format.js] — **untouched by Phase 2** |

---

## 4. Data model — nine tables, documented

**Final count: 9.** Six are named by v1.2 §21 [R]. Three are justified additions, each with a distinct purpose; together they are the **entire** audit/history system — there is deliberately no second, generic audit log (task §2: avoid duplicate audit systems).

```
ingredient_categories 1──n ingredients 1──n ingredient_price_events (append-only)
                       (review_interval_days)      │
                                                   n recipe_items (loss per line)
recipes (1:1 products) ────────────────────────────┘
   │ expected_batch_yield · batch_overhead_estimate · provenance
   └─1──n production_batches (actuals; immutable once closed)
overhead_months (1/month: forecast+actual fixed overhead, categories)
labour_rates (effective-dated €/h rows; append-only; starts empty)
product_cost_records (append-only: current cost = latest row per product)
```

Not in Phase 2 [R §33 per-phase requirements]: `price_history`, `benchmark_entries`, `bulk_quotes`, `bulk_quote_items`, `quote_events`.

### 4.1 `ingredient_categories` — [R §21]

- **Purpose:** groups ingredients; carries the D18 category review interval that overrides the 90-day default.
- **Key fields:** `id`; `name String(100) UNIQUE`; `review_interval_days Integer NOT NULL DEFAULT 90`.
- **Relationships:** 1─n `ingredients` (deletion refused at the API while children exist — the category-deletion pattern [F: admin.py:471-494]).
- **Constraints/indexes:** unique `name`; interval validated `ge=1, le=1095` (Pydantic).
- **Currency/units:** none (organizational only).
- **Audit/history:** none needed — no money, no cost input. Interval changes recompute members' `next_reviewed` (only those without an explicit override) in the same transaction.
- **Mutability:** mutable (reference data).
- **ACs:** AC-36.

### 4.2 `ingredients` + `ingredient_price_events` — [R §21, §6, §12]

**`ingredients` (mutable current state):**
- **Purpose:** the ingredient catalogue — identity, current purchase figures, derived rate, provenance, review dates.
- **Key fields:** `id`; `name String(150) UNIQUE`; `category_id FK→ingredient_categories (RESTRICT) NULL`; `unit String(20)` (canonical, A3); `purchase_qty Numeric(12,4)`; `purchase_price Numeric(10,2)`; `effective_cost_per_unit Numeric(12,6)` (derived, stored; a **rate**, not an amount — cent-rounding it would corrupt per-gram costs); `currency String(3) NOT NULL DEFAULT 'eur'`; `density_g_per_ml Numeric(10,4) NULL` (A3); §12 provenance: `source Text NULL`, `confidence String(6) DEFAULT 'medium'`, `is_estimate Boolean DEFAULT TRUE`, `last_reviewed Date NULL`, `next_reviewed Date NULL` (computed, stored), `review_interval_days Integer NULL` (explicit override); `invoice_ref String(255) NULL` (free text; no credentials — §26); timestamps.
- **Relationships:** 1─n `recipe_items` (RESTRICT — deletion refused with dependent products named); 1─n price events.
- **Constraints/indexes:** unique `name`; index `category_id`; API-layer case-insensitive duplicate-name check (the `Category` pattern [F: admin.py:448]); qty `> 0`, price `≥ 0`, loss/interval bounds in Pydantic.
- **Currency/units:** `currency` per row; `unit` canonical A3; `density_g_per_ml` the only recorded cross-dimension bridge.
- **Audit/history:** current state only — history lives entirely in the event table.
- **Mutability:** mutable via admin API.
- **ACs:** AC-3, AC-4, AC-9, AC-35, AC-36, AC-37, AC-38.

**`ingredient_price_events` (append-only) [A3, P→A]:**
- **Purpose:** the effective-dated estimate→actual replacement history [R §6 "recording a new purchase replaces the effective cost per unit from its effective date"; §20.1 "replacement history"]. The **single** price audit for ingredients — no second ledger.
- **Key fields:** `id`; `ingredient_id FK CASCADE (index)`; `admin_user_id FK users SET NULL` + `admin_name String(150)` (actor snapshot); from/to snapshots of `purchase_qty`, `purchase_price`, `effective_cost_per_unit`, `unit`, `currency`, `is_estimate`, `source`, `confidence`; `effective_from Date NOT NULL`; `created_at`.
- **Relationships:** n─1 ingredient; n─1 admin user.
- **Constraints/indexes:** index `ingredient_id`; written **only when values actually changed** (the `ProductVatEvent` discipline [F: admin.py:637-654]).
- **Currency/units:** full from/to snapshots — a unit or currency change is a price event by definition (it changes the rate's meaning).
- **Audit/history:** this *is* the audit; no UPDATE/DELETE endpoints exist.
- **Mutability:** append-only.
- **ACs:** AC-6 (estimate→actual trail), AC-9, AC-37 (recalc trigger site), AC-25 (immutability test).

### 4.3 `recipes` + `recipe_items` — [R §21, §6-§9]

**`recipes` (mutable current state; 1:1 product):**
- **Purpose:** the costing truth per product — quantities, losses, labour split, yield, batch-overhead estimate (A1) — distinct from the customer-facing `products.ingredients` text [R §6].
- **Key fields:** `id`; `product_id FK products CASCADE UNIQUE (index)`; `hands_on_minutes Numeric(7,2) NOT NULL` (7.5 legal [R §9]); `unattended_minutes Numeric(7,2) NOT NULL DEFAULT 0` (excluded from cost, recorded for §9 discipline); `expected_batch_yield Integer NOT NULL ≥1`; `batch_overhead_estimate Numeric(10,2) NOT NULL DEFAULT 0` (A1); recipe-level provenance (A3): `source`, `confidence`, `is_estimate`, `last_reviewed`, `next_reviewed`; `notes Text NULL`.
- **Relationships:** 1─1 product; 1─n items; product's batches reference the same product.
- **Constraints/indexes:** unique `product_id`; full-replace upsert semantics (the `AdminVatUpdateRequest` replace discipline [F: schemas.py:524-528]) so partial edits can never leave a half-written recipe.
- **Currency/units:** none of its own (money figures derive from ingredients/overhead; minutes are minutes).
- **Audit/history:** current state only — history is the cost records' snapshots.
- **Mutability:** mutable via admin API (upsert); every change appends a new cost record.
- **ACs:** AC-2, AC-4, AC-5, AC-6, AC-8 (via A1 estimate ledger), AC-9, AC-36 (recipe review dates).

**`recipe_items`:**
- **Purpose:** one ingredient line with usable quantity and its own loss figure — "each recipe line carries its own loss figure (0 % is a valid, explicit entry)" [R §7]; **no global percentage exists anywhere in the schema or code** (AC-4 source-scan test).
- **Key fields:** `id`; `recipe_id FK CASCADE (index)`; `ingredient_id FK ingredients RESTRICT (index)`; `usable_qty Numeric(12,4) >0`; `unit String(20)`; `loss_pct Numeric(5,2) NOT NULL DEFAULT 0` (0–100); `note String(255) NULL`.
- **Relationships:** n─1 recipe, n─1 ingredient.
- **Constraints/indexes:** FK indexes; unique `(recipe_id, ingredient_id)`; unit convertibility to the ingredient's unit validated at save (A3).
- **Currency/units:** line unit may differ from ingredient unit (same dimension or recorded density, else refused).
- **Audit/history:** none of its own (recipe upsert + cost-record snapshots cover it).
- **Mutability:** replaced with the recipe (upsert).
- **ACs:** AC-3, AC-4.

### 4.4 `production_batches` — [R §21, §8]

- **Purpose:** expected-vs-actual yield and overhead reality; the actual side of A1's estimate↔actual rule; variance record.
- **Key fields:** `id`; `product_id FK products CASCADE (index)`; `produced_at Date`; `expected_yield Integer` (snapshot of the recipe's figure at creation — the recipe may change later, the batch may not); `actual_yield Integer NULL` until close; `batch_overhead_estimated Numeric(10,2)`; `batch_overhead_actual Numeric(10,2) NULL`; `overhead_categories Text NULL` (newline list — AC-8 ledger 2); `usage_notes Text NULL` (§8 "actual usage/wastage where captured"); `status String(6) DEFAULT 'open'`; `yield_variance_pct Numeric(8,2) NULL` (stored at close); `closed_at DateTime NULL`.
- **Relationships:** n─1 product; feeds `costing` via "latest closed batch".
- **Constraints/indexes:** FK index; close requires `actual_yield ≥ 0` and `batch_overhead_actual ≥ 0` (explicit zero allowed — never a guess); once `closed_at` is set, every write → 409 [R §21 "immutable once closed"].
- **Currency/units:** overhead figures in the shop's costing currency by convention (EUR); currency coherence enforced at cost-computation level (§4.8).
- **Audit/history:** the closed batch *is* history (AC-25 immutability tests).
- **Mutability:** mutable while open; **append-only in effect once closed**.
- **ACs:** AC-5, AC-6, AC-8, AC-25.

### 4.5 `overhead_months` — [R §21, §10B]

- **Purpose:** fixed monthly overhead ledger — forecast total + expected monthly production hours, month-end actuals, variance note; source of the fixed rate.
- **Key fields:** `id`; `month Date UNIQUE` (first of month); `fixed_overhead_total Numeric(10,2)`; `forecast_hours Numeric(7,2) >0`; `currency String(3) NOT NULL DEFAULT 'eur'`; `included_categories Text` (newline list — AC-8 ledger 1); `actual_total/actual_hours Numeric NULL` (month-end review); `variance_note Text NULL`; §12 provenance columns (the forecast is an estimate).
- **Relationships:** none (standalone ledger); read by every cost computation.
- **Constraints/indexes:** unique `month`; duplicate month refused (409).
- **Currency/units:** declared currency; hours are hands-on production hours.
- **Audit/history:** forecast + actuals stored side by side on the same row — variance is *recorded*, never rewriting the forecast [R §10B].
- **Mutability:** mutable (it is a ledger of *inputs*, not computations); its consequences (cost records) are append-only.
- **ACs:** AC-7, AC-8, AC-9.

### 4.6 `labour_rates` — [A5]

- **Purpose:** the centrally managed, effective-dated €/hour setting (D3 default until the owner records a row).
- **Key fields:** `valid_from Date PK`; `rate Numeric(10,2)`; `currency String(3) NOT NULL DEFAULT 'eur'`; `source Text NULL`.
- **Relationships:** none.
- **Constraints/indexes:** PK on `valid_from`; resolution = newest `valid_from ≤ calculation date`, else `DEFAULT_LABOUR_RATE = Decimal("20.00")` (single definition in `costing.py`).
- **Currency/units:** declared per row.
- **Audit/history:** append-only by construction (a change is a new dated row — the `vat_rates` pattern); each cost record snapshots the exact rate it used.
- **Mutability:** append-only (no UPDATE/DELETE endpoints).
- **ACs:** AC-2 (rate applied), AC-25 (rate history never rewritten).

### 4.7 — *tables 4.1–4.6 are the inputs; the two computations follow*

### 4.8 `product_cost_records` (append-only) — [P→A; R §12 snapshots]

- **Purpose:** every cost computation, ever — "current cost" = latest row per product; the immutable reconstruction basis for any historical decision [R §12 "freeze the full cost composition … so any historical price decision can be reconstructed exactly"; task rule "never silently rewrite historical values"].
- **Key fields:** `id`; `product_id FK products CASCADE (index)`; `computed_at DateTime`; `trigger String(30)` (`ingredient_price_change | recipe_change | batch_closed | overhead_month_change | labour_rate_change | initial`); `currency String(3) NULL` (common currency of inputs; NULL + `missing:["mixed_currencies"]` when inputs disagree); `total_cost Numeric(10,2) NULL` (NULL while incomplete — a guessed total is never stored); `is_complete Boolean`; `missing JSON` (machine-readable reason codes); `breakdown JSON` (per-line figures incl. units/loss/mode, labour with rate+version (A5), batch-overhead mode+amount (A1), fixed rate+hours, yield source, all Decimals as strings).
- **Relationships:** n─1 product.
- **Constraints/indexes:** index `product_id` (latest-per-product = `ORDER BY computed_at DESC, id DESC LIMIT 1`).
- **Currency/units:** own `currency`; per-line units inside `breakdown`.
- **Audit/history:** this *is* the computation audit — no parallel summary table, no denormalized `products.current_cost` cache to drift (§21's "as needed" is satisfied by lookup).
- **Mutability:** append-only; no UPDATE/DELETE endpoints; corrections are new records.
- **ACs:** AC-5, AC-6, AC-37, AC-38 (plus A2's `not_computable` derivation source).

**No new columns on any existing table** — Phase 2 is a pure `create_all` deploy (§5).

---

## 5. Additive-only database upgrade strategy (PostgreSQL + SQLite)

1. All nine tables are new; `create_all` builds them on both engines at next boot — the exact mechanism that shipped `vat_rates`, `order_tax_lines`, `product_vat_events` in v3.14.0 [F: app/__init__.py:200-201]. **Zero `_ADDITIVE_COLUMNS` entries, zero ALTERs, zero data fixes; `seed.py` untouched.**
2. Proven on real PostgreSQL before merge: extended `Temp/staging_verify.py` boots the real app on the throwaway cluster and asserts table existence, the full write path, and `Numeric(12,6)/(12,4)` exact round-trips (memory lesson: green SQLite tests ≠ prod-safe DDL).
3. Rollback [R §30]: revert the deploy; empty tables are inert under old code. No Phase 2 write path touches orders, products, coupons, Stripe or VAT data.
4. `Numeric` returns `Decimal` on both engines — costing never receives a float from storage.

---

## 6. `app/costing.py` — the single costing brain

Module docstring states the money discipline (as `vat.py` does [F]); pure-Decimal, refusal-first, one place for coherence rules; DB helpers thin.

### 6.1 Constants

`DEFAULT_REVIEW_INTERVAL_DAYS = 90` (D18 [R §13]) · `FLOOR = Decimal("0.40")` (D2 — **computed for flags in Phase 2, enforced only from Phase 3**) · `DEFAULT_LABOUR_RATE = Decimal("20.00")` (D3, A5 fallback) · `MARGIN_FLOOR_DIVISOR = Decimal("0.60")`.

### 6.2 Units (A3)

```python
UNIT_DIMENSIONS = {"mg": "mass", "g": "mass", "kg": "mass", "ml": "volume", "l": "volume", "each": "count"}
UNIT_ALIASES    = {"gram": "g", "grams": "g", "gr": "g", "kilo": "kg", "kilogram": "kg",
                   "kilograms": "kg", "liter": "l", "litre": "l", "litres": "l", "liters": "l",
                   "piece": "each", "pcs": "each", "item": "each", "items": "each"}
convert_qty(qty, from_unit, to_unit, density_g_per_ml=None) -> Decimal
    # same dimension: exact Decimal factor; mass<->volume: ONLY with a recorded density;
    # anything else -> MismatchedUnitsError (both units named in the message)
```

### 6.3 Cost assembly — the ONE costing path [R §22; Phase 6 quotes reuse it]

```python
def compute_product_cost(recipe, lines, latest_closed_batch, overhead_month,
                         labour_rate, on_date) -> CostResult
```

- Per line: `purchase_qty = usable_qty / (1 - loss_pct/100)`; convert to the ingredient's unit (A3); `line_cost = qty × effective_cost_per_unit` — **unrounded** (AC-4's intermediates).
- Labour: `hands_on_minutes/60 × rate` (unattended contributes 0) — rate+version snapshotted (A5).
- Yield: latest closed batch's `actual_yield` if one exists, else `recipe.expected_batch_yield` — sellable output always [R §8].
- Batch overhead — the A1 rule, exactly one source:
  - no closed batch → `recipe.batch_overhead_estimate / expected_batch_yield`, `mode:"estimated"`;
  - ≥1 closed batch → latest closed batch's `batch_overhead_actual / actual_yield`, `mode:"actual"`;
  - never both; the mode rides into the breakdown frozen.
- Fixed overhead: `rate = fixed_overhead_total / forecast_hours` [R §10B]; per unit `hours × rate`.
- Total: `total_per_unit = round_half_up(batch_total / yield + fixed_per_unit, cent)` — the **single** rounding point [R §6].
- Completeness (never an exception, always data): missing effective cost / no lines / no overhead month / `forecast_hours ≤ 0` / yield ≤ 0 / mixed input currencies → `is_complete=False`, `total_cost=None`, `missing=[codes]`.

### 6.4 Review scheduling (D18)

`effective_interval_days(ingredient)` = explicit override → category interval → 90 · `next_review_date(last, interval)` = `last + interval` days · `is_overdue(next, today)` = `today ≥ next` [R §12] — derived at read, no scheduler, exposed on every surface that shows the cost.

### 6.5 Margins & computability (A2) — flags only in Phase 2

`margin(net, cost)` · `min_net_price(cost) = cost / 0.60` [R §4] · `evaluate_margins(product, cost_record, active_currency)` → per price kind (`regular`, `sale`): `{status: "ok"|"not_computable", margin?, min_net?, reason?}` where `not_computable` carries `{code: "currency_mismatch", cost_currency, price_currency}`. **No conversion, no comparison across currencies.**

### 6.6 Recalculation orchestration

`recompute_dependent_costs(db, ingredient)` — appends one `product_cost_record` per dependent product (recipe dependency via `recipe_items`), inside the caller's transaction, regardless of review dates [R §13, AC-37]. Incomplete dependents become incomplete records, never exceptions — one bad recipe must not make an ingredient price unsaveable. Approved prices are never touched (AC-38).

---

## 7. Router design — `app/routers/costing.py` [R §22]

All endpoints `CurrentAdmin`; every cost-input write appends its ledger row and recalculates dependents in the same transaction.

| Endpoint | Behaviour (new since plan v1: the labour-rate endpoint, A5) |
|---|---|
| `GET/POST/PUT/DELETE /api/admin/ingredient-categories` | CRUD; delete refused while children exist; interval change recomputes members' `next_reviewed` (override holders exempt) |
| `GET /api/admin/ingredients` (`?search&category_id&overdue&due_within_days&page`) | List with provenance + derived overdue flag |
| `GET/POST/PUT/DELETE /api/admin/ingredients` (+ `GET /{id}`) | CRUD; delete refused while recipe lines use it (dependents named). Any change to `purchase_qty`/`purchase_price`/`unit`/`currency` writes a price event (only on actual change) and triggers `recompute_dependent_costs` [AC-37] |
| `POST /api/admin/ingredients/{id}/review` | Mark reviewed (`last_reviewed=today` default), recompute `next_reviewed` [AC-35] |
| `GET/PUT /api/admin/products/{product_id}/recipe` | Full-replace upsert (lines validated: unique ingredient, qty>0, loss 0–100, unit convertible); change → `refresh_cost(trigger="recipe_change")` |
| `GET/POST /api/admin/batches`, `PUT /api/admin/batches/{id}`, `POST /api/admin/batches/{id}/close` | Record production; close freezes actuals + variance and refuses later writes (409) [AC-5, AC-25]; close → `refresh_cost(trigger="batch_closed")` |
| `GET/POST/PUT /api/admin/overhead-months` | Forecast/actuals; duplicate month 409; responses carry `overlap_warnings` from `overhead_overlap` — warning, never a block [AC-8]; write → refresh costs |
| `PUT /api/admin/costing/labour-rate` | Appends an effective-dated rate row (A5); append-only; change → `refresh_cost(trigger="labour_rate_change")` for all costed products |
| `GET /api/admin/cost-review` | `{overdue, upcoming, overlap_warnings}` across ingredients, recipes, overhead months — renders the derived flag only [R §12] |

`admin.py` gains only: `cost` summary block on `admin_product_detail`, `cost_status` on `_admin_card`, `?needs_cost_review` filter on `/api/admin/products` — extending the `needs_vat_review` rail pattern [F: admin.py:519-553].

---

## 8. Pydantic schemas (schemas.py)

Same grammar as the existing file [F]. Highlights:

- `IngredientOut` (+ derived `overdue`, `effective_interval_days`), `IngredientCreateRequest/UpdateRequest` (bounds; `confidence: Literal["low","medium","high"]`; `currency: ^[a-z]{3}$`), `IngredientReviewRequest`.
- `IngredientPriceEventOut` — the replacement history payload.
- `RecipeUpsertRequest` (`hands_on_minutes ge=0`, `unattended_minutes ge=0`, `expected_batch_yield ge=1`, `batch_overhead_estimate ge=0`, unique-ingredient `items min_length=1`, provenance) + `RecipeOut` (items with computed line previews).
- `ProductionBatchOut/CreateRequest/UpdateRequest/CloseRequest`; `OverheadMonthOut/…` + `overlap_warnings: list[str]`; `LabourRateOut/LabourRateSetRequest {valid_from, rate, currency?, source?}`.
- `CostReviewOut`; `MarginEvaluationOut {status, margin?, min_net?, reason?}` (A2 machine-readable).
- `_AdminCostFields` on `AdminProductCard/Detail`: `cost_status: not_costed | incomplete | costed | margin_flagged | not_computable`; detail adds `ProductCostSummaryOut` (`total_cost, currency, computed_at, is_complete, missing, margins{regular, sale}, inputs_overdue`). **Public `ProductCard`/`ProductDetail` gain nothing** [R §26; F: `_AdminVatFields` discipline].

---

## 9. Admin UI — API-only (A4)

No frontend files change; verified by diff at review. Data entry until Phase 5 is via the admin API (dev OpenAPI docs at `/api/docs` [F: app/__init__.py:255] or curl with an admin bearer token). All endpoints are admin-only (403 otherwise — tested), Pydantic-validated, and audited via the ledgers of §4. The Phase 5 surfaces of §20 (catalogue, recipe editor, batches, overhead, cost-review center, under-review rail) consume exactly the endpoints in §7 — no rework created for them here.

---

## 10. Roles and permissions

Single existing `admin` role; `CurrentAdmin` on every new endpoint [R §17 D17, §26; F: security.py:127-131]. No public costing surface, no anonymous path, no rate-limit changes (admin-only, consistent with existing admin writes). Last-admin invariant untouched [F: admin.py:1262-1277]. Non-admin → 403 tested (AC-29 pattern).

---

## 11. Audit and cost-history design — one system, no duplicates

| Concern | The one place it lives |
|---|---|
| Ingredient price/estimate changes | `ingredient_price_events` (append-only, actor + from/to + effective date) |
| Cost computations & their inputs | `product_cost_records` (append-only, full breakdown incl. rate version, overhead mode, units) |
| Production reality | closed `production_batches` (immutable once closed) |
| Overhead forecasts vs actuals | `overhead_months` (side-by-side fields, variance recorded) |
| Labour-rate changes | `labour_rates` (effective-dated append-only) |

No generic audit log, no event-sourcing layer, no denormalized caches. Recalculation appends records; it never mutates prices (AC-38) and never approaches `orders`/`order_items` [R §14 step 6, §25].

---

## 12. Costing and historical integrity — the explicit rule set

Each mandated rule, and where it is enforced:

| Rule | Enforcement |
|---|---|
| Decimal throughout | `costing.py` pure Decimal; `_dec()` purifies boundary floats [F: vat.py:90-95]; `Numeric` columns return Decimal |
| Intermediates keep precision | No quantization before the final total; AC-4 asserts 0.7895 / 1.5789 as intermediates |
| Rounding only at the final point | Single `round_half_up(total_per_unit, 0.01)` [R §6] |
| Allocation on actual sellable yield | Yield rule in §6.3 (latest closed batch actual → else expected) [R §8, AC-5] |
| Expected vs actual + variance recorded | `production_batches.expected/actual/yield_variance_pct` (stored at close) |
| Wastage per line/recipe, never universal | `recipe_items.loss_pct` per line, default explicit 0; **no global constant exists** — source-scan test (AC-4) |
| Estimated vs actual vs incomplete distinguished | `is_estimate` provenance everywhere; cost-record `mode` flags (A1); `is_complete`+`missing` (never a guessed total) |
| Price change → immediate recalculation | `recompute_dependent_costs` inside the write transaction, regardless of review date [AC-37] |
| Recalc creates history, never overwrites | append-only `product_cost_records` [AC-38] |
| Fixed overhead via expected monthly hours | `overhead_months.forecast_hours` → rate → per-unit [AC-7] |
| No double-counting batch vs fixed | Two disjoint ledgers + `overhead_overlap` warning (AC-8) + A1's exactly-one-source rule within the batch ledger |
| History preserves inputs, assumptions, rate versions | `breakdown` JSON snapshots everything (units, losses, rate+version, overhead mode+amount, yield source), Decimals as strings [R §12] |

---

## 13. Validation and incomplete-data behaviour

Fail-closed for data, fail-open for the shop [R §27]: costing refuses to produce a total (`is_complete=False`, `total_cost=None`, machine-readable `missing`) when any input is absent or currencies mix; incomplete products stay visible admin drafts (`cost_status="incomplete"`); **nothing blocks publication or checkout in Phase 2** (Phase 3/5 gates). Overdue inputs are flagged at read, never hidden, never blocking alone [R §12/§13]. Unit/density violations refuse at save with plain-language 400s (A3). Estimate→actual replaces current figures from the effective date and recalculates forward only [R §6, D5]. Recalculation failures never roll back their triggering write (§6.6).

---

## 14. Acceptance-criteria coverage matrix

| AC | Implementation | Tests (`test_costing.py` unless noted) | Evidence needed to pass |
|---|---|---|---|
| **AC-2** labour | `labour_cost` + `labour_rates` resolution (A5) | pure tests: 12 min→4.00; 45→15.00; 7.5→2.50; unattended minutes → 0; a recorded rate row overrides the default and is snapshotted | exact Decimal equality on all four figures; breakdown carries `rate`+`rate_source` |
| **AC-3** unit conversion | `convert_qty` + `effective_cost_per_unit` | 25 kg @ €37.50 → rate 1.50/kg; 500 g line → 0.75 pre-wastage; `g↔each` refused; mass↔volume refused without density, allowed with | exact rates; refusal messages name both units |
| **AC-4** wastage | `purchase_qty` per line; per-line `loss_pct` | 1000 g @5% → 1052.63 g → intermediate 1.5789 → 1.58; 500 g → 0.7895 → 0.79; 0% explicit; **source scan: no universal loss constant in `costing.py`** | digit-level intermediate + final values; grep-based scan assert |
| **AC-5** yield allocation | yield rule §6.3; variance at close | batch €26.40 expected 24 / actual 22 → unit cost 1.20; variance −8.33 stored; expected record unchanged; closed-batch edit → 409 | new cost record uses 22; `yield_variance_pct == -8.33`; mutation refused |
| **AC-6** batch overhead estimate→actual | A1 rule | estimate-only costing `mode:"estimated"`; close a batch → next record `mode:"actual"` with the batch's figures; **old record still `mode:"estimated"` with original figures** | mode + amounts asserted on both records |
| **AC-7** fixed overhead | `overhead_months` rate | €800/160 h → 5.00/h; 24-min product → 2.00; month-end actuals + variance note recorded; forecast unchanged | exact figures; immutability of forecast fields after actuals |
| **AC-8** double-count warning | `overhead_overlap` | same category in month + batch → warning non-empty; disjoint → empty; warning never blocks the write | warning list contents exact |
| **AC-9** provenance | §12 columns on every estimate | every created input carries source/date/confidence/status; overdue derivation; `is_estimate` surfaces labelled, never "verified" | field-by-field assertions on API payloads |
| **AC-36** intervals | `effective_interval_days` precedence | default: 2026-01-01 → 2026-04-01; 30-day category → 2026-01-31; per-ingredient override beats both; category change moves non-override members | computed `next_reviewed` equals all three expectations |
| **AC-37** immediate recalc | `recompute_dependent_costs` in-transaction | flour-price PUT → response OK and, in the same committed state, each dependent product has a new record `trigger="ingredient_price_change"`; review dates ignored | record count + trigger + total changes; no deferred/pending state |
| **AC-38** recalc ≠ repricing | no price write paths | after AC-37: `products.price`/`sale_price` byte-identical in DB **and** via public catalog API; only records/flags changed | exact before/after equality; public API field diff |
| *AC-25 (partial)* immutability | closed batches, ledgers | mutation attempts → 409/absent endpoints | refusal statuses |
| *AC-29 (pattern)* auth | `CurrentAdmin` everywhere | customer token on representative endpoints → 403 | 403 payloads |
| *Regression* | additive admin fields; public schema untouched | admin card `cost_status="not_costed"` on seed products; public `ProductCard` JSON unchanged field-by-field; Phase 1/1.1 currency tests still green (full suite) | suite green in full (never `-k`) |
| *A2* currency | `evaluate_margins` | EUR-cost/USD-active fixture → `not_computable` + `currency_mismatch` reason with both codes; no conversion performed (assert no FX code exists); active-currency flip → same record evaluates (pure-function test) | machine-readable reason codes |
| *Staging* | §5 | extended `Temp/staging_verify.py`: 19 existing checks + ~7 new (tables exist on Postgres; full write path; Numeric exactness; recalc end-to-end) | all PASS, non-zero exit on failure |

---

## 15. Exact files expected to change

**New:** `backend/app/costing.py` · `backend/app/routers/costing.py` · `backend/tests/test_costing.py`
**Modified:** `backend/app/models.py` (9 appended models) · `backend/app/__init__.py` (one import + one `include_router`; no `_ADDITIVE_COLUMNS` change) · `backend/app/schemas.py` (costing schemas + `_AdminCostFields`) · `backend/app/routers/admin.py` (~30 lines: cost summary, `cost_status`, `needs_cost_review`) · `Temp/staging_verify.py` (untracked)
**Removed from v1's list:** `config.py` — no longer touched (labour rate moved from env setting to the versioned table, A5).
**Untouched:** `orders.py`, `payments.py`, `stripe_gateway.py`, `vat.py`, `seed.py`, `database.py`, `serialize.py`, `config.py`, every frontend file, `render.yaml`, environment/production config, and the uncommitted Phase 1/1.1 changes in shared files (all Phase 2 edits are additive sections in `models.py`/`schemas.py`/`admin.py`, disjoint from the currency work).

---

## 16. Implementation order and dependencies

1. **Models + boot** — 9 models appended; boot on SQLite + staging cluster; tables exist and are **empty**. (Zero behaviour change.)
2. **`costing.py` pure functions** — units (A3), wastage, labour (+rate resolution, A5), overhead, assembly (A1 mode rule), review dates, margins/computability (A2) + direct tests (AC-2…9 numerics). No routers yet.
3. **Labour-rate + ingredient/category routers** — CRUD, provenance, price events, in-transaction `recompute_dependent_costs` (AC-35/36/37/38).
4. **Recipes router** — upsert + validation + `refresh_cost`; cost records become real.
5. **Batches + overhead months** — close/freeze/variance, overlap warning, recalc triggers (AC-5/6/7/8).
6. **Cost-review endpoint + admin product surface** — the rail data.
7. **Staging extension** — real-PostgreSQL proof of all of the above.
8. **Full verification + report** — suite (full run), ruff, frontend build sanity, staging all-green. Stop for review; no commit without instruction.

Steps 3–6 are independently testable; 2 blocks 3–6; 1 blocks all.

---

## 17. Risks

| Risk | L | I | Handling |
|---|---|---|---|
| Recalc cost on ingredient write (synchronous, all dependents) | L | L | ~24-product catalog; measured in tests; documented O(products × lines) |
| Phase-2 flags mistaken for enforcement | M | M | Flags are read-only data; docstrings + this plan say so; enforcement is Phase 3 behind its own approval |
| Mixed-currency inputs producing a silent wrong cost | L | M | Mix → `is_complete=False` + `missing:["mixed_currencies"]`; margins `not_computable` (A2) |
| SQLite/Postgres divergence | L | H | Zero ALTERs; `Numeric` round-trips asserted on real Postgres staging |
| Interleaving with uncommitted Phase 1/1.1 edits in shared files | M | M | Additive sections only; full suite + staging re-prove currency behaviour at every step; the tree is never reset or reformatted |
| Over-building Phase 3 early | M | M | `margin`/`min_net_price` feed `cost_status` only; no approval/charm/history/publication code |
| Density figure wrong → systematically wrong line costs | M | M | Density is per-ingredient, dated, provenance-tagged, review-scheduled like every other estimate |

---

## 18. Remaining questions requiring owner approval

**None blocking.** All five decisions are incorporated (§1); the spec gaps they covered are closed. Two confirmable **assumptions** ride along with this document and take effect unless vetoed at plan review (both are data-entry conventions, not architecture, and both are trivially changeable later):

- **Assumption 1 — unit alias vocabulary** (§6.2): `mg/g/kg`, `ml/l`, `each` (+ listed aliases). A unit outside the list is refused at save today; extending the constant is a one-line change. If the owner routinely buys in a unit not listed (e.g. `pound`, `crate`), say so and it joins the vocabulary before Phase 2 lands.
- **Assumption 2 — "latest closed batch" selects the actuals** (A1): greatest `produced_at`, tiebreak greatest `id`. A rolling average over the last N batches was considered and deliberately deferred (it adds statefulness without a spec requirement); if the owner prefers rolling, it is a contained change to one function — best decided with real production data in Phase 5.

---

*Finalized 2026-11-05 under the Phase 2 Plan Finalization task (documentation only). Nothing in this document changes code, data or configuration. Phase 2 implementation begins only on the owner's explicit approval; the uncommitted Phase 1/1.1 working tree is preserved untouched.*
