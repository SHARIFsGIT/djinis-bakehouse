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

**Finding your way** — the search box answers while you type: the first five
matches with photo, shelf and price, walked with the arrow keys, and `/`
puts the cursor in it from anywhere. A bake's page opens its photo large,
shares through the phone's own share sheet (the link is copied where there
is none) and offers the bakes this browser opened last ("Recently viewed" —
kept in localStorage, never sent anywhere). A wrong address lands on a page
with the search, the shelves and those same ways back. `/contact` ("Find
us") shows the address with a map link, phone, email, bake hours and the
delivery area, all from Settings — the same details feed the home page's
schema.org `Bakery` data, and the page is in the sitemap. Every page names
its browser tab (`lib/titles.js`). An open order page looks again every 45
seconds and whenever its tab comes back into view, so the timeline moves on
without a reload.

**On a phone** — the catalog opens on bakes: shelves are a row to swipe and
the filters rise from the bottom edge as a sheet. On a bake's page and in
the basket, the page's one button ("Add to basket", "Checkout") waits at
the bottom edge whenever the real one is off screen (`hooks/useDock.js`).
Every field is 16px on a touch screen so iOS never zooms into one, the
smallest controls grow to 32px, hover styles are kept for pointers that
hover, and the layout keeps clear of the notch and the home indicator. A
web manifest and icons let the shop be added to a home screen; there is
deliberately no service worker, so nobody is ever served a stale shop. The
desk's tables (orders, enquiries, products, customers, promotions, the
dashboard) fold down to what a phone can show, with every row's actions on
the screen. All of it is laid out down to a 320px-wide screen: the header
keeps its mark and drops the name beside it only when the name would not
fit, the home page's two buttons stack, and nothing has to be scrolled
sideways. Dialogs are drawn at the end of the page body (a portal), so one
opened from inside a frosted card or a drawer still covers the whole screen.

**Readable by everyone** — a skip link is the first Tab on every page, focus
is always visible, icon buttons carry names, the search is a proper
combobox, sheets and dialogs take the keyboard in and hand it back, and
`prefers-reduced-motion` is honoured. Both themes are tuned against WCAG
AA: on the two paper tones, the ink at every strength the pages dim text
to, each label on its own fill and each semantic colour used as text
compute to 4.5:1 or better. At night the wine has two seats for that
reason — `--color-secondary` is the one read as text, `--brand-wine` the
deeper one under cream text (`index.css`). The rating stars keep the honey
on cream; the number is always beside them.

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
rate-limited, idempotent — re-subscribing says so instead of erroring). The
baker works the list at the foot of the Marketing desk: the count, every
address with when and where it signed up, a remove button for "please take me
off", and a one-click CSV export for the mailing tool of their choice. It is
its own list — campaigns never draw on it; they go only to customers whose
consent is on record.

**Where the van goes** — Settings → Delivery area takes the postal codes the
shop delivers to (`10115`, or `101*` for a whole area). Checkout tells the
customer as soon as the postal code is typed, and the server refuses an
order to an address outside the list before any stock is reserved. Left
empty there is no limit, exactly as before.

**Legal notice** — Settings → Legal notice holds the shop's "Impressum" in
the owner's own words. Once it has text it is served at `/imprint` (and
`/impressum`), linked from the footer of every page and listed in the
sitemap; until then there is no page and no link — the software never
invents one.

**A basket that mends itself** — when a line can no longer be honoured (the
bake sold out, came off the menu, or fewer are left than asked for) the
server names the line and what is left of it, and the basket offers the fix
on the spot: take what is left, or remove it. A shopper who loses the race
for the last ones is told what is on the shelf *now*.

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
Payment is cash on delivery (settled by staff when the order is marked
delivered) plus two online rails, each appearing at checkout only once its
keys exist — never a dead button:

- **Stripe's hosted Checkout** (`STRIPE_SECRET_KEY`) — cards and wallets,
  and whatever else the Stripe account has switched on: the shop asks for
  Stripe's automatic method set, so the dashboard decides what customers
  see (`STRIPE_PAYMENT_METHODS` can only narrow it). Card details never
  touch this server and 3-D Secure/SCA is handled on Stripe's page. The
  page mirrors the shop — one line per product with its photo, delivery and
  tax rows, the coupon as a real discount row, the bakery's palette
  (`app/stripe_gateway.py` builds the session; the lines always sum to the
  stored order total in integer cents).
- **PayPal Checkout** (`PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`) — its
  own option beside Stripe, the Orders v2 way with nothing of PayPal's
  running in the shop's pages: the server creates the order, the customer
  approves it on PayPal's site, the server captures it. PayPal never takes
  money the shop did not ask it to capture, so a cancelled order is simply
  never charged (`app/paypal_gateway.py`, standard library only).

Either way the order is placed unpaid and **turns paid only on the
provider's word** — its signed webhook, the shop asking it when the customer
returns, or the reconciliation sweep (every 15 minutes) for orders nobody
spoke for. All three end in one idempotent, row-locked transition, so they
can arrive in any order, or twice, and the order is marked paid and the
receipt sent exactly once. One press of the pay button is one order
(`Idempotency-Key`), a webhook delivered twice acts once, and an unpaid
order can be paid again from its page and cancels itself — stock restored —
when its window closes.

Behind both sits **one payment ledger** (`payment_transactions`,
`payment_refunds`, `payment_events`): a row per pay page with the
provider's own ids, the amount, the method actually used (read back from
the provider, so a receipt says "Klarna" rather than guessing "card"), its
state — pending, processing, succeeded, failed, cancelled, expired,
partially refunded, refunded, disputed — and an audit line for every
change. **Refunds are the provider's doing**: the desk's Refund button (or a
cancellation) asks Stripe or PayPal to send the money, and the order reads
"refunded" only once they confirm; a refused refund stays on record as
failed. Refunds and disputes made in a provider's own dashboard flow back
through its webhooks, and the desk's **Payment check** lists whatever does
not add up. Delivery and tax are calculated server-side from the approved
money rules — while the EUR delivery figures await the owner's approval,
checkout pauses fail-closed rather than charge an unapproved figure.
Going live is a procedure: `docs/operations/PAYMENTS_GO_LIVE.md`.

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

**Accounts** — one field takes **an email address or a phone number**, at
sign-up and at log-in, with a password; JWT access/refresh tokens (silent
refresh in the frontend), profile & password management, an address book with
default addresses. A number is one account however it is typed — `0176 555 0100`,
`+49 176 555 0100` and `0049 176…` are the same (a number written the national
way is read as the shop's own country, `PHONE_DEFAULT_COUNTRY_CODE`). An
account opened with a phone number alone simply has no email: nothing is
mailed to it (its orders live on its order page), the customer can add an
address in their profile at any time, and — since a reset link cannot be
mailed — the desk can make one on the customer's card and pass it on.
Two more ways in, each appearing only when it's configured (never a dead button):
**"Continue with Facebook"** (Facebook Login for the web — set `FACEBOOK_APP_ID` /
`FACEBOOK_APP_SECRET`), and a **texted sign-in code** — six digits sent to the
number on the account, no password needed (`TWILIO_ACCOUNT_SID` /
`TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER`). Codes are single-use, valid 10
minutes, capped at five wrong tries, and rate-limited; Facebook handshakes never
put tokens in a URL — the callback issues a one-time code the page exchanges
server-side.
Orders keep a snapshot of the delivery address, so editing addresses later never
changes past orders. Customers can cancel while an order is pending/confirmed/
baking — stock is restored, orders paid online auto-refund through the provider that took the money, and cancelling after the
ovens start keeps a late-cancellation fee the admin tunes under Settings
(refunds pay back the total minus the fee; 0% turns it off). Customers can
reorder a past box with one click. Under Profile they also hold the key to
their own data (GDPR art. 17): a password-confirmed deletion scrubs the
person — name, contact details, hearts, reviews, restock asks — while past
orders remain only as anonymized invoices, as bookkeeping law requires. (The
shop's only admin cannot close their own account — the desk would be left
without a key.) For an access request (art. 15) the API serves everything
the shop keeps on the signed-in customer as JSON at
`GET /api/auth/me/export`; the storefront carries no button for it.

**Baker's desk (admin)** — the whole desk sits in one sidebar (Dashboard,
Orders, Products, Reviews, Promotions, Users, Settings) alongside the baker's
own profile and address book, so nothing needs a second click to reach; on a
phone the sidebar flattens into a thumb-swipe rail of the same tabs, and the
Store link leads back to the counter. The dashboard opens on the day, in the
order a shift asks its questions: **what is waiting for you** (orders to
confirm, catering enquiries to answer, bakes running low — each row opens the
desk that deals with it), **what is due next** (open orders with a delivery
date, soonest first, overdue ones leading), then **the last 30 days** — order
value, orders, average order and new customers, each set against the 30 days
before — over a thirty-day chart (one column per day, active currency, kept
orders; readable by pointer, finger, arrow keys or as a table), and the
latest orders and best sellers;
order management with a status timeline
(pending → confirmed → baking → ready → out for delivery → delivered), each
online order's payment panel (the provider's ids, what is held, full or
partial refunds through Stripe or PayPal, "check with the provider" and the
audit trail), a Payment check tab that names what does not reconcile,
a CSV export for bookkeeping, plus a Bulk enquiries tab for the
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
that copies the coupon code their tier earns; the Marketing desk (consent,
campaigns, personal vouchers, the suppression list and the newsletter
sign-ups with CSV export); and Settings, ordered the way the day runs — the
home page photo on top (swappable from the desk, resettable to the shipped
one), the storefront copy (announcement bar and the home page's "today" note;
clearing a line hides it), managers for the home page's coming-soon band and
testimonials (add, edit, re-order, shelve, photos included), the cancellation
policy, contact details and hours, the delivery area and the legal notice. Every desk ping — a new order, a new customer (Facebook sign-ups
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
                     (per address and per account), the shop's public base URL
    shoptime.py      the bakery's own clock (SHOP_TIMEZONE): "today", and the
                     times printed on receipts, emails and exports
    delivery.py      the delivery area — postal codes and `101*` areas
    stripe_gateway.py / paypal_gateway.py
                     the two provider seams — every call to Stripe or PayPal
                     goes through one of them, and tests swap them for fakes
    ledger.py        the payment ledger's own rules (records; never calls a
                     provider)
    payment_ops.py   what a payment needs from either provider: opening a pay
                     page, issuing a refund and recording the answer
    serialize.py     shared model → schema helpers
    seed.py          idempotent demo data (restores what the storefront promises)
    uploads.py       validated photo storage (type/size checks, safe deletes)
    routers/         auth (rate-limited, +avatar), products (+favorites),
                     orders, payments (provider webhooks, confirm, pay-again,
                     the reconciliation sweep), admin (+photo uploads),
                     admin_payments (payment history, refunds, provider
                     check, reconciliation report), marketing (newsletter,
                     +bulk orders & voucher board), content (testimonials,
                     coming-soon)
  static/            product & site photos (generated, see scripts/);
                     uploads/ holds admin- and customer-added photos
  scripts/           reset_customer_order_data.py — the guarded customer/order
                     reset (dry-run by default; see docs/operations/);
                     check_payments.py — the read-only payment readiness
                     check (rehearsals on test keys / sandbox only)
  tests/             the backend suite (`pytest tests/`): end-to-end API
                     (test_api.py), payment integrity and hardening, the
                     ledger/refund/reconciliation flows, PayPal through a
                     fake gateway, both real gateways against a stubbed
                     SDK / stubbed wire, costing, and the two ops scripts
docs/
  business/          the pricing/costing specification set (v1.2 is live)
                     and the Phase 0/2 audit + delivery records
  operations/        EUR-migration runbook, the production-reset procedure
                     and PAYMENTS_GO_LIVE.md (rehearsal and live switch)
frontend/
  src/
    api/client.js    axios + token attach + silent refresh on 401, request
                     time limits, a session that survives blocked storage
    context/         Auth, Cart, Favorites, Toast providers (sign-in and the
                     basket follow across browser tabs)
    hooks/           useStore, useBasketQuote, useDock (the phone's
                     bottom-edge bar)
    lib/             format helpers, imageResize (client-side photo shrinking),
                     coupon handoff (basket → checkout), store (the one shared
                     fetch of /api/store), apiError (failed requests in a
                     customer's words), delivery (the area check), navigation
                     (the safe way back after login), titles (what each page
                     calls its browser tab), recent (recently viewed bakes)
    components/      Navbar, Footer, ProductCard, PhotoTile, SearchBox, Sheet,
                     Lightbox, DockBar, Pager, guards, small UI bits
    pages/           Home, Products, ProductDetail, Cart, Checkout, BulkOrder,
                     Contact, auth, account/*, admin/*
  public/            manifest.json, and the home-screen icons that
                     scripts/prepare_images.py cuts from the shop mark
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
| `ORDER_RATE_LIMIT` / `REVIEW_RATE_LIMIT` | 12 / 10 per hour per account | a signed-in account's own brakes — every order reserves stock and rings the desk, every review emails the admins |
| `PAYMENT_ACTION_RATE_LIMIT` | 30 per minute per account | opening a pay page / checking a payment — each is a call to Stripe or PayPal |
| `SHOP_TIMEZONE` | `Europe/Berlin` | the bakery's clock: what "today" is for order numbers and delivery dates, and the time on receipts, emails and exports (an unknown name falls back to UTC with an error in the log) |
| `CLIENT_IP_HEADER`       | empty (first `X-Forwarded-For` hop) | the header your edge sets with the visitor's real address (e.g. `CF-Connecting-IP`) — only ever one the edge overwrites itself; see `.env.example` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | empty / 587 | the one mail account behind receipts, order updates, password resets, restock notes, desk alerts and campaigns. **Empty host = nothing is sent** (dev logs each message; production logs recipient + subject only and says "Email is OFF" at boot) |
| `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` | empty | "Continue with Facebook" — the button appears once both are set |
| `PHONE_DEFAULT_COUNTRY_CODE` | `49` | the country a phone number written the national way belongs to ("0176…" is read as "+49 176…"); digits only, no "+" |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | empty | texted sign-in codes; all three together switch them on (logging in with a phone number and a password needs none of them) |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | empty | the card option appears once the key is set (`sk_test_…` rehearses, `sk_live_…` charges); the signing secret of the endpoint delivering to `/api/payments/webhook` — without it Stripe's webhooks are refused |
| `STRIPE_PAYMENT_METHODS` | `automatic` | Stripe offers what its dashboard has switched on; a comma list (`card`, `link`, `klarna`, `sepa_debit`, `ideal`, `bancontact`, `eps`, `p24`, `revolut_pay`, `amazon_pay`, `paypal`) only narrows that set; unknown entries drop with a warning |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | empty | PayPal as its own checkout option — appears once both are set |
| `PAYPAL_ENVIRONMENT` | `sandbox` | `sandbox` or `live` — which PayPal the credentials belong to (any other word keeps PayPal off) |
| `PAYPAL_WEBHOOK_ID` | empty | id of the PayPal webhook delivering to `/api/payments/paypal/webhook`; without it PayPal's webhooks are refused |
| `PUBLIC_BASE_URL` | empty | the shop's one public address — every link the shop hands out is built on it (password-reset and unsubscribe links, back-in-stock links, the sitemap, the return from a pay page). Empty = the address each request arrived on. **Set it in production** (`https://your-domain`, no trailing slash), provided the shop answers on that one address only |
| `SEED_DEMO_DATA`         | unset (dev yes, production no)   | demo customers/orders/reviews; production refuses `true` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | empty                     | first admin on a fresh no-demo database (required then) |
| `UPLOADS_DIR`            | empty (`static/uploads`)         | absolute path to a persistent disk on ephemeral platforms |

## Going to production

**What is still open for the owner** — the live shop's unfinished business
(payments never rehearsed, the legal notice, email, the optional sign-in
and payment rails), each with its fix: [`PENDING.md`](PENDING.md).

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
  outstanding token for that account, on every device. The per-address
  brakes are only as honest as the address: set `CLIENT_IP_HEADER` once you
  have confirmed which header your edge sets itself. Phone sign-in codes
  carry their own ceiling per number (five an hour), whoever asks.
- The app shell is served with a Content-Security-Policy: scripts only
  from the shop's own origin, plus the exact inline snippet the build
  carries (admitted by hash, read from `frontend/dist/index.html` at
  boot). An inline `<script>` added to `index.html` is picked up
  automatically; a third-party script host would have to be added to the
  policy in `app/__init__.py` on purpose.
- Email is off until `SMTP_HOST` is set — the boot log says so in
  production. Nothing else in the shop depends on it, but receipts, order
  updates and password resets go nowhere without it.
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

**Turning on online payments (Stripe and PayPal)**

Both rails are live in code and appear the moment their keys exist; until
then the shop runs on cash on delivery, so nothing breaks in the meantime.
The full procedure — backup, deploy, rehearsal on test keys and in the
PayPal sandbox, the live switch, rollback, and the questions that are the
owner's to decide — is **`docs/operations/PAYMENTS_GO_LIVE.md`**. In short:

1. *Stripe*: set `STRIPE_SECRET_KEY` (test key first). In the Stripe
   dashboard point a webhook endpoint at
   `https://<your-domain>/api/payments/webhook` with the events
   `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `checkout.session.expired`,
   `charge.refunded`, `refund.updated`, `charge.dispute.created`,
   `charge.dispute.closed`, and copy its signing secret into
   `STRIPE_WEBHOOK_SECRET`. Which methods the page offers is decided in the
   dashboard (Settings → Payment methods).
2. *PayPal*: create a REST app at developer.paypal.com, set
   `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_ENVIRONMENT`
   (`sandbox` first), add a webhook to
   `https://<your-domain>/api/payments/paypal/webhook` with the events
   listed in the runbook, and put its id in `PAYPAL_WEBHOOK_ID`.
3. Run `python backend/scripts/check_payments.py` — read-only; it asks both
   providers about the account and the webhook and says what, if anything,
   blocks online payments. `--rehearse` additionally builds a one-euro test
   pay page at each provider (test keys / sandbox only — with live
   credentials it refuses).
4. Rehearse on the site with a Stripe test card (`4242 4242 4242 4242`) and
   a PayPal sandbox buyer, refund from the desk, then switch to live keys.
   Keep cash on delivery on too.

Notes: charges go out in `STRIPE_CURRENCY` (EUR only — any other value
pauses checkout fail-closed). Every provider call lives behind
`backend/app/stripe_gateway.py` or `backend/app/paypal_gateway.py`, so the
rest of the app never imports the Stripe SDK or builds a PayPal URL — and
everything crossing those seams is a plain dict (the SDK's Event/Session
objects refuse `.get()` by design, which once turned every live webhook
delivery into a 500 while the dict-returning test fake kept the suite
green; `tests/test_stripe_gateway.py` pins signed payloads through the real
verification). The Stripe SDK is pinned to the 16.x line in
`requirements.txt`: each SDK major pins its own Stripe API version, and the
session this app builds is written for that one.

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
- *Phone*: signing up and logging in with a phone number and a password
  needs no setup — it is always on. The texted code is the extra: sign up at
  twilio.com, buy or verify a sender number, and set
  `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER`.
  Customers can then also sign in with a six-digit code texted to the number
  on their account (set at registration or in the profile). Codes are
  single-use, good for ten minutes, capped at five wrong tries, and one
  text per number per minute. In dev, with no Twilio keys, the code comes
  back with the API response so the whole flow stays testable.

**Opening for real customers**

`docs/operations/LAUNCH_CHECKLIST.md` is the whole list in order: which
accounts and keys the shop needs (and which it can open without), the
launch sequence with a check after every step, and the decisions that are
the owner's alone. In short:

- An SMTP account (`SMTP_*`) — until it is set the shop sends no email.
- `PUBLIC_BASE_URL` — the shop's one public address.
- Stripe and PayPal live credentials — follow
  `docs/operations/PAYMENTS_GO_LIVE.md`; rehearse first. Cash on delivery
  works without either.
- A legal notice, contact details and (if you deliver locally) a delivery
  area — all under Settings at the desk.
- Facebook app + Twilio number, only if you want those sign-in rails.
- To refresh photography: drop new shots into `images/` and re-run
  `python scripts/prepare_images.py` (requires Pillow).
