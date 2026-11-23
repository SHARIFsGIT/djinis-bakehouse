"""Text sign-in codes through Bird Verify (EU).

Bird generates every code, sends it by SMS and checks what the customer
types. The shop never sees a code: it asks Bird to text one to a number,
and later asks Bird whether the digits the customer typed are right.
Nothing about a code is generated, stored, logged or returned here.

Two calls, both keyed by the phone number itself — there is no
verification id to keep (Bird's API reference, "Create a verification"
and "Create a verification passcode check":
https://bird.com/docs/api/reference/create-verification and
https://bird.com/docs/api/reference/create-verification-check):

    POST {base}/v1/verify/verifications
        {"to": {"phone_number": "+49…"},
         "options": {"channels": ["sms"], "code_length": 6}}

    POST {base}/v1/verify/verifications/check
        {"to": {"phone_number": "+49…"}, "code": "123456"}

with the workspace key as a bearer token. `options.channels` narrows the
delivery plan to SMS alone: left out, Bird tries WhatsApp before SMS in
most countries, and with SMS unusable it answers 422 instead of falling
back to anything else.

Everything that is not a clear, well-formed answer is "unavailable": the
caller then signs nobody in and confirms nothing. Logs carry Bird's error
code and request id and at most a masked number — never a key, a code or a
whole phone number.

Stdlib only (urllib), like the PayPal and Facebook gateways.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import phones
from .config import get_settings

logger = logging.getLogger("bakery.sms")

class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Bird answers where it is asked; a redirect is never followed. Left to
    itself urllib would send the request again — bearer key included — to
    wherever a 301/302/303 points. Refused here, the redirect surfaces as
    the 3xx answer it is, and the gateway treats that as no answer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirects())

# The one door to the network. The test suite shuts it for every test
# (backend/conftest.py), so no test can ever reach Bird.
_urlopen = _OPENER.open

# A customer is waiting on the other end of both calls.
REQUEST_TIMEOUT_SECONDS = 10

CREATE_PATH = "/v1/verify/verifications"
CHECK_PATH = "/v1/verify/verifications/check"

# Six digits — Bird's default, pinned on every request so a change in the
# dashboard can never drift away from the six-digit field the shop shows.
CODE_LENGTH = 6

# A workspace key names its region in its prefix, and works only against
# that region's host (any other host answers 421). Organization master keys
# (bm_…) need a workspace header the shop does not send, so they are not
# accepted here.
_KEY_REGION = re.compile(r"^bk_(eu1|us1)_")
_REGION_HOSTS = {
    "eu1": "https://eu1.platform.bird.com",
    "us1": "https://us1.platform.bird.com",
}

# What a check comes back as.
VERIFIED = "verified"  # Bird confirms the code, for this very number
WRONG = "wrong"  # not the code; the verification is still open
EXPIRED = "expired"  # the ten minutes ran out
EXHAUSTED = "exhausted"  # too many wrong codes
GONE = "gone"  # nothing open for this number any more (or never was)


class Unavailable(Exception):
    """Bird could not be asked, or its answer can't be trusted. Nothing is
    sent, confirmed or signed in on the strength of it."""


class Refused(Exception):
    """Bird will not text a code to this number: not a usable recipient, no
    usable SMS route, or the verification came back blocked or ended."""


class TooMany(Exception):
    """Bird's own per-number caps (429): five sends an hour, ten checks a
    minute. Back off."""


@dataclass(frozen=True)
class Checked:
    outcome: str
    attempts_remaining: int | None = None


def api_base() -> str:
    return get_settings().bird_api_base.strip().rstrip("/")


def misconfigured() -> str | None:
    """Why text sign-in is off although a key is set — for the boot log —
    or None when there is nothing to say. Never names the key itself."""
    key = get_settings().bird_api_key.strip()
    if not key:
        return None
    region = _KEY_REGION.match(key)
    if region is None:
        return (
            "BIRD_API_KEY is not a Bird workspace key (bk_eu1_… or bk_us1_…). "
            "Text sign-in stays OFF."
        )
    host = _REGION_HOSTS[region.group(1)]
    if api_base() != host:
        return (
            f"BIRD_API_KEY belongs to Bird's {region.group(1)} region, which answers only at "
            f"{host}, but BIRD_API_BASE is {api_base()!r}. Text sign-in stays OFF until they match."
        )
    return None


def configured() -> bool:
    """Text sign-in is on where a usable key is set — and nowhere else."""
    return bool(get_settings().bird_api_key.strip()) and misconfigured() is None


def _log_answer(path: str, status: int, data: dict, level: int = logging.WARNING) -> None:
    """What Bird's error docs say to log: the code, name and request id. Never
    the message or the details — they may repeat what was sent."""
    error = data.get("error")
    error = error if isinstance(error, dict) else {}
    logger.log(
        level,
        "Bird %s answered %s %s %s (request %s)",
        path,
        status,
        error.get("code") or "-",
        error.get("name") or "-",
        error.get("request_id") or "-",
    )


def _post(path: str, body: dict) -> tuple[int, dict]:
    """One authenticated JSON call. Returns (status, body) for any answer,
    Bird's error envelope included; raises Unavailable when none came or it
    could not be read."""
    if not configured():
        raise Unavailable("Bird Verify is not configured")
    request = urllib.request.Request(
        api_base() + path,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {get_settings().bird_api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except OSError:
            raw = b""
    except Exception as exc:  # timeouts, DNS, TLS: one door
        logger.warning("Bird %s failed (%s)", path, type(exc).__name__)
        raise Unavailable("Bird did not answer") from None
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        logger.warning("Bird %s answered %s with a body that is not a JSON object", path, status)
        raise Unavailable("Bird's answer could not be read")
    return status, data


def _not_ok(path: str, status: int, data: dict) -> Exception:
    """The exception for an answer that is neither a result nor a refusal the
    caller handles itself."""
    if status == 429:
        _log_answer(path, status, data)
        return TooMany("Bird is holding back sends or checks for this number")
    if status in (401, 403, 421):
        _log_answer(path, status, data, logging.ERROR)
        hint = {
            401: "the API key is missing, unknown or revoked",
            403: "the API key lacks the Verify read/write permission",
            421: "the API key's region does not match BIRD_API_BASE",
        }[status]
        logger.error("Bird Verify refused the shop's credentials: %s", hint)
        return Unavailable(hint)
    _log_answer(path, status, data, logging.ERROR)
    return Unavailable(f"Bird answered {status}")


def start(phone: str) -> None:
    """Have Bird text a sign-in code to `phone` (E.164), by SMS and nothing
    else. Asking again for the same number reuses the verification in
    progress: inside Bird's resend cooldown nothing new is sent, and every
    code already sent stays valid until the verification ends.

    Raises Refused (no code can go to this number), TooMany (Bird's caps)
    or Unavailable (no trustworthy answer)."""
    status, data = _post(
        CREATE_PATH,
        {
            "to": {"phone_number": phone},
            "options": {"channels": ["sms"], "code_length": CODE_LENGTH},
        },
    )
    if status == 422:
        # An invalid recipient, or no usable channel once the plan is
        # narrowed to SMS: nothing was sent.
        _log_answer(CREATE_PATH, status, data)
        raise Refused("Bird will not text a code to this number")
    if status != 200:
        raise _not_ok(CREATE_PATH, status, data)

    to = data.get("to")
    if not isinstance(to, dict) or to.get("phone_number") != phone:
        logger.error("Bird's verification answer names another recipient")
        raise Unavailable("Bird's answer was not understood")
    channels = data.get("channels")
    plan = [c.get("channel") for c in channels if isinstance(c, dict)] if isinstance(channels, list) else []
    if not plan or any(channel != "sms" for channel in plan) or data.get("last_channel") not in (None, "sms"):
        # The request asked for SMS alone. A plan with anything else means
        # Bird did not honour that — say so loudly and trust nothing.
        logger.error("Bird planned a channel other than SMS for %s", phones.masked(phone))
        raise Unavailable("Bird's delivery plan is not SMS-only")

    state = data.get("status")
    if state == "pending":
        logger.info("text code on its way to %s (sms)", phones.masked(phone))
        return
    if state in ("blocked", "failed", "canceled", "expired"):
        # Fraud controls, or a verification that ended before any code
        # could be used: no code is coming.
        logger.warning("Bird's verification for %s came back %s", phones.masked(phone), state)
        raise Refused(f"the verification came back {state}")
    logger.error("Bird's verification came back in an unexpected state")
    raise Unavailable("Bird's answer was not understood")


def send_quietly(phone: str) -> None:
    """start(), for a background task: whatever happens is logged and nothing
    is raised — the customer already has their (deliberately uniform)
    answer."""
    try:
        start(phone)
    except TooMany:
        logger.warning("text code to %s held back by Bird's per-number cap", phones.masked(phone))
    except Refused:
        logger.warning("Bird would not text a code to %s", phones.masked(phone))
    except Unavailable as exc:
        logger.error("text code to %s not sent: %s", phones.masked(phone), exc)
    except Exception:  # a background task must never die loudly over a text
        logger.exception("text code to %s not sent", phones.masked(phone))


def check(phone: str, code: str) -> Checked:
    """Ask Bird whether `code` is right for `phone` (the same E.164 number
    the code was sent to). Only VERIFIED means yes. A wrong code is a normal
    answer, not an error.

    Raises TooMany (checked too quickly) or Unavailable (no trustworthy
    answer) — in both cases nothing is confirmed."""
    status, data = _post(CHECK_PATH, {"to": {"phone_number": phone}, "code": code})
    if status in (404, 422):
        # 404 E13000: no verification is open for this number — none was
        # started, or it already ended (verified, expired, out of tries).
        # Bird is explicit that this is never evidence of success.
        _log_answer(CHECK_PATH, status, data, logging.INFO)
        return Checked(GONE)
    if status != 200:
        raise _not_ok(CHECK_PATH, status, data)

    success = data.get("success")
    verification = data.get("verification")
    if not isinstance(success, bool) or not isinstance(verification, dict):
        logger.error("Bird's check answer has no success flag or verification")
        raise Unavailable("Bird's answer was not understood")
    to = verification.get("to")
    if not isinstance(to, dict) or to.get("phone_number") != phone:
        logger.error("Bird's check answer names another recipient")
        raise Unavailable("Bird's answer was not understood")
    state = verification.get("status")
    if success:
        if state == "verified":
            logger.info("text code confirmed for %s", phones.masked(phone))
            return Checked(VERIFIED)
        # success without a verified verification contradicts the contract
        logger.error("Bird's check said success, but the verification is %r", state)
        raise Unavailable("Bird's answer was not understood")

    remaining = data.get("attempts_remaining")
    if not isinstance(remaining, int) or isinstance(remaining, bool):
        remaining = None
    reason = data.get("reason")
    if state == "pending":
        # incorrect_code — or a reason Bird adds later; either way, not yes
        return Checked(WRONG, remaining)
    if reason == "expired" or state == "expired":
        return Checked(EXPIRED)
    if reason == "attempts_exhausted" or state == "failed":
        return Checked(EXHAUSTED)
    return Checked(GONE)  # canceled, blocked, or a final state added later
