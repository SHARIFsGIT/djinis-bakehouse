"""FastAPI application factory for Djini's Bakehouse."""

import asyncio
import base64
import hashlib
import logging
import re
import threading
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from . import bird_verify
from . import paypal_gateway, stripe_gateway
from .config import get_settings
from .database import Base, engine
from .routers import (
    admin,
    admin_payments,
    auth,
    content,
    marketing,
    orders,
    payments,
    products,
)
# Aliased on purpose: a plain `from .routers import costing` would rebind
# the app package's `costing` attribute to the router module, shadowing
# the app.costing engine module everywhere `from app import costing` runs.
from .routers import costing as costing_routes
from .security import DbSession

BACKEND_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger("bakery")

# What a client-supplied X-Request-ID must look like to be kept.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Columns added after the first release. create_all() cannot add them to an
# existing database, so additive changes are listed here and applied on start.
_ADDITIVE_COLUMNS = {
    "users": {
        "token_version": "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0",
        "avatar_url": "ALTER TABLE users ADD COLUMN avatar_url VARCHAR(255)",
        "reset_token_hash": "ALTER TABLE users ADD COLUMN reset_token_hash VARCHAR(64)",
        "reset_token_expires": "ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP",
        "phone_otp_hash": "ALTER TABLE users ADD COLUMN phone_otp_hash VARCHAR(64)",
        "phone_otp_expires": "ALTER TABLE users ADD COLUMN phone_otp_expires TIMESTAMP",
        "phone_otp_created_at": "ALTER TABLE users ADD COLUMN phone_otp_created_at TIMESTAMP",
        "phone_otp_attempts": "ALTER TABLE users ADD COLUMN phone_otp_attempts INTEGER NOT NULL DEFAULT 0",
        "facebook_id": "ALTER TABLE users ADD COLUMN facebook_id VARCHAR(64)",
        "login_code_hash": "ALTER TABLE users ADD COLUMN login_code_hash VARCHAR(64)",
        "login_code_expires": "ALTER TABLE users ADD COLUMN login_code_expires TIMESTAMP",
        # Manual customer classification + marketing permission (GDPR):
        # all nullable, all NULL on arrival — no customer is classified or
        # opted in by the migration itself.
        "classification": "ALTER TABLE users ADD COLUMN classification VARCHAR(12)",
        "marketing_consent_at": "ALTER TABLE users ADD COLUMN marketing_consent_at TIMESTAMP",
        "marketing_consent_source": "ALTER TABLE users ADD COLUMN marketing_consent_source VARCHAR(50)",
        "marketing_consent_evidence": "ALTER TABLE users ADD COLUMN marketing_consent_evidence TEXT",
        "marketing_token": "ALTER TABLE users ADD COLUMN marketing_token VARCHAR(64)",
        # Text sign-in: the number a Bird Verify code proved, and when. Both
        # nullable with no default — NULL on arrival, so no number already
        # on file is trusted until its owner confirms it.
        "phone_verified_number": "ALTER TABLE users ADD COLUMN phone_verified_number VARCHAR(30)",
        "phone_verified_at": "ALTER TABLE users ADD COLUMN phone_verified_at TIMESTAMP",
    },
    # A database that booted an earlier build of the payment ledger has the
    # table without this column; a fresh one gets it from create_all.
    "payment_transactions": {
        "method_type": "ALTER TABLE payment_transactions ADD COLUMN method_type VARCHAR(40)",
    },
    "orders": {
        "cancel_fee": "ALTER TABLE orders ADD COLUMN cancel_fee NUMERIC(10,2) NOT NULL DEFAULT 0",
        "stripe_session_id": "ALTER TABLE orders ADD COLUMN stripe_session_id VARCHAR(255)",
        "stripe_payment_intent_id": "ALTER TABLE orders ADD COLUMN stripe_payment_intent_id VARCHAR(255)",
        # A string default is safe on both engines (the boolean DEFAULT 1
        # lesson); EUR since the 2026-11-05 cutover. Databases that already
        # carry the column keep it as created — their historical rows stay
        # "usd" exactly as placed, never relabelled.
        "currency": "ALTER TABLE orders ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'eur'",
    },
    "products": {
        # The VAT foundation: classification stays NULL on existing rows —
        # no product is classified or approved automatically. Only the review
        # status carries the quoted string default (same lesson as currency).
        "vat_category": "ALTER TABLE products ADD COLUMN vat_category VARCHAR(20)",
        "vat_review_status": (
            "ALTER TABLE products ADD COLUMN vat_review_status "
            "VARCHAR(12) NOT NULL DEFAULT 'pending'"
        ),
        "milk_content_percent": "ALTER TABLE products ADD COLUMN milk_content_percent NUMERIC(5,2)",
    },
    "order_items": {
        # VAT snapshots, frozen at checkout once the shop prices
        # VAT-inclusive. Nullable on purpose: historical lines keep NULL
        # rather than a guessed tax figure.
        "vat_category": "ALTER TABLE order_items ADD COLUMN vat_category VARCHAR(20)",
        "vat_rate": "ALTER TABLE order_items ADD COLUMN vat_rate NUMERIC(5,2)",
        "net_unit_price": "ALTER TABLE order_items ADD COLUMN net_unit_price NUMERIC(10,2)",
        "gross_unit_price": "ALTER TABLE order_items ADD COLUMN gross_unit_price NUMERIC(10,2)",
        "net_line_total": "ALTER TABLE order_items ADD COLUMN net_line_total NUMERIC(10,2)",
        "vat_amount": "ALTER TABLE order_items ADD COLUMN vat_amount NUMERIC(10,2)",
        "gross_line_total": "ALTER TABLE order_items ADD COLUMN gross_line_total NUMERIC(10,2)",
    },
    "store_settings": {
        "announcement": "ALTER TABLE store_settings ADD COLUMN announcement VARCHAR(255)",
        "today_note": "ALTER TABLE store_settings ADD COLUMN today_note VARCHAR(255)",
        "hero_image": "ALTER TABLE store_settings ADD COLUMN hero_image VARCHAR(255)",
        "marquee_items": "ALTER TABLE store_settings ADD COLUMN marquee_items TEXT",
        "contact_address": "ALTER TABLE store_settings ADD COLUMN contact_address VARCHAR(255)",
        "contact_phone": "ALTER TABLE store_settings ADD COLUMN contact_phone VARCHAR(50)",
        "contact_email": "ALTER TABLE store_settings ADD COLUMN contact_email VARCHAR(255)",
        "opening_hours": "ALTER TABLE store_settings ADD COLUMN opening_hours TEXT",
        "brand_color": "ALTER TABLE store_settings ADD COLUMN brand_color VARCHAR(7)",
        # Both nullable, both NULL on arrival: no notice is published and no
        # delivery limit applies until the baker writes one.
        "legal_notice": "ALTER TABLE store_settings ADD COLUMN legal_notice TEXT",
        "delivery_postal_codes": "ALTER TABLE store_settings ADD COLUMN delivery_postal_codes TEXT",
    },
    "coupons": {
        "min_subtotal": "ALTER TABLE coupons ADD COLUMN min_subtotal NUMERIC(10,2)",
        # FALSE, not 0 — PostgreSQL refuses an integer default on a boolean.
        "once_per_customer": "ALTER TABLE coupons ADD COLUMN once_per_customer BOOLEAN NOT NULL DEFAULT FALSE",
        # FALSE by design: pre-cutover minimums stay unapproved (USD numerals
        # must not present themselves as euro thresholds) until an admin
        # re-saves the coupon from the Promotions desk.
        "min_subtotal_approved": "ALTER TABLE coupons ADD COLUMN min_subtotal_approved BOOLEAN NOT NULL DEFAULT FALSE",
        # Personalised-voucher columns (marketing desk): all nullable, NULL
        # on every existing shop-wide code. A plain INTEGER for the user
        # restriction — ALTER TABLE can't add a portable FK — the application
        # owns the reference on databases altered in place.
        "fixed_amount_off": "ALTER TABLE coupons ADD COLUMN fixed_amount_off NUMERIC(10,2)",
        "restricted_to_user_id": "ALTER TABLE coupons ADD COLUMN restricted_to_user_id INTEGER",
        "expires_at": "ALTER TABLE coupons ADD COLUMN expires_at TIMESTAMP",
        "max_redemptions": "ALTER TABLE coupons ADD COLUMN max_redemptions INTEGER",
        "created_by_admin_id": "ALTER TABLE coupons ADD COLUMN created_by_admin_id INTEGER",
    },
    "reviews": {
        # TRUE, not 1 — PostgreSQL refuses an integer default on a boolean
        # column (SQLite happily takes either, which is how tests missed it).
        "is_approved": "ALTER TABLE reviews ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT TRUE",
    },
}

# Follow-up data updates tied to a column above: they run exactly once, on
# the boot that adds the column — never again, so a later decision made in
# the console always sticks.
_ADDITIVE_COLUMN_FIXES = {
    # The welcome voucher became one-per-customer after databases already
    # carried the code; the flag arrives with the column and flips it.
    "coupons.once_per_customer":
        "UPDATE coupons SET once_per_customer = TRUE WHERE code = 'WELCOME10'",
}

# One-off copy corrections for older databases, matched against the exact
# shipped text so anything the baker rewrote is never touched. Each runs on
# every boot but only ever fires once — the match disappears with the text.
_DATA_FIXES = (
    "UPDATE categories SET description = NULL WHERE description = "
    "'Daily loaves, baguettes, focaccia and soft rolls — baked every morning.'",
    "UPDATE categories SET description = NULL WHERE description = "
    "'Doughnuts, maritozzo and seasonal sweet things.'",
    # The EUR cutover (2026-11-05): the shipped announcement promised a
    # dollar-denominated delivery threshold that is pending owner approval as
    # an EUR figure, so the untouched default stops promising a number.
    "UPDATE store_settings SET announcement = "
    "'WELCOME10 takes 10% off your first order' "
    "WHERE announcement = 'Free delivery on boxes over $50 · "
    "WELCOME10 takes 10% off your first order'",
    # Deleted-account shells were anonymised to a `.invalid` address the
    # email schema itself rejects — one such row 500s the whole Users roll.
    # Rewrite them to the shop's own non-mail subdomain (REPLACE is portable
    # across SQLite and PostgreSQL; fires once — the pattern it matches
    # disappears with the rewrite).
    "UPDATE users SET email = REPLACE(email, '@customers.invalid', "
    "'@deleted.djinisbakehouse.com') "
    "WHERE email LIKE 'deleted-account-%@customers.invalid'",
)


class SlidingWindowLimiter:
    """In-process rate limiter: max `limit` events per `window` seconds per key.

    Good enough for a single-worker deployment; swap for Redis if the API
    ever runs behind several workers.
    """

    # How many calls pass between sweeps of keys that have gone quiet.
    PRUNE_EVERY = 1024

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        # Handlers run on a thread pool: the check-then-append below must be
        # one step, or two requests arriving together both slip under the
        # limit.
        self._lock = threading.Lock()
        self._calls = 0

    def allow(self, key: str) -> bool:
        now = monotonic()
        with self._lock:
            self._calls += 1
            if self._calls % self.PRUNE_EVERY == 0:
                self._prune(now)
            events = self.hits[key]
            while events and now - events[0] > self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def _prune(self, now: float) -> None:
        """Forget keys with nothing left inside the window. Without this the
        table only ever grows — one entry per address ever seen, and a
        caller inventing addresses could grow it until the worker runs out
        of memory. Called under the lock."""
        stale = [
            key
            for key, events in self.hits.items()
            if not events or now - events[-1] > self.window
        ]
        for key in stale:
            del self.hits[key]


def create_limiters(settings) -> dict[str, SlidingWindowLimiter]:
    return {
        "login": SlidingWindowLimiter(settings.login_rate_limit),
        "register": SlidingWindowLimiter(settings.register_rate_limit),
        # Password reset — asking and redeeming share the same budget.
        "forgot": SlidingWindowLimiter(settings.forgot_rate_limit),
        "reset": SlidingWindowLimiter(settings.forgot_rate_limit),
        # Phone sign-in codes — texts cost money, so the brake is tight.
        "otp": SlidingWindowLimiter(settings.otp_rate_limit),
        # ...and a ceiling per phone number, per hour, whoever is asking:
        # the per-address brake above is no brake at all against a caller
        # that changes address, and every code is five more guesses.
        "otp_number": SlidingWindowLimiter(auth.PHONE_CODES_PER_HOUR, window=3600.0),
        # ...per phone number, per day; and for the whole shop, per day — a
        # ceiling on the SMS bill whoever asks and for whichever numbers.
        "otp_number_day": SlidingWindowLimiter(auth.PHONE_CODES_PER_DAY, window=86400.0),
        "otp_shop_day": SlidingWindowLimiter(settings.sms_daily_limit, window=86400.0),
        # Public write endpoints — tighter than auth because one blast of
        # signups or catering leads is plenty for anyone.
        "newsletter": SlidingWindowLimiter(5),
        "bulk": SlidingWindowLimiter(5),
        # Back-in-stock asks — emails leave the shop when they're answered,
        # so the brake matches the newsletter's.
        "restock": SlidingWindowLimiter(5),
        # One-click unsubscribe from marketing emails — a write, so braked
        # like the other public writes (the token itself is unguessable).
        "unsubscribe": SlidingWindowLimiter(10),
        # Basket quotes — a read (nothing is stored), but public and
        # unauthenticated, so it still gets a brake. Generous on purpose:
        # the storefront re-quotes on every basket change.
        "quote": SlidingWindowLimiter(settings.quote_rate_limit),
        # Per-account brakes (security.account_brake). Orders: every one
        # reserves stock and rings the desk, and a cash order costs its
        # sender nothing. Reviews: each one emails every admin. Payment
        # actions: each one is a call to Stripe or PayPal.
        "orders": SlidingWindowLimiter(settings.order_rate_limit, window=3600.0),
        "reviews": SlidingWindowLimiter(settings.review_rate_limit, window=3600.0),
        "payment_actions": SlidingWindowLimiter(settings.payment_action_rate_limit),
    }


_INLINE_SCRIPT = re.compile(rb"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)


def _content_security_policy(index_html: Path) -> str:
    """The Content-Security-Policy the app shell is served with.

    The shop keeps its sign-in tokens where page scripts can read them, so
    the one thing that must never happen is a script the shop didn't ship
    running on its pages. This policy is that rule, enforced by the
    browser: scripts only from this origin — plus the exact inline snippets
    the built index.html itself carries (the pre-paint theme switch),
    admitted by their SHA-256 so nothing else inline can ride along. The
    hashes are read from the built file, so the policy follows the build
    instead of having to be kept in step by hand.

    Everything else is same-origin too: the API (connect-src), fonts and
    styles (inline style attributes are allowed — the UI library sets
    them), images from here or any https host (a product photo may live
    elsewhere), no plugins, no framing (frame-ancestors, the modern form of
    X-Frame-Options), and forms that post only home. Stripe's hosted page
    and the Facebook dialog are full-page navigations — outside what a CSP
    governs — so checkout and social sign-in are untouched.
    """
    html = index_html.read_bytes()
    sources = ["'self'"]
    for match in _INLINE_SCRIPT.finditer(html):
        # The browser hashes the script text as it parsed it: CR/LF pairs
        # already folded to LF, whatever the file on disk carries.
        body = match.group(1).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if body.strip():
            digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
            sources.append(f"'sha256-{digest}'")
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {' '.join(sources)}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def configure_logging() -> None:
    """One predictable format for application logs; uvicorn keeps its own."""
    if logger.handlers:  # already configured (tests build several apps)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _upgrade_schema() -> None:
    """Apply additive column changes so existing databases keep working."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for column, statement in columns.items():
                if column not in present:
                    logger.info("Upgrading schema: adding %s.%s", table, column)
                    conn.execute(text(statement))
                    fix = _ADDITIVE_COLUMN_FIXES.get(f"{table}.{column}")
                    if fix:
                        conn.execute(text(fix))
        for statement in _DATA_FIXES:
            result = conn.execute(text(statement))
            if result.rowcount:
                logger.info("Applied copy fix (%d row%s)", result.rowcount, "s" if result.rowcount != 1 else "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables, bring older databases up to date, then seed reference data.
    configure_logging()
    Base.metadata.create_all(bind=engine)
    _upgrade_schema()
    from .seed import seed_db

    seed_db()

    # Housekeeping: read desk pings older than a month quietly leave.
    from .database import SessionLocal
    from .notifications import prune_notifications

    housekeeping = SessionLocal()
    try:
        pruned = prune_notifications(housekeeping)
        if pruned:
            logger.info("Pruned %d read notification(s) past retention", pruned)
    finally:
        housekeeping.close()

    settings = get_settings()
    if settings.is_production and settings.enable_docs:
        logger.warning(
            "API docs are enabled in production — set ENABLE_DOCS=false in .env if that is not intended."
        )
    # A shop whose mail goes nowhere should say so at boot, not be found out
    # by a customer who never got a receipt or a reset link.
    if settings.is_production and not settings.smtp_host:
        logger.warning(
            "Email is OFF — SMTP_HOST is not set: receipts, order updates, password "
            "resets and desk alerts are logged (subject only), never sent."
        )
    # Currency-bound money rules pending owner approval as EUR figures: each
    # unset value keeps checkout fail-closed (see routers/orders). Say it at
    # boot so a paused shop is never a mystery.
    pending_rules = [
        name
        for name, value in (
            ("SHIPPING_FEE", settings.shipping_fee),
            ("FREE_SHIPPING_THRESHOLD", settings.free_shipping_threshold),
            ("TAX_RATE", settings.tax_rate),
        )
        if value is None
    ]
    if pending_rules:
        logger.warning(
            "Checkout is PAUSED — %s pending owner approval as EUR values; "
            "quotes and placement refuse rather than charge unapproved figures.",
            ", ".join(pending_rules),
        )

    # Self-heal for payment webhooks: online orders stuck pending past
    # their pay-by window are reconciled with their provider — at boot,
    # then every few minutes. A moved or dead webhook endpoint must not
    # freeze the book.
    sweep_task: asyncio.Task | None = None
    if stripe_gateway.configured() or paypal_gateway.configured():

        async def _payment_sweep() -> None:
            while True:
                try:
                    await run_in_threadpool(payments.reconcile_stale_card_orders)
                except Exception:
                    logger.exception("payment reconciliation sweep failed")
                await asyncio.sleep(payments.SWEEP_INTERVAL_SECONDS)

        sweep_task = asyncio.create_task(_payment_sweep())

    # Which tills are real, said once at boot — nobody should have to
    # guess from a dashboard whether the shop is rehearsing or charging.
    logger.info(
        "Payments: Stripe %s · PayPal %s",
        f"{stripe_gateway.mode()} mode" if stripe_gateway.configured() else "off",
        (
            f"{paypal_gateway.environment()}"
            + ("" if paypal_gateway.webhook_ready() else " (no PAYPAL_WEBHOOK_ID — its webhooks are refused)")
        )
        if paypal_gateway.configured()
        else "off",
    )
    if paypal_gateway.misconfigured():
        logger.error(paypal_gateway.misconfigured())
    if stripe_gateway.configured() and not settings.stripe_webhook_secret:
        logger.warning(
            "STRIPE_WEBHOOK_SECRET is not set — Stripe's webhooks are refused; payments "
            "land only when the customer returns or the reconciliation sweep runs."
        )

    # Text sign-in, said once at boot like the tills above.
    logger.info(
        "Text sign-in codes: %s",
        f"Bird Verify at {bird_verify.api_base()}" if bird_verify.configured() else "off",
    )
    if bird_verify.misconfigured():
        logger.error(bird_verify.misconfigured())

    logger.info("%s API ready (%s)", settings.bakery_name, settings.app_env)
    yield
    if sweep_task is not None:
        sweep_task.cancel()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.bakery_name} API",
        version="3.13.3",
        description="Bakery e-commerce API — customers, catalog, orders, coupons, online payments (Stripe and PayPal), email / phone-code / Facebook sign-in, bulk enquiries with emailed replies, saved bakes, newsletter and admin.",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url="/api/redoc" if settings.enable_docs else None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Read by the basket page when a line is refused (routers/orders.py)
        # — only a separately hosted frontend needs them spelled out.
        expose_headers=["X-Basket-Product", "X-Basket-Available", "X-Request-ID"],
    )

    # Uploaded photos can live on a persistent disk (UPLOADS_DIR) so they
    # survive redeploys on platforms with ephemeral filesystems. Mounted
    # before /static so the more specific path wins.
    if settings.uploads_dir:
        uploads_path = Path(settings.uploads_dir)
        uploads_path.mkdir(parents=True, exist_ok=True)
        app.mount("/static/uploads", StaticFiles(directory=uploads_path), name="uploads")

    static_dir = Path(settings.static_dir)
    if not static_dir.is_absolute():
        static_dir = BACKEND_DIR / static_dir
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    limiters = create_limiters(settings)
    app.state.limiters = limiters

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Security headers plus a request id and one access-log line per request."""
        # A caller's own id is kept only when it is id-shaped — the value is
        # echoed into the log and the response, so anything else (a
        # kilobyte of noise, control characters) is replaced, not trusted.
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Logged here — with the traceback — so production 500s are never
            # silent, and the client still gets a clean JSON body.
            logger.exception("unhandled error id=%s %s %s", request_id, request.method, request.url.path)
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
        duration_ms = (monotonic() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # The desk's answers are about people — customer lists, orders, the
        # spreadsheets — so neither the browser nor anything in between may
        # keep a copy. The storefront's own caching is left as it was.
        if request.url.path == "/api/admin" or request.url.path.startswith("/api/admin/"):
            response.headers["Cache-Control"] = "no-store"
        # HSTS pins the https:// canonical domain — browsers ignore it over
        # plain HTTP, so dev on localhost is untouched.
        if settings.is_production or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            '%s %s -> %s %.1fms id=%s',
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response

    @app.get("/api/health", tags=["health"])
    def health(db: DbSession):
        """Liveness + database readiness in one probe for load balancers."""
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Health check: database unreachable")
            return JSONResponse(
                status_code=503, content={"status": "unhealthy", "database": "down"}
            )
        return {"status": "ok", "database": "up", "environment": settings.app_env}

    app.include_router(auth.router)
    app.include_router(products.router)
    app.include_router(orders.router)
    app.include_router(payments.router)
    app.include_router(admin.router)
    app.include_router(admin_payments.router)
    app.include_router(costing_routes.router)
    app.include_router(marketing.router)
    app.include_router(content.router)
    # Root-level crawl files (sitemap.xml, robots.txt) — declared before the
    # SPA catch-all takes over unknown root paths.
    app.include_router(content.seo_router)

    # Single-origin production deploy: serve the built SPA if it is present.
    dist_dir = BACKEND_DIR.parent / "frontend" / "dist"
    if settings.serve_frontend_dist and (dist_dir / "index.html").is_file():

        # The app shell's Content-Security-Policy, worked out once from the
        # built index.html (see _content_security_policy).
        shell_headers = {
            "Cache-Control": "no-cache",
            "Content-Security-Policy": _content_security_policy(dist_dir / "index.html"),
        }

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            # Unknown API paths must stay JSON 404s — the catch-all exists
            # for the SPA's client-side routes, not to swallow typos in /api.
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            candidate = (dist_dir / full_path).resolve()
            if full_path and candidate.is_file() and dist_dir.resolve() in candidate.parents:
                response = FileResponse(candidate)
                if full_path.startswith("assets/"):
                    # Vite fingerprints these filenames — safe to cache forever.
                    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return response
            # A fingerprinted asset that isn't there is a 404, never the app
            # shell: a tab left open across a deploy asks for a chunk whose
            # name no longer exists, and answering with index.html would hand
            # the browser HTML where it asked for JavaScript.
            if full_path.startswith("assets/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            # index.html must always be re-fetched so SPA updates go live.
            return FileResponse(dist_dir / "index.html", headers=shell_headers)

    return app


app = create_app()
