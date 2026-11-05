"""Regression coverage for POST /api/executor/orders/{id}/cancel -- the
Traveler desk's new single-order cancel/close route (desk-page build,
2026-11-xx). Same owner-or-admin auth style as tests/test_cme_routes.py,
same real-fake-Bitunix-client style as tests/test_executor_live_e1_engine.py
for the two real (non-mocked) happy-path tests, so the actual
cancel_traveler_entry_order()/_market_close_traveler_order() wiring is
proven, not just the route's auth branch."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_traveler_cancel_route.db"
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
from database import SessionLocal, UserModel, ExecutorAccount, ExecutorOrder, TravelerPlan, ExecutorAuditLog
import auth
import executor_accounts as ea
import executor_bitunix_client as ebc
from main import app


def _clean_db_files():
    for path in ["kabroda_test_traveler_cancel_route.db", "kabroda_test_traveler_cancel_route.db-journal",
                 "kabroda_test_traveler_cancel_route.db-shm", "kabroda_test_traveler_cancel_route.db-wal"]:
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
    for model in (ExecutorOrder, TravelerPlan, ExecutorAuditLog, ExecutorAccount):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "trav_admin@kabroda.com", "trav_owner@kabroda.com", "trav_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="trav_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="travadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="trav_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="travowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="trav_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="travother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()
    owner_id = owner.id

    account = ea.create_account(db, user_id=owner_id, label="trav_owner_bitunix")
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()
    account_id = account.id

    plan = TravelerPlan(
        symbol="BTC/USDT", date_key="2026-11-05", session_id="us_ny_futures", status="WAITING_TOUCH",
        direction="LONG", breakout_trigger=100.0, breakdown_trigger=90.0, stop_price=90.0, t1_price=106.18,
        journey_cap_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3),
    )
    db.add(plan)
    db.commit()
    plan_id = plan.id

    yield {"db": db, "account_id": account_id, "plan_id": plan_id}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


def _make_order(db, account_id, plan_id, **overrides):
    defaults = dict(
        trade_plan_id=plan_id, traveler_plan_id=plan_id, account_id=account_id, mode="LIVE",
        symbol="BTC/USDT", direction="LONG", entry_price=100.0, stop_price=90.0, t1_price=106.18,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="PENDING_ENTRY",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
    )
    defaults.update(overrides)
    order = ExecutorOrder(**defaults)
    db.add(order)
    db.commit()
    return order


def _async(value=None, exc=None):
    async def _fake(self, *a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


# ------------------------------------------------------------------ auth / basic routing

def test_cancel_non_owner_gets_403(env):
    order = _make_order(env["db"], env["account_id"], env["plan_id"])
    client = _login("trav_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cancel_non_owning_admin_gets_403(env):
    """Same mutual-isolation rule as every other account-mutation route on
    this domain (see test_cme_routes.py's own test of the same rule) --
    _executor_owner_or_admin() grants no admin bypass at all."""
    order = _make_order(env["db"], env["account_id"], env["plan_id"])
    client = _login("trav_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cancel_no_such_order_404(env):
    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/orders/999999/cancel")
    assert resp.status_code == 404


def test_cancel_already_terminal_order_returns_400(env):
    order = _make_order(env["db"], env["account_id"], env["plan_id"], management_state="CLOSED_T1")
    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 400


# ------------------------------------------------------------------ resting entry -> real cancel path

def test_cancel_resting_entry_succeeds(env, monkeypatch):
    order = _make_order(env["db"], env["account_id"], env["plan_id"],
                         management_state="PENDING_ENTRY", entry_exchange_order_id="entry-1")
    monkeypatch.setattr(ebc.BitunixClient, "cancel_orders",
                         _async({"code": 0, "data": {"successList": [{"orderId": "entry-1"}], "failureList": []}, "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "get_order_detail",
                         _async({"code": 0, "data": {"orderId": "entry-1", "status": "CANCELED"}, "msg": "ok"}))

    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "cancelled"

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "CLOSED_MANUAL"
    assert order.exit_reason == "MANUAL"


def test_cancel_resting_entry_second_call_after_success_400s_not_double_cancels(env, monkeypatch):
    """The real financial-risk case: a double-click on the Cancel button
    must never fire a second real cancel_orders() call against the
    exchange once the order is already terminal."""
    order = _make_order(env["db"], env["account_id"], env["plan_id"],
                         management_state="PENDING_ENTRY", entry_exchange_order_id="entry-1")
    monkeypatch.setattr(ebc.BitunixClient, "cancel_orders",
                         _async({"code": 0, "data": {"successList": [{"orderId": "entry-1"}], "failureList": []}, "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "get_order_detail",
                         _async({"code": 0, "data": {"orderId": "entry-1", "status": "CANCELED"}, "msg": "ok"}))

    client = _login("trav_owner@kabroda.com", "ownerpass123")
    first = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert first.status_code == 200

    second = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert second.status_code == 400


def test_cancel_resting_entry_raced_by_a_real_fill_returns_502_not_a_false_cancel(env, monkeypatch):
    order = _make_order(env["db"], env["account_id"], env["plan_id"],
                         management_state="PENDING_ENTRY", entry_exchange_order_id="entry-1")
    monkeypatch.setattr(ebc.BitunixClient, "cancel_orders",
                         _async({"code": 0, "data": {"successList": [], "failureList": []}, "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "get_order_detail",
                         _async({"code": 0, "data": {"orderId": "entry-1", "status": "FILLED"}, "msg": "ok"}))

    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 502

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "PENDING_ENTRY"   # untouched -- never falsely booked as cancelled


# ------------------------------------------------------------------ open position -> real close path

def test_close_open_position_succeeds(env, monkeypatch):
    order = _make_order(env["db"], env["account_id"], env["plan_id"],
                         management_state="ENTRY_FILLED_ORDERS_PLACED", entry_fill_price=100.0,
                         position_id="pos-1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(ebc.BitunixClient, "close_position", _async({"code": 0, "data": {}, "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "get_position", _async({"code": 0, "data": [], "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "get_order_detail",
                         _async({"code": 0, "data": {"orderId": "t1-1", "status": "NEW"}, "msg": "ok"}))
    monkeypatch.setattr(ebc.BitunixClient, "cancel_orders",
                         _async({"code": 0, "data": {"successList": [{"orderId": "t1-1"}], "failureList": []}, "msg": "ok"}))

    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "closed"

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "CLOSED_MANUAL"


def test_close_open_position_unconfirmed_returns_502_not_a_false_close(env, monkeypatch):
    order = _make_order(env["db"], env["account_id"], env["plan_id"],
                         management_state="ENTRY_FILLED_ORDERS_PLACED", entry_fill_price=100.0,
                         position_id="pos-1")
    monkeypatch.setattr(ebc.BitunixClient, "close_position", _async({"code": 0, "data": {}, "msg": "ok"}))
    # Still shows an open position on every confirm attempt -- close never confirmed.
    monkeypatch.setattr(ebc.BitunixClient, "get_position",
                         _async({"code": 0, "data": [{"positionId": "pos-1", "symbol": "BTCUSDT", "side": "BUY", "avgOpenPrice": "100.0", "qty": "0.01"}], "msg": "ok"}))

    client = _login("trav_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/orders/{order.id}/cancel")
    assert resp.status_code == 502

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"   # untouched
