"""
Regression coverage for /api/admin/traveler-plan-status --
CC_WORK_ORDER_RADAR_PLAN_PANEL.md work item 2. TestClient-based, same
style as tests/test_executor_admin_routes.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_admin_plan_status.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime as dt

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, TravelerPlan
import auth
from main import app


def _clean_db_files():
    for path in ["kabroda_test_admin_plan_status.db", "kabroda_test_admin_plan_status.db-journal",
                 "kabroda_test_admin_plan_status.db-shm", "kabroda_test_admin_plan_status.db-wal"]:
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
    db.query(UserModel).filter(UserModel.email.in_([
        "radar_admin@kabroda.com", "radar_nonadmin@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="radar_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="radaradmin", tier="admin", is_admin=True, subscription_status="active"))
    db.add(UserModel(email="radar_nonadmin@kabroda.com", password_hash=auth.hash_password("plainpass123"),
                      username="radarplain", tier="basic", is_admin=False, subscription_status="active"))
    db.commit()

    yield {"db": db}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


def _make_plan(db, **kwargs):
    defaults = dict(
        symbol="BTC/USDT", date_key="2026-09-18", session_id="us_ny_futures",
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=81414.10, breakdown_trigger=80000.0,
        stop_price=81100.0, t1_price=82000.0,
        cross_time=dt.datetime(2026, 9, 18, 14, 5, 0), cross_price=81414.10,
        journey_cap_at=dt.datetime(2026, 9, 25, 14, 5, 0),
        last_transition_reason="LONG cross confirmed at 81,414.10 -- resting limit at 81,414.10, watching for a trigger touch",
    )
    defaults.update(kwargs)
    row = TravelerPlan(**defaults)
    db.add(row)
    db.commit()
    return row


def test_traveler_plan_status_requires_admin(env):
    client = _login("radar_nonadmin@kabroda.com", "plainpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 403


def test_traveler_plan_status_empty_when_no_rows(env):
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["rows"] == []


def test_traveler_plan_status_returns_real_fields(env):
    _make_plan(env["db"])
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["status"] == "WAITING_TOUCH"
    assert row["direction"] == "LONG"
    assert row["breakout_trigger"] == 81414.10
    assert row["stop_price"] == 81100.0
    assert row["t1_price"] == 82000.0
    assert row["cross_price"] == 81414.10
    assert row["fill_time"] is None
    assert row["fill_price"] is None
    assert row["journey_cap_at"] is not None
    assert "seconds_since_update" in row


def test_traveler_plan_status_surfaces_a_multi_day_active_journey(env):
    # The real case this endpoint exists to handle correctly: a journey
    # that crossed on an EARLIER date_key and is still active today --
    # NOT scoped to today's date_key (unlike TradePlan's own endpoint),
    # since traveler_plan_engine.py's own design allows a WAITING_TOUCH
    # journey to span up to 7 days.
    _make_plan(env["db"], date_key="2026-09-15", status="WAITING_TOUCH")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["date_key"] == "2026-09-15"
    assert row["status"] == "WAITING_TOUCH"


def test_traveler_plan_status_returns_the_most_recent_row_only(env):
    _make_plan(env["db"], date_key="2026-09-15", status="DONE", last_transition_reason="older, concluded journey")
    newer = _make_plan(env["db"], date_key="2026-09-18", status="WAITING_TOUCH", last_transition_reason="newer, active journey")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id"] == newer.id
    assert body["rows"][0]["last_transition_reason"] == "newer, active journey"


def test_traveler_plan_status_shows_fill_fields_when_filled(env):
    _make_plan(
        env["db"], status="FILLED",
        fill_time=dt.datetime(2026, 9, 18, 15, 0, 0), fill_price=81400.0,
        last_transition_reason="trigger touch fill at 81,400.00",
    )
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["status"] == "FILLED"
    assert row["fill_price"] == 81400.0
    assert row["fill_time"] is not None
    # cross_price is audit-only and DIFFERENT from fill_price on a real
    # journey -- confirms the endpoint never conflates the two fields the
    # way v2's trigger_price/fill_price collapse to the same value.
    assert row["cross_price"] == 81414.10
