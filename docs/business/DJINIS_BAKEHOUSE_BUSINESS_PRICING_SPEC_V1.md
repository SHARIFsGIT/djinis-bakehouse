# Djini's Bakehouse — Business & Pricing Specification v1.0

**Status:** Draft for review — not approved for implementation.
**Baseline audited:** commit `e5ab20928bdc6cdd2ad15521e3c3d03778a4fe9a` (v3.14.0), deployed at https://djinisbakehouse.com on 2026-11-04.
**Document role:** This specification describes *what should be built and why*. **It does not authorize code changes, database migrations, configuration changes, or production deployment.** Every implementation step requires the owner's explicit approval, and every VAT/legal conclusion requires the written confirmation of the appointed German tax adviser and the authorities listed in §29.

---

## 1. Executive summary

Djini's Bakehouse runs a working USD, tax-exclusive storefront (FastAPI + SQLAlchemy + PostgreSQL backend, Vite SPA frontend, Stripe Sandbox card payments plus cash-on-delivery, deployed on Render). Version 3.14.0 shipped the data foundation for a EUR migration: per-order currency, per-line VAT snapshot columns, an effective-dated 7 %/19 % rate table, a Decimal VAT engine that refuses to price unapproved products, and an admin classification workflow with a full audit ledger.

What does **not** exist yet is everything this specification is really about: the shop has **no cost data at all** — no ingredient catalogue, no recipes with quantities, no wastage records, no labour split, no overhead allocation, no margin math, no price-approval workflow, no price history, no market benchmarks. Today an admin can set any price and hand out any percentage coupon; nothing computes or protects the confirmed **40 % minimum gross margin**, and nothing prices with the confirmed **€0.49/€0.99 charm endings**.

This document specifies a costing → pricing → approval → VAT-inclusive EUR checkout pipeline in eight phases (§31), with testable acceptance criteria (§26) and numeric examples throughout. The plan is fail-closed wherever the rules are unforgiving: no publication without complete cost/ingredient/allergen data and an approved VAT classification; no discount that breaches the margin floor; no inclusive checkout until classifications and delivery-VAT treatment are professionally confirmed.

**Estimated implementation shape:** ~6 new database tables, one new backend costing/pricing module family, one admin UI surface (cost editor, pricing recommendation, benchmarks, price history), and a guarded cutover. No change to the existing checkout arithmetic until Phase 4, and historical USD orders remain untouched forever.

## 2. Business objectives and scope

**Objectives**

1. Move customer-facing pricing to EUR, VAT-inclusive, with legally confirmed classifications.
2. Price every product from a defensible cost base: ingredients (with wastage), labour, hybrid overhead — with recorded provenance and confidence for every estimate.
3. Enforce a **minimum 40 % gross margin on net selling price**, before *and* after discounts.
4. Publish only charm prices (€0.49 / €0.99 endings), rounded up, never in breach of the margin floor.
5. Keep publication gated on complete food information (ingredients, allergens) and approved VAT classification.
6. Preserve every historical order exactly as placed (USD, exclusive) — immutable.

**In scope:** ingredient catalogue and costing; recipes and yield; wastage; labour; overhead; margin and charm pricing; price approval and history; market benchmarking; discount margin rules; VAT-inclusive EUR checkout; admin workflows; Stripe EUR; reporting by currency.

**Out of scope (confirmed):** supplier integrations; automatic invoice extraction (optional free-text invoice references only); multi-currency *selling* (EUR only after cutover; USD is history); Stripe Live activation (separate future authorization); automatic repricing without human approval; inventing market data.

## 3. Confirmed business decisions

| # | Decision | Status |
|---|---|---|
| D1 | Target currency EUR; VAT-inclusive customer prices after legal/tax approval | Confirmed |
| D2 | Minimum gross margin 40 % of net selling price (margin, not markup) | Confirmed |
| D3 | Labour cost €20 per hands-on production hour; unattended time excluded unless separately justified | Confirmed |
| D4 | Charm endings €0.49 / €0.99 only; always round up; never breach margin | Confirmed |
| D5 | Ingredient costs entered manually; estimates first, actuals replace them over time | Confirmed |
| D6 | Wastage recorded per ingredient/recipe — **no generic percentage** | Confirmed |
| D7 | Cost allocation by *sellable* yield; expected-vs-actual variance recorded, history never rewritten | Confirmed |
| D8 | Hybrid overhead: batch-level (manual) + fixed monthly (via forecast production hours) | Confirmed |
| D9 | Benchmark weighting Bremen 60 % / Germany 40 %; ≥ 3 comparables or limited-sample warning; never invent data | Confirmed |
| D10 | Price changes require admin approval, take effect immediately for new orders, full audit trail | Confirmed |
| D11 | 40 % margin is a minimum **after** discounts; basket discounts allocated proportionally with exact-cent reconciliation; delivery is never product revenue/cost | Confirmed |
| D12 | Publication blocked by incomplete cost/ingredient/allergen data or unapproved VAT classification; drafts allowed in the admin catalogue | Confirmed |
| D13 | Manual EUR gross selling prices — the owner decides each final price (the system recommends, never auto-publishes) | Confirmed |

**Recommendations (not confirmed — need owner decision, see §30):** discount-aware pricing headroom (D14); treatment of the existing `sale_price` promotional field under the margin rule; bulk-order quotation policy; single- vs multi-step approval.

## 4. Pricing and gross-margin formulas

**Gross margin** (the only margin definition used in this system):

```
Margin = (Net Selling Price − Total Product Cost) / Net Selling Price
```

**Minimum net selling price** (margin floor 0.40):

```
Minimum Net Price = Total Product Cost / (1 − 0.40) = Total Product Cost / 0.60
```

**Total Product Cost** (§5–§9):

```
Total Product Cost =
    Ingredient Cost (purchase quantities incl. wastage)     §5, §6
  + Labour Cost (hands-on minutes / 60 × €20)                §9
  + Batch Overhead per unit (actual sellable yield)          §10A
  + Fixed Monthly Overhead per unit (hands-on hours × rate)  §10B
```

**VAT is always calculated separately from the net price.** Net price is a business figure; gross price is its VAT-loaded presentation:

```
Gross Price = Net Price × (1 + VAT rate)      (7 % or 19 %, per approved classification)
Net Price    = Gross Price / (1 + VAT rate)   (rounded half-up to the cent; VAT = gross − net)
```

**Margin vs markup — explicitly.** Markup = (Price − Cost)/Cost. A 40 % margin equals a 66.67 % markup. The system computes margin only; any screen showing "markup" is prohibited to avoid confusion.

**Numeric example (base case, 7 % VAT):**

| Step | Value |
|---|---|
| Total Product Cost | €2.10 |
| Minimum Net Price = 2.10 / 0.60 | **€3.50** |
| Minimum Gross = 3.50 × 1.07 | €3.745 |
| Charm price (round **up** to .49/.99) | **€3.99** |
| Derived net = 3.99 / 1.07 → 3.7290 → **€3.73** | VAT = 3.99 − 3.73 = €0.26 |
| Achieved margin = (3.73 − 2.10) / 3.73 | **43.70 % ≥ 40 % ✓** |

**Same cost at 19 % VAT:** min net €3.50 → min gross €4.165 → charm **€4.49** → net 4.49/1.19 = €3.77 → VAT €0.72 → margin (3.77−2.10)/3.77 = **44.30 % ✓**.

**Current implementation:** none of this exists in code. `products.price`/`sale_price` are free-admin-entry numbers; no cost fields, no margin computation anywhere. *Gap: entire module.*

## 5. Charm-pricing algorithm

**Rule set (confirmed):** endings €0.49 and €0.99 only; always round **up** to the next eligible ending; calculate the minimum *net* price first; apply VAT per the approved classification; verify the margin on the **cent-rounded** derived net; never round down into a margin breach.

**Exact algorithm (the implementable form):**

```
charm_price(cost, vat_rate):
  1. min_net   = cost / 0.60                      # full precision, not rounded
  2. min_gross = min_net × (1 + vat_rate)         # full precision
  3. candidates = every €0.49 / €0.99 value ≥ min_gross   (ascending)
  4. for each candidate gross g:
         net = round_half_up(g / (1 + vat_rate))   # to the cent
         if (net − cost) / net ≥ 0.40:  RETURN g  # margin verified on rounded net
  5. (loop always terminates: each step adds €0.50 gross ≈ €0.47 net)
```

**Why the verification loop is mandatory (cent-level edge case):** the derived net is rounded to the cent, and half-up rounding can move the net *down* by up to just under half a cent. If `min_net` lands within that sub-cent window below a rounding boundary, the rounded net can fall below `min_net` and the margin dips under 40 % by a fraction of a percent. The gap between charm endings (€0.50) makes this rare, but it is mathematically possible; therefore margin is *verified* in step 4 and the algorithm steps up an ending rather than shipping a breach. A price that already lands exactly on an eligible ending (min_gross = €4.99) is kept — "up to the next eligible" includes the value itself when it is eligible.

**Documented edge cases:**

| Case | Behaviour |
|---|---|
| min_gross exactly €X.49 or €X.99 | That value is the first candidate (never rounded further up) |
| Derived net rounds down below min_net | Step up to the next ending; re-verify (loop) |
| Cost rises so the current price fails step 4 | Product flagged under-review (§13); price change needs approval |
| VAT classification changes (7 % ↔ 19 %) | Charm re-run; new gross requires approval (§13) |
| Discount planning | Charm pricing runs on undiscounted net; discount headroom is §14/§30-Q1 |

## 6. Ingredient and recipe costing model

**Ingredient catalogue (per ingredient, admin-entered):** name; purchase quantity; purchase price; measurement unit; **effective cost per unit** (derived: purchase price / purchase quantity, e.g. a 25 kg sack at €37.50 → €1.50/kg); optional invoice/receipt reference; provenance fields (§12: source/assumption, last-reviewed date, confidence low/medium/high, estimated-vs-actual flag).

**Recipe (per product):** list of ingredient lines — usable quantity + unit (e.g. 500 g flour, 6 eggs); per-line preparation loss (§7); plus labour split (§9) and batch fields (§8). The existing `products.ingredients` free-text field remains the customer-facing ingredient statement; the recipe table is the costing truth. (Publication gate §17 cross-checks that the statement exists.)

**Unit conversion acceptance example:** flour purchased 25 kg @ €37.50 → €1.50/kg; recipe needs 500 g → base €0.75; with 5 % preparation loss the purchase quantity is 500/0.95 = 526.32 g → **€0.79** (1.5789 → round half-up at the final product-cost step; full precision is kept inside the sum).

**Estimates → actuals:** an ingredient starts as an estimate; recording a new purchase replaces the effective cost per unit from its effective date. Historic cost computations are never recomputed retroactively — cost *snapshots* (§12) preserve what each price decision was based on.

**Current implementation:** none. No ingredient, recipe, or cost tables exist. *Gap: entire module.*

## 7. Wastage calculation

Recorded per ingredient line or per recipe (both supported):

- **Required usable quantity** — what the recipe needs in the bowl.
- **Expected preparation/production loss** — % or absolute, per line (e.g. 5 % flour sift loss, 1 egg per 12 cracked = 8.33 %).
- **Expected purchase quantity** = usable / (1 − loss rate). Example: 1000 g usable at 5 % → 1052.63 g purchased.
- **Actual usage / actual wastage** — recorded when known (production batch entries, §8); replaces the estimate for *future* costing once available.

**Product cost always uses the quantity that must actually be purchased.** A generic wastage percentage applied to every product is explicitly prohibited — each recipe line carries its own loss figure (0 % is a valid, explicit entry).

## 8. Recipe yield and production-batch workflow

- Every recipe carries a **standard expected batch yield** (e.g. 24 croissants).
- After production, the admin records the **actual sellable yield** (e.g. 22 — two misshapen).
- **Cost allocation uses sellable output**: batch cost €26.40 across 22 units → **€1.20/unit**, not the theoretical €1.10.
- **Variance recorded, history immutable**: expected 24 vs actual 22 → −8.33 % variance stored on the batch record; historical batches and completed orders are never edited.
- Rolling actual-vs-expected variance informs the *next* costing's expected yield (recommendation shown to the admin; the admin confirms the figure used).

**Production batch record:** product, date, expected yield, actual sellable yield, actual batch overhead (§10A), actual ingredient usage/wastage where captured (§7), free-text notes.

## 9. Labour costing

```
Labour Cost = hands-on minutes / 60 × €20
```

- Recipes record **hands-on minutes** separately from unattended time (proofing, oven, cooling). Unattended time is excluded unless separately justified as a measurable operating cost — any such justification is recorded in the overhead ledger (§10), never silently in labour.
- Examples: 12 min → €4.00; 15 min → €5.00; 45 min → €15.00; 7.5 min → €2.50; 90 min → €30.00.
- **Current implementation:** `Product.prep_time_minutes` is a single display-only field (shown as "Ready in…" — verified: referenced only in serialization schemas, never in pricing). *Gap: split field, costing use.*

## 10. Hybrid overhead allocation

**A. Batch-related overhead** — manually entered per production batch (estimated initially, actuals later): directly attributable electricity, production consumables, batch-specific expenses. Allocated across the **actual sellable yield** of that batch: €4.40 over 22 units → €0.20/unit.

**B. Fixed monthly overhead** — a monthly record holds the forecast fixed overhead total (€) and the **expected monthly production hours** (realistic forecast; reviewed monthly). Rate = total / forecast hours. Example: €800 / 160 h = **€5.00 per production hour**. Per product: hands-on minutes / 60 × rate → 24 min → 0.4 h × €5.00 = €2.00.

**Month-end review:** record actual overhead and actual hours; store the variance (€ and h) on the month record. Variances inform next month's forecast; they never rewrite past costings or order history.

**Double-counting prevention (structural + procedural):**

1. The two ledgers are separate tables with separate categories; the monthly fixed-overhead record lists the cost categories it includes, and the entry form warns when a category also appears in batch overhead entries.
2. Procedure: the monthly review checklist (admin workflow) explicitly asks "is anything in fixed overhead also entered per-batch?" — a confirmed overlap reduces the fixed pool.
3. Acceptance test AC-8 (§26) fails the build if a category flagged in both places doesn't raise the warning.

## 11. Market benchmarking methodology

- **Comparables:** minimum 3 per category *where available*; similar product type, size, finish, packaging; per competitor entry: competitor name, location, product name, size (g), price, currency, source URL, date checked, region tag (`bremen` | `germany`).
- **Normalization:** price per unit and per 100 g.
- **Weighted index:** `0.6 × Bremen normalized average + 0.4 × Germany-wide normalized average` (computed per product/category from its comparable set).
- **Limited-sample warning:** fewer than 3 reliable comparables in either region → the benchmark panel shows a prominent limited-sample warning and the weighted index is labelled indicative only.
- **Never invent data.** No synthetic competitors, no estimated competitor prices. An empty benchmark table is a valid state.
- **Benchmarks inform, never determine:** the pricing recommendation screen shows the index next to the margin-based minimum and the charm candidate; the owner's manual decision (D13) is recorded as the approved price.

*Current implementation: none. Gap: table + admin panel.*

## 12. Cost-confidence and data-provenance model

Every estimated cost (ingredient effective cost, loss rate, labour split, batch overhead, expected yield, fixed-overhead forecast) records:

| Field | Values |
|---|---|
| Source / assumption | free text (e.g. "regional supermarket shelf price, Bremen") |
| Last reviewed | date |
| Confidence | low / medium / high |
| Status | estimated / actual |

Rules: estimates are never presented as verified accounting figures (every admin surface labels them); a cost older than a configurable review interval (default 90 days) shows a "review due" flag; product-cost *snapshots* freeze the full cost composition (per-ingredient figures, labour, overhead rates) behind every approved price, so any historical price decision can be reconstructed exactly.

## 13. Cost-change and price-approval workflow

**When an ingredient cost (or any cost input) changes:**

1. Identify affected products (recipe dependency).
2. Recompute their costs and suggested charm prices automatically.
3. Recompute the margin against the **currently approved** price.
4. Flag every product below 40 % on the desk's "under-review" rail.
5. Admin review and approval required before any published price changes. *No auto-repricing, ever.*
6. Historical prices and completed orders unchanged (orders are snapshots; `order_items` never recomputed).

**Approval record (immutable):** previous price, new price, approving admin, approval timestamp, reason / change reference (e.g. "flour +12 %, invoice 2026-11-08").

**Effectivity:** approved changes apply immediately to new orders. Publication of a first-time price follows the same approval path.

**Current implementation:** none — price edits are instant and unaudited (`admin.py:update_product`). The VAT workflow (`admin.py:update_product_vat` + `ProductVatEvent`) is the pattern to replicate for prices.

## 14. Discount and coupon rules

- The 40 % margin is a minimum **after** discounts — per affected product, checked individually.
- **Rejection:** a coupon, promotional price, or manual discount that would push any affected product below 40 % is rejected at the point of application (quote and checkout both). Ordinary admin accounts cannot override the floor. (Bulk-order quotations are negotiated off-system until a policy is decided — §30-Q3.)
- **Basket-wide discounts** are allocated proportionally to each product's pre-discount selling value, with **largest-remainder cent reconciliation** so the allocated parts sum to the original discount exactly.
- **Delivery charges are never product revenue or cost** — margin math excludes delivery entirely (delivery VAT is §16).

**Proportional allocation — worked example.** Basket: 2 × Product A (gross €3.73 each @ 7 %) and 1 × Product B (gross €5.19 @ 19 %). Subtotal €12.65. Coupon 10 % → total discount = round(1.265, 2) = **€1.27**. Exact per-line shares: A €0.746, B €0.519. Largest-remainder allocation: floor to A €0.74, B €0.51 (sum €1.25); two cents remain; order by fractional part (B .9, A .6) → B +1 → €0.52, A +1 → €0.75. **Sum = €1.27 exactly.** ✓

**VAT after allocation (per rate, exact cents):** A: gross 7.46 − 0.75 = 6.71 → net 6.71/1.07 = 6.2710 → **€6.27**, VAT €0.44. B: 5.19 − 0.52 = 4.67 → net 4.67/1.19 = 3.9244 → **€3.92**, VAT €0.75. Reconciliation: 6.71 + 4.67 = 11.38 = 12.65 − 1.27 ✓; rate groups reassemble (6.27+0.44=6.71; 3.92+0.75=4.67) ✓.

**Margin-after-discount — rejection example.** Product A net €3.73, cost €2.10 (margin 43.7 %). A 10 % coupon → allocated net €3.36 → margin (3.36−2.10)/3.36 = **37.5 % < 40 % → the coupon is refused for a basket containing only A.** In the mixed basket above, B's higher margin can carry the basket total — the rule checks **each product individually**, so A at 37.5 % makes even the mixed basket ineligible for the 10 % code. Pricing with discount headroom is therefore a real business question (§30-Q1): to sustain d % off, base net must be ≥ cost / (0.6 × (1 − d)) — for 10 %: cost/0.54.

**Current implementation:** percent-only coupons (`Coupon.percent_off`), applied basket-wide with no margin knowledge; `sale_price` promotional field unguarded. *Gap: margin gate at quote/checkout/coupon-apply; sale_price policy.*

## 15. VAT-inclusive pricing architecture

The target architecture (Phase 4) extends the shipped v3.14.0 foundation:

- **Classification & approval:** `products.vat_category` + `vat_review_status` (approved only via `admin.py:update_product_vat`, fully audited in `product_vat_events`). Fail-closed: `vat.py:resolve_product_rate` raises for unclassified / unapproved / adviser-parked products — checkout refuses the basket.
- **Rates:** effective-dated `vat_rates` rows (currently REDUCED 7.00 %, STANDARD 19.00 % from 2026-01-01 — *provisional values, not legal conclusions*). A rate change is a new dated row; history never moves.
- **Per-line pricing:** `vat.py:price_line_inclusive` (exists, tested, unwired) → per-line `VatLine` snapshots written to the seven `order_items` VAT columns.
- **Basket pricer (to build):** mixed-rate basket pricing with proportional coupon allocation (§14), delivery treatment (§16), and a total that reconciles with `vat.py:summarize_by_rate` to the cent; results persisted as `order_tax_lines` rows.
- **Snapshots immutable:** every order freezes its figures (already true for USD; same columns carry EUR/VAT data).
- **Discount order of operations:** allocation **before** VAT extraction (per rate), exact-cent reconciliation — as worked in §14.
- **Mode switch:** `config.py:tax_mode` (`"exclusive"` today, read by nothing) gates the new path; USD/exclusive remains until the Phase 7 cutover.

## 16. Delivery VAT dependency

Delivery VAT treatment is **configurable and undecided**. Options to be confirmed by the tax adviser: (a) delivery follows the goods' rate (common when delivery is part of the main supply), (b) exempt/separately treated. Until configured, **inclusive mode refuses to complete checkout** (fail-closed) rather than guessing. The configuration is a settings value (e.g. `DELIVERY_VAT_MODE = follow_goods | exempt | unset`, default `unset`) plus the amount basis (net delivery fee loaded per the confirmed treatment). No code path may default silently to any treatment.

## 17. Product publication and food-information requirements

A product is **publishable** (visible and sellable) only when **all** hold:

1. Cost information complete: recipe with all ingredient lines costed (no missing effective cost per unit), labour split entered, overhead allocated (or explicitly deferred with recorded justification).
2. Ingredient statement present (`products.ingredients`).
3. Allergen information present (`products.allergens`) — **reviewed** before publication; generic cross-contact disclaimers never substitute for appropriate allergen controls (kitchen controls are an operating procedure, not a software feature — flagged in §29).
4. VAT classification approved (`vat_review_status = approved`).
5. A price approved through §13.

Incomplete products remain admin-visible drafts (`is_available=false` semantics plus a computed `publication_blockers` list per product). The storefront and checkout both enforce the gate — checkout re-validates server-side (never trusting the browser).

**Current implementation:** `is_available` is a bare boolean an admin can flip freely; no completeness checks. *Gap: computed gate.*

## 18. Admin dashboard and user workflows

New/extended desk surfaces (all behind existing `CurrentAdmin` auth — `security.py:get_current_admin`):

1. **Ingredient catalogue** — CRUD, provenance fields, estimate→actual replacement history.
2. **Recipe & cost editor** (per product): ingredient lines with loss, hands-on/unattended minutes, batch fields; live cost breakdown and margin preview at the current price.
3. **Pricing recommendation screen** — cost, minimum net (40 %), charm candidates with verified margins, benchmark index beside them (limited-sample warnings), current approved price, Approve/Reject with reason.
4. **Price history** — immutable log (§13).
5. **Production batches** — record actual yield/overhead/usage; variance view.
6. **Overhead monthly record** — forecast/actual hours + totals, variance, double-count warning.
7. **Market benchmark panel** — comparables CRUD, normalized view, weighted index.
8. **Under-review rail** — margin-flagged products after cost changes (extends the existing `needs_vat_review` pattern, `admin.py:all_products`).
9. **Dashboard stats** — revenue split by currency (USD/EUR never summed together).

## 19. Data model proposal

New tables (all additive; created by the existing `create_all` + additive-ALTER mechanism in `app/__init__.py` — no destructive migrations):

| Table | Key columns |
|---|---|
| `ingredients` | id, name, unit, purchase_qty, purchase_price, effective_cost_per_unit (derived, stored), source, confidence, is_estimate, last_reviewed, invoice_ref? |
| `recipe_items` | id, product_id FK, ingredient_id FK, usable_qty, unit, loss_pct, note |
| `recipes` (1:1 with product) | product_id FK unique, hands_on_minutes, unattended_minutes, expected_batch_yield, notes |
| `production_batches` | id, product_id FK, produced_at, expected_yield, actual_yield, batch_overhead_estimated, batch_overhead_actual, usage_notes; immutable once closed |
| `overhead_months` | id, month (unique), fixed_overhead_total, forecast_hours, actual_total?, actual_hours?, variance_note |
| `price_history` | id, product_id FK, previous_price?, new_price, net_price, vat_rate_at_approval, cost_snapshot (JSON), approved_by FK users, approved_at, reason |
| `benchmark_entries` | id, product_id FK?/category_id FK?, competitor_name, region, location, product_name, size_g, price, currency, source_url, checked_at |

Reused unchanged: `products` (+ costing summary fields as needed, e.g. `current_cost` cached with provenance), `vat_rates`, `order_tax_lines`, `product_vat_events`, `order_items` snapshot columns, `orders.currency`, `coupons` (percent-off already fits proportional allocation).

## 20. API and backend architecture proposal

- New router family `app/routers/costing.py` (admin): ingredients, recipes, batches, overhead months, benchmarks, price approvals — audit-mirroring the VAT endpoint pattern.
- Pure costing module `app/costing.py` (mirrors `vat.py` style: Decimal, pure functions, refusal-only): `product_cost(recipe, ingredient_costs, overhead_rates)`, `charm_candidates(cost, vat_rate)`, `margin(net, cost)`, `allocate_discount(lines, pct)`.
- `orders.py`: coupon application gains the margin gate (fail-closed 400 with the offending product named); Phase 4 adds the inclusive branch behind `tax_mode` writing snapshots + `order_tax_lines`.
- `marketing.py:storefront`: expose `tax_mode` (+ delivery-VAT mode when relevant) so the frontend labels correctly.
- `admin.py:stats` + lifetime spend: group by `Order.currency` (currently sums across currencies blindly — pre-existing defect, fix in Phase 1).
- Publication gate enforced in product serialization (storefront hides) **and** re-checked at checkout.

## 21. Frontend component proposal

- Admin: `admin/Ingredients.jsx`, `admin/RecipeEditor.jsx` (cost breakdown + margin meter), `admin/PricingReview.jsx` (recommendation + approval), `admin/PriceHistory.jsx`, `admin/Batches.jsx`, `admin/Overhead.jsx`, `admin/Benchmarks.jsx`; extend `admin/Products.jsx` rail with `publication_blockers` badges.
- Storefront: `format.js:taxLabel()` gains an inclusive variant ("incl. 7 % / 19 % VAT"); `Cart/Checkout` render per-rate VAT rows from the quote; `PriceTag` unchanged (prices are gross by data).
- No customer-facing costing UI ever (costs, margins, benchmarks are desk-only; never serialized into public schemas — same discipline as the existing VAT fields via `_AdminVatFields`).

## 22. Stripe currency and checkout requirements

- **Order-currency fix first (Phase 1, pre-EUR):** `stripe_gateway.py:_line_items` (lines 103/113/124) and the coupon creation (line 159) currently read `settings.stripe_currency`, not `order.currency`. After any cutover, re-issuing a pay link for an old pending USD order would bill USD amounts under an EUR label. Fix: derive from `order.currency` everywhere. (Audited defect — the only latent money bug found.)
- EUR minor units: `stripe_gateway.py:cents()` (× 100, integer) is currency-agnostic — €3.99 → 399.
- Inclusive mode session shape: gross line amounts; the flat "Tax (8.75 %)" row (`_tax_label`) is dropped/replaced per adviser guidance; the one-off Stripe Coupon carries the order currency.
- Webhooks/confirm/refund are currency-blind (matched by `metadata.order_id`; refunds take integer cents from `order.total − fee` — `orders.py:_refund_paid_order`). EUR refunds work unchanged.
- `STRIPE_CURRENCY=eur` is a Render env change at cutover only. **Stripe Live remains untouched by all phases here.**

## 23. Historical order compatibility

Guaranteed by the shipped design: every order stores its own `currency` and full line snapshots (`order_items.*`, incl. the seven VAT columns — NULL on all historical rows, meaning "priced exclusive"). Catalogue re-pricing, VAT classification, or cost changes never touch placed orders; receipts render in each order's own currency (`email.py`, `OrderDetail/Receipt/Orders`, `admin/Orders`). Acceptance: AC-21/AC-24 (§26) and the existing regression suite.

## 24. Security, audit and permission requirements

- All costing/pricing/benchmark endpoints admin-only (`CurrentAdmin`); last-admin invariant preserved.
- Immutable audit ledgers: `product_vat_events` (exists), `price_history` (new), closed `production_batches` — append-only; no UPDATE/DELETE paths.
- Public API surfaces never expose costs, margins, benchmarks, or review status (extend the existing `_AdminVatFields` separation discipline).
- No secrets in any spec-derived surface; ingredient invoice references are free text and must not contain credentials.
- Every price approval records the approving admin + timestamp + reason (§13).

## 25. Error handling and fail-closed conditions

| Condition | Behaviour (fail-closed) |
|---|---|
| Any basket product unclassified / VAT-unapproved (inclusive mode) | Checkout + quote refuse with the product named |
| Delivery VAT mode unset (inclusive mode) | Checkout refuses; settings page demands configuration |
| Discount would breach 40 % on any affected product | Coupon application refused, product named; no admin override |
| Ingredient effective cost missing at costing time | Costing refuses; product flagged not publishable |
| Fewer than 3 benchmark comparables | Limited-sample warning; index labelled indicative |
| Cost input past review interval (default 90 d) | "Review due" flag; confidence shown, never hidden |
| Stripe unreachable at order placement | Existing behaviour: order cancelled, 503, nothing charged (unchanged) |

## 26. Testing and acceptance criteria

Testable criteria (each maps to automated tests in `backend/tests/` + `frontend/src/lib` node tests where pure):

| ID | Criterion (with example where numeric) |
|---|---|
| AC-1 | `margin(net, cost) = (net−cost)/net`; `min_net(cost)=cost/0.6`; 2.10 → 3.50; margin ≠ markup asserted (40 % margin ≠ 40 % markup) |
| AC-2 | Labour: minutes/60×20 — 12→€4.00; 45→€15.00; 7.5→€2.50; unattended minutes contribute 0 |
| AC-3 | Purchase-unit conversion: 25 kg @ €37.50 → €1.50/kg; 500 g → €0.75 (pre-wastage) |
| AC-4 | Wastage: 1000 g usable @5 % → 1052.63 g purchased → €1.58 @ €1.50/kg; 0 % loss explicit; no global percentage exists in code |
| AC-5 | Yield: batch €26.40, expected 24 / actual 22 → unit cost €1.20 (sellable), variance −8.33 % recorded, expected record unchanged |
| AC-6 | Batch overhead €4.40 / 22 → €0.20/unit; estimate then actual replacement updates future costings only |
| AC-7 | Fixed overhead €800 / 160 h → €5.00/h; 24 min product → €2.00; month-end variance recorded, history immutable |
| AC-8 | A cost category present in both overhead ledgers raises the double-count warning (test injects overlap) |
| AC-9 | Every estimate carries source/reviewed-date/confidence/status; >90 d → review-due flag; estimates labelled, never "verified" |
| AC-10 | Charm: cost 2.10 @7 % → €3.99; @19 % → €4.49; exact-landing kept (min_gross €4.99 → €4.99); sub-cent net-rounding breach case steps up an ending (loop test) |
| AC-11 | Discount margin gate: A net 3.73/cost 2.10 + 10 % → 37.5 % → refused, product named; accepted when headroom exists (net 4.20 → 44.4 % post-discount) |
| AC-12 | Allocation: 12.65 basket / 10 % → 0.75 + 0.52 = 1.27 exactly (largest remainder); property test across random baskets |
| AC-13 | Cent reconciliation: Σ line gross − discount = total; per rate net+VAT = gross line (§14 example) |
| AC-14 | <3 comparables in a region → limited-sample warning rendered; index labelled indicative |
| AC-15 | Price approval writes previous/new/admin/timestamp/reason; unapproved recommendation never publishes |
| AC-16 | Approved change visible to the next order immediately; in-flight carts re-quote on next interaction |
| AC-17 | Inclusive checkout with any unapproved product → 4xx naming it (engine `resolve_product_rate` chain) |
| AC-18 | Mixed basket (§14): 7 % line + 19 % line → two `order_tax_lines`, exact figures 6.27/0.44 and 3.92/0.75 |
| AC-19 | Delivery VAT unset → inclusive checkout refuses; configured modes compute per config; no silent default |
| AC-20 | EUR Stripe session: line amounts × 100 (€3.99 → 399); currency = order currency (incl. re-issued old USD order keeps `usd`) |
| AC-21 | Historical USD order: after cutover, renders USD, taxes exclusive, VAT snapshot columns still NULL, untouched |
| AC-22 | EUR receipt shows € + "incl. VAT" breakdown; EUR refund = cents(total − fee) |
| AC-23 | Admin stats/lifetime spend never sum USD+EUR; per-currency figures |
| AC-24 | Publication gate: missing cost/ingredient/allergen/VAT-approval each block publication; blocker list names the missing piece |
| AC-25 | No mutation of completed orders/production batches/price history (immutability tests attempt writes) |

## 27. Migration and deployment strategy

Additive only, staged per §31; every phase deploys while the shop still runs USD/exclusive (customer-invisible until Phase 7). Data migrations are admin data-entry (ingredients, recipes, classifications, EUR prices), not SQL scripts. Cutover (Phase 7) is a single maintenance moment: Render env `TAX_MODE=inclusive` + `STRIPE_CURRENCY=eur`, after all gates green and written approvals in hand. Render auto-deploys from `main` on push; database backup before each phase's deploy (established practice).

## 28. Rollback strategy

- **Pre-cutover phases:** revert the deploy (Render roll-back); new tables/rows are inert under old code (additive schema discipline, proven in v3.14.0).
- **Cutover itself:** flip `TAX_MODE`/`STRIPE_CURRENCY` back — every order snapshots its own figures, so mixed history remains coherent; new EUR orders placed during the window stay EUR (correct — that's what customers paid).
- **Bad cost/price data:** correct forward via new approvals (§13); history never edited.

## 29. External legal and tax dependencies (not software features)

Software cannot grant authorization. Written confirmations required before the Phase 7 cutover:

1. **German tax adviser:** per-product VAT classifications (the five provisional categories in `vat.py` are placeholders, not conclusions); delivery VAT treatment (§16); rate validity.
2. **Ausländerbehörde Bremen:** written permission for self-employment under the owner's §16b residence status.
3. **Handwerkskammer Bremen:** assessment of the proposed baking activities and qualification requirements (bakery trade is a licensed craft — Anlage A context to be confirmed with the chamber).
4. **LMTVet Bremen:** food-business registration and hygiene requirements (includes the allergen-management operating controls referenced in §17).
5. **Gewerbeanmeldung + Finanzamt:** trade registration and tax registration (VAT/USt-IdNr implications adviser-confirmed).

No EUR-inclusive public cutover until 1 is complete and the operating permissions 2–5 are in hand.

## 30. Unresolved questions and assumptions

**Genuinely needing the owner's decision:**

- **Q1 — Discount headroom:** price with coupon headroom by default (min net = cost/(0.6×(1−max planned discount)), e.g. cost/0.54 for 10 % codes) or strict floor at 40 % and accept that most coupons get refused? Affects every charm recommendation.
- **Q2 — `sale_price` promotional field:** keep it (subject to the same margin gate) or retire it in favour of coupons only?
- **Q3 — Bulk-order discounts:** negotiated off-system (margin rule N/A) or quoted inside the system with the same gate?
- **Q4 — Approval granularity:** single-admin approval (current roles) or a two-step maker/checker for price changes?
- **Q5 — Review interval:** 90-day cost-review default confirmed?

**Assumptions (stated, not decided):** €20/h and 40 % are shop-wide constants (single rate/floor, not per-product); EUR cutover is one-way for *new* orders; benchmarks start empty; ingredient stock levels are not inventory-managed (costing only, `products.stock` remains the sellable counter).

## 31. Recommended implementation phases

| Phase | Content | Gate to next |
|---|---|---|
| **0** | This specification; no code | Owner approval of spec + Q1–Q5 |
| **1 — Currency & history safety** | Stripe order-currency fix (§22); per-currency admin stats/lifetime spend; USD behaviour byte-identical | Full regression green; historical USD order test |
| **2 — Costing foundation** | `costing.py` + tables §19 (ingredients, recipes, batches, overhead); provenance; Decimal purity | AC-2,3,4,5,6,7,8,9 |
| **3 — Pricing recommendations** | Margin + charm engine; benchmark panel; approval + price history; under-review rail | AC-1,10,14,15,16 |
| **4 — VAT-inclusive basket & checkout** | Basket pricer; allocation + reconciliation; delivery-VAT config; snapshots + `order_tax_lines`; margin gate on coupons; behind `tax_mode` | AC-11,12,13,17,18,19 |
| **5 — Frontend & admin workflows** | §18/§21 surfaces; publication gates; per-rate VAT display | AC-24; UI review |
| **6 — Stripe Sandbox rehearsal** | EUR sessions/coupons/refunds; failed payments; old-USD coexistence; no real money | AC-20,21,22,23 |
| **7 — Legal approval & cutover** | Only with §29 written approvals + complete catalogue; explicit human authorization; env flip; post-cutover smoke | AC-25 + adviser sign-off |

**Files likely to require future change** (implementation estimate, not authorization): `backend/app/{models.py, config.py, schemas.py, costing.py (new), vat.py, email.py}`, `backend/app/routers/{orders.py, admin.py, marketing.py, costing.py (new)}`, `backend/app/stripe_gateway.py`, `backend/tests/{test_api.py, test_costing.py (new), test_stripe_gateway.py}`, `frontend/src/lib/{format.js, costing tests}`, `frontend/src/pages/admin/{Products.jsx + new screens}`, `frontend/src/pages/{Cart.jsx, Checkout.jsx}`, `frontend/src/pages/account/{OrderDetail.jsx, OrderReceipt.jsx}`.

## Risk register

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Stripe session currency from settings, not order (live defect today, bites at cutover) | M | H | Phase 1 fix + AC-20 |
| R2 | Admin revenue sums across currencies (live defect) | H | M | Phase 1 split + AC-23 |
| R3 | Unapproved classification blocks checkout at cutover (fail-closed cliff) | H | H | Pre-cutover catalogue audit; publication gate; AC-17/24 |
| R4 | Cent-rounding margin breach in charm derivation | L | M | Verify-loop algorithm §5; AC-10 |
| R5 | Discount floor refuses most coupons (business surprise) | H | M | Q1 decision before Phase 3 |
| R6 | Overhead double-counting skews costs | M | M | Disjoint ledgers + warning + AC-8 |
| R7 | Estimates drift from reality (stale costs) | H | M | Provenance + review-due flags §12 |
| R8 | Legal permissions late (§29) | M | H | Cutover gated on written approvals; Phase 6 rehearsal independent |
| R9 | Float/Decimal mixing between costing and order columns | M | M | Decimal purity in `costing.py`/`vat.py`; reconciliation tests AC-13 |
| R10 | Benchmark data sparse → misleading index | H | L | Limited-sample warnings; benchmarks advisory only |

---

*End of Djini's Bakehouse Business & Pricing Specification v1.0. This document records decisions and designs; it authorizes nothing. Implementation, migration, and cutover each require the owner's explicit approval; VAT and legal conclusions require the external confirmations in §29.*
