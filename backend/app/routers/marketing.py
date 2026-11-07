"""Storefront-facing endpoints: public shop copy, newsletter signups,
catering leads and the voucher board."""

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

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
from ..database import utcnow
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
        # The baker's brand pick, or None for the shipped burgundy
        brand_color=settings.brand_color,
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
    of a promotion. Three exceptions, each mirroring what checkout would
    refuse anyway (the board is just being honest): a once-per-customer
    code the signed-in shopper has already used; a personalised voucher
    minted for one specific customer (it belongs to their inbox, not the
    board); and a code past its expiry or total-redemption cap."""
    coupons = list(
        db.scalars(
            select(Coupon)
            .where(Coupon.is_active.is_(True), Coupon.restricted_to_user_id.is_(None))
            .order_by(Coupon.min_subtotal.is_(None), Coupon.percent_off.desc())
        )
    )
    now = utcnow()
    coupons = [c for c in coupons if not (c.expires_at is not None and c.expires_at <= now)]
    coupons = [c for c in coupons if c.code not in _fully_redeemed_codes(db, coupons)]
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


def _fully_redeemed_codes(db, coupons: list[Coupon]) -> set[str]:
    """Codes from `coupons` whose kept-order redemptions already reached
    their total cap (uncapped codes are never listed)."""
    capped = [c for c in coupons if c.max_redemptions is not None]
    if not capped:
        return set()
    counts = {
        row.coupon_code: int(row.used)
        for row in db.execute(
            select(Order.coupon_code.label("coupon_code"), func.count(Order.id).label("used"))
            .where(
                Order.coupon_code.in_([c.code for c in capped]),
                Order.status != OrderStatus.CANCELLED.value,
            )
            .group_by(Order.coupon_code)
        )
    }
    return {
        c.code for c in capped if counts.get(c.code, 0) >= int(c.max_redemptions)
    }


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


# --------------------------------------------------------------------------
# The marketing desk — consent, campaigns, personalised vouchers
# --------------------------------------------------------------------------
#
# GDPR / German e-marketing posture, in one place: marketing permission is
# explicit, separately recorded (source + evidence), and never implied by an
# account, an order or a newsletter signup. Every send consults the consent
# flag AND the suppression list; every marketing email carries a working
# one-click unsubscribe; transactional mail (receipts, status notes) is a
# different code path that suppression never touches.

from decimal import Decimal  # noqa: E402
import secrets  # noqa: E402

from .. import costing  # noqa: E402
from ..database import SessionLocal  # noqa: E402
from ..email import campaign_email  # noqa: E402
from ..loyalty import loyalty_tier  # noqa: E402
from ..models import (  # noqa: E402
    CampaignDelivery,
    MarketingCampaign,
    Suppression,
    User,
    UserRole,
)
from ..schemas import (  # noqa: E402
    CampaignCreateRequest,
    CampaignDetailOut,
    CampaignDeliveryOut,
    CampaignOut,
    CampaignSegmentIn,
    ConsentUpdateRequest,
    CouponOut,
    MarketingOverviewOut,
    MarketingStateOut,
    SuppressionOut,
    UnsubscribeRequest,
    VoucherCreateRequest,
)


def _suppressed_emails(db: DbSession) -> set[str]:
    """Every address on the suppression list, lowercased — one query per
    send, however many recipients."""
    return {
        row.lower()
        for row in db.scalars(select(Suppression.email))
    }


def _new_marketing_token(db: DbSession) -> str:
    """A fresh unsubscribe token that collides with nothing stored."""
    while True:
        token = secrets.token_urlsafe(32)
        if db.scalar(select(User.id).where(User.marketing_token == token)) is None:
            return token


@router.put(
    "/admin/users/{user_id}/marketing-consent", response_model=MarketingStateOut
)
def update_marketing_consent(
    user_id: int, payload: ConsentUpdateRequest, admin: CurrentAdmin, db: DbSession
):
    """Record or withdraw one customer's marketing consent. A grant must
    name its source and carry evidence (GDPR Art. 7 — the shop must be able
    to demonstrate consent); withdrawing clears the record and puts the
    address on the suppression list so nothing marketing ever mails it
    again without a fresh, recorded consent."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.granted:
        user.marketing_consent_at = utcnow()
        user.marketing_consent_source = payload.source.strip()
        user.marketing_consent_evidence = payload.evidence.strip()
        if not user.marketing_token:
            user.marketing_token = _new_marketing_token(db)
        # Fresh recorded consent lifts an earlier suppression — that is the
        # only thing that ever does.
        for row in db.scalars(select(Suppression).where(Suppression.email == user.email)):
            db.delete(row)
    else:
        user.marketing_consent_at = None
        user.marketing_consent_source = None
        user.marketing_consent_evidence = None
        if db.scalar(select(Suppression.id).where(Suppression.email == user.email)) is None:
            db.add(
                Suppression(email=user.email, reason="withdrawn",
                            note=f"withdrawn by {admin.full_name} at the desk")
            )
    db.commit()
    db.refresh(user)
    suppressed = db.scalar(select(Suppression).where(Suppression.email == user.email))
    return MarketingStateOut(
        marketing_consent_at=user.marketing_consent_at,
        marketing_consent_source=user.marketing_consent_source,
        marketing_consent_evidence=user.marketing_consent_evidence,
        suppressed=suppressed is not None,
        suppression_reason=suppressed.reason if suppressed else None,
    )


def _segment_customers(db: DbSession, segment: CampaignSegmentIn | None):
    """Active customer accounts matching the segment's manual-classification
    and loyalty-tier lists (a missing list = any). Loyalty is computed from
    the same measured facts the Users roll uses — the campaign tool never
    invents its own tier arithmetic."""
    users = list(
        db.scalars(
            select(User)
            .where(User.role == UserRole.CUSTOMER.value, User.is_active.is_(True))
            .order_by(User.id)
        )
    )
    if segment is None or not (segment.classifications or segment.loyalty):
        return users
    if segment.classifications:
        wanted = {c for c in segment.classifications if c}
        users = [u for u in users if u.classification in wanted]
    if segment.loyalty:
        wanted = {t for t in segment.loyalty if t}
        active = str(get_settings().stripe_currency or "").strip().lower()
        facts: dict[int, tuple[int, float, object]] = {}
        for row in db.execute(
            select(
                Order.user_id.label("user_id"),
                func.count(Order.id).label("orders"),
                func.sum(Order.total).label("spend"),
                func.max(Order.created_at).label("last"),
            )
            .where(
                Order.status != OrderStatus.CANCELLED.value,
                Order.currency == active,
            )
            .group_by(Order.user_id)
        ):
            facts[row.user_id] = (int(row.orders), round(float(row.spend or 0), 2), row.last)
        matched = []
        for user in users:
            count, spend, last = facts.get(user.id, (0, 0.0, None))
            if loyalty_tier(count, spend, last) in wanted:
                matched.append(user)
        users = matched
    return users


def _segment_json(segment: CampaignSegmentIn | None) -> dict | None:
    """The segment as stored on the campaign row — None when it selects
    everyone (a fully-empty segment means no narrowing at all)."""
    if segment is None:
        return None
    data = segment.model_dump(exclude_none=True)
    return data or None


def _campaign_audience(db: DbSession, campaign: MarketingCampaign):
    """(sendable, held) for a campaign: everyone the segment names, split by
    consent + suppression, each held address carrying its reason. The single
    recipient of a one-customer message goes through the same two gates —
    no personal message outruns permission."""
    if campaign.recipient_user_id is not None:
        user = db.get(User, campaign.recipient_user_id)
        people = [user] if user is not None else []
    else:
        segment = None
        if campaign.segment:
            segment = CampaignSegmentIn(**campaign.segment)
        people = _segment_customers(db, segment)
    suppressed = _suppressed_emails(db)
    sendable, held = [], []
    for user in people:
        email = user.email.lower()
        if email in suppressed:
            held.append((user, "suppressed (unsubscribed or withdrawn)"))
        elif user.marketing_consent_at is None:
            held.append((user, "no marketing consent"))
        else:
            sendable.append(user)
    return sendable, held


@router.get("/admin/marketing/overview", response_model=MarketingOverviewOut)
def marketing_overview(admin: CurrentAdmin, db: DbSession):
    """The marketing desk's headline numbers, including the costing book's
    brake on voucher discounts (the same 40 % margin floor the costing desk
    flags shelf prices against)."""
    active = str(get_settings().stripe_currency or "").strip().lower()
    customers = list(
        db.scalars(select(User).where(User.role == UserRole.CUSTOMER.value))
    )
    classifications = {}
    for user in customers:
        if user.classification:
            classifications[user.classification] = classifications.get(user.classification, 0) + 1
    cap = costing.max_safe_voucher_discount(db, active)
    return MarketingOverviewOut(
        customers=len(customers),
        consenting=sum(1 for u in customers if u.marketing_consent_at is not None),
        suppressed=len(_suppressed_emails(db)),
        classification_counts=classifications,
        campaigns_sent=db.scalar(
            select(func.count(MarketingCampaign.id)).where(
                MarketingCampaign.status == "sent"
            )
        )
        or 0,
        voucher_percent_cap=cap["percent"] if cap else None,
        voucher_fixed_cap=float(cap["fixed_amount"]) if cap else None,
        costed_products=cap["costed_products"] if cap else 0,
    )


@router.post("/admin/marketing/campaigns/preview")
def preview_campaign(payload: CampaignCreateRequest, admin: CurrentAdmin, db: DbSession):
    """Who a campaign would reach, before anything is created or sent:
    the consenting matches it would mail and the holds (with reasons)."""
    campaign = MarketingCampaign(
        name=payload.name,
        subject=payload.subject,
        body=payload.body,
        segment=_segment_json(payload.segment),
        recipient_user_id=payload.recipient_user_id,
    )
    if payload.recipient_user_id is not None and db.get(User, payload.recipient_user_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    sendable, held = _campaign_audience(db, campaign)
    return {
        "recipients": len(sendable),
        "held": [{"name": u.full_name, "reason": reason} for u, reason in held],
    }


@router.post("/admin/marketing/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignCreateRequest, admin: CurrentAdmin, db: DbSession):
    """Save a campaign as a draft. Nothing is emailed until the separate
    send action — the two-step is deliberate, so a saved draft can be read
    back before it goes anywhere."""
    if payload.recipient_user_id is not None and db.get(User, payload.recipient_user_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    campaign = MarketingCampaign(
        name=payload.name.strip(),
        subject=payload.subject.strip(),
        body=payload.body,
        segment=_segment_json(payload.segment),
        recipient_user_id=payload.recipient_user_id,
        created_by_admin_id=admin.id,
        created_by_name=admin.full_name,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_out(db, campaign)


def _campaign_out(db: DbSession, campaign: MarketingCampaign) -> CampaignOut:
    out = CampaignOut.model_validate(campaign)
    extra = {}
    if campaign.recipient_user_id is not None:
        user = db.get(User, campaign.recipient_user_id)
        extra["recipient_name"] = user.full_name if user else "(deleted account)"
    return out.model_copy(update=extra) if extra else out


@router.get("/admin/marketing/campaigns", response_model=list[CampaignOut])
def list_campaigns(admin: CurrentAdmin, db: DbSession):
    return [_campaign_out(db, c) for c in db.scalars(
        select(MarketingCampaign).order_by(MarketingCampaign.created_at.desc())
    )]


@router.get("/admin/marketing/campaigns/{campaign_id}", response_model=CampaignDetailOut)
def campaign_detail(campaign_id: int, admin: CurrentAdmin, db: DbSession):
    campaign = db.get(MarketingCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    out = CampaignDetailOut.model_validate(_campaign_out(db, campaign))
    users = {u.id: u for u in db.scalars(select(User))}
    out.deliveries = [
        CampaignDeliveryOut.model_validate(d).model_copy(
            update={"customer_name": users[d.user_id].full_name if d.user_id in users else None}
        )
        for d in db.scalars(
            select(CampaignDelivery)
            .where(CampaignDelivery.campaign_id == campaign_id)
            .order_by(CampaignDelivery.status, CampaignDelivery.email)
        )
    ]
    return out


def _deliver_campaign(campaign_id: int, base_url: str) -> None:
    """The background send: one email per queued delivery — individually
    addressed, individually personalised, never a list — then the counts on
    the campaign row are trued up. Runs on its own session; a mail outage
    marks deliveries failed instead of losing them."""
    db = SessionLocal()
    try:
        campaign = db.get(MarketingCampaign, campaign_id)
        if campaign is None:
            return
        sent = failed = 0
        for delivery in db.scalars(
            select(CampaignDelivery).where(
                CampaignDelivery.campaign_id == campaign_id,
                CampaignDelivery.status == "queued",
            )
        ):
            user = db.get(User, delivery.user_id) if delivery.user_id else None
            if user is None or not user.marketing_token:
                delivery.status = "failed"
                delivery.reason = "account or unsubscribe token missing"
                failed += 1
                continue
            unsubscribe_url = f"{base_url.rstrip('/')}/unsubscribe?token={user.marketing_token}"
            subject, html, text = campaign_email(
                {
                    "first_name": user.first_name,
                    "subject": campaign.subject,
                    "body": campaign.body.replace("{{first_name}}", user.first_name),
                    "why": f"You're receiving this because you opted in to news from us "
                    f"({user.marketing_consent_source or 'recorded consent'}).",
                    "unsubscribe_url": unsubscribe_url,
                }
            )
            try:
                send_email(user.email, subject, html, text)
                delivery.status = "sent"
                sent += 1
            except Exception:
                delivery.status = "failed"
                delivery.reason = "send failed — see the application log"
                failed += 1
        campaign.sent_count = (campaign.sent_count or 0) + sent
        campaign.failed_count = (campaign.failed_count or 0) + failed
        campaign.status = "sent"
        campaign.sent_at = utcnow()
        db.commit()
    finally:
        db.close()


@router.post("/admin/marketing/campaigns/{campaign_id}/send", response_model=CampaignOut)
def send_campaign(
    campaign_id: int,
    admin: CurrentAdmin,
    db: DbSession,
    background: BackgroundTasks,
    request: Request,
):
    """Send a draft campaign. The audience is resolved at this moment
    (consent + suppression, exactly as previewed), one delivery row is
    written per person — including the holds, with their reasons — and the
    emails themselves go out as a background task, one per recipient."""
    campaign = db.get(MarketingCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(status_code=409, detail=f"This campaign is already {campaign.status}")
    return _send_campaign_row(db, campaign, background, request)


@router.get("/admin/marketing/suppression", response_model=list[SuppressionOut])
def list_suppression(admin: CurrentAdmin, db: DbSession):
    """The suppression list, newest first. Rows leave it one way only: a
    freshly recorded consent (see update_marketing_consent)."""
    return list(
        db.scalars(select(Suppression).order_by(Suppression.created_at.desc()))
    )


def _voucher_code(db: DbSession) -> str:
    """A unique DJ-XXXXXX voucher code — random, never derived from the
    customer, so the code itself leaks nothing."""
    while True:
        code = f"DJ-{secrets.token_hex(3).upper()}"
        if db.scalar(select(Coupon.id).where(Coupon.code == code)) is None:
            return code


@router.post("/admin/marketing/vouchers", response_model=CouponOut, status_code=201)
def create_voucher(payload: VoucherCreateRequest, admin: CurrentAdmin, db: DbSession):
    """Mint a personalised voucher for one customer. Always one-per-customer
    and restricted to that account; percent or fixed-euro (never both); the
    40 % margin floor from the costing book caps either kind — a voucher
    must not be able to push any costed product below its floor price."""
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    active = str(get_settings().stripe_currency or "").strip().lower()
    cap = costing.max_safe_voucher_discount(db, active)
    if cap is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "No product has usable costing data yet, so no margin-safe voucher "
                "bound exists — record costs on the Costing tab first (fail-closed, "
                "never a guessed floor)."
            ),
        )
    if payload.percent_off is not None and payload.percent_off > cap["percent"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{payload.percent_off}% would break the 40% margin floor on at least "
                f"one costed product — the most a voucher may take off is {cap['percent']}%."
            ),
        )
    if payload.amount_off is not None:
        amount = Decimal(str(payload.amount_off)).quantize(Decimal("0.01"))
        if amount > cap["fixed_amount"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"€{amount} would break the 40% margin floor on at least one costed "
                    f"product — the most a voucher may take off is €{cap['fixed_amount']}."
                ),
            )
    coupon = Coupon(
        code=_voucher_code(db),
        percent_off=payload.percent_off if payload.percent_off is not None else 0,
        fixed_amount_off=(
            float(Decimal(str(payload.amount_off)).quantize(Decimal("0.01")))
            if payload.amount_off is not None
            else None
        ),
        description=payload.note.strip() if payload.note and payload.note.strip()
        else f"Personal voucher for {user.first_name}",
        once_per_customer=True,
        restricted_to_user_id=user.id,
        expires_at=payload.expires_at,
        max_redemptions=payload.max_redemptions,
        created_by_admin_id=admin.id,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return CouponOut.model_validate(coupon).model_copy(
        update={"restricted_to_name": user.full_name, "redemptions": 0}
    )


@router.post("/admin/marketing/vouchers/{coupon_id}/send", response_model=CampaignOut, status_code=201)
def send_voucher(coupon_id: int, admin: CurrentAdmin, db: DbSession, background: BackgroundTasks, request: Request):
    """Email one personalised voucher to its customer — through the same
    consent + suppression gates as any campaign, on the same delivery
    ledger. Refuses (422) for a customer without marketing permission: a
    voucher email is marketing, whatever it's worth."""
    coupon = db.get(Coupon, coupon_id)
    if coupon is None or coupon.restricted_to_user_id is None:
        raise HTTPException(status_code=404, detail="Personal voucher not found")
    user = db.get(User, coupon.restricted_to_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="The voucher's customer no longer exists")
    campaign = MarketingCampaign(
        name=f"Voucher {coupon.code}",
        subject=f"A little something from Djini — your voucher {coupon.code}",
        body=(
            "Hi {{first_name}},\n\n"
            f"Here's a personal voucher code just for you:\n\n"
            f"{coupon.code} — "
            + (
                f"€{float(coupon.fixed_amount_off):.2f} off your next order"
                if coupon.fixed_amount_off is not None
                else f"{coupon.percent_off}% off your next order"
            )
            + (
                f"\nIt's yours until {coupon.expires_at:%d %B %Y} — after that it's gone."
                if coupon.expires_at is not None else ""
            )
            + "\n\nSee you at the counter,\nDjini"
        ),
        recipient_user_id=user.id,
        created_by_admin_id=admin.id,
        created_by_name=admin.full_name,
    )
    db.add(campaign)
    db.flush()
    sendable, held = _campaign_audience(db, campaign)
    if not sendable:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail=(
                f"This customer can't be mailed: {held[0][1] if held else 'no audience'}. "
                "Record marketing consent first."
            ),
        )
    # Reuse the campaign send path wholesale — one delivery row, the same
    # background sender, the same footer.
    return _send_campaign_row(db, campaign, background, request)


def _send_campaign_row(db: DbSession, campaign: MarketingCampaign, background: BackgroundTasks, request: Request) -> CampaignOut:
    """The send internals shared by the campaign and voucher endpoints
    (send_campaign validates the campaign first; both land here)."""
    settings = get_settings()
    if settings.is_production and not settings.smtp_host:
        # Fail-closed: without a mail server a "sent" campaign would be a
        # lie — refuse rather than pretend.
        raise HTTPException(
            status_code=503,
            detail="No mail server is configured (SMTP_HOST) — nothing was sent.",
        )
    sendable, held = _campaign_audience(db, campaign)
    for user in sendable:
        if not user.marketing_token:
            user.marketing_token = _new_marketing_token(db)
    for user, reason in held:
        db.add(CampaignDelivery(campaign_id=campaign.id, user_id=user.id, email=user.email,
                                status="suppressed", reason=reason))
    for user in sendable:
        db.add(CampaignDelivery(campaign_id=campaign.id, user_id=user.id, email=user.email,
                                status="queued"))
    campaign.status = "sending"
    campaign.recipient_count = len(sendable)
    campaign.suppressed_count = len(held)
    db.commit()
    background.add_task(_deliver_campaign, campaign.id, str(request.base_url))
    db.refresh(campaign)
    return _campaign_out(db, campaign)


@router.post("/marketing/unsubscribe")
def unsubscribe(payload: UnsubscribeRequest, request: Request, db: DbSession):
    """The one-click opt-out behind every marketing email's footer. The
    token is the customer's own random key — no login needed, nothing to
    guess. Processing is immediate: consent cleared, address suppressed,
    confirmation back. Transactional mail (receipts, order updates) is a
    different path and keeps arriving."""
    limiter = request.app.state.limiters["unsubscribe"]
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Too many attempts — try again shortly.")
    token = payload.token.strip()
    user = db.scalar(select(User).where(User.marketing_token == token)) if token else None
    if user is None:
        # Unknown or already-rotated token: same answer either way, so the
        # endpoint never confirms which tokens exist.
        raise HTTPException(status_code=404, detail="This unsubscribe link isn't valid anymore.")
    user.marketing_consent_at = None
    user.marketing_consent_source = None
    user.marketing_consent_evidence = None
    user.marketing_token = _new_marketing_token(db)  # the old link dies with its email
    if db.scalar(select(Suppression.id).where(Suppression.email == user.email)) is None:
        db.add(Suppression(email=user.email, reason="unsubscribed",
                           note="via the email footer link"))
    db.commit()
    return {"message": "You're unsubscribed", "first_name": user.first_name}
