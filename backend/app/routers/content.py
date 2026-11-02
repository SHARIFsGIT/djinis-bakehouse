"""Home page content the baker curates: testimonials and coming-soon items."""

from fastapi import APIRouter
from sqlalchemy import select

from ..models import ComingSoonItem, Testimonial
from ..schemas import ComingSoonOut, TestimonialOut
from ..security import DbSession

router = APIRouter(prefix="/api", tags=["storefront"])


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
