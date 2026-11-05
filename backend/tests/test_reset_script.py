"""Tests for scripts/reset_customer_order_data.py — the most dangerous
file in the repo, so every guard gets pinned.

Each test builds its own throwaway SQLite database in tmp_path and seeds a
miniature bakery: an admin, two customers, four orders (paid euro with a
Stripe intent, pending dollar history, paid cash, paid card with no intent)
with their children, protected rows across the shop tables, and one
notification of every kind. Nothing here ever touches the
suite's shared database, the dev database, or any production system —
run_reset() is called directly with an engine bound to a file pytest throws
away, and the main()/argv tests stub run_reset out entirely so the flag
parsing is exercised without an engine at all.
"""

import importlib.util
import inspect
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Address,
    AdminNotification,
    BulkEnquiry,
    BulkReply,
    Category,
    Coupon,
    Favorite,
    LoyaltyVoucher,
    Order,
    OrderEvent,
    OrderItem,
    OrderTaxLine,
    Product,
    Review,
    Subscriber,
    Testimonial as HomepageQuote,  # aliased off "Test*": not a test class
    User,
)

# The script is not a package module — load it straight from scripts/, the
# same way test_stripe_gateway.py pulls in helpers.
SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "reset_customer_order_data.py"
)
_spec = importlib.util.spec_from_file_location("reset_customer_order_data", SCRIPT_PATH)
reset_script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = reset_script
_spec.loader.exec_module(reset_script)

NOT_PRODUCTION = SimpleNamespace(is_production=False)
PRODUCTION = SimpleNamespace(is_production=True)


def make_throwaway_db(tmp_path):
    """A fresh database with the full schema and FK enforcement ON, so a
    wrong child/parent deletion order fails here instead of in production."""
    engine = create_engine(f"sqlite:///{tmp_path / 'throwaway.db'}")

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_bakery(engine):
    """One admin, two customers, order history in two currencies, protected
    shop rows, and a notification of every kind. Returns the seeded ids."""
    with sessionmaker(bind=engine)() as session:
        baker = User(
            email="baker@djinis.example",
            password_hash="x",
            first_name="Djini",
            last_name="B.",
            role="admin",
        )
        mara = User(
            email="mara@example.com",
            password_hash="x",
            first_name="Mara",
            last_name="M",
            role="customer",
        )
        timo = User(
            email="timo@example.com",
            password_hash="x",
            first_name="Timo",
            last_name="T",
            role="customer",
        )
        bread = Category(name="Bread", slug="bread")
        session.add_all([baker, mara, timo, bread])
        session.flush()

        loaf = Product(name="Loaf", slug="loaf", price=3.5, category_id=bread.id)
        tier_code = Coupon(code="TIER10", percent_off=10)
        session.add_all([loaf, tier_code])
        session.flush()
        session.add(LoyaltyVoucher(tier="vip", coupon_id=tier_code.id))
        session.add(Subscriber(email="news@example.com", source="footer"))
        party = BulkEnquiry(
            name="Party Planner",
            email="party@example.com",
            event_date=date(2026, 12, 1),
            event_type="birthday",
            guests=20,
        )
        session.add(party)
        session.flush()
        session.add(BulkReply(enquiry_id=party.id, message="Quote sent"))
        session.add(HomepageQuote(name="A Regular", quote="Warm bread, warmer hello"))

        session.add(
            Address(
                user_id=mara.id,
                recipient="Mara M",
                line1="1 Lane",
                city="Berlin",
                state="BE",
                postal_code="10115",
            )
        )
        paid_euro = Order(
            order_number="DJ-1001",
            user_id=mara.id,
            payment_method="card",
            payment_status="paid",
            subtotal=7,
            total=7,
            currency="eur",
            stripe_payment_intent_id="pi_demo_1",
            recipient="Mara M",
            line1="1 Lane",
            city="Berlin",
            state="BE",
            postal_code="10115",
            country="Germany",
        )
        dollar_history = Order(
            order_number="DJ-1002",
            user_id=timo.id,
            payment_method="card",
            payment_status="pending",
            subtotal=3.5,
            total=3.5,
            currency="usd",  # pre-cutover history — goes with the rest
            recipient="Timo T",
            line1="2 Lane",
            city="Berlin",
            state="BE",
            postal_code="10115",
            country="Germany",
        )
        paid_cash = Order(
            order_number="DJ-1003",
            user_id=mara.id,
            payment_method="cash",
            payment_status="paid",  # settled, but Stripe was never involved
            subtotal=3.5,
            total=3.5,
            currency="eur",
            recipient="Mara M",
            line1="1 Lane",
            city="Berlin",
            state="BE",
            postal_code="10115",
            country="Germany",
        )
        card_without_intent = Order(
            order_number="DJ-1004",
            user_id=timo.id,
            payment_method="card",
            payment_status="paid",  # settled with no intent recorded — a demo row
            subtotal=3.5,
            total=3.5,
            currency="eur",
            recipient="Timo T",
            line1="2 Lane",
            city="Berlin",
            state="BE",
            postal_code="10115",
            country="Germany",
        )
        session.add_all([paid_euro, dollar_history, paid_cash, card_without_intent])
        session.flush()
        session.add(
            OrderItem(
                order_id=paid_euro.id,
                product_id=loaf.id,
                product_name="Loaf",
                unit_price=3.5,
                quantity=2,
                subtotal=7,
            )
        )
        session.add(OrderEvent(order_id=paid_euro.id, status="pending"))
        session.add(
            OrderTaxLine(
                order_id=paid_euro.id,
                vat_rate=7,
                net_amount=6.54,
                vat_amount=0.46,
                gross_amount=7,
            )
        )
        session.add(Review(product_id=loaf.id, user_id=timo.id, rating=5))
        session.add(Favorite(user_id=mara.id, product_id=loaf.id))
        for kind in ("order", "review", "enquiry", "customer"):
            session.add(AdminNotification(kind=kind, title=f"{kind} ping"))
        session.commit()
        return baker.id


def count_rows(engine, table_name):
    table = Base.metadata.tables[table_name]
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(table))


def assert_untouched(engine):
    """The whole miniature shop, still on its shelf."""
    assert count_rows(engine, "users") == 3  # admin + both customers
    assert count_rows(engine, "orders") == 4  # both currencies, cash, no-intent
    assert count_rows(engine, "order_items") == 1
    assert count_rows(engine, "order_events") == 1
    assert count_rows(engine, "order_tax_lines") == 1
    assert count_rows(engine, "reviews") == 1
    assert count_rows(engine, "favorites") == 1
    assert count_rows(engine, "addresses") == 1
    assert count_rows(engine, "admin_notifications") == 4
    assert count_rows(engine, "products") == 1
    assert count_rows(engine, "loyalty_vouchers") == 1


def test_dry_run_is_the_default_at_the_api_seam():
    # execute is keyword-only and defaults to False — the flag a human must
    # pass deliberately can't be tripped over by position.
    signature = inspect.signature(reset_script.run_reset)
    assert signature.parameters["execute"].default is False
    assert signature.parameters["execute"].kind is inspect.Parameter.KEYWORD_ONLY


def test_a_dry_run_reports_everything_and_deletes_nothing(tmp_path):
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)

    report = reset_script.run_reset(engine, NOT_PRODUCTION, execute=False)

    assert report["status"] == "dry-run"
    assert_untouched(engine)

    # The report itself is honest about what would go.
    assert report["customers_to_delete"] == 2
    assert report["admins_before"] == 1
    assert set(report["notifications_to_delete"]) == {"order", "review", "customer"}
    # Children before parents, derived from the live FK graph.
    order = report["deletion_order"]
    assert order.index("orders") > order.index("order_items")
    assert order.index("orders") > order.index("order_events")
    assert order.index("orders") > order.index("order_tax_lines")
    # Identity by name, never by credential.
    assert "throwaway.db" in report["identity"]
    assert report["identity"].startswith("sqlite")
    # Only the one order that genuinely carries a Stripe payment intent is
    # counted: the paid cash order and the paid card order with no intent
    # never touched Stripe, and the pending dollar order isn't settled.
    assert report["settled_stripe_payments"] == 1
    # The paid order earns its own note: Stripe is never called.
    assert any("Stripe" in note for note in report["notes"])
    # The loyalty-voucher ambiguity is said out loud every run.
    assert any("loyalty_vouchers" in note for note in report["notes"])


def test_execute_on_a_throwaway_db_removes_customers_and_orders_only(tmp_path):
    engine = make_throwaway_db(tmp_path)
    baker_id = seed_bakery(engine)

    report = reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)

    assert report["status"] == "executed"
    assert report["problems"] == []

    # Customer data gone — both currencies of history with it.
    for gone in (
        "orders",
        "order_items",
        "order_events",
        "order_tax_lines",
        "reviews",
        "favorites",
        "addresses",
    ):
        assert count_rows(engine, gone) == 0, gone
    assert report["customers_after"] == 0

    # The admin survived, by id, not by count alone.
    users = Base.metadata.tables["users"]
    with engine.connect() as conn:
        kept = conn.execute(select(users.c.email, users.c.role)).fetchall()
    assert kept == [("baker@djinis.example", "admin")]
    with engine.connect() as conn:
        assert conn.scalar(select(users.c.id).where(users.c.id == baker_id)) == baker_id

    # The shop itself: every protected table kept its rows, prices included.
    for kept_table in (
        "categories",
        "products",
        "coupons",
        "loyalty_vouchers",
        "subscribers",
        "bulk_enquiries",
        "bulk_replies",
        "testimonials",
    ):
        assert count_rows(engine, kept_table) == 1, kept_table
    products = Base.metadata.tables["products"]
    with engine.connect() as conn:
        assert conn.scalar(select(products.c.price)) == 3.5

    # Desk pings: only the enquiry about a KEPT lead remains.
    notifications = Base.metadata.tables["admin_notifications"]
    with engine.connect() as conn:
        kinds_left = sorted(
            row[0] for row in conn.execute(select(notifications.c.kind).distinct())
        )
    assert kinds_left == ["enquiry"]


def test_a_failure_mid_deletion_rolls_back_everything(tmp_path):
    # The one-transaction promise, actually exercised: the second DELETE of
    # the run (favorites, after addresses) crashes on purpose, and what was
    # already deleted must come back with everything else.
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _crash_mid_reset(_conn, _cursor, statement, *_args, **_kwargs):
        if statement.strip().upper().startswith("DELETE FROM FAVORITES"):
            raise RuntimeError("simulated mid-reset crash")

    with pytest.raises(RuntimeError, match="simulated mid-reset crash"):
        reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)

    # Nothing went: customers, all four orders, every child row.
    assert_untouched(engine)


def test_production_is_refused_before_anything_happens(tmp_path):
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)

    with pytest.raises(reset_script.ResetAborted, match="production"):
        reset_script.run_reset(engine, PRODUCTION, execute=True)

    assert_untouched(engine)


# ── The non-SQLite confirmation gate ──────────────────────────────────────
# APP_ENV defaults to "dev", so a production PostgreSQL URL with a missing or
# mislabelled APP_ENV must not sail through on the flag alone: executing
# against any non-SQLite database needs the exact identity the dry run
# printed, passed back as --confirm-database. None of these tests connect —
# the gate runs before the first engine call, so an engine that is nothing
# but a URL exercises it without touching the network.


def _postgres_url():
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username="bakery",
        password="secret",
        host="db.example.com",
        port=5432,
        database="bakery_prod",
    )


def test_a_postgres_execute_with_app_env_missing_is_refused():
    # No APP_ENV at all: settings carries no is_production attribute.
    engine = SimpleNamespace(url=_postgres_url())
    with pytest.raises(reset_script.ResetAborted, match="non-SQLite"):
        reset_script.run_reset(engine, SimpleNamespace(), execute=True)


def test_app_env_dev_with_a_postgres_url_still_needs_confirmation():
    engine = SimpleNamespace(url=_postgres_url())
    with pytest.raises(reset_script.ResetAborted) as refused:
        reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)
    # The refusal names the exact identity it wants passed back.
    assert _postgres_url().render_as_string(hide_password=True) in str(refused.value)


def test_a_confirmation_for_a_different_database_is_refused():
    engine = SimpleNamespace(url=_postgres_url())
    with pytest.raises(reset_script.ResetAborted, match="non-SQLite"):
        reset_script.run_reset(
            engine,
            NOT_PRODUCTION,
            execute=True,
            confirmed_identity="postgresql+psycopg://bakery@elsewhere.example.com/other",
        )


def test_the_matching_confirmation_passes_the_gate_without_connecting():
    # The right identity gets past the gate — proven by the very next step
    # failing on an engine that has no connect() at all: the guard fired
    # first and nothing reached for the network.
    engine = SimpleNamespace(url=_postgres_url())
    with pytest.raises(AttributeError):
        reset_script.run_reset(
            engine,
            NOT_PRODUCTION,
            execute=True,
            confirmed_identity=_postgres_url().render_as_string(hide_password=True),
        )


def test_production_refusal_is_absolute_even_with_a_matching_confirmation():
    engine = SimpleNamespace(url=_postgres_url())
    with pytest.raises(reset_script.ResetAborted, match="production"):
        reset_script.run_reset(
            engine,
            PRODUCTION,
            execute=True,
            confirmed_identity=_postgres_url().render_as_string(hide_password=True),
        )


def test_no_admin_to_preserve_means_no_run(tmp_path):
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)
    users = Base.metadata.tables["users"]
    with engine.begin() as conn:
        conn.execute(
            users.delete().where(users.c.role == "admin")
        )

    with pytest.raises(reset_script.ResetAborted, match="admin"):
        reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)

    # The customers it refused to guess about are all still there.
    assert count_rows(engine, "users") == 2
    assert count_rows(engine, "orders") == 4


def test_an_unknown_user_role_aborts_rather_than_guesses(tmp_path):
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)
    users = Base.metadata.tables["users"]
    with engine.begin() as conn:
        conn.execute(
            users.update().where(users.c.email == "timo@example.com").values(role="staff")
        )

    with pytest.raises(reset_script.ResetAborted, match="role"):
        reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)

    assert_untouched(engine)


def test_an_unknown_notification_kind_aborts_rather_than_guesses(tmp_path):
    engine = make_throwaway_db(tmp_path)
    seed_bakery(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(AdminNotification(kind="mystery", title="?"))
        session.commit()

    with pytest.raises(reset_script.ResetAborted, match="notification"):
        reset_script.run_reset(engine, NOT_PRODUCTION, execute=True)

    assert count_rows(engine, "admin_notifications") == 5  # nothing deleted


# ── The graph guards, unit-level on synthetic metadata ─────────────────────
# These use hand-built MetaData so drift can be simulated without ever
# touching the real Base.metadata the rest of the suite shares.


def synthetic_metadata():
    md = MetaData()
    Table("users", md, Column("id", Integer, primary_key=True))
    Table("orders", md, Column("id", Integer, primary_key=True), Column("user_id", ForeignKey("users.id")))
    return md


def test_schema_drift_aborts_instead_of_guessing():
    md = synthetic_metadata()
    Table("mystery_table", md, Column("id", Integer, primary_key=True))
    with pytest.raises(reset_script.ResetAborted, match="mystery_table"):
        reset_script._classify(md)


def test_a_classified_table_vanishing_is_also_drift():
    md = synthetic_metadata()
    md.remove(md.tables["orders"])
    with pytest.raises(reset_script.ResetAborted, match="no longer exist"):
        reset_script._classify(md)


def test_buckets_that_overlap_abort_the_run(monkeypatch):
    """The three buckets must be mutually exclusive — every rule downstream
    ("kept tables may not reference fully-deleted ones", the predicate
    deletes, the after-count checks) assumes exclusive membership. A future
    edit that puts one table in two buckets must abort before anything real
    is classified."""
    monkeypatch.setattr(reset_script, "DELETE_BY_PREDICATE",
                        reset_script.DELETE_BY_PREDICATE | frozenset({"orders"}))
    with pytest.raises(reset_script.ResetAborted, match="overlap") as excinfo:
        reset_script._classify(synthetic_metadata())
    assert "orders" in str(excinfo.value)

    monkeypatch.setattr(reset_script, "KEEP",
                        reset_script.KEEP | frozenset({"favorites"}))
    with pytest.raises(reset_script.ResetAborted, match="overlap"):
        reset_script._classify(synthetic_metadata())


def test_a_kept_table_may_not_reference_a_deleted_table():
    md = synthetic_metadata()
    Table(
        "loyalty_vouchers",
        md,
        Column("tier", Integer, primary_key=True),
        Column("order_id", ForeignKey("orders.id")),  # would dangle
    )
    problems = reset_script._check_fk_graph(md)
    assert any("fully-deleted" in p for p in problems)


def test_a_kept_table_referencing_users_needs_set_null():
    md = synthetic_metadata()
    Table(
        "product_vat_events",
        md,
        Column("id", Integer, primary_key=True),
        Column("admin_user_id", ForeignKey("users.id")),  # no ondelete
    )
    assert any("SET NULL" in p for p in reset_script._check_fk_graph(md))

    md2 = synthetic_metadata()
    Table(
        "product_vat_events",
        md2,
        Column("id", Integer, primary_key=True),
        Column("admin_user_id", ForeignKey("users.id", ondelete="SET NULL")),
    )
    assert reset_script._check_fk_graph(md2) == []


def test_deletion_order_puts_children_first_and_spots_cycles():
    md = synthetic_metadata()
    for child in ("order_items", "order_events", "order_tax_lines"):
        Table(
            child,
            md,
            Column("id", Integer, primary_key=True),
            Column("order_id", ForeignKey("orders.id")),
        )
    order = reset_script._deletion_order(md)
    # The three children go before the order rows they point at — the FK-ON
    # throwaway databases make a parent-first order an actual failure.
    assert order[:3] == ["order_events", "order_items", "order_tax_lines"]
    assert order[-1] == "orders"

    cycle = MetaData()
    Table("orders", cycle, Column("id", Integer, primary_key=True), Column("address_id", ForeignKey("addresses.id")))
    Table("addresses", cycle, Column("id", Integer, primary_key=True), Column("order_id", ForeignKey("orders.id")))
    with pytest.raises(reset_script.ResetAborted, match="circular"):
        reset_script._deletion_order(cycle)


def test_the_application_never_imports_the_reset_script():
    # The reset must be a human decision every time — nothing the app or the
    # test harness loads (app/, run.py, the root conftest, the tests) may so
    # much as mention the script, or startup could grow a reset.
    backend_dir = SCRIPT_PATH.parents[1]
    candidates = [
        *(backend_dir / "app").rglob("*.py"),
        *filter(lambda p: p.parent == backend_dir, backend_dir.glob("*.py")),
        *(backend_dir / "tests").glob("*.py"),
    ]
    offenders = [
        str(path.relative_to(backend_dir))
        for path in candidates
        if path != Path(__file__).resolve()  # this file is the one legitimate mentioner
        and "reset_customer_order_data" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_main_defaults_to_a_dry_run_and_exits_zero(monkeypatch, capsys):
    """The dry-run default is the script's most important property; it is
    pinned at the run_reset() signature elsewhere, and here at the seam a
    human actually touches: argv. No argv, no flags — nothing deleted."""
    captured = {}

    def fake_run_reset(**kwargs):
        captured.update(kwargs)
        return {"status": "dry-run"}

    monkeypatch.setattr(reset_script, "run_reset", fake_run_reset)
    monkeypatch.setattr(reset_script, "_render", lambda report: "rendered dry-run")

    assert reset_script.main([]) == 0
    assert captured == {"execute": False, "confirmed_identity": None}
    assert "rendered dry-run" in capsys.readouterr().out


def test_main_passes_execute_and_confirmation_through(monkeypatch, capsys):
    captured = {}

    def fake_run_reset(**kwargs):
        captured.update(kwargs)
        return {"status": "executed"}

    monkeypatch.setattr(reset_script, "run_reset", fake_run_reset)
    monkeypatch.setattr(reset_script, "_render", lambda report: "rendered executed")

    identity = "postgresql+psycopg://bakery:***@db.example.com:5432/bakery_prod"
    assert reset_script.main(["--execute", "--confirm-database", identity]) == 0
    assert captured == {"execute": True, "confirmed_identity": identity}


def test_main_exits_one_when_the_run_aborts(monkeypatch, capsys):
    """A refused pre-flight check must read as failure on the terminal —
    an operator scripting this relies on the exit code, not the prose."""

    def refuse(**kwargs):
        raise reset_script.ResetAborted("unrecognised table(s) ['future_table']")

    monkeypatch.setattr(reset_script, "run_reset", refuse)
    assert reset_script.main([]) == 1
    err = capsys.readouterr().err
    assert "ABORTED" in err and "future_table" in err


def test_main_exits_one_on_an_unexpected_report_status(monkeypatch):
    """Dry-run and executed are the only statuses that mean success; a
    report carrying problems (e.g. after-count mismatches) must not exit 0."""
    monkeypatch.setattr(
        reset_script, "run_reset", lambda **kwargs: {"status": "executed-with-problems"}
    )
    monkeypatch.setattr(reset_script, "_render", lambda report: "rendered")
    assert reset_script.main(["--execute"]) == 1
