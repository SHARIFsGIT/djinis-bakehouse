"""Outgoing text messages — today, the sign-in codes for phone login.

Twilio's REST API through stdlib urllib, mirroring the email module's
discipline: no SDK, and unconfigured means log-only (the code lands in the
server log with the rest of the story) so a dev box never fails to send.
"""

import base64
import logging
import urllib.parse
import urllib.request

from .config import get_settings

logger = logging.getLogger("bakery.sms")


def configured() -> bool:
    settings = get_settings()
    return bool(
        settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number
    )


def send_sms(to: str, body: str) -> bool:
    """Deliver one text. A failed send logs loudly but never raises — the
    code still works from the server log, and the customer can ask again."""
    settings = get_settings()
    if not configured():
        logger.info("sms to %s | %s", to, body)
        return True
    data = urllib.parse.urlencode(
        {"From": settings.twilio_from_number, "To": to, "Body": body}
    ).encode()
    credentials = base64.b64encode(
        f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()
    ).decode()
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
        data=data,
        headers={"Authorization": f"Basic {credentials}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return True
    except Exception:
        logger.exception("sms to %s failed", to)
        return False
