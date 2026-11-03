"""Facebook's Login dialog and Graph API, behind one module so the rest of
the app never builds a Facebook URL or parses a Graph response — the same
discipline as the Stripe gateway.

Stdlib only (urllib): no SDK to install, no extra dependency to deploy.
Tests swap these functions for fakes; production talks to the real Graph.
"""

import json
import logging
import urllib.parse
import urllib.request

from .config import get_settings

logger = logging.getLogger("bakery.facebook")

GRAPH_BASE = "https://graph.facebook.com"
DIALOG_BASE = "https://www.facebook.com"


class FacebookError(RuntimeError):
    """A call to Facebook failed — the message is safe to log, never to show."""


def configured() -> bool:
    settings = get_settings()
    return bool(settings.facebook_app_id and settings.facebook_app_secret)


def authorize_url(redirect_uri: str, state: str) -> str:
    """The browser's first hop: Facebook's Login dialog."""
    settings = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": settings.facebook_app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "email,public_profile",
            "response_type": "code",
        }
    )
    return f"{DIALOG_BASE}/{settings.facebook_api_version}/dialog/oauth?{query}"


def _get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode())
    except Exception as exc:  # HTTP errors, timeouts, malformed JSON — one door
        logger.warning("graph call failed (%s)", type(exc).__name__)
        raise FacebookError("graph call failed") from exc


def exchange_code(code: str, redirect_uri: str) -> str:
    """Swap the dialog's one-time code for a short-lived user access token."""
    settings = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": settings.facebook_app_id,
            "client_secret": settings.facebook_app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
    )
    data = _get_json(f"{GRAPH_BASE}/{settings.facebook_api_version}/oauth/access_token?{query}")
    token = data.get("access_token")
    if not token:
        raise FacebookError("no access token in the exchange response")
    return token


def fetch_profile(access_token: str) -> dict:
    """The fields the shop needs: Facebook's user id, name and email."""
    settings = get_settings()
    query = urllib.parse.urlencode({"fields": "id,name,email", "access_token": access_token})
    data = _get_json(f"{GRAPH_BASE}/{settings.facebook_api_version}/me?{query}")
    if not data.get("id"):
        raise FacebookError("no id in the profile response")
    return data
