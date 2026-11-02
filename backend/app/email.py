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
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except Exception:
        # A mail outage must never take down the request (or the background
        # task that sends this) — say it loud in the log instead.
        logger.exception("sending email to %s failed (subject: %s)", to, subject)


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


_PAYMENT_LABELS = {"card": "Card", "cash_on_delivery": "Cash on delivery"}


def order_receipt_email(receipt: dict) -> tuple[str, str, str]:
    """The (subject, html, text) triple for a paid order's receipt.

    `receipt` carries plain values only (order number, items, totals, …) so
    the background task never has to touch a database session.
    """
    shop = get_settings().bakery_name
    number = receipt["order_number"]
    subject = f"Receipt {number} — {shop}"
    payment = _PAYMENT_LABELS.get(receipt["payment_method"], receipt["payment_method"])
    paid = receipt["payment_status"] == "paid"

    lines = [
        (item["name"], item["quantity"], item["subtotal"]) for item in receipt["items"]
    ]

    def _row(label: str, value: str, bold: bool = False) -> str:
        style = (
            'font-weight:bold;border-top:1px dashed #e7ddd6;padding-top:8px;'
            if bold
            else ""
        )
        return (
            f'<tr><td style="padding:4px 0;{style}font-size:14px;color:#33322e;">'
            f"{label}</td>"
            f'<td style="padding:4px 0;text-align:right;{style}font-size:14px;'
            f'color:#33322e;">{value}</td></tr>'
        )

    def _usd(amount: float) -> str:
        return f"${amount:,.2f}"

    shipping_label = "FREE" if receipt["shipping"] == 0 else _usd(receipt["shipping"])
    item_rows = "".join(
        _row(f"{name} × {qty}", _usd(subtotal)) for name, qty, subtotal in lines
    )
    subtotal_row = _row("Subtotal", _usd(receipt["subtotal"]))
    discount_row = (
        _row(f"Discount ({receipt['coupon_code']})", f"−{_usd(receipt['discount'])}")
        if receipt["discount"]
        else ""
    )
    tax_row = _row("Tax", _usd(receipt["tax"]))
    delivery_row = _row("Delivery", shipping_label)
    total_row = _row("Total", _usd(receipt["total"]), bold=True)
    payment_line = (
        f"Paid by {payment} — this email is your proof of purchase."
        if paid
        else f"{payment} — pay the courier at your door."
    )

    text = (
        f"Hi {receipt['first_name']},\n\n"
        f"Here's your receipt for order {number}, placed {receipt['placed_at']}.\n\n"
        + "".join(f"  {name} × {qty}  {_usd(subtotal)}\n" for name, qty, subtotal in lines)
        + f"\n  Subtotal  {_usd(receipt['subtotal'])}\n"
        + (
            f"  Discount ({receipt['coupon_code']})  −{_usd(receipt['discount'])}\n"
            if receipt["discount"]
            else ""
        )
        + f"  Tax  {_usd(receipt['tax'])}\n"
        + f"  Delivery  {shipping_label}\n"
        + f"  Total  {_usd(receipt['total'])}\n\n"
        + f"{payment_line}\n\nThank you for baking with us.\n"
    )
    html = f"""\
<div style="margin:0;padding:24px;background:#f6f1ee;font-family:Georgia,'Times New Roman',serif;color:#33322e;">
  <div style="max-width:480px;margin:0 auto;background:#fffdf9;border-radius:14px;padding:32px;
              border:1px solid #e7ddd6;">
    <p style="margin:0 0 4px;font-size:15px;color:{_BRAND};font-weight:bold;">{shop}</p>
    <h1 style="margin:0 0 4px;font-size:22px;color:#33322e;">Your receipt</h1>
    <p style="margin:0 0 16px;font-size:13px;color:#8a837a;">
      Order {number} · placed {receipt['placed_at']}
    </p>
    <table width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 8px;">
      {item_rows}
      {subtotal_row}
      {discount_row}
      {tax_row}
      {delivery_row}
      {total_row}
    </table>
    <p style="margin:12px 0 0;font-size:14px;line-height:1.6;">
      {payment_line}
    </p>
    <p style="margin:16px 0 0;font-size:13px;color:#8a837a;">
      Thank you for baking with us, {receipt['first_name']}.
    </p>
  </div>
</div>
"""
    return subject, html, text


def send_order_receipt(receipt: dict) -> None:
    """Queue-safe entry point for the background task after checkout."""
    try:
        subject, html, text = order_receipt_email(receipt)
        send_email(receipt["email"], subject, html, text)
    except Exception:
        logger.exception("building the receipt email for %s failed", receipt.get("order_number"))
