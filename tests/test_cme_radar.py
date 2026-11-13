"""Coverage for cme_radar.py's get_public_cme_snapshot() -- specifically
the "position" field's state scope, which is what the Executive Dashboard's
OPEN/FLAT chip and the CME Desk's "Open position" row both read directly.

2026-11-13 (real bug found live, Account 17): this field used to include
PENDING_ENTRY/WAITING_RETEST orders (no fill has happened yet, no real
exchange position exists) alongside real filled positions, so an unfilled
resting order displayed as a false "OPEN" position -- directly contradicted
by CrossTrade's own real broker dashboard ("No open positions on Tradovate")
checked at the same moment. Fixed by importing cme_engine's own
_OPEN_POSITION_STATES instead of maintaining a second, drifted definition.
These tests exist specifically so that regression can't silently return."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_cme_radar.db")

import datetime

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, CMEPlan, CMEOrder, CMEConfig, CMETransition
import executor_accounts as ea
import cme_radar as radar

UTC = datetime.timezone.utc


def _clean_db_files():
    for path in ["kabroda_test_cme_radar.db", "kabroda_test_cme_radar.db-journal",
                 "kabroda_test_cme_radar.db-shm", "kabroda_test_cme_radar.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (CMETransition, CMEOrder, CMEPlan, CMEConfig, ExecutorAccount):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _plan(db, symbol="MNQ"):
    today_key = radar._today_date_key()
    plan = CMEPlan(
        symbol=symbol, date_key=today_key, lock_time=datetime.datetime.utcnow(),
        status="ARMED", box=20.0, bo=4500.0, bd=4480.0,
        stop_long=4480.0, stop_short=4500.0, target_long=4540.0, target_short=4460.0,
    )
    db.add(plan)
    db.flush()
    return plan


def _account(db):
    account = ea.create_account(db, user_id=1, label="cme_radar_test")
    db.flush()
    return account


def _order(db, account, plan, **overrides):
    defaults = dict(
        cme_plan_id=plan.id, account_id=account.id, mode="LIVE",
        symbol=plan.symbol, direction="LONG", contracts=1, box_at_entry=plan.box,
        decision="WOULD_PLACE", management_state="PENDING_ENTRY",
    )
    defaults.update(overrides)
    order = CMEOrder(**defaults)
    db.add(order)
    db.flush()
    return order


def test_a_pending_entry_order_is_not_reported_as_an_open_position(db):
    # The exact bug: an unfilled resting order must never show up as
    # "position" -- that's what the OPEN/FLAT chip and the Desk's "Open
    # position" row both read directly.
    account = _account(db)
    plan = _plan(db, symbol="MNQ")
    _order(db, account, plan, management_state="PENDING_ENTRY", entry_fill_price=None)

    out = radar.get_public_cme_snapshot(db)
    assert out["symbols"]["MNQ"]["position"] is None


def test_a_waiting_retest_order_is_not_reported_as_an_open_position(db):
    account = _account(db)
    plan = _plan(db, symbol="MES")
    _order(db, account, plan, symbol="MES", management_state="WAITING_RETEST", entry_fill_price=None)

    out = radar.get_public_cme_snapshot(db)
    assert out["symbols"]["MES"]["position"] is None


def test_a_filled_order_is_reported_as_a_real_open_position(db):
    account = _account(db)
    plan = _plan(db, symbol="MNQ")
    _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
           entry_fill_time=datetime.datetime.utcnow())

    out = radar.get_public_cme_snapshot(db)
    position = out["symbols"]["MNQ"]["position"]
    assert position is not None
    assert position["direction"] == "LONG"
    assert position["entry_fill_price"] == 4505.0


def test_an_unprotected_filled_order_still_counts_as_an_open_position(db):
    account = _account(db)
    plan = _plan(db, symbol="MES")
    _order(db, account, plan, symbol="MES", management_state="ENTRY_FILLED_UNPROTECTED",
           entry_fill_price=4500.0, entry_fill_time=datetime.datetime.utcnow())

    out = radar.get_public_cme_snapshot(db)
    assert out["symbols"]["MES"]["position"] is not None
