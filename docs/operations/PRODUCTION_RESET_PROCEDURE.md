# Production Customer/Order Reset — Separately Reviewed Procedure

Status: **DRAFT for owner review — nothing in it has been executed.**
Prepared 2026-11-05 at the owner's request, after the owner authorised
deleting all test customer accounts and customer/order-related production
records **without taking a backup**.

This document is the "new, explicit owner decision" the reset script's
production guard requires. The script as shipped
(`backend/scripts/reset_customer_order_data.py`, commit `e87c72e`) refuses
to run wherever `APP_ENV=production` — on Render itself a reset is
impossible, by design, and **that guard is not changed or bypassed by this
procedure**. What this procedure covers is a deliberate, human-run,
identity-confirmed execution from the owner's own machine, gated on the
sign-offs below.

---

## 1. Owner authorisation on record (2026-11-05)

- Scope authorised: the agreed customer/order reset scope (below) against
  the production database.
- Backup: **explicitly waived by the owner.** This conflicts with
  `EUR_MIGRATION_RUNBOOK.md` §1–2, which requires a verified backup before
  any reset. The waiver is accepted only by the countersignature below.

> I understand the deletion is irreversible, that no backup exists, that no
> restore of customer/order data will be possible afterwards, and that
> Stripe-side records (payment intents, charges, refunds) survive
> independently at Stripe and are unaffected either way.
>
> Owner signature: ______________________   Date: ____________

## 2. What is deleted — the agreed scope (and nothing else)

**Deleted entirely:** `orders`, `order_items`, `order_events`,
`order_tax_lines`, `reviews`, `favorites`, `addresses`.

**Deleted selectively:** `users` rows with `role = 'customer'` (every admin
account is preserved by id, verified after); `admin_notifications` of kind
`order`, `review` or `customer` (enquiry pings about kept bulk leads stay).

**Preserved and verified unchanged by the script's own after-report:**
`products` (with prices and uploaded images on the uploads disk), 
`categories`, `coupons` **and their settings — including
`min_subtotal`/`min_subtotal_approved` flags**, `loyalty_vouchers` (tier →
coupon configuration; the owner's "loyalty configuration" item — this table
holds no customer rows), `subscribers`, `bulk_enquiries`, `bulk_replies`,
`testimonials`, `coming_soon_items`, `vat_rates`, `store_settings`,
`product_vat_events`, and the entire costing book
(`ingredient_categories`, `ingredients`, `ingredient_price_events`,
`recipes`, `recipe_items`, `production_batches`, `overhead_months`,
`labour_rates`, `product_cost_records`), plus every admin account with its
existing login credentials and password.

**Never touched:** the database schema (no DROP, no TRUNCATE, no DDL — the
script issues row DELETEs only, inside one transaction), Stripe (the script
never calls it), and Render infrastructure.

The script aborts before deleting anything if it finds a user role other
than customer/admin, a notification kind it does not recognise, an
unrecognised table (schema drift), zero admin accounts, or a kept table
whose foreign keys would dangle.

## 3. Preconditions

1. This document countersigned (§1).
2. The maintenance window chosen (a quiet window is recommended; the
   deletion itself is one transaction of a few seconds).
3. The owner is logged in to the Render dashboard and can retrieve the
   database's **External Database URL**
   (Data → `bakehouse-db` → Connections → External URL). It contains the
   password — treat it as a secret; do not paste it into chats or tickets.
4. A terminal at `backend/` of this repository (the venv includes the
   PostgreSQL driver), on a machine where `APP_ENV` is **not** set to
   `production` (a laptop is fine; the default is `dev`).

## 4. Step 1 — read-only dry run (deletes nothing)

```powershell
$env:DATABASE_URL = "<external database URL from Render>"
.\venv\Scripts\python.exe scripts/reset_customer_order_data.py
```

Read the report line by line:

- **database** — the identity must be the production database you expect
  (host and database name from the Render dashboard), with the password
  masked.
- **customer accounts** — the number of test customers that would go.
- **admin accounts** — the number that will be kept and verified after.
- **row counts before** — every table; anything unexpected (an unknown
  table, a role you don't recognise) aborts the run on its own.
- **notes** — including the count of paid card orders carrying Stripe
  payment intents; those Stripe records will stop being referenced
  (refunds, if ever owed, are a Stripe-dashboard matter, done before or
  independently of this reset).

If anything in the dry run is unexpected, **stop here** and report to the
owner before proceeding.

## 5. Step 2 — execution (the only write, one transaction)

Re-run with both flags. `--confirm-database` must carry the **exact
identity string the dry run printed** — this is the independent
confirmation the non-SQLite guard demands; a confirmation naming any other
database is refused:

```powershell
.\venv\Scripts\python.exe scripts/reset_customer_order_data.py --execute --confirm-database "<exact identity from the dry run>"
```

Everything happens in one transaction, children before parents. A failure
partway rolls back completely (pinned by a regression test). The after-report
must show: status `executed`, an empty `PROBLEMS` list, deleted tables at
zero, **admin count unchanged**, and every protected table at its
before-count.

## 6. Step 3 — post-reset verification (independent of the script)

1. Admin login still works with the existing credentials (the configured
   administrator account was kept, not recreated).
2. Products, prices, images, categories unchanged in the admin desk.
3. Coupons (including FRESHBAKE15's pending-review minimum, unchanged) and
   loyalty tiers still configure the Promotions desk.
4. Newsletter subscribers and bulk enquiries/replies still listed.
5. Costing desk data intact.
6. Storefront checkout still paused (delivery pricing/VAT still pending
   owner approval — the reset does not change that posture).

## 7. Notes and conflict register

- **Backup waiver vs runbook:** runbook §1–2 makes a verified backup the
  first gate; the owner's 2026-11-05 authorisation waives it. The waiver is
  recorded and countersigned in §1 rather than silently applied.
- **The absolute production refusal stays:** on any machine actually
  configured `APP_ENV=production` (including Render's own shell) the script
  refuses before doing anything, confirmation or not. Only the combination
  (owner laptop + external URL + exact-identity confirmation) can execute,
  which is precisely the "separately reviewed procedure with explicit
  approval" the guard was designed to require.
- **No code changes are needed to execute this procedure** — the flags and
  guards shipped in commit `e87c72e` already support exactly this path, and
  the script remains dry-run-by-default for everyone else.
- **Stripe Sandbox remains the only payment mode**; this procedure does not
  touch Stripe settings, and payment records live at Stripe independently of
  the database rows deleted here.
