# Phase 2 Costing Foundation — Implementation Report

**Date:** 2026-11-05
**Base:** `e5ab209` (v3.14.0, deployed) + uncommitted Phase 1/1.1 working-tree changes
**Contract:** `docs/business/PHASE2_COSTING_IMPLEMENTATION_PLAN.md` (approved final v2)
**Status:** Implemented, verified, and **committed** as `19c9603` (2026-11-05; the working-tree wording above describes the hand-off moment, before the commit landed)

---

## 1. Files changed and why

### New files

| File | Lines | Purpose |
|---|---|---|
| `backend/app/costing.py` | 662 | The pure-Decimal costing engine: unit conversion (`convert_qty`, explicit density crossing only), purchase-quantity scaling (`purchase_qty`), effective labour-rate lookup (`labour_rate_on` — table rate or identifiable `default_fallback`), the batch cost computation (`compute_product_cost`), the actuals selector (latest genuinely-closed batch), record append/refresh (`refresh_cost`), dependent and global recalculation, margin evaluation, review-date arithmetic (`next_review_date`, `is_overdue`), and the admin cost-status summary. No HTTP here — everything is callable and unit-testable against a session. |
| `backend/app/routers/costing.py` | 861 | The admin-only costing API under `/api/admin/costing` (inventory in §3). Owns the write-time rules the plan places at the edges: duplicate-name rejection, recipe validation (yield > 0, unknown ingredients, unconvertible line units refused with 400), price-edit provenance (before/after capture + append-only `IngredientPriceEvent` + dependent recalculation in one transaction), batch close with actual-yield/actual-overhead capture and variance, overhead-month overlap warnings that never block, effective-dated labour-rate append with duplicate-date rejection. |
| `backend/tests/test_costing.py` | 1175 | 28 tests covering every gate AC, the financial-integrity rules, the auth matrix, and Phase 1/1.1 regression (map in §7). |

### Modified files (Phase 2 additions only — all pre-existing uncommitted Phase 1/1.1 content preserved)

| File | Why |
|---|---|
| `backend/app/models.py` | Nine new ORM models (§2). `LabourRate` additionally gained a `created_at` column (deviation D4) so its API output can carry the append order. No existing model, column, or relationship was altered. |
| `backend/app/schemas.py` | Pydantic schemas for the new endpoints (categories, ingredients, price events, recipes, batches, overhead months, labour rate, cost review) plus `_AdminCostFields`/`ProductCostSummaryOut` so admin product cards and detail carry `cost_status` and the margin summary. No existing schema field was changed. |
| `backend/app/routers/admin.py` | Admin product list/detail integration: `_admin_card` computes `cost_status`; `all_products` gains `needs_cost_review` filter; `admin_product_detail` attaches the cost summary when a recipe exists. Imports only additive. |
| `backend/app/__init__.py` | Registers the costing router: `from .routers import costing as costing_routes` (aliased — see deviation D6) and `app.include_router(costing_routes.router)`. |

### Files deliberately untouched

Phase 1/1.1 modified files not named above were **not** edited by Phase 2 and retain their working-tree state exactly: `backend/app/routers/orders.py`, `backend/app/routers/payments.py`, `backend/app/stripe_gateway.py`, `backend/tests/test_api.py`, `backend/tests/test_stripe_gateway.py`, `frontend/src/lib/format.js`, `frontend/src/lib/format.test.js`, `frontend/src/pages/admin/Dashboard.jsx`, `frontend/src/pages/admin/Users.jsx`.

---

## 2. New tables (all start empty — nothing is seeded)

| Table | Purpose |
|---|---|
| `ingredient_categories` | Owner-defined groupings with a category-level review interval (used when an ingredient has no override). |
| `ingredients` | The purchase side: name, unit, purchase qty + price (from which `effective_cost_per_unit` is derived), per-line loss at the ingredient level if set, optional `density_g_per_ml`, category link, invoice ref, review dates. |
| `ingredient_price_events` | Append-only provenance: every price-relevant edit records before/after purchase price and effective rate, actor, timestamp, note. Never updated, only appended and read. |
| `recipes` | One per product: hands-on minutes, expected sellable yield, estimated batch overhead, review interval override, notes. |
| `recipe_items` | Recipe lines: ingredient, usable qty + unit, per-line loss pct. Unique per (recipe, ingredient) — no accidental double-count. |
| `production_batches` | Baking reality: expected defaults (from the recipe) vs. actual sellable yield and actual batch overhead captured at close, plus the computed yield variance %. |
| `overhead_months` | Fixed monthly overhead ledger: month, `fixed_overhead_total`, `forecast_hours` (→ €/h rate used for allocation), optional month-end actuals. Kept strictly separate from batch overhead. |
| `labour_rates` | Effective-dated labour rate history (same pattern as `vat_rates`): `valid_from`, rate, currency, actor, `created_at`. The €20/h default is **not** a seeded row — it is the code fallback, identifiable as `default_fallback`. |
| `product_cost_records` | Immutable cost snapshots: per-unit total, completeness flag, missing-reason list, currency, trigger, and the full exact breakdown (line costs with units, labour with rate+source+valid_from, batch overhead with mode, fixed allocation with hours+rate) — every number a string so no float ever enters the money path. Appends only; nothing ever updates or deletes a row via the API. |

**No second audit system:** ingredient price provenance lives in `ingredient_price_events` only; existing order/VAT/Stripe audit trails are untouched. No unrelated tables were added.

---

## 3. Endpoint inventory (all admin-only)

Every route uses the existing `CurrentAdmin` dependency — **no new auth mechanism**. Verified in tests: unauthenticated → 401, ordinary customer → 403, admin → 2xx (`test_costing_requires_admin`, plus staging F8).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/costing/ingredient-categories` | List categories |
| POST | `/api/admin/costing/ingredient-categories` | Create (interval ≥ 1) |
| PUT | `/api/admin/costing/ingredient-categories/{id}` | Update (interval changes move members' derived dates) |
| DELETE | `/api/admin/costing/ingredient-categories/{id}` | Delete (refuses while ingredients reference it) |
| GET | `/api/admin/costing/ingredients` | List with `search`, `category_id`, `overdue`, `due_within_days`, paging |
| POST | `/api/admin/costing/ingredients` | Create (case-insensitive duplicate rejection) |
| GET | `/api/admin/costing/ingredients/{id}` | Detail |
| PUT | `/api/admin/costing/ingredients/{id}` | Edit; price/unit/density-relevant edits append an `IngredientPriceEvent` and recalculate dependent products in the same transaction |
| POST | `/api/admin/costing/ingredients/{id}/review` | Mark reviewed today (recomputes next review date) |
| GET | `/api/admin/costing/ingredients/{id}/price-events` | Full append-only price history |
| DELETE | `/api/admin/costing/ingredients/{id}` | Delete (refuses while a recipe line references it) |
| GET | `/api/admin/costing/products/{id}/recipe` | Recipe with lines + per-line derived cost |
| PUT | `/api/admin/costing/products/{id}/recipe` | Upsert (validates yield > 0, ingredient existence, unit convertibility; recalculates cost immediately) |
| GET | `/api/admin/costing/batches` | List batches (filterable by product, status) |
| POST | `/api/admin/costing/batches` | Open a batch (defaults from the recipe) |
| PUT | `/api/admin/costing/batches/{id}` | Amend an open batch |
| POST | `/api/admin/costing/batches/{id}/close` | Close with actual sellable yield + actual batch overhead → variance + new actual-mode cost record |
| GET | `/api/admin/costing/overhead-months` | List the fixed-overhead ledger |
| POST | `/api/admin/costing/overhead-months` | Create month (refuses duplicate month) |
| PUT | `/api/admin/costing/overhead-months/{id}` | Update (month-end actuals never trigger recalculation; total/hours changes do) |
| GET | `/api/admin/costing/labour-rate` | Today's effective rate + status + full history |
| PUT | `/api/admin/costing/labour-rate` | Append an effective-dated rate (409 on duplicate `valid_from`; recomputes only if the new row is today's effective) |
| GET | `/api/admin/costing/cost-review` | The review desk: overdue entries, entries due within a window (default 14 days), and overhead overlap warnings |

Additionally, the existing admin surface gained (still behind `CurrentAdmin`): `cost_status` on every admin product card, `?needs_cost_review=true` on the product list, and a `cost` summary block on admin product detail. The public catalog exposes **nothing** costing-related (verified in `test_regression_admin_card_and_public_catalog`).

---

## 4. Database upgrade approach

- **Additive only.** All nine tables are new and created by the existing `Base.metadata.create_all(engine)` at boot — the same mechanism that already creates every table on both SQLite and PostgreSQL. **Zero** entries were added to `_ADDITIVE_COLUMNS` (no existing table needed a new column, except `labour_rates.created_at`, which is part of a brand-new table and therefore additive by construction).
- No destructive migration, no data rewrite, no backfill. Historical orders, VAT snapshots, and payment records are physically untouched — no code path in Phase 2 writes to any pre-existing table except `products` reads.
- Verified on both engines: the full SQLite suite (148 tests) and the PostgreSQL staging run (§9), including the "legacy database" Stage D that proves `_upgrade_schema` and `create_all` coexist safely on a pre-Phase-2 database shape.
- The reserved-word column `product_cost_records."trigger"` is quoted everywhere (ORM + raw staging SQL) and round-trips on both engines.
- **The production database was never connected to or modified.**

---

## 5. Calculation examples (exact Decimal results)

All money math is `decimal.Decimal` with `ROUND_HALF_UP`, one single rounding to cents at the final per-unit total (spec §4/§16). Line/intermediate values are never rounded in storage.

| Scenario | Exact arithmetic | Result |
|---|---|---|
| AC-3 effective rate | 25 kg @ €37.50 | `37.50 / 25 = 1.50` €/kg exactly |
| AC-4 wastage scaling, 1000 g @ 5% loss @ €1.50/kg | `1000 × 100 / 95 = 1052.631578…` g → `× 1.50/1000 = 1.578947…` | **€1.58** per unit |
| AC-4b, 500 g @ 5% | `526.315789…` g → `0.789473…` | **€0.79** |
| AC-5 §8 example | ingredients €26.40, yield 24 | `26.40 / 24 = 1.10` exactly (estimated) |
| AC-5 actual | same batch, 22 sellable | `26.40 / 22 = 1.20` exactly; variance `(22−24)/24 = −8.33%` stored |
| AC-6 §10A batch overhead | €4.40 overhead, 22 units | `4.40 / 22 = 0.20` per unit |
| AC-7 §10B fixed overhead | €800 / 160 h | `5.00` €/h; 24 min hands-on → `24/60 × 5.00 = 2.00` batch labour+allocation → €0.02/unit at yield 100 |
| Labour §9 | 12 min @ €20 → 4.00; 45 min → 15.00; 7.5 min → 2.50 | hands-on only; unattended excluded |
| Density crossing | 515 g @ 1.03 g/ml | `515 / 1.03 = 500` ml = 0.5 l → @ €1.20/l = **€0.60** exactly; without density the line is refused, never guessed |
| Price-change recalc (staging F5) | 17.6 kg @ €41.00/25 kg = 28.864 | `/24 = 1.2026…` → **€1.20**; `/22 = 1.3116…` → **€1.31** |

Margin arithmetic (evaluation only, never a price suggestion): `margin = (net − cost)/net`; at 40% floor `min_net = cost / 0.60`.

---

## 6. Historical-integrity evidence

1. **Cost records are append-only.** No API path issues UPDATE/DELETE on `product_cost_records`. Every recalculation appends a new row; tests snapshot the previous row's full JSON and assert it is byte-identical after price changes, batch closes, labour-rate changes, and unit changes (`test_ac5_end_to_end…`, `test_ingredient_price_change_recalculates_ac37`, `test_labour_rate_effective_dating`, `test_unit_change_records_event_and_preserves_history`).
2. **Every snapshot is self-contained.** The breakdown stores the exact labour rate, currency, rate source (`table` / `default_fallback`), and `valid_from` used; each line stores its unit, quantity, loss, and unit-cost basis. A later rate or unit change cannot rewrite what an old record says — the old record simply predates it.
3. **Price events are append-only provenance.** `ingredient_price_events` rows are created on relevant edits with before/after values and are never modified; the endpoint is read-only (§3).
4. **Actuals never contaminate estimates and vice versa.** A record stores exactly one batch-overhead mode (`estimated` or `actual`); estimated and actual overhead are **never summed** (`test_ac6_batch_overhead_component_and_modes` asserts the mode flips rather than adds; `test_actuals_selector_refuses_unsuitable_batches` proves open batches, zero-yield batches, and missing actual overhead are all rejected by the selector, with the deterministic `produced_at DESC, id DESC` tie-break).
5. **Fixed overhead is a separate ledger.** `overhead_months` (fixed) and `production_batches.batch_overhead_*` (batch) never mix; labour is excluded from both and priced from `labour_rates`.
6. **Selling prices are inviolable.** `test_regression_admin_card_and_public_catalog` asserts `products.price`/`sale_price` are byte-equal after every costing operation and the public catalog response contains no cost keys (AC-38).
7. **Incompleteness is honest.** Missing inputs produce `total_cost = null` plus specific reason codes (`no_recipe`, `no_recipe_lines`, `no_overhead_month`, `invalid_forecast_hours`, `invalid_yield`, `mixed_currencies`, `ingredient_missing_price:<name>`, `unit_mismatch:<name>`); nothing is invented (`test_incomplete_reasons_are_data`).

---

## 7. Acceptance-criteria → implementation/test mapping

| AC | Implementation | Test(s) |
|---|---|---|
| **AC-2** new tables exist, start empty, no fake seed data | create_all; seeding untouched; labour default is a code fallback, not a row | Staging F1 + F2 (counts all 0 on PG); `test_labour_rate_effective_dating` (history starts `[]`, source `default_fallback`) |
| **AC-3** 25 kg @ €37.50 → €1.50/kg | `effective_cost_per_unit` derived on write in Decimal | `test_effective_rate_ac3`; staging F3 (PG Numeric round-trip `1.500000`) |
| **AC-4** purchase scaling with loss | `purchase_qty(usable, loss)`; per-line loss in recipe items; no global wastage constant | `test_purchase_qty_scaling_ac4` (1.58 / 0.79), `test_per_line_wastage`, `test_no_global_wastage_constant` (source scan) |
| **AC-5** estimated vs actual €1.10→€1.20, variance, immutable history | close-batch actuals selector + append-only records | `test_ac5_end_to_end_cost_and_batch_history` (old record byte-identical; re-close → 409) |
| **AC-6** €4.40/22 = €0.20; actual replaces, never adds | single-mode batch overhead in breakdown | `test_ac6_batch_overhead_component_and_modes` |
| **AC-7** €800/160 h; 24 min → €2.00; month-end actuals don't reprice history | fixed allocation = hands-on hours × month €/h; PUT only recalcs on total/hours change | `test_fixed_overhead_rate_ac7`, `test_ac7_fixed_overhead_allocation_and_month_end_actuals` |
| **AC-8** overlap warnings never block | `_overlap_warnings` returned alongside saves and in cost-review | `test_ac8_overhead_overlap_warnings_never_block` |
| **AC-9** variance stored on close | `yield_variance_pct` computed with ROUND_HALF_UP | `test_ac5…` (−8.33 asserted); staging F6 (−8.33 on PG) |
| **AC-36** override > category > 90; calendar-month math | `next_review_date` with `relativedelta`; interval sources in priority order | `test_review_scheduling_math_ac36` (2026-01-01+90→2026-04-01; +30→2026-01-31), `test_intervals_via_api_ac36` (category change moves members) |
| **AC-37** price change → event + immediate dependent recalc regardless of review dates | `update_ingredient` single-transaction edit+event+`recompute_dependent_costs`; trigger `ingredient_price_change`; no-op edits write nothing | `test_ingredient_price_change_recalculates_ac37`; staging F5 |
| **AC-38** costing never touches selling prices | no write path to price fields; public schema unchanged | `test_regression_admin_card_and_public_catalog` (byte-equal + no cost keys in public catalog) |
| **A2** cost currency ≠ active selling currency → `not_computable`, `currency_mismatch`, both codes shown | `evaluate_margins` refuses cross-currency; no FX anywhere | `test_currency_mismatch_is_not_computable_a2`; staging F7 |
| **Security** (§5) 401/403/2xx matrix | `CurrentAdmin` on every route | `test_costing_requires_admin`; staging F8 |
| **Units** explicit conversion only; density only when supplied; never guess | `convert_qty` same-dimension exact; mass↔volume requires `density_g_per_ml`; count never converts; recipe write refuses unconvertible lines (400) | `test_unit_conversion_rules`, `test_density_based_conversion`, `test_unit_change_records_event_and_preserves_history` (density removal → `unit_mismatch:<name>` incomplete record, old records keep their units) |
| **Labour** effective-dated, append, overlap validation, fallback identifiable, history immovable | `labour_rates` table + `labour_rate_on` fallback; 409 duplicate `valid_from` | `test_labour_rate_effective_dating` (fallback → table → future-dated → 409; old records keep their rate) |
| **Phase 1/1.1 regression** | — | Full suite: all 120 pre-existing tests (incl. Phase 1/1.1 additions) pass unchanged |

---

## 8. Verification runs (all actually executed, 2026-11-05)

| # | Command | Result |
|---|---|---|
| 1 | `backend> venv\Scripts\python.exe -m pytest tests -q` (full suite, never `-k`) | **148 passed**, 2 pre-existing warnings (~40 s) — 120 pre-existing + 28 new costing |
| 2 | `backend> venv\Scripts\python.exe -m ruff check app/ tests/ run.py --select F` | **All checks passed** |
| 3 | `frontend> npm test` | **10 passed** |
| 4 | `frontend> npm run build` | **Success** (pre-existing >500 kB chunk-size warning, unchanged since v3.13.0) |
| 5 | PostgreSQL staging (§9) | **27/27 PASS** |

## 9. PostgreSQL staging results

Throwaway local cluster (PostgreSQL 18, port 55432, trust auth, **no passwords**, directory deleted afterwards; never committed; production never contacted). Script: `Temp/staging_verify.py`, run with `DATABASE_URL=postgresql+psycopg://stage_admin@127.0.0.1:55432/bakehouse_stage`.

- **Stages A–E** (pre-existing Phase 1/2B–2D checks): 19/19 PASS — no regression from Phase 2.
- **Stage F** (new, Phase 2): 8/8 PASS —
  - F1 all nine tables created on PG; F2 all start empty;
  - F3 ingredient write derives exactly €1.50/kg surviving the PG `Numeric` round-trip (`1.500000`);
  - F4 recipe write lands a complete **€1.10** record (`26.40/24`), `trigger='recipe_change'` — reserved-word column quoted and read back on PG;
  - F5 price change → provenance event (37.50→41.00, effective 1.50→1.64) + recalculated **€1.20** record (`28.864/24`);
  - F6 batch close at 22 → actual-mode **€1.31** (`28.864/22`), variance **−8.33** stored, breakdown mode `actual`;
  - F7 labour-rate append (`source='table'`, history 1 row) + cost-review entry `reason='scheduled'` with `cost_status='not_computable'` and `currency_mismatch` (EUR cost vs USD active price);
  - F8 unauthenticated access → 401.

## 10. Deviations from the approved plan

1. **`default_fallback` identifier.** The task text (§3) requires the fallback be "identifiable as `default_fallback`"; the plan body had earlier said `d3-default`. Implemented as **`default_fallback`** (the task wording is later and more specific).
2. **Per-unit composition.** All four components (ingredients, labour, batch overhead, fixed allocation) are divided by the sellable yield, then summed, with one final cent rounding — the spec §16 decisive example reads the plan's formula this way, and this matches every worked example (§8, §10A, §10B).
3. **`ProductionBatchCreate` fields optional.** `expected_yield` / `batch_overhead_estimated` default from the recipe when omitted (the recipe is the source of estimates); explicit values still win.
4. **`labour_rates.created_at`.** The model sketch had no creation timestamp, but the API surface (append-only history, ordered listing) needs it. New table → additive by construction.
5. **Route registration shape.** 14 paths under the `/api/admin/costing` prefix (23 FastAPI route objects incl. aliases); the plan named the resources, not exact path counts.
6. **`costing_routes` alias in `app/__init__.py`.** `from .routers import costing` would rebind the package attribute `app.costing` to the router module, shadowing `app/costing.py` for `from app import costing`. The alias avoids the collision; behaviour is identical.

No other deviations. Where the plan was silent the spec examples were followed verbatim.

## 11. Unresolved issues

- **None within Phase 2 scope.** The two open items are pre-existing and outside this phase:
  - the frontend >500 kB chunk-size build warning (present since before v3.13.0; cosmetic);
  - the Stripe webhook endpoint still pointing at the dead `onrender.com` subdomain — user-side standing item, deliberately untouched (Phase 2 §6 forbids Stripe changes).

## 12. Confirmations required by the task

- **Phase 1/1.1 preserved.** All 11 modified files from Phase 1/1.1 carry their changes unchanged; Phase 2 added to 4 of them and created 3 new files. The other 9 Phase 1/1.1 files were not opened for editing. The full suite (which includes every Phase 1/1.1 test) passes, and staging Stages A–E pass unmodified.
- **No production actions.** No connection to the production database; no Stripe API calls or mode changes; no Render actions; no deployment; no environment variables or secrets read or modified; Stripe remains TEST/Sandbox.
- **Nothing committed, pushed, or deployed.** Working tree state is exactly implementation + this documentation.

## 13. Diff summary

Modified (Phase 1/1.1 + Phase 2 combined working tree vs `e5ab209`):

```
 backend/app/__init__.py                |   5 +
 backend/app/models.py                  | 259 +++++++++++++++++++++-
 backend/app/routers/admin.py           | 166 ++++++++++----
 backend/app/routers/orders.py          |  37 +++-
 backend/app/routers/payments.py        |    8 +
 backend/app/schemas.py                 | 387 ++++++++++++++++++++++++++++++++-
 backend/app/stripe_gateway.py          |  57 ++++-
 backend/tests/test_api.py              |  224 +++++++++++++++++++
 backend/tests/test_stripe_gateway.py   |   96 ++++++++
 frontend/src/lib/format.js             |  66 +++++-
 frontend/src/lib/format.test.js        |   32 +++
 frontend/src/pages/admin/Dashboard.jsx |   34 ++-
 frontend/src/pages/admin/Users.jsx     |   10 +-
 13 files changed, 1304 insertions(+), 77 deletions(-)
```

Of these, Phase 2's edits touch `__init__.py`, `models.py`, `routers/admin.py`, `schemas.py` (additive hunks on top of Phase 1/1.1). The remaining 9 files are Phase 1/1.1 only, untouched by Phase 2.

New untracked files created by Phase 2: `backend/app/costing.py` (662 lines), `backend/app/routers/costing.py` (861 lines), `backend/tests/test_costing.py` (1175 lines), `docs/business/PHASE2_IMPLEMENTATION_REPORT.md` (this report).

Pre-existing untracked paths (not created by Phase 2, not modified except `Temp/staging_verify.py` which gained Stage F): `Temp/` (staging harness — throwaway, never to be committed) and `docs/` (business documentation including the approved plan).

---

**Stopped for owner review.** No commit, no push, no deploy.
