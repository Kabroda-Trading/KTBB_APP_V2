"""
Regression coverage for traveler_radar.py + GET /api/radar/traveler-snapshot
-- Step 1 of the V2 Crown retirement + radar rebuild (CLAUDE.md "Strategic
Direction", V2_RETIREMENT_MAP.md). TestClient-based, same style as
tests/test_admin_plan_status.py, but this route is PUBLIC (no login) and
scoped to TODAY's real (symbol, session_id, date_key) rather than that
route's "most recent row" shortcut -- so these tests seed fixtures against
the same real date_key session_manager.resolve_current_session() resolves
today, and specifically prove a stale/wrong-day row is correctly ignored
(the one behavior this route does differently).
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_traveler_radar.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime as dt
import json
import time

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, SessionLock, TravelerPlan, ExecutorAccount, ExecutorOrder
import executor_accounts as ea
import session_manager
from main import app

TODAY_KEY = session_manager.resolve_current_session(dt.datetime.now(dt.timezone.utc), "AUTO")["date_key"]


def _clean_db_files():
    for path in ["kabroda_test_traveler_radar.db", "kabroda_test_traveler_radar.db-journal",
                 "kabroda_test_traveler_radar.db-shm", "kabroda_test_traveler_radar.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(TravelerPlan).delete()
    db.query(SessionLock).delete()
    db.query(ExecutorOrder).delete()
    db.commit()
    yield {"db": db}
    db.close()
    database.engine.dispose()
    _clean_db_files()


def _make_lock(db, date_key=TODAY_KEY, bo=81414.10, bd=80000.0, r30_high=81300.0, r30_low=80100.0,
               anchor_price=80700.0, daily_resistance=82000.0, daily_support=79000.0):
    row = SessionLock(
        symbol="BTC/USDT", session_id="us_ny_futures", date_key=date_key,
        lock_time=int(time.time()),
        packet_data=json.dumps({"levels": {
            "breakout_trigger": bo, "breakdown_trigger": bd,
            "range30m_high": r30_high, "range30m_low": r30_low,
            "anchor_price": anchor_price,
            "daily_resistance": daily_resistance, "daily_support": daily_support,
        }}),
    )
    db.add(row)
    db.commit()
    return row


def _make_plan(db, date_key=TODAY_KEY, **kwargs):
    defaults = dict(
        symbol="BTC/USDT", date_key=date_key, session_id="us_ny_futures",
        status="WAITING_CROSS",
    )
    defaults.update(kwargs)
    row = TravelerPlan(**defaults)
    db.add(row)
    db.commit()
    return row


def _make_account(db, user_id, mode="DRY_RUN", gate_profile="GATE_TRAVELER", is_active=True):
    account = ea.create_account(db, user_id=user_id, label="traveler_radar_test_acct")
    account.mode = mode
    account.gate_profile = gate_profile
    account.is_active = is_active
    db.commit()
    return account


def _make_order(db, traveler_plan_id, account_id, mode="DRY_RUN", **kwargs):
    defaults = dict(
        trade_plan_id=traveler_plan_id,
        traveler_plan_id=traveler_plan_id, account_id=account_id, mode=mode,
        symbol="BTC/USDT", direction="LONG", entry_price=81414.10, stop_price=81100.0,
        t1_price=82000.0, qty=0.01, risk_dollars_used=100.0,
        decision="WOULD_PLACE", management_state="ENTRY_FILLED_ORDERS_PLACED",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
    )
    defaults.update(kwargs)
    row = ExecutorOrder(**defaults)
    db.add(row)
    db.commit()
    return row


def test_route_is_public_no_login_required(env):
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    assert resp.status_code == 200


def test_unlocked_no_plan(env):
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    body = resp.json()
    assert body["ok"] is True
    assert body["locked"] is False
    assert body["plan"] is None
    assert body["levels"]["breakout_trigger"] is None


def test_locked_no_plan_yet_shows_levels_only(env):
    _make_lock(env["db"])
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    body = resp.json()
    assert body["locked"] is True
    assert body["levels"]["breakout_trigger"] == 81414.10
    assert body["levels"]["breakdown_trigger"] == 80000.0
    assert body["levels"]["daily_resistance"] == 82000.0
    assert body["levels"]["daily_support"] == 79000.0
    assert body["price"] == 80700.0
    assert body["price_as_of"] == "lock"
    assert body["plan"] is None


def test_pre_cross_plan_has_no_speculative_direction(env):
    # The one behavior change this whole rebuild embodies: unlike V2's old
    # dossier, there is no direction/stop/t1 guess before a real cross.
    _make_lock(env["db"])
    _make_plan(env["db"], status="WAITING_CROSS")
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    plan = resp.json()["plan"]
    assert plan["status"] == "WAITING_CROSS"
    assert plan["direction"] is None
    assert plan["stop_price"] is None
    assert plan["t1_price"] is None
    assert plan["mgmt_mode"] is None


def test_post_cross_plan_shows_direction_stop_t1_no_t2_t3_keys(env):
    _make_lock(env["db"])
    _make_plan(
        env["db"], status="WAITING_TOUCH", direction="LONG",
        stop_price=81100.0, t1_price=82000.0,
        cross_time=dt.datetime(2026, 9, 18, 14, 5, 0), cross_price=81414.10,
    )
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    plan = resp.json()["plan"]
    assert plan["direction"] == "LONG"
    assert plan["stop_price"] == 81100.0
    assert plan["t1_price"] == 82000.0
    assert "t2_price" not in plan and "t3_price" not in plan


def test_stale_wrong_day_plan_is_ignored(env):
    # The real behavior difference from /api/admin/traveler-plan-status's
    # "most recent row" shortcut: this public route is scoped to TODAY's
    # real (symbol, session_id, date_key), so an old, unrelated journey
    # from a different date_key must never leak into today's response.
    _make_plan(env["db"], date_key="2020-01-01", status="DONE", direction="SHORT")
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    assert resp.json()["plan"] is None


def test_mgmt_fields_populate_for_a_closed_dry_run_order(env):
    plan = _make_plan(env["db"], status="FILLED", direction="LONG", fill_price=81414.10)
    from database import UserModel
    u = UserModel(email="traveler_radar_owner@kabroda.com", password_hash="x", username="tr_owner",
                  tier="basic", is_admin=False, subscription_status="active")
    env["db"].add(u)
    env["db"].commit()
    account = _make_account(env["db"], u.id, mode="DRY_RUN")
    _make_order(
        env["db"], plan.id, account.id, mode="DRY_RUN",
        management_state="CLOSED_STOP", exit_reason="STOP", exit_price=81100.0,
        exit_time=dt.datetime(2026, 9, 18, 15, 0, 0), realized_pnl_r=-1.0,
    )
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    plan_out = resp.json()["plan"]
    assert plan_out["mgmt_mode"] == "DRY_RUN"
    assert plan_out["mgmt_exit_reason"] == "STOP"
    assert plan_out["mgmt_realized_pnl_r"] == -1.0
    assert plan_out["mgmt_exit_approximated"] is False


def test_mgmt_exit_approximated_true_only_for_live_contingency_exit(env):
    plan = _make_plan(env["db"], status="FILLED", direction="LONG")
    from database import UserModel
    u = UserModel(email="traveler_radar_owner2@kabroda.com", password_hash="x", username="tr_owner2",
                  tier="basic", is_admin=False, subscription_status="active")
    env["db"].add(u)
    env["db"].commit()
    account = _make_account(env["db"], u.id, mode="LIVE")
    _make_order(
        env["db"], plan.id, account.id, mode="LIVE",
        management_state="CLOSED_C5_EXIT", exit_reason="C5_EXIT", exit_price=81900.0, realized_pnl_r=0.85,
    )
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    plan_out = resp.json()["plan"]
    assert plan_out["mgmt_mode"] == "LIVE"
    assert plan_out["mgmt_exit_approximated"] is True


def test_mgmt_prefers_live_order_over_dry_run_order(env):
    plan = _make_plan(env["db"], status="FILLED", direction="LONG")
    from database import UserModel
    u = UserModel(email="traveler_radar_owner3@kabroda.com", password_hash="x", username="tr_owner3",
                  tier="basic", is_admin=False, subscription_status="active")
    env["db"].add(u)
    env["db"].commit()
    dry_account = _make_account(env["db"], u.id, mode="DRY_RUN")
    live_account = _make_account(env["db"], u.id, mode="LIVE")
    _make_order(
        env["db"], plan.id, live_account.id, mode="LIVE",
        management_state="CLOSED_T1", exit_reason="T1", exit_price=82000.0, realized_pnl_r=1.0,
    )
    _make_order(
        env["db"], plan.id, dry_account.id, mode="DRY_RUN",
        management_state="CLOSED_STOP", exit_reason="STOP", exit_price=81100.0, realized_pnl_r=-1.0,
    )
    client = TestClient(app)
    resp = client.get("/api/radar/traveler-snapshot")
    plan_out = resp.json()["plan"]
    assert plan_out["mgmt_mode"] == "LIVE"
    assert plan_out["mgmt_exit_reason"] == "T1"
