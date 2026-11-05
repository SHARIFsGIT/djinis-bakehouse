# Djini's Bakehouse — Phase 0.1: Specification Correction Report

**Type:** Documentation-only correction (Phase 0.1)
**Date:** 2026-11-05
**Input:** `DJINIS_BAKEHOUSE_BUSINESS_PRICING_SPEC_V1.1.md` (source, unchanged) · `PHASE0_REAL_MARKET_PRICING_AUDIT.md` (findings) · no prior Phase 0.1 correction notes existed
**Output:** `DJINIS_BAKEHOUSE_BUSINESS_PRICING_SPEC_V1.2.md` (new file; v1.1 retained unchanged)
**Scope honored:** no application code, database, migration, environment variable, Stripe setting or production configuration was modified; nothing committed, pushed or deployed; no prices, products, VAT or payment behaviour changed.

---

## 1. Findings addressed

| Finding | Sections changed in v1.2 | What changed |
|---|---|---|
| **P0-F1** (audit §4): `benchmark_entries` lacks evidence provenance fields | §11 · §20 item 7 · §21 table · AC-14 · header changelog C1 | The four fields — `evidence_confidence` (high/medium/low, with the audit's tier definitions), `comparability_limitations` (free text), `price_basis`, `source_type` — added to the comparables methodology, the proposed table schema, the benchmark-panel surface and the acceptance criterion. Entry without them is inadmissible evidence; the Phase 0 CSV/JSON dataset already carries this shape. |
| **40 % margin / tax mode ambiguity** | §4 · §15.1 · changelog C2 | New "Tax-mode independence" paragraph in §4 and a clause in §15.1's first rule: the floor is defined on the **net** selling price, applies to **every affected product after discounts, individually**, and binds identically under USD-exclusive today and EUR-inclusive after cutover — VAT never enters the margin. |
| **Wastage intermediate miscalculation** | §6 · AC-4 · changelog C3 | §6's 500 g example now shows the correct intermediate **0.7895 → €0.79** (v1.1 wrongly carried 1.5789 there — the 1000 g figure). AC-4's 1000 g case now explicitly shows **1.5789 → €1.58**, with a cross-reference between the two so the scaling is unmistakable. |
| **Gross/net examples conflated** | §4 · changelog C3 | The single base-case table is split into a **net-price example** (margin arithmetic only, no VAT) and a **gross-price example** (charm + VAT presentation, margin still judged on derived net); the 19 % variant follows the same split. |
| **Overdue flags vs. admin reminders conflated** | §12 · §13 · changelog C3 | §12 now distinguishes the **backend derived overdue flag** (`now ≥ next_reviewed`, computed at read, deterministic, testable, no scheduler) from the **admin reminder surfaces** that render it; §13's reminder paragraph states the desk renders the flag and never independently decides overdueness. |
| **Charm increment range wrong** | §5 step 5 · changelog C3 | "≈ €0.47 net" corrected to the VAT-dependent range: €0.50 gross = **€0.47 net at 7 %, €0.42 net at 19 % — always ≥ €0.42**. |
| **Non-charm example unlabelled** | §15.1 acceptance case · changelog C3 | Labelled **arithmetic-only**: net €4.20 is a chosen net for demonstrating the gate, not a charm-derived published price (D4 requires .49/.99 gross endings on storefront prices). |
| **Quotation expiry not finalized** | §16 · §21 · §27 · AC-32 · §32 · §34 R13 · changelog C4 | Finalized: validity **14 calendar days**, starting at the **`sent` event** (`expires_at = sent_at + 14 calendar days`, stored at send; never-sent never expires). Acceptance rejected when **`now >= expires_at`** (server-side, inside the acceptance endpoint). `expired` is **derived at read/acceptance**; a scheduled expiry job is optional presentation, never required for enforcement. `bulk_quotes.valid_until` renamed `expires_at` with the semantics attached. Removed from §32's unresolved list (noted as resolved); R13 and the §27 fail-closed row aligned. |

## 2. Unresolved issues

**None from the correction list** — all eight required corrections are applied. Previously-open items that remain open (unchanged from v1.1 §32, correctly so): VAT classifications and delivery-VAT treatment (adviser); §16b/HWK/LMTVet/Gewerbe permissions (§31); initial cost/recipe/overhead data entry (W-1…W-3); benchmark evidence collection (W-5 in-store workstream). The audit's Phase 0 workstream row "Confirm 14-day quote validity — owner — Phase 6 configuration" is now **resolved by C4** (the audit file itself is a dated record and was not edited).

## 3. D1–D18 preservation

**Confirmed preserved verbatim.** §3's decision table is byte-identical between v1.1 and v1.2 (the v1.1→v1.2 diff contains no hunk in §3); every D-reference elsewhere (D4 charm, D11/D14 discount floor, D13 manual pricing, D16–D18 workflows) keeps its original meaning. The clarifications in C2/C3 are wording and mechanics only — they interpret and finalize existing decisions, they do not alter any decision. The legal/compliance gates (§31), the fail-closed conditions (§27), the phase plan (§33), the risk register (§34, R13 wording only), and the architectural constraints (additive-only schema, immutable USD history, single shared costing path, single-admin approval) are unchanged.

## 4. Consistency check against the Phase 0 audit

| Audit reference | v1.2 state | Consistent |
|---|---|---|
| §4 P0-F1 (benchmark provenance schema gap) | C1 closes it; §11/§21/AC-14 now require the four fields; dataset shape matches | ✓ |
| §5 W-1…W-4 blockers | Unchanged in v1.2 (§32 open items); no invented data, no derived prices | ✓ |
| §6 evidence hierarchy (12 High / 9 Medium / 7 Low) | Tier definitions carried into §11's `evidence_confidence` values verbatim | ✓ |
| §7 workstream "Confirm 14-day quote validity" | Resolved by C4 in the spec (audit file left as the dated record it is) | ✓ |
| §9 migration discipline (additive DDL, both engines, staged PostgreSQL) | §21/§29/§33 untouched — constraints preserved | ✓ |
| §10 "no price recommendations" | v1.2 derives none; all numeric examples are v1.1's illustrations, corrected arithmetically only | ✓ |

Cross-checks performed: the v1.1→v1.2 diff touches only the sections listed in §1 above (27 hunks, all inside those sections); §3 (decisions) and §31 (legal gates) have zero hunks; no stray artifacts remain in the file.

## 5. Readiness

**v1.2 is ready for owner approval** as the Phase 0 gate re-opens on the corrected text: every Phase 0 QA finding that targeted the specification is incorporated, no approved decision moved, and the remaining open items are the genuine external dependencies (adviser, authorities, owner data entry) that no specification can resolve. Approval of v1.2 would authorize nothing by itself — implementation phases remain individually gated per §33 and the document's standing role statement.
