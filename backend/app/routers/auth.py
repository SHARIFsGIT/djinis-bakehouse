"""Authentication, profile and address management endpoints."""

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from ..config import get_settings
from ..email import quick_login_email, reset_password_email, send_email
from ..models import Address, User, utcnow
from ..notifications import notify_admin
from ..schemas import (
    AccessTokenResponse,
    AddressCreate,
    AddressOut,
    AddressUpdate,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordChangeRequest,
    QuickLoginRedeem,
    QuickLoginRequest,
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
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..uploads import MAX_AVATAR_BYTES, delete_photo, save_photo

router = APIRouter(prefix="/api/auth", tags=["auth"])

settings = get_settings()

# How long an emailed reset link stays usable.
RESET_TOKEN_MINUTES = 30
# Sign-in links live shorter than resets — enough to walk to your phone,
# not long enough to be forwarded around.
LOGIN_LINK_MINUTES = 15


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
def register(payload: RegisterRequest, db: DbSession, request: Request):
    _check_rate_limit(request, "register")
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=payload.phone,
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


@router.post("/quick-login")
def quick_login(
    payload: QuickLoginRequest, db: DbSession, request: Request, background: BackgroundTasks
):
    """Email the account a one-tap sign-in link.

    Same shape as forgot-password, deliberately: the answer is identical
    whether or not the email belongs to an account, and it shares the
    "forgot" rate budget — both are "email a link to this address" asks, so
    one brake covers both.
    """
    _check_rate_limit(request, "forgot")
    message = {
        "message": f"If that email is with us, a sign-in link is on its way — "
        f"it works once, for {LOGIN_LINK_MINUTES} minutes."
    }
    user = db.scalar(
        select(User).where(User.email == payload.email.lower(), User.is_active.is_(True))
    )
    if user is None:
        return message

    token = secrets.token_urlsafe(32)
    user.login_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.login_token_expires = utcnow() + timedelta(minutes=LOGIN_LINK_MINUTES)
    db.commit()

    link = f"{str(request.base_url).rstrip('/')}/login?token={token}"
    subject, html, text = quick_login_email(user.first_name, link, LOGIN_LINK_MINUTES)
    background.add_task(send_email, user.email, subject, html, text)
    if not settings.is_production:
        message["login_link"] = link
    return message


@router.post("/quick-login/redeem", response_model=TokenResponse)
def quick_login_redeem(payload: QuickLoginRedeem, db: DbSession, request: Request):
    """Turn a sign-in link into a session. Single-use; the password is
    untouched, so existing logins keep working."""
    _check_rate_limit(request, "login")
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    user = db.scalar(select(User).where(User.login_token_hash == digest))
    if (
        user is None
        or not user.is_active
        or user.login_token_expires is None
        or user.login_token_expires <= utcnow()
    ):
        raise HTTPException(
            status_code=400,
            detail="This sign-in link is invalid or has expired — request a fresh one.",
        )
    user.login_token_hash = None
    user.login_token_expires = None
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
        user.phone = payload.phone
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
