"""Outgoing email.

SMTP is optional until the shop sets SMTP_HOST: without it every message
lands in the application log (links included) so flows stay testable and
nothing fails silently. One send function for whatever the shop emails
next — password resets today, newsletters and marketing later.
"""

import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings

logger = logging.getLogger("bakery.email")

# Deep wine from the storefront theme (oklch 45% 0.16 12), for email clients
# that only speak hex.
_BRAND = "#7f2230"


def send_email(to: str, subject: str, html: str, text: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning(
            "SMTP_HOST is not set — email to %s logged, not sent", to,
        )
        logger.info("email to %s | %s\n%s", to, subject, text)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)


def reset_password_email(first_name: str, link: str, minutes: int) -> tuple[str, str, str]:
    """The (subject, html, text) triple for a password-reset ask."""
    shop = get_settings().bakery_name
    subject = f"Reset your password — {shop}"
    text = (
        f"Hi {first_name},\n\n"
        f"Someone (hopefully you) asked to reset the password for your "
        f"{shop} account.\n\n"
        f"Open this link within {minutes} minutes to choose a new one:\n"
        f"{link}\n\n"
        f"If you didn't ask, ignore this email — your password stays as it is.\n"
    )
    html = f"""\
<div style="margin:0;padding:24px;background:#f6f1ee;font-family:Georgia,'Times New Roman',serif;color:#33322e;">
  <div style="max-width:480px;margin:0 auto;background:#fffdf9;border-radius:14px;padding:32px;
              border:1px solid #e7ddd6;">
    <p style="margin:0 0 4px;font-size:15px;color:{_BRAND};font-weight:bold;">{shop}</p>
    <h1 style="margin:0 0 12px;font-size:22px;color:#33322e;">Reset your password</h1>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">
      Hi {first_name}, someone asked to reset the password for your account.
      The button below works for the next {minutes} minutes.
    </p>
    <p style="margin:0 0 16px;">
      <a href="{link}"
         style="display:inline-block;background:{_BRAND};color:#fffdf9;text-decoration:none;
                padding:12px 28px;border-radius:999px;font-size:15px;font-weight:bold;">
        Choose a new password
      </a>
    </p>
    <p style="margin:0 0 4px;font-size:13px;line-height:1.6;color:#8a837a;">
      If the button doesn't work, paste this into your browser:<br>
      <a href="{link}" style="color:{_BRAND};word-break:break-all;">{link}</a>
    </p>
    <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#8a837a;">
      Didn't ask? Ignore this email — your password stays as it is.
    </p>
  </div>
</div>
"""
    return subject, html, text
