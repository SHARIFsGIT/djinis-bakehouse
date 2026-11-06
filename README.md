# Djini's Bakehouse

A small-batch bakery storefront — a complete, working e-commerce project built with a
modern Python backend and a modern React frontend. No API keys required: it runs
entirely on your machine with SQLite and local JWT auth.

```
frontend/   React 19 · Vite 7 · Tailwind CSS 4 · DaisyUI 5 · react-router 7 · axios · lucide-react
backend/    FastAPI · SQLAlchemy 2 · SQLite (any SQL DB via DATABASE_URL) · PyJWT · bcrypt
```

## Quick start

You need Python 3.11+ and Node 20+ (Vite 7 requires 20.19+).

**1. Backend** — from the project root:

```bash
python -m venv venv
venv/Scripts/activate        # Windows  (venv/bin/activate on macOS/Linux)
pip install -r backend/requirements.txt

cd backend
copy .env.example .env       # optional — defaults already work
python run.py                # serves http://127.0.0.1:8000 (PORT/HOST env vars override)
```

`run.py` works from any directory, checks that dependencies are installed and
that the port is free, and explains both problems in plain words instead of a
traceback.

One Windows trap worth knowing: if `python` on your PATH is Anaconda's base
interpreter or another project's venv, the server may start and then crash on a
missing package (`ImportError: email-validator is not installed…`). That
interpreter isn't the project's venv. Either activate the venv first (step 1
above) or skip activation and call it directly:

```bash
"venv/Scripts/python.exe" run.py        # from the backend folder
```

On first start the database is created and seeded automatically with
3 categories, 25 products (real photos), two vouchers, demo reviews, three
testimonials, three coming-soon items and a handful of historical orders.
(A production database seeds the catalog and your own admin login only — see
"Going to production" below.)

**2. Frontend** — in a second terminal:

```bash
cd frontend
npm install
npm run dev                  # serves http://localhost:5173, proxies /api to :8000
```

**3. Tests:**

```bash
pip install -r backend/requirements-dev.txt
cd backend && python -m pytest tests/ -q
```

## Demo accounts

| Role     | Email                      | Password       |
| -------- | -------------------------- | -------------- |
| Customer | `customer@example.com`     | `Customer123!` |
| Admin    | `admin@djinisbakehouse.com`| `Admin123!`    |

The login screen has one-click buttons that fill these in — they exist only
where demo data was seeded; on a production deployment they hide themselves
and the admin is the login you set at deploy time.

## What it does

**Shopfront** — catalog with category/search/price/sale/stock filters and sorting,
product pages with reviews (one per customer, editable), related items,
ingredients & allergens. Every product and category carries a real photo; images
ship pre-resized (900px, ~60–110 KB progressive JPEGs) via `scripts/prepare_images.py`.
The palette is drawn from the shop mark itself — deep burgundy with a lighter
wine secondary and honey gold over warm cream; at night the surfaces stay warm
— near-black cocoa backgrounds, the same burgundy-to-wine brand gradient, and
gold stepping back to an accent role (success is the logo's sage green, info
its cocoa; only the rating stars stay gold). One signature finish runs through
the whole shop: every brand surface — buttons, badges, the announcement bar,
the today strip, the rating meters — wears the same burgundy-to-wine gradient
(`.brand-gradient` in `index.css`, applied automatically to `btn-primary`,
`btn-secondary` and the brand badges). The category links sit dead-centre on
the main navbar, on the mark's own line — the search box narrows rather than
letting them collide — and the avatar goes straight to the customer's
"My corner" (orders, saved bakes, addresses, profile) or, for the baker,
straight to the desk. The two headline lines shoppers see (the announcement
bar and the home page's "today" note, a burgundy piece attached to the hero
photo) are served publicly from `GET /api/store` and editable under Settings.
The home page also carries two bands the baker curates from the same desk:
"Coming soon to the counter" (upcoming bakes with a photo, a when-label and a
line of copy) and "Word around the neighborhood" (hand-picked testimonials
with optional star ratings) — both served from the API and hidden when empty.

**Back-in-stock alerts** — a sold-out bake's page takes an email instead of
an order ("Tell me when it's back", guests included, rate-limited and
idempotent). The moment the baker refills the shelf from the desk, every
waiting shopper gets exactly one email — the asks are claimed and cleared
in the same save that restocked the shelf, so a second save can never
answer twice.

**Sale nudges (honest ones)** — the basket keeps a free-delivery progress meter
against the approved threshold (€25 for everyone, €15 for VIP regulars — the
line is judged on the basket's shelf value, never moved by a coupon), product
cards flag "Only N left" while stock lasts,
and the basket suggests three best-rated one-tap add-ons ("Complete the box")
that aren't already in it. A voucher board sits under the basket's tally:
every live code with its saving, one tap to apply — and threshold vouchers
(`FRESHBAKE15`, 15% off boxes from its minimum) show a "add €X more to unlock
it — worth €Y" meter that turns the gap into a goal instead of a rejection.
The chosen code rides along to checkout (re-validated against the live basket
and the threshold server-side) and quick-apply chips offer the other live
codes there too.

**Bulk & event orders** — a public `/bulk` page takes catering enquiries
(occasion, date from tomorrow onward, headcount up to 2,000, a required
phone number and the spread they have in mind), rate-limited and pre-filled
for signed-in customers. Every enquiry is acknowledged to the customer by
email the moment it lands. The baker works the leads from a second tab
under Orders — new → contacted → quoted → confirmed/declined — and answers
each one right in the drawer: the reply is emailed to the enquirer (a first
reply moves the lead to "contacted") and kept beside it, so the whole
conversation reads in one place. Her own private notes and a CSV export
round out the ledger.

**Saved bakes (wishlist)** — signed-in customers heart any bake from the grid or
product page; hearts sync to the server (`PUT/DELETE /api/products/{id}/favorite`,
idempotent) and surface in the navbar and under Account → Saved bakes. Guests are
invited to log in rather than losing the tap.

**Findable from the outside** — the shop serves a live `sitemap.xml`
(every standing page plus each live bake, stamped with its last change)
and a `robots.txt` that keeps crawlers out of the API and the private
corners; product pages carry their own `<title>`, description and
schema.org Product structured data (price, availability, aggregate
rating), so search engines and share cards speak the catalog's own words.

**Newsletter capture** — the footer collects sign-ups (`POST /api/newsletter`,
rate-limited, idempotent — re-subscribing says so instead of erroring). Admins see
the list under a Newsletter tab with subscriber counts and a one-click CSV export
for the mailing tool of their choice.

**Verified-purchase reviews** — reviews written by customers who actually bought
the bake (any non-cancelled order containing it) carry a "Verified purchase"
badge, computed from a Order⨝OrderItem query at read time. Product pages also
show the star distribution (5→1) so shoppers see the shape of the feedback.

**Cart & checkout** — cart persists in the browser; the server re-validates every
line (price, availability, stock) at checkout and is the source of truth for money.
Coupon codes (`WELCOME10`, `FRESHBAKE15` — manage them under Promotions, where
each code can carry a minimum basket) take a percent off the subtotal once that
minimum is met; tax is recalculated on the discounted amount. A code can also
be flagged **one per customer** — the welcome voucher is: once a customer has a
standing order that carried the code, it drops off *their* voucher board and
both preview and checkout refuse it; cancelling that order hands the code back.
The seeder restores these codes on startup if they're ever deleted from the
console, because the storefront copy promises them.
Payment is cash on delivery (settled by staff when the order is marked delivered)
plus — once Stripe is configured — **online card payments through Stripe's hosted
Checkout**: the order is placed unpaid, the customer pays on Stripe's page (card
details never touch this server, and 3-D Secure/SCA is handled there), and the
`checkout.session.completed` webhook marks the order paid and emails the receipt.
The hosted page mirrors the shop — one line per product with its photo, delivery
and tax rows, the coupon as a real discount row, the bakery's cream/burgundy
palette and a serif face (`app/stripe_gateway.py` builds the session; the lines
always sum to the stored order total in integer cents). Logo and bank-statement
descriptor come from the Stripe dashboard's branding settings.
A synchronous confirm check runs when the customer returns so the page is correct
even if the webhook is still in flight. Unpaid card orders can be paid again with
a fresh link (`POST /api/payments/{id}/session`) and cancel themselves — stock
restored — when their pay window expires. Cancelling a paid card order refunds
the card through Stripe (the total less any late-cancellation fee); if the refund
call fails, the timeline says so instead of pretending it happened. A refund
issued by hand in the Stripe dashboard syncs back through the
`charge.refunded` webhook, so the book never claims money it has already
returned, and a `charge.dispute.created` event rings the desk the moment a
chargeback opens. Set
`STRIPE_SECRET_KEY` to turn the card option on (checkout offers cash only without
it); `STRIPE_WEBHOOK_SECRET` verifies the webhook, `STRIPE_CURRENCY` (EUR
only) sets the charge currency, and `STRIPE_PAYMENT_METHODS` can add local
rails (Klarna, PayPal, SEPA direct debit, iDEAL, Bancontact) to the hosted
page. Delivery and tax are calculated server-side from the approved money
rules — while the EUR delivery figures await the owner's approval, checkout
pauses fail-closed rather than charge an unapproved figure.

**Order emails that follow the box** — every move the desk dials (confirmed,
baking, ready, out for delivery, delivered, cancelled) lands in the
customer's mailbox in the same breath, the baker's own note riding along.
Customers cancelling their own order get the confirmation email too, and an
order that dies because its payment window closed gets an honest word about
where it went.

**Photo uploads** — admins add or replace a product's photo straight from the
product form (file picker or phone camera): the browser shrinks it locally
(1200px JPEG) and posts it to `POST /api/admin/uploads/product-photo`, which
validates the type and size (JPEG/PNG/WebP, ≤ 6 MB) and stores it under
`static/uploads/products/` with a server-generated name. Customers can set a
profile photo the same way (512px, ≤ 2 MB) — it shows up in the navbar, on the
profile page and in the admin user list, and can be removed with one click.

**Accounts** — email login with JWT access/refresh tokens (silent refresh in the
frontend), profile & password management, an address book with default addresses.
Two more ways in, each appearing only when it's configured (never a dead button):
**"Continue with Facebook"** (Facebook Login for the web — set `FACEBOOK_APP_ID` /
`FACEBOOK_APP_SECRET`), and **phone sign-in** — a six-digit code texted to the
number on the account (`TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` /
`TWILIO_FROM_NUMBER`). Phone codes are single-use, valid 10 minutes, capped at
five wrong tries, and rate-limited; Facebook handshakes never put tokens in a
URL — the callback issues a one-time code the page exchanges server-side.
Orders keep a snapshot of the delivery address, so editing addresses later never
changes past orders. Customers can cancel while an order is pending/confirmed/
baking — stock is restored, paid card orders auto-refund through Stripe, and cancelling after the
ovens start keeps a late-cancellation fee the admin tunes under Settings
(refunds pay back the total minus the fee; 0% turns it off). Customers can
reorder a past box with one click. Under Profile they also hold the keys to
their own data (GDPR arts. 15/17): one click downloads everything the shop
keeps on them as JSON, and a password-confirmed deletion scrubs the person
— name, contact details, hearts, reviews, restock asks — while past orders
remain only as anonymized invoices, as bookkeeping law requires.

**Baker's desk (admin)** — the whole desk sits in one sidebar (Dashboard,
Orders, Products, Reviews, Promotions, Users, Settings) alongside the baker's
own profile and address book, so nothing needs a second click to reach; on a
phone the sidebar flattens into a thumb-swipe rail of the same tabs, and the
Store link leads back to the counter. Dashboard with
revenue/orders/customers stats, a thirty-day trading chart (one bar per
day, active currency, kept orders), best sellers, low stock and recent
orders;
order management with a status timeline
(pending → confirmed → baking → ready → out for delivery → delivered), payment
controls and a CSV export for bookkeeping, plus a Bulk enquiries tab for the
catering pipeline (fresh leads ring the navbar bell, which deep-links
straight to them); the reviews desk hides and restores customer reviews, with
the wall's average always honest; full product CRUD with
photo uploads from your own PC or phone, and shelves aren't a fixed list — the
product form offers "+ New shelf…" so a category can be named on the spot (it
lands in the navbar, the shop sidebar and the home tiles straight away);
coupon management on a redesigned Promotions desk (summary strip with live
counts and honest redemptions, search and status filters, and a drawer
editor for create/edit that floats over the page so nothing beneath it
ever jumps);
user management as the regulars' roll — measured loyalty tiers, tier chips,
search, sorting and shelves of ten, with a gift button per VIP/loyal regular
that copies the coupon code their tier earns; newsletter subscribers with CSV
export; and Settings, ordered the way the day runs — the home page photo on
top (swappable from the desk, resettable to the shipped one), the storefront
copy (announcement bar and the home page's "today" note; clearing a line hides
it), managers for the home page's coming-soon band and testimonials (add,
edit, re-order, shelve, photos included), and the cancellation policy at the
very bottom. Every desk ping — a new order, a new customer (Facebook sign-ups
included), a bulk enquiry, a review — lands in the notification bell *and*,
once SMTP is configured, in every active admin's inbox the same moment: the
email is queued as a background task, so a slow mail server never slows the
shop, and a failed send only logs.

## Project layout

```
backend/
  app/
    __init__.py      app factory, lifespan (create tables + seed), CORS, gzip,
                     security headers, /static mount, SPA catch-all
    config.py        settings from .env (pydantic-settings)
    database.py      engine/session, Base
    models.py        User, Address, Category, Product, Review, Coupon,
                     Favorite, Subscriber, Order, OrderItem, OrderEvent,
                     StoreSetting, BulkEnquiry, Testimonial, ComingSoonItem
                     (admin-tuned shop rules, storefront copy & home bands)
    schemas.py       Pydantic request/response models
    security.py      bcrypt hashing, JWT create/decode, auth dependencies,
                     client-key extraction + sliding-window rate limiters
    serialize.py     shared model → schema helpers
    seed.py          idempotent demo data (restores what the storefront promises)
    uploads.py       validated photo storage (type/size checks, safe deletes)
    routers/         auth (rate-limited, +avatar), products (+favorites),
                     orders, admin (+photo uploads), marketing (newsletter,
                     +bulk orders & voucher board), content (testimonials,
                     coming-soon)
  static/            product & site photos (generated, see scripts/);
                     uploads/ holds admin- and customer-added photos
  scripts/           reset_customer_order_data.py — the guarded customer/order
                     reset (dry-run by default; see docs/operations/)
  tests/             205 backend tests: 124 end-to-end API (test_api.py),
                     36 costing, 24 reset-script, 21 on the Stripe gateway
                     seam (signed webhooks, plain-dict handback, payment
                     methods)
docs/
  business/          the pricing/costing specification set (v1.2 is live)
                     and the Phase 0/2 audit + delivery records
  operations/        EUR-migration runbook and the production-reset procedure
frontend/
  src/
    api/client.js    axios + token attach + silent refresh on 401
    context/         Auth, Cart, Favorites, Toast providers
    lib/             format helpers, imageResize (client-side photo shrinking),
                     coupon handoff (basket → checkout)
    components/      Navbar, Footer, ProductCard, PhotoTile, guards, small UI bits
    pages/           Home, Products, ProductDetail, Cart, Checkout, BulkOrder,
                     auth, account/*, admin/*
scripts/
  prepare_images.py  resizes/crops originals from images/ into backend/static
images/              original photography (source of truth for the pipeline)
```

## Configuration (`backend/.env`)

| Variable                 | Default                          | Notes                       |
| ------------------------ | -------------------------------- | --------------------------- |
| `APP_ENV`                | `dev`                            | `production` guards insecure defaults |
| `DATABASE_URL`           | `sqlite:///./bakehouse.db`       | any SQLAlchemy URL; `postgres://` is mapped to psycopg3 automatically |
| `SECRET_KEY`             | dev fallback                     | set your own in production  |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | 30 / 30 | |
| `TAX_RATE` / `SHIPPING_FEE` / `FREE_SHIPPING_THRESHOLD` | unset (fail-closed) | the approved EUR figures — `0.07` / `2.99` / `25.00` (2026-11-06); while either delivery figure is unset, checkout pauses itself rather than charge an unapproved amount |
| `VIP_FREE_DELIVERY_THRESHOLD` | unset | VIP regulars earn free delivery from this smaller basket (owner-approved €15); unset = everyone shares the standard line |
| `QUOTE_RATE_LIMIT`           | 30 per minute per IP | the public basket-price endpoint's brake |
| `CORS_ORIGINS`           | localhost:5173                   | comma-separated list        |
| `SERVE_FRONTEND_DIST`    | `true`                           | serve `frontend/dist` from FastAPI |
| `ENABLE_DOCS`            | `true`                           | Swagger UI at `/api/docs`   |
| `LOGIN_RATE_LIMIT` / `REGISTER_RATE_LIMIT` | 10 / 5 per minute per IP | brute-force brakes |
| `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` | empty | "Continue with Facebook" — the button appears once both are set |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | empty | phone sign-in codes over SMS; all three together switch it on |
| `STRIPE_PAYMENT_METHODS` | `card` | comma list of hosted-page methods (`klarna`, `paypal`, `sepa_debit`, `ideal`, `bancontact` alongside `card`; unknown entries drop with a warning) |
| `SEED_DEMO_DATA`         | unset (dev yes, production no)   | demo customers/orders/reviews; production refuses `true` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | empty                     | first admin on a fresh no-demo database (required then) |
| `UPLOADS_DIR`            | empty (`static/uploads`)         | absolute path to a persistent disk on ephemeral platforms |

## Going to production

**What a fresh production database gets.** The catalog always seeds — 3
shelves, 25 products with their photos, and the two vouchers the storefront
copy promises. Everything demo does not: no demo customers or logins, no order
history, no seeded reviews, testimonials or coming-soon band. Instead the
seeder creates **one** admin account from `ADMIN_EMAIL` / `ADMIN_PASSWORD`,
and refuses to start without them — just as it refuses a default `SECRET_KEY`,
a `SEED_DEMO_DATA=true` in production, or leftover dev settings. The login
page's demo quick-fill buttons detect this and hide themselves (`demo_accounts`
on `GET /api/store`). Attach a persistent disk and set `UPLOADS_DIR` so
admin-uploaded photos survive redeploys.

**Option A — Render (recommended, one click).** The repo ships a blueprint
(`render.yaml`): web service + Postgres + a 1 GB uploads disk, health check on
`/api/health`, `SECRET_KEY` generated, docs disabled, frontend built and served
from the same origin.

1. Push this repo to GitHub (see below), then in Render: **New → Blueprint**,
   pick the repo.
2. Render asks for the two values marked `sync: false` — `ADMIN_EMAIL` and
   `ADMIN_PASSWORD`. That's the baker's login; use the real email.
3. Deploy. First boot creates the database, seeds the catalog and the admin.
   Point your domain at the service (Settings → Custom Domains); TLS is
   automatic.
4. Log in, set the storefront copy under Settings, re-enable the vouchers you
   want live under Promotions, and open the shop.

**Option B — Railway (manual, same shape).** Create a project, add the
GitHub repo and a **PostgreSQL** plugin, then set:

- Build command: `pip install -r backend/requirements.txt && cd frontend && npm ci && npm run build`
- Start command: `uvicorn app:app --app-dir backend --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`
- Variables: `APP_ENV=production`, `DATABASE_URL` = the plugin's connection
  string, a generated `SECRET_KEY`, `SEED_DEMO_DATA=false`, `ADMIN_EMAIL`,
  `ADMIN_PASSWORD`, `ENABLE_DOCS=false`. If you attach a volume, mount it
  (e.g. at `/data`) and set `UPLOADS_DIR=/data/uploads`.

**Option C — your own server (VPS, Docker, nginx).** Single process, no build
step at runtime:

```bash
cd backend
pip install -r requirements.txt
cd ../frontend && npm ci && npm run build && cd ..
APP_ENV=production SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
DATABASE_URL=postgresql://user:pass@localhost/bakehouse \
SEED_DEMO_DATA=false ADMIN_EMAIL=you@yourdomain.com ADMIN_PASSWORD=strong-pass \
UPLOADS_DIR=/var/lib/bakehouse/uploads \
uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="127.0.0.1"
```

Put nginx (or Caddy) in front for TLS, proxy to :8000, and point its health
check at `/api/health` — it returns 503 the moment the database is unreachable,
so a bad deploy stops receiving traffic. Deep links like
`/products/butter-croissant` work; unknown paths fall back to the SPA, and
path traversal is blocked.

**Operating notes**

- Schema additions after the first release are applied automatically on
  startup (see `_upgrade_schema` in `app/__init__.py`) — no migration tool to
  run by hand.
- Watch the logs: every request gets an `X-Request-ID` (returned to the client
  and logged with method, path, status and duration); unhandled errors log the
  full traceback but never leak it to the client.
- Rate limiting is in-process — fine for one worker; use a shared store
  (e.g. Redis) if you scale horizontally. Password changes revoke every
  outstanding token for that account, on every device.
- Backups: Render/Railway snapshot the managed Postgres for you; on your own
  box, `pg_dump` on a cron is enough at this scale.
- Resetting the shop is deleting the database and rebooting — first boot
  re-seeds the catalog and asks for `ADMIN_EMAIL`/`ADMIN_PASSWORD` again.

**Version control.** The project is a plain git repo (first commit made at
hand-off). Keep it that way on GitHub/GitLab — the PaaS deploys track the
`main` branch, and `backend/.env`, databases, `node_modules`, `dist/` and
uploaded photos stay out of history via `.gitignore`.

**Turning on VAT-inclusive pricing (the German B2C mode)**

The default pricing mode is `TAX_MODE=exclusive`: net shelf prices, the
approved 7 % register rate added at the till. The inclusive mode — shelf
prices *are* gross, VAT extracted per line, delivery VAT allocated across
the basket's rates — is fully implemented and tested but ships dark,
because it refuses any purchasable product without an approved VAT
classification (fail-closed, per product, by design). To open it:

1. Classify every purchasable product at the desk (Products → the
   tax-review rail; each change is audit-logged) — the classification is
   a legal decision the app never makes for you.
2. Review `GET /api/admin/vat/report` (admin-only): it counts the
   catalog, names every blocker, and says `checkout_ready_for_inclusive`.
3. Set `TAX_MODE=inclusive` (Render env var). Quotes, checkout, Stripe's
   hosted page (no separate tax row — VAT rides inside the lines), the
   receipt email ("VAT (included)") and the order book (per-line and
   per-rate snapshots, immutable) all switch together through the one
   shared pricing service. Existing orders are never recalculated.

**Turning on online payments (Stripe)**

The integration is live in code; it just needs keys. Rehearse in test mode
first, then swap in live keys — the shop runs on cash on delivery until the
secret key exists, so nothing breaks in the meantime.

1. Create a Stripe account (standard, not Connect) and confirm the
   business profile — payouts need it.
2. In Render (or your `.env`), set `STRIPE_SECRET_KEY` to the **test**
   secret key (`sk_test_…`, Developers → API keys). Card checkout appears
   the moment it's set.
3. In the Stripe dashboard (Developers → Webhooks → Add endpoint), point an
   endpoint at `https://<your-domain>/api/payments/webhook` with the events
   `checkout.session.completed`, `checkout.session.expired`,
   `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`,
   `checkout.session.async_payment_expired`,
   `charge.refunded`, `charge.dispute.created`. Copy its signing secret
   (`whsec_…`) into `STRIPE_WEBHOOK_SECRET`. (The two charge events keep
   the book honest when money moves from the Stripe dashboard itself: a
   by-hand refund syncs the order's payment status, and a dispute rings
   the desk the moment it opens. `async_payment_failed` returns the reserved
   bakes to the shelf the moment a delayed payment fails, rather than
   waiting for the session's expiry.)
4. Buy something with a Stripe test card (`4242 4242 4242 4242`, any future
   expiry, any CVC): the order should flip to paid and email the receipt;
   abandoning the page instead should leave it payable from the order page
   and auto-cancel it after 30 minutes.
5. Happy? Switch both Render env vars to the **live** keys (`sk_live_…` and
   the live webhook endpoint's secret) — and keep cash on delivery on too;
   most German customers still like it.

Local payment methods (Klarna, PayPal, SEPA direct debit, iDEAL,
Bancontact) can ride along on the hosted page: set `STRIPE_PAYMENT_METHODS`
to a comma list (default `card`). Availability is Stripe's to decide per
country — see their payment-methods docs before enabling one.

Notes: charges go out in `STRIPE_CURRENCY` (EUR only — any other value
pauses checkout fail-closed); refunds on cancellation are issued
through the gateway automatically; every gateway call lives behind
`backend/app/stripe_gateway.py` so the rest of the app never imports the
Stripe SDK — and everything crossing that seam is a plain dict (the SDK's
Event/Session objects refuse `.get()` by design, which once turned every
live webhook delivery into a 500 while the dict-returning test fake kept
the suite green; `tests/test_stripe_gateway.py` now pins signed payloads
through the real verification).

**Turning on Facebook and phone sign-in**

Both rails are live in code and appear the moment their keys exist — never
before, so no customer ever sees a button that can't work.

- *Facebook*: create an app at developers.facebook.com, add the Facebook
  Login product, and under its settings put
  `https://<your-domain>/api/auth/facebook/callback` in **Valid OAuth
  Redirect URIs** (add `http://localhost:8000/api/auth/facebook/callback`
  for local testing). Copy the App ID and App secret into
  `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET`. Accounts are matched by
  Facebook id, then by email — an existing customer who clicks it keeps
  their whole order history, and the Facebook button never puts tokens in
  a URL (the callback issues a one-time code the page exchanges).
- *Phone*: sign up at twilio.com, buy or verify a sender number, and set
  `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER`.
  Customers then sign in with a six-digit code texted to the number on
  their account (set at registration or in the profile). Codes are
  single-use, good for ten minutes, capped at five wrong tries, and one
  text per number per minute. In dev, with no Twilio keys, the code comes
  back with the API response so the whole flow stays testable.

**Still to wire up for the real market**

- Stripe live keys — rehearse with the test-mode walkthrough above first.
- Facebook app + Twilio number, if you want those sign-in rails live.
- To refresh photography: drop new shots into `images/` and re-run
  `python scripts/prepare_images.py` (requires Pillow).
