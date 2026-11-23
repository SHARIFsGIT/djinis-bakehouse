"""Authentication, profile and address management endpoints."""

import hashlib
import logging
import secrets
import threading
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .. import facebook_gateway, payment_ops, phones, sms_gateway
from ..config import get_settings
from ..email import (
    DELETED_ACCOUNT_DOMAIN,
    PHONE_ACCOUNT_DOMAIN,
    is_parked,
    reset_password_email,
    send_email,
)
from ..facebook_gateway import FacebookError
from ..models import (
    Address,
    CheckoutAttempt,
    ClassificationEvent,
    Favorite,
    Order,
    PaymentRefund,
    PaymentTransaction,
    RestockAlert,
    Review,
    Subscriber,
    User,
    UserRole,
    utcnow,
)
from ..notifications import notify_admin
from ..serialize import find_matching_address
from ..schemas import (
    AccessTokenResponse,
    AccountDeleteRequest,
    AddressCreate,
    AddressOut,
    AddressUpdate,
    ForgotPasswordRequest,
    LoginCodeExchange,
    LoginRequest,
    PasswordChangeRequest,
    PhoneLoginRequest,
    PhoneLoginVerify,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
    UserUpdateRequest,
)
from ..security import (
    CurrentUser,
    DbSession,
    client_key,
    create_access_token,
    create_login_state,
    create_refresh_token,
    decode_token,
    hash_password,
    site_base,
    verify_login_state,
    verify_password,
)
from ..uploads import MAX_AVATAR_BYTES, delete_photo, save_photo

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("bakery.auth")

settings = get_settings()

# How long an emailed reset link stays usable.
RESET_TOKEN_MINUTES = 30
# A texted sign-in code: ten minutes of life, five wrong tries, and a
# minute before the same number can ask for another (the texts cost money).
PHONE_OTP_MINUTES = 10
PHONE_OTP_MAX_ATTEMPTS = 5
PHONE_RESEND_SECONDS = 60
# ...and how many codes one number may be sent in an hour. Each code allows
# five guesses, so without this ceiling a patient script gets five guesses
# a minute at an account for as long as it likes (and, with Twilio on, bills
# the shop a text a minute while it does). Enforced by the "otp_number"
# limiter in the app factory.
PHONE_CODES_PER_HOUR = 5
# The one-time code a social callback hands the browser — a short window,
# because the SPA redeems it the instant the page loads.
LOGIN_CODE_SECONDS = 60


def _check_rate_limit(request: Request, name: str) -> None:
    limiter = request.app.state.limiters[name]
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a minute.")


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        user=UserOut.model_validate(user),
    )


def _phone_holders(db, phone: str, *, apart_from: int | None = None) -> list[User]:
    """The accounts a phone number is on file for — whichever way either
    side spelled it (app/phones.py). Oldest first; never more than a few."""
    query = select(User).where(User.phone.in_(phones.spellings(phone)))
    if apart_from is not None:
        query = query.where(User.id != apart_from)
    return list(db.scalars(query.order_by(User.id).limit(3)))


# There is no unique index on the phone column (accounts older than phone
# sign-in may share a number), so "is this number free?" and "take it" are
# two steps. The lock makes them one for everyone inside this process —
# which is the whole deployment: one worker, by design (render.yaml).
_phone_claim = threading.Lock()

# What a login for an account that does not exist is checked against, so it
# takes as long as one for an account that does — the answer's wording is
# the same either way, and its timing should not give away what it won't.
_NOBODY = hash_password(secrets.token_urlsafe(16))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession, request: Request, background: BackgroundTasks):
    _check_rate_limit(request, "register")
    email = payload.email.lower() if payload.email else None
    phone = phones.canonical(payload.phone) if payload.phone else None
    if is_parked(email):
        # The shop's own parking domains are its to write, nobody's to claim.
        raise HTTPException(status_code=400, detail="That email address can't be used for an account")
    if email and db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    with _phone_claim:
        # One number, one account — signing in with a phone number looks the
        # number up, so two accounts sharing it would open each other.
        if phone and _phone_holders(db, phone):
            raise HTTPException(
                status_code=409, detail="An account with this phone number already exists — log in instead"
            )
        user = User(
            # An account opened with a phone number alone still needs a
            # unique, well-formed entry in the address column; it gets a
            # parked one that nobody is shown and nothing is sent to.
            email=email or f"phone-{secrets.token_hex(8)}@{PHONE_ACCOUNT_DOMAIN}",
            password_hash=hash_password(payload.password),
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip(),
            phone=phone,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            # The same sign-up arrived twice at once (a double tap): both
            # passed the check above, and the other request's insert won the
            # unique email. Same answer as finding it there — not a 500.
            db.rollback()
            raise HTTPException(
                status_code=409, detail="An account with this email already exists"
            ) from None
        # A new regular is news worth hearing at the desk.
        notify_admin(
            db,
            kind="customer",
            title=f"New customer — {user.full_name}",
            body=user.mailbox or user.phone,
            link="/admin/users",
            background=background,
        )
        db.commit()
    db.refresh(user)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession, request: Request):
    """Email and password, or phone number and password."""
    _check_rate_limit(request, "login")
    if payload.email:
        known = list(db.scalars(select(User).where(User.email == payload.email.lower())))
        wrong = "Incorrect email or password"
    else:
        # A number shared by two old accounts is still one way in for each:
        # the password says whose.
        known = _phone_holders(db, payload.phone)
        wrong = "Incorrect phone number or password"
    user = next((u for u in known if verify_password(payload.password, u.password_hash)), None)
    if user is None:
        if not known:
            verify_password(payload.password, _NOBODY)
        raise HTTPException(status_code=401, detail=wrong)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated")
    return _token_response(user)


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest, db: DbSession, request: Request, background: BackgroundTasks
):
    """Email the account a one-shot reset link.

    The answer is the same whether or not the email belongs to an account,
    so nobody can probe the customer list from here.
    """
    _check_rate_limit(request, "forgot")
    message = {
        "message": f"If that email is with us, the link is on its way — "
        f"valid for {RESET_TOKEN_MINUTES} minutes."
    }
    user = db.scalar(
        select(User).where(User.email == payload.email.lower(), User.is_active.is_(True))
    )
    if user is None:
        return message

    token = secrets.token_urlsafe(32)
    # Only the digest is stored; the secret lives in the email alone.
    user.reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.reset_token_expires = utcnow() + timedelta(minutes=RESET_TOKEN_MINUTES)
    db.commit()

    link = f"{site_base(request)}/reset-password?token={token}"
    subject, html, text = reset_password_email(user.first_name, link, RESET_TOKEN_MINUTES)
    background.add_task(send_email, user.email, subject, html, text)
    # Dev convenience: with SMTP unset the link lands in the server log, so
    # hand it straight back for local testing. Never in production.
    if not settings.is_production:
        message["reset_link"] = link
    return message


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: DbSession, request: Request):
    """Redeem a reset link: set the new password and retire the token."""
    _check_rate_limit(request, "reset")
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    user = db.scalar(select(User).where(User.reset_token_hash == digest))
    if (
        user is None
        or not user.is_active
        or user.reset_token_expires is None
        or user.reset_token_expires <= utcnow()
    ):
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired — request a fresh one.",
        )
    user.password_hash = hash_password(payload.password)
    user.reset_token_hash = None
    user.reset_token_expires = None
    # Kill every outstanding session, exactly like an in-app password change.
    user.token_version += 1
    db.commit()
    return {"message": "Password updated — log in with the new one."}


def _require_phone_rail() -> None:
    """Phone sign-in answers only where its texts can actually be sent.

    On a production deployment without Twilio a code would reach nothing
    but the server log — the customer could never receive it, yet anyone
    could keep guessing at it. The storefront already hides the rail there
    (/api/store → phone_login); the API must not leave the same door ajar.
    Dev keeps working without Twilio: the code rides back in the response."""
    if settings.is_production and not sms_gateway.configured():
        raise HTTPException(status_code=404, detail="Phone sign-in is not configured")


@router.post("/phone/request")
def phone_request(
    payload: PhoneLoginRequest, db: DbSession, request: Request, background: BackgroundTasks
):
    """Text the account a one-shot sign-in code.

    The same anti-probe shape as forgot-password: the answer never reveals
    whether the number belongs to an account, and a code that went out
    seconds ago is never sent twice in a row.
    """
    _require_phone_rail()
    _check_rate_limit(request, "otp")
    message = {
        "message": f"If that number is with us, a sign-in code is on its way — "
        f"it works for {PHONE_OTP_MINUTES} minutes."
    }
    user = next((u for u in _phone_holders(db, payload.phone) if u.is_active), None)
    if user is None:
        return message
    number = phones.canonical(user.phone)
    now = utcnow()
    if (
        user.phone_otp_created_at is not None
        and user.phone_otp_expires is not None
        and user.phone_otp_expires > now
        and (now - user.phone_otp_created_at).total_seconds() < PHONE_RESEND_SECONDS
    ):
        return message  # a fresh code is still out — don't text twice in a minute
    # The hourly ceiling per number (PHONE_CODES_PER_HOUR). Keyed on the
    # number itself, not the caller's address, so it holds whoever asks and
    # from wherever — and answered with the same words as everything else,
    # so it tells a prober nothing.
    if not request.app.state.limiters["otp_number"].allow(number):
        logger.warning("sign-in code ceiling reached for a number — no code sent this hour")
        return message

    code = f"{secrets.randbelow(1_000_000):06d}"
    # Only the digest is stored; the secret lives in the text message alone.
    user.phone_otp_hash = hashlib.sha256(code.encode()).hexdigest()
    user.phone_otp_expires = now + timedelta(minutes=PHONE_OTP_MINUTES)
    user.phone_otp_created_at = now
    user.phone_otp_attempts = 0
    db.commit()

    background.add_task(
        sms_gateway.send_sms,
        number,
        f"{settings.bakery_name}: your sign-in code is {code}. "
        f"It works once, for {PHONE_OTP_MINUTES} minutes.",
    )
    # Dev convenience: without a Twilio number the code lands in the server
    # log, so hand it straight back for local testing. Never in production.
    if not settings.is_production:
        message["dev_code"] = code
    return message


@router.post("/phone/verify", response_model=TokenResponse)
def phone_verify(payload: PhoneLoginVerify, db: DbSession, request: Request):
    """Turn a texted code into a session. Wrong tries are counted — five
    and the code burns, so guessing six digits is never worth it."""
    _require_phone_rail()
    _check_rate_limit(request, "login")

    def refused() -> HTTPException:
        return HTTPException(
            status_code=400, detail="That code is wrong or has expired — send a fresh one."
        )

    user = next((u for u in _phone_holders(db, payload.phone) if u.is_active), None)
    if user is None or not user.phone_otp_hash:
        raise refused()
    user.phone_otp_attempts += 1
    if (
        user.phone_otp_expires is None
        or user.phone_otp_expires <= utcnow()
        or user.phone_otp_attempts > PHONE_OTP_MAX_ATTEMPTS
    ):
        user.phone_otp_hash = None  # burned — the next try needs a fresh code
        db.commit()
        raise refused()
    if hashlib.sha256(payload.code.encode()).hexdigest() != user.phone_otp_hash:
        db.commit()
        raise refused()

    user.phone_otp_hash = None
    user.phone_otp_expires = None
    user.phone_otp_created_at = None
    db.commit()
    return _token_response(user)


# --------------------------------------------------------------------------
# "Continue with Facebook" — OAuth via the Login dialog
# --------------------------------------------------------------------------

def _split_name(name: str, email: str) -> tuple[str, str]:
    """Facebook hands back one display name; the shop wants first and last."""
    parts = name.split(" ", 1)
    if parts[0]:
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    local = email.split("@")[0]
    return local, ""


@router.get("/facebook/start")
def facebook_start(request: Request):
    """The Login dialog's URL — the browser goes to Facebook, Facebook sends
    it back to /api/auth/facebook/callback with a one-time code."""
    if not facebook_gateway.configured():
        raise HTTPException(status_code=404, detail="Facebook sign-in is not configured")
    redirect_uri = f"{site_base(request)}/api/auth/facebook/callback"
    return {"url": facebook_gateway.authorize_url(redirect_uri, create_login_state())}


@router.get("/facebook/callback")
def facebook_callback(
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Where Facebook lands the browser after its dialog. No human sees this
    page: it issues a one-time login code and redirects to the SPA, which
    exchanges it for real tokens — so tokens never ride in a URL."""
    base = site_base(request)

    def back(slug: str) -> RedirectResponse:
        return RedirectResponse(f"{base}/auth/callback?error={slug}")

    if error:  # the customer closed the dialog
        return back("facebook_declined")
    if not code or not state or not verify_login_state(state):
        return back("facebook_state")
    redirect_uri = f"{base}/api/auth/facebook/callback"
    try:
        access_token = facebook_gateway.exchange_code(code, redirect_uri)
        profile = facebook_gateway.fetch_profile(access_token)
    except FacebookError:
        return back("facebook_exchange")

    fb_id = str(profile.get("id", ""))
    email = str(profile.get("email", "")).strip().lower()
    if not fb_id or not email:
        return back("facebook_no_email")

    user = db.scalar(select(User).where(User.facebook_id == fb_id))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
    if user is not None and not user.is_active:
        return back("account_disabled")
    if user is None:
        first_name, last_name = _split_name(str(profile.get("name", "")), email)
        user = User(
            email=email,
            # No password until the customer sets one (a reset email does it);
            # this hash is unguessable, so the password rail stays shut.
            password_hash=hash_password(secrets.token_urlsafe(32)),
            first_name=first_name,
            last_name=last_name,
            facebook_id=fb_id,
        )
        db.add(user)
        db.flush()
        # A new regular is news worth hearing at the desk.
        notify_admin(
            db,
            kind="customer",
            title=f"New customer — {user.full_name}",
            body=f"{user.email} (via Facebook)",
            link="/admin/users",
            background=background,
        )
    elif user.facebook_id != fb_id:
        user.facebook_id = fb_id  # the same person, a second way in

    one_time = secrets.token_urlsafe(32)
    user.login_code_hash = hashlib.sha256(one_time.encode()).hexdigest()
    user.login_code_expires = utcnow() + timedelta(seconds=LOGIN_CODE_SECONDS)
    db.commit()
    return RedirectResponse(f"{base}/auth/callback?code={one_time}")


@router.post("/exchange", response_model=TokenResponse)
def exchange_login_code(payload: LoginCodeExchange, db: DbSession, request: Request):
    """Swap a social callback's one-time code for a real session."""
    _check_rate_limit(request, "login")
    digest = hashlib.sha256(payload.code.encode()).hexdigest()
    user = db.scalar(select(User).where(User.login_code_hash == digest))
    if (
        user is None
        or not user.is_active
        or user.login_code_expires is None
        or user.login_code_expires <= utcnow()
    ):
        raise HTTPException(
            status_code=400, detail="That sign-in window has expired — please start again."
        )
    user.login_code_hash = None
    user.login_code_expires = None
    db.commit()
    return _token_response(user)


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(payload: RefreshRequest, db: DbSession):
    data = decode_token(payload.refresh_token, expected_type="refresh")
    user = db.get(User, int(data["sub"])) if data and str(data.get("sub", "")).isdigit() else None
    if user is None or data.get("ver", 0) != user.token_version or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    return AccessTokenResponse(access_token=create_access_token(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.put("/me", response_model=UserOut)
def update_me(payload: UserUpdateRequest, user: CurrentUser, db: DbSession):
    if payload.first_name is not None:
        user.first_name = payload.first_name.strip()
    if payload.last_name is not None:
        user.last_name = payload.last_name.strip()
    if payload.email is not None and payload.email.lower() != (user.mailbox or "").lower():
        # Adding an address to an account opened with a phone number. One
        # already on file is the account's identity and is not changed here.
        if user.mailbox:
            raise HTTPException(status_code=400, detail="Your email address can't be changed here")
        email = payload.email.lower()
        if is_parked(email):
            raise HTTPException(status_code=400, detail="That email address can't be used for an account")
        if db.scalar(select(User.id).where(User.email == email, User.id != user.id)):
            raise HTTPException(status_code=400, detail="Another account already uses this email address")
        user.email = email
    # "Is the number free?" and "take it" as one step (see _phone_claim).
    with _phone_claim:
        if payload.phone is not None:
            phone = (phones.canonical(payload.phone) if payload.phone else None) or None  # emptied = cleared
            if phone is None and not user.mailbox:
                # The number is this account's only way in.
                raise HTTPException(
                    status_code=400,
                    detail="Add an email address first — your phone number is how you sign in",
                )
            if phone and _phone_holders(db, phone, apart_from=user.id):
                raise HTTPException(
                    status_code=400, detail="Another account already uses this phone number"
                )
            if phone != user.phone:
                # A code texted to the old number must never open the new one.
                user.phone_otp_hash = None
                user.phone_otp_expires = None
                user.phone_otp_created_at = None
                user.phone_otp_attempts = 0
            user.phone = phone
        try:
            db.commit()
        except IntegrityError:
            # Two accounts reached for the same address at the same moment,
            # and the other one's write won the unique column.
            db.rollback()
            raise HTTPException(
                status_code=400, detail="Another account already uses this email address"
            ) from None
    db.refresh(user)
    return user


@router.put("/me/password", response_model=TokenResponse)
def change_password(payload: PasswordChangeRequest, user: CurrentUser, db: DbSession):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    # Invalidate every outstanding token, then hand this device fresh ones.
    user.token_version += 1
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(photo: UploadFile, user: CurrentUser, db: DbSession):
    """Set the profile photo — a phone camera or a file from the desk works."""
    url = await save_photo(photo, folder="avatars", max_bytes=MAX_AVATAR_BYTES)
    delete_photo(user.avatar_url)  # drop the previous upload, if any
    user.avatar_url = url
    db.commit()
    db.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserOut)
def remove_avatar(user: CurrentUser, db: DbSession):
    delete_photo(user.avatar_url)
    user.avatar_url = None
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------
# The customer's own data — see it whole, or take it back (GDPR art. 15/17)
# --------------------------------------------------------------------------

@router.get("/me/export")
def export_my_data(user: CurrentUser, db: DbSession):
    """Everything the shop holds on this account, as one JSON download.

    Secrets are described, never included: passwords and codes exist only
    as one-way hashes, which are of no use to anyone but the login itself.
    """
    def _order(order):
        return {
            "order_number": order.order_number,
            "placed_at": order.created_at.isoformat(),
            "status": order.status,
            "payment_method": order.payment_method,
            "payment_status": order.payment_status,
            "currency": order.currency,
            "subtotal": float(order.subtotal),
            "discount_amount": float(order.discount_amount or 0),
            "coupon_code": order.coupon_code,
            "tax": float(order.tax),
            "shipping_fee": float(order.shipping_fee),
            "total": float(order.total),
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "delivered_to": {
                "recipient": order.recipient,
                "phone": order.phone,
                "line1": order.line1,
                "line2": order.line2,
                "city": order.city,
                "state": order.state,
                "postal_code": order.postal_code,
                "country": order.country,
            },
            "items": [
                {
                    "product_name": item.product_name,
                    "unit_price": float(item.unit_price),
                    "quantity": item.quantity,
                    "subtotal": float(item.subtotal),
                }
                for item in order.items
            ],
            "timeline": [
                {"status": event.status, "comment": event.comment, "at": event.created_at.isoformat()}
                for event in order.timeline
            ],
            # What the shop keeps about paying for this order: each pay
            # page it was handed, through whom, and what became of it. No
            # card or account details exist here to export — those never
            # reach the shop, only the provider's own reference does.
            "payments": [
                {
                    "provider": tx.provider,
                    "method": payment_ops.method_label(tx.method_type),
                    "state": tx.status,
                    "amount": float(tx.amount),
                    "currency": tx.currency,
                    "opened_at": tx.created_at.isoformat(),
                    "paid_at": tx.paid_at.isoformat() if tx.paid_at else None,
                    "provider_reference": tx.provider_payment_id or tx.provider_reference,
                    "refunded_amount": float(tx.refunded_amount or 0),
                    "refunds": [
                        {
                            "amount": float(refund.amount),
                            "status": refund.status,
                            "at": refund.created_at.isoformat(),
                        }
                        for refund in refunds.get(tx.id, [])
                    ],
                }
                for tx in payments.get(order.id, [])
            ],
        }

    orders = list(
        db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at))
    )
    order_ids = [order.id for order in orders] or [0]
    payments: dict[int, list[PaymentTransaction]] = {}
    for tx in db.scalars(
        select(PaymentTransaction)
        .where(PaymentTransaction.order_id.in_(order_ids))
        .order_by(PaymentTransaction.id)
    ):
        payments.setdefault(tx.order_id, []).append(tx)
    refunds: dict[int, list[PaymentRefund]] = {}
    for refund in db.scalars(
        select(PaymentRefund).where(PaymentRefund.order_id.in_(order_ids)).order_by(PaymentRefund.id)
    ):
        refunds.setdefault(refund.transaction_id, []).append(refund)
    return {
        "exported_at": utcnow().isoformat(),
        "account": {
            "email": user.mailbox,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "avatar_url": user.avatar_url,
            "facebook_linked": user.facebook_id is not None,
            "member_since": user.created_at.isoformat(),
            # What the shop keeps but cannot show: one-way hashes only.
            "password_storage": "bcrypt hash (not exportable)",
        },
        "addresses": [
            {
                "label": a.label,
                "recipient": a.recipient,
                "phone": a.phone,
                "line1": a.line1,
                "line2": a.line2,
                "city": a.city,
                "state": a.state,
                "postal_code": a.postal_code,
                "country": a.country,
                "is_default": a.is_default,
            }
            for a in user.addresses
        ],
        "orders": [_order(o) for o in orders],
        "reviews": [
            {
                "product_id": r.product_id,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at.isoformat(),
            }
            for r in db.scalars(select(Review).where(Review.user_id == user.id))
        ],
        "saved_products": list(
            db.scalars(select(Favorite.product_id).where(Favorite.user_id == user.id))
        ),
        "restock_alerts": list(
            db.scalars(
                select(RestockAlert.product_id).where(RestockAlert.email == user.email.lower())
            )
        ),
        "newsletter_subscribed": user.email.lower()
        in set(db.scalars(select(Subscriber.email).where(Subscriber.email == user.email.lower()))),
    }


@router.post("/me/delete")
def delete_my_account(
    payload: AccountDeleteRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    background: BackgroundTasks,
):
    """Close the account and scrub the person from it (GDPR art. 17).

    The *books* stay honest: orders and their address snapshots are
    invoices, which the shop must keep by law — but they keep belonging to
    "a deleted customer", never to a name. Everything that is purely the
    person (address book, reviews, hearts, asks, subscriptions, logins)
    goes away entirely, the account is deactivated with an unguessable
    password and no way in, and every outstanding session dies.

    The password is asked again at the door — an open tab on a shared
    computer must not be enough to erase an account.
    """
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    # The shop must never end up with zero active admins — the same rule
    # the Users desk keeps (admin.update_user). This door needs it too: an
    # admin's own profile page carries the same "close my account" button.
    if user.role == UserRole.ADMIN.value:
        other_admins = db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.ADMIN.value, User.is_active.is_(True), User.id != user.id
            )
        )
        if not other_admins:
            raise HTTPException(
                status_code=400,
                detail="You are the shop's only admin — closing this account would lock "
                "everyone out of the desk. Make another account an admin first.",
            )

    user_id = user.id
    old_email = user.email.lower()

    # The purely personal rows go first, explicitly — the dev database runs
    # SQLite without foreign keys enforced, so nothing may rely on the
    # cascade alone.
    db.execute(delete(Address).where(Address.user_id == user_id))
    db.execute(delete(Review).where(Review.user_id == user_id))
    db.execute(delete(Favorite).where(Favorite.user_id == user_id))
    db.execute(delete(RestockAlert).where(RestockAlert.email == old_email))
    db.execute(delete(Subscriber).where(Subscriber.email == old_email))
    # The marketing person goes with the rest: the classification ledger
    # (it names the person) and the consent record itself. The suppression
    # row STAYS — deleting the account is not a licence to start mailing
    # the address again should it ever return.
    db.execute(delete(ClassificationEvent).where(ClassificationEvent.user_id == user_id))
    # Remembered checkout presses are the account's, not the books' — the
    # orders they placed (and those orders' payment ledger) stay.
    db.execute(delete(CheckoutAttempt).where(CheckoutAttempt.user_id == user_id))

    delete_photo(user.avatar_url)
    # The account row stays — orders point at it — but it is a shell: no
    # name, no contact details, no linked logins, no way to sign in, and
    # every token already issued dies with the version bump. The placeholder
    # address is unique per account and lives under a non-mail subdomain of
    # the shop's own domain — syntactically valid on purpose: an `.invalid`
    # style suffix is rejected by the email schema, and one deleted account
    # would then 500 the whole Users roll.
    user.email = f"deleted-account-{user_id}@{DELETED_ACCOUNT_DOMAIN}"
    user.first_name = "Deleted"
    user.last_name = "customer"
    user.phone = None
    user.avatar_url = None
    user.facebook_id = None
    user.reset_token_hash = None
    user.reset_token_expires = None
    user.phone_otp_hash = None
    user.phone_otp_expires = None
    user.phone_otp_created_at = None
    user.login_code_hash = None
    user.login_code_expires = None
    user.classification = None
    user.marketing_consent_at = None
    user.marketing_consent_source = None
    user.marketing_consent_evidence = None
    user.marketing_token = None
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.token_version += 1
    user.is_active = False

    # The desk's audit trail names the account number, never the person.
    notify_admin(
        db,
        kind="customer",
        title=f"A customer deleted their account (#{user_id})",
        body="Their data was anonymized; past orders remain as invoices.",
        link="/admin/users",
        background=background,
    )
    db.commit()
    logger.info("account #%s deleted and anonymized at its own request", user_id)
    return {
        "message": "Your account has been deleted — past orders remain only as anonymized invoices."
    }


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------

# The address lines that may be emptied; every other one is required.
_CLEARABLE_ADDRESS_FIELDS = frozenset({"phone", "line2"})


def _get_own_address(db, user: User, address_id: int) -> Address:
    address = db.get(Address, address_id)
    if address is None or address.user_id != user.id:
        raise HTTPException(status_code=404, detail="Address not found")
    return address


def _unset_defaults(db, user: User, keep_id: int | None = None):
    for addr in db.scalars(select(Address).where(Address.user_id == user.id)):
        if addr.is_default and addr.id != keep_id:
            addr.is_default = False


@router.get("/addresses", response_model=list[AddressOut])
def list_addresses(user: CurrentUser, db: DbSession):
    return list(user.addresses)


@router.post("/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
def create_address(payload: AddressCreate, user: CurrentUser, db: DbSession):
    data = payload.model_dump()
    if find_matching_address(db, user.id, data) is not None:
        # The book already has this doorstep — say so rather than file a
        # second copy of it (checkout quietly reuses; the book, where the
        # customer is looking at the list, deserves the plain word).
        raise HTTPException(
            status_code=409,
            detail="This address is already in your book — edit it instead of adding it again",
        )
    if payload.is_default:
        _unset_defaults(db, user)
    # First address becomes the default automatically
    data["is_default"] = payload.is_default or not user.addresses
    address = Address(user_id=user.id, **data)
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


@router.put("/addresses/{address_id}", response_model=AddressOut)
def update_address(address_id: int, payload: AddressUpdate, user: CurrentUser, db: DbSession):
    address = _get_own_address(db, user, address_id)
    if payload.is_default:
        _unset_defaults(db, user, keep_id=address.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None and field not in _CLEARABLE_ADDRESS_FIELDS:
            # A required line sent as null means "leave it" — the column
            # cannot hold nothing, and the customer did not ask for that.
            continue
        setattr(address, field, value)
    db.commit()
    db.refresh(address)
    return address


@router.delete("/addresses/{address_id}")
def delete_address(address_id: int, user: CurrentUser, db: DbSession):
    address = _get_own_address(db, user, address_id)
    db.delete(address)
    db.commit()
    return {"message": "Address deleted"}
