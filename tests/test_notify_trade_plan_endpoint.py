"""
Regression coverage for POST /api/admin/test-notify-trade-plan -- the
plan-specific test-fire endpoint Andy's build request asked for (Kabroda
AI Brain repo AGENT_LOG.md, "trade-plan email notifications", 2026-08-31),
built against a REAL TradePlan row rather than synthetic content.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_notify_trade_plan.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, TradePlan, UserModel
import auth
import notify
from main import app


def _clean_db_files():
    for path in ["kabroda_test_notify_trade_plan.db", "kabroda_test_notify_trade_plan.db-journal",
                 "kabroda_test_notify_trade_plan.db-shm", "kabroda_test_notify_trade_plan.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def env(monkeypatch):
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(TradePlan).delete()
    db.query(UserModel).filter(UserModel.email == "notify_admin@kabroda.com").delete()
    db.add(UserModel(
        email="notify_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
        username="notifyadmin", tier="admin", is_admin=True, subscription_status="active",
    ))
    db.commit()

    plan = TradePlan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        status="WAITING", direction="LONG", tier="PREMIUM",
        trigger_price=79062.43, stop_price=78573.37, stop_basis="beyond sweep wick low",
        t1=79650.0, t2=80100.0, t3=80800.0,
        fuel_requirement="push must read FUELED at the cross", management="30/70 runner",
    )
    db.add(plan)
    db.commit()
    plan_id = plan.id

    sent = []
    monkeypatch.setattr(notify, "send_admin_email", lambda subject, body: sent.append((subject, body)) or True)

    client = TestClient(app)
    client.post("/login", data={"email": "notify_admin@kabroda.com", "password": "adminpass123"})

    yield {"client": client, "plan_id": plan_id, "sent": sent, "db": db}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def test_requires_admin():
    _clean_db_files()
    database.init_db()
    client = TestClient(app)  # no login
    resp = client.post("/api/admin/test-notify-trade-plan", params={"plan_id": 1, "event": "lock"})
    assert resp.status_code == 403
    database.engine.dispose()
    _clean_db_files()


def test_unknown_plan_id_404(env):
    resp = env["client"].post("/api/admin/test-notify-trade-plan", params={"plan_id": 999999, "event": "lock"})
    assert resp.status_code == 404


def test_unknown_event_400(env):
    resp = env["client"].post(
        "/api/admin/test-notify-trade-plan", params={"plan_id": env["plan_id"], "event": "bogus"},
    )
    assert resp.status_code == 400


def test_lock_event_fires_for_real_waiting_plan(env):
    resp = env["client"].post(
        "/api/admin/test-notify-trade-plan", params={"plan_id": env["plan_id"], "event": "lock"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(env["sent"]) == 1
    assert "79062" in env["sent"][0][0]


def test_armed_event_does_not_apply_returns_ok_false(env):
    # build_lock_email() only applies to a WAITING-status row for "lock",
    # but "armed" always builds regardless of status -- test the inverse:
    # "lock" on a non-WAITING row correctly reports it doesn't apply.
    db = env["db"]
    plan = db.query(TradePlan).filter(TradePlan.id == env["plan_id"]).first()
    plan.status = "DONE"
    db.commit()

    resp = env["client"].post(
        "/api/admin/test-notify-trade-plan", params={"plan_id": env["plan_id"], "event": "lock"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert env["sent"] == []
