"""What a crawler — and a link preview — learns from the first answer of each
page, before any script runs.

The shop is a single-page app: every address used to be answered with the
same index.html, so a product page told a crawler nothing until it had run
the app, a link to a bake shared in a chat previewed the home page, and an
address no page answers came back "200 OK". Here each address gets a head of
its own — the title the page itself will show, a description of what is on
it, the one address it is known by, whether it belongs in a search index at
all, a share card, and structured data built only from what the page
displays — and an address no page answers is a 404.

The words stay the app's own: titles come from the same table as the tab
titles (frontend/src/lib/titles.js — tests/test_seo.py keeps the two in
step), a product's description follows the product page's own rule, and
nothing here states a fact the shop's data does not hold.
"""

import json
import re
from dataclasses import dataclass, field
from html import escape
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import stripe_gateway
from .config import get_settings
from .models import Category, Product, get_store_setting
from .serialize import product_to_card, review_summary

SHOP_NAME = "Djini's Bakehouse"
DEFAULT_TITLE = f"{SHOP_NAME} — Fresh from the oven"
DEFAULT_DESCRIPTION = f"{SHOP_NAME} — breads, pastries and more, to order online."
DESCRIPTION_LIMIT = 160
DEFAULT_IMAGE = "/static/site/baker.jpg"
LOGO = "/static/site/logo.png"

# The tab title of each page: the same table, in the same order, as
# frontend/src/lib/titles.js (first match wins; a prefix also covers what is
# under it). tests/test_seo.py reads that file and fails if the two part.
PAGE_TITLES = [
    ("/products", "Explore our bakes"),
    ("/cart", "Your basket"),
    ("/checkout", "Checkout"),
    ("/bulk", "Bulk & event orders"),
    ("/contact", "Find us"),
    ("/login", "Log in"),
    ("/register", "Create an account"),
    ("/forgot-password", "Forgot your password"),
    ("/reset-password", "Choose a new password"),
    ("/unsubscribe", "Unsubscribe"),
    ("/privacy", "Privacy policy"),
    ("/terms", "Terms of service"),
    ("/refunds", "Refunds & cancellations"),
    ("/imprint", "Legal notice"),
    ("/impressum", "Legal notice"),
    ("/account/saved", "Saved bakes"),
    ("/account/addresses", "Address book"),
    ("/account/profile", "Your profile"),
    ("/account", "Your orders"),
    ("/admin/orders", "Desk: Orders"),
    ("/admin/products", "Desk: Products"),
    ("/admin/costing", "Desk: Costing"),
    ("/admin/reviews", "Desk: Reviews"),
    ("/admin/promotions", "Desk: Promotions"),
    ("/admin/users", "Desk: Customers"),
    ("/admin/marketing", "Desk: Marketing"),
    ("/admin/settings", "Desk: Settings"),
    ("/admin", "Baker's desk"),
]

# Every address the app answers (frontend/src/App.jsx) apart from the product
# pages; the desk and the account are whole sections whose own routes and
# guards take over. Anything else is a 404.
_PAGES = {
    "/", "/products", "/cart", "/bulk", "/contact", "/login", "/register", "/auth/callback",
    "/forgot-password", "/reset-password", "/unsubscribe", "/privacy", "/terms", "/refunds",
    "/imprint", "/impressum", "/checkout",
}
_SECTIONS = ("/account", "/admin")

# Pages that answer but belong in no search index. The private ones carry
# nothing a crawler should follow; a basket or a log-in form is simply no
# landing page.
_PRIVATE = ("/admin", "/account", "/checkout", "/reset-password", "/unsubscribe", "/auth/callback")
_TRANSACTIONAL = ("/cart", "/login", "/register", "/forgot-password")
_PRODUCT = re.compile(r"/products/([^/]+)")

# A town after a German postcode: "…, 28359, Bremen" or "28359 Bremen".
_POSTCODE_TOWN = re.compile(r"\b(\d{5})\b[\s,]+([^\d,]+)")


@dataclass
class Head:
    title: str
    description: str
    url: str  # the page's own address, absolute, without its query
    status: int = 200
    canonical: str | None = None
    robots: str | None = None
    og_type: str = "website"
    image: str | None = None
    structured: list[dict] = field(default_factory=list)
    # The shell's font preloads (frontend/index.html) — the home page only.
    preload_fonts: bool = False


# --------------------------------------------------------------------------
# Words — the same rules as frontend/src/lib/titles.js
# --------------------------------------------------------------------------


def title_for(path: str) -> str:
    if path == "/":
        return DEFAULT_TITLE
    for prefix, label in PAGE_TITLES:
        if path == prefix or path.startswith(prefix + "/"):
            return f"{label} — {SHOP_NAME}"
    return DEFAULT_TITLE


def clip(text, limit: int = DESCRIPTION_LIMIT) -> str:
    """Whitespace folded; past `limit`, cut at a word and closed with an
    ellipsis — so a snippet never ends mid-word."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"


def product_description(description, category) -> str:
    """The bake's own description, then its shelf and the shop's name when
    that still fits — the product page writes the same line."""
    own = " ".join(str(description or "").split())
    if own and own[-1] not in ".!?…":
        own += "."
    if category:
        longer = f"{own} {category} from {SHOP_NAME}.".strip()
        if len(longer) <= DESCRIPTION_LIMIT:
            return longer
    return clip(own) if own else DEFAULT_DESCRIPTION


def _listing(words: list[str]) -> str:
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def town(address: str | None) -> str | None:
    """The town of the shop's published contact address, when it says one."""
    match = _POSTCODE_TOWN.search(address or "")
    return (match.group(2).strip() or None) if match else None


# --------------------------------------------------------------------------
# Structured data — only what the page itself shows
# --------------------------------------------------------------------------


def _absolute(base: str, url: str) -> str:
    return url if url.startswith(("http://", "https://")) else f"{base}/{url.lstrip('/')}"


def _currency() -> str:
    """The currency the storefront prices in: the configured one when the
    shop can charge it, else the storefront's own euro default."""
    configured = str(get_settings().stripe_currency or "").strip().lower()
    return (configured if configured in stripe_gateway.SUPPORTED_CURRENCIES else "eur").upper()


def _postal_address(address: str | None, phone: str | None) -> dict | None:
    """The published contact address as a PostalAddress — when it reads as
    "street, house number, postcode, town". Anything less is left out."""
    match = _POSTCODE_TOWN.search(address or "")
    if not match:
        return None
    street = " ".join(part.strip() for part in address[: match.start()].split(",") if part.strip())
    if not street:
        return None
    postal = {
        "@type": "PostalAddress",
        "streetAddress": street,
        "postalCode": match.group(1),
        "addressLocality": match.group(2).strip(),
    }
    if str(phone or "").replace(" ", "").startswith(("+49", "0049")):
        postal["addressCountry"] = "DE"
    return postal


def _organization(base: str, store) -> dict:
    org = {
        "@type": "OnlineStore",
        "@id": f"{base}/#organization",
        "name": SHOP_NAME,
        "url": f"{base}/",
        "logo": f"{base}{LOGO}",
    }
    if (store.contact_email or "").strip():
        org["email"] = store.contact_email.strip()
    if (store.contact_phone or "").strip():
        org["telephone"] = store.contact_phone.strip()
    postal = _postal_address(store.contact_address, store.contact_phone)
    if postal:
        org["address"] = postal
    return org


def _breadcrumbs(trail: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(trail, start=1)
        ],
    }


# --------------------------------------------------------------------------
# Each page
# --------------------------------------------------------------------------


def _shelves(db: Session) -> list[Category]:
    """The shelves with something on them, in the shop's own order."""
    stocked = select(Product.category_id).where(Product.is_available.is_(True))
    return list(
        db.scalars(
            select(Category).where(Category.id.in_(stocked)).order_by(Category.sort_order, Category.name)
        )
    )


def _not_found(base: str, path: str) -> Head:
    return Head(
        title=f"Page not found — {SHOP_NAME}",
        description=DEFAULT_DESCRIPTION,
        url=f"{base}{path}",
        status=404,
        robots="noindex",
    )


def _product_head(db: Session, base: str, slug: str) -> Head | None:
    product = db.scalar(select(Product).where(Product.slug == slug, Product.is_available.is_(True)))
    if product is None:
        return None
    card = product_to_card(product, review_summary(db, [product.id]))
    url = f"{base}/products/{product.slug}"
    shelf = product.category
    image = _absolute(base, product.image_url) if product.image_url else None
    item = {
        "@type": "Product",
        "@id": f"{url}#product",
        "name": product.name,
        "description": " ".join((product.description or "").split()) or product.name,
        "sku": str(product.id),
        "url": url,
        "brand": {"@type": "Brand", "name": SHOP_NAME},
    }
    # Google reads a price in Germany as the whole price, VAT included. While
    # the shop adds VAT at the checkout (TAX_MODE=exclusive) the shelf price
    # is not that, and a search result would show a lower price than the
    # shopper pays — so the offer is published only with VAT-inclusive prices.
    if get_settings().tax_mode == "inclusive":
        price, currency = f"{card.current_price:.2f}", _currency()
        item["offers"] = {
            "@type": "Offer",
            "url": url,
            "price": price,
            "priceCurrency": currency,
            "priceSpecification": {
                "@type": "UnitPriceSpecification",
                "price": price,
                "priceCurrency": currency,
                "valueAddedTaxIncluded": True,
            },
            "availability": "https://schema.org/InStock" if product.stock > 0 else "https://schema.org/OutOfStock",
            "itemCondition": "https://schema.org/NewCondition",
            "seller": {"@type": "Organization", "name": SHOP_NAME},
        }
    if image:
        item["image"] = [image]
    if shelf:
        item["category"] = shelf.name
    # Stars only when there are live reviews on the page to back them.
    if card.review_count > 0:
        item["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": round(card.average_rating, 1),
            "reviewCount": card.review_count,
            "bestRating": 5,
            "worstRating": 1,
        }
    trail = [("Shop", f"{base}/products")]
    if shelf:
        trail.append((shelf.name, f"{base}/products?category={shelf.slug}"))
    trail.append((product.name, url))
    graph = [_breadcrumbs(trail)]
    # A product result needs an offer or live reviews to stand on; with
    # neither, a Product would only be flagged as incomplete.
    if "offers" in item or "aggregateRating" in item:
        graph.insert(0, item)
    return Head(
        title=f"{product.name} — {SHOP_NAME}",
        description=product_description(product.description, shelf.name if shelf else None),
        url=url,
        canonical=url,
        og_type="product",
        image=image,
        structured=[{"@context": "https://schema.org", "@graph": graph}],
    )


def _listing_head(db: Session, base: str, query: Mapping[str, str], place: str) -> Head:
    shelves = _shelves(db)
    wanted = (query.get("category") or "").strip()
    shelf = next((s for s in shelves if s.slug == wanted), None)
    page = query.get("page", "")
    canonical = f"{base}/products"
    if shelf:
        canonical += f"?category={shelf.slug}"
        names = sorted(
            db.scalars(
                select(Product.name).where(Product.category_id == shelf.id, Product.is_available.is_(True))
            )
        )
        title = f"{shelf.name} — {SHOP_NAME}"
        more = " and more" if len(names) > 3 else ""
        description = clip(
            shelf.description
            or f"{shelf.name} from {SHOP_NAME}{place}: {_listing(names[:3])}{more} — order online."
        )
    else:
        title = title_for("/products")
        words = _listing([s.name.lower() for s in shelves])
        description = clip(
            f"The full case at {SHOP_NAME}{place}"
            + (f" — {words}" if words else "")
            + ". See prices and what is in stock, and order online."
        )
    if page.isdigit() and int(page) > 1:
        canonical += ("&" if "?" in canonical else "?") + f"page={int(page)}"
    head = Head(title=title, description=description, url=f"{base}/products", canonical=canonical)
    # A search is the shopper's own question, not a page of the shop.
    if (query.get("search") or "").strip():
        head.robots = "noindex, follow"
    return head


def head_for(path: str, query: Mapping[str, str], db: Session, base: str) -> Head:
    """The head for one address (`path` with its leading slash, no query)."""
    if path != "/" and path not in _PAGES and not _PRODUCT.fullmatch(path):
        if not any(path == s or path.startswith(s + "/") for s in _SECTIONS):
            return _not_found(base, path)

    store = get_store_setting(db)
    shop_town = town(store.contact_address)
    place = f" in {shop_town}" if shop_town else ""

    product = _PRODUCT.fullmatch(path)
    if product:
        return _product_head(db, base, product.group(1)) or _not_found(base, path)

    if any(path == p or path.startswith(p + "/") for p in _PRIVATE):
        return Head(title=title_for(path), description=DEFAULT_DESCRIPTION, url=f"{base}{path}", robots="noindex, nofollow")
    if path in _TRANSACTIONAL:
        return Head(title=title_for(path), description=DEFAULT_DESCRIPTION, url=f"{base}{path}", robots="noindex, follow")

    if path == "/products":
        return _listing_head(db, base, query, place)

    if path == "/":
        words = _listing([s.name.lower() for s in _shelves(db)])
        lead = f"{words[:1].upper()}{words[1:]} from {SHOP_NAME}" if words else SHOP_NAME
        return Head(
            title=DEFAULT_TITLE,
            description=clip(f"{lead}{place} — order online."),
            url=f"{base}/",
            canonical=f"{base}/",
            # Measured: the hero's heading reflowed as the faces arrived
            # (layout shift 0.163 → 0.035 with them preloaded). Elsewhere the
            # preloads only delayed the first paint, so they stay here.
            preload_fonts=True,
            structured=[
                {
                    "@context": "https://schema.org",
                    "@graph": [
                        _organization(base, store),
                        {
                            "@type": "WebSite",
                            "@id": f"{base}/#website",
                            "url": f"{base}/",
                            "name": SHOP_NAME,
                            "inLanguage": "en",
                            "publisher": {"@id": f"{base}/#organization"},
                        },
                    ],
                }
            ],
        )

    if path in ("/imprint", "/impressum"):
        published = bool((store.legal_notice or "").strip())
        return Head(
            title=title_for(path),
            description=clip(f"Who runs {SHOP_NAME}{place} — the shop's legal notice (Impressum)."),
            url=f"{base}{path}",
            canonical=f"{base}/imprint" if published else None,
            # Not yet written: a placeholder is no page to find.
            robots=None if published else "noindex, follow",
        )

    # The contact page lists only the details the shop has published.
    published = _listing([
        label
        for label, value in (
            ("address", store.contact_address),
            ("bake hours", store.opening_hours),
            ("email", store.contact_email),
            ("phone", store.contact_phone),
        )
        if (value or "").strip()
    ])
    descriptions = {
        "/bulk": f"Events & catering from {SHOP_NAME}{place}: tell us the date and the crowd, and we quote within a working day.",
        "/contact": f"Where {SHOP_NAME} is{place}, when we bake, and how to reach us"
        + (f" — {published}." if published else "."),
        "/privacy": f"How {SHOP_NAME} collects, uses and protects your personal data.",
        "/terms": f"The terms that apply when you order from {SHOP_NAME}.",
        "/refunds": f"How cancellations and refunds work at {SHOP_NAME}.",
    }
    return Head(
        title=title_for(path),
        description=clip(descriptions.get(path, DEFAULT_DESCRIPTION)),
        url=f"{base}{path}",
        canonical=f"{base}{path}",
    )


# --------------------------------------------------------------------------
# Into the page
# --------------------------------------------------------------------------


def _json(data: dict) -> str:
    """JSON-LD that can never close its own <script>: "<", ">" and "&" are
    written as their \\u escapes, which JSON reads back as the same text."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(head: Head, base: str) -> str:
    """The head's tags. The canonical link, the robots rule and the
    structured data carry data-seo: they describe this one address, and the
    app drops them once the shopper moves on (App.jsx)."""
    a = lambda value: escape(str(value), quote=True)  # noqa: E731
    image = head.image or f"{base}{DEFAULT_IMAGE}"
    share_url = head.canonical or head.url
    tags = [
        f"<title>{a(head.title)}</title>",
        f'<meta name="description" content="{a(head.description)}" />',
    ]
    if head.robots:
        tags.append(f'<meta name="robots" content="{a(head.robots)}" data-seo />')
    else:
        # Indexable pages let a search engine show a photo at full width.
        tags.append('<meta name="robots" content="max-image-preview:large" data-seo />')
    if head.canonical:
        tags.append(f'<link rel="canonical" href="{a(head.canonical)}" data-seo />')
    tags += [
        f'<meta property="og:type" content="{a(head.og_type)}" />',
        f'<meta property="og:site_name" content="{a(SHOP_NAME)}" />',
        f'<meta property="og:title" content="{a(head.title)}" />',
        f'<meta property="og:description" content="{a(head.description)}" />',
        f'<meta property="og:url" content="{a(share_url)}" />',
        f'<meta property="og:image" content="{a(image)}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{a(head.title)}" />',
        f'<meta name="twitter:description" content="{a(head.description)}" />',
        f'<meta name="twitter:image" content="{a(image)}" />',
    ]
    for block in head.structured:
        tags.append(f'<script type="application/ld+json" data-seo>{_json(block)}</script>')
    return "\n    ".join(tags)


class Shell:
    """The built index.html, split once at its SEO block, so each answer is
    the shell with that block written for its own address. Its font preload
    block is kept only where the head asks for it (the home page)."""

    START = "<!-- seo:start"
    END = "<!-- seo:end -->"
    FONTS_START = "<!-- fonts:start"
    FONTS_END = "<!-- fonts:end -->"

    def __init__(self, html: str):
        self.html = html
        self.parts = self._split(html)
        start, end = html.find(self.FONTS_START), html.find(self.FONTS_END)
        without_fonts = html[:start] + html[end + len(self.FONTS_END):] if 0 <= start < end else html
        self.parts_without_fonts = self._split(without_fonts)

    @classmethod
    def _split(cls, html: str):
        start, end = html.find(cls.START), html.find(cls.END)
        return (html[:start], html[end + len(cls.END):]) if 0 <= start < end else None

    def render(self, head: Head, base: str) -> str:
        parts = self.parts if head.preload_fonts else self.parts_without_fonts
        if parts is None:  # a shell built without the markers: served as it is
            return self.html
        return parts[0] + render(head, base) + parts[1]
