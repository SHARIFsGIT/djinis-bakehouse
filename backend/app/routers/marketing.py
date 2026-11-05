"""Storefront-facing endpoints: public shop copy, newsletter signups,
catering leads and the voucher board."""

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..models import (
    DEFAULT_ANNOUNCEMENT,
    DEFAULT_HERO_IMAGE,
    DEFAULT_MARQUEE,
    DEFAULT_TODAY_NOTE,
    BulkEnquiry,
    Coupon,
    Order,
    OrderStatus,
    Subscriber,
    get_store_setting,
)
from ..schemas import (
    BulkEnquiryCreate,
    BulkEnquiryOut,
    CouponPreviewOut,
    NewsletterRequest,
    NewsletterResponse,
    StorefrontOut,
    SubscriberOut,
)
from ..config import get_settings
from ..email import bulk_enquiry_ack_email, send_email
from ..notifications import notify_admin
from ..security import CurrentAdmin, DbSession, OptionalUser, client_key
from .. import facebook_gateway, sms_gateway, stripe_gateway

router = APIRouter(prefix="/api", tags=["storefront"])


def _marquee_lines(raw: str | None) -> list[str]:
    """The strip's phrases: one per line, blanks skipped. Never set shows
    the shipped set; deliberately cleared comes back empty (band hidden)."""
    if raw is None:
        raw = DEFAULT_MARQUEE
    return [line.strip() for line in raw.splitlines() if line.strip()]


@router.get("/store", response_model=StorefrontOut)
def storefront(db: DbSession):
    """The copy on the home page — editable from the admin Settings tab.
    An empty string means the admin cleared it on purpose (hide the bar);
    NULL means it was never set, so the default line shows. The hero photo
    has no 'hidden' state, so empty and NULL both fall back to the shipped
    picture."""
    settings = get_store_setting(db)
    # The currency label is published only when it is one the gateway can
    # actually charge (the same test routers/orders._charge_currency applies
    # before taking an order). A misconfigured value already 503s checkout;
    # letting it through here would retune every storefront price tag to a
    # symbol nothing can buy. None keeps the frontend on its own euro
    # default — the same fail-closed posture as the unset delivery figures.
    configured_currency = str(get_settings().stripe_currency or "").strip().lower()
    chargeable_currency = (
        configured_currency if configured_currency in stripe_gateway.SUPPORTED_CURRENCIES else None
    )
    return StorefrontOut(
        announcement=(
            settings.announcement if settings.announcement is not None else DEFAULT_ANNOUNCEMENT
        ),
        today_note=settings.today_note if settings.today_note is not None else DEFAULT_TODAY_NOTE,
        hero_image=settings.hero_image or DEFAULT_HERO_IMAGE,
        marquee=_marquee_lines(settings.marquee_items),
        demo_accounts=get_settings().demo_data_enabled,
        card_payments=stripe_gateway.configured(),
        facebook_login=facebook_gateway.configured(),
        # Phone codes only need Twilio in production — in dev the code comes
        # back with the response (dev_code), so the rail genuinely works.
        phone_login=sms_gateway.configured() or not get_settings().is_production,
        contact_address=settings.contact_address,
        contact_phone=settings.contact_phone,
        contact_email=settings.contact_email,
        opening_hours=settings.opening_hours,
        # The money rules behind every basket estimate the storefront quotes.
        currency=chargeable_currency,
        tax_rate=get_settings().tax_rate,
        shipping_fee=get_settings().shipping_fee,
        free_shipping_threshold=get_settings().free_shipping_threshold,
    )


@router.post("/newsletter", response_model=NewsletterResponse, status_code=status.HTTP_201_CREATED)
def subscribe(payload: NewsletterRequest, db: DbSession, request: Request, response: Response):
    # The form is public — keep it from being used as a mail relay.
    limiter = request.app.state.limiters["newsletter"]
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="That's a few too many signups. Try again shortly.")

    email = payload.email.lower()
    existing = db.scalar(select(Subscriber).where(Subscriber.email == email))
    if existing:
        # Signing up twice is enthusiasm, not an error — return 200, not 409.
        response.status_code = status.HTTP_200_OK
        return NewsletterResponse(email=email, already_subscribed=True)

    db.add(Subscriber(email=email, source=payload.source))
    db.commit()
    return NewsletterResponse(email=email, already_subscribed=False)


@router.post("/bulk-orders", response_model=BulkEnquiryOut, status_code=status.HTTP_201_CREATED)
def create_bulk_enquiry(
    payload: BulkEnquiryCreate, db: DbSession, request: Request, background: BackgroundTasks
):
    """A catering lead from the public "Bulk order" page.

    Open to guests — a wedding planner shouldn't need an account to ask about
    200 croissants — so it is rate-limited and validated like the newsletter
    form rather than tied to a user.
    """
    limiter = request.app.state.limiters["bulk"]
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="That's a few too many enquiries. Try again shortly.")

    if payload.event_date < date.today() + timedelta(days=1):
        raise HTTPException(
            status_code=400,
            detail="Give us at least a day's notice — pick a date from tomorrow onward",
        )
    if payload.event_date > date.today() + timedelta(days=365):
        raise HTTPException(status_code=400, detail="That date is a little far ahead — talk to us closer to the time")

    enquiry = BulkEnquiry(
        name=payload.name.strip(),
        email=payload.email.lower(),
        phone=payload.phone.strip(),
        event_date=payload.event_date,
        event_type=payload.event_type,
        guests=payload.guests,
        items_note=payload.items_note,
        message=payload.message,
    )
    db.add(enquiry)
    db.flush()
    # A catering lead is worth acting on today — same transaction, one ping.
    notify_admin(
        db,
        kind="enquiry",
        title=f"Bulk enquiry from {enquiry.name} — {enquiry.event_date:%b %d}",
        body=f"{enquiry.guests} guests · {enquiry.email} · {enquiry.phone}",
        # Straight to the enquiry rail — the Orders tab itself shows no leads
        link="/admin/orders?tab=bulk",
        background=background,
    )
    db.commit()
    db.refresh(enquiry)
    # The asker gets the same news the desk did — their enquiry landed and
    # an answer is coming. Plain values only: the task runs after the
    # session that loaded `enquiry` has closed.
    ack = bulk_enquiry_ack_email(
        {
            "name": enquiry.name,
            "event_type": enquiry.event_type,
            "event_date": f"{enquiry.event_date:%d %B %Y}",
            "guests": enquiry.guests,
        }
    )
    background.add_task(send_email, enquiry.email, *ack)
    return enquiry


@router.get("/coupons", response_model=list[CouponPreviewOut])
def public_vouchers(user: OptionalUser, db: DbSession):
    """The live voucher board for the basket page.

    Only active codes, and only what a shopper needs to decide: the saving
    and the basket it needs. Codes are meant to be shown — that's the point
    of a promotion. One exception: a once-per-customer code the signed-in
    shopper has already used drops off their board (checkout enforces it
    either way, so the board is just being honest)."""
    coupons = list(
        db.scalars(
            select(Coupon)
            .where(Coupon.is_active.is_(True))
            .order_by(Coupon.min_subtotal.is_(None), Coupon.percent_off.desc())
        )
    )
    if user is not None:
        used = set(
            db.scalars(
                select(Order.coupon_code).where(
                    Order.user_id == user.id,
                    Order.coupon_code.is_not(None),
                    Order.status != OrderStatus.CANCELLED.value,
                )
            )
        )
        coupons = [c for c in coupons if not (c.once_per_customer and c.code in used)]
    return coupons


@router.get("/admin/newsletter", response_model=list[SubscriberOut])
def list_subscribers(
    admin: CurrentAdmin,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    return list(
        db.scalars(
            select(Subscriber)
            .order_by(Subscriber.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )


@router.get("/admin/newsletter/export")
def export_subscribers(admin: CurrentAdmin, db: DbSession):
    """CSV of the mailing list, newest first — for the newsletter tool."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "signed_up", "source"])
    for s in db.scalars(select(Subscriber).order_by(Subscriber.created_at.desc())):
        writer.writerow(
            [s.email, s.created_at.strftime("%Y-%m-%d %H:%M"), s.source or ""]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="newsletter.csv"'},
    )
