"""Coverage for database.py's cme_orders unique-constraint migration --
the fix-up that runs against an already-existing table left over from
before the 3-column (cme_plan_id, account_id, direction) constraint
existed. The original 2-column constraint (cme_plan_id, account_id)
blocked the real D1 fan-out's OCO-style LONG+SHORT pair from ever both
being written -- see cme_engine.py/cme_risk.py's own comments for the
live-bug side of this; this file covers only the schema fix itself,
against a simulated old-schema table (a real empty one, and a real
non-empty one that must NOT be touched).

Each test restores cme_orders to the CURRENT correct schema at its own
START, rather than relying on the previous test's teardown -- database.py's
engine/DATABASE_URL are module-level globals bound once at first import,
so when this file runs alongside the other CME test files in one pytest
process, whichever test module python imports first during collection
wins that env var for the whole process; this file's own
kabroda_test_cme_schema_migration.db filename is not reliably the real
file in use, and _clean_db_files()'s disk-level removal can be a no-op
against the real shared file. Found 2026-11-04 debugging a real
cross-test leak (one test's deliberately-wrong old-schema table survived
into the next, breaking it with "no such column: cme_orders.mode")."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_cme_schema_migration.db")

from sqlalchemy import text

import database
from database import SessionLocal, CMEOrder


def _clean_db_files():
    for path in ["kabroda_test_cme_schema_migration.db", "kabroda_test_cme_schema_migration.db-journal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _restore_correct_cme_orders_schema():
    """Unconditionally drops and recreates cme_orders from the CURRENT
    model -- the one thing every test in this file can rely on at its
    own start, regardless of what any previous test (in this file or,
    via the shared-engine reality above, possibly another file) left
    the real table looking like."""
    database.init_db()
    with database.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS cme_orders"))
    database.Base.metadata.create_all(bind=database.engine)


def _make_old_schema_table():
    """Mimics the table shape every cme_orders table had before this
    fix -- a plain 2-column UNIQUE, no direction awareness."""
    with database.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS cme_orders"))
        conn.execute(text("""
            CREATE TABLE cme_orders (
                id INTEGER PRIMARY KEY,
                cme_plan_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                direction VARCHAR,
                UNIQUE (cme_plan_id, account_id)
            )
        """))


def test_migration_fixes_an_empty_old_schema_table():
    _restore_correct_cme_orders_schema()
    _make_old_schema_table()

    database.init_db()   # the migration under test runs here

    # The real D1 fan-out needs exactly this to work: two rows, same
    # (plan, account), differing only by direction.
    db = SessionLocal()
    db.add(CMEOrder(cme_plan_id=999101, account_id=999101, direction="LONG", symbol="MNQ",
                     mode="DRY_RUN", management_state="PENDING_ENTRY"))
    db.add(CMEOrder(cme_plan_id=999101, account_id=999101, direction="SHORT", symbol="MNQ",
                     mode="DRY_RUN", management_state="PENDING_ENTRY"))
    db.commit()
    assert db.query(CMEOrder).filter_by(cme_plan_id=999101, account_id=999101).count() == 2
    db.close()
    _restore_correct_cme_orders_schema()   # leave the shared table correct for whatever runs next


def test_migration_refuses_to_drop_a_non_empty_old_schema_table(capsys):
    _restore_correct_cme_orders_schema()
    _make_old_schema_table()
    with database.engine.begin() as conn:
        conn.execute(text("INSERT INTO cme_orders (cme_plan_id, account_id, direction) VALUES (999102, 999102, 'LONG')"))

    database.init_db()   # must NOT drop a non-empty table, even a wrongly-shaped one

    with database.engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM cme_orders")).scalar()
    assert count == 1   # the real row survived -- never silently discarded
    captured = capsys.readouterr()
    assert "cannot auto-migrate" in captured.out

    _restore_correct_cme_orders_schema()   # leave the shared table correct for whatever runs next


def test_migration_is_a_no_op_once_already_correct():
    _restore_correct_cme_orders_schema()
    database.init_db()   # must not error or drop anything on an already-correct table

    db = SessionLocal()
    db.add(CMEOrder(cme_plan_id=999103, account_id=999103, direction="LONG", symbol="MNQ",
                     mode="DRY_RUN", management_state="PENDING_ENTRY"))
    db.commit()
    assert db.query(CMEOrder).filter_by(cme_plan_id=999103, account_id=999103).count() == 1
    db.close()
    _restore_correct_cme_orders_schema()   # leave the shared table correct for whatever runs next
