"""The desk's Users page: the roll's summary, its new filters (contact,
role, status, consent) and A-to-Z order, the customer spreadsheet, and one
customer's orders exactly.

Rides on test_api.py's client (imported, so the app boots once whichever
file pytest reaches first). Every account here is a fresh, uniquely named
throwaway in the test database, and every count is compared before and
after, so the assertions hold in the full suite and alone. What a test
grants or switches off it puts back: test_marketing.py counts the shop's
consenters exactly, so none may leak from here.
"""

import csv
import io
import secrets

import test_api as api  # first: it points the app at the test database
from test_api import admin_headers, auth_header, checkout_payload, client

from app.database import SessionLocal
from app.models import User

DOMAIN = "@usersdesk.example.com"
PASSWORD = "Passw0rd!"


def _roll(**params) -> dict:
    response = client.get("/api/admin/users", params=params, headers=admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _only_id(search: str) -> int:
    rows = _roll(search=search, per_page=50)["items"]
    assert len(rows) == 1, rows
    return rows[0]["id"]


def _phone_account(first_name: str) -> str:
    number = "+49176" + "".join(secrets.choice("0123456789") for _ in range(7))
    response = client.post(
        "/api/auth/register",
        json={"phone": number, "password": PASSWORD, "first_name": first_name},
    )
    assert response.status_code in (200, 201), response.text
    return number


def _grant_consent(user_id: int) -> None:
    response = client.put(
        f"/api/admin/users/{user_id}/marketing-consent",
        json={"granted": True, "source": "at the counter", "evidence": "Signed card on file"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text


def _put_back(user_id: int) -> None:
    """The account as it was before the test touched it: no consent, active.
    Straight in the book — the desk's own withdraw would also file a
    suppression, which is a change of its own."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        user.marketing_consent_at = None
        user.marketing_consent_source = None
        user.marketing_consent_evidence = None
        user.is_active = True
        db.commit()
    finally:
        db.close()


def test_the_summary_counts_live_customers_whatever_the_filters():
    before = _roll()["summary"]
    api.register_and_login(f"sum.mail{DOMAIN}", PASSWORD)
    _phone_account("Sumphone")
    after = _roll()["summary"]

    assert after["customers"] == before["customers"] + 2
    assert after["new_last_30_days"] == before["new_last_30_days"] + 2
    assert after["phone_only"] == before["phone_only"] + 1
    assert after["admins"] >= 1
    assert after["currency"]

    mail_id = _only_id("sum.mail")
    try:
        _grant_consent(mail_id)
        assert _roll()["summary"]["marketing_consent"] == after["marketing_consent"] + 1

        # The figures speak for the whole roll: a search that finds nobody, or
        # a filter, never changes them.
        whole = _roll()["summary"]
        assert _roll(search="no-such-customer-anywhere")["summary"] == whole
        assert _roll(role="admin", contact="phone")["summary"] == whole
        # ...and they add up: nobody orders twice without having ordered once.
        assert whole["returning"] <= whole["ordered"] <= whole["customers"]
    finally:
        _put_back(mail_id)
    assert _roll()["summary"]["marketing_consent"] == after["marketing_consent"]


def test_the_roll_narrows_by_contact_role_status_and_consent():
    phone = _phone_account("Narrowphone")
    api.register_and_login(f"narrow.mail{DOMAIN}", PASSWORD)
    mail_id = _only_id("narrow.mail")

    phone_rows = _roll(contact="phone", per_page=50)["items"]
    assert phone_rows and all(row["email"] is None for row in phone_rows)
    assert any(row["phone"] and row["phone"].endswith(phone[-7:]) for row in phone_rows)
    assert all(row["email"] for row in _roll(contact="email", per_page=50)["items"])

    admins = _roll(role="admin", per_page=50)["items"]
    assert admins and all(row["role"] == "admin" for row in admins)
    assert all(row["role"] == "customer" for row in _roll(role="customer", per_page=50)["items"])

    try:
        # Deactivated at the desk: found under status=inactive, gone from active.
        assert client.put(
            f"/api/admin/users/{mail_id}", json={"is_active": False}, headers=admin_headers()
        ).status_code == 200
        inactive = _roll(status="inactive", search="narrow.mail", per_page=50)["items"]
        assert [row["id"] for row in inactive] == [mail_id]
        assert _roll(status="active", search="narrow.mail", per_page=50)["items"] == []
        assert all(not row["is_active"] for row in _roll(status="inactive", per_page=50)["items"])

        _grant_consent(mail_id)
        assert [row["id"] for row in _roll(consent="yes", search="narrow.mail")["items"]] == [mail_id]
        assert _roll(consent="no", search="narrow.mail")["items"] == []
    finally:
        _put_back(mail_id)

    # Words the roll doesn't know are refused, not ignored.
    for bad in ({"contact": "fax"}, {"role": "baker"}, {"status": "asleep"}, {"consent": "maybe"}, {"sort": "height"}):
        assert client.get("/api/admin/users", params=bad, headers=admin_headers()).status_code == 422


def test_name_order_reads_a_to_z_whatever_the_case():
    for first in ("Zelda", "anna", "Mia"):
        token = api.register_and_login(f"abc.{first.lower()}{DOMAIN}", PASSWORD)
        response = client.put(
            "/api/auth/me", json={"first_name": first, "last_name": "Order"}, headers=auth_header(token)
        )
        assert response.status_code == 200, response.text
    names = [row["first_name"] for row in _roll(search="abc.", sort="name", per_page=50)["items"]]
    assert names == ["anna", "Mia", "Zelda"]


def test_the_spreadsheet_is_the_filtered_roll_and_defuses_names():
    token = api.register_and_login(f"csv.formula{DOMAIN}", PASSWORD)
    client.put(
        "/api/auth/me", json={"first_name": "=1+1", "last_name": "Baker"}, headers=auth_header(token)
    )
    api.register_and_login(f"csv.plain{DOMAIN}", PASSWORD)

    response = client.get(
        "/api/admin/users/export", params={"search": "csv.formula"}, headers=admin_headers()
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "bakehouse-customers.csv" in response.headers["content-disposition"]
    header, *rows = list(csv.reader(io.StringIO(response.text)))
    assert header[:4] == ["first_name", "last_name", "email", "phone"]
    assert len(rows) == 1  # the search narrowed it, exactly as on the page
    row = dict(zip(header, rows[0]))
    assert row["first_name"] == "'=1+1"  # shown by the spreadsheet, never run
    assert row["email"] == f"csv.formula{DOMAIN}"
    assert row["role"] == "customer" and row["active"] == "yes"
    assert row["orders"] == "0" and row["lifetime_spend"] == "0.00"

    # Both addresses when the filter allows both; never for a customer.
    both = list(csv.reader(io.StringIO(client.get(
        "/api/admin/users/export", params={"search": "csv.", "sort": "name"}, headers=admin_headers()
    ).text)))
    assert len(both) == 3
    assert client.get("/api/admin/users/export", headers=auth_header(token)).status_code == 403


def test_a_customers_card_lists_their_orders_exactly():
    # "ann.k" is inside "joann.k": a search would catch both customers.
    mine = api.register_and_login(f"ann.k{DOMAIN}", PASSWORD)
    theirs = api.register_and_login(f"joann.k{DOMAIN}", PASSWORD)
    api._top_up_stock("butter-croissant")
    api._top_up_stock("beef-curry-puff")
    placed = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=auth_header(mine)
    )
    other = client.post(
        "/api/orders", json=checkout_payload(payment_method="cash_on_delivery"), headers=auth_header(theirs)
    )
    assert placed.status_code == 201, placed.text
    assert other.status_code == 201, other.text

    searched = client.get("/api/admin/orders", params={"search": "ann.k"}, headers=admin_headers()).json()
    assert len(searched["orders"]) == 2

    ann = next(row for row in _roll(search="ann.k", per_page=50)["items"] if row["email"] == f"ann.k{DOMAIN}")
    exact = client.get("/api/admin/orders", params={"user_id": ann["id"]}, headers=admin_headers()).json()
    assert [order["order_number"] for order in exact["orders"]] == [placed.json()["order_number"]]
    assert exact["pagination"]["total_items"] == 1
    assert client.get("/api/admin/orders", params={"user_id": 0}, headers=admin_headers()).status_code == 422


def _found(search: str) -> set[int]:
    return {row["id"] for row in _roll(search=search, per_page=50)["items"]}


def _german_mobile() -> tuple[str, str]:
    """A fresh mobile number's seven last digits, and how the shop stores it."""
    tail = "".join(secrets.choice("0123456789") for _ in range(7))
    return tail, f"+49176{tail}"


def test_a_number_is_found_however_it_is_written():
    tail, stored = _german_mobile()
    made = client.post(
        "/api/auth/register",
        json={"phone": f"0176 {tail[:3]} {tail[3:]}", "password": PASSWORD, "first_name": "Searchy"},
    )
    assert made.status_code in (200, 201), made.text
    assert made.json()["user"]["phone"] == stored  # stored as it always was
    me = made.json()["user"]["id"]

    for typed in (
        f"0176 {tail[:3]} {tail[3:]}",  # at home
        stored,  # as stored
        f"+49 176 {tail[:3]} {tail[3:]}",  # abroad
        f"0049 176 {tail}",  # an old business card
        f"+49 (0)176 {tail}",  # the trunk 0 kept by mistake
        f"0176-{tail[:3]}-{tail[3:]}",
        f"(0176) {tail[:3]}/{tail[3:]}",
        f"0176.{tail[:3]}.{tail[3:]}",
        f"0176 {tail[:3]}",  # the start of it
        tail,  # the end of it, as before
    ):
        assert me in _found(typed), typed


def test_a_number_stored_the_old_way_is_found_both_ways():
    """Accounts from before app/phones.py keep the number as it was typed."""
    tail, _ = _german_mobile()
    api.register_and_login(f"legacy.{tail}{DOMAIN}", PASSWORD)
    me = _only_id(f"legacy.{tail}")
    db = SessionLocal()
    try:
        db.get(User, me).phone = f"0176 {tail[:3]} {tail[3:5]} {tail[5:]}"
        db.commit()
    finally:
        db.close()
    assert me in _found(f"+49 176 {tail}")
    assert me in _found(f"0176{tail}")


def test_a_number_search_finds_no_other_number():
    tail, _ = _german_mobile()
    mine = f"0176 {tail}"
    neighbour = f"0176 {tail[:-1]}{(int(tail[-1]) + 1) % 10}"  # one digit apart
    abroad = f"+44 176 {tail}"  # the same digits behind another country code
    ids = {}
    for label, number in (("mine", mine), ("neighbour", neighbour), ("abroad", abroad)):
        made = client.post("/api/auth/register", json={"phone": number, "password": PASSWORD, "first_name": label})
        assert made.status_code in (200, 201), made.text
        ids[label] = made.json()["user"]["id"]

    for typed in (mine, f"+49 176 {tail}", f"0049176{tail}"):
        found = _found(typed)
        assert ids["mine"] in found, typed
        assert ids["neighbour"] not in found and ids["abroad"] not in found, typed
    # A search that is not shaped like a number never matches one: the old
    # digit-for-digit rule found every number holding the "2" of "anna2".
    assert ids["mine"] not in _found(f"anna{tail[-3:]}")
    # Two digits are no number to look for.
    assert ids["mine"] not in _found(tail[-2:])


def test_names_and_addresses_are_still_found_as_before():
    token = api.register_and_login(f"plain.search{DOMAIN}", PASSWORD)
    client.put("/api/auth/me", json={"first_name": "Philippa", "last_name": "Search"}, headers=auth_header(token))
    me = _only_id("plain.search")
    for typed in ("philippa", "PHILIPPA SEARCH", "plain.search@", f"plain.search{DOMAIN}", "ppa sea"):
        assert me in _found(typed), typed
