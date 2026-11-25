"""The first answer of each page (app/seo.py): its own head and its status,
and the crawl files — what a search engine and a link preview see before
any script runs.

Rides on test_api.py's client (imported first, so the app boots once, on the
test database). The page tests need a frontend build (frontend/dist), as the
shell tests in test_hardening.py do, and skip without one. Whatever a test
changes in the shop — a product, a review, a setting — it puts back.
"""

import json
import re
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from test_api import client  # first: it points the app at the test database

from app import seo
from app.config import get_settings
from app.database import SessionLocal
from app.models import Product, Review, User, get_store_setting

REPO = Path(__file__).resolve().parents[2]
BASE = get_settings().public_base_url.strip().rstrip("/") or "http://testserver"


@pytest.fixture(scope="module", autouse=True)
def _shell_served():
    if not client.get("/").headers.get("content-type", "").startswith("text/html"):
        pytest.skip("no frontend build on this machine (frontend/dist) — nothing serves the shell")


def _head(html: str) -> dict:
    """The tags a crawler reads first, as one dict."""
    meta = {}
    for attr, key, value in re.findall(r'<meta (name|property)="([^"]+)" content="([^"]*)"', html):
        meta[key] = unescape(value)
    canonical = re.findall(r'<link rel="canonical" href="([^"]+)"', html)
    blocks = re.findall(r'<script type="application/ld\+json" data-seo>(.*?)</script>', html, re.S)
    return {
        "title": unescape(re.search(r"<title>(.*?)</title>", html, re.S).group(1)),
        "description": meta.get("description"),
        "robots": meta.get("robots"),
        "canonical": unescape(canonical[0]) if canonical else None,
        "og": {k[3:]: v for k, v in meta.items() if k.startswith("og:")},
        "ld": [json.loads(block) for block in blocks],
        "titles": len(re.findall(r"<title>", html)),
    }


def _graph(head: dict, kind: str) -> dict:
    nodes = [n for block in head["ld"] for n in block.get("@graph", [block])]
    return next(n for n in nodes if n["@type"] == kind)


def _product(slug: str) -> dict:
    response = client.get(f"/api/products/{slug}")
    assert response.status_code == 200, response.text
    return response.json()


def _a_slug() -> str:
    """A bake with a shelf and a photo — other suites add bare ones first."""
    shelf = client.get("/api/products", params={"per_page": 48}).json()["products"]
    return next(p["slug"] for p in shelf if p["category_slug"] and p["image_url"])


# --------------------------------------------------------------------------
# Each page names itself
# --------------------------------------------------------------------------


def test_the_home_page_names_the_shop_its_shelves_and_its_town():
    db = SessionLocal()
    store = get_store_setting(db)
    before = (store.contact_address, store.contact_phone, store.contact_email)
    store.contact_address, store.contact_phone, store.contact_email = (
        "Musterstraße, 4, 28359, Bremen", "+49 176 0000000", "hello@example.com"
    )
    db.commit()
    try:
        page = client.get("/")
        head = _head(page.text)
        shelves = [c["name"].lower() for c in client.get("/api/products/categories").json() if c["product_count"]]
        assert page.status_code == 200
        assert head["title"] == seo.DEFAULT_TITLE and head["titles"] == 1
        assert head["description"].startswith(shelves[0].capitalize())
        assert "from Djini's Bakehouse in Bremen — order online." in head["description"]
        assert head["canonical"] == f"{BASE}/" and head["og"]["url"] == f"{BASE}/"
        assert head["robots"] == "max-image-preview:large"

        org = _graph(head, "OnlineStore")
        assert org["name"] == "Djini's Bakehouse" and org["url"] == f"{BASE}/"
        assert org["email"] == "hello@example.com" and org["telephone"] == "+49 176 0000000"
        assert org["address"] == {
            "@type": "PostalAddress",
            "streetAddress": "Musterstraße 4",
            "postalCode": "28359",
            "addressLocality": "Bremen",
            "addressCountry": "DE",
        }
        site = _graph(head, "WebSite")
        assert site["publisher"] == {"@id": org["@id"]} and site["inLanguage"] == "en"
    finally:
        store.contact_address, store.contact_phone, store.contact_email = before
        db.commit()
        db.close()


def test_an_address_that_is_not_one_is_left_out_not_guessed():
    assert seo._postal_address("Somewhere nice", "+49 1") is None
    assert seo._postal_address("28359 Bremen", "+49 1") is None  # no street
    assert seo.town("Musterstraße 4, 28359 Bremen") == "Bremen"
    assert seo.town(None) is None and seo.town("no postcode here") is None
    # Germany only when the shop's own number says so.
    assert "addressCountry" not in seo._postal_address("Main St 1, 12345 Town", "+1 555 0100")


def test_a_product_page_carries_its_own_head_and_structured_data():
    slug = _a_slug()
    bake = _product(slug)
    page = client.get(f"/products/{slug}?utm_source=chat")
    head = _head(page.text)
    url = f"{BASE}/products/{slug}"
    assert page.status_code == 200
    assert head["title"] == f"{bake['name']} — Djini's Bakehouse" and head["titles"] == 1
    assert head["description"] == seo.product_description(bake["description"], bake["category_name"])
    assert head["canonical"] == url  # the query is no part of the page
    assert head["og"]["type"] == "product" and head["og"]["url"] == url
    assert head["og"]["image"] == f"{BASE}{bake['image_url']}"

    crumbs = [(c["name"], c["item"]) for c in _graph(head, "BreadcrumbList")["itemListElement"]]
    assert crumbs == [
        ("Shop", f"{BASE}/products"),
        (bake["category_name"], f"{BASE}/products?category={bake['category_slug']}"),
        (bake["name"], url),
    ]


def test_a_price_is_published_only_when_it_is_the_whole_price(monkeypatch):
    slug = _a_slug()
    bake = _product(slug)
    # As shipped, VAT is added at the checkout. Google would show the shelf
    # price as the whole price, so there is no offer — and no Product at all
    # unless live reviews give it something to stand on.
    monkeypatch.setattr(get_settings(), "tax_mode", "exclusive", raising=True)
    page = client.get(f"/products/{slug}").text
    nodes = [n for block in _head(page)["ld"] for n in block["@graph"]]
    assert all("offers" not in n for n in nodes) and '"price"' not in page
    assert any(n["@type"] == "Product" for n in nodes) is (bake["review_count"] > 0)

    # With VAT-inclusive prices the shelf price is the whole price: the offer appears.
    monkeypatch.setattr(get_settings(), "tax_mode", "inclusive", raising=True)
    item = _graph(_head(client.get(f"/products/{slug}").text), "Product")
    assert item["name"] == bake["name"] and item["sku"] == str(bake["id"])
    assert item["brand"] == {"@type": "Brand", "name": "Djini's Bakehouse"}
    assert item["image"] == [f"{BASE}{bake['image_url']}"] and item["category"] == bake["category_name"]
    offer = item["offers"]
    # The price the page shows, in the shop's currency, VAT in it.
    assert offer["price"] == f"{bake['current_price']:.2f}" and offer["priceCurrency"] == "EUR"
    assert offer["priceSpecification"]["valueAddedTaxIncluded"] is True
    assert offer["availability"] == (
        "https://schema.org/InStock" if bake["stock"] > 0 else "https://schema.org/OutOfStock"
    )
    if bake["review_count"] == 0:
        assert "aggregateRating" not in item


def test_a_bake_with_no_shelf_or_photo_still_gets_a_whole_head(monkeypatch):
    monkeypatch.setattr(get_settings(), "tax_mode", "inclusive", raising=True)  # so the Product is drawn
    db = SessionLocal()
    bare = Product(name="Plain test loaf", slug="seo-plain-test-loaf", price=2.5, stock=0, is_available=True)
    db.add(bare)
    db.commit()
    try:
        page = client.get("/products/seo-plain-test-loaf")
        head = _head(page.text)
        item = _graph(head, "Product")
        assert page.status_code == 200
        assert head["description"] == seo.DEFAULT_DESCRIPTION  # nothing of its own to say
        assert head["og"]["image"] == f"{BASE}{seo.DEFAULT_IMAGE}"  # the shop's photo, never an empty card
        assert "image" not in item and "category" not in item  # no photo or shelf is invented
        assert item["offers"]["availability"] == "https://schema.org/OutOfStock"
        crumbs = [c["name"] for c in _graph(head, "BreadcrumbList")["itemListElement"]]
        assert crumbs == ["Shop", "Plain test loaf"]
    finally:
        db.delete(bare)
        db.commit()
        db.close()


def test_stars_only_with_live_reviews_behind_them(monkeypatch):
    monkeypatch.setattr(get_settings(), "tax_mode", "exclusive", raising=True)  # reviews alone carry the Product
    slug = _a_slug()
    db = SessionLocal()
    product = db.query(Product).filter(Product.slug == slug).one()
    # one review per customer and bake — two who have not reviewed this one
    taken = {r.user_id for r in db.query(Review).filter(Review.product_id == product.id)}
    first, second = [u for u in db.query(User).order_by(User.id) if u.id not in taken][:2]
    review = Review(product_id=product.id, user_id=first.id, rating=4, comment="Lovely", is_approved=True)
    hidden = Review(product_id=product.id, user_id=second.id, rating=1, comment="Taken down", is_approved=False)
    db.add_all([review, hidden])
    db.commit()
    try:
        bake = _product(slug)
        item = _graph(_head(client.get(f"/products/{slug}").text), "Product")
        rating = item["aggregateRating"]
        assert "offers" not in item  # VAT still added at the checkout: stars, but no price
        # exactly the numbers the page shows — the taken-down review is in neither
        assert rating["reviewCount"] == bake["review_count"]
        assert rating["ratingValue"] == round(bake["average_rating"], 1)
    finally:
        db.delete(review)
        db.delete(hidden)
        db.commit()
        db.close()


def test_a_shelf_is_a_page_of_its_own_and_its_filters_are_not():
    shelf = next(c for c in client.get("/api/products/categories").json() if c["product_count"])
    home = f"{BASE}/products?category={shelf['slug']}"

    head = _head(client.get(f"/products?category={shelf['slug']}").text)
    assert head["title"] == f"{shelf['name']} — Djini's Bakehouse" and head["canonical"] == home
    assert head["robots"] == "max-image-preview:large"
    assert shelf["name"] in head["description"]

    narrowed = _head(client.get(f"/products?category={shelf['slug']}&sort=price_asc&in_stock=true&page=1").text)
    assert narrowed["canonical"] == home
    assert _head(client.get(f"/products?category={shelf['slug']}&page=2").text)["canonical"] == f"{home}&page=2"

    searched = _head(client.get(f"/products?category={shelf['slug']}&search=bread").text)
    assert searched["robots"] == "noindex, follow" and searched["canonical"] == home

    unknown = _head(client.get("/products?category=no-such-shelf").text)
    assert unknown["canonical"] == f"{BASE}/products" and unknown["title"] == "Explore our bakes — Djini's Bakehouse"
    everything = _head(client.get("/products").text)
    assert everything["canonical"] == f"{BASE}/products" and everything["description"].startswith("The full case")


def test_the_standing_pages_name_themselves():
    for path, title in (
        ("/bulk", "Bulk & event orders"),
        ("/contact", "Find us"),
        ("/privacy", "Privacy policy"),
        ("/terms", "Terms of service"),
        ("/refunds", "Refunds & cancellations"),
    ):
        page = client.get(path)
        head = _head(page.text)
        assert page.status_code == 200, path
        assert head["title"] == f"{title} — Djini's Bakehouse", path
        assert head["canonical"] == f"{BASE}{path}" and head["description"], path
    # Every page answers with its own description — none borrows another's.
    descriptions = [_head(client.get(p).text)["description"] for p in ("/", "/products", "/bulk", "/contact", "/privacy")]
    assert len(set(descriptions)) == len(descriptions)


def test_the_contact_description_names_only_what_is_published():
    db = SessionLocal()
    store = get_store_setting(db)
    fields = ("contact_address", "opening_hours", "contact_email", "contact_phone")
    before = {name: getattr(store, name) for name in fields}
    try:
        store.contact_address, store.opening_hours = "Musterstraße, 4, 28359, Bremen", "Mon–Fri 7–18"
        store.contact_email, store.contact_phone = "hello@example.com", None
        db.commit()
        described = _head(client.get("/contact").text)["description"]
        assert described.endswith("— address, bake hours and email.") and "phone" not in described
        for name in fields:
            setattr(store, name, None)
        db.commit()
        assert _head(client.get("/contact").text)["description"] == (
            "Where Djini's Bakehouse is, when we bake, and how to reach us."
        )
    finally:
        for name, value in before.items():
            setattr(store, name, value)
        db.commit()
        db.close()


# --------------------------------------------------------------------------
# Statuses and addresses
# --------------------------------------------------------------------------


def test_an_address_no_page_answers_is_a_404():
    for path in ("/no-such-page", "/products/no-such-bake", "/products/a/b", "/contact/extra"):
        page = client.get(path)
        head = _head(page.text)
        assert page.status_code == 404, path
        assert head["robots"] == "noindex" and head["canonical"] is None, path
        assert head["title"] == "Page not found — Djini's Bakehouse", path
        assert page.headers["content-type"].startswith("text/html")  # the app still draws its own 404


def test_every_page_the_app_draws_is_one_the_server_answers():
    """A page added to App.jsx but not to app/seo.py would answer 404 with
    noindex — drawn for the person, dropped by every search engine."""
    source = (REPO / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    sample = {":slug": _a_slug(), ":id": "1"}

    def concrete(path):
        return "/".join(sample.get(part, part) for part in path.split("/"))

    paths = ["/"]
    for section in ("account", "admin"):  # their pages sit inside them
        start = source.index(f'path="{section}"')
        block = source[start:source.index("</Route>", start)]
        source = source.replace(block, "")
        paths += [f"/{section}"] + [f"/{section}/{concrete(p)}" for p in re.findall(r'path="([^"]+)"', block)[1:]]
    paths += [f"/{concrete(p)}" for p in re.findall(r'path="([^"*]+)"', source)]
    assert len(paths) >= 30, paths  # the reading found the routes
    for path in paths:
        assert client.get(path).status_code == 200, path


def test_head_is_answered_wherever_get_is():
    # Uptime monitors and link checkers ask with HEAD (RFC 9110: a server
    # supports GET and HEAD). Each answer is the GET's status and headers.
    for path, status in (
        ("/", 200), ("/products", 200), ("/contact", 200), ("/robots.txt", 200), ("/sitemap.xml", 200),
        ("/no-such-page", 404), ("/products/no-such-bake", 404),
    ):
        asked, got = client.head(path), client.get(path)
        assert asked.status_code == status == got.status_code, path
        assert asked.headers["content-type"] == got.headers["content-type"], path
    assert client.head("/products/", follow_redirects=False).status_code == 301
    # The API's own routes answer HEAD as they always have.
    assert client.head("/api/products").status_code == 405


def test_a_bake_taken_off_the_shelf_is_a_404():
    slug = _a_slug()
    db = SessionLocal()
    product = db.query(Product).filter(Product.slug == slug).one()
    product.is_available = False
    db.commit()
    try:
        assert client.get(f"/products/{slug}").status_code == 404
        assert f"/products/{slug}<" not in client.get("/sitemap.xml").text
    finally:
        product.is_available = True
        db.commit()
        db.close()


def test_one_address_per_page():
    for path, target in (
        ("/products/", "/products"),
        ("/contact/", "/contact"),
        ("/products/?category=breads", "/products?category=breads"),
    ):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 301, path
        assert page.headers["location"] == target, path


def test_private_and_transactional_pages_stay_out_of_the_index():
    for path in ("/cart", "/login", "/register", "/forgot-password"):
        head = _head(client.get(path).text)
        assert head["robots"] == "noindex, follow" and head["canonical"] is None and not head["ld"], path
    for path in ("/checkout", "/account", "/account/orders/12", "/account/orders/12/receipt", "/admin",
                 "/admin/users", "/reset-password?token=x", "/unsubscribe?token=x", "/auth/callback"):
        page = client.get(path)
        head = _head(page.text)
        assert page.status_code == 200, path  # the app's own guards decide what shows
        assert head["robots"] == "noindex, nofollow" and head["canonical"] is None and not head["ld"], path


def test_the_legal_notice_is_indexed_once_it_is_written():
    db = SessionLocal()
    store = get_store_setting(db)
    before = store.legal_notice
    store.legal_notice = ""
    db.commit()
    try:
        for path in ("/imprint", "/impressum"):
            head = _head(client.get(path).text)
            assert head["robots"] == "noindex, follow" and head["canonical"] is None, path
        store.legal_notice = "Djini's Bakehouse, Bremen"
        db.commit()
        for path in ("/imprint", "/impressum"):  # one page, one address
            head = _head(client.get(path).text)
            assert head["canonical"] == f"{BASE}/imprint" and head["robots"] == "max-image-preview:large", path
    finally:
        store.legal_notice = before
        db.commit()
        db.close()


# --------------------------------------------------------------------------
# What can go wrong
# --------------------------------------------------------------------------


def test_a_description_can_never_break_out_of_the_page(monkeypatch):
    monkeypatch.setattr(get_settings(), "tax_mode", "inclusive", raising=True)  # the Product carries the description
    slug = _a_slug()
    db = SessionLocal()
    product = db.query(Product).filter(Product.slug == slug).one()
    before = product.description
    hostile = 'Crisp </script><script>alert(1)</script> & "sweet" <b>'
    product.description = hostile
    db.commit()
    try:
        html = client.get(f"/products/{slug}").text
        assert "<script>alert(1)" not in html
        head = _head(html)
        assert _graph(head, "Product")["description"] == hostile  # read back exactly
        assert head["description"].startswith("Crisp </script>")  # an attribute value, escaped in the HTML
        assert 'content="Crisp &lt;/script&gt;' in html
    finally:
        product.description = before
        db.commit()
        db.close()


def test_a_head_that_fails_still_serves_the_page(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(seo, "head_for", broken)
    page = client.get("/products")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "seo:start" in page.text  # the plain shell, as built


def test_the_font_preloads_open_the_home_page_only():
    """The three faces are preloaded on the home page — they stop its hero
    reflowing as they arrive — and nowhere else, where they only delayed the
    first paint. They are the stylesheet's own files: nothing loads twice."""
    preload = re.compile(r'<link rel="preload" href="([^"]+)" as="font" type="font/woff2" crossorigin')
    home = preload.findall(client.get("/").text)
    assert len(home) == 3
    built = (REPO / "frontend" / "dist" / "index.html").read_text(encoding="utf-8")
    stylesheet = re.search(r'href="/(assets/index-[^"]+\.css)"', built).group(1)
    css = (REPO / "frontend" / "dist" / stylesheet).read_text(encoding="utf-8")
    assert all(href in css for href in home)
    for path in ("/products", f"/products/{_a_slug()}", "/products?category=breads", "/contact", "/cart", "/no-such-page"):
        page = client.get(path).text
        assert not preload.findall(page) and "fonts:start" not in page, path


def test_every_page_keeps_the_shells_policy_and_freshness():
    for path in ("/", f"/products/{_a_slug()}", "/no-such-page"):
        page = client.get(path)
        assert "script-src 'self'" in page.headers["content-security-policy"], path
        assert page.headers["cache-control"] == "no-cache", path


# --------------------------------------------------------------------------
# The crawl files
# --------------------------------------------------------------------------


def test_robots_lets_a_crawler_render_the_pages():
    body = client.get("/robots.txt").text
    assert "Disallow: /api" not in body  # the pages read the API as they draw themselves
    for rule in ("Disallow: /admin", "Disallow: /account", "Disallow: /checkout"):
        assert rule in body
    assert f"Sitemap: {BASE}/sitemap.xml" in body


def test_the_api_is_fetched_but_never_listed():
    assert client.get("/api/products").headers.get("x-robots-tag") == "noindex"
    assert "x-robots-tag" not in client.get("/").headers


def test_the_sitemap_lists_each_public_page_once_at_its_canonical_address():
    response = client.get("/sitemap.xml")
    root = ET.fromstring(response.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "i": "http://www.google.com/schemas/sitemap-image/1.1"}
    entries = root.findall("s:url", ns)
    locs = [e.find("s:loc", ns).text for e in entries]
    assert len(locs) == len(set(locs))
    assert all(loc.startswith(f"{BASE}/") for loc in locs)
    shelves = [c for c in client.get("/api/products/categories").json() if c["product_count"]]
    for shelf in shelves:
        assert f"{BASE}/products?category={shelf['slug']}" in locs
    for private in ("/cart", "/checkout", "/account", "/admin", "/login"):
        assert not any(urlsplit(loc).path.startswith(private) for loc in locs)
    # Each bake's page carries its photo.
    product_entries = [e for e in entries if "/products/" in e.find("s:loc", ns).text]
    assert product_entries and all(e.find("i:image/i:loc", ns) is not None for e in product_entries
                                   if _product(e.find("s:loc", ns).text.rsplit("/", 1)[1])["image_url"])
    # And every address on the map answers 200, naming itself as canonical.
    for loc in locs:
        parts = urlsplit(loc)
        page = client.get(parts.path + (f"?{parts.query}" if parts.query else ""))
        assert page.status_code == 200, loc
        assert _head(page.text)["canonical"] == loc, loc


# --------------------------------------------------------------------------
# The server and the app say the same words
# --------------------------------------------------------------------------


def test_the_titles_are_the_apps_own():
    source = (REPO / "frontend" / "src" / "lib" / "titles.js").read_text(encoding="utf-8")
    pages = re.findall(r'\["(/[^"]*)",\s*"([^"]*)"\]', source)
    assert pages == seo.PAGE_TITLES
    assert f'SHOP_NAME = "{seo.SHOP_NAME}"' in source
    assert "DEFAULT_TITLE = `${SHOP_NAME} — Fresh from the oven`" in source
    assert "DEFAULT_DESCRIPTION = `${SHOP_NAME} — breads, pastries and more, to order online.`" in source
    assert f"DESCRIPTION_LIMIT = {seo.DESCRIPTION_LIMIT}" in source


def test_the_description_rules_match_the_apps():
    # The same cases are in frontend/src/lib/titles.test.js.
    assert seo.clip("  short \n text  ") == "short text"
    assert seo.clip("one two three four", 10) == "one two…"  # cut at a word
    assert seo.clip("one, two, three", 10) == "one, two…"  # no comma before the ellipsis
    assert seo.clip("abcdefghijklmnop", 10) == "abcdefghi…"  # one long word: cut inside it
    long = "Laminated over two days with cultured butter, then rolled and folded again. " * 4
    clipped = seo.clip(long)
    assert len(clipped) <= 160 and clipped.endswith("…") and long.startswith(clipped[:-1])
    assert seo.product_description("Laminated over two days.", "Pastries") == (
        "Laminated over two days. Pastries from Djini's Bakehouse."
    )
    assert seo.product_description("No full stop", "Breads") == "No full stop. Breads from Djini's Bakehouse."
    assert seo.product_description("", "Breads") == "Breads from Djini's Bakehouse."
    assert seo.product_description(None, None) == seo.DEFAULT_DESCRIPTION
    assert seo.product_description("x" * 150, "Breads") == "x" * 150 + "."  # no room for the shelf
