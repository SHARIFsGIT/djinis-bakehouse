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

**Sale nudges (honest ones)** — the basket keeps a free-delivery progress meter
against the $50 threshold, product cards flag "Only N left" while stock lasts,
and the basket suggests three best-rated one-tap add-ons ("Complete the box")
that aren't already in it. A voucher board sits under the basket's tally:
every live code with its saving, one tap to apply — and threshold vouchers
(`FRESHBAKE15`, 15% off boxes of $40+) show a "add $X more to unlock it —
worth $Y" meter that turns the gap into a goal instead of a rejection. The
chosen code rides along to checkout (re-validated against the live basket and
the threshold server-side) and quick-apply chips offer the other live codes
there too.

**Bulk & event orders** — a public `/bulk` page takes catering enquiries
(occasion, date from tomorrow onward, headcount up to 2,000, the spread they
have in mind), rate-limited and pre-filled for signed-in customers. The baker
works them from a second tab under Orders — new → contacted → quoted →
confirmed/declined, with her own notes on each lead and a CSV export for the
ledger.

**Saved bakes (wishlist)** — signed-in customers heart any bake from the grid or
product page; hearts sync to the server (`PUT/DELETE /api/products/{id}/favorite`,
idempotent) and surface in the navbar and under Account → Saved bakes. Guests are
invited to log in rather than losing the tap.

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
minimum is met; tax is recalculated on the discounted amount. The seeder
restores these codes on startup if they're ever deleted from the console,
because the storefront copy promises them.
Payment is cash on delivery, settled by staff when the order is marked delivered. (A
simulated instant-"paid" card branch exists for dev and demos — production refuses
it unless `CARD_PAYMENTS_ENABLED=true` is set; replace it with a real gateway such
as Stripe before ever turning that on.) Free delivery over $50, tax and shipping
calculated server-side.

**Photo uploads** — admins add or replace a product's photo straight from the
product form (file picker or phone camera): the browser shrinks it locally
(1200px JPEG) and posts it to `POST /api/admin/uploads/product-photo`, which
validates the type and size (JPEG/PNG/WebP, ≤ 6 MB) and stores it under
`static/uploads/products/` with a server-generated name. Customers can set a
profile photo the same way (512px, ≤ 2 MB) — it shows up in the navbar, on the
profile page and in the admin user list, and can be removed with one click.

**Accounts** — email login with JWT access/refresh tokens (silent refresh in the
frontend), profile & password management, an address book with default addresses.
Orders keep a snapshot of the delivery address, so editing addresses later never
changes past orders. Customers can cancel while an order is pending/confirmed/
baking — stock is restored, pre-paid orders auto-refund, and cancelling after the
ovens start keeps a late-cancellation fee the admin tunes under Settings
(refunds pay back the total minus the fee; 0% turns it off). Customers can
reorder a past box with one click.

**Baker's desk (admin)** — the whole desk sits in one sidebar (Dashboard,
Orders, Products, Reviews, Promotions, Users, Settings) alongside the baker's
own profile and address book, so nothing needs a second click to reach; on a
phone the sidebar flattens into a thumb-swipe rail of the same tabs, and the
Store link leads back to the counter. Dashboard with
revenue/orders/customers stats, best sellers, low stock and recent orders;
order management with a status timeline
(pending → confirmed → baking → ready → out for delivery → delivered), payment
controls and a CSV export for bookkeeping, plus a Bulk enquiries tab for the
catering pipeline (fresh leads ring the navbar bell, which deep-links
straight to them); the reviews desk hides and restores customer reviews, with
the wall's average always honest; full product CRUD with
photo uploads from your own PC or phone, and shelves aren't a fixed list — the
product form offers "+ New shelf…" so a category can be named on the spot (it
lands in the navbar, the shop sidebar and the home tiles straight away);
coupon management (create, pause, retune the percent and the minimum basket);
user management as the regulars' roll — measured loyalty tiers, tier chips,
search, sorting and shelves of ten, with a gift button per VIP/loyal regular
that copies the coupon code their tier earns; newsletter subscribers with CSV
export; and Settings, ordered the way the day runs — the home page photo on
top (swappable from the desk, resettable to the shipped one), the storefront
copy (announcement bar and the home page's "today" note; clearing a line hides
it), managers for the home page's coming-soon band and testimonials (add,
edit, re-order, shelve, photos included), and the cancellation policy at the
very bottom.

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
  tests/test_api.py  36 end-to-end API tests
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
| `TAX_RATE` / `SHIPPING_FEE` / `FREE_SHIPPING_THRESHOLD`     | 0.0875 / 5.99 / 50 |            |
| `CORS_ORIGINS`           | localhost:5173                   | comma-separated list        |
| `SERVE_FRONTEND_DIST`    | `true`                           | serve `frontend/dist` from FastAPI |
| `ENABLE_DOCS`            | `true`                           | Swagger UI at `/api/docs`   |
| `LOGIN_RATE_LIMIT` / `REGISTER_RATE_LIMIT` | 10 / 5 per minute per IP | brute-force brakes |
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

**Still to wire up for the real market**

- Real payments: checkout runs on cash on delivery; a Stripe intent call
  replaces the (dev-only, env-gated) simulated card branch when cards return.
- Transactional email: set `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`
  (plus `SMTP_FROM`) and receipts and password resets go out — the sending
  code is ready in `backend/app/email.py`.
- To refresh photography: drop new shots into `images/` and re-run
  `python scripts/prepare_images.py` (requires Pillow).
