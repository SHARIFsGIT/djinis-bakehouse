"""Shared model -> schema helpers used by several routers."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Address, Order, OrderItem, Product, Review, User
from .schemas import ProductCard, ReviewOut

# The fields that make a delivery address the *same doorstep* — label and
# default flag deliberately excluded (they're bookkeeping, not place).
_ADDRESS_MATCH_FIELDS = (
    "recipient",
    "line1",
    "line2",
    "city",
    "state",
    "postal_code",
    "country",
)


def _normalized(value) -> str:
    """Case- and whitespace-blind comparison form: '12  Crumb Lane' and
    '12 crumb lane' are the same door."""
    return " ".join(str(value or "").lower().split())


def find_matching_address(db: Session, user_id: int, data: dict) -> Address | None:
    """The address in this user's book that is already this doorstep, or None.

    Checkout and the address book both ask before filing a new row, so
    placing orders can never grow the book a copy at a time.
    """
    addresses = db.scalars(select(Address).where(Address.user_id == user_id)).all()
    for address in addresses:
        if all(
            _normalized(getattr(address, field)) == _normalized(data.get(field))
            for field in _ADDRESS_MATCH_FIELDS
        ):
            return address
    return None


def review_summary(db: Session, product_ids: list[int]) -> dict[int, tuple[float, int]]:
    """Average rating and review count per product id, in one query.

    Live reviews only — the numbers shoppers see never include one the baker
    has taken down."""
    if not product_ids:
        return {}
    rows = (
        db.query(Review.product_id, func.avg(Review.rating), func.count(Review.id))
        .filter(Review.product_id.in_(product_ids), Review.is_approved.is_(True))
        .group_by(Review.product_id)
        .all()
    )
    return {pid: (round(float(avg) or 0.0, 1), int(count)) for pid, avg, count in rows}


def verified_buyers(db: Session, product_id: int, user_ids: list[int]) -> set[int]:
    """Which of these reviewers actually received this product (cancelled orders don't count)."""
    if not user_ids:
        return set()
    rows = (
        db.query(Order.user_id)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .filter(
            Order.user_id.in_(user_ids),
            OrderItem.product_id == product_id,
            Order.status != "cancelled",
        )
        .distinct()
        .all()
    )
    return {user_id for (user_id,) in rows}


def product_to_card(product: Product, ratings: dict[int, tuple[float, int]] | None = None) -> ProductCard:
    ratings = ratings or {}
    avg, count = ratings.get(product.id, (0.0, 0))
    return ProductCard(
        id=product.id,
        name=product.name,
        slug=product.slug,
        price=float(product.price),
        sale_price=float(product.sale_price) if product.sale_price is not None else None,
        current_price=product.current_price,
        is_on_sale=product.is_on_sale,
        discount_percentage=product.discount_percentage,
        is_featured=product.is_featured,
        is_available=product.is_available,
        stock=product.stock,
        image_url=product.image_url,
        category_name=product.category.name if product.category else None,
        category_slug=product.category.slug if product.category else None,
        average_rating=avg,
        review_count=count,
    )


def review_to_out(review: Review, verified_purchase: bool = False) -> ReviewOut:
    user: User | None = review.user
    return ReviewOut(
        id=review.id,
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
        author_name=user.full_name if user else None,
        author_id=review.user_id,
        verified_purchase=verified_purchase,
        is_approved=review.is_approved,
    )
