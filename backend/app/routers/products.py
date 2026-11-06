"""Product catalog: categories, listing with filters, detail, reviews and saved bakes."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select

from ..models import Category, Favorite, Product, RestockAlert, Review
from ..schemas import (
    CategoryOut,
    PaginationOut,
    ProductCard,
    ProductDetail,
    ProductListResponse,
    RestockAlertRequest,
    RestockAlertResponse,
    ReviewCreate,
    ReviewOut,
    SortOption,
)
from ..notifications import notify_review
from ..security import CurrentUser, DbSession, OptionalUser, client_key
from ..serialize import product_to_card, review_summary, review_to_out, verified_buyers

router = APIRouter(prefix="/api/products", tags=["products"])

VALID_SORTS = [s.value for s in SortOption]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: DbSession):
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()
    counts = dict(
        db.query(Product.category_id, func.count(Product.id))
        .where(Product.is_available.is_(True))
        .group_by(Product.category_id)
        .all()
    )
    # A shelf the baker just named has no photo of its own yet — it borrows
    # its first bake's, so the home tiles never sit on a bare letter tile.
    first_photos: dict[int, str] = {}
    for category_id, image_url in db.execute(
        select(Product.category_id, Product.image_url)
        .where(Product.is_available.is_(True), Product.image_url.is_not(None))
        .order_by(Product.category_id, Product.id)
    ):
        first_photos.setdefault(category_id, image_url)
    return [
        CategoryOut(
            id=c.id,
            name=c.name,
            slug=c.slug,
            description=c.description,
            image_url=c.image_url or first_photos.get(c.id),
            product_count=counts.get(c.id, 0),
        )
        for c in categories
    ]


@router.get("/featured", response_model=list[ProductCard])
def featured_products(db: DbSession, limit: int = Query(default=8, ge=1, le=24)):
    products = db.scalars(
        select(Product)
        .where(Product.is_featured.is_(True), Product.is_available.is_(True), Product.stock > 0)
        .order_by(Product.created_at.desc())
        .limit(limit)
    ).all()
    ratings = review_summary(db, [p.id for p in products])
    return [product_to_card(p, ratings) for p in products]


# Declared before /{slug} so "favorites" is never swallowed as a slug.
@router.get("/favorites", response_model=list[ProductCard])
def list_favorites(user: CurrentUser, db: DbSession):
    """The customer's saved bakes, newest heart first."""
    products = db.scalars(
        select(Product)
        .join(Favorite, Favorite.product_id == Product.id)
        .where(Favorite.user_id == user.id)
        .order_by(Favorite.created_at.desc())
    ).all()
    ratings = review_summary(db, [p.id for p in products])
    return [product_to_card(p, ratings) for p in products]


@router.put("/{product_id}/favorite")
def save_favorite(product_id: int, user: CurrentUser, db: DbSession):
    product = db.get(Product, product_id)
    if product is None or not product.is_available:
        raise HTTPException(status_code=404, detail="Product not found")
    already = db.scalar(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.product_id == product_id)
    )
    if not already:  # hearting twice just keeps the existing one
        db.add(Favorite(user_id=user.id, product_id=product_id))
        db.commit()
    return {"product_id": product_id, "saved": True}


@router.delete("/{product_id}/favorite")
def remove_favorite(product_id: int, user: CurrentUser, db: DbSession):
    favorite = db.scalar(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.product_id == product_id)
    )
    if favorite:
        db.delete(favorite)
        db.commit()
    return {"product_id": product_id, "saved": False}


@router.post("/{product_id}/restock-alert", response_model=RestockAlertResponse, status_code=status.HTTP_201_CREATED)
def request_restock_alert(
    product_id: int,
    payload: RestockAlertRequest,
    db: DbSession,
    request: Request,
    response: Response,
):
    """Ask to be emailed the moment a sold-out bake is back.

    Open to guests — the whole point is catching the shopper who's standing
    at the empty shelf — so it's rate-limited like the newsletter form, not
    gated behind an account. Asking twice keeps the one ask already on file.
    """
    limiter = request.app.state.limiters["restock"]
    if not limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429, detail="That's a few too many asks. Try again shortly."
        )

    product = db.get(Product, product_id)
    if product is None or not product.is_available:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.stock > 0:
        raise HTTPException(
            status_code=400, detail="This bake is in stock right now — no need to wait"
        )

    email = payload.email.lower()
    already = db.scalar(
        select(RestockAlert).where(
            RestockAlert.product_id == product_id, RestockAlert.email == email
        )
    )
    if not already:
        db.add(RestockAlert(product_id=product_id, email=email))
        db.commit()
    else:
        response.status_code = status.HTTP_200_OK
    return RestockAlertResponse(
        email=email, product_id=product_id, already_registered=already is not None
    )


@router.get("", response_model=ProductListResponse)
def list_products(
    db: DbSession,
    category: str | None = None,
    search: str | None = None,
    sort: SortOption = SortOption.NEWEST,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    on_sale: bool = False,
    in_stock: bool = False,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=48),
):
    query = select(Product).where(Product.is_available.is_(True))

    if category:
        cat = db.scalar(select(Category).where(Category.slug == category))
        if cat is None:
            raise HTTPException(status_code=404, detail="Category not found")
        query = query.where(Product.category_id == cat.id)

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(Product.name.ilike(term), Product.description.ilike(term))
        )

    effective_price = func.coalesce(Product.sale_price, Product.price)
    if min_price is not None:
        query = query.where(effective_price >= min_price)
    if max_price is not None:
        query = query.where(effective_price <= max_price)
    if on_sale:
        query = query.where(Product.sale_price.isnot(None), Product.sale_price < Product.price)
    if in_stock:
        query = query.where(Product.stock > 0)

    if sort == SortOption.RATING:
        # Sort the whole catalog, not just the page: join the per-product
        # average rating and let the database order by it before paging.
        # Live reviews only — the wall and the ranking agree.
        rating_subq = (
            select(
                Review.product_id,
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).label("review_count"),
            )
            .where(Review.is_approved.is_(True))
            .group_by(Review.product_id)
            .subquery()
        )
        query = query.outerjoin(rating_subq, rating_subq.c.product_id == Product.id)
        ordering = (
            func.coalesce(rating_subq.c.avg_rating, 0).desc(),
            func.coalesce(rating_subq.c.review_count, 0).desc(),
            Product.created_at.desc(),
        )
    else:
        ordering = (
            {
                SortOption.NEWEST: Product.created_at.desc(),
                SortOption.NAME: Product.name.asc(),
                SortOption.PRICE_ASC: effective_price.asc(),
                SortOption.PRICE_DESC: effective_price.desc(),
            }.get(sort, Product.created_at.desc()),
        )

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    products = db.scalars(
        query.order_by(*ordering).offset((page - 1) * per_page).limit(per_page)
    ).all()
    ratings = review_summary(db, [p.id for p in products])

    return ProductListResponse(
        products=[product_to_card(p, ratings) for p in products],
        pagination=PaginationOut(
            page=page,
            per_page=per_page,
            total_items=total,
            total_pages=max(1, -(-total // per_page)),
        ),
    )


@router.get("/{slug}", response_model=ProductDetail)
def product_detail(slug: str, db: DbSession, user: OptionalUser):
    product = db.scalar(
        select(Product).where(Product.slug == slug, Product.is_available.is_(True))
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    ratings = review_summary(db, [product.id])
    # Live reviews for everyone; a hidden one stays in its author's own view,
    # flagged, so the form still finds it and can say why it's off the wall.
    visible = [
        r for r in product.reviews if r.is_approved or (user is not None and r.user_id == user.id)
    ]
    ordered_reviews = sorted(visible, key=lambda r: r.created_at, reverse=True)
    buyers = verified_buyers(db, product.id, [r.user_id for r in ordered_reviews])
    detail = ProductDetail(
        **product_to_card(product, ratings).model_dump(),
        description=product.description,
        prep_time_minutes=product.prep_time_minutes,
        ingredients=product.ingredients,
        allergens=product.allergens,
        created_at=product.created_at,
        reviews=[
            review_to_out(r, verified_purchase=r.user_id in buyers) for r in ordered_reviews
        ],
    )

    if product.category_id:
        related = db.scalars(
            select(Product)
            .where(
                Product.category_id == product.category_id,
                Product.id != product.id,
                Product.is_available.is_(True),
            )
            .limit(4)
        ).all()
        related_ratings = review_summary(db, [p.id for p in related])
        detail.related = [product_to_card(p, related_ratings) for p in related]

    return detail


@router.post("/{product_id}/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def add_review(
    product_id: int,
    payload: ReviewCreate,
    user: CurrentUser,
    db: DbSession,
    response: Response,
    background: BackgroundTasks,
):
    """Create a review, or edit the customer's existing one for this product.

    Either way the baker's bell rings — a changed review deserves a second
    look as much as a fresh one."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    review = db.scalar(
        select(Review).where(Review.product_id == product_id, Review.user_id == user.id)
    )
    if review:
        review.rating = payload.rating
        review.comment = payload.comment
        response.status_code = status.HTTP_200_OK
        notify_review(db, review, changed=True, background=background)
    else:
        review = Review(
            product_id=product_id, user_id=user.id, rating=payload.rating, comment=payload.comment
        )
        db.add(review)
        db.flush()  # give the ping its product/author relationships
        notify_review(db, review, changed=False, background=background)
    db.commit()
    db.refresh(review)
    return review_to_out(review)


@router.delete("/{product_id}/reviews/{review_id}", status_code=status.HTTP_200_OK)
def delete_review(product_id: int, review_id: int, user: CurrentUser, db: DbSession):
    review = db.get(Review, review_id)
    if review is None or review.product_id != product_id:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Not allowed to delete this review")
    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}
