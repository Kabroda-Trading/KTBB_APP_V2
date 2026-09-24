"""
Regression coverage for GET /api/export/traveler-log.csv -- the Traveler-
native counterpart to /api/export/gate-log.csv, built 2026-09-23 (V2 Crown
retirement, Andy's ruling: ship this BEFORE GateLog is deleted, so the
Kabroda AI Brain never loses its forward-test data source). Same style as
tests/test_gate_log_export.py.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_traveler_log_export.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
os.environ["GATE_LOG_EXPORT_API_KEY"] = "test-export-key"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import csv
import io
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, TravelerPlan, ExecutorOrder, UserModel
import executor_accounts as ea
from main import app


def _clean_db_files():
    for path in ["kabroda_test_traveler_log_export.db", "kabroda_test_traveler_log_export.db-journal",
                 "kabroda_test_traveler_log_export.db-shm", "kabroda_test_traveler_log_export.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def export_env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(TravelerPlan).delete()
    db.query(ExecutorOrder).delete()
    db.commit()

    def make_plan(symbol="BTC/USDT", date_key="2026-08-31", status="WAITING_CROSS", **kwargs):
        row = TravelerPlan(symbol=symbol, date_key=date_key, session_id="us_ny_futures", status=status, **kwargs)
        db.add(row)
        db.commit()
        return row

    client = TestClient(app)
    yield {"db": db, "make_plan": make_plan, "client": client}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _plan_columns():
    return [c.name for c in TravelerPlan.__table__.columns]


_MGMT_COLUMNS = ["mgmt_mode", "mgmt_management_state", "mgmt_entry_fill_price",
                 "mgmt_entry_fill_time", "mgmt_exit_reason", "mgmt_exit_price",
                 "mgmt_exit_time", "mgmt_realized_pnl_r", "mgmt_c5_fired", "mgmt_bbwp_fired"]


def test_export_requires_api_key(export_env):
    resp = export_env["client"].get("/api/export/traveler-log.csv")
    assert resp.status_code == 401


def test_export_rejects_wrong_api_key(export_env):
    resp = export_env["client"].get("/api/export/traveler-log.csv", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_export_empty_still_returns_header_only(export_env):
    resp = export_env["client"].get("/api/export/traveler-log.csv", headers={"X-API-Key": "test-export-key"})
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert list(reader) == []
    assert reader.fieldnames == _plan_columns() + _MGMT_COLUMNS


def test_export_plan_with_no_order_yet_has_null_mgmt_columns(export_env):
    export_env["make_plan"](status="WAITING_CROSS")
    resp = export_env["client"].get("/api/export/traveler-log.csv", headers={"X-API-Key": "test-export-key"})
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["status"] == "WAITING_CROSS"
    for col in _MGMT_COLUMNS:
        assert rows[0][col] == ""


def test_export_includes_mgmt_columns_from_linked_order(export_env):
    plan = export_env["make_plan"](status="FILLED", direction="LONG",
                                    stop_price=89000.0, t1_price=91000.0, fill_price=90000.0)
    user = UserModel(email="traveler_export_owner@kabroda.com", password_hash="x", username="tle_owner",
                      tier="basic", is_admin=False, subscription_status="active")
    export_env["db"].add(user)
    export_env["db"].commit()
    account = ea.create_account(export_env["db"], user_id=user.id, label="traveler_export_acct")
    order = ExecutorOrder(
        trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=account.id, mode="DRY_RUN",
        symbol="BTC/USDT", direction="LONG", entry_price=90000.0, stop_price=89000.0, t1_price=91000.0,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="CLOSED_T1",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
        exit_reason="T1", exit_price=91000.0, realized_pnl_r=1.0,
    )
    export_env["db"].add(order)
    export_env["db"].commit()

    resp = export_env["client"].get("/api/export/traveler-log.csv", headers={"X-API-Key": "test-export-key"})
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["mgmt_mode"] == "DRY_RUN"
    assert rows[0]["mgmt_exit_reason"] == "T1"
    assert rows[0]["mgmt_realized_pnl_r"] == "1.0"


def test_export_prefers_live_order_over_dry_run(export_env):
    """The same real risk this session already mutation-verified twice
    elsewhere (traveler_radar.py, /api/dashboard/mas-history): ExecutorOrder's
    real unique constraint is (traveler_plan_id, account_id), so multiple
    accounts can each hold their own order against one journey. The DRY_RUN
    order is inserted SECOND (higher id) to catch a regression to a naive
    'last seen wins' pick."""
    plan = export_env["make_plan"](status="FILLED", direction="LONG")
    user = UserModel(email="traveler_export_owner2@kabroda.com", password_hash="x", username="tle_owner2",
                      tier="basic", is_admin=False, subscription_status="active")
    export_env["db"].add(user)
    export_env["db"].commit()
    live_account = ea.create_account(export_env["db"], user_id=user.id, label="traveler_export_live")
    dry_account = ea.create_account(export_env["db"], user_id=user.id, label="traveler_export_dry")

    def _order(account, mode, exit_reason, exit_price, r):
        return ExecutorOrder(
            trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=account.id, mode=mode,
            symbol="BTC/USDT", direction="LONG", entry_price=90000.0, stop_price=89000.0, t1_price=91000.0,
            qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state=f"CLOSED_{exit_reason}",
            gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
            exit_reason=exit_reason, exit_price=exit_price, realized_pnl_r=r,
        )

    export_env["db"].add(_order(live_account, "LIVE", "T1", 91000.0, 1.0))
    export_env["db"].commit()
    export_env["db"].add(_order(dry_account, "DRY_RUN", "STOP", 89000.0, -1.0))
    export_env["db"].commit()

    resp = export_env["client"].get("/api/export/traveler-log.csv", headers={"X-API-Key": "test-export-key"})
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["mgmt_mode"] == "LIVE"
    assert rows[0]["mgmt_exit_reason"] == "T1"


def test_export_since_filters_by_date_key(export_env):
    export_env["make_plan"](date_key="2026-08-01")
    export_env["make_plan"](date_key="2026-08-31")

    resp = export_env["client"].get(
        "/api/export/traveler-log.csv?since=2026-08-15", headers={"X-API-Key": "test-export-key"},
    )
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["date_key"] == "2026-08-31"


def test_export_symbol_filters(export_env):
    export_env["make_plan"](symbol="BTC/USDT")
    export_env["make_plan"](symbol="ETH/USDT")

    resp = export_env["client"].get(
        "/api/export/traveler-log.csv?symbol=ETH/USDT", headers={"X-API-Key": "test-export-key"},
    )
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"
