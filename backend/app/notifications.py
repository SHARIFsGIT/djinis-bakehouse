"""The baker's bell — one helper every customer-facing write calls.

Each call sites adds the row in its own transaction, so a ping exists if and
only if the order/review/enquiry it describes does.
"""

from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import delete, select

from .database import utcnow
from .email import send_admin_notification
from .models import AdminNotification, Review, User, UserRole

# How long a read ping is worth keeping around before housekeeping sweeps it.
READ_RETENTION_DAYS = 30


def notify_admin(
    db,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    background: BackgroundTasks | None = None,
) -> None:
    """Queue a desk ping. No commit here — the caller's transaction owns it.

    Hand a `background` along and the same ping also travels to every active
    admin's inbox: the bell is for the desk, the email is for the baker
    who's away from it."""
    if body:
        body = body[:500]
    title = title[:255]
    db.add(AdminNotification(kind=kind, title=title, body=body, link=link))
    if background is not None:
        recipients = ", ".join(
            db.scalars(
                select(User.email)
                .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
                .order_by(User.id)
            )
        )
        if recipients:
            background.add_task(send_admin_notification, recipients, title, body, link)


def notify_review(
    db, review: Review, *, changed: bool, background: BackgroundTasks | None = None
) -> None:
    """Word the ping for a review the shop can't afford to miss."""
    product = review.product
    author = review.user.full_name if review.user else "Someone"
    verb = "changed their review" if changed else "left a review"
    notify_admin(
        db,
        kind="review",
        title=f"{author} {verb} on {product.name if product else 'a bake'}",
        body=f"{review.rating} of 5 — {(review.comment or '').strip()[:180] or 'no note'}",
        link=f"/admin/reviews?focus={review.id}",
        background=background,
    )


def prune_notifications(db) -> int:
    """Sweep read pings past their retention (called once per boot).

    Unread pings are never deleted — the baker decides when they're read."""
    cutoff = utcnow() - timedelta(days=READ_RETENTION_DAYS)
    result = db.execute(
        delete(AdminNotification).where(
            AdminNotification.is_read.is_(True), AdminNotification.created_at < cutoff
        )
    )
    db.commit()
    return result.rowcount or 0
