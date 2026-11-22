# Payments — Rehearsal and Going Live (Stripe + PayPal)

Status: **prepared 2026-11-22 — nothing in this document has been executed
against production.** The code it describes lives on the branch
`payments/unified-ledger-paypal`; production still runs the previous
release until the owner merges and deploys it (step 1).

What has been verified, and where:

| | Where it was proven |
| --- | --- |
| Order, ledger, refund, dispute and webhook logic | automated suite (provider calls replaced by fakes) |
| The requests the shop sends to Stripe and PayPal | unit tests against a stubbed SDK / stubbed wire |
| Row locking, unique constraints, concurrent requests | a throwaway local PostgreSQL 18 — never production |
| Upgrade from the schema production runs today | same throwaway PostgreSQL: old release's schema, then this one booted on it |
| Checkout, order page and desk tools in a browser | built frontend against stand-in provider pages |
| **A real Stripe or PayPal account accepting these requests** | **not yet — no credentials exist on the build machine. Steps 2 and 3 are that proof.** |

Nothing below asks for a real payment as a test. Rehearsals use Stripe test
keys and the PayPal sandbox; the live switch is verified by read-only
checks and by watching the first genuine order.

---

## What changes when this release boots

Four new tables are created (`payment_transactions`, `payment_refunds`,
`payment_events`, `checkout_attempts`), and two empty, optional columns are
added to `store_settings` (`legal_notice`, `delivery_postal_codes` — the
shop's legal notice and its delivery area, both blank until the owner fills
them in under Settings). **No existing column or row is altered** — the
upgrade is additive, and the previous release ignores the new tables and
columns, so the code can be rolled back without touching the database (one
exception: see "Rolling back").

Payments made before this release keep working: an order paid through
Stripe under the old code gets its ledger row written from the order's own
record the first time it is refunded or opened at the desk.

## The rules the system keeps

- Coming back from a pay page proves nothing. An order turns **paid** only
  on the provider's word: its signed webhook, the shop asking it when the
  customer returns, or the periodic check (every 15 minutes).
- The amount charged is the stored order total, computed on the server. A
  total sent by a browser is never used.
- One press of the pay button is one order, however often it arrives.
- A webhook delivered twice acts once; events may arrive in any order.
- A cancelled order is never captured; money that lands on one anyway
  (a Stripe tab left open) is sent straight back and the desk is told.
- "Refunded" means the provider confirmed a refund. The desk cannot label
  a provider-paid order refunded by hand.
- Every change of payment state leaves a line in the audit trail.

---

## Step 0 — Before the deploy: a backup you have checked

1. In the Render dashboard, open the database **bakehouse-db** and confirm
   it is the one the web service **djinis-bakehouse** uses (the service's
   `DATABASE_URL` comes from it).
2. Create a backup/export of it (the database's recovery/backups page; or
   `pg_dump` using the External Database URL, which only the owner holds).
3. **Check it**: it finished without error, it is not empty, and you know
   how you would restore it. Write down the time it was taken.

The deploy does not need the backup to succeed — it only adds tables and
two empty columns — but no change reaches a database holding real orders
without one.

## Step 1 — Deploy

1. Merge `payments/unified-ledger-paypal` into `main`. Render deploys
   `main` automatically.
2. Watch the deploy log. The boot ends with a line naming the tills, e.g.
   `Payments: Stripe test mode · PayPal off`, and no traceback.
3. Confirm the new release is what is being served (compare a string only
   this release contains — bundle hashes differ between builds):

   ```bash
   curl -s https://djinisbakehouse.com/api/health
   curl -s https://djinisbakehouse.com/ | grep -o 'assets/index-[^"]*\.js'
   curl -s https://djinisbakehouse.com/assets/<that file> | grep -c "How would you like to pay?"
   ```

4. In the Render shell, from the repository root:

   ```bash
   python backend/scripts/check_payments.py
   ```

   It only reads. It prints what is configured, asks Stripe and PayPal
   about the account and the webhook, looks at the shop around the
   payments (is email on, is a legal notice published, what the shop's
   clock says, the delivery area), and ends with either "Nothing blocks
   online payments" or the list of what does.

**If the deploy misbehaves:** Render → the service → Deploys → roll back to
the previous deploy. The new tables and columns stay where they are, unused.

## Step 2 — Rehearse on Stripe test keys

Production already runs on Stripe **test** keys, so this step is a real
rehearsal on the real site with no real money.

1. Stripe dashboard (test mode) → Developers → Webhooks. Make sure exactly
   one endpoint delivers to

   `https://djinisbakehouse.com/api/payments/webhook`

   (an endpoint still pointing at the old `onrender.com` address must be
   repointed or deleted) and that it is subscribed to all of:

   ```
   checkout.session.completed
   checkout.session.async_payment_succeeded
   checkout.session.async_payment_failed
   checkout.session.expired
   charge.refunded
   refund.updated
   charge.dispute.created
   charge.dispute.closed
   ```

   Optional, used when present: `refund.failed`, `charge.refund.updated`.
   Its signing secret (`whsec_…`) is `STRIPE_WEBHOOK_SECRET` in Render.
2. Stripe dashboard → Settings → Payment methods: switch on what the shop
   should offer. The shop asks Stripe for its automatic set, so this page
   — not the code — decides what customers see. (`STRIPE_PAYMENT_METHODS`
   in Render, if it is set from an earlier release, narrows that set;
   remove it or set it to `automatic` to let the dashboard decide.)
3. Run `python backend/scripts/check_payments.py --rehearse`. It builds a
   one-euro test pay page exactly as checkout does and closes it again —
   proof that Stripe accepts what this release sends.
4. On the site, as a customer:
   - Buy something with `4242 4242 4242 4242` (any future date, any CVC).
     The order page must say **Order confirmed / Paid**, and the receipt
     email must arrive.
   - Buy again with `4000 0025 0000 3155` — the bank's 3-D Secure step
     appears on Stripe's page; the order is paid after it.
   - Buy again with `4000 0000 0000 9995` — Stripe's page declines it.
     Going back to the shop, the order is unpaid and offers **Complete
     payment**.
   - Start a purchase and leave Stripe's page: the order says you left
     before paying; **Complete payment** opens a fresh page.
5. At the desk (Orders → Manage the paid order):
   - The **Payment** panel shows Stripe, the method used, the amounts and
     Stripe's own ids.
   - **Refund…** part of it. The panel shows the refund as *Sent back*,
     the order as *Partially refunded*, the customer gets the refund email,
     and the refund appears in the Stripe dashboard.
   - Refund a little more **from the Stripe dashboard**. Within a minute
     the panel shows it too (this is the `charge.refunded` webhook).
   - Cancel the order: the rest goes back through Stripe.
6. Orders → **Payment check** lists anything that does not add up. After a
   clean rehearsal it should name nothing from these test orders.

## Step 3 — Rehearse PayPal in the sandbox

1. developer.paypal.com → Apps & Credentials, **Sandbox**: create (or
   open) a REST app. Its Client ID and Secret are `PAYPAL_CLIENT_ID` and
   `PAYPAL_CLIENT_SECRET`.
2. In the same app → Webhooks → add one delivering to

   `https://djinisbakehouse.com/api/payments/paypal/webhook`

   subscribed to:

   ```
   CHECKOUT.ORDER.APPROVED
   PAYMENT.CAPTURE.COMPLETED
   PAYMENT.CAPTURE.PENDING
   PAYMENT.CAPTURE.DENIED
   PAYMENT.CAPTURE.DECLINED
   PAYMENT.CAPTURE.REFUNDED
   PAYMENT.CAPTURE.REVERSED
   CUSTOMER.DISPUTE.CREATED
   CUSTOMER.DISPUTE.RESOLVED
   ```

   The **Webhook ID** PayPal then shows is `PAYPAL_WEBHOOK_ID`.
3. In Render set the three values and `PAYPAL_ENVIRONMENT=sandbox`. The
   PayPal option appears at checkout (with the test-mode notice).
4. `python backend/scripts/check_payments.py --rehearse-pay` creates a
   one-euro sandbox order and prints its approval link. Approve it with a
   sandbox **personal** (buyer) account, then run the
   `--finish-paypal <id>` command it printed: it captures and refunds the
   euro — proof that PayPal accepts every request this release makes.
5. On the site: pay an order with PayPal using the sandbox buyer; leave
   PayPal once without approving and use **Complete payment with PayPal**;
   refund part of a paid order from the desk; refund a little from the
   sandbox PayPal account and watch the panel pick it up.

## Step 4 — Go live with Stripe

Do this only after step 2 is clean.

1. The Stripe account must be **activated**: business details and identity
   verified, a bank account for payouts, the public details customers see
   (statement descriptor, support contact). `check_payments.py` reports
   whether Stripe says the account can take charges.
2. In **live** mode, create the webhook endpoint again (same address, same
   events as step 2). Live and test endpoints are separate and have
   different signing secrets.
3. In Render, replace **both** values together: `STRIPE_SECRET_KEY` with
   the live secret key and `STRIPE_WEBHOOK_SECRET` with the live
   endpoint's secret. (A restricted key is safer than the full secret key:
   it needs to write Checkout Sessions, Coupons and Refunds, and to read
   Payment Intents — plus, for the readiness check only, to read Webhook
   Endpoints and Payment Method Configurations.)
4. After the restart, run `python backend/scripts/check_payments.py` once
   more. It must report **live mode**, charges enabled, and the live
   endpoint subscribed to every event. The test-mode notice is gone from
   checkout.
5. Do **not** buy from your own shop to test it. Watch the first genuine
   card order instead: it should turn paid within seconds. If it does not,
   open it at the desk and press **Check with Stripe** — and look at the
   webhook's delivery log in the Stripe dashboard.
6. **PayPal in the meantime.** The two providers go live separately, and
   checkout marks each option's own mode. A sandbox PayPal option on a
   live shop is still a dead end for real customers — they see it, with
   its test-mode notice, and cannot pay with it. Either take PayPal live
   in the same sitting (step 5) or clear `PAYPAL_CLIENT_ID` until it is
   ready.

## Step 5 — Go live with PayPal

Do this only after step 3 is clean.

1. A PayPal **Business** account, verified by PayPal, with EUR enabled.
2. developer.paypal.com → Apps & Credentials, **Live**: create the REST
   app and, in it, the webhook (same address and events as step 3). Live
   credentials and the live webhook id are different from the sandbox ones.
3. In Render, replace all four together: `PAYPAL_CLIENT_ID`,
   `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, and
   `PAYPAL_ENVIRONMENT=live`. Mixed values are refused by PayPal at the
   first call — nothing is charged, PayPal checkout simply fails until
   they agree.
4. Run `python backend/scripts/check_payments.py` and watch the first
   genuine PayPal order, as in step 4.

---

## Running the shop afterwards

**Refunds.** Orders → Manage → Payment → **Refund…** sends money back
through the provider that took it — all that is still held, or a part. The
order's status changes only when the provider confirms; a refund the
provider refuses is kept on the record as *Not sent*, with its reason, and
can be tried again. Cancelling a paid order refunds what is still held
automatically.

**A payment that never showed up.** Open the order and press **Check with
Stripe / PayPal**. The shop asks the provider directly and records what it
says. Do not mark the order paid by hand unless the money really arrived
some other way.

**Payment check** (Orders → Payment check) reads the shop's own ledger and
lists what does not add up:

| It says | It means | What to do |
| --- | --- | --- |
| Payment never arrived | unpaid long after the pay window | Check with the provider |
| Payment still settling | paid with a method that confirms later | wait; hold the bake until *Paid* |
| Paid twice | two payments held for one order | refund the extra from the panel |
| Money held for a cancelled order | a refund did not go through | refund it from the panel |
| Refund not sent | the provider refused a refund | try again, or use the provider's dashboard |
| Refund slow to confirm | pending for days | look it up at the provider |
| Dispute open | a chargeback or PayPal case | answer it in the provider's dashboard before its deadline |
| Charged amount differs from the order | should never happen | compare with the provider before anything else |
| Order and ledger disagree | should never happen | Check with the provider |
| Marked paid by hand | no provider payment behind the label | confirm the money really arrived |

**Disputes** are answered in the Stripe dashboard or PayPal's Resolution
Centre, not here — the shop records that one opened and how it ended.
No setup prevents every chargeback or fraudulent payment.

## Switching a provider off, and rolling back

- Remove `STRIPE_SECRET_KEY` (or the PayPal credentials) and that option
  disappears from checkout at the next restart. Existing orders stay
  readable; refunds through that provider wait until it is configured
  again.
- **Rolling the code back** to the previous release is safe **until the
  first PayPal order exists.** The previous release does not know the
  `paypal` payment method and would fail to display such an order. After
  that point, switch PayPal off instead of rolling back.

## For the owner to decide or have reviewed

These are business and legal questions the software does not answer.

1. **Legal pages.** Privacy, Terms and Refunds now mention PayPal while it
   is switched on, and nothing else in them was changed. Have them
   reviewed for: naming each payment processor and what is shared with it
   (the order number, amount and items; for Stripe also the email
   address), the legal basis, and the processors' own privacy notices.
   **The legal notice (Impressum) is yours to write**: Settings → Legal
   notice. Until it has text there is no such page and no footer link —
   the software never invents one. A shop selling online in Germany or
   the EU is expected to name its operator; what yours must contain is a
   question for your accountant or a lawyer.
2. **The order button.** Card and PayPal buttons state the amount ("Pay
   €11.02", "Pay €11.02 with PayPal"). The cash button reads "Place
   order". Where the law requires the button to spell out the obligation
   to pay, have that wording checked.
3. **Prices and VAT.** Whether prices are shown and charged VAT-inclusive
   is the existing `TAX_MODE` decision (products are still unclassified);
   it is unchanged by this release. The VAT number belongs in the Stripe
   dashboard's tax settings.
4. **Withdrawal and refunds.** The Refunds page describes the shop's own
   policy for fresh food. Confirm it matches the consumer rules that apply.
5. **PayPal Seller Protection.** PayPal orders are created without a
   shipping address (the shop already holds it, and PayPal then cannot
   refuse a payment over an address format). That likely makes them
   ineligible for Seller Protection claims that depend on the address. If
   protection matters more, the delivery address can be passed to PayPal
   instead — a code change.
6. **Which methods to offer.** Decided in the Stripe dashboard. Methods
   that settle later (SEPA debit) leave the order "being confirmed" for
   days and can fail afterwards; the shop holds the bake until *Paid* only
   if the desk follows the Payment check.
7. **The shop's clock.** "Today" — for order numbers and the earliest
   delivery date a customer may pick — and the times printed in emails,
   receipts and CSV exports follow `SHOP_TIMEZONE` (default
   `Europe/Berlin`); pages show times in the reader's own time zone.
   Change the variable if the bakery keeps another clock.
8. **Where you deliver.** Settings → Delivery area takes the postal codes
   the van reaches (`101*` covers an area). Left empty, checkout accepts
   an address anywhere — which is how it has always behaved.
9. **Newsletter sign-ups.** The footer form stores an address the moment
   it is typed (no confirmation email). The list is separate from campaign
   consent and is only ever exported for your own newsletter tool —
   whether that tool should ask each address to confirm (double opt-in)
   before the first mailing is a question for whoever advises you on
   e-marketing law.

## Environment variables

| Variable | Secret | Meaning |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | yes | `sk_test_…` rehearses, `sk_live_…` charges; empty = no card option |
| `STRIPE_WEBHOOK_SECRET` | yes | signing secret of the endpoint for the same mode |
| `STRIPE_PAYMENT_METHODS` | no | `automatic` (default) or a list that narrows Stripe's own set |
| `PAYPAL_CLIENT_ID` | yes | REST app credentials; both set = PayPal option on |
| `PAYPAL_CLIENT_SECRET` | yes | |
| `PAYPAL_ENVIRONMENT` | no | `sandbox` (default) or `live` — must match the credentials |
| `PAYPAL_WEBHOOK_ID` | no | id of the webhook for the same environment |
| `PUBLIC_BASE_URL` | no | the shop's one public address (`https://djinisbakehouse.com`) — every emailed link and every return from a pay page is built on it; set it in production |
| `SHOP_TIMEZONE` | no | the bakery's clock (default `Europe/Berlin`) |

Secrets are entered only in Render's environment settings. They are never
committed, never placed in `.env.example`, and never printed by the app or
by `check_payments.py`.
