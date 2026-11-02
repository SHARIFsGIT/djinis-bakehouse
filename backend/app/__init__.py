"""FastAPI application factory for Djini's Bakehouse."""

import logging
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from .config import get_settings
from .database import Base, engine
from .routers import admin, auth, content, marketing, orders, products
from .security import DbSession

BACKEND_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger("bakery")

# Columns added after the first release. create_all() cannot add them to an
# existing database, so additive changes are listed here and applied on start.
_ADDITIVE_COLUMNS = {
    "users": {
        "token_version": "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0",
        "avatar_url": "ALTER TABLE users ADD COLUMN avatar_url VARCHAR(255)",
        "reset_token_hash": "ALTER TABLE users ADD COLUMN reset_token_hash VARCHAR(64)",
        "reset_token_expires": "ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP",
    },
    "orders": {
        "cancel_fee": "ALTER TABLE orders ADD COLUMN cancel_fee NUMERIC(10,2) NOT NULL DEFAULT 0",
    },
    "store_settings": {
        "announcement": "ALTER TABLE store_settings ADD COLUMN announcement VARCHAR(255)",
        "today_note": "ALTER TABLE store_settings ADD COLUMN today_note VARCHAR(255)",
        "hero_image": "ALTER TABLE store_settings ADD COLUMN hero_image VARCHAR(255)",
        "marquee_items": "ALTER TABLE store_settings ADD COLUMN marquee_items TEXT",
    },
    "coupons": {
        "min_subtotal": "ALTER TABLE coupons ADD COLUMN min_subtotal NUMERIC(10,2)",
    },
}


class SlidingWindowLimiter:
    """In-process rate limiter: max `limit` events per `window` seconds per key.

    Good enough for a single-worker deployment; swap for Redis if the API
    ever runs behind several workers.
    """

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = monotonic()
        events = self.hits[key]
        while events and now - events[0] > self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


def create_limiters(settings) -> dict[str, SlidingWindowLimiter]:
    return {
        "login": SlidingWindowLimiter(settings.login_rate_limit),
        "register": SlidingWindowLimiter(settings.register_rate_limit),
        # Password reset — asking and redeeming share the same budget.
        "forgot": SlidingWindowLimiter(settings.forgot_rate_limit),
        "reset": SlidingWindowLimiter(settings.forgot_rate_limit),
        # Public write endpoints — tighter than auth because one blast of
        # signups or catering leads is plenty for anyone.
        "newsletter": SlidingWindowLimiter(5),
        "bulk": SlidingWindowLimiter(5),
    }


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables, bring older databases up to date, then seed reference data.
    configure_logging()
    Base.metadata.create_all(bind=engine)
    _upgrade_schema()
    from .seed import seed_db

    seed_db()
    settings = get_settings()
    if settings.is_production and settings.enable_docs:
        logger.warning(
            "API docs are enabled in production — set ENABLE_DOCS=false in .env if that is not intended."
        )
    logger.info("%s API ready (%s)", settings.bakery_name, settings.app_env)
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.bakery_name} API",
        version="3.5.0",
        description="Bakery e-commerce API — customers, catalog, orders, coupons, bulk enquiries, saved bakes, newsletter and admin.",
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
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
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
    app.include_router(admin.router)
    app.include_router(marketing.router)
    app.include_router(content.router)

    # Single-origin production deploy: serve the built SPA if it is present.
    dist_dir = BACKEND_DIR.parent / "frontend" / "dist"
    if settings.serve_frontend_dist and (dist_dir / "index.html").is_file():

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
            # index.html must always be re-fetched so SPA updates go live.
            return FileResponse(dist_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
