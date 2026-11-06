"""Guarded customer/order data reset — DRY RUN BY DEFAULT.

Deletes every customer account and order/payment record while keeping the
shop itself intact: products (with prices, untouched), categories,
shop-wide coupons (a personalised voucher goes with its customer),
newsletter subscribers, bulk enquiries and replies, testimonials,
coming-soon items, store settings, VAT data and the whole costing book.
Marketing campaign records and the GDPR suppression list survive the wipe —
campaigns are admin-authored content (their per-recipient delivery rows are
customer data and go), and suppression outlives the account by design, so
a reset can never re-permission someone who said no.
The admin account and auth configuration are preserved — verified, not
assumed.

The contract this script holds itself to (each rule is checked at runtime,
and any surprise aborts the run BEFORE anything is deleted):

  * Dry run unless --execute is passed; a dry run deletes nothing.
  * Refuses to run against a production environment (APP_ENV=production),
    no exceptions in code — running it elsewhere is a documented, manual
    owner decision (docs/operations/EUR_MIGRATION_RUNBOOK.md).
  * APP_ENV alone is not trusted to identify a safe target: APP_ENV
    defaults to "dev", so a production PostgreSQL URL with a missing
    APP_ENV would look safe. Executing against any non-SQLite database
    additionally requires its exact identity (as printed by the dry run)
    passed back as --confirm-database. A dry run is always allowed — it
    only reads. The APP_ENV=production refusal above is absolute and is
    never bypassed by any confirmation.
  * Prints the database it is about to touch by identity, never by
    credential (the password never leaves the URL object).
  * Classifies every table in the live schema as DELETE or KEEP; a table
    it does not recognise aborts the run (schema drift, not a guess).
  * Verifies the foreign-key graph: a kept table may not reference a
    table being emptied, and may reference partially-deleted tables only
    through SET NULL rules.
  * Deletes children before parents inside ONE transaction — a failure
    halfway leaves the database exactly as it was.
  * Verifies afterwards: deleted sets are empty, the admin account(s)
    survived, protected tables still hold their rows.
  * The pre-flight counts and the after-verification read OUTSIDE the
    delete transaction (the report is written for humans to read, not to
    lock the database). Concurrent writes during a run can therefore
    surface as PROBLEMS in the after-report even though the deletion
    itself was sound — this is why the runbook's first step is a
    maintenance window, and any such report means "investigate", not
    "trust the count".
  * NEVER touches products, Stripe, or the network. It does not refund or
    cancel anything at Stripe — historical payment intents simply stop
    being referenced. It is never imported at application startup; it only
    runs when a human runs this file.

Deletion is irreversible without a verified backup. Take one first.

Usage (from backend/):
    python scripts/reset_customer_order_data.py             # dry run
    python scripts/reset_customer_order_data.py --execute   # actually delete

A non-SQLite --execute additionally needs --confirm-database "<identity>"
with the exact identity string the dry run printed.
"""

import argparse
import sys
from pathlib import Path

# The script lives in backend/scripts/; make `app` importable either way
# it is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, engine as app_engine  # noqa: E402

# ── What goes, what stays ──────────────────────────────────────────────────
# Every table in app.models must appear in exactly one of these sets, or the
# run aborts: an unrecognised table is schema drift, and drift is never a
# guessing game.

# Customer-generated data, emptied completely (children before parents —
# the order is derived from the live FK graph at runtime, never hardcoded).
DELETE_ALL = frozenset(
    {
        "orders",
        "order_items",
        "order_events",
        "order_tax_lines",
        "reviews",
        "favorites",
        "restock_alerts",
        "addresses",
        # Per-customer marketing records: the classification audit ledger
        # dies with the customer it describes, and every campaign delivery
        # row carries a recipient email snapshot.
        "classification_events",
        "campaign_deliveries",
    }
)

# Partially emptied: a predicate decides which rows go.
#   users            — role 'customer' goes, 'admin' stays (verified after)
#   admin_notifications — desk pings ABOUT deleted things go (order, review,
#                     customer); enquiry pings stay (bulk enquiries are kept)
#   coupons          — personalised vouchers (restricted_to_user_id set) go
#                     with their customer; shop-wide codes stay (verified)
DELETE_BY_PREDICATE = frozenset({"users", "admin_notifications", "coupons"})

# The shop itself — counted before and after, verified untouched.
KEEP = frozenset(
    {
        "categories",
        "products",
        "loyalty_vouchers",
        "subscribers",
        "bulk_enquiries",
        "bulk_replies",
        "testimonials",
        "coming_soon_items",
        "vat_rates",
        "store_settings",
        "product_vat_events",
        "ingredient_categories",
        "ingredients",
        "ingredient_price_events",
        "recipes",
        "recipe_items",
        "production_batches",
        "overhead_months",
        "labour_rates",
        "product_cost_records",
        # Campaigns are admin-authored shop content (kept like bulk
        # enquiries); only their per-recipient delivery rows are customer
        # data, and those sit in DELETE_ALL above.
        "marketing_campaigns",
        # Unsubscribe records outlive the account they came from — deleting
        # them would re-permission people who said no (see auth.py's account
        # deletion, which makes the same call).
        "suppression_list",
    }
)

KNOWN_ROLES = frozenset({"customer", "admin"})
KNOWN_NOTIFICATION_KINDS = frozenset({"order", "review", "enquiry", "customer"})
DELETED_NOTIFICATION_KINDS = frozenset({"order", "review", "customer"})

# The owner's brief listed "customer loyalty vouchers" among the deletions,
# but the loyalty_vouchers table is shop configuration (which coupon each
# tier hands out), not per-customer data — no customer rows exist in it.
# The script keeps it and says so loudly; the owner decides if that is wrong.
AMBIGUITY_NOTES = (
    "loyalty_vouchers is shop config (tier -> coupon), not customer data — "
    "KEPT. Flagged because the reset brief mentioned 'customer loyalty "
    "vouchers'; if it must go, that is a separate, explicit decision.",
    "suppression_list (unsubscribes) is KEPT on purpose: a reset must never "
    "re-permission someone who opted out, and account deletion in auth.py "
    "makes the same call. Clearing it is a separate, explicit decision.",
)


class ResetAborted(Exception):
    """A pre-flight check refused the run — nothing has been deleted."""


def _classify(metadata) -> None:
    """Abort unless every live table is classified exactly once."""
    # The three buckets are literals, but a future edit can make two of them
    # overlap — and everything downstream ("kept tables may not reference
    # fully-deleted ones", the predicate deletes, the after-count checks)
    # silently assumes exclusive membership. Check the pairs directly.
    overlap = sorted(
        (DELETE_ALL & DELETE_BY_PREDICATE) | (DELETE_ALL & KEEP) | (DELETE_BY_PREDICATE & KEEP)
    )
    if overlap:
        raise ResetAborted(
            f"internal classification overlap: {overlap} sits in more than one "
            "bucket — the script's own sets are inconsistent and need fixing "
            "before it classifies a real database."
        )
    tables = set(metadata.tables)
    classified = DELETE_ALL | DELETE_BY_PREDICATE | KEEP
    unknown = sorted(tables - classified)
    missing = sorted(classified - tables)
    if unknown:
        raise ResetAborted(
            f"unrecognised table(s) {unknown} — the schema has drifted from "
            "this script's classification. Update the script deliberately; "
            "it will not guess what a new table holds."
        )
    if missing:
        raise ResetAborted(
            f"classified table(s) {missing} no longer exist — the schema has "
            "drifted. Update the script deliberately."
        )


def _check_fk_graph(metadata) -> list[str]:
    """A kept table must not dangle when the deleted rows go.

    Rules: a kept (or partially-kept) table may not reference a table being
    emptied entirely; it may reference the partially-emptied users table
    only through SET NULL (the row survives its customer) or CASCADE (the
    row dies with its customer — coupons.restricted_to_user_id is the case
    in point, and its rows are predicate-deleted here anyway). Deleted
    tables may reference anything — their rows are going too.
    """
    problems = []
    kept = set(KEEP) | set(DELETE_BY_PREDICATE)
    for name, table in metadata.tables.items():
        if name not in kept:
            continue
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if target in DELETE_ALL:
                problems.append(
                    f"kept table '{name}' references fully-deleted '{target}' "
                    f"via {fk} — rows would dangle"
                )
            elif target == "users" and (fk.ondelete or "").upper() not in ("SET NULL", "CASCADE"):
                problems.append(
                    f"kept table '{name}' references users via {fk} without "
                    "SET NULL or CASCADE — deleting customers would break it"
                )
    return problems


def _deletion_order(metadata) -> list[str]:
    """DELETE_ALL tables, children before parents, derived from the live
    FK graph: a table is safe to empty once nothing still queued references
    it (deleting a parent while children point at it would break the very
    foreign keys this script promises to respect)."""
    remaining = set(DELETE_ALL) & set(metadata.tables)
    order = []
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if not any(
                fk.column.table.name == name
                for other in remaining
                for fk in metadata.tables[other].foreign_keys
            )
        )
        if not ready:  # a cycle among deleted tables — nothing hardcodes it
            raise ResetAborted(f"circular FK dependency among {sorted(remaining)}")
        order.extend(ready)
        remaining -= set(ready)
    return order


def _counts(conn, metadata, names) -> dict[str, int]:
    return {
        name: conn.scalar(select(func.count()).select_from(metadata.tables[name]))
        for name in names
    }


def run_reset(
    engine=app_engine, settings=None, *, execute: bool = False, confirmed_identity: str | None = None
) -> dict:
    """The whole reset, guarded. Returns the report dict (also human-readable
    via main()). Raises ResetAborted before touching data if any check
    fails."""
    settings = settings or get_settings()
    report: dict = {
        "identity": engine.url.render_as_string(hide_password=True),
        "execute": execute,
        "notes": list(AMBIGUITY_NOTES),
    }

    # ── Refuse production, always ────────────────────────────────────────
    if getattr(settings, "is_production", False):
        raise ResetAborted(
            "refused: this looks like a production environment "
            "(APP_ENV=production). A production reset is a documented, "
            "manual owner decision — see "
            "docs/operations/EUR_MIGRATION_RUNBOOK.md"
        )

    # ── Refuse non-SQLite targets without an exact identity match ───────
    # APP_ENV defaults to "dev", so the check above alone would let a
    # production PostgreSQL URL through whenever APP_ENV is missing or
    # misconfigured. Any database engine the app did not ship with is
    # treated as too precious to delete on a flag alone: executing against
    # it needs the operator to pass back the exact identity the dry run
    # printed (--confirm-database). Read-only dry runs stay open to every
    # engine; the APP_ENV=production refusal above can never be confirmed
    # past.
    backend = engine.url.get_backend_name()
    if execute and backend != "sqlite":
        expected = engine.url.render_as_string(hide_password=True)
        if (confirmed_identity or "").strip() != expected:
            raise ResetAborted(
                f"refused: this is a {backend} database. Executing against a "
                f"non-SQLite target requires its exact identity passed back "
                f"as --confirm-database \"{expected}\" — and a verified "
                "backup first (see docs/operations/EUR_MIGRATION_RUNBOOK.md)."
            )

    metadata = Base.metadata
    _classify(metadata)

    with engine.connect() as conn:
        # ── Pre-flight sanity on the data itself ─────────────────────────
        users_table = metadata.tables["users"]
        notifications_table = metadata.tables["admin_notifications"]
        orders_table = metadata.tables["orders"]
        roles = [row[0] for row in conn.execute(select(users_table.c.role).distinct())]
        unknown_roles = sorted(set(roles) - KNOWN_ROLES)
        if unknown_roles:
            raise ResetAborted(
                f"unknown user role(s) {unknown_roles} — the script only "
                "knows how to keep 'admin' and delete 'customer'"
            )
        kinds = [
            row[0]
            for row in conn.execute(
                select(notifications_table.c.kind).distinct()
            )
        ]
        unknown_kinds = sorted(set(kinds) - KNOWN_NOTIFICATION_KINDS)
        if unknown_kinds:
            raise ResetAborted(
                f"unknown admin notification kind(s) {unknown_kinds} — the "
                "script only knows order/review/enquiry/customer"
            )

        admins_before = conn.scalar(
            select(func.count())
            .select_from(users_table)
            .where(users_table.c.role == "admin")
        )
        if not admins_before:
            raise ResetAborted(
                "no admin account exists — the reset's core promise (keep the "
                "admin) could not be verified, so nothing runs"
            )

        fk_problems = _check_fk_graph(metadata)
        if fk_problems:
            raise ResetAborted("FK graph unsafe: " + "; ".join(fk_problems))

        # ── The picture before ────────────────────────────────────────────
        order = _deletion_order(metadata)
        report["deletion_order"] = order
        report["counts_before"] = _counts(
            conn, metadata, sorted(DELETE_ALL | KEEP | DELETE_BY_PREDICATE)
        )
        customers = conn.scalar(
            select(func.count())
            .select_from(users_table)
            .where(users_table.c.role == "customer")
        )
        report["customers_to_delete"] = customers
        report["admins_before"] = admins_before
        report["notifications_to_delete"] = {
            kind: conn.scalar(
                select(func.count())
                .select_from(notifications_table)
                .where(notifications_table.c.kind == kind)
            )
            for kind in sorted(DELETED_NOTIFICATION_KINDS)
        }
        # Only orders that genuinely carry a Stripe payment intent earn the
        # Stripe caveat: a settled cash-on-delivery order (or a seeded demo
        # row) never touched Stripe and must not inflate the number.
        settled_stripe = conn.scalar(
            select(func.count())
            .select_from(orders_table)
            .where(
                orders_table.c.payment_method == "card",
                orders_table.c.stripe_payment_intent_id.is_not(None),
                orders_table.c.payment_status != "pending",
            )
        )
        report["settled_stripe_payments"] = settled_stripe
        if settled_stripe:
            report["notes"].append(
                f"{settled_stripe} paid card order(s) carry Stripe payment "
                "intents; they will stop being referenced. This script never "
                "calls Stripe — refunds, if any are owed, are a separate "
                "manual step."
            )

        # Personalised vouchers are customer data: they go with their
        # customer, and the shop-wide codes must account for every row.
        coupons_table = metadata.tables["coupons"]
        personal = coupons_table.c.restricted_to_user_id
        report["personal_vouchers_to_delete"] = conn.scalar(
            select(func.count()).select_from(coupons_table).where(personal.is_not(None))
        )
        report["shopwide_coupons_before"] = conn.scalar(
            select(func.count()).select_from(coupons_table).where(personal.is_(None))
        )
        # A tier handing out a personalised code would dangle once the code's
        # customer goes — no endpoint builds that link, so meeting one is
        # drift the script refuses to guess about.
        loyalty_table = metadata.tables["loyalty_vouchers"]
        tier_linked_personal = conn.scalar(
            select(func.count()).select_from(
                loyalty_table.join(coupons_table, loyalty_table.c.coupon_id == coupons_table.c.id)
            ).where(personal.is_not(None))
        )
        if tier_linked_personal:
            raise ResetAborted(
                f"{tier_linked_personal} loyalty voucher(s) point at a "
                "personalised coupon — the coupon's customer is being deleted "
                "and the tier link would dangle. Untangle it before resetting."
            )

    if not execute:
        report["status"] = "dry-run"
        return report

    # ── The deletion: one transaction, children before parents ────────────
    with engine.begin() as conn:
        for name in order:
            conn.execute(delete(metadata.tables[name]))
        conn.execute(
            delete(metadata.tables["admin_notifications"]).where(
                metadata.tables["admin_notifications"].c.kind.in_(
                    DELETED_NOTIFICATION_KINDS
                )
            )
        )
        # Personalised vouchers go with their customers — children before
        # parents like everything else, and explicitly so on databases whose
        # coupons FK was added by ALTER TABLE (no engine-level CASCADE there).
        conn.execute(
            delete(metadata.tables["coupons"]).where(
                metadata.tables["coupons"].c.restricted_to_user_id.is_not(None)
            )
        )
        # Customers last — everything referencing them is already gone.
        conn.execute(
            delete(metadata.tables["users"]).where(
                metadata.tables["users"].c.role == "customer"
            )
        )

    # ── Post-verify: read fresh, compare against the promise ─────────────
    with engine.connect() as conn:
        leftover = {
            name: count
            for name, count in _counts(conn, metadata, sorted(DELETE_ALL)).items()
            if count
        }
        report["counts_after"] = _counts(
            conn, metadata, sorted(DELETE_ALL | KEEP | DELETE_BY_PREDICATE)
        )
        report["customers_after"] = conn.scalar(
            select(func.count())
            .select_from(users_table)
            .where(users_table.c.role == "customer")
        )
        report["admins_after"] = conn.scalar(
            select(func.count())
            .select_from(users_table)
            .where(users_table.c.role == "admin")
        )
        report["enquiry_notifications_after"] = conn.scalar(
            select(func.count())
            .select_from(notifications_table)
            .where(notifications_table.c.kind == "enquiry")
        )
        coupons_table = metadata.tables["coupons"]
        report["personal_vouchers_after"] = conn.scalar(
            select(func.count())
            .select_from(coupons_table)
            .where(coupons_table.c.restricted_to_user_id.is_not(None))
        )
        report["shopwide_coupons_after"] = conn.scalar(
            select(func.count())
            .select_from(coupons_table)
            .where(coupons_table.c.restricted_to_user_id.is_(None))
        )

    problems = []
    if leftover:
        problems.append(f"deleted tables still hold rows: {leftover}")
    if report["customers_after"]:
        problems.append(f"{report['customers_after']} customer(s) survived")
    if report["admins_after"] != report["admins_before"]:
        problems.append(
            f"admin count changed: {report['admins_before']} -> {report['admins_after']}"
        )
    for name in sorted(KEEP):
        if report["counts_after"][name] != report["counts_before"][name]:
            problems.append(
                f"protected table '{name}' changed: "
                f"{report['counts_before'][name]} -> {report['counts_after'][name]}"
            )
    if report["personal_vouchers_after"]:
        problems.append(
            f"{report['personal_vouchers_after']} personalised voucher(s) survived"
        )
    if report["shopwide_coupons_after"] != report["shopwide_coupons_before"]:
        problems.append(
            "shop-wide coupons changed: "
            f"{report['shopwide_coupons_before']} -> {report['shopwide_coupons_after']}"
        )
    report["status"] = "executed" if not problems else "EXECUTED WITH PROBLEMS"
    report["problems"] = problems
    return report


def _render(report: dict) -> str:
    lines = [
        f"database   : {report['identity']}",
        f"mode       : {'EXECUTE (real deletion)' if report['execute'] else 'DRY RUN (nothing will be deleted)'}",
        "",
        "deletes, children first:",
        f"  order           : {' -> '.join(report['deletion_order'])}",
        f"  customer accounts: {report['customers_to_delete']}",
        f"  admin accounts   : {report['admins_before']} (kept, verified after)",
        f"  personalised vchrs: {report['personal_vouchers_to_delete']} (go with their customers)",
        "  desk pings       : "
        + ", ".join(f"{k} ×{v}" for k, v in report["notifications_to_delete"].items()),
        "",
        "row counts before:",
    ]
    lines += [f"  {name:<24} {count}" for name, count in report["counts_before"].items()]
    if report["execute"]:
        lines += ["", "row counts after:"]
        lines += [
            f"  {name:<24} {count}" for name, count in report["counts_after"].items()
        ]
        lines += [
            "",
            f"customers remaining: {report['customers_after']}",
            f"admins remaining   : {report['admins_after']}",
        ]
        if report.get("problems"):
            lines += ["", "PROBLEMS:"] + [f"  - {p}" for p in report["problems"]]
    lines += ["", "notes:"] + [f"  - {n}" for n in report["notes"]]
    if not report["execute"]:
        lines += [
            "",
            "This was a dry run — nothing was deleted. "
            "Re-run with --execute to delete for real (irreversible without a backup).",
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete all customer accounts and order/payment records, "
        "keeping the shop (products, prices, coupons, subscribers, enquiries, "
        "admin). Dry run by default."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually delete (the default is a dry run that changes nothing)",
    )
    parser.add_argument(
        "--confirm-database",
        metavar="IDENTITY",
        default=None,
        help="required by --execute against any non-SQLite database: the "
        "exact identity string the dry run printed for it",
    )
    args = parser.parse_args(argv)

    try:
        report = run_reset(
            execute=args.execute, confirmed_identity=args.confirm_database
        )
    except ResetAborted as reason:
        print(f"ABORTED — nothing was deleted.\n{reason}", file=sys.stderr)
        return 1
    print(_render(report))
    return 0 if report["status"] in ("dry-run", "executed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
