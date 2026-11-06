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
    # ── Currency-bound money rules — PENDING OWNER APPROVAL (2026-11-05) ──
    # The shop now charges euro only. The old figures below were USD values
    # and are NOT approved EUR values, so none of them ships as a default:
    # each stays None (checkout paused, fail-closed) until the owner sets an
    # explicit EUR figure in the environment. Kept here for review only:
    #   tax_rate              was 0.0875  (US sales tax; German VAT is pending
    #                         professional tax review — setting a rate here is
    #                         an exclusive-mode sales-tax knob, not a VAT
    #                         decision; the VAT-inclusive engine stays off)
    #   shipping_fee          was 5.99     (USD delivery fee)
    #   free_shipping_threshold was 50.0   (USD free-delivery threshold)
    #   vip_spend_threshold   was 150      (USD lifetime-spend VIP threshold)
    # See docs/operations/EUR_MIGRATION_RUNBOOK.md for what must be confirmed.
    tax_rate: float | None = None
    shipping_fee: float | None = None
    free_shipping_threshold: float | None = None
    vip_spend_threshold: float | None = None
    # Checkout pricing mode. "exclusive" — today's behaviour: net shelf
    # prices, tax added at the register while a rate is approved (no
    # approved rate, no tax line at all). "inclusive" — the German target:
    # gross shelf prices, VAT extracted per line by the engine in app/vat.py.
    # The EUR cutover (2026-11-05) did NOT flip this: inclusive mode waits
    # for the professional VAT review's classifications, and nothing reads
    # it until then, so setting it changes nothing by itself.
    tax_mode: str = "exclusive"

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
    # The shop's one charge currency (2026-11-05 owner decision: EUR only).
    # Any other value pauses checkout fail-closed rather than charging money
    # the pipeline hasn't approved — see routers/orders._charge_currency.
    stripe_currency: str = "eur"
    # Which payment methods the hosted Checkout page offers, comma-separated
    # (default just cards). Values the gateway doesn't know are dropped with
    # a warning; an empty result falls back to card, so the page never stops
    # offering the one method that always works. Availability is Stripe's to
    # decide per country — see their payment-methods docs before enabling.
    stripe_payment_methods: str = "card"

    # "Continue with Facebook" — Facebook Login for the web. Set the App ID
    # and secret from the app's dashboard and the button appears on the login
    # page; leave them empty and email/phone login carry the shop alone.
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    facebook_api_version: str = "v23.0"  # Graph pin — bump when Facebook retires it

    # Phone sign-in codes over SMS, sent through Twilio. All three together
    # switch it on; empty means log-only (dev never fails to send).
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Auth rate limiting (per IP, sliding window)
    login_rate_limit: int = 10  # attempts per minute
    register_rate_limit: int = 5  # accounts per minute
    forgot_rate_limit: int = 5  # reset asks + redemptions per minute
    otp_rate_limit: int = 5  # sign-in-code asks per minute

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
