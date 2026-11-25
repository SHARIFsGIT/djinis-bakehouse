"""The shell's two marked blocks (app/seo.py Shell): the SEO block written
for each address, and the font preloads kept on the home page only.

Plain string tests on a made-up shell — they need no frontend build, so
they run on every machine (tests/test_seo.py covers the real build). Imports
test_api first, so the app boots on the test database.
"""

from test_api import client  # noqa: F401 — first: it points the app at the test database

from app import seo
from app.database import SessionLocal

BASE = "https://shop.example"
SHELL = (
    "<!doctype html><html><head>\n"
    "<!-- seo:start — defaults -->\n<title>Default</title>\n<!-- seo:end -->\n"
    "<!-- fonts:start — the faces -->\n"
    '<link rel="preload" href="/assets/face.woff2" as="font" type="font/woff2" crossorigin />\n'
    "<!-- fonts:end -->\n"
    "<script>/* theme */</script>\n"
    '</head><body><div id="root"></div></body></html>'
)


def _head(**fields) -> seo.Head:
    return seo.Head(title="A page", description="About it", url=f"{BASE}/a", **fields)


def test_the_home_page_keeps_the_font_preloads():
    page = seo.Shell(SHELL).render(_head(preload_fonts=True), BASE)
    assert '<link rel="preload" href="/assets/face.woff2"' in page
    assert "<title>A page</title>" in page and "<title>Default</title>" not in page


def test_every_other_page_goes_without_them_and_keeps_the_rest():
    page = seo.Shell(SHELL).render(_head(), BASE)
    assert "preload" not in page and "fonts:start" not in page and "fonts:end" not in page
    assert "<title>A page</title>" in page
    assert "<script>/* theme */</script>" in page and '<div id="root"></div>' in page


def test_a_shell_without_the_font_block_serves_as_before():
    start, end = SHELL.index("<!-- fonts:start"), SHELL.index("<!-- fonts:end -->") + len("<!-- fonts:end -->")
    plain = SHELL[:start] + SHELL[end:]
    for preload in (True, False):
        page = seo.Shell(plain).render(_head(preload_fonts=preload), BASE)
        assert "<title>A page</title>" in page and "preload" not in page


def test_a_shell_without_the_seo_markers_is_served_as_built():
    built = "<!doctype html><html><head><title>Built</title></head><body></body></html>"
    assert seo.Shell(built).render(_head(preload_fonts=True), BASE) == built


def test_only_the_home_page_asks_for_the_preloads():
    db = SessionLocal()
    try:
        assert seo.head_for("/", {}, db, BASE).preload_fonts is True
        for path in ("/products", "/products/butter-croissant", "/contact", "/cart", "/admin", "/no-such-page"):
            assert seo.head_for(path, {}, db, BASE).preload_fonts is False, path
    finally:
        db.close()
