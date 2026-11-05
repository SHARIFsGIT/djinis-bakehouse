# Djini's Bakehouse — Phase 0: Real Market Pricing Audit & Initial Benchmark Dataset

**Type:** Read-only inspection & research report (Phase 0 of the Real Market Pricing Implementation)
**Date:** 2026-11-04 (all evidence accessed this date)
**Baseline:** `DJINIS_BAKEHOUSE_BUSINESS_PRICING_SPEC_V1.1.md` (decisions D1–D18) · Production v3.14.0 @ commit `e5ab209`
**Companion files:** `templates/benchmark_evidence.csv`, `templates/benchmark_evidence.json`, `templates/product_cost_data_template.csv`

**Constraints honored:** No application code, database, migration, environment variable, Stripe setting or production configuration was modified. Nothing was committed, pushed or deployed. No products were created, no orders accepted, no payments enabled. No product costs or recommended prices were invented. **The only files created are the four documentation files listed above (all untracked).**

**Evidence honesty statement:** No database records were inspected. Product-catalogue facts come from source code (`backend/app/seed.py`, `backend/app/models.py`, routers) cross-checked against the **public** API (`GET https://djinisbakehouse.com/api/products?per_page=48` and one product detail endpoint) on 2026-11-04. Competitor prices come only from named public sources, each row carrying its URL, access date, evidence confidence and comparability limitations.

---

## 1. Executive summary

1. **Catalogue (Task A):** 25 products across Breads (8), Pastries (10), Desserts (7), all published and available on the live storefront (Christmas Stollen in stock at 0). Full descriptive, ingredient (free-text), allergen (free-text) and image data exists for every product. Current prices are **USD, tax-exclusive** (pre-cutover state by design).
2. **Costing capability (Task B): none exists.** No ingredients table, no recipes, no packaging/labour/yield/wastage/overhead records, no cost or price history. The only cost-adjacent field is `products.prep_time_minutes`, which is display-only. The margin floor (40 %, D14) is therefore not computable for any product today.
3. **Benchmarking capability (Task C): none exists** — nothing to duplicate. Spec §11 already defines the methodology (≥3 comparables per category where available, per-100 g normalization, 0.6 Bremen / 0.4 Germany weighting, limited-sample warnings, never invent data); the gap is purely implementation.
4. **Data readiness (Task D):** catalogue data = **Verified**; VAT classification = **pending** (adviser); every costing input = **Missing**; per-product **weight/portion size = requires admin confirmation** and is the single most blocking gap — it blocks per-100 g normalization and therefore any defensible comparison against competitor evidence (all of which is per-piece or per-kg).
5. **Market evidence (Task E):** 28 evidence rows collected (12 High / 9 Medium / 7 Low confidence). Breads are well covered in Bremen (Effenberger, 6 SKUs URL-verified); croissants are triangulated across Kamps channels (€1.59–€2.90); stollen are strong Germany-wide (Bäckerei Claus URL-verified, Dr. Quendt et al.). Several niche items (maritozzo, croffle, vol-au-vent, rhum horn…) have **no verified online comparable — in-store collection required**.
6. **No recommended prices are derived in this report** — benchmarks inform, the owner decides (D13).

---

## 2. Task A — Product catalogue (25 products)

Source: `backend/app/seed.py:71–148` (PRODUCTS tuple), cross-checked against the live public API on 2026-11-04 (IDs, prices, sale prices, availability byte-matched; IDs 1–25 confirmed in seed order). All products: `is_available = True`, image at `/static/products/{slug}.jpg`, description + ingredients + allergens present (free-text, not structured).

| ID | Product | Category | Price (USD) | Sale (USD) | Stock | Featured | VAT status |
|----|---------|----------|-------------|------------|-------|----------|------------|
| 1 | French Baguette | Breads | 4.50 | — | 36 | — | pending |
| 2 | Fresh Bagels | Breads | 6.50 | — | 24 | — | pending |
| 3 | Beef Floss Bread | Breads | 7.50 | 5.95 | 18 | — | pending |
| 4 | Triple Cheese Bread | Breads | 8.50 | — | 14 | ✓ | pending |
| 5 | Blueberry Cheese Bread | Breads | 7.50 | 5.95 | 16 | — | pending |
| 6 | Wool Roll Bread | Breads | 6.50 | — | 20 | ✓ | pending |
| 7 | Rosemary Focaccia | Breads | 7.00 | — | 12 | — | pending |
| 8 | Garden Focaccia | Breads | 8.50 | — | 10 | ✓ | pending |
| 9 | Butter Croissant | Pastries | 3.75 | — | 48 | ✓ | pending |
| 10 | Croffle | Pastries | 5.50 | — | 22 | ✓ | pending |
| 11 | Apple Strudel | Pastries | 5.25 | — | 15 | — | pending |
| 12 | Beef Curry Puff | Pastries | 4.75 | — | 26 | ✓ | pending |
| 13 | Chocolate Cream Puff | Pastries | 4.25 | — | 30 | — | pending |
| 14 | Fruit Cushion Pastry | Pastries | 5.75 | — | 12 | — | pending |
| 15 | Rhum Horn | Pastries | 4.95 | 3.95 | 18 | — | pending |
| 16 | Vol-au-Vent | Pastries | 5.25 | — | 4 | — | pending |
| 17 | Pretzels & Bagels | Pastries | 6.25 | 4.95 | 16 | — | pending |
| 18 | Cheese Straws | Pastries | 4.50 | — | 20 | — | pending |
| 19 | Glazed Doughnuts | Desserts | 5.95 | — | 28 | ✓ | pending |
| 20 | Maritozzo | Desserts | 6.25 | 5.25 | 16 | — | pending |
| 21 | Strawberry Maritozzo | Desserts | 6.95 | — | 14 | ✓ | pending |
| 22 | Strawberry Mille-Feuille | Desserts | 7.25 | — | 8 | — | pending |
| 23 | Strawberry Puff | Desserts | 5.95 | — | 11 | — | pending |
| 24 | Double Chocolate Cream Bun | Desserts | 5.50 | — | 19 | — | pending |
| 25 | Christmas Stollen | Desserts | 12.00 | 9.95 | **0** | — | pending |

Notes:
- **VAT status:** seed inserts products without VAT fields (`vat_category` NULL, `vat_review_status` 'pending' per `models.py:144–184` defaults). The classification workflow shipped in v3.14.0 has not been exercised in production. Production database not inspected — statement derives from seed logic + schema defaults.
- Multi-item products (from descriptions): Fresh Bagels = 4 bagels; Pretzels & Bagels = 2+2; Cheese Straws = a dozen; Glazed Doughnuts = six. Weights are **not** stated anywhere — see Task D.
- 6 products carry sale prices; per D15 these must eventually pass the same 40 % margin gate — impossible until costs exist.

---

## 3. Task B — Existing costing capabilities: inventory

**Result: no costing machinery exists.** Repository-wide search for costing concepts (ingredient, recipe, packaging, overhead, wastage, batch yield, cost snapshot, price history) matches only the free-text `products.ingredients` display field.

| Required capability (spec §8–§10, §21) | Status | Evidence |
|---|---|---|
| Ingredient catalogue + purchase units/categories | **Missing** | no table/model |
| Ingredient prices + provenance + review scheduling | **Missing** | no table/model |
| Recipes (per-product ingredient lines, loss %) | **Missing** | only free-text `ingredients` (`models.py:159`) |
| Packaging costs | **Missing** | nothing |
| Labour minutes costed (hands-on/unattended) | **Missing** | `prep_time_minutes` exists but is display-only |
| Batch yield / wastage | **Missing** | `products.stock` is the sellable counter only |
| Batch overhead (est./actual) | **Missing** | nothing |
| Monthly fixed overhead | **Missing** | nothing |
| Cost history / cost snapshots | **Missing** | nothing |
| Price history + approvals (`price_history`) | **Missing** | proposed spec §21 |
| Margin/charm engine (`app/costing.py`) | **Missing** | proposed spec §22 |

**Reusable foundations (patterns to mirror, not costing features):** `app/vat.py` (Decimal, pure, refusal-only), the additive migration machinery (`_ADDITIVE_COLUMNS` + `_upgrade_schema`, proven on PostgreSQL and SQLite in `Temp/staging_verify.out`, ALL CHECKS PASSED), the `product_vat_events` append-only audit pattern, and the `_AdminVatFields` public/admin serialization separation.

---

## 4. Task C — Existing benchmarking capabilities

**Result: none — no duplication risk.** No competitor/benchmark/market-position code, tables or UI exist. Spec §11 already specifies the target methodology; the audit found one **schema gap** worth a spec amendment before Phase 3:

> **P0-F1 (finding):** the §21 `benchmark_entries` proposal (id, product_id?, category_id?, competitor_name, region, location, product_name, size_g, price, currency, source_url, checked_at) lacks the provenance fields the Phase-0 evidence standard requires: `evidence_confidence`, `comparability_limitations`, `price_basis`, `source_type`. Recommend amending the spec so collected evidence keeps its quality tier inside the system. (Documentation-level recommendation only — nothing implemented.)

---

## 5. Task D — Data readiness per product

Full 25-row matrix: `templates/product_cost_data_template.csv`. Summary classification (applies uniformly to all 25 products):

| Data class | Readiness | Detail |
|---|---|---|
| Name/category/description/ingredients text/allergens/image/price/availability | **Verified** | seed + live API cross-check |
| VAT classification | **Requires adviser confirmation** | all 25 pending; workflow exists, unused |
| **Weight / portion size** | **Requires admin confirmation** | absent everywhere; descriptions imply counts (e.g. "four bagels") but never grams |
| Recipe (structured) | **Missing** | free-text only |
| Ingredient costs | **Missing** | no data, no table |
| Packaging cost | **Missing** | |
| Labour cost | **Missing** | `prep_time_minutes` is not a cost |
| Batch yield & wastage | **Missing** | |
| Fixed/batch overhead | **Missing** | |

**Blockers to a defensible selling price (in dependency order):**
1. **W-1 Product weights/portions** (admin) — blocks per-100 g normalization, hence every §11 comparison, and blocks size-honest comparison for stollen (competitors sell 500/750/1000 g units vs Djini's unweighted €12.00-equivalent).
2. **W-2 Recipes + ingredient prices** (owner entry) — blocks any cost figure, hence margin floor checks (D14), charm derivation (D2) and quote costing (D16).
3. **W-3 Monthly overhead + labour rate** (owner entry; €20/h assumed constant per spec §30) — blocks full cost.
4. **W-4 VAT classifications** (adviser) — blocks EUR-inclusive pricing and publication gates.

Until W-2/W-3 land, **no product's current USD price can be judged margin-safe or unsafe** — the first honest margin computation becomes possible only after Phase 2 data entry.

---

## 6. Task E — Initial market-research dataset (verified public evidence)

Full dataset: `templates/benchmark_evidence.csv` (+ JSON twin with schema notes). **28 rows · 12 High · 9 Medium · 7 Low confidence · all accessed 2026-11-04 · Bremen 60 % / Germany-wide 40 % split per §11.**

**Evidence hierarchy applied:** High = direct page fetch with visible price · Medium = official-domain snippet / delivery platform / reputable press · Low = unofficial lists, social media, aggregators, bot-gated pages · Excluded = generic market estimates, non-comparable geographies.

### Category coverage summary

| Djini category | Verified rows | Bremen-region rows | ≥3 comparables? | Anchor evidence |
|---|---|---|---|---|
| Breads | 7 | 7 | **Yes** | Effenberger Bremen, 6 SKUs URL-verified: €5.50–€8.00 per 500–750 g loaf (€7.33–€16.00/kg) |
| Pastries | 12 | 0 priced (Kamps Bremen stores exist; verified prices are Krefeld/Berlin/Düsseldorf) | Quantity yes, **Bremen quality no** | Croissant triangulation: Kamps €1.59 (URL-verified) / €1.59–€2.20 (own delivery, other cities) / €2.90 (Wolt) / ~€1.70 (GoFlink) / €1.99 (BackWerk, unofficial) / ~€0.99 supermarket floor |
| Desserts | 9 | 1 (Rosinen-Stuten €7.40/500 g) | Stollen yes; doughnuts range-only | Bäckerei Claus URL-verified: Dresdner Christstollen ab €14.00, 1.0 kg €26.50, Stollenkonfekt €6.50, all "inkl. 7 % MwSt."; Dr. Quendt 1000 g €9.99 (snippet); Krapfen €1.50–2.00 typical (press survey, Franconia) |

**Weighted §11 index: not computable at this stage** — Djini weights unknown (W-1), and Pastries/Desserts lack 3 reliable Bremen comparables, so the limited-sample warning would apply to every category today.

**Honest flags baked into the dataset:** Effenberger's own per-kg figure for Früchtebrot (15,60 €/kg) contradicts its line price (16,00 €/kg) — line price recorded as authoritative. Kamps delivery-menu prices carry a €80 minimum / no-weekend-delivery / non-Bremen-zone limitation. BackWerk prices come from an unofficial Google-Sites list (official pages publish no prices). GoFlink page is bot-gated (HTTP 403) — snippet-sourced, Low. Dr. Quendt's page is JS-rendered and its JSON endpoint errored — snippet-medium, verify in a browser before relying on it. Lidl US pricing excluded as non-comparable.

**Products with no verified online comparable — in-store collection required:** croffle, rhum horn, vol-au-vent, beef curry puff, fruit cushion pastry, cheese straws, maritozzo (both), mille-feuille, strawberry puff, chocolate cream bun, wool roll, focaccia (both), bagels. Identified Bremen collection targets (no published online prices): **Sam urban Baker** (Hillmannplatz), **Baguetterie Fillo** (Vegesack), **French Connection**, plus Kamps Bremen Hbf/airport in-store boards.

---

## 7. Proposed phases

The implementation phasing of spec v1.1 §33 is **unchanged** by this audit (Phase 1 currency safety first; VAT/historical/legal gates preserved). Phase 0 adds parallel **data-collection workstreams** that gate later phases:

| Workstream | Owner | Gates |
|---|---|---|
| Record weight/portion size for all 25 products (W-1) | admin, one sitting | benchmark normalization, Phase 3 recommendations |
| Enter recipes + ingredient costs + packaging (W-2) | owner | Phase 2 acceptance, any margin math |
| Enter monthly overhead forecast (W-3) | owner | Phase 2 full-cost views |
| VAT classifications (W-4) | adviser | Phase 4 inclusive checkout, publication gates |
| In-store Bremen price collection for uncovered products | owner (shop visits) | Phase 3 benchmark panel quality |
| Spec amendment: benchmark_entries provenance fields (P0-F1) | this desk, on approval | Phase 3 schema |
| Confirm 14-day quote validity (spec §30) | owner | Phase 6 configuration |

---

## 8. Exact files/modules requiring modification (for later phases; nothing changed now)

**New (backend):** `app/costing.py` (pure Decimal costing/charm/margin engine — spec §22); `app/routers/costing.py` (admin: ingredients, categories, recipes, batches, overhead, benchmarks, price approvals); `app/routers/quotes.py` (admin + tokenized public quote acceptance); matching schemas + test files.
**Modified (backend):** `app/models.py` (10–11 new tables per §21; optional nullable `products.current_cost` cache); `app/routers/orders.py` (coupon margin gate D14, effective promotional price D15, Phase 4 inclusive branch behind `tax_mode`); `app/routers/marketing.py` (storefront `tax_mode` exposure); `app/stripe_gateway.py:103/113/124/159` (**Phase 1 order-currency fix** — the audited latent money bug); `app/routers/admin.py` (desk surfaces, under-review rail); `app/seed.py` (new tables created **empty** — never seeded with invented data).
**Frontend:** admin costing/pricing desk, benchmark panel, quotation desk, cost-review center; `format.js` inclusive tax label; cart/checkout per-rate VAT rows; public quote-acceptance page.
**Reused unchanged:** `vat.py` discipline, `_upgrade_schema` additive migration, `product_vat_events`, `_AdminVatFields`, coupons (percent-off fits allocation).

---

## 9. Risks and migration requirements

1. **Additive-only DDL, both engines** — every new table lands via `create_all` + `_upgrade_schema`; raw DDL must run on PostgreSQL (prod) **and** SQLite (dev/tests). Precedent lesson: a `boolean DEFAULT 1` in raw DDL broke the prod Postgres boot on 2026-11-02.
2. **Verify on staged PostgreSQL before deploy** — repeat the `Temp/staging_verify.out` pattern (fresh boot, API exercise, idempotent re-boot, legacy upgrade path, gap-fill never overwrites) for Phases 2/3/4/6; ALL CHECKS PASSED is the release gate.
3. **New tables start empty** — ingredients, recipes, overhead, benchmarks: owner/admin entry only. The Phase-0 CSV/JSON dataset is a **manual-entry source with provenance fields**, not a seed file; no auto-import (preserves §11 "never invent data" and auditability).
4. **Append-only ledgers** — `price_history`, `quote_events`, closed `production_batches`: no UPDATE/DELETE paths, mirroring `product_vat_events`.
5. **Phase ordering is a hard requirement** — the Stripe order-currency fix (§24) must precede any EUR work; USD behaviour stays byte-identical; historical orders immutable.
6. **Risk R10 already material** (benchmark sparsity): Bremen pastry/dessert coverage is below the 3-comparable bar; the §11 limited-sample warning must ship with the Phase 3 panel, not be deferred.
7. **Stollen seasonality** — Christmas Stollen stock is 0 and stollen evidence is seasonal; collecting Bremen stollen prices in-store before the season ends preserves the strongest comparable window.
8. **No price recommendations in this report** — deliberately. Benchmarks inform; the owner decides (D13). Margin math becomes possible only after W-2/W-3.

---

## 10. Deliverables and status

| File | Content |
|---|---|
| `docs/business/PHASE0_REAL_MARKET_PRICING_AUDIT.md` | this report |
| `docs/business/templates/benchmark_evidence.csv` | 28-row evidence dataset (Task E columns + §21 alignment) |
| `docs/business/templates/benchmark_evidence.json` | same dataset, structured, with schema/provenance notes and P0-F1 spec-amendment finding |
| `docs/business/templates/product_cost_data_template.csv` | 25-product cost-data readiness matrix (Task D) |

All files are new and untracked. **No application code, database, environment, Stripe or production configuration was touched. Stopping here for review.**
