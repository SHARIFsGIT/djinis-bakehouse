"""Home page content the baker curates: testimonials and coming-soon items,
plus the crawl files (sitemap.xml, robots.txt) every serious shop serves."""

from xml.sax.saxutils import escape

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from ..models import ComingSoonItem, Product, Testimonial
from ..schemas import ComingSoonOut, TestimonialOut
from ..security import DbSession

router = APIRouter(prefix="/api", tags=["storefront"])

# The crawl files live at the domain root, not under /api — a second router
# without a prefix, included by the app factory before the SPA catch-all.
seo_router = APIRouter(tags=["seo"])


@router.get("/testimonials", response_model=list[TestimonialOut])
def list_testimonials(db: DbSession):
    """Words from the neighborhood, in the baker's chosen order.
    An empty list simply hides the section — no placeholders."""
    return list(
        db.scalars(
            select(Testimonial)
            .where(Testimonial.is_active.is_(True))
            .order_by(Testimonial.sort_order, Testimonial.created_at.desc())
        )
    )


@router.get("/coming-soon", response_model=list[ComingSoonOut])
def list_coming_soon(db: DbSession):
    """What's on its way to the counter — the home page's look-ahead band."""
    return list(
        db.scalars(
            select(ComingSoonItem)
            .where(ComingSoonItem.is_active.is_(True))
            .order_by(ComingSoonItem.sort_order, ComingSoonItem.created_at.desc())
        )
    )


def _url(base: str, path: str, *, changefreq: str, priority: str, lastmod=None) -> str:
    last = f"<lastmod>{lastmod:%Y-%m-%d}</lastmod>" if lastmod else ""
    return (
        "<url>"
        f"<loc>{escape(base + path)}</loc>"
        f"{last}"
        f"<changefreq>{changefreq}</changefreq>"
        f"<priority>{priority}</priority>"
        "</url>"
    )


@seo_router.get("/sitemap.xml")
def sitemap(request: Request, db: DbSession):
    """The shop's map for crawlers: the standing pages plus every live
    bake's page, each stamped with the moment it last changed. Sold-out
    bakes stay on the map — the page still answers, it just says so."""
    base = str(request.base_url).rstrip("/")
    urls = [
        _url(base, "/", changefreq="daily", priority="1.0"),
        _url(base, "/products", changefreq="daily", priority="0.9"),
        _url(base, "/bulk", changefreq="monthly", priority="0.6"),
        _url(base, "/privacy", changefreq="yearly", priority="0.2"),
        _url(base, "/terms", changefreq="yearly", priority="0.2"),
        _url(base, "/refunds", changefreq="yearly", priority="0.2"),
    ]
    for product in db.scalars(
        select(Product)
        .where(Product.is_available.is_(True))
        .order_by(Product.updated_at.desc())
    ):
        urls.append(
            _url(
                base,
                f"/products/{product.slug}",
                changefreq="weekly",
                priority="0.8",
                lastmod=product.updated_at,
            )
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


@seo_router.get("/robots.txt")
def robots(request: Request):
    """Crawl rules: the storefront is open, the API and every private corner
    (the desk, the account, checkout) are not."""
    base = str(request.base_url).rstrip("/")
    body = (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /admin\n"
        "Disallow: /account\n"
        "Disallow: /checkout\n"
        f"\nSitemap: {base}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")
