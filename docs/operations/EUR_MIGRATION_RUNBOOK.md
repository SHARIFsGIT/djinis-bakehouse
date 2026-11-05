# EUR Migration & Customer/Order Reset — Operations Runbook

Status date: 2026-11-05. Owner: Djini's Bakehouse.
Scope: the euro cutover shipped in the working tree (backend + frontend) and the
customer/order data reset it prepares for.

**Nothing in this runbook has been executed against production.** No backup is
claimed to exist. Every step below is a future, manual, owner-approved action —
the code changes themselves are sitting uncommitted for review.

---

## Where the shop stands after the cutover

- **EUR is the only chargeable currency.** New products, orders, checkout,
  refunds-on-new-orders and costing inputs are EUR-only. Historical rows keep
  the currency they were placed in (USD rows render as USD and are never
  summed with euro money).
- **Product prices were not converted.** Every existing price and sale price
  keeps its numeral; only the currency interpretation changed to EUR
  (`$3.50` → `€3.50` — same 3.50, different symbol). A before/after
  comparison is in the migration report.
- **Checkout is fail-closed while delivery pricing is unapproved.** With
  `SHIPPING_FEE` / `FREE_SHIPPING_THRESHOLD` unset, quoting and checkout
  answer `503` with *"Checkout is paused — the shop's delivery pricing is
  being finalised. Please try again soon."* The pay-link route (`POST
  /api/payments/{order}/session`) shares the same gate: while the rules are
  unset, no new Stripe session is minted for any order — stored totals are
  never recalculated, charging simply waits. Setting an approved value for
  **both** re-opens checkout and pay links. With `TAX_RATE` unset, no tax
  line is charged or shown anywhere — never a 0% rate.

## Old USD values — pending, NOT approved as EUR

These figures were correct as dollars. None of them is an approved euro value,
and none is set anywhere in the code:

| Setting | Old USD value | Status |
| --- | --- | --- |
| Tax rate | 8.75% | **Pending professional review.** German VAT treatment (7% reduced / 19% standard, inclusive vs exclusive) must come from a tax adviser. No rate is invented or defaulted. |
| Delivery fee | $5.99 | Pending owner approval as an EUR figure. |
| Free-delivery threshold | $50.00 | Pending owner approval as an EUR figure. |
| VIP spend threshold | $150.00 | Pending owner approval as an EUR figure (tiers currently ride order counts, which still work). |
| FRESHBAKE15 minimum spend | $40.00 | **Pending review in existing databases.** A database that already carries the coupon keeps its stored `40.00` minimum — a dollar numeral. It is marked *unapproved*: shoppers see no threshold and cannot apply the code (quote, preview and checkout all refuse it with a "being reviewed" message) until an administrator re-saves the minimum from the **Promotions desk** — typing it there is the approval as a euro figure (or clear it to none). Fresh databases seed the coupon with no minimum. |

Until approved values exist, leave `TAX_RATE`, `SHIPPING_FEE`,
`FREE_SHIPPING_THRESHOLD` and `VIP_SPEND_THRESHOLD` **unset** in every
environment. The storefront already hides the free-delivery promise, the
delivery-progress nudge and the tax row while they are unset.

---

## 1. Required verified Render database backup

Before any reset is even considered, take a fresh backup of the Render
PostgreSQL database **and verify it** (point 2). How backups are taken depends
on the Render plan in use (dashboard manual snapshot, `pg_dump` from a shell,
or an automated snapshot window) — confirm which applies before relying on it.

**No backup currently exists or has been verified by this migration.** Nothing
in the shipped code depends on one being present.

## 2. Confirming the backup is usable before reset

A backup that has never been restored is a hope, not a backup. Before the
reset, restore the backup to a scratch location (a local Postgres instance or a
temporary Render database) and check:

1. The restore completes without errors.
2. Row counts for `products`, `orders`, `users` match the source at backup time.
3. The application can boot against the restored database (read-only check).

Only a backup that passes all three counts as verified.

## 3. The exact dry-run command

From `backend/`:

```
python scripts/reset_customer_order_data.py
```

This is the default mode. It deletes nothing: it prints the database identity
(no credentials), the deletion order it derived from the live foreign keys, the
customers/order-children/notification counts it *would* delete, the row counts
of every table, and its notes (including the Stripe and loyalty-vouchers
caveats below). The real deletion only happens with:

```
python scripts/reset_customer_order_data.py --execute
```

On any **non-SQLite** database (PostgreSQL, MySQL, …) `--execute` additionally
refuses unless the exact identity the dry run printed is passed back:

```
python scripts/reset_customer_order_data.py --execute \
    --confirm-database "postgresql+psycopg://user:***@host:5432/dbname"
```

This is deliberate: `APP_ENV` alone is not trusted to identify a safe target
(it defaults to `dev`, so a production PostgreSQL URL with a missing APP_ENV
must not slip through on a flag). The `APP_ENV=production` refusal is absolute
and no confirmation gets past it.

## 4. Reviewing row counts and protected records

The dry run prints before-counts for all 30 tables. Check them against
expectations:

- **Would delete (all rows):** `orders`, `order_items`, `order_events`,
  `order_tax_lines`, `reviews`, `favorites`, `addresses`.
- **Would delete (matching rows only):** `users` where `role = 'customer'`;
  `admin_notifications` where kind is `order`, `review` or `customer`
  (enquiry pings stay — bulk enquiries are kept).
- **Protected, verified unchanged after any execution:** `products` (with
  prices), `categories`, `coupons`, `loyalty_vouchers`, `subscribers`,
  `bulk_enquiries`, `bulk_replies`, `testimonials`, `coming_soon_items`,
  `vat_rates`, `store_settings`, `product_vat_events`, `ingredient_categories`,
  `ingredients`, `ingredient_price_events`, `recipes`, `recipe_items`,
  `production_batches`, `overhead_months`, `labour_rates`,
  `product_cost_records`, and the admin account(s).

Any table the script does not recognise aborts the run — schema drift is
reported, never guessed through.

**Known ambiguity — loyalty vouchers.** The reset brief listed "customer
loyalty vouchers", but `loyalty_vouchers` is shop configuration (which coupon
each loyalty tier hands out), holds no customer rows, and is therefore KEPT.
The script says so in every run's notes. Deleting it is a separate, explicit
owner decision; until then it stays.

## 5. Explicit approval required before execution

A real reset needs, in writing from the owner:

1. The verified backup from points 1–2 (its date and where it rests).
2. Confirmation that all order/payment history may be destroyed, including
   Stripe Sandbox payment records that will stop being referenced (the script
   never calls Stripe — refunds, if owed, are handled in the Stripe dashboard
   beforehand).
3. The exact date/time of the maintenance window.

No code path executes the reset automatically — not at startup, not on deploy.
It is a human running `--execute` at a terminal, deliberately.

## 6. Maintenance-window reset procedure

The script refuses to run where `APP_ENV=production` (`backend/scripts/
reset_customer_order_data.py`, first check in `run_reset`). That refusal is
intentional and is not bypassed by this runbook: **a production reset is not
executable with the script as shipped** and would require a new, explicitly
reviewed procedure and owner approval to change that guard (plus a verified
backup first). Independent of APP_ENV, `--execute` against any non-SQLite
database (staging Postgres included) refuses without `--confirm-database
"<identity>"` carrying the exact identity string the dry run printed — see
point 3. What the scripted procedure covers today is any non-production
environment:

1. Put the shop in maintenance mode / take it offline for the window.
   This is not just caution: the script's pre-flight counts and its
   after-verification read **outside** the delete transaction (the report
   is written for humans, not as a lock). A write landing mid-run — a
   customer placing an order between the delete and the after-count —
   shows up as a PROBLEM in the after-report even though the deletion
   itself was sound. A quiet window is what keeps the report readable.
2. Take and verify the backup (points 1–2).
3. Run the dry run (point 3); read every count.
4. Run `--execute` (plus `--confirm-database` on a non-SQLite target).
   Everything happens in **one transaction** — children before parents — so
   a failure partway rolls back completely and leaves the database untouched.
5. Read the after-report: deleted tables at zero, admin count unchanged,
   protected tables unchanged, problems list empty. (A non-empty problems
   list after a run in a quiet window means investigate — see point 1.)

## 7. Post-reset database verification

After an execution, verify independently of the script's own report:

1. Admin login still works (the account was kept, not recreated).
2. Products, categories, prices, images unchanged in the admin desk.
3. Newsletter subscriber list and bulk enquiries still listed.
4. Storefront checkout still fail-closed (or live, per the approved values).
5. Coupons and loyalty tiers still configure the promotions desk.

## 8. EUR Sandbox payment and refund verification

Stripe remains **Sandbox only** — going live is out of scope and nowhere
enabled. Once checkout is re-opened with approved values, verify in Sandbox:

1. Place a card order end-to-end (Stripe test card) — the charge, receipt and
   order status (paid, EUR throughout; no USD anywhere).
2. Cancel/refund that order from the admin desk — the refund follows the
   original payment intent, in its original cents.
3. Check the dashboard's revenue split shows the euro bucket only.

## 9. Shipping and VAT decisions still required

Before reopening checkout, the owner must approve EUR values for the delivery
fee and the free-delivery threshold (point 9 of the business decisions), and a
tax adviser must settle VAT treatment (rate basis, inclusive vs exclusive —
`TAX_MODE` stays `exclusive` until then; no receipt may present the old 8.75%
US rate as German VAT). The VIP spend threshold can wait: tiers ride order
counts meanwhile. Also part of reopening: **review FRESHBAKE15's minimum** in
the Promotions desk (the "pending review" badge) — re-type it as an approved
euro figure or clear it; until then the code cannot be applied at all (by
design, see the table above).

## 10. Render environment variables to review manually

Reviewed in the Render dashboard by the owner — **not changed by this
migration**:

| Variable | Required value | Why |
| --- | --- | --- |
| `STRIPE_CURRENCY` | `eur` (or unset — the code default is `eur`) | Makes the charge currency explicit; USD is refused by the gateway. |
| `TAX_RATE` | unset until VAT is professionally settled | Any value here is charged as tax on every order. |
| `SHIPPING_FEE` | unset until an EUR fee is approved | Unset = checkout paused (fail-closed). |
| `FREE_SHIPPING_THRESHOLD` | unset until approved | Same gate as the fee. |
| `VIP_SPEND_THRESHOLD` | unset until approved | Tiers ride counts meanwhile. |
| `APP_ENV` | `production` (unchanged) | Also what makes the reset script refuse to run there. |

Also in the **Stripe dashboard** (the onrender.com subdomain stopped working
2026-11-04; the canonical URL is `djinisbakehouse.com`): repoint the webhook
endpoint to `https://djinisbakehouse.com/api/payments/webhook`. Without it,
paid orders rely on the self-healing reconciliation sweep instead of instant
webhook confirmation.

## 11. Rollback and restore — and its limitations

**The deletion is irreversible without a verified backup.** There is no undo
command and no soft-delete. After an `--execute`:

- What a restore gives back: the entire database as of the backup — products,
  orders, customers, everything, including anything that happened between the
  backup and the reset (which is lost).
- What a restore does **not** give back: nothing at Stripe. Payment intents,
  charges and refunds live at Stripe and are unaffected either way; the
  restored database only re-links to them by id.
- Limitations to confirm for the plan in use before relying on any of this:
  how far back Render's automated snapshots reach, whether a restore targets a
  new database or overwrites the live one, and how long a restore takes.

If the reset ran without a verified backup, the customer/order data is gone —
which is why point 5 refuses that sequence.
