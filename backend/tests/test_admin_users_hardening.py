"""The security review's follow-ups for the desk's Users page (2026-11-25):
the audit trail, "no-store" on the desk's answers, and the refusal paths the
suite did not pin down yet — each refusal checked to change nothing.

Rides on test_api.py's client (imported first, so the app boots once, on the
test database). Every account is a fresh throwaway; whatever a test
promotes, grants or switches off it puts back, so test_api's "last admin"
and test_marketing's exact consent count still hold.
"""

import base64
import json
import logging
import re
import secrets
import time

import jwt

from test_api import admin_headers, auth_header, client  # first: it points the app at the test database

from app import audit
from app.config import get_settings
from app.database import SessionLocal
from app.models import ClassificationEvent, Suppression, User

DOMAIN = "@hardening.example.com"
PASSWORD = "Passw0rd!"
# An audit line carries ids, short codes and a time — nothing else fits.
AUDIT_LINE = re.compile(
    r"audit action=[a-z_.]+ admin_id=\d+ target_id=\d+ before=[A-Za-z0-9_.-]+ after=[A-Za-z0-9_.-]+ at=\S+"
)


def _account(name: str) -> dict:
    """A fresh customer: its id, address and both of its tokens."""
    email = f"{name}.{secrets.token_hex(3)}{DOMAIN}"
    made = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "first_name": name.title(), "last_name": "Probe"},
    )
    assert made.status_code in (200, 201), made.text
    body = made.json()
    return {"id": body["user"]["id"], "email": email, "access": body["access_token"], "refresh": body["refresh_token"]}


def _admin_id(headers: dict) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _state(user_id: int) -> tuple:
    """Everything a refused call must leave as it was."""
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return (u.role, u.is_active, u.classification, u.marketing_consent_at, u.reset_token_hash, u.token_version)
    finally:
        db.close()


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "bakery.audit"]


def _put_back(user_id: int, headers: dict) -> None:
    """A test's account as it found it: an active customer. Reactivated
    first, since the desk refuses to demote an admin while it is switched
    off and only one other admin is active."""
    client.put(f"/api/admin/users/{user_id}", json={"is_active": True}, headers=headers)
    client.put(f"/api/admin/users/{user_id}", json={"role": "customer"}, headers=headers)


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def _unsigned(claims: dict) -> str:
    part = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


# --------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------


def test_role_and_activation_changes_are_audited(caplog):
    caplog.set_level(logging.INFO, logger="bakery.audit")
    admin = admin_headers()
    admin_id = _admin_id(admin)
    target = _account("audited")
    try:
        for body in ({"role": "admin"}, {"is_active": False}, {"is_active": True}, {"role": "customer"}):
            assert client.put(f"/api/admin/users/{target['id']}", json=body, headers=admin).status_code == 200
        # A write that changes nothing is not an event.
        assert client.put(f"/api/admin/users/{target['id']}", json={"role": "customer"}, headers=admin).status_code == 200

        lines = _audit_lines(caplog)
        assert all(AUDIT_LINE.fullmatch(line) for line in lines), lines
        who = f"admin_id={admin_id} target_id={target['id']}"
        assert [line.split(" at=")[0] for line in lines] == [
            f"audit action=user.role_changed {who} before=customer after=admin",
            f"audit action=user.deactivated {who} before=active after=inactive",
            f"audit action=user.activated {who} before=inactive after=active",
            f"audit action=user.role_changed {who} before=admin after=customer",
        ]
    finally:
        _put_back(target["id"], admin)


def test_classification_changes_are_audited(caplog):
    caplog.set_level(logging.INFO, logger="bakery.audit")
    admin = admin_headers()
    admin_id = _admin_id(admin)
    target = _account("worded")
    for word in ("vip", "loyal", None, None):  # the last write is an echo — no event
        response = client.put(
            f"/api/admin/users/{target['id']}/classification", json={"classification": word}, headers=admin
        )
        assert response.status_code == 200, response.text
    lines = _audit_lines(caplog)
    assert all(AUDIT_LINE.fullmatch(line) for line in lines), lines
    who = f"admin_id={admin_id} target_id={target['id']}"
    assert [line.split(" at=")[0] for line in lines] == [
        f"audit action=user.classification_changed {who} before=none after=vip",
        f"audit action=user.classification_changed {who} before=vip after=loyal",
        f"audit action=user.classification_changed {who} before=loyal after=none",
    ]


def test_consent_is_audited_without_its_source_or_evidence(caplog):
    caplog.set_level(logging.INFO)
    admin = admin_headers()
    admin_id = _admin_id(admin)
    target = _account("consented")
    source = "counter, Maria"
    evidence = "Signed card from Maria Musterfrau, 0176 555 0100, kept in the till"
    try:
        grant = client.put(
            f"/api/admin/users/{target['id']}/marketing-consent",
            json={"granted": True, "source": source, "evidence": evidence},
            headers=admin,
        )
        assert grant.status_code == 200, grant.text
        withdraw = client.put(
            f"/api/admin/users/{target['id']}/marketing-consent", json={"granted": False}, headers=admin
        )
        assert withdraw.status_code == 200, withdraw.text

        lines = _audit_lines(caplog)
        assert all(AUDIT_LINE.fullmatch(line) for line in lines), lines
        who = f"admin_id={admin_id} target_id={target['id']}"
        assert [line.split(" at=")[0] for line in lines] == [
            f"audit action=user.marketing_consent_granted {who} before=no after=yes",
            f"audit action=user.marketing_consent_withdrawn {who} before=yes after=no",
        ]
        # Nothing the desk typed about the person reaches any log line, and
        # the audit lines name the account by its id alone.
        for private in (evidence, "Musterfrau", source):
            assert private not in caplog.text, private
        assert not any(target["email"] in line for line in lines)
    finally:
        db = SessionLocal()
        try:
            user = db.get(User, target["id"])
            user.marketing_consent_at = user.marketing_consent_source = user.marketing_consent_evidence = None
            for row in db.query(Suppression).filter(Suppression.email == target["email"]):
                db.delete(row)
            db.commit()
        finally:
            db.close()


def test_an_audit_line_cannot_be_forged_by_a_stray_value(caplog):
    caplog.set_level(logging.INFO, logger="bakery.audit")
    audit.record("user.role_changed", admin_id=1, target_id=2, before="customer\naudit action=fake", after="admin x=1")
    (line,) = _audit_lines(caplog)
    assert AUDIT_LINE.fullmatch(line), line
    assert "\n" not in line and " x=1" not in line


# --------------------------------------------------------------------------
# No copies of the desk's answers
# --------------------------------------------------------------------------


def test_the_desks_answers_are_never_stored():
    admin = admin_headers()
    for path in ("/api/admin/users", "/api/admin/users/export", "/api/admin/orders", "/api/admin/stats"):
        response = client.get(path, headers=admin)
        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-store", path
    # A refusal says the same — there is nothing in it worth keeping either.
    assert client.get("/api/admin/users").headers.get("cache-control") == "no-store"
    # The storefront's own caching is left exactly as it was.
    for path in ("/api/store", "/api/products"):
        assert "no-store" not in client.get(path).headers.get("cache-control", ""), path


# --------------------------------------------------------------------------
# Refusals — and nothing changed behind them
# --------------------------------------------------------------------------


def test_role_and_activation_changes_need_a_desk_login():
    target = _account("unsigned")
    before = _state(target["id"])
    for body in ({"role": "admin"}, {"is_active": False}):
        assert client.put(f"/api/admin/users/{target['id']}", json=body).status_code == 401
        # …and a customer can't do it either, not even to their own account
        assert client.put(
            f"/api/admin/users/{target['id']}", json=body, headers=auth_header(target["access"])
        ).status_code == 403
    assert _state(target["id"]) == before


def test_a_customer_cannot_touch_marketing_consent():
    target = _account("consent.target")
    mallory = _account("mallory")
    before = _state(target["id"])
    for body in ({"granted": True, "source": "the till", "evidence": "trust me"}, {"granted": False}):
        for whose in (target["id"], mallory["id"]):
            path = f"/api/admin/users/{whose}/marketing-consent"
            assert client.put(path, json=body, headers=auth_header(mallory["access"])).status_code == 403
            assert client.put(path, json=body).status_code == 401
    assert _state(target["id"]) == before
    db = SessionLocal()
    try:
        assert db.query(Suppression).filter(Suppression.email == target["email"]).count() == 0
    finally:
        db.close()


def test_classification_needs_an_admin_whoever_asks():
    target = _account("unworded")
    mallory = _account("mallory.word")
    admin_token = admin_headers()["Authorization"].split()[1]
    wrong_key = jwt.encode(_claims(admin_token), "not-the-shop-secret-0123456789abcdef", algorithm="HS256")
    before = _state(target["id"])
    for headers, expected in (
        ({}, 401),
        (auth_header(mallory["access"]), 403),
        (auth_header(mallory["refresh"]), 401),
        (auth_header(wrong_key), 401),
        (auth_header(_unsigned(_claims(admin_token))), 401),
    ):
        response = client.put(
            f"/api/admin/users/{target['id']}/classification", json={"classification": "vip"}, headers=headers
        )
        assert response.status_code == expected, (headers, response.status_code)
    assert _state(target["id"]) == before
    db = SessionLocal()
    try:
        assert db.query(ClassificationEvent).filter(ClassificationEvent.user_id == target["id"]).count() == 0
    finally:
        db.close()


def test_a_refresh_token_is_not_an_access_token():
    login = client.post("/api/auth/login", json={"email": "admin@djinisbakehouse.com", "password": "Admin123!"}).json()
    target = _account("refreshed")
    before = _state(target["id"])
    refresh = auth_header(login["refresh_token"])
    assert client.get("/api/admin/users", headers=refresh).status_code == 401
    assert client.get("/api/admin/users/export", headers=refresh).status_code == 401
    assert client.put(f"/api/admin/users/{target['id']}", json={"role": "admin"}, headers=refresh).status_code == 401
    assert _state(target["id"]) == before
    # The same login's access token is let in — the refusal is about the type.
    assert client.get("/api/admin/users", headers=auth_header(login["access_token"])).status_code == 200


def test_forged_and_unsigned_tokens_are_refused():
    admin_token = admin_headers()["Authorization"].split()[1]
    claims = _claims(admin_token)
    settings = get_settings()
    header, payload, signature = admin_token.split(".")
    someone_else = base64.urlsafe_b64encode(json.dumps({**claims, "sub": "1"}).encode()).rstrip(b"=").decode()
    for label, token in (
        ("wrong key", jwt.encode(claims, "not-the-shop-secret-0123456789abcdef", algorithm="HS256")),
        ("unsigned", _unsigned(claims)),
        ("payload swapped under a real signature", f"{header}.{someone_else}.{signature}"),
        ("expired", jwt.encode({**claims, "exp": int(time.time()) - 60}, settings.secret_key, algorithm=settings.algorithm)),
        ("garbage", "not.a.token"),
    ):
        for method, path in (("GET", "/api/admin/users"), ("GET", "/api/admin/users/export")):
            assert client.request(method, path, headers=auth_header(token)).status_code == 401, label


def test_a_demoted_admins_old_token_opens_nothing_at_the_desk():
    admin = admin_headers()
    former = _account("former.admin")
    bystander = _account("bystander")
    try:
        assert client.put(f"/api/admin/users/{former['id']}", json={"role": "admin"}, headers=admin).status_code == 200
        old = auth_header(
            client.post("/api/auth/login", json={"email": former["email"], "password": PASSWORD}).json()["access_token"]
        )
        assert client.get("/api/admin/users", headers=old).status_code == 200  # an admin, for now
        assert client.put(f"/api/admin/users/{former['id']}", json={"role": "customer"}, headers=admin).status_code == 200

        before = _state(bystander["id"])
        assert client.get("/api/admin/users", headers=old).status_code == 403
        assert client.get("/api/admin/users/export", headers=old).status_code == 403
        assert client.put(f"/api/admin/users/{bystander['id']}", json={"role": "admin"}, headers=old).status_code == 403
        assert client.put(
            f"/api/admin/users/{bystander['id']}/classification", json={"classification": "vip"}, headers=old
        ).status_code == 403
        assert _state(bystander["id"]) == before
        # The token itself still signs them in — as the customer they now are.
        assert client.get("/api/auth/me", headers=old).json()["role"] == "customer"

        # Switched off instead: the same old token stops working altogether.
        assert client.put(f"/api/admin/users/{former['id']}", json={"role": "admin"}, headers=admin).status_code == 200
        again = auth_header(
            client.post("/api/auth/login", json={"email": former["email"], "password": PASSWORD}).json()["access_token"]
        )
        assert client.put(f"/api/admin/users/{former['id']}", json={"is_active": False}, headers=admin).status_code == 200
        assert client.get("/api/admin/users", headers=again).status_code == 401
    finally:
        _put_back(former["id"], admin)


def test_tier_codes_need_an_admin():
    admin = admin_headers()
    mallory = _account("mallory.codes")
    before = client.get("/api/admin/loyalty-vouchers", headers=admin).json()
    for tier in ("vip", "loyal"):
        path = f"/api/admin/loyalty-vouchers/{tier}"
        assert client.put(path, json={"coupon_id": None}).status_code == 401
        assert client.put(path, json={"coupon_id": None}, headers=auth_header(mallory["access"])).status_code == 403
        assert client.put(path, json={"coupon_id": None}, headers=auth_header(mallory["refresh"])).status_code == 401
    assert client.get("/api/admin/loyalty-vouchers", headers=auth_header(mallory["access"])).status_code == 403
    assert client.get("/api/admin/loyalty-vouchers", headers=admin).json() == before
