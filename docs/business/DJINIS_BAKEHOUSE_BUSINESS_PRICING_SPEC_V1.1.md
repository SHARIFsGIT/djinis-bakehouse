# Djini's Bakehouse — Business & Pricing Specification v1.1

**Status:** Draft for review — not approved for implementation.
**Supersedes:** v1.0 (`DJINIS_BAKEHOUSE_BUSINESS_PRICING_SPEC_V1.md`, retained unchanged for reference).
**Baseline audited:** commit `e5ab20928bdc6cdd2ad15521e3c3d03778a4fe9a` (v3.14.0), deployed at https://djinisbakehouse.com on 2026-11-04.
**Document role:** This specification describes *what should be built and why*. **It does not authorize code changes, database migrations, configuration changes, or production deployment.** Every implementation step requires the owner's explicit approval, and every VAT/legal conclusion requires the written confirmation of the appointed German tax adviser and the authorities listed in §31.

### What changed from v1.0

The five open questions of v1.0 (Q1–Q5) are now decided and incorporated as **D14–D18**:

| New decision | Policy |
|---|---|
| **D14** Discount headroom | Strict 40 % margin **after** discounts; no automatic coupon headroom in normal prices |
| **D15** Promotional price | `sale_price` field **retained**, under the same margin validation and audit |
| **D16** Bulk orders | In-system admin **quotation workflow** (new §16) with all costing/VAT/margin safeguards |
| **D17** Price approval | **Single-admin** approval with complete audit history (no maker/checker initially) |
| **D18** Cost review | **90-day default** review interval, category-specific overrides, immediate recalculation on supplier-price change (new §13) |

Consequences applied throughout: the charm engine's minimum stays at `cost/0.60` (no headroom inflation); the discount gate is now policy rather than proposal (§15); the roadmap gains phases for promotional validation, bulk quotations and cost review (§33); acceptance criteria grow from 25 to 38 (§28); the risk register adds R11–R14 (§34). Section numbering shifted accordingly (v1.0 §13→§14, §14→§15, new §13 and §16 inserted, v1.0 §15–31 → §17–33).

---

## 1. Executive summary

Djini's Bakehouse runs a working USD, tax-exclusive storefront (FastAPI + SQLAlchemy + PostgreSQL backend, Vite SPA frontend, Stripe Sandbox card payments plus cash-on-delivery, deployed on Render). Version 3.14.0 shipped the data foundation for a EUR migration: per-order currency, per-line VAT snapshot columns, an effective-dated 7 %/19 % rate table, a Decimal VAT engine that refuses to price unapproved products, and an admin classification workflow with a full audit ledger.

What does **not** exist yet is everything this specification is really about: the shop has **no cost data at all** — no ingredient catalogue, no recipes with quantities, no wastage records, no labour split, no overhead allocation, no margin math, no price-approval workflow, no price history, no market benchmarks, no promotional-price validation, no bulk quotation workflow, and no cost-review scheduling. Today an admin can set any price and any promotional price and hand out any percentage coupon; nothing computes or protects the confirmed **40 % minimum gross margin**, and nothing prices with the confirmed **€0.49/€0.99 charm endings**.

This document specifies a costing → pricing → approval → VAT-inclusive EUR checkout pipeline in nine phases (§33), with testable acceptance criteria (§28) and numeric examples throughout. With D14–D18 confirmed, the margin floor now binds **every price mechanism** — regular prices, promotional prices, coupons, and bulk quotations — with no ordinary admin override. The plan is fail-closed wherever the rules are unforgiving: no publication without complete cost/ingredient/allergen data and an approved VAT classification; no discount or promotional price that breaches the margin floor; no quotation approval below 40 %; no inclusive checkout until classifications and delivery-VAT treatment are professionally confirmed.

**Estimated implementation shape:** ~10 new database tables, one new backend costing/pricing module family plus a quotation service, two admin UI surfaces (costing/pricing desk and quotation desk, with cost-review reminders), and a guarded cutover. No change to the existing checkout arithmetic until Phase 4, and historical USD orders remain untouched forever.

## 2. Business objectives and scope

**Objectives**

1. Move customer-facing pricing to EUR, VAT-inclusive, with legally confirmed classifications.
2. Price every product from a defensible cost base: ingredients (with wastage), labour, hybrid overhead — with recorded provenance and confidence for every estimate.
3. Enforce a **minimum 40 % gross margin on net selling price**, before *and* after discounts, across **all price mechanisms**: regular prices, promotional prices (`sale_price`), coupons, and bulk quotations (D14, D15, D16).
4. Publish only charm prices (€0.49 / €0.99 endings), rounded up, never in breach of the margin floor.
5. Keep publication gated on complete food information (ingredients, allergens) and approved VAT classification.
6. Preserve every historical order exactly as placed (USD, exclusive) — immutable.
7. Quote bulk orders inside the system, with the same costing, VAT and margin safeguards as storefront sales — never as a bypass (D16).
8. Keep every cost estimate reviewed on schedule (90-day default, category overrides) and every published price change approved by an authorized admin with a full audit trail (D17, D18).

**In scope:** ingredient catalogue and costing; recipes and yield; wastage; labour; overhead; margin and charm pricing; price approval and history (regular and promotional); market benchmarking; discount margin rules; bulk-order quotations end to end; cost-review scheduling; VAT-inclusive EUR checkout; admin workflows; Stripe EUR; reporting by currency.

**Out of scope (confirmed):** supplier integrations; automatic invoice extraction (optional free-text invoice references only); multi-currency *selling* (EUR only after cutover; USD is history); Stripe Live activation (separate future authorization); automatic repricing without human approval; maker/checker (two-person) price approval in the initial implementation (D17); inventing market data.

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
| D14 | Discount headroom: **strict** 40 % margin on the discounted net price; no automatic price increases to create coupon headroom; coupons, promotional prices and manual discounts all pass the same validation; no ordinary admin override | Confirmed (v1.1) |
| D15 | Promotional price: **retain** the existing `sale_price` field for compatibility; promotional prices must pass margin validation, cannot be published or used at checkout when invalid, and are approved/audited like any price change | Confirmed (v1.1) |
| D16 | Bulk orders: **in-system** admin quotation workflow — full costing, VAT from approved classifications, 40 % margin gate, approval, validity/expiry, customer acceptance, conversion to order, immutable snapshot | Confirmed (v1.1) |
| D17 | Price approval: **single-admin** approval with complete audit history (admin identity, previous/new price, reason, timestamp); existing role-based authorization respected; no two-person approval initially | Confirmed (v1.1) |
| D18 | Cost review: **90-day default** interval; category-specific intervals where justified; overdue reminders; supplier-price changes trigger recalculation immediately regardless of review date; recalculation never changes an approved price without approval | Confirmed (v1.1) |

All eighteen decisions (D1–D18) are confirmed. D14–D18 were confirmed on 2026-11-04 and supersede the open questions Q1–Q5 of v1.0 §30.

## 4. Pricing and gross-margin formulas

**Gross margin** (the only margin definition used in this system):

```
Margin = (Net Selling Price − Total Product Cost) / Net Selling Price
```

**Minimum net selling price** (margin floor 0.40):

```
Minimum Net Price = Total Product Cost / (1 − 0.40) = Total Product Cost / 0.60
```

**Post-discount margin** (D14 — binds every discount mechanism: coupons, promotional prices, manual discounts, quotation discounts):

```
Post-discount margin = (Discounted net selling price − Total Product Cost) / Discounted net selling price
Required:  Post-discount margin ≥ 0.40          (per affected product, individually)
```

Per D14 the pricing engine's minimum **stays at `cost / 0.60`**: normal product prices are never automatically raised to build coupon headroom. (An owner who *voluntarily* prices higher through the manual decision D13 obtains headroom naturally; the engine neither computes nor enforces it.)

**Total Product Cost** (§5–§10):

```
Total Product Cost =
    Ingredient Cost (purchase quantities incl. wastage)     §6, §7
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

**Post-discount example (the D14 gate):** the same product (net €3.73, cost €2.10) with a 10 % coupon → discounted net €3.36 → margin (3.36−2.10)/3.36 = **37.5 % < 40 % → the coupon is refused** (worked fully in §15).

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
| Cost rises so the current price fails step 4 | Product flagged under-review (§14); price change needs approval |
| VAT classification changes (7 % ↔ 19 %) | Charm re-run; new gross requires approval (§14) |
| Discount planning | Per D14 the engine prices at the strict `cost/0.60` floor — no headroom is added. Voluntary headroom is the owner's manual decision (D13), never an engine behaviour |

## 6. Ingredient and recipe costing model

**Ingredient catalogue (per ingredient, admin-entered):** name; category (drives the review interval, §13); purchase quantity; purchase price; measurement unit; **effective cost per unit** (derived: purchase price / purchase quantity, e.g. a 25 kg sack at €37.50 → €1.50/kg); optional invoice/receipt reference; provenance fields (§12: source/assumption, last-reviewed date, next-review date, confidence low/medium/high, estimated-vs-actual flag).

**Recipe (per product):** list of ingredient lines — usable quantity + unit (e.g. 500 g flour, 6 eggs); per-line preparation loss (§7); plus labour split (§9) and batch fields (§8). The existing `products.ingredients` free-text field remains the customer-facing ingredient statement; the recipe table is the costing truth. (Publication gate §19 cross-checks that the statement exists.)

**Unit conversion acceptance example:** flour purchased 25 kg @ €37.50 → €1.50/kg; recipe needs 500 g → base €0.75; with 5 % preparation loss the purchase quantity is 500/0.95 = 526.32 g → **€0.79** (1.5789 → round half-up at the final product-cost step; full precision is kept inside the sum).

**Estimates → actuals:** an ingredient starts as an estimate; recording a new purchase replaces the effective cost per unit from its effective date, and **immediately triggers recalculation of every dependent product cost** (D18, §13). Historic cost computations are never recomputed retroactively — cost *snapshots* (§12) preserve what each price decision was based on.

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
3. Acceptance test AC-8 (§28) fails the build if a category flagged in both places doesn't raise the warning.

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
| Next review due | date (computed — §13) |
| Confidence | low / medium / high |
| Status | estimated / actual |

Rules: estimates are never presented as verified accounting figures (every admin surface labels them); costs past their review-due date raise **overdue cost-review reminders** on the admin desk (§13, D18); product-cost *snapshots* freeze the full cost composition (per-ingredient figures, labour, overhead rates) behind every approved price, so any historical price decision can be reconstructed exactly.

## 13. Cost-review scheduling and recalculation policy (D18)

**Review intervals.** Default **90 days**, computed per ingredient (and per other cost inputs) as `next_reviewed = last_reviewed + interval`. Where justified, **category-specific intervals** override the default: each ingredient category (§6) carries its own `review_interval_days` (e.g. dairy 30 days because prices move weekly; dried goods 180 days), and an explicit per-ingredient override is allowed for exceptions. Example: flour reviewed 2026-01-01 on the default 90-day interval → next review **2026-04-01**; butter reviewed 2026-01-01 in a 30-day category → next review **2026-01-31**.

**Reminders.** The admin desk surfaces overdue reviews (and, secondarily, reviews due within a configurable window). Overdue inputs are flagged wherever their cost appears — never hidden — but they do not by themselves block pricing; blocking is the publication gate's job (§19).

**Immediate recalculation on supplier-price change.** Entering a new purchase price (estimate or actual) for an ingredient triggers, in the same transaction, recomputation of every dependent product cost and re-evaluation of every affected margin — **regardless of the review date**. Products whose current approved price (regular *or* promotional) now falls below 40 % are flagged onto the under-review rail (§14). The recalculation **never changes an approved selling price**; it changes flags and recommendations only. Publishing a new price always requires admin approval (D18, D17).

**Current implementation:** none — no review dates, no intervals, no recalculation trigger, no reminders. *Gap: fields + scheduler + admin surface.*

## 14. Cost-change and price-approval workflow (D10, D17)

**When an ingredient cost (or any cost input) changes:**

1. Identify affected products (recipe dependency).
2. Recompute their costs and suggested charm prices automatically (immediately on supplier-price change — §13).
3. Recompute the margin against the **currently approved** price — regular price *and* promotional price where set.
4. Flag every product below 40 % (either mechanism) on the desk's "under-review" rail.
5. Admin review and approval required before any published price changes. *No auto-repricing, ever.*
6. Historical prices and completed orders unchanged (orders are snapshots; `order_items` never recomputed).

**Approval model (D17): single-admin.** An authorized admin (existing role-based authorization via `security.py:get_current_admin` — no new role machinery) can approve price changes. Two-person maker/checker approval is explicitly **not** required in the initial implementation; the audit history below is the control. Every approval records: **previous price, new price, approving admin identity, approval timestamp, reason / change reference**.

**Scope of approval:** the same workflow covers regular price changes and **promotional price (`sale_price`) changes** (D15) — `price_history` entries carry a kind (`regular` | `promotional`) so the two streams stay distinguishable. An invalid promotional price cannot be approved (§15.2, §19).

**Effectivity:** approved changes apply immediately to new orders. In-flight carts re-quote at next interaction. Publication of a first-time price follows the same approval path.

**Current implementation:** none — price edits are instant and unaudited (`admin.py:update_product`). The VAT workflow (`admin.py:update_product_vat` + `ProductVatEvent`) is the pattern to replicate for prices.

## 15. Discount, coupon and promotional-price rules (D11, D14, D15)

### 15.1 Coupons — strict post-discount floor

- The 40 % margin is a minimum **after** discounts — per affected product, checked **individually** (a high-margin line cannot carry a breaching line).
- **Rejection:** a coupon that would push any affected product below 40 % on its discounted net is rejected at the point of application (quote and checkout both). **No ordinary admin override** may bypass this (D14).
- **No automatic headroom:** per D14, normal prices are computed at the strict `cost/0.60` floor; the engine never inflates a recommendation so a coupon will fit. If most coupons are therefore refused on tightly-priced items, that is the accepted consequence — design coupons around items with natural margin (D13 headroom) or improve costs.
- **Basket-wide discounts** are allocated proportionally to each product's pre-discount selling value — using the **effective price** (promotional price where active, §15.2) — with **largest-remainder cent reconciliation** so the allocated parts sum to the original discount exactly.
- **Delivery charges are never product revenue or cost** — margin math excludes delivery entirely (delivery VAT is §18).

**Proportional allocation — worked example.** Basket: 2 × Product A (gross €3.73 each @ 7 %) and 1 × Product B (gross €5.19 @ 19 %). Subtotal €12.65. Coupon 10 % → total discount = round(1.265, 2) = **€1.27**. Exact per-line shares: A €0.746, B €0.519. Largest-remainder allocation: floor to A €0.74, B €0.51 (sum €1.25); two cents remain; order by fractional part (B .9, A .6) → B +1 → €0.52, A +1 → €0.75. **Sum = €1.27 exactly.** ✓

**VAT after allocation (per rate, exact cents):** A: gross 7.46 − 0.75 = 6.71 → net 6.71/1.07 = 6.2710 → **€6.27**, VAT €0.44. B: 5.19 − 0.52 = 4.67 → net 4.67/1.19 = 3.9244 → **€3.92**, VAT €0.75. Reconciliation: 6.71 + 4.67 = 11.38 = 12.65 − 1.27 ✓; rate groups reassemble (6.27+0.44=6.71; 3.92+0.75=4.67) ✓.

**Margin-after-discount — rejection example (now policy, D14).** Product A net €3.73, cost €2.10 (margin 43.7 %). A 10 % coupon → allocated net €3.36 → margin (3.36−2.10)/3.36 = **37.5 % < 40 % → refused**, product named. In the mixed basket above, the rule checks each product individually, so A at 37.5 % makes even the mixed basket ineligible for the 10 % code. **Acceptance case:** the same product priced (voluntarily, via D13) at net €4.20 survives the 10 % coupon: 4.20 × 0.9 = €3.78 → margin (3.78−2.10)/3.78 = **44.4 % ≥ 40 % → coupon accepted.**

### 15.2 Promotional prices (`sale_price`) — retained and guarded (D15)

- The existing `sale_price` field is **kept as-is** (no rename, no removal) for compatibility with existing products and the frontend's struck-through-price display.
- **Validation at publish:** whenever a promotional price is set or changed, it must pass `margin(sale_net, cost) ≥ 0.40`, where `sale_net = round_half_up(sale_price / (1 + vat_rate))`. An invalid promotional price **cannot be published or used at checkout**; the admin UI shows the computed margin and the reason for refusal inline.
- **Interaction with coupons:** the coupon gate (§15.1) runs on the **effective price** — a coupon applied on top of an active promotional price is checked against the *promotional* net. Stacking must still clear 40 % per product.
- **Interaction with cost changes:** promotional prices enter the same recalculation-and-flagging as regular prices (§13) — a cost rise that pushes the *promotional* price below 40 % flags the product for review.
- **Audit:** promotional price changes are approved and recorded exactly like regular changes (§14), with `kind=promotional` in `price_history` (previous/new price, approver, timestamp, reason).
- The field **must not become an uncontrolled alternative pricing mechanism**: there is exactly one margin rule and one approval path, and `sale_price` lives inside both.

**Numeric examples (cost €2.10, 7 % VAT, regular €3.99):**

| Proposed sale (gross) | Sale net | Margin | Verdict |
|---|---|---|---|
| €3.49 | 3.49/1.07 → €3.26 | (3.26−2.10)/3.26 = **35.6 %** | **Refused** — cannot publish |
| €3.79 | 3.79/1.07 → €3.54 | (3.54−2.10)/3.54 = **40.7 %** | **Approved** — publishable, audited |

### 15.3 Bulk orders — pointer

Bulk-order discounts are no longer negotiated off-system: they are quoted, validated, approved and audited inside the system (§16), under the same 40 % gate, the same costing module and the same approved VAT classifications. Bulk quotations must not bypass normal costing, VAT or margin safeguards; a quote that cannot satisfy the 40 % minimum is rejected or recalculated before approval.

**Current implementation (§15 whole):** percent-only coupons (`Coupon.percent_off`), applied basket-wide with no margin knowledge; `sale_price` promotional field unguarded. *Gap: margin gate at quote/checkout/coupon-apply; sale_price validation and audit.*

## 16. Bulk-order quotation workflow (D16)

**Purpose:** one admin workflow that takes a customer's bulk request ("120 croissants for a office event on the 12th") from request to paid order — with real costing, real VAT and the real margin gate, and a price the customer has explicitly accepted.

**Quotation record captures:** customer and contact information (name, email, phone; customer account link when one exists); requested products and quantities, including **custom production quantities** (any batch size, not just catalogue packaging); **ingredient and packaging costs** (from the costing module §6–§8, with packaging entered as ingredient-like cost lines); **labour costs** (§9); **batch and fixed overhead allocation** (§10) at the quoted quantity; **estimated production yield** (sellable output the quote is costed on — §8); **custom proposed prices** (the owner's manual decision, D13, informed by the computed minimum); **VAT treatment based on approved classifications** (§17 — an unclassified product cannot be quoted); **40 % margin validation** (per quoted line); admin approval; **validity and expiry date** (mandatory; default length proposed at 14 days — confirm at Phase 6 configuration, §32); **status model**; **customer acceptance**; **conversion into an order**; and an **immutable snapshot of the accepted quotation**.

**Status model (statuses exactly as confirmed):** `draft` → `pending_approval` → `approved` → `sent` → `accepted` | `rejected` | `expired`. Transitions are recorded in an append-only `quote_events` ledger (actor, timestamp, from/to status, note). Acceptance is only possible while the quote is `sent`, unexpired, and matches the `approved` content (any edit after sending invalidates the sent state back to draft/pending).

**Safeguards (identical to storefront — no parallel math):**

1. Quotation costing calls the **same** costing module (`app/costing.py` proposal, §22) with the quoted quantities — never a bespoke bulk formula.
2. Approval **refuses** any quote line whose proposed net margin is below 40 % (fail-closed; recalculated or rejected before approval — D16).
3. VAT comes only from approved classifications; an unclassified or adviser-parked product cannot appear on a quote.
4. Acceptance before expiry only; an expired quote cannot be accepted (AC-32).
5. Conversion re-checks **legal gates only** (VAT approval still valid, food information complete) — it never re-prices: the agreed price is a commitment (D16, AC-34). A post-acceptance cost rise is accepted business risk, bounded by the quote validity.

**Conversion:** an accepted quote converts (explicit admin action) into a normal order through the standard order pipeline at the agreed prices: line items carry the quote's VAT snapshots into `order_items`, currency follows the quote, payment follows the customer's chosen existing method (card → standard Stripe session; cash on delivery). The order references its originating quote; the quote is locked and linked to the order.

**Numeric example (100-unit croissant quote, 7 % VAT):** ingredients incl. packaging €1.45/unit; labour 180 hands-on minutes for the batch = 3 h × €20 = €60 → €0.60/unit; batch overhead €18 → €0.18/unit; fixed overhead 3 h × €5.00 = €15 → €0.15/unit. **Total cost €2.38/unit (€238.00 the batch); minimum net = 2.38/0.60 = €3.97.** Customer asks for €3.90 gross → net €3.64 → margin (3.64−2.38)/3.64 = **34.6 % → approval refused**; counter-proposal €4.50 gross → net €4.21 → margin (4.21−2.38)/4.21 = **43.5 % → approvable.**

**Current implementation:** none. *Gap: model, service, admin UI, acceptance path, conversion.*

## 17. VAT-inclusive pricing architecture

The target architecture (Phase 4) extends the shipped v3.14.0 foundation:

- **Classification & approval:** `products.vat_category` + `vat_review_status` (approved only via `admin.py:update_product_vat`, fully audited in `product_vat_events`). Fail-closed: `vat.py:resolve_product_rate` raises for unclassified / unapproved / adviser-parked products — checkout refuses the basket, and quotations refuse the line (§16).
- **Rates:** effective-dated `vat_rates` rows (currently REDUCED 7.00 %, STANDARD 19.00 % from 2026-01-01 — *provisional values, not legal conclusions*). A rate change is a new dated row; history never moves.
- **Per-line pricing:** `vat.py:price_line_inclusive` (exists, tested, unwired) → per-line `VatLine` snapshots written to the seven `order_items` VAT columns.
- **Basket pricer (to build):** mixed-rate basket pricing with proportional coupon allocation (§15.1), delivery treatment (§18), and a total that reconciles with `vat.py:summarize_by_rate` to the cent; results persisted as `order_tax_lines` rows.
- **Snapshots immutable:** every order freezes its figures (already true for USD; same columns carry EUR/VAT data). Accepted quotations freeze theirs at acceptance (§16).
- **Discount order of operations:** allocation **before** VAT extraction (per rate), exact-cent reconciliation — as worked in §15.1.
- **Mode switch:** `config.py:tax_mode` (`"exclusive"` today, read by nothing) gates the new path; USD/exclusive remains until the Phase 8 cutover.

## 18. Delivery VAT dependency

Delivery VAT treatment is **configurable and undecided**. Options to be confirmed by the tax adviser: (a) delivery follows the goods' rate (common when delivery is part of the main supply), (b) exempt/separately treated. Until configured, **inclusive mode refuses to complete checkout** (fail-closed) rather than guessing. The configuration is a settings value (e.g. `DELIVERY_VAT_MODE = follow_goods | exempt | unset`, default `unset`) plus the amount basis (net delivery fee loaded per the confirmed treatment). No code path may default silently to any treatment.

## 19. Product publication and food-information requirements

A product is **publishable** (visible and sellable) only when **all** hold:

1. Cost information complete: recipe with all ingredient lines costed (no missing effective cost per unit), labour split entered, overhead allocated (or explicitly deferred with recorded justification).
2. Ingredient statement present (`products.ingredients`).
3. Allergen information present (`products.allergens`) — **reviewed** before publication; generic cross-contact disclaimers never substitute for appropriate allergen controls (kitchen controls are an operating procedure, not a software feature — flagged in §31).
4. VAT classification approved (`vat_review_status = approved`).
5. A price approved through §14 — and, when a promotional price is set, **a valid one** (§15.2): an invalid `sale_price` is a publication blocker until fixed or cleared.

Incomplete products remain admin-visible drafts (`is_available=false` semantics plus a computed `publication_blockers` list per product). The storefront and checkout both enforce the gate — checkout re-validates server-side (never trusting the browser). Bulk-quote-only items (custom productions not offered publicly) are exempt from *public* publication but must still pass VAT-approval and food-information checks before conversion (§16).

**Current implementation:** `is_available` is a bare boolean an admin can flip freely; no completeness checks. *Gap: computed gate.*

## 20. Admin dashboard and user workflows

New/extended desk surfaces (all behind existing `CurrentAdmin` auth — `security.py:get_current_admin`):

1. **Ingredient catalogue** — CRUD, provenance fields, categories with review intervals (§13), estimate→actual replacement history.
2. **Recipe & cost editor** (per product): ingredient lines with loss, hands-on/unattended minutes, batch fields; live cost breakdown and margin preview at the current price **and at the current promotional price**.
3. **Pricing recommendation screen** — cost, minimum net (40 %), charm candidates with verified margins, benchmark index beside them (limited-sample warnings), current approved price, Approve/Reject with reason; **promotional-price validation feedback inline** (computed sale margin, refusal reason when invalid — §15.2).
4. **Price history** — immutable log (§14), regular and promotional streams distinguishable.
5. **Production batches** — record actual yield/overhead/usage; variance view.
6. **Overhead monthly record** — forecast/actual hours + totals, variance, double-count warning.
7. **Market benchmark panel** — comparables CRUD, normalized view, weighted index.
8. **Under-review rail** — margin-flagged products after cost changes, covering regular *and* promotional breaches (extends the existing `needs_vat_review` pattern, `admin.py:all_products`).
9. **Cost-review center** — overdue and upcoming reviews by ingredient/category (D18 reminders), one-click "mark reviewed".
10. **Quotation desk** — quote list with status filters, quote editor (§16), quote detail with status history and margin breakdown, approve/send/reject actions, convert-to-order.
11. **Dashboard stats** — revenue split by currency (USD/EUR never summed together).

## 21. Data model proposal

New tables (all additive; created by the existing `create_all` + additive-ALTER mechanism in `app/__init__.py` — no destructive migrations):

| Table | Key columns |
|---|---|
| `ingredients` | id, name, category_id FK, unit, purchase_qty, purchase_price, effective_cost_per_unit (derived, stored), source, confidence, is_estimate, last_reviewed, next_reviewed (computed, stored), review_interval_days? (explicit override), invoice_ref? |
| `ingredient_categories` | id, name (unique), review_interval_days (default 90 — D18) |
| `recipe_items` | id, product_id FK, ingredient_id FK, usable_qty, unit, loss_pct, note |
| `recipes` (1:1 with product) | product_id FK unique, hands_on_minutes, unattended_minutes, expected_batch_yield, notes |
| `production_batches` | id, product_id FK, produced_at, expected_yield, actual_yield, batch_overhead_estimated, batch_overhead_actual, usage_notes; immutable once closed |
| `overhead_months` | id, month (unique), fixed_overhead_total, forecast_hours, actual_total?, actual_hours?, variance_note |
| `price_history` | id, product_id FK, kind (regular \| promotional), previous_price?, new_price, net_price, vat_rate_at_approval, cost_snapshot (JSON), approved_by FK users, approved_at, reason |
| `benchmark_entries` | id, product_id FK?/category_id FK?, competitor_name, region, location, product_name, size_g, price, currency, source_url, checked_at |
| `bulk_quotes` | id, quote_number, customer_name, contact_email, contact_phone, user_id? FK, status (draft \| pending_approval \| approved \| sent \| accepted \| rejected \| expired), currency, valid_until, subtotal, total, margin_snapshot (JSON), acceptance_snapshot? (JSON, frozen at acceptance), created_by FK, approved_by? FK, approved_at?, sent_at?, accepted_at?, converted_order_id? FK orders, notes |
| `bulk_quote_items` | id, quote_id FK, product_id? FK, name_snapshot, quantity, unit_cost_snapshot (JSON: ingredients/labour/batch overhead/fixed overhead), proposed_net, proposed_gross, vat_category_snapshot, vat_rate_snapshot, line_total |
| `quote_events` | id, quote_id FK, from_status, to_status, actor_id FK users, at, note; append-only |

Reused unchanged: `products` (+ costing summary fields as needed, e.g. `current_cost` cached with provenance; `sale_price` retained per D15), `vat_rates`, `order_tax_lines`, `product_vat_events`, `order_items` snapshot columns, `orders.currency`, `coupons` (percent-off already fits proportional allocation).

## 22. API and backend architecture proposal

- New router family `app/routers/costing.py` (admin): ingredients, categories, recipes, batches, overhead months, benchmarks, price approvals — audit-mirroring the VAT endpoint pattern.
- New router family `app/routers/quotes.py` (admin + one tokenized public path): quote CRUD; `POST /api/admin/quotes/{id}/approve|send|reject`; `GET /api/quotes/{token}` + `POST /api/quotes/{token}/accept` (unguessable single-use acceptance token — no customer account required); `POST /api/admin/quotes/{id}/convert`.
- **Cost-review endpoints:** `GET /api/admin/cost-review` (overdue/upcoming, by category), `POST /api/admin/ingredients/{id}/review` (mark reviewed); recalculation runs inside ingredient-price writes (§13).
- **Promotional validation:** `sale_price` validated inside product create/update and exposed via a dry-run `POST /api/admin/products/{id}/validate-pricing` so the UI can show margins before saving.
- Pure costing module `app/costing.py` (mirrors `vat.py` style: Decimal, pure functions, refusal-only): `product_cost(recipe, ingredient_costs, overhead_rates)`, `charm_candidates(cost, vat_rate)`, `margin(net, cost)`, `allocate_discount(lines, pct)`, `quote_cost(lines, quantities)` — **one costing path shared by storefront and quotations** (§16 safeguard 1).
- `orders.py`: coupon application gains the margin gate (fail-closed 400 with the offending product named); effective-price rule for active promotional prices; Phase 4 adds the inclusive branch behind `tax_mode` writing snapshots + `order_tax_lines`.
- `marketing.py:storefront`: expose `tax_mode` (+ delivery-VAT mode when relevant) so the frontend labels correctly.
- `admin.py:stats` + lifetime spend: group by `Order.currency` (currently sums across currencies blindly — pre-existing defect, fix in Phase 1).
- Publication gate enforced in product serialization (storefront hides) **and** re-checked at checkout.

## 23. Frontend component proposal

- Admin: `admin/Ingredients.jsx`, `admin/RecipeEditor.jsx` (cost breakdown + margin meter), `admin/PricingReview.jsx` (recommendation + approval + promotional validation feedback), `admin/PriceHistory.jsx`, `admin/Batches.jsx`, `admin/Overhead.jsx`, `admin/Benchmarks.jsx`, `admin/CostReview.jsx` (reminders + mark-reviewed); **quotation surfaces** `admin/Quotes.jsx` (list/status), `admin/QuoteEditor.jsx`, `admin/QuoteDetail.jsx` (status history, margin breakdown, convert-to-order); extend `admin/Products.jsx` rail with `publication_blockers` badges (including invalid-promotional-price).
- Storefront: `format.js:taxLabel()` gains an inclusive variant ("incl. 7 % / 19 % VAT"); `Cart/Checkout` render per-rate VAT rows from the quote; `PriceTag` unchanged (prices are gross by data; struck-through original price display continues — D15 compatibility).
- No customer-facing costing UI ever (costs, margins, benchmarks, quote internals are desk-only; never serialized into public schemas — same discipline as the existing VAT fields via `_AdminVatFields`). The public quote-acceptance page shows only agreed prices, VAT and terms.

## 24. Stripe currency and checkout requirements

- **Order-currency fix first (Phase 1, pre-EUR):** `stripe_gateway.py:_line_items` (lines 103/113/124) and the coupon creation (line 159) currently read `settings.stripe_currency`, not `order.currency`. After any cutover, re-issuing a pay link for an old pending USD order would bill USD amounts under an EUR label. Fix: derive from `order.currency` everywhere. (Audited defect — the only latent money bug found.)
- EUR minor units: `stripe_gateway.py:cents()` (× 100, integer) is currency-agnostic — €3.99 → 399.
- Inclusive mode session shape: gross line amounts; the flat "Tax (8.75 %)" row (`_tax_label`) is dropped/replaced per adviser guidance; the one-off Stripe Coupon carries the order currency.
- **Quote-converted orders (§16):** a converted quote pays through the standard session creation at the agreed line prices and the quote's currency — no new Stripe integration, no repricing between acceptance and payment.
- Webhooks/confirm/refund are currency-blind (matched by `metadata.order_id`; refunds take integer cents from `order.total − fee` — `orders.py:_refund_paid_order`). EUR refunds work unchanged.
- `STRIPE_CURRENCY=eur` is a Render env change at cutover only. **Stripe Live remains untouched by all phases here.**

## 25. Historical order compatibility

Guaranteed by the shipped design: every order stores its own `currency` and full line snapshots (`order_items.*`, incl. the seven VAT columns — NULL on all historical rows, meaning "priced exclusive"). Catalogue re-pricing, promotional-price changes, VAT classification, cost changes, or quote conversions never touch placed orders; receipts render in each order's own currency (`email.py`, `OrderDetail/Receipt/Orders`, `admin/Orders`). Accepted quotations are likewise frozen at acceptance (`bulk_quotes.acceptance_snapshot`, §16) and cannot be edited afterwards. Acceptance: AC-21/AC-24/AC-33 (§28) and the existing regression suite.

## 26. Security, audit and permission requirements

- All costing/pricing/benchmark/quotation endpoints admin-only (`CurrentAdmin`); the sole public exception is the **unguessable, single-use quote-acceptance token** (§22), which can only view and accept one quote — never edit it. Last-admin invariant preserved.
- Immutable audit ledgers: `product_vat_events` (exists), `price_history` (new, regular + promotional), `quote_events` (new), closed `production_batches`, accepted `bulk_quotes` — append-only; no UPDATE/DELETE paths.
- Single-admin approval (D17) relies on this audit trail as the control of record: every approval carries admin identity + timestamp + reason + previous/new price.
- Public API surfaces never expose costs, margins, benchmarks, quote internals, or review status (extend the existing `_AdminVatFields` separation discipline).
- No secrets in any spec-derived surface; ingredient invoice references are free text and must not contain credentials.

## 27. Error handling and fail-closed conditions

| Condition | Behaviour (fail-closed) |
|---|---|
| Any basket product unclassified / VAT-unapproved (inclusive mode) | Checkout + quote refuse with the product named |
| Delivery VAT mode unset (inclusive mode) | Checkout refuses; settings page demands configuration |
| Coupon would breach 40 % on any affected product (D14) | Coupon application refused, product named; **no admin override** |
| `sale_price` below 40 % margin at publish (D15) | Save/publication refused with computed margin and reason |
| Quote line below 40 % at approval (D16) | Approval refused; quote must be recalculated or rejected |
| Acceptance attempt on expired (or non-`sent`) quote | Refused; status remains expired/sent |
| Conversion blocked by legal gates (VAT approval lapsed, food info incomplete) | Conversion refused with the missing item named; price never changed by conversion |
| Ingredient effective cost missing at costing time | Costing refuses; product flagged not publishable |
| Fewer than 3 benchmark comparables | Limited-sample warning; index labelled indicative |
| Cost input past review date | Overdue reminder shown (§13); confidence shown, never hidden |
| Supplier-price change entered | Recalculation immediate — flags/recommendations only, **approved prices unchanged** (D18) |
| Stripe unreachable at order placement | Existing behaviour: order cancelled, 503, nothing charged (unchanged) |

## 28. Testing and acceptance criteria

Testable criteria (each maps to automated tests in `backend/tests/` + `frontend/src/lib` node tests where pure). AC-1…AC-25 are unchanged from v1.0; **AC-26…AC-38 are new in v1.1** and cover the eighteen mandated v1.1 criteria as mapped below the table.

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
| AC-9 | Every estimate carries source/reviewed-date/confidence/status; overdue → review reminder; estimates labelled, never "verified" |
| AC-10 | Charm: cost 2.10 @7 % → €3.99; @19 % → €4.49; exact-landing kept (min_gross €4.99 → €4.99); sub-cent net-rounding breach case steps up an ending (loop test) |
| AC-11 | **Coupon margin gate (D14):** refusal case — A net 3.73/cost 2.10 + 10 % → 3.36 → 37.5 % → refused, product named; acceptance case — net 4.20 + 10 % → 3.78 → 44.4 % → accepted; every affected product must individually clear 40 % |
| AC-12 | Allocation: 12.65 basket / 10 % → 0.75 + 0.52 = 1.27 exactly (largest remainder); property test across random baskets |
| AC-13 | Cent reconciliation: Σ line gross − discount = total; per rate net+VAT = gross line (§15.1 example) |
| AC-14 | <3 comparables in a region → limited-sample warning rendered; index labelled indicative |
| AC-15 | Price approval writes previous/new/admin/timestamp/reason; unapproved recommendation never publishes |
| AC-16 | Approved change visible to the next order immediately; in-flight carts re-quote on next interaction |
| AC-17 | Inclusive checkout with any unapproved product → 4xx naming it (engine `resolve_product_rate` chain) |
| AC-18 | Mixed basket (§15.1): 7 % line + 19 % line → two `order_tax_lines`, exact figures 6.27/0.44 and 3.92/0.75 |
| AC-19 | Delivery VAT unset → inclusive checkout refuses; configured modes compute per config; no silent default |
| AC-20 | EUR Stripe session: line amounts × 100 (€3.99 → 399); currency = order currency (incl. re-issued old USD order keeps `usd`) |
| AC-21 | Historical USD order: after cutover, renders USD, taxes exclusive, VAT snapshot columns still NULL, untouched |
| AC-22 | EUR receipt shows € + "incl. VAT" breakdown; EUR refund = cents(total − fee) |
| AC-23 | Admin stats/lifetime spend never sum USD+EUR; per-currency figures |
| AC-24 | Publication gate: missing cost/ingredient/allergen/VAT-approval each block publication; blocker list names the missing piece |
| AC-25 | No mutation of completed orders/production batches/price history (immutability tests attempt writes) |
| AC-26 | `sale_price` compatibility: existing products with `sale_price` load, serialize and render unchanged (field not removed/renamed; frontend struck-through display intact) |
| AC-27 | Invalid promotional price blocked: cost €2.10, regular €3.99, proposed sale €3.49 → net €3.26 → 35.6 % → save/publication refused with reason; not used at checkout |
| AC-28 | Valid promotional price passes: sale €3.79 → net €3.54 → 40.7 % → publishable; `price_history` entry `kind=promotional` with approver + reason |
| AC-29 | Authorization (D17): approval endpoints require an authorized admin (non-admin → 403); single-admin approval is sufficient (no maker/checker enforced); existing roles respected |
| AC-30 | Bulk-quote costing (D16): 100-unit quote — ingredients+packaging €1.45/u + labour €0.60/u + batch overhead €0.18/u + fixed €0.15/u = **€2.38/u, €238.00 total**; same costing module as storefront (no parallel formula) |
| AC-31 | Bulk-quote margin gate: proposed €3.90 gross @7 % → net €3.64 → 34.6 % → **approval refused**; €4.50 → net €4.21 → 43.5 % → approvable; min net displayed = 2.38/0.60 = €3.97 |
| AC-32 | Expired quotes cannot be accepted: acceptance at valid_until+1 day → refused, status stays expired; only `sent` quotes are acceptable |
| AC-33 | Accepted quotes freeze an immutable snapshot (lines, cost breakdown, VAT, terms); write-after-acceptance attempts fail/no-op; `quote_events` ledger records the transition |
| AC-34 | Conversion: accepted quote → order at **identical** agreed prices (line prices and totals byte-equal); `order_items` VAT snapshots populated from the quote; legal gates re-checked (VAT approval, food info) — price never altered by conversion |
| AC-35 | Review reminders (D18): ingredient reviewed 2026-01-01, default interval → next review **2026-04-01**; overdue items appear in the cost-review center |
| AC-36 | Category intervals: a 30-day category (e.g. dairy) overrides the 90-day default — reviewed 2026-01-01 → next **2026-01-31**; explicit per-ingredient override wins over both |
| AC-37 | Immediate recalculation: entering a new flour price recomputes every dependent product cost in the same transaction, regardless of review date; under-review rail updates for products whose regular **or** promotional price now breaches |
| AC-38 | Recalculation ≠ repricing (D18): after AC-37, all approved selling prices (regular and promotional) unchanged in DB and storefront; only flags/recommendations change until explicit approval |

**Mapping of the eighteen v1.1-mandated criteria:** 1→AC-11 (refusal case) · 2→AC-11 (acceptance case) · 3→AC-26 · 4→AC-27 · 5→AC-28 · 6→AC-29 · 7→AC-15 · 8→AC-16 · 9→AC-21/AC-25 · 10→AC-30 · 11→AC-31 · 12→AC-32 · 13→AC-33 · 14→AC-34 · 15→AC-35 · 16→AC-36 · 17→AC-37 · 18→AC-38.

## 29. Migration and deployment strategy

Additive only, staged per §33; every phase deploys while the shop still runs USD/exclusive (customer-invisible until Phase 8). Data migrations are admin data-entry (ingredients, recipes, classifications, EUR prices), not SQL scripts. Cutover (Phase 8) is a single maintenance moment: Render env `TAX_MODE=inclusive` + `STRIPE_CURRENCY=eur`, after all gates green and written approvals in hand. Render auto-deploys from `main` on push; database backup before each phase's deploy (established practice).

## 30. Rollback strategy

- **Pre-cutover phases:** revert the deploy (Render roll-back); new tables/rows are inert under old code (additive schema discipline, proven in v3.14.0).
- **Cutover itself:** flip `TAX_MODE`/`STRIPE_CURRENCY` back — every order snapshots its own figures, so mixed history remains coherent; new EUR orders placed during the window stay EUR (correct — that's what customers paid).
- **Accepted quotations:** never rolled back or edited; a wrongly issued quote is `rejected` before acceptance or honoured/fulfilled as a business decision — the ledger records either.
- **Bad cost/price data:** correct forward via new approvals (§14); history never edited.

## 31. External legal and tax dependencies (not software features)

Software cannot grant authorization. Written confirmations required before the Phase 8 cutover:

1. **German tax adviser:** per-product VAT classifications (the five provisional categories in `vat.py` are placeholders, not conclusions); delivery VAT treatment (§18); rate validity.
2. **Ausländerbehörde Bremen:** written permission for self-employment under the owner's §16b residence status.
3. **Handwerkskammer Bremen:** assessment of the proposed baking activities and qualification requirements (bakery trade is a licensed craft — Anlage A context to be confirmed with the chamber).
4. **LMTVet Bremen:** food-business registration and hygiene requirements (includes the allergen-management operating controls referenced in §19).
5. **Gewerbeanmeldung + Finanzamt:** trade registration and tax registration (VAT/USt-IdNr implications adviser-confirmed).

No EUR-inclusive public cutover until 1 is complete and the operating permissions 2–5 are in hand.

## 32. Unresolved questions and assumptions

**Genuinely unresolved (no invented answers):**

- Product-specific **VAT classifications** — adviser confirmation pending (§31.1).
- **Delivery VAT treatment** — adviser confirmation pending (§18); inclusive checkout fails closed until set.
- **External legal approvals** — §16b permission, HWK assessment, LMTVet registration, Gewerbe/tax registration (§31.2–5).
- **Initial ingredient and packaging cost data** — the owner must enter real figures; the tables start empty.
- **Initial recipes and expected yields** — owner data-entry; nothing to derive them from.
- **Actual monthly fixed-overhead figures** — first real month of data; forecasts until then (§10B).
- **Competitor benchmark evidence** — comparables must be collected from real sources; benchmarks start empty (§11).
- **Default quote validity period** — proposed 14 days (§16); confirm at Phase 6 configuration.

**Assumptions (stated, not decided):** €20/h and 40 % are shop-wide constants (single rate/floor, not per-product); EUR cutover is one-way for *new* orders; benchmarks start empty; ingredient stock levels are not inventory-managed (costing only, `products.stock` remains the sellable counter); the existing role model (single-admin approval, D17) is sufficient for the initial implementation.

## 33. Recommended implementation phases

Phase 1 remains the first code phase with unchanged priority (currency safety before anything else); VAT, historical-order and legal-approval gates from v1.0 are all preserved. Phases new or extended in v1.1 are marked.

| Phase | Content | Gate to next |
|---|---|---|
| **0** | This specification (v1.1; decisions D1–D18) | Owner approval of the spec |
| **1 — Currency & history safety** *(unchanged)* | Stripe order-currency fix (§24); per-currency admin stats/lifetime spend; USD behaviour byte-identical | Full regression green; historical USD order test |
| **2 — Costing foundation + cost review** *(extended: D18)* | `costing.py` + tables §21 (ingredients, categories, recipes, batches, overhead); provenance; **review scheduling: last/next review dates, 90-day default, category intervals, immediate recalc-on-price-change (backend)** | AC-2,3,4,5,6,7,8,9,36,37,38 |
| **3 — Pricing recommendations, promotional validation & approval** *(extended: D14, D15, D17)* | Margin + charm engine; benchmark panel; price approval + history (regular + promotional kinds); **sale_price margin validation**; under-review rail covering both price mechanisms | AC-1,10,14,15,16,26,27,28,29 |
| **4 — VAT-inclusive basket & checkout** *(unchanged)* | Basket pricer; allocation + reconciliation; delivery-VAT config; snapshots + `order_tax_lines`; margin gate on coupons (effective price); behind `tax_mode` | AC-11,12,13,17,18,19 |
| **5 — Frontend & admin workflows** *(extended)* | §20/§23 surfaces; publication gates; per-rate VAT display; **cost-review reminder UI; promotional validation feedback** | AC-24, 35 (reminder surfacing); UI review |
| **6 — Bulk-order quotations** *(new: D16)* | `bulk_quotes`/`bulk_quote_items`/`quote_events` tables; quotation service reusing `costing.py`; editor + approval + send + tokenized acceptance + expiry + conversion; Sandbox only | AC-30,31,32,33,34 |
| **7 — Stripe Sandbox rehearsal** *(was Phase 6)* | EUR sessions/coupons/refunds; failed payments; old-USD coexistence; **quote-converted orders end to end**; no real money | AC-20,21,22,23 |
| **8 — Legal approval & cutover** *(was Phase 7; gates unchanged)* | Only with §31 written approvals + complete catalogue; explicit human authorization; env flip; post-cutover smoke | AC-25 + adviser sign-off |

**Per-phase requirements for the v1.1 additions:**

- *Phase 2 (cost review):* DB — `ingredient_categories.review_interval_days`, `ingredients.last_reviewed/next_reviewed/review_interval_days?`; backend — recalc hook inside ingredient-price writes, `GET /api/admin/cost-review`; frontend — none yet (Phase 5); tests — AC-36/37/38.
- *Phase 3 (promotional validation):* DB — `price_history.kind`; backend — validation inside product create/update + dry-run endpoint; frontend — inline margin feedback (Phase 5 polish); tests — AC-26/27/28/29.
- *Phase 6 (bulk quotations):* DB — `bulk_quotes`, `bulk_quote_items`, `quote_events`; backend — `routers/quotes.py`, `costing.quote_cost`, tokenized acceptance, conversion into the order pipeline; frontend — `admin/Quotes.jsx`, `QuoteEditor.jsx`, `QuoteDetail.jsx`, public acceptance page; tests — AC-30…34.

**Files likely to require future change** (implementation estimate, not authorization): `backend/app/{models.py, config.py, schemas.py, costing.py (new), vat.py, email.py}`, `backend/app/routers/{orders.py, admin.py, marketing.py, costing.py (new), quotes.py (new)}`, `backend/app/stripe_gateway.py`, `backend/tests/{test_api.py, test_costing.py (new), test_quotes.py (new), test_stripe_gateway.py}`, `frontend/src/lib/{format.js, costing tests}`, `frontend/src/pages/admin/{Products.jsx + new screens incl. Quotes}`, `frontend/src/pages/{Cart.jsx, Checkout.jsx}`, `frontend/src/pages/account/{OrderDetail.jsx, OrderReceipt.jsx}`.

## 34. Risk register

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Stripe session currency from settings, not order (live defect today, bites at cutover) | M | H | Phase 1 fix + AC-20 |
| R2 | Admin revenue sums across currencies (live defect) | H | M | Phase 1 split + AC-23 |
| R3 | Unapproved classification blocks checkout at cutover (fail-closed cliff) | H | H | Pre-cutover catalogue audit; publication gate; AC-17/24 |
| R4 | Cent-rounding margin breach in charm derivation | L | M | Verify-loop algorithm §5; AC-10 |
| R5 | Strict floor (D14) refuses most coupons on tightly-priced items | H | M | **Accepted policy consequence (D14)** — no engine headroom; owner may price with voluntary headroom via D13; coupon design targets naturally high-margin items |
| R6 | Overhead double-counting skews costs | M | M | Disjoint ledgers + warning + AC-8 |
| R7 | Estimates drift from reality (stale costs) | H | M | Provenance + 90-day/category review cycle + immediate recalc on supplier change (§13, D18) |
| R8 | Legal permissions late (§31) | M | H | Cutover gated on written approvals; rehearsal phases independent |
| R9 | Float/Decimal mixing between costing and order columns | M | M | Decimal purity in `costing.py`/`vat.py`; reconciliation tests AC-13 |
| R10 | Benchmark data sparse → misleading index | H | L | Limited-sample warnings; benchmarks advisory only |
| R11 | Quotation workflow scope creep / parallel pricing math | M | M | Fixed v1 field set; single shared costing module (§16 safeguard 1); AC-30 |
| R12 | Stale promotional price silently breaching margin after cost rises | M | M | `sale_price` inside the same recalculation flagging as regular prices (§13, §15.2); AC-37 |
| R13 | Quote accepted, then costs rise before conversion → agreed price below floor | M | M | Mandatory short validity (proposed 14 d); conversion never reprices (commitment, AC-34); accepted business risk bounded by expiry |
| R14 | Single-admin approval: one compromised/errant account can change prices | L | M | Complete immutable audit trail (D17 control of record); role auth; maker/checker noted as a future option, not initial scope |

---

*End of Djini's Bakehouse Business & Pricing Specification v1.1. This document records decisions and designs; it authorizes nothing. Implementation, migration, and cutover each require the owner's explicit approval; VAT and legal conclusions require the external confirmations in §31.*
