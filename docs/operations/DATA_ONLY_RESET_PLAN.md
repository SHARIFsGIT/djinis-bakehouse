# Production Data-Only Reset — Plan (DRAFT, NOTHING EXECUTED)

Status: **DRAFT for owner approval — no TRUNCATE, DELETE, DROP, or sequence
change in this document has been run against production.** Prepared
2026-11-06, revised the same day after the owner confirmed the clear/preserve
decisions recorded in §5. Scope: **remove application data; keep the
database, schema, indexes, constraints, sequences-as-objects, extensions,
and all catalog/VAT/GDPR configuration.** No Render environment variable,
Stripe setting, or schema object is touched.

Companion documents: `PRODUCTION_RESET_PROCEDURE.md` (the narrower
customer/order reset of 2026-11-05, which *kept* the admin account — this
plan is broader: every account goes and boot re-creates the initial admin).

---

## 0. How to read this plan

- The authoritative schema source is the code at HEAD (`1e8f895`): the app
  runs `Base.metadata.create_all()` + additive upgrades at every boot, and
  the production database was built by exactly this code. The live database
  is nevertheless **inspected first** (§7) — if the live tables differ from
  the inventory below, the plan is revised before approval, never forced.
- The reset is **one transaction** of `DELETE`s + sequence restarts. No DDL,
  no TRUNCATE. A failure at any point rolls back to the exact pre-reset
  state (proven in §12).

## 1. Complete table inventory (35 tables, from `app/models.py` at HEAD)

All 35 application tables live in the `public` schema. 32 have integer `id`
primary keys backed by sequences (`<table>_id_seq`, SERIAL style). The three
exceptions — `loyalty_vouchers` (PK `tier`), `vat_rates` (PK `code`+
`valid_from`), `labour_rates` (PK `valid_from`) — have no sequence.
(`recipe_items` carries a unique `(recipe_id, ingredient_id)` constraint
*in addition to* its id PK, so it does have a sequence.)

**Migration history:** there are **no in-database migration tables**. The app
does not use Alembic; schema evolution is boot-time additive code
(`_ADDITIVE_COLUMNS` / `_upgrade_schema` in `app/__init__.py`). Nothing to
preserve in the database itself — and nothing this reset could damage.

**Extensions:** the application requires **none** (verified by grep across
backend code and `render.yaml`). The inspection in §7 confirms what the live
database actually has installed; whatever is there stays untouched.

**System tables preserved by definition:** `pg_catalog.*`,
`information_schema.*`, all `pg_*` objects. This plan touches none.

## 2. Tables to CLEAR (28 — all rows, owner-confirmed 2026-11-06)

| Group | Tables |
|-------|--------|
| Order book | `order_items`, `order_events`, `order_tax_lines`, `orders` |
| Customer activity | `reviews`, `favorites`, `restock_alerts`, `addresses` |
| Marketing | `campaign_deliveries`, `marketing_campaigns`, `classification_events`, `admin_notifications`, `subscribers` |
| Bulk | `bulk_replies`, `bulk_enquiries` |
| Costing book (all nine) | `ingredient_price_events`, `recipe_items`, `recipes`, `production_batches`, `product_cost_records`, `ingredients`, `ingredient_categories`, `overhead_months`, `labour_rates` |
| Promotions | `loyalty_vouchers` (tier → coupon links), `coupons` (shop-wide codes **and** personal vouchers) |
| Shop content | `testimonials` |
| Accounts | `users` — **every account, admin included**; the initial admin is re-created securely at next boot (§8) |

Deleting `users` last lets the schema's own FK rules settle the kept audit
ledger: `product_vat_events.admin_user_id` is `ON DELETE SET NULL` (the
name snapshot stays), and personal vouchers are already gone with `coupons`
(see §4).

## 3. Tables to PRESERVE (7 — untouched, verified by after-counts)

| Table | What it holds |
|-------|---------------|
| `categories` | catalog shelves |
| `products` | the 25 products: prices, sale prices, stock, uploaded images, **VAT-classification columns** |
| `vat_rates` | the EUR VAT rate table |
| `product_vat_events` | the owner's classification **audit history** (its `admin_user_id` is SET NULL when accounts go; the name snapshot stays) |
| `store_settings` | contact & hours row, storefront copy |
| `suppression_list` | **GDPR — always preserved**: email-keyed (no user FK), outlives accounts by design; a reset must never re-permission someone who said no |
| `coming_soon_items` | coming-soon band (not part of the cleared costing/testimonial data) |

Environment-resident configuration is untouched by a data reset **by
definition** (requirement: no Render changes): EUR currency + tax 7% +
shipping 2.99 / free 25 live in Render env vars (`STRIPE_CURRENCY`,
`TAX_RATE`, `SHIPPING_FEE`, `FREE_SHIPPING_THRESHOLD`), Stripe keys/webhook
secrets in Render env + Stripe dashboard. No SQL in this plan references
them. Schema, constraints, indexes, sequences-as-objects, and database
configuration are untouched (no DDL anywhere in the plan).

## 4. Foreign-key dependency analysis

`DELETE` runs **children before parents**, inside one transaction. Two of
the costing rules are `ON DELETE RESTRICT`, which makes the order
**mandatory, not advisory**: `ingredients → ingredient_categories` and
`recipe_items → ingredients`. The full order:

```
order_items, order_events, order_tax_lines      (children of orders)
orders                                          (references users with NO ondelete — must empty before accounts)
reviews, favorites, restock_alerts, addresses   (customer activity)
campaign_deliveries                             (child of marketing_campaigns)
marketing_campaigns, classification_events, admin_notifications, subscribers
bulk_replies                                    (child of bulk_enquiries)
bulk_enquiries
ingredient_price_events                         (child of ingredients)
recipe_items                                    (child of recipes AND ingredients — before both)
recipes                                         (references products, which stay)
production_batches, product_cost_records        (reference products, which stay)
ingredients                                     (after its price events and recipe lines)
ingredient_categories                           (RESTRICT — after ingredients)
overhead_months, labour_rates                   (standalone)
loyalty_vouchers                                (references coupons — before them)
coupons                                         (both shop-wide and personal)
testimonials
users                                           LAST — FK rules finish the job
```

Why `DELETE`, not `TRUNCATE`: PostgreSQL refuses `TRUNCATE users` outright
because a **kept** table — `product_vat_events`, the classification audit
history — holds an FK constraint to it; `TRUNCATE … CASCADE` would empty
that ledger too, violating the preservation requirement. `DELETE` follows
the schema's own `SET NULL`/`CASCADE`/`RESTRICT` rules exactly. Atomicity
is identical (one transaction; any statement failing rolls back everything
— proven in §12).

Kept→kept references: `products → categories` (both kept), `recipes →
products` etc. are cleared-table references into kept tables (safe). The
only kept table referencing a cleared table is `product_vat_events → users`
via `ON DELETE SET NULL` — exactly the case the schema designed for.

## 5. Owner decisions on record (2026-11-06)

- Coupons: **CLEAR** (shop-wide and personal).
- Loyalty vouchers: **CLEAR** (tier → coupon links; re-linked from the
  Promotions desk whenever wanted).
- Bulk enquiries + replies: **CLEAR**.
- Testimonials: **CLEAR**.
- Costing: **CLEAR** — all nine tables (ingredients, recipes, recipe items,
  price events, batches, overheads, labour rates, cost records, ingredient
  categories).
- Preserved: products/categories/prices/images, product VAT classifications
  + `vat_rates` + `product_vat_events`, `store_settings`, EUR/shipping
  (env), GDPR `suppression_list`, `coming_soon_items`.

The 2026-11-05 no-backup waiver covered customer/order data; this wider
scope (coupons, costing book, testimonials, bulk leads) is covered by the
owner's explicit confirmation of these decisions. Deletion is irreversible
without a backup.

## 6. Coupon reseeding safeguard (read before executing)

`_restore_promised_content()` (`app/seed.py:357-377`) runs on **every boot**
whenever products exist, and re-adds any **missing** coupon from the
shipped `COUPONS` constant — there is no environment gate. Consequence:
**the restart that re-creates the admin will also re-create WELCOME10 and
FRESHBAKE15**, both active and both with **no spend minimum**: the shipped
`COUPONS` constant deliberately carries no FRESHBAKE15 threshold (its old
"$40" was a USD figure — `seed.py:155` — so the €40-minimum decision stays
open for the owner on the Promotions desk, exactly as today). An existing
row is never re-activated or re-thresholded, only absent codes are added.

- **This is the documented, designed behavior** — the storefront's public
  copy promises both codes; `memory`/docs record "pause (never delete)" as
  the retirement path precisely because of it.
- **Safe approach, no code change (recommended):** after the reset boot,
  if you do not want the two codes live, **pause them from the Promotions
  desk** (two clicks). A paused row persists — the seeder does not
  reactivate existing coupons — so the choice survives every future boot.
- **Alternative (a code change, NOT part of this task, not deployed):** an
  environment gate such as `SEED_PROMISED_COUPONS=false` around the coupon
  loop in `_restore_promised_content()`, defaulted to today's behavior.
  Implement and deploy only as a separately approved change; this plan
  neither requires nor includes it.
- Testimonials and coming-soon items are **not** re-seeded in production
  (that path is gated by `SEED_DEMO_DATA`, false in production — verified
  in §11). The costing book is never boot-seeded.

## 7. Step 1 — read-only inspection (run FIRST, from the owner's laptop)

Confirms the live database matches this inventory before approval. Uses the
backend venv's PostgreSQL driver; prints identity (password masked), drift,
row counts, FK graph, sequences, extensions, roles.

```powershell
$env:DATABASE_URL = "<external database URL from Render — treat as a secret>"
.\venv\Scripts\python.exe ..\Temp\inspect_prod_db.py
```

(The script lives, gitignored, at `Temp/inspect_prod_db.py`; its CLEAR set
matches §2. Any drift line or unexpected FK attention mark = revise the
plan, never force it. The only expected attention mark:
`product_vat_events.admin_user_id -> users`, safe by `ON DELETE SET NULL`.)

## 8. Exact proposed SQL (NOT YET RUN against production — one transaction)

```sql
BEGIN;

-- Order book, children first
DELETE FROM order_items;
DELETE FROM order_events;
DELETE FROM order_tax_lines;
DELETE FROM orders;

-- Customer activity
DELETE FROM reviews;
DELETE FROM favorites;
DELETE FROM restock_alerts;
DELETE FROM addresses;

-- Marketing data
DELETE FROM campaign_deliveries;
DELETE FROM marketing_campaigns;
DELETE FROM classification_events;
DELETE FROM admin_notifications;
DELETE FROM subscribers;

-- Bulk enquiries, replies first
DELETE FROM bulk_replies;
DELETE FROM bulk_enquiries;

-- Costing book, children first (two RESTRICT rules make this order mandatory)
DELETE FROM ingredient_price_events;
DELETE FROM recipe_items;
DELETE FROM recipes;
DELETE FROM production_batches;
DELETE FROM product_cost_records;
DELETE FROM ingredients;
DELETE FROM ingredient_categories;
DELETE FROM overhead_months;
DELETE FROM labour_rates;

-- Promotions: tier links before the codes they point at
DELETE FROM loyalty_vouchers;
DELETE FROM coupons;

-- Shop content
DELETE FROM testimonials;

-- Accounts last (FK rules settle the kept audit ledgers' admin ids)
DELETE FROM users;

-- Sequence restarts: one per cleared table that has one
-- (loyalty_vouchers and labour_rates have none — string/date PKs)
ALTER SEQUENCE order_items_id_seq          RESTART WITH 1;
ALTER SEQUENCE order_events_id_seq         RESTART WITH 1;
ALTER SEQUENCE order_tax_lines_id_seq      RESTART WITH 1;
ALTER SEQUENCE orders_id_seq               RESTART WITH 1;
ALTER SEQUENCE reviews_id_seq              RESTART WITH 1;
ALTER SEQUENCE favorites_id_seq            RESTART WITH 1;
ALTER SEQUENCE restock_alerts_id_seq       RESTART WITH 1;
ALTER SEQUENCE addresses_id_seq            RESTART WITH 1;
ALTER SEQUENCE campaign_deliveries_id_seq  RESTART WITH 1;
ALTER SEQUENCE marketing_campaigns_id_seq  RESTART WITH 1;
ALTER SEQUENCE classification_events_id_seq RESTART WITH 1;
ALTER SEQUENCE admin_notifications_id_seq  RESTART WITH 1;
ALTER SEQUENCE subscribers_id_seq          RESTART WITH 1;
ALTER SEQUENCE bulk_replies_id_seq         RESTART WITH 1;
ALTER SEQUENCE bulk_enquiries_id_seq       RESTART WITH 1;
ALTER SEQUENCE ingredient_price_events_id_seq RESTART WITH 1;
ALTER SEQUENCE recipe_items_id_seq         RESTART WITH 1;
ALTER SEQUENCE recipes_id_seq              RESTART WITH 1;
ALTER SEQUENCE production_batches_id_seq   RESTART WITH 1;
ALTER SEQUENCE product_cost_records_id_seq RESTART WITH 1;
ALTER SEQUENCE ingredients_id_seq          RESTART WITH 1;
ALTER SEQUENCE ingredient_categories_id_seq RESTART WITH 1;
ALTER SEQUENCE overhead_months_id_seq      RESTART WITH 1;
ALTER SEQUENCE coupons_id_seq              RESTART WITH 1;
ALTER SEQUENCE testimonials_id_seq         RESTART WITH 1;
ALTER SEQUENCE users_id_seq                RESTART WITH 1;

COMMIT;
```

26 sequence restarts — one per cleared table that has an id sequence.
(`ALTER SEQUENCE … RESTART` is transactional; a rollback undoes the
restarts together with the deletes. `loyalty_vouchers` and `labour_rates`
have none — string/date PKs; the only other sequence-less table,
`vat_rates`, is preserved.)

## 9. Step 2 — initial admin recreation (secure, after the wipe)

1. In the Render dashboard, restart the web service (Manual Deploy → deploy
   latest commit, or Restart). Boot then runs, in order: `create_all()`
   (idempotent — no schema change), `seed_db()` → products exist →
   `_restore_promised_content()` re-creates **WELCOME10 + FRESHBAKE15**
   (see §6 — pause them from the desk if unwanted) → `_ensure_admin()` →
   finds zero admins → `_bootstrap_admin()` creates the account named by
   the existing `ADMIN_EMAIL` / `ADMIN_PASSWORD` Render env vars,
   role=admin, bcrypt-hashed (`app/seed.py:393-439`).
2. The password never appears in SQL, chat, or logs (the boot log names
   only the email). No env variable is changed.
3. Log in at `/login` with those credentials. Optional: rotate
   `ADMIN_PASSWORD` in Render later + restart.

## 10. Step 3 — post-reset verification checklist

1. Inspection script re-run: all 28 cleared tables at 0 rows; all 7 kept
   tables at their before-counts; suppression rows intact.
2. `SELECT last_value, is_called FROM users_id_seq;` → `1 | f`.
3. Health: `GET /api/health` → 200 `db-up`; storefront loads.
4. `GET /api/store` → `currency: "eur"` and the approved tax/shipping rules
   unchanged (env-resident, untouched).
5. Admin login works; Products desk shows the 25 products, prices, images,
   VAT classifications unchanged; Costing desk is empty (fresh book);
   Promotions desk shows exactly the two re-seeded codes, both active with
   **no minimum** (pause from the desk if unwanted; set a EUR minimum there
   if the €40 idea returns) and no personal vouchers; Marketing desk empty
   of campaigns with the suppression list still populated; Users roll shows
   only the initial admin; no testimonials or coming-soon changes on the
   storefront (coming-soon preserved, testimonials gone).
6. Checkout quote still works (catalog + env rules intact).
7. Render dashboard: no env changes made; Stripe untouched.

## 11. Execution preconditions (nothing runs until the owner approves)

1. §5 decisions are on record (done — this revision).
2. The no-backup posture extended to the wider scope (covered by §5's
   confirmation), or a backup taken.
3. §7 inspection run and matching this plan's expectations.
4. Quiet maintenance window (the transaction itself takes seconds).
5. Owner's explicit go-ahead on the final §8 SQL.

## 12. Local dry-run evidence (PostgreSQL 18.3, 2026-11-06 — production untouched)

Ran against a throwaway local cluster (initdb'd, port 55433, deleted
afterwards) staged with the app's own `create_all()` + demo seed plus every
row the demo seed omits: all 28 cleared tables non-empty (incl. a complete
costing book, a personal voucher, a loyalty tier link, 3 testimonials), all
7 kept tables non-empty. Scripts: `Temp/reset_test/` (gitignored).

**Reset execution — 17/17 PASS**
- A deliberately failing variant of the exact plan SQL raised and **rolled
  back atomically**: users/orders/coupons/ingredients all still at their
  before-counts.
- Wrong costing order refused as predicted: `DELETE FROM ingredients` before
  `recipe_items` → `IntegrityError` (the RESTRICT proof — the plan's order
  is mandatory).
- The real plan SQL (28 DELETEs + 26 sequence restarts, one transaction)
  executed clean: **all 28 cleared tables at 0**; all 7 kept tables at
  exact before-counts; `product_vat_events` kept its row with
  `admin_user_id` SET NULL and the admin-name snapshot intact.
- Sequences read from the sequence objects (`SELECT last_value, is_called`):
  users/orders/coupons/recipes/recipe_items/ingredients all `1 / f`; the
  first insert after the reset took **id 1**. Confirmed `labour_rates` and
  `loyalty_vouchers` have no sequence (the plan restarts none for them).

**Boot after the reset (fresh process, env set before imports — like a
Render boot; SEED_DEMO_DATA=false) — 18/18 PASS**
- Boot re-created **exactly one admin** from `ADMIN_EMAIL`/`ADMIN_PASSWORD`,
  bcrypt-verified; `users` = 1.
- **Coupon safeguard confirmed:** boot re-created **exactly WELCOME10 +
  FRESHBAKE15**, both active, **both with no spend minimum** (FRESHBAKE15's
  USD $40 was deliberately not carried to EUR — the €40 decision stays with
  the owner on the Promotions desk). No personal vouchers, no loyalty tier
  links, no testimonials, no costing rows were (re)created; coming-soon,
  suppression, products, vat_rates, product_vat_events, store_settings and
  categories unchanged by boot.
- A second boot added nothing further (coupons still 2, users still 1) —
  the re-seed is idempotent.

Total: **35/35 checks passed.** Production was never contacted.
