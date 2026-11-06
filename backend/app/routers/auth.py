"""Authentication, profile and address management endpoints."""

import hashlib
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from .. import facebook_gateway, sms_gateway
from ..config import get_settings
from ..email import reset_password_email, send_email
from ..facebook_gateway import FacebookError
from ..models import (
    Address,
    Favorite,
    Order,
    RestockAlert,
    Review,
    Subscriber,
    User,
    utcnow,
)
from ..notifications import notify_admin
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


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession, request: Request, background: BackgroundTasks):
    _check_rate_limit(request, "register")
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    # One number, one account — phone sign-in looks the number up, so two
    # accounts sharing it would sign each other in.
    if payload.phone and db.scalar(select(User.id).where(User.phone == payload.phone)):
        raise HTTPException(
            status_code=409, detail="An account with this phone number already exists — log in instead"
        )

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=payload.phone or None,
    )
    db.add(user)
    db.flush()
    # A new regular is news worth hearing at the desk.
    notify_admin(
        db,
        kind="customer",
        title=f"New customer — {user.full_name}",
        body=user.email,
        link="/admin/users",
        background=background,
    )
    db.commit()
    db.refresh(user)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession, request: Request):
    _check_rate_limit(request, "login")
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
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
        "message": f"If that email is with us, a reset link is on its way — "
        f"it works for {RESET_TOKEN_MINUTES} minutes."
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

    link = f"{str(request.base_url).rstrip('/')}/reset-password?token={token}"
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


@router.post("/phone/request")
def phone_request(
    payload: PhoneLoginRequest, db: DbSession, request: Request, background: BackgroundTasks
):
    """Text the account a one-shot sign-in code.

    The same anti-probe shape as forgot-password: the answer never reveals
    whether the number belongs to an account, and a code that went out
    seconds ago is never sent twice in a row.
    """
    _check_rate_limit(request, "otp")
    message = {
        "message": f"If that number is with us, a sign-in code is on its way — "
        f"it works for {PHONE_OTP_MINUTES} minutes."
    }
    user = db.scalar(
        select(User).where(User.phone == payload.phone, User.is_active.is_(True))
    )
    if user is None:
        return message
    now = utcnow()
    if (
        user.phone_otp_created_at is not None
        and user.phone_otp_expires is not None
        and user.phone_otp_expires > now
        and (now - user.phone_otp_created_at).total_seconds() < PHONE_RESEND_SECONDS
    ):
        return message  # a fresh code is still out — don't text twice in a minute

    code = f"{secrets.randbelow(1_000_000):06d}"
    # Only the digest is stored; the secret lives in the text message alone.
    user.phone_otp_hash = hashlib.sha256(code.encode()).hexdigest()
    user.phone_otp_expires = now + timedelta(minutes=PHONE_OTP_MINUTES)
    user.phone_otp_created_at = now
    user.phone_otp_attempts = 0
    db.commit()

    background.add_task(
        sms_gateway.send_sms,
        payload.phone,
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
    _check_rate_limit(request, "login")

    def refused() -> HTTPException:
        return HTTPException(
            status_code=400, detail="That code is wrong or has expired — send a fresh one."
        )

    user = db.scalar(
        select(User).where(User.phone == payload.phone, User.is_active.is_(True))
    )
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
    redirect_uri = f"{str(request.base_url).rstrip('/')}/api/auth/facebook/callback"
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
    base = str(request.base_url).rstrip("/")

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
    if payload.phone is not None:
        phone = payload.phone or None  # an emptied field clears the number
        if phone and db.scalar(select(User.id).where(User.phone == phone, User.id != user.id)):
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
    db.commit()
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
        }

    orders = list(
        db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at))
    )
    return {
        "exported_at": utcnow().isoformat(),
        "account": {
            "email": user.email,
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

    delete_photo(user.avatar_url)
    # The account row stays — orders point at it — but it is a shell: no
    # name, no contact details, no linked logins, no way to sign in, and
    # every token already issued dies with the version bump.
    user.email = f"deleted-account-{user_id}@customers.invalid"
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
    if payload.is_default:
        _unset_defaults(db, user)
    # First address becomes the default automatically
    data = payload.model_dump()
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
