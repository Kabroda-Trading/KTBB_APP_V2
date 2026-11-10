"""Regression coverage for CME's new HTTP routes: GET /api/radar/cme-snapshot
(public), GET /api/admin/cme-status (admin-only), GET/POST
/api/executor/accounts/{id}/cme-config (owner-or-admin), POST
/api/tradovate/flatten_all (owner-or-admin, mocked executor). Same style as
tests/test_alt_matrix_routes.py."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_cme_routes.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
import datetime
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, ExecutorAccount, CMEPlan, CMEOrder, CMEConfig, CMETransition
import auth
import executor_accounts as ea
from main import app


def _clean_db_files():
    for path in ["kabroda_test_cme_routes.db", "kabroda_test_cme_routes.db-journal",
                 "kabroda_test_cme_routes.db-shm", "kabroda_test_cme_routes.db-wal"]:
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
    for model in (CMETransition, CMEOrder, CMEPlan, CMEConfig, ExecutorAccount):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "cme_admin@kabroda.com", "cme_owner@kabroda.com", "cme_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="cme_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="cmeadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="cme_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="cmeowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="cme_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="cmeother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()
    owner_id, other_id = owner.id, other.id

    account = ea.create_account(db, user_id=owner_id, label="cme_owner_tradovate")
    db.commit()
    account_id = account.id

    yield {"account_id": account_id, "owner_id": owner_id, "other_id": other_id, "db": db}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


# ------------------------------------------------------------------ GET /api/radar/cme-snapshot (public)

def test_public_snapshot_works_with_no_login(env):
    client = TestClient(app)
    resp = client.get("/api/radar/cme-snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["symbols"].keys()) == {"MNQ", "MES"}


def _today_ny_date_key():
    import cme_clock
    return datetime.datetime.now(datetime.timezone.utc).astimezone(cme_clock.NY_TZ).strftime("%Y-%m-%d")


def test_public_snapshot_reflects_a_real_armed_plan(env):
    db = env["db"]
    db.add(CMEPlan(symbol="MNQ", date_key=_today_ny_date_key(), lock_time=datetime.datetime.utcnow(),
                    status="ARMED", box=20.0, reachability=0.4, bo=4500.0, bd=4480.0))
    db.commit()

    client = TestClient(app)
    resp = client.get("/api/radar/cme-snapshot")
    assert resp.status_code == 200
    assert resp.json()["symbols"]["MNQ"]["plan"]["status"] == "ARMED"


def test_public_snapshot_does_not_fabricate_a_plan_with_no_lock_yet(env):
    client = TestClient(app)
    resp = client.get("/api/radar/cme-snapshot")
    assert resp.json()["symbols"]["MES"]["plan"] is None


def test_public_snapshot_does_not_leak_yesterdays_plan_as_today(env):
    """2026-11-10 (audit Bug 3) -- a stale row from a prior day (even one
    carrying a MISSED_WINDOW / Databento 422 error) must never surface as
    today's live state. Only today's date_key is eligible."""
    db = env["db"]
    # A genuinely-old failed lock with the exact error string the audit found.
    db.add(CMEPlan(symbol="MNQ", date_key="2026-11-09", lock_time=datetime.datetime(2026, 11, 9, 15, 0, 0),
                    status="MISSED_WINDOW", status_reason="Databento bars query failed (422)"))
    db.commit()

    client = TestClient(app)
    resp = client.get("/api/radar/cme-snapshot")
    assert resp.status_code == 200
    # No row for today's date_key -> MNQ must report null, NOT yesterday's row.
    assert resp.json()["symbols"]["MNQ"]["plan"] is None


def test_public_snapshot_prefers_todays_plan_over_a_stale_one(env):
    db = env["db"]
    today = _today_ny_date_key()
    db.add(CMEPlan(symbol="MES", date_key="2026-11-09", lock_time=datetime.datetime(2026, 11, 9, 15, 0, 0),
                    status="MISSED_WINDOW", status_reason="stale"))
    db.add(CMEPlan(symbol="MES", date_key=today, lock_time=datetime.datetime.utcnow(),
                    status="ARMED", box=20.0, reachability=0.4, bo=4500.0, bd=4480.0))
    db.commit()

    client = TestClient(app)
    resp = client.get("/api/radar/cme-snapshot")
    assert resp.json()["symbols"]["MES"]["plan"]["status"] == "ARMED"


# ------------------------------------------------------------------ GET /api/admin/cme-status

def test_admin_status_requires_login(env):
    client = TestClient(app)
    resp = client.get("/api/admin/cme-status")
    assert resp.status_code == 403


def test_admin_status_rejects_non_admin_owner(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/admin/cme-status")
    assert resp.status_code == 403


def test_admin_status_allows_admin_and_returns_configs(env):
    db = env["db"]
    db.add(CMEConfig(account_id=env["account_id"], mnq_enabled=True, mes_enabled=False))
    db.commit()

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["configs"]) == 1
    assert body["configs"][0]["account_id"] == env["account_id"]
    assert body["configs"][0]["mnq_enabled"] is True
    assert body["configs"][0]["mes_enabled"] is False


def test_admin_status_includes_execution_provider_fields(env):
    """2026-11-05 -- execution_provider/crosstrade_account_id were added
    to ExecutorAccount the night CrossTrade was wired in, but never
    surfaced on this route -- the CME desk page needs them."""
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.execution_provider = "crosstrade"
    account.crosstrade_account_id = "72422359"
    db.add(CMEConfig(account_id=env["account_id"], mnq_enabled=True, mes_enabled=False))
    db.commit()

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-status")
    cfg = resp.json()["configs"][0]
    assert cfg["execution_provider"] == "crosstrade"
    assert cfg["crosstrade_account_id"] == "72422359"


def test_admin_status_defaults_execution_provider_to_tradovate(env):
    db = env["db"]
    db.add(CMEConfig(account_id=env["account_id"], mnq_enabled=True, mes_enabled=False))
    db.commit()

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-status")
    assert resp.json()["configs"][0]["execution_provider"] == "tradovate"


# ------------------------------------------------------------------ GET /api/admin/cme-market-data-test

def test_market_data_test_requires_admin(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/admin/cme-market-data-test")
    assert resp.status_code == 403


def test_market_data_test_calls_the_real_fetch_function_and_reports_success(env, monkeypatch):
    import cme_market

    async def _fake_fetch(symbol, date_key):
        return [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}] * 15
    monkeypatch.setattr(cme_market, "fetch_calibration_1m", _fake_fetch)

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-market-data-test?symbol=MNQ&date_key=2026-11-04")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["symbol"] == "MNQ"
    assert body["date_key"] == "2026-11-04"
    assert body["bar_count"] == 15
    assert body["first_bar"] == {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}


def test_market_data_test_surfaces_a_real_fetch_failure_clearly(env, monkeypatch):
    import cme_market

    async def _fake_fail(symbol, date_key):
        raise RuntimeError("Failed to fetch calibration 1m bars for MES on 2026-11-04")
    monkeypatch.setattr(cme_market, "fetch_calibration_1m", _fake_fail)

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-market-data-test?symbol=MES&date_key=2026-11-04")
    assert resp.status_code == 502
    assert "Failed to fetch calibration" in resp.json()["error"]


def test_market_data_test_defaults_date_key_to_today_ny(env, monkeypatch):
    import cme_market

    seen = {}
    async def _fake_fetch(symbol, date_key):
        seen["date_key"] = date_key
        return []
    monkeypatch.setattr(cme_market, "fetch_calibration_1m", _fake_fetch)

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/cme-market-data-test?symbol=MNQ")
    assert resp.status_code == 200
    assert seen["date_key"] == resp.json()["date_key"]
    assert len(seen["date_key"]) == 10   # "YYYY-MM-DD"


# ------------------------------------------------------------------ POST /api/admin/cme-order-placement-test/{id}(/cancel)

def test_order_placement_test_requires_admin(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/admin/cme-order-placement-test/{env['account_id']}")
    assert resp.status_code == 403


def test_order_placement_test_calls_the_real_client_and_returns_its_response(env, monkeypatch):
    import cme_executor as cx

    class _FakeClient:
        async def place_bracket_limit_order(self, symbol, action, qty, limit_price, stop_price, target_price):
            assert symbol == "MNQ" and action == "Buy" and qty == 1
            assert limit_price < stop_price < target_price or stop_price < limit_price < target_price
            return {"orderId": 999888777}

    monkeypatch.setattr(cx, "_client_for", lambda account: _FakeClient())

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/admin/cme-order-placement-test/{env['account_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["response"]["orderId"] == 999888777


def test_order_placement_test_surfaces_a_real_failure_clearly(env, monkeypatch):
    import cme_executor as cx

    class _FakeClient:
        async def place_bracket_limit_order(self, *a, **k):
            raise RuntimeError("CrossTrade order placement failed (400): unknown_account")

    monkeypatch.setattr(cx, "_client_for", lambda account: _FakeClient())

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/admin/cme-order-placement-test/{env['account_id']}")
    assert resp.status_code == 502
    assert "unknown_account" in resp.json()["error"]


def test_order_placement_test_cancel_requires_admin(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/admin/cme-order-placement-test/{env['account_id']}/cancel?order_id=1")
    assert resp.status_code == 403


def test_order_placement_test_cancel_calls_the_real_client(env, monkeypatch):
    import cme_executor as cx

    seen = {}
    class _FakeClient:
        async def cancel_order(self, order_id):
            seen["order_id"] = order_id
            return True

    monkeypatch.setattr(cx, "_client_for", lambda account: _FakeClient())

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/admin/cme-order-placement-test/{env['account_id']}/cancel?order_id=999888777")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["cancelled"] is True
    assert seen["order_id"] == 999888777


# ------------------------------------------------------------------ GET/POST /api/executor/accounts/{id}/cme-config

def test_get_config_non_owner_gets_403(env):
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/cme-config")
    assert resp.status_code == 403


def test_get_config_reports_schema_defaults_with_no_row_and_creates_nothing(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/cme-config")
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["mnq_enabled"] is True and cfg["mes_enabled"] is True
    assert cfg["daily_risk_budget_usd"] == 350.0
    assert cfg["contracts_cap"] == 3
    assert cfg["trailing_dd_limit_usd"] == 2000.0

    db = env["db"]
    assert db.query(CMEConfig).filter_by(account_id=env["account_id"]).first() is None


def test_post_config_creates_row_on_first_touch_and_applies_only_provided_fields(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/cme-config", json={"mes_enabled": False, "contracts_cap": 1})
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["mnq_enabled"] is True   # unchanged default
    assert cfg["mes_enabled"] is False
    assert cfg["contracts_cap"] == 1
    assert cfg["daily_risk_budget_usd"] == 350.0   # untouched

    db = env["db"]
    row = db.query(CMEConfig).filter_by(account_id=env["account_id"]).first()
    assert row is not None and row.mes_enabled is False and row.contracts_cap == 1


def test_post_config_non_owner_gets_403(env):
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/cme-config", json={"mnq_enabled": False})
    assert resp.status_code == 403


# ------------------------------------------------------------------ POST /api/tradovate/flatten_all

def test_flatten_all_non_owner_gets_403(env):
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.post("/api/tradovate/flatten_all", params={"account_id": env["account_id"]})
    assert resp.status_code == 403


def test_flatten_all_no_such_account_404(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/tradovate/flatten_all", params={"account_id": 999999})
    assert resp.status_code == 404


def test_flatten_all_owner_succeeds_with_mocked_executor(env, monkeypatch):
    import cme_executor
    mock_flatten = AsyncMock(return_value={"cancelled": [1], "closed": [2], "errors": []})
    monkeypatch.setattr(cme_executor, "emergency_flatten", mock_flatten)

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/tradovate/flatten_all", params={"account_id": env["account_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["cancelled"] == [1]
    assert body["closed"] == [2]
    mock_flatten.assert_awaited_once()


# ------------------------------------------------------------------ POST /api/cme/orders/{id}/cancel

def _make_order(db, account_id, **overrides):
    plan = CMEPlan(symbol="MNQ", date_key="2026-11-05", lock_time=datetime.datetime(2026, 11, 5, 15, 0, 0),
                    status="ARMED", box=20.0, bo=4500.0, bd=4480.0)
    db.add(plan)
    db.flush()
    defaults = dict(cme_plan_id=plan.id, account_id=account_id, mode="LIVE", symbol="MNQ",
                     direction="LONG", contracts=1, management_state="WAITING_RETEST")
    defaults.update(overrides)
    order = CMEOrder(**defaults)
    db.add(order)
    db.commit()
    return order


def test_cme_cancel_order_non_owner_gets_403(env):
    order = _make_order(env["db"], env["account_id"])
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cme_cancel_order_no_such_order_404(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/cme/orders/999999/cancel")
    assert resp.status_code == 404


def test_cme_cancel_order_owner_succeeds_with_mocked_executor(env, monkeypatch):
    import cme_executor
    order = _make_order(env["db"], env["account_id"])
    mock_cancel = AsyncMock(return_value={"action": "cancelled", "order_id": order.id})
    monkeypatch.setattr(cme_executor, "cancel_or_close_order", mock_cancel)

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "cancelled"
    mock_cancel.assert_awaited_once()


def test_cme_cancel_order_non_owning_admin_gets_403(env, monkeypatch):
    """_executor_owner_or_admin() is a misleading name -- confirmed via
    its own docstring and this real test: Andy's 2026-09-07 ruling made
    this domain full mutual isolation, with NO admin bypass at all (admin
    only governs OTHER parts of the site). An admin who doesn't own this
    account gets 403 here, same as any other non-owner -- same as every
    other account-mutation route on this domain (kill-switch, flatten_all,
    cme-config)."""
    import cme_executor
    order = _make_order(env["db"], env["account_id"])
    monkeypatch.setattr(cme_executor, "cancel_or_close_order",
                         AsyncMock(return_value={"action": "closed", "order_id": order.id}))

    client = _login("cme_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert resp.status_code == 403


def test_cme_cancel_order_already_terminal_returns_400(env, monkeypatch):
    import cme_executor
    order = _make_order(env["db"], env["account_id"], management_state="CLOSED_STOP")
    # Not mocked -- exercises the REAL cancel_or_close_order() so this test
    # proves the actual ValueError-on-terminal-state path, not a mock's promise.

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert resp.status_code == 400


def test_cme_cancel_order_second_call_after_success_400s_not_double_cancels(env, monkeypatch):
    """Route-level version of the double-cancel race test in
    test_cme_executor.py -- confirms the SAME real behavior through the
    actual HTTP route, not just the underlying function."""
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    ea.set_credentials(db, account, api_key="fake-user", api_secret="fake-pass", set_by="test@kabroda.com")
    db.commit()
    order = _make_order(db, env["account_id"], entry_exchange_order_id="777")

    async def _fake_cancel(self, *a, **kw):
        return True
    monkeypatch.setattr(tvc.TradovateClient, "cancel_order", _fake_cancel)

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    first = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert first.status_code == 200
    assert first.json()["action"] == "cancelled"

    second = client.post(f"/api/cme/orders/{order.id}/cancel")
    assert second.status_code == 400


# ------------------------------------------------------------------ POST /api/executor/accounts/{id}/tradovate-account-info

def test_set_tradovate_account_info_persists_and_is_used_by_verify_auth(env, monkeypatch):
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info",
                        json={"tradovate_account_id": 987654, "tradovate_account_name": "APEX987654"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["account"]["tradovate_account_id"] == 987654
    assert body["account"]["tradovate_account_name"] == "APEX987654"

    db.refresh(account)
    assert account.tradovate_account_id == 987654
    assert account.tradovate_account_name == "APEX987654"

    seen_account_ids = []

    def _spy_init(self, username, password, account_id, account_name, is_demo=False):
        seen_account_ids.append(account_id)
        self.username, self.password, self.account_id, self.account_name, self.is_demo = (
            username, password, account_id, account_name, is_demo)

    monkeypatch.setattr(tvc.TradovateClient, "__init__", _spy_init)
    monkeypatch.setattr(tvc.TradovateClient, "get_balance", lambda self: _async_ok({"cashBalance": 1.0}))
    monkeypatch.setattr(tvc.TradovateClient, "get_positions", lambda self: _async_ok([]))

    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "u", "api_secret": "p"})
    client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert seen_account_ids == [987654]   # not 0 -- the whole point of this route


def test_set_tradovate_account_info_persists_is_demo_and_is_used_by_default(env, monkeypatch):
    """2026-11-04 -- found while setting up the first real Tradovate
    account (MFFU, an evaluation account that trades on Tradovate's demo
    gateway, not live): the real trading path (cme_executor._client_for())
    hardcoded is_demo=False with no account-level override at all. This
    covers the stored-field default path; the next test covers the
    explicit ?demo= query override."""
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info",
                        json={"tradovate_account_id": 1, "tradovate_is_demo": True})
    assert resp.status_code == 200
    assert resp.json()["account"]["tradovate_is_demo"] is True

    db.refresh(account)
    assert account.tradovate_is_demo is True

    seen_is_demo = []

    def _spy_init(self, username, password, account_id, account_name, is_demo=False):
        seen_is_demo.append(is_demo)

    monkeypatch.setattr(tvc.TradovateClient, "__init__", _spy_init)
    monkeypatch.setattr(tvc.TradovateClient, "get_balance", lambda self: _async_ok({"cashBalance": 1.0}))
    monkeypatch.setattr(tvc.TradovateClient, "get_positions", lambda self: _async_ok([]))

    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "u", "api_secret": "p"})
    client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert seen_is_demo == [True]   # picked up the stored account field, no query override given


def test_connection_demo_query_param_overrides_stored_is_demo(env, monkeypatch):
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    account.tradovate_is_demo = True
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "u", "api_secret": "p"})

    seen_is_demo = []

    def _spy_init(self, username, password, account_id, account_name, is_demo=False):
        seen_is_demo.append(is_demo)

    monkeypatch.setattr(tvc.TradovateClient, "__init__", _spy_init)
    monkeypatch.setattr(tvc.TradovateClient, "get_balance", lambda self: _async_ok({"cashBalance": 1.0}))
    monkeypatch.setattr(tvc.TradovateClient, "get_positions", lambda self: _async_ok([]))

    client.post(f"/api/executor/accounts/{env['account_id']}/test-connection?demo=false")
    assert seen_is_demo == [False]   # explicit override wins over the stored True


def test_set_tradovate_account_info_persists_crosstrade_provider(env):
    """2026-11-04 -- added once a real evaluation account (MFFU) confirmed
    direct Tradovate API access is gated off for unfunded accounts."""
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info",
                        json={"tradovate_account_id": 0, "execution_provider": "crosstrade",
                              "crosstrade_account_id": "DEMO12345678"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"]["execution_provider"] == "crosstrade"
    assert body["account"]["crosstrade_account_id"] == "DEMO12345678"

    db.refresh(account)
    assert account.execution_provider == "crosstrade"
    assert account.crosstrade_account_id == "DEMO12345678"


def test_set_tradovate_account_info_rejects_an_invalid_execution_provider(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info",
                        json={"tradovate_account_id": 1, "execution_provider": "bogus"})
    assert resp.status_code == 400


def test_connection_routes_crosstrade_accounts_to_the_crosstrade_client(env, monkeypatch):
    import cme_crosstrade_client as ctc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    account.execution_provider = "crosstrade"
    account.crosstrade_account_id = "DEMO12345678"
    db.commit()

    seen = []

    def _spy_init(self, bearer_token, crosstrade_account_id):
        seen.append((bearer_token, crosstrade_account_id))

    monkeypatch.setattr(ctc.CrossTradeClient, "__init__", _spy_init)
    monkeypatch.setattr(ctc.CrossTradeClient, "get_balance", lambda self: _async_ok({"cashBalance": 1.0}))
    monkeypatch.setattr(ctc.CrossTradeClient, "get_positions", lambda self: _async_ok([]))

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "the-bearer-token", "api_secret": "n/a"})
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert seen == [("the-bearer-token", "DEMO12345678")]


def test_connection_crosstrade_without_account_id_gives_a_clear_error(env):
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    account.execution_provider = "crosstrade"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "token", "api_secret": "n/a"})
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 400
    assert "crosstrade_account_id" in resp.json()["error"]


def test_set_tradovate_account_info_non_owner_gets_403(env):
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info",
                        json={"tradovate_account_id": 1})
    assert resp.status_code == 403


def test_set_tradovate_account_info_requires_an_id(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tradovate-account-info", json={})
    assert resp.status_code == 422   # tradovate_account_id is required, not optional


async def _async_ok(value):
    return value


# ------------------------------------------------------------------ POST /api/executor/accounts/{id}/test-connection (Tradovate branch)

def test_connection_routes_tradovate_accounts_to_the_tradovate_client(env, monkeypatch):
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                        json={"api_key": "fake-user", "api_secret": "fake-pass"})
    assert resp.status_code == 200

    async def _fake_balance(self):
        return {"cashBalance": 48765.0}

    async def _fake_positions(self):
        return []

    monkeypatch.setattr(tvc.TradovateClient, "get_balance", _fake_balance)
    monkeypatch.setattr(tvc.TradovateClient, "get_positions", _fake_positions)

    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["checks"]["get_balance"]["response"]["cashBalance"] == 48765.0
    assert body["checks"]["get_positions"]["response"] == []


# ------------------------------------------------------------------ GET /api/executor/accounts/{id}/tradovate-accounts

def test_lookup_tradovate_accounts_returns_id_and_name_for_each_account(env, monkeypatch):
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "u", "api_secret": "p"})

    async def _fake_list_accounts(self):
        return [{"id": 6918900000001, "name": "APEX6918900000001", "active": True},
                {"id": 6918900000002, "name": "APEX6918900000002", "active": False}]
    monkeypatch.setattr(tvc.TradovateClient, "list_accounts", _fake_list_accounts)

    resp = client.get(f"/api/executor/accounts/{env['account_id']}/tradovate-accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["accounts"] == [
        {"id": 6918900000001, "name": "APEX6918900000001", "active": True},
        {"id": 6918900000002, "name": "APEX6918900000002", "active": False},
    ]


def test_lookup_tradovate_accounts_demo_query_param_works_before_anything_is_saved(env, monkeypatch):
    """2026-11-04 -- real UX bug found live: SAVE refuses to persist
    tradovate_is_demo without a valid numeric account ID already present,
    but LOOK UP is the only way to discover that ID -- a chicken-and-egg
    trap for a brand-new account with no stored tradovate_is_demo at all.
    The admin UI's LOOK UP button now sends the checkbox's live state as
    ?demo=, which must work even though nothing has been saved yet."""
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()
    assert account.tradovate_is_demo is None   # nothing saved yet -- the real scenario

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "u", "api_secret": "p"})

    seen_is_demo = []

    def _spy_init(self, username, password, account_id, account_name, is_demo=False):
        seen_is_demo.append(is_demo)

    monkeypatch.setattr(tvc.TradovateClient, "__init__", _spy_init)
    monkeypatch.setattr(tvc.TradovateClient, "list_accounts", lambda self: _async_ok([]))

    resp = client.get(f"/api/executor/accounts/{env['account_id']}/tradovate-accounts?demo=true")
    assert resp.status_code == 200
    assert seen_is_demo == [True]   # query override worked despite no stored value


def test_lookup_tradovate_accounts_requires_credentials_first(env):
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/tradovate-accounts")
    assert resp.status_code == 400


def test_lookup_tradovate_accounts_rejects_a_non_tradovate_account(env):
    client = _login("cme_owner@kabroda.com", "ownerpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/tradovate-accounts")
    assert resp.status_code == 400   # account.exchange defaults to "bitunix" in this fixture


def test_lookup_tradovate_accounts_non_owner_gets_403(env):
    client = _login("cme_other@kabroda.com", "otherpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/tradovate-accounts")
    assert resp.status_code == 403


def test_connection_reports_a_real_auth_failure_without_hiding_which_check_failed(env, monkeypatch):
    import cme_tradovate_client as tvc

    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.exchange = "tradovate"
    db.commit()

    client = _login("cme_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "bad-user", "api_secret": "bad-pass"})

    async def _rejected(self):
        raise RuntimeError("Tradovate authentication failed and no prior token is available")

    monkeypatch.setattr(tvc.TradovateClient, "get_balance", _rejected)
    monkeypatch.setattr(tvc.TradovateClient, "get_positions", _rejected)

    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["checks"]["get_balance"]["ok"] is False
    assert "authentication failed" in body["checks"]["get_balance"]["error"]
