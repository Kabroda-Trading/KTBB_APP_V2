"""Coverage for alt_matrix_radar.py -- read-only snapshot builders. Real
file-backed SQLite DB, no network, no Bitunix client involved at all
(this module makes no exchange calls)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_alt_matrix_radar.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, AltMatrixPlan, AltMatrixOrder, AltMatrixConfig, AltMatrixTransition
import executor_accounts as ea
import alt_matrix_radar as amr


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_radar.db", "kabroda_test_alt_matrix_radar.db-journal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (AltMatrixTransition, AltMatrixOrder, AltMatrixPlan, AltMatrixConfig, ExecutorAccount):
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


def _plan(db, symbol="SOL/USDT", status="ARMED", **overrides):
    defaults = dict(symbol=symbol, signal_bar_time=datetime.datetime(2026, 10, 9, 4, 0, 0),
                     date_key="2026-10-09", ema21=101.0, ema55=100.0, atr14=2.0, status=status)
    defaults.update(overrides)
    plan = AltMatrixPlan(**defaults)
    db.add(plan)
    db.flush()
    return plan


def _account(db, label="radar_test"):
    account = ea.create_account(db, user_id=1, label=label)
    db.commit()
    return account


_order_counter = {"n": 0}


def _order(db, account, plan, **overrides):
    _order_counter["n"] += 1
    defaults = dict(
        alt_matrix_plan_id=plan.id, account_id=account.id, mode="DRY_RUN",
        symbol=plan.symbol, direction="LONG", decision="WOULD_PLACE",
        management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0,
    )
    defaults.update(overrides)
    order = AltMatrixOrder(**defaults)
    db.add(order)
    db.flush()
    return order


# ------------------------------------------------------------------ get_public_alt_matrix_snapshot

def test_public_snapshot_covers_both_symbols_with_no_data(db):
    out = amr.get_public_alt_matrix_snapshot(db)
    assert out["ok"] is True
    assert set(out["symbols"].keys()) == {"SOLUSDT", "ETHUSDT"}
    assert out["symbols"]["SOLUSDT"]["plan"] is None
    assert out["symbols"]["SOLUSDT"]["position"] is None


def test_public_snapshot_reports_the_latest_plan_honestly(db):
    _plan(db, status="ARMED")
    out = amr.get_public_alt_matrix_snapshot(db)
    plan = out["symbols"]["SOLUSDT"]["plan"]
    assert plan is not None
    assert plan["status"] == "ARMED"
    assert plan["ema21"] == 101.0


def test_public_snapshot_picks_the_most_recent_plan_not_an_old_one(db):
    _plan(db, status="SKIPPED_MACRO", signal_bar_time=datetime.datetime(2026, 10, 8, 20, 0, 0))
    _plan(db, status="ARMED", signal_bar_time=datetime.datetime(2026, 10, 9, 4, 0, 0))
    out = amr.get_public_alt_matrix_snapshot(db)
    assert out["symbols"]["SOLUSDT"]["plan"]["status"] == "ARMED"


def test_public_snapshot_reports_open_position_without_account_identity(db):
    account = _account(db)
    plan = _plan(db)
    _order(db, account, plan, management_state="TRAILING", entry_fill_price=105.0, sl_price_current=101.0, be_amended=True, mfe_r=2.5)

    out = amr.get_public_alt_matrix_snapshot(db)
    position = out["symbols"]["SOLUSDT"]["position"]
    assert position is not None
    assert position["entry_fill_price"] == 105.0
    assert position["be_amended"] is True
    assert position["mfe_r"] == 2.5
    assert "account_id" not in position
    assert "account_label" not in position


def test_public_snapshot_position_none_when_order_is_closed(db):
    account = _account(db)
    plan = _plan(db)
    _order(db, account, plan, management_state="CLOSED_STOP")
    out = amr.get_public_alt_matrix_snapshot(db)
    assert out["symbols"]["SOLUSDT"]["position"] is None


def test_public_snapshot_position_none_for_not_entered_order(db):
    account = _account(db)
    plan = _plan(db, status="CONCURRENCY_SKIPPED")
    _order(db, account, plan, management_state="NOT_ENTERED", decision="CONCURRENCY_SKIPPED")
    out = amr.get_public_alt_matrix_snapshot(db)
    assert out["symbols"]["SOLUSDT"]["position"] is None


# ------------------------------------------------------------------ get_admin_alt_matrix_status

def test_admin_status_includes_configs_and_account_labels(db):
    account = _account(db, label="andy_bitunix_main")
    db.add(AltMatrixConfig(account_id=account.id, sol_enabled=True, eth_enabled=False))
    db.commit()

    out = amr.get_admin_alt_matrix_status(db)
    assert len(out["configs"]) == 1
    assert out["configs"][0]["account_label"] == "andy_bitunix_main"
    assert out["configs"][0]["sol_enabled"] is True
    assert out["configs"][0]["eth_enabled"] is False


def test_admin_status_includes_recent_orders_with_full_detail(db):
    account = _account(db)
    plan = _plan(db)
    _order(db, account, plan, management_state="CLOSED_EMA21_TRAIL", exit_reason="EMA21_TRAIL", exit_price=112.0, realized_pnl_r=1.2)

    out = amr.get_admin_alt_matrix_status(db)
    orders = out["symbols"]["SOLUSDT"]["recent_orders"]
    assert len(orders) == 1
    assert orders[0]["exit_reason"] == "EMA21_TRAIL"
    assert orders[0]["realized_pnl_r"] == 1.2
    assert orders[0]["account_id"] == account.id   # admin view DOES carry account identity


def test_admin_status_excludes_orders_with_no_decision_yet(db):
    # A freshly created PENDING_ENTRY row with decision=None shouldn't
    # appear until a real decision has actually been recorded.
    account = _account(db)
    plan = _plan(db)
    _order(db, account, plan, decision=None, management_state="PENDING_ENTRY")
    out = amr.get_admin_alt_matrix_status(db)
    assert out["symbols"]["SOLUSDT"]["recent_orders"] == []


def test_admin_status_includes_recent_transitions_most_recent_first(db):
    account = _account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    db.add(AltMatrixTransition(alt_matrix_plan_id=plan.id, alt_matrix_order_id=order.id, from_state="PENDING_ENTRY", to_state="FILLED", price=100.0))
    db.add(AltMatrixTransition(alt_matrix_plan_id=plan.id, alt_matrix_order_id=order.id, from_state="FILLED", to_state="TRAILING", price=101.0))
    db.commit()

    out = amr.get_admin_alt_matrix_status(db)
    assert len(out["recent_transitions"]) == 2
    assert out["recent_transitions"][0]["to_state"] == "TRAILING"   # most recent (higher id) first
    assert out["recent_transitions"][1]["to_state"] == "FILLED"


def test_admin_status_respects_recent_transitions_limit(db):
    account = _account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    for i in range(5):
        db.add(AltMatrixTransition(alt_matrix_plan_id=plan.id, alt_matrix_order_id=order.id, from_state="A", to_state=f"B{i}"))
    db.commit()

    out = amr.get_admin_alt_matrix_status(db, recent_transitions_limit=2)
    assert len(out["recent_transitions"]) == 2
