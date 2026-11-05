"""Outgoing email.

SMTP is optional until the shop sets SMTP_HOST: without it every message
lands in the application log (links included) so flows stay testable and
nothing fails silently. One send function for whatever the shop emails
next — password resets today, newsletters and marketing later.
"""

import logging
import smtplib
from email.message import EmailMessage
from html import escape

from .config import get_settings
from .money import money

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


def bulk_enquiry_ack_email(enquiry: dict) -> tuple[str, str, str]:
    """Confirmation to a catering lead that the enquiry landed."""
    shop = get_settings().bakery_name
    subject = f"We've got your enquiry — {shop}"
    event_date = enquiry["event_date"]
    text = (
        f"Hi {enquiry['name']},\n\n"
        f"Thank you for your enquiry about {enquiry['guests']} guests on "
        f"{event_date}. Djini will write or call back within one working day "
        f"with a quote, portions and delivery timing. Nothing is charged "
        f"until you say yes.\n\n"
        f"If anything changes in the meantime, just reply to this email.\n"
    )
    html = f"""\
<div style="margin:0;padding:24px;background:#f6f1ee;font-family:Georgia,'Times New Roman',serif;color:#33322e;">
  <div style="max-width:480px;margin:0 auto;background:#fffdf9;border-radius:14px;padding:32px;
              border:1px solid #e7ddd6;">
    <p style="margin:0 0 4px;font-size:15px;color:{_BRAND};font-weight:bold;">{shop}</p>
    <h1 style="margin:0 0 12px;font-size:22px;color:#33322e;">We've got your enquiry</h1>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">
      Hi {escape(enquiry['name'])}, thank you for thinking of us for your{' '}
      {enquiry['event_type']} — {enquiry['guests']} guests on {event_date}.
      Djini will write or call back within one working day with a quote,
      portions and delivery timing. Nothing is charged until you say yes.
    </p>
    <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#8a837a;">
      If anything changes in the meantime, just reply to this email.
    </p>
  </div>
</div>
"""
    return subject, html, text


def bulk_reply_email(reply: dict) -> tuple[str, str, str]:
    """The baker's answer to a catering lead, emailed to the enquirer."""
    shop = get_settings().bakery_name
    subject = f"Re: your enquiry for {reply['event_date']} — {shop}"
    # The baker's message is free text — escape it and keep its line breaks.
    message_html = escape(reply["message"]).replace("\n", "<br>")
    text = (
        f"Hi {reply['name']},\n\n"
        f"Djini replied to your enquiry ({reply['guests']} guests on "
        f"{reply['event_date']}):\n\n"
        f"\"{reply['message']}\"\n\n"
        f"Reply to this email to reach her directly.\n"
    )
    html = f"""\
<div style="margin:0;padding:24px;background:#f6f1ee;font-family:Georgia,'Times New Roman',serif;color:#33322e;">
  <div style="max-width:480px;margin:0 auto;background:#fffdf9;border-radius:14px;padding:32px;
              border:1px solid #e7ddd6;">
    <p style="margin:0 0 4px;font-size:15px;color:{_BRAND};font-weight:bold;">{shop}</p>
    <h1 style="margin:0 0 12px;font-size:22px;color:#33322e;">Djini wrote back</h1>
    <p style="margin:0 0 16px;font-size:13px;color:#8a837a;">
      About your enquiry — {reply['guests']} guests on {reply['event_date']}
    </p>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">
      Hi {escape(reply['name'])},<br><br>
      {message_html}
    </p>
    <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#8a837a;">
      Reply to this email to reach Djini directly.
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

    # The order's own money — receipts for historical orders stay in the
    # currency they were placed in, whatever the shop charges today (EUR for
    # every order since the 2026-11-05 cutover).
    currency = receipt.get("currency", "eur")

    def _amount(value: float) -> str:
        return money(value, currency)

    shipping_label = "FREE" if receipt["shipping"] == 0 else _amount(receipt["shipping"])
    item_rows = "".join(
        _row(f"{name} × {qty}", _amount(subtotal)) for name, qty, subtotal in lines
    )
    subtotal_row = _row("Subtotal", _amount(receipt["subtotal"]))
    discount_row = (
        _row(f"Discount ({receipt['coupon_code']})", f"−{_amount(receipt['discount'])}")
        if receipt["discount"]
        else ""
    )
    # No tax line at all while no rate is approved — a "Tax €0.00" row would
    # read as a 0%-VAT decision, and VAT treatment is explicitly pending.
    tax_row = _row("Tax", _amount(receipt["tax"])) if receipt["tax"] else ""
    delivery_row = _row("Delivery", shipping_label)
    total_row = _row("Total", _amount(receipt["total"]), bold=True)
    payment_line = (
        f"Paid by {payment} — this email is your proof of purchase."
        if paid
        else f"{payment} — pay the courier at your door."
    )

    text = (
        f"Hi {receipt['first_name']},\n\n"
        f"Here's your receipt for order {number}, placed {receipt['placed_at']}.\n\n"
        + "".join(f"  {name} × {qty}  {_amount(subtotal)}\n" for name, qty, subtotal in lines)
        + f"\n  Subtotal  {_amount(receipt['subtotal'])}\n"
        + (
            f"  Discount ({receipt['coupon_code']})  −{_amount(receipt['discount'])}\n"
            if receipt["discount"]
            else ""
        )
        + (f"  Tax  {_amount(receipt['tax'])}\n" if receipt["tax"] else "")
        + f"  Delivery  {shipping_label}\n"
        + f"  Total  {_amount(receipt['total'])}\n\n"
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


def admin_notification_email(title: str, body: str | None, link: str | None) -> tuple[str, str, str]:
    """The desk ping as an email — for the baker who isn't at the desk.

    Same card the customers get, minus anything only the admin console
    could act on: the link is shown as a desk path, not a public URL, so
    the email never carries a guessable absolute address."""
    shop = get_settings().bakery_name
    subject = f"{title} — {shop}"
    text = f"{title}\n\n{body or ''}"
    if link:
        text += f"\n\nAt the desk: {link}"
    text += "\n"
    link_html = (
        f'<p style="margin:0;font-size:13px;color:#8a837a;">At the desk: {escape(link)}</p>'
        if link
        else ""
    )
    html = f"""\
<div style="margin:0;padding:24px;background:#f6f1ee;font-family:Georgia,'Times New Roman',serif;color:#33322e;">
  <div style="max-width:480px;margin:0 auto;background:#fffdf9;border-radius:14px;padding:32px;
              border:1px solid #e7ddd6;">
    <p style="margin:0 0 4px;font-size:15px;color:{_BRAND};font-weight:bold;">{shop}</p>
    <h1 style="margin:0 0 12px;font-size:20px;color:#33322e;">{escape(title)}</h1>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">
      {escape(body) if body else ""}
    </p>
    {link_html}
  </div>
</div>
"""
    return subject, html, text


def send_admin_notification(to: str, title: str, body: str | None, link: str | None) -> None:
    """Queue-safe entry point for the mirrored desk ping."""
    try:
        subject, html, text = admin_notification_email(title, body, link)
        send_email(to, subject, html, text)
    except Exception:
        logger.exception("building the admin notification email failed")
