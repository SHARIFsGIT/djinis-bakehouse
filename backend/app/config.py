"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    # ── Currency-bound money rules — OWNER-APPROVED EUR FIGURES (2026-11-06) ──
    # The shop charges euro only. The figures below stay unset as shipped
    # defaults (checkout pauses fail-closed without them) and are set in the
    # environment to the owner's approved values:
    #   SHIPPING_FEE=2.99  FREE_SHIPPING_THRESHOLD=25.00  TAX_RATE=0.07
    #   VIP_FREE_DELIVERY_THRESHOLD=15.00
    # TAX_RATE remains the exclusive-mode register rate (7 % by approval) —
    # the reduced/standard per-product classification and the VAT-inclusive
    # engine (7/19, seeded in vat_rates) stay parked until the tax adviser
    # clears each product, exactly as before.
    #   vip_spend_threshold   was 150 (USD) — still unset; spend-based VIP
    #                         stays off and tiers ride on order counts.
    # See docs/operations/EUR_MIGRATION_RUNBOOK.md.
    tax_rate: float | None = None
    shipping_fee: float | None = None
    free_shipping_threshold: float | None = None
    # VIP regulars earn free delivery from a smaller basket than everyone
    # else. None = no VIP benefit configured and the standard threshold
    # applies to everyone.
    vip_free_delivery_threshold: float | None = None
    vip_spend_threshold: float | None = None
    # Checkout pricing mode. "exclusive" — net shelf prices, tax added at
    # the register (the approved 7 % register rate). "inclusive" — the
    # German B2C target: gross shelf prices, VAT extracted per line by the
    # engine in app/vat.py, shipping VAT allocated across the basket's
    # rates. Inclusive mode refuses any purchasable product without an
    # approved classification — fail-closed, per product, by name — so it
    # only opens once the owner has classified the catalog at the desk.
    # An unusable value is a boot error, never a silent mode.
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
    # Which payment methods Stripe's hosted page offers. "automatic" (the
    # default) leaves the choice to Stripe: every method switched on in the
    # Stripe dashboard that suits the customer, the amount and the device —
    # cards, Apple Pay and Google Pay, Link, and whatever bank or pay-later
    # methods the account is eligible for. Nothing is hardcoded, so nothing
    # unsupported is ever requested. A comma-separated list instead narrows
    # that automatic set to the named methods (it can only filter — Stripe
    # still shows a method only where it is genuinely available).
    stripe_payment_methods: str = "automatic"

    # PayPal Checkout, as its own provider beside Stripe. The PayPal option
    # appears at checkout the moment both the client id and the secret are
    # set; PAYPAL_WEBHOOK_ID (from the webhook created in PayPal's developer
    # dashboard) is what lets the shop verify PayPal's notifications — until
    # it is set, they are refused. PAYPAL_ENVIRONMENT picks the API the
    # credentials belong to: "sandbox" for rehearsal, "live" for real money
    # (empty = sandbox; any other word switches PayPal off, never guessed).
    # Sandbox credentials never work against live and vice versa, so a
    # mismatch fails loudly at the first call rather than charging anyone.
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_environment: str = "sandbox"

    # The shop's own public address (https://djinisbakehouse.com), used for
    # the links a payment provider sends the customer back to. Empty = taken
    # from the request that started the payment, as it always was.
    public_base_url: str = ""

    # "Continue with Facebook" — Facebook Login for the web. Set the App ID
    # and secret from the app's dashboard and the button appears on the login
    # page; leave them empty and email/phone login carry the shop alone.
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    facebook_api_version: str = "v23.0"  # Graph pin — bump when Facebook retires it

    # Text sign-in codes through Bird Verify (app/bird_verify.py). Bird
    # generates, texts and checks every code; the shop never sees one.
    # BIRD_API_KEY is a workspace key with Verify read/write (bk_eu1_…) — a
    # secret, set in Render's dashboard only. Empty = text sign-in is off
    # everywhere: the button is hidden, the endpoints answer 404, and
    # password sign-in carries on exactly as before.
    bird_api_key: str = ""
    # The regional host the key belongs to: a bk_eu1_ key answers only at
    # eu1, and a mismatch keeps text sign-in off rather than failing calls.
    bird_api_base: str = "https://eu1.platform.bird.com"
    # The most text codes the shop asks Bird to send in a day, all numbers
    # together — a ceiling on the bill if someone tries to run up SMS
    # traffic. Counted in memory, so a restart starts the count afresh.
    sms_daily_limit: int = 100
    # A phone number typed the national way ("0176 …") is read as this
    # country's — the shop's own. Digits only, no "+". A number from anywhere
    # else is typed with its "+" and country code (app/phones.py).
    phone_default_country_code: str = "49"

    # Auth rate limiting (per IP, sliding window)
    login_rate_limit: int = 10  # attempts per minute
    register_rate_limit: int = 5  # accounts per minute
    forgot_rate_limit: int = 5  # reset asks + redemptions per minute
    otp_rate_limit: int = 5  # sign-in-code asks per minute
    quote_rate_limit: int = 30  # public basket price checks per minute
    # Brakes on what a signed-in account can do, keyed on the account (an
    # address changes; an account does not). Far above anything a person
    # does, and enough to stop a script from reserving the whole shelf with
    # unpaid orders, burying the desk in pings, or hammering a provider.
    order_rate_limit: int = 12  # orders placed per account per hour
    review_rate_limit: int = 10  # reviews written or edited per account per hour
    payment_action_rate_limit: int = 30  # pay-page opens and payment checks per account per minute
    # Which request header carries the visitor's real address, for the
    # brakes above. Empty (the default) keeps the long-standing behaviour:
    # the first X-Forwarded-For hop — which is only honest when the proxy in
    # front REPLACES that header; a proxy that appends to it lets any caller
    # write their own first hop and walk past every per-address brake. When
    # the edge provides an address header it sets itself (Cloudflare's
    # CF-Connecting-IP, for one), name it here. Only ever a header your own
    # edge overwrites — a pass-through header is as forgeable as the default.
    client_ip_header: str = ""

    # Where the shop is. The database keeps UTC; "today" on a delivery date
    # and the time printed on a receipt are this zone's (app/shoptime.py).
    # An IANA name; "local" means the server's own clock.
    shop_timezone: str = "Europe/Berlin"

    # Outgoing email (password resets today; newsletters and marketing later).
    # Empty SMTP_HOST = the log-only fallback: messages are logged with their
    # links instead of sent, so the flow never fails silently.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Djini's Bakehouse <no-reply@djinisbakehouse.com>"

    @field_validator("*", mode="before")
    @classmethod
    def _blank_means_unset(cls, value, info):
        """A dashboard field left empty arrives as "". For a number or a
        switch that means "not set" — the default applies — and never a
        reason to refuse to boot (a blank SMTP_PORT once did exactly that).
        Text settings keep their empty string: an empty SMTP_HOST is how
        mail is switched off."""
        if isinstance(value, str) and not value.strip():
            field = cls.model_fields.get(info.field_name)
            if field is not None and field.annotation is not str:
                return field.default
        return value

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
        if self.tax_mode not in ("exclusive", "inclusive"):
            raise ValueError(
                f"TAX_MODE must be 'exclusive' or 'inclusive', not {self.tax_mode!r} — "
                "the pricing mode is a financial decision, never a guess."
            )
        # A dashboard field left empty arrives as "" — that is "not set", and
        # not set means the rehearsal default. (Anything else that is not
        # "sandbox" or "live" is never guessed at: PayPal simply stays off —
        # see paypal_gateway.configured — and the boot log says why. A
        # payment setting must not be able to take the whole shop down.)
        if not self.paypal_environment.strip():
            self.paypal_environment = "sandbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
