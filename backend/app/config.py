"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Values that must never reach a production deployment.
_DEV_SECRET_KEYS = {
    "djini-bakehouse-dev-secret-key-2026-change-me",
    "change-me-in-production",
    "dev-only-secret-change-me-before-production",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "dev" or "production" — production fails fast on insecure defaults
    app_env: str = "dev"

    # Database (SQLite by default — any SQLAlchemy URL works)
    database_url: str = "sqlite:///./bakehouse.db"

    # Security — fallback is a fixed dev-only key; set SECRET_KEY in .env for anything shared
    secret_key: str = "djini-bakehouse-dev-secret-key-2026-change-me"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    algorithm: str = "HS256"

    # Business rules
    bakery_name: str = "Djini's Bakehouse"
    tax_rate: float = 0.0875
    shipping_fee: float = 5.99
    free_shipping_threshold: float = 50.0

    # CORS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Serving
    static_dir: str = "static"  # resolved against the backend working directory
    enable_docs: bool = True  # set false in production to hide /api/docs and /api/redoc
    serve_frontend_dist: bool = True  # serve frontend/dist at / when it exists (single-origin deploys)

    # Demo data. The catalog (categories, products, vouchers) always seeds on a
    # fresh database; demo customers, order history and reviews only when this
    # is on. Unset = dev yes, production no (and production refuses it outright).
    seed_demo_data: bool | None = None

    # The first admin on a fresh database when demo data is off — required then,
    # ignored when demo data is on.
    admin_email: str = ""
    admin_password: str = ""

    # Where uploaded photos live. Empty = backend/static/uploads. Set to an
    # absolute path on a persistent disk when the filesystem is ephemeral
    # (Render/Railway containers) so admin photos survive redeploys.
    uploads_dir: str = ""

    # Online card payments via Stripe's hosted Checkout. Set STRIPE_SECRET_KEY
    # and the card option comes alive at checkout; leave it empty and the shop
    # runs on cash on delivery alone. STRIPE_WEBHOOK_SECRET verifies the
    # payment-confirmation webhook (create the endpoint in the Stripe
    # dashboard to get one). Keys belong in the environment, never in code.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_currency: str = "usd"

    # Auth rate limiting (per IP, sliding window)
    login_rate_limit: int = 10  # attempts per minute
    register_rate_limit: int = 5  # accounts per minute
    forgot_rate_limit: int = 5  # reset asks + redemptions per minute

    # Outgoing email (password resets today; newsletters and marketing later).
    # Empty SMTP_HOST = the log-only fallback: messages are logged with their
    # links instead of sent, so the flow never fails silently.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Djini's Bakehouse <no-reply@djinisbakehouse.com>"

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def demo_data_enabled(self) -> bool:
        """Demo customers/orders/reviews seed fresh databases in dev only,
        unless SEED_DEMO_DATA says otherwise explicitly."""
        if self.seed_demo_data is not None:
            return self.seed_demo_data
        return not self.is_production

    @property
    def resolved_database_url(self) -> str:
        """Anchors a relative SQLite path to the backend folder so the launch
        directory never decides where the database file lives, and maps the
        postgres:// / postgresql:// URLs platforms hand out to the psycopg3
        dialect SQLAlchemy should actually use."""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix) and not Path(url[len(prefix):]).is_absolute():
            path = (BACKEND_DIR / url[len(prefix):]).resolve().as_posix()
            return f"sqlite:///{path}"
        for old in ("postgres://", "postgresql://"):
            if url.startswith(old):
                return "postgresql+psycopg://" + url[len(old):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def model_post_init(self, __context) -> None:
        if self.is_production and self.secret_key in _DEV_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is still a development default. Generate one with "
                "python -c \"import secrets; print(secrets.token_hex(32))\" and put it in .env "
                "before starting in production."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
