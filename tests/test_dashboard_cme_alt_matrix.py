"""Regression coverage for the desk-page build's new P&L aggregation
routes: GET /api/dashboard/cme-overview, /cme-history,
/alt-matrix-overview, /alt-matrix-history. Neither CME nor Alt Matrix had
any aggregation route before this (cme_radar.py/alt_matrix_radar.py only
expose raw recent-10-orders) -- these mirror /api/dashboard/overview's own
shape and user-scoping pattern exactly (is_admin -> every account, else the
logged-in user's own account_ids only)."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_dashboard_cme_am.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
import datetime
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, ExecutorAccount, CMEPlan, CMEOrder, AltMatrixOrder
import auth
import executor_accounts as ea
from main import app


def _clean_db_files():
    for path in ["kabroda_test_dashboard_cme_am.db", "kabroda_test_dashboard_cme_am.db-journal",
                 "kabroda_test_dashboard_cme_am.db-shm", "kabroda_test_dashboard_cme_am.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    for model in (CMEOrder, CMEPlan, AltMatrixOrder, ExecutorAccount):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "dam_admin@kabroda.com", "dam_owner@kabroda.com", "dam_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="dam_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="damadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="dam_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="damowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="dam_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="damother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()

    owner_account = ea.create_account(db, user_id=owner.id, label="dam_owner_acct")
    other_account = ea.create_account(db, user_id=other.id, label="dam_other_acct")
    db.commit()

    _plan_counter = {"n": 0}

    def _next_cme_plan_id():
        _plan_counter["n"] += 1
        plan = CMEPlan(symbol="MNQ", date_key=f"2026-11-{_plan_counter['n']:02d}",
                        lock_time=datetime.datetime(2026, 11, 5, 15, 0, 0),
                        status="ARMED", box=20.0, bo=4500.0, bd=4480.0)
        db.add(plan)
        db.commit()
        return plan.id

    def make_cme_order(account_id, realized_pnl_r, dollar_pnl, closed_at, **overrides):
        # CMEOrder's real unique constraint is (cme_plan_id, account_id,
        # direction) -- a fresh plan per order avoids colliding with it
        # when a test creates several closed orders for the same account.
        defaults = dict(cme_plan_id=_next_cme_plan_id(), account_id=account_id, mode="LIVE", symbol="MNQ",
                         direction="LONG", contracts=1, management_state="CLOSED_T1",
                         realized_pnl_r=realized_pnl_r, dollar_pnl=dollar_pnl, closed_at=closed_at)
        defaults.update(overrides)
        o = CMEOrder(**defaults)
        db.add(o)
        db.commit()
        return o

    def make_am_order(account_id, realized_pnl_r, realized_pnl_usd, closed_at, **overrides):
        # AltMatrixOrder's real unique constraint is (alt_matrix_plan_id,
        # account_id) -- increment the plan id per order, same convention
        # tests/test_alt_matrix_executor.py's own fixture already uses.
        _plan_counter["n"] += 1
        defaults = dict(alt_matrix_plan_id=_plan_counter["n"], account_id=account_id, mode="LIVE", symbol="SOL/USDT",
                         direction="LONG", management_state="CLOSED_MANUAL", qty=10.0,
                         realized_pnl_r=realized_pnl_r, realized_pnl_usd=realized_pnl_usd, closed_at=closed_at)
        defaults.update(overrides)
        o = AltMatrixOrder(**defaults)
        db.add(o)
        db.commit()
        return o

    yield {
        "db": db, "owner_id": owner.id, "owner_account_id": owner_account.id,
        "other_account_id": other_account.id, "make_cme_order": make_cme_order, "make_am_order": make_am_order,
    }

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


# ------------------------------------------------------------------ CME

def test_cme_overview_requires_login(env):
    client = TestClient(app)
    resp = client.get("/api/dashboard/cme-overview")
    assert resp.status_code == 401


def test_cme_overview_scoped_to_owner_only(env):
    now = datetime.datetime.utcnow()
    env["make_cme_order"](env["owner_account_id"], 1.0, 350.0, now)
    env["make_cme_order"](env["owner_account_id"], -0.5, -175.0, now)
    env["make_cme_order"](env["other_account_id"], 2.0, 700.0, now)   # not this user's -- must not leak in

    client = _login("dam_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/dashboard/cme-overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["closed_trades"] == 2
    assert data["win_rate"] == 50.0
    assert data["net_r"] == pytest.approx(0.5)
    assert data["net_usd"] == pytest.approx(175.0)


def test_cme_overview_admin_sees_every_account(env):
    now = datetime.datetime.utcnow()
    env["make_cme_order"](env["owner_account_id"], 1.0, 350.0, now)
    env["make_cme_order"](env["other_account_id"], 2.0, 700.0, now)

    client = _login("dam_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/dashboard/cme-overview")
    data = resp.json()
    assert data["closed_trades"] == 2
    assert data["net_usd"] == pytest.approx(1050.0)


def test_cme_overview_days_param_filters(env):
    env["make_cme_order"](env["owner_account_id"], 1.0, 350.0, datetime.datetime.utcnow() - datetime.timedelta(days=5))
    env["make_cme_order"](env["owner_account_id"], -0.5, -175.0, datetime.datetime.utcnow())

    client = _login("dam_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/dashboard/cme-overview", params={"days": 1})
    data = resp.json()
    assert data["closed_trades"] == 1
    assert data["net_usd"] == pytest.approx(-175.0)


def test_cme_history_returns_cumulative_usd_series(env):
    now = datetime.datetime.utcnow()
    env["make_cme_order"](env["owner_account_id"], 1.0, 100.0, now - datetime.timedelta(hours=2))
    env["make_cme_order"](env["owner_account_id"], -0.5, -40.0, now - datetime.timedelta(hours=1))

    client = _login("dam_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/dashboard/cme-history")
    data = resp.json()
    assert data["ok"] is True
    assert len(data["pnl_series"]) == 2
    assert data["pnl_series"][0]["cumulative_usd"] == pytest.approx(100.0)
    assert data["pnl_series"][1]["cumulative_usd"] == pytest.approx(60.0)
    assert len(data["trades"]) == 2


# ------------------------------------------------------------------ Alt Matrix

def test_alt_matrix_overview_requires_login(env):
    client = TestClient(app)
    resp = client.get("/api/dashboard/alt-matrix-overview")
    assert resp.status_code == 401


def test_alt_matrix_overview_scoped_to_owner_only(env):
    now = datetime.datetime.utcnow()
    env["make_am_order"](env["owner_account_id"], 1.5, 150.0, now)
    env["make_am_order"](env["other_account_id"], 3.0, 300.0, now)   # not this user's

    client = _login("dam_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/dashboard/alt-matrix-overview")
    data = resp.json()
    assert data["ok"] is True
    assert data["closed_trades"] == 1
    assert data["net_r"] == pytest.approx(1.5)
    assert data["net_usd"] == pytest.approx(150.0)


def test_alt_matrix_history_days_param_filters(env):
    env["make_am_order"](env["owner_account_id"], 1.0, 100.0, datetime.datetime.utcnow() - datetime.timedelta(days=10))
    env["make_am_order"](env["owner_account_id"], 2.0, 200.0, datetime.datetime.utcnow())

    client = _login("dam_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/dashboard/alt-matrix-history", params={"days": 1})
    data = resp.json()
    assert len(data["trades"]) == 1
    assert data["trades"][0]["realized_pnl_usd"] == pytest.approx(200.0)
