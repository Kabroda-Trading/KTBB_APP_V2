"""Regression coverage for POST /api/alt-matrix/orders/{id}/cancel -- the
Alt Matrix desk's new single-order cancel/close route (desk-page build,
2026-11-xx). No cancel entry point existed for Alt Matrix at all before
this. Same owner-or-admin auth style as tests/test_cme_routes.py and
tests/test_traveler_order_cancel_route.py; the two real (non-mocked)
happy/failure-path tests use the same fake-Bitunix-client style as
tests/test_alt_matrix_executor.py so the actual market_close() wiring is
proven, not just the route's auth branch."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_alt_matrix_cancel_route.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, ExecutorAccount, AltMatrixPlan, AltMatrixOrder, AltMatrixConfig, AltMatrixTransition
import auth
import executor_accounts as ea
import executor_bitunix_client as ebc
from main import app


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_cancel_route.db", "kabroda_test_alt_matrix_cancel_route.db-journal",
                 "kabroda_test_alt_matrix_cancel_route.db-shm", "kabroda_test_alt_matrix_cancel_route.db-wal"]:
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
    for model in (AltMatrixTransition, AltMatrixOrder, AltMatrixPlan, AltMatrixConfig, ExecutorAccount):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "amc_admin@kabroda.com", "amc_owner@kabroda.com", "amc_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="amc_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="amcadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="amc_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="amcowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="amc_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="amcother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()
    owner_id = owner.id

    account = ea.create_account(db, user_id=owner_id, label="amc_owner_bitunix")
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()
    account_id = account.id

    yield {"db": db, "account_id": account_id}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


_plan_id_counter = {"n": 0}


def _make_order(db, account_id, **overrides):
    _plan_id_counter["n"] += 1
    defaults = dict(
        alt_matrix_plan_id=_plan_id_counter["n"], account_id=account_id, mode="LIVE",
        symbol="SOL/USDT", direction="LONG", management_state="PENDING_ENTRY",
        qty=10.0, r_distance=2.0, sl_price_initial=98.0,
    )
    defaults.update(overrides)
    order = AltMatrixOrder(**defaults)
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
    order = _make_order(env["db"], env["account_id"])
    client = _login("amc_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cancel_non_owning_admin_gets_403(env):
    order = _make_order(env["db"], env["account_id"])
    client = _login("amc_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cancel_no_such_order_404(env):
    client = _login("amc_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/alt-matrix/orders/999999/cancel")
    assert resp.status_code == 404


def test_cancel_pending_entry_returns_400_not_a_real_cancel_target(env):
    """Alt Matrix entries are immediate market orders -- there is no
    resting order to cancel while still PENDING_ENTRY (a sub-second
    window). This must 400, not silently no-op or error oddly."""
    order = _make_order(env["db"], env["account_id"], management_state="PENDING_ENTRY")
    client = _login("amc_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 400


def test_cancel_already_terminal_order_returns_400(env):
    order = _make_order(env["db"], env["account_id"], management_state="CLOSED_STOP")
    client = _login("amc_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 400


# ------------------------------------------------------------------ open position -> real close path

@pytest.mark.parametrize("state", ["ENTRY_FILLED_UNPROTECTED", "FILLED", "TRAILING"])
def test_cancel_closes_an_open_position_in_any_open_state(env, monkeypatch, state):
    order = _make_order(env["db"], env["account_id"], management_state=state,
                         entry_fill_price=100.0, position_id="pos-1")
    monkeypatch.setattr(ebc.BitunixClient, "close_position", _async({"code": 0, "data": {}}))
    monkeypatch.setattr(ebc.BitunixClient, "get_position", _async({"code": 0, "data": [], "msg": "ok"}))
    import market_data
    monkeypatch.setattr(market_data, "fetch_bitunix_5m", _async([{"close": "104.0"}]))

    client = _login("amc_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "closed"

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "CLOSED_MANUAL"
    assert order.exit_reason == "MANUAL"


def test_cancel_second_call_after_success_400s_not_double_closes(env, monkeypatch):
    """The real financial-risk case: a double-click on Close must never
    fire a second real close_position() call once the position is already
    terminal."""
    order = _make_order(env["db"], env["account_id"], management_state="FILLED",
                         entry_fill_price=100.0, position_id="pos-1")
    monkeypatch.setattr(ebc.BitunixClient, "close_position", _async({"code": 0, "data": {}}))
    monkeypatch.setattr(ebc.BitunixClient, "get_position", _async({"code": 0, "data": [], "msg": "ok"}))
    import market_data
    monkeypatch.setattr(market_data, "fetch_bitunix_5m", _async([{"close": "104.0"}]))

    client = _login("amc_owner@kabroda.com", "ownerpass123")
    first = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert first.status_code == 200

    second = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert second.status_code == 400


def test_cancel_unconfirmed_close_returns_502_not_a_false_close(env, monkeypatch):
    order = _make_order(env["db"], env["account_id"], management_state="FILLED",
                         entry_fill_price=100.0, position_id="pos-1")
    monkeypatch.setattr(ebc.BitunixClient, "close_position", _async({"code": 0, "data": {}}))
    # Still shows an open position on every confirm attempt -- close never confirmed.
    monkeypatch.setattr(ebc.BitunixClient, "get_position",
                         _async({"code": 0, "data": [{"positionId": "pos-1", "symbol": "SOLUSDT", "side": "BUY", "avgOpenPrice": "100.0", "qty": "10.0"}], "msg": "ok"}))

    client = _login("amc_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/alt-matrix/orders/{order.id}/cancel")
    assert resp.status_code == 502

    db = env["db"]
    db.refresh(order)
    assert order.management_state == "FILLED"   # untouched
