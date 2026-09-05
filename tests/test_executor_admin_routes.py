"""
Regression coverage for /api/executor/* admin routes -- TestClient-based,
same style as tests/test_notify_trade_plan_endpoint.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_admin_routes.db"
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
from database import (
    SessionLocal, UserModel, ExecutorAccount, ExecutorRiskState, ExecutorOrder,
    ExecutorAuditLog, ExecutorGlobalConfig, ExecutorMechanismTest,
)
import auth
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
from main import (
    app, _CONFIRM_ENABLE_LIVE_ORDERS, _CONFIRM_TINY_TEST_PLACE,
    _CONFIRM_TINY_TEST_PARTIAL_CLOSE, _CONFIRM_TINY_TEST_MOVE_SL, _CONFIRM_TINY_TEST_FLASH_CLOSE,
)


def _clean_db_files():
    for path in ["kabroda_test_executor_admin_routes.db", "kabroda_test_executor_admin_routes.db-journal",
                 "kabroda_test_executor_admin_routes.db-shm", "kabroda_test_executor_admin_routes.db-wal"]:
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
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount, ExecutorGlobalConfig, ExecutorMechanismTest):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "exec_admin@kabroda.com", "exec_owner@kabroda.com", "exec_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="exec_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="execadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="exec_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="execowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="exec_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="execother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()
    owner_id, other_id = owner.id, other.id

    account = ea.create_account(db, user_id=owner_id, label="owner_bitunix")
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


# ------------------------------------------------------------------ authorization

def test_owner_sees_only_their_own_account(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/executor/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert [a["id"] for a in body["accounts"]] == [env["account_id"]]


def test_admin_sees_all_accounts(env):
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/executor/accounts")
    body = resp.json()
    assert env["account_id"] in [a["id"] for a in body["accounts"]]


def test_non_owner_non_admin_gets_403_on_credentials(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                        json={"api_key": "k", "api_secret": "s"})
    assert resp.status_code == 403


# ------------------------------------------------------------------ test-connection (real, read-only Bitunix call -- monkeypatched here)

def test_connection_test_requires_credentials_first(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 400
    assert "credentials" in resp.json()["error"].lower()


def test_connection_test_non_owner_gets_403(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 403


def _patch_verify_auth_reads(monkeypatch, executor_bitunix_client, get_balance=None, get_leverage=None, get_pairs=None):
    """Shared helper: monkeypatch all three verify-auth read calls at
    once, each with its own configurable fake (default: a plausible
    success payload)."""
    async def default_get_balance(self, margin_coin="USDT"):
        return {"marginCoin": "USDT", "available": "1234.56", "margin": "0"}

    async def default_get_leverage(self, symbol, margin_coin="USDT"):
        return {"symbol": symbol, "leverage": 10, "marginMode": "ISOLATION"}

    async def default_get_pairs(self, symbols=None):
        return {"symbol": "BTCUSDT", "minTradeVolume": "0.0001"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_balance", get_balance or default_get_balance)
    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_leverage_and_margin_mode", get_leverage or default_get_leverage)
    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_trading_pairs", get_pairs or default_get_pairs)


def test_connection_test_success_never_places_an_order(env, monkeypatch):
    import executor_bitunix_client
    called = {"place_order": 0}

    async def fake_place_order(self, *a, **k):
        called["place_order"] += 1
        raise AssertionError("place_order must never be called by verify-auth")

    _patch_verify_auth_reads(monkeypatch, executor_bitunix_client)
    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "place_order", fake_place_order)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "real-key", "api_secret": "real-secret"})

    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["checks"]["get_balance"]["ok"] is True
    assert body["checks"]["get_balance"]["response"]["available"] == "1234.56"
    assert body["checks"]["get_leverage_and_margin_mode"]["response"]["marginMode"] == "ISOLATION"
    assert body["checks"]["get_trading_pairs"]["response"]["minTradeVolume"] == "0.0001"
    assert called["place_order"] == 0


def test_connection_test_failure_reports_error_not_500(env, monkeypatch):
    import executor_bitunix_client

    async def fake_get_balance_fails(self, margin_coin="USDT"):
        raise RuntimeError("simulated network/auth failure")

    _patch_verify_auth_reads(monkeypatch, executor_bitunix_client, get_balance=fake_get_balance_fails)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                json={"api_key": "real-key", "api_secret": "real-secret"})

    resp = client.post(f"/api/executor/accounts/{env['account_id']}/test-connection")
    assert resp.status_code == 200  # the route itself succeeds -- the failure is reported per-check
    body = resp.json()
    assert body["ok"] is False  # overall verify-auth fails since one check failed
    assert body["checks"]["get_balance"]["ok"] is False
    assert "simulated network/auth failure" in body["checks"]["get_balance"]["error"]
    # the OTHER two checks still ran and are reported independently -- one
    # failing endpoint doesn't hide whether the rest of the signing chain works
    assert body["checks"]["get_leverage_and_margin_mode"]["ok"] is True
    assert body["checks"]["get_trading_pairs"]["ok"] is True


def test_non_owner_non_admin_gets_403_on_kill_switch(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "test"})
    assert resp.status_code == 403


def test_owner_can_engage_their_own_kill_switch(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "testing"})
    assert resp.status_code == 200
    assert resp.json()["account"]["kill_switch_engaged"] is True


def test_only_admin_can_create_accounts(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/accounts", json={"user_id": env["owner_id"], "label": "second_account"})
    assert resp.status_code == 403

    admin_client = _login("exec_admin@kabroda.com", "adminpass123")
    resp2 = admin_client.post("/api/executor/accounts", json={"user_id": env["owner_id"], "label": "second_account"})
    assert resp2.status_code == 200


def test_only_admin_can_engage_global_kill_switch(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/global-kill-switch", json={"reason": "test"})
    assert resp.status_code == 403


# ------------------------------------------------------------------ credential handling never echoes the secret

def test_credential_set_response_never_contains_the_secret(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/credentials",
                        json={"api_key": "super-secret-key-abc123", "api_secret": "super-secret-value-xyz789"})
    assert resp.status_code == 200
    body_text = resp.text
    assert "super-secret-key-abc123" not in body_text
    assert "super-secret-value-xyz789" not in body_text

    # And the account listing never leaks it either.
    list_resp = client.get("/api/executor/accounts")
    assert "super-secret-key-abc123" not in list_resp.text
    assert "super-secret-value-xyz789" not in list_resp.text
    account = next(a for a in list_resp.json()["accounts"] if a["id"] == env["account_id"])
    assert account["has_credentials"] is True


# ------------------------------------------------------------------ kill-switch toggle reflected in a subsequent dry run

def test_kill_switch_toggle_reflected_in_next_plan_build(env):
    import asyncio
    import executor_plan_builder
    from database import TradePlan

    db = env["db"]
    plan = TradePlan(
        symbol="BTC/USDT", date_key="2026-09-04", session_id="us_ny_futures", status="FILLED",
        direction="LONG", tier="STANDARD", trigger_price=100.0, fill_price=100.0,
        stop_price=95.0, t1=112.0, t2=120.0, t3=132.0,
    )
    db.add(plan)
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.assumed_balance_usd = 100000.0
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    db.commit()  # flush() alone leaves an open write transaction, which
    # would block the TestClient's OWN db session (a different thread)
    # from writing -- SQLite single-writer locking, not an app bug.

    before = asyncio.run(executor_plan_builder.build_hypothetical_order(db, plan, account, state))
    assert before["decision"] == "WOULD_PLACE"

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "toggle test"})
    assert resp.status_code == 200

    db.refresh(account)
    after = asyncio.run(executor_plan_builder.build_hypothetical_order(db, plan, account, state))
    assert after["decision"] == "SKIPPED_KILL_SWITCH"


# ------------------------------------------------------------------ risk-state editing

def test_owner_can_edit_risk_state(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/risk-state",
                        json={"risk_last_usd": 250.0})
    assert resp.status_code == 200
    assert resp.json()["risk_state"]["risk_last_usd"] == 250.0

    get_resp = client.get(f"/api/executor/accounts/{env['account_id']}/risk-state")
    assert get_resp.json()["risk_state"]["risk_last_usd"] == 250.0


# ------------------------------------------------------------------ page rendering (the create-account form)

def test_admin_page_renders_create_account_form_with_user_picker(env):
    # 2026-09-05: the page shipped with the POST /api/executor/accounts
    # route wired but no actual button/form to call it -- caught live by
    # Andy after deploy. This locks in that the form (and its user
    # dropdown, admin-only) actually renders.
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.get("/admin/executor")
    assert resp.status_code == 200
    assert "CREATE ACCOUNT" in resp.text
    assert "newAccountUserId" in resp.text
    assert f"#{env['owner_id']}" in resp.text  # the owner user appears in the picker


def test_owner_page_renders_without_create_account_form(env):
    # A non-admin owner can still see the page (their own account, kill
    # switch, etc.) but must NOT see the admin-only create-account form
    # (creation itself is admin-only at the route level too).
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/admin/executor")
    assert resp.status_code == 200
    assert "CREATE ACCOUNT" not in resp.text


# ------------------------------------------------------------------ live orders global gate (Stage 2, 2026-09-05)

def test_global_config_defaults_to_both_flags_false(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/executor/global-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "global_kill_switch_engaged": False, "live_orders_enabled": False}


def test_non_admin_cannot_enable_live_orders(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/live-orders/enable", json={"reason": "testing", "confirm": _CONFIRM_ENABLE_LIVE_ORDERS})
    assert resp.status_code == 403


def test_enable_live_orders_wrong_confirm_phrase_returns_400(env):
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.post("/api/executor/live-orders/enable", json={"reason": "testing", "confirm": "nope"})
    assert resp.status_code == 400
    assert client.get("/api/executor/global-config").json()["live_orders_enabled"] is False


def test_admin_enable_then_disable_live_orders_reflected_in_global_config_and_audit_log(env):
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.post("/api/executor/live-orders/enable", json={"reason": "tiny order test", "confirm": _CONFIRM_ENABLE_LIVE_ORDERS})
    assert resp.status_code == 200
    assert client.get("/api/executor/global-config").json()["live_orders_enabled"] is True

    resp = client.post("/api/executor/live-orders/disable")
    assert resp.status_code == 200
    assert client.get("/api/executor/global-config").json()["live_orders_enabled"] is False

    audit = client.get("/api/executor/audit-log").json()["audit_log"]
    event_types = [r["event_type"] for r in audit]
    assert "LIVE_ORDERS_ENABLED" in event_types
    assert "LIVE_ORDERS_DISABLED" in event_types


# ------------------------------------------------------------------ tiny order mechanism test (Stage 2, 2026-09-05)
# REAL MONEY -- BitunixClient methods are monkeypatched at the class
# level for every test here, no real network call is ever made.

def _enable_live_orders_and_credentials(env, client_admin):
    client_admin.post("/api/executor/live-orders/enable", json={"reason": "testing", "confirm": _CONFIRM_ENABLE_LIVE_ORDERS})
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()


def _patch_happy_path_client(monkeypatch):
    call_state = {"get_position_calls": 0}

    async def fake_get_position(self, symbol):
        call_state["get_position_calls"] += 1
        # #1: pre-flight (nothing open yet). #2: post-fill lookup (found).
        # #3+: flash-close's own confirmation check -- the position is
        # gone by then.
        if call_state["get_position_calls"] in (1, 3):
            return {"code": 0, "data": [], "msg": "Success"}
        return {"code": 0, "data": [{"positionId": "pos1", "symbol": "BTCUSDT", "side": "LONG", "avgOpenPrice": "100.0", "qty": "0.0002"}], "msg": "Success"}

    async def fake_get_trading_pairs(self, symbol):
        return {"code": 0, "data": [{"symbol": "BTCUSDT", "minTradeVolume": "0.0001", "basePrecision": 4, "quotePrecision": 1}], "msg": "Success"}

    async def fake_place_order(self, **kwargs):
        return {"code": 0, "data": {"orderId": "order1", "clientId": "client1"}, "msg": "Success"}

    async def fake_get_order_detail(self, order_id=None, client_id=None):
        return {"code": 0, "data": {"orderId": order_id, "status": "FILLED"}, "msg": "Success"}

    async def fake_set_position_tpsl(self, **kwargs):
        return {"code": 0, "data": {"orderId": "tpsl1"}, "msg": "Success"}

    async def fake_modify_tpsl(self, **kwargs):
        return {"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}

    async def fake_get_pending_tp_sl_order(self, symbol=None, position_id=None):
        return {"code": 0, "data": [{"id": "tpsl1", "positionId": position_id, "tpPrice": "101.0", "slPrice": "99.0"}], "msg": "Success"}

    async def fake_close_position(self, position_id):
        return {"code": 0, "data": {"positionId": position_id}, "msg": "Success"}

    monkeypatch.setattr(ebc.BitunixClient, "get_position", fake_get_position)
    monkeypatch.setattr(ebc.BitunixClient, "get_trading_pairs", fake_get_trading_pairs)
    monkeypatch.setattr(ebc.BitunixClient, "place_order", fake_place_order)
    monkeypatch.setattr(ebc.BitunixClient, "get_order_detail", fake_get_order_detail)
    monkeypatch.setattr(ebc.BitunixClient, "get_pending_tp_sl_order", fake_get_pending_tp_sl_order)
    monkeypatch.setattr(ebc.BitunixClient, "set_position_tpsl", fake_set_position_tpsl)
    monkeypatch.setattr(ebc.BitunixClient, "modify_position_tp_sl_order", fake_modify_tpsl)
    monkeypatch.setattr(ebc.BitunixClient, "close_position", fake_close_position)


def test_tiny_test_place_blocked_when_live_orders_disabled_returns_403_even_with_correct_confirm_and_credentials(env, monkeypatch):
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()
    # live orders NOT enabled

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tiny-test/place",
                        json={"confirm": _CONFIRM_TINY_TEST_PLACE})
    assert resp.status_code == 403
    assert "live orders" in resp.json()["error"]


def test_tiny_test_place_wrong_confirm_phrase_returns_400(env):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tiny-test/place", json={"confirm": "nope"})
    assert resp.status_code == 400


def test_tiny_test_place_non_owner_non_admin_returns_403(env):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)

    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tiny-test/place",
                        json={"confirm": _CONFIRM_TINY_TEST_PLACE})
    assert resp.status_code == 403


def test_tiny_test_place_blocked_by_account_kill_switch_even_when_live_orders_enabled(env):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/tiny-test/place",
                        json={"confirm": _CONFIRM_TINY_TEST_PLACE})
    assert resp.status_code == 403
    assert "kill switch" in resp.json()["error"]


def test_tiny_test_full_ladder_happy_path_via_routes(env, monkeypatch):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)
    _patch_happy_path_client(monkeypatch)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    account_id = env["account_id"]

    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/place", json={"confirm": _CONFIRM_TINY_TEST_PLACE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["test"]["status"] == "TPSL_SET"
    test_id = body["test"]["id"]

    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/{test_id}/partial-close",
                        json={"confirm": _CONFIRM_TINY_TEST_PARTIAL_CLOSE})
    assert resp.status_code == 200
    assert resp.json()["test"]["status"] == "PARTIAL_CLOSED"

    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/{test_id}/move-sl-breakeven",
                        json={"confirm": _CONFIRM_TINY_TEST_MOVE_SL})
    assert resp.status_code == 200
    assert resp.json()["test"]["status"] == "SL_MOVED_BREAKEVEN"

    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/{test_id}/flash-close",
                        json={"confirm": _CONFIRM_TINY_TEST_FLASH_CLOSE})
    assert resp.status_code == 200
    assert resp.json()["test"]["status"] == "FULLY_CLOSED"

    audit_events = [r["event_type"] for r in client.get("/api/executor/audit-log").json()["audit_log"]]
    for expected in ("TEST_MECHANISM_STARTED", "TEST_ORDER_PLACED", "TEST_ORDER_FILL_CONFIRMED",
                     "TEST_INITIAL_TPSL_SET", "TEST_PARTIAL_CLOSED", "TEST_SL_MOVED_TO_BREAKEVEN",
                     "TEST_POSITION_FLASH_CLOSED"):
        assert expected in audit_events


def test_tiny_test_action_called_out_of_order_returns_409(env, monkeypatch):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)
    _patch_happy_path_client(monkeypatch)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    account_id = env["account_id"]

    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/place", json={"confirm": _CONFIRM_TINY_TEST_PLACE})
    test_id = resp.json()["test"]["id"]

    # Skip straight to flash-close before any partial-close -- must reject.
    resp = client.post(f"/api/executor/accounts/{account_id}/tiny-test/{test_id}/flash-close",
                        json={"confirm": _CONFIRM_TINY_TEST_FLASH_CLOSE})
    assert resp.status_code == 409


def test_tiny_test_list_endpoint_scoped_to_owner(env, monkeypatch):
    client_admin = _login("exec_admin@kabroda.com", "adminpass123")
    _enable_live_orders_and_credentials(env, client_admin)
    _patch_happy_path_client(monkeypatch)

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    account_id = env["account_id"]
    client.post(f"/api/executor/accounts/{account_id}/tiny-test/place", json={"confirm": _CONFIRM_TINY_TEST_PLACE})

    resp = client.get(f"/api/executor/accounts/{account_id}/tiny-test")
    assert resp.status_code == 200
    assert len(resp.json()["tests"]) == 1

    other_client = _login("exec_other@kabroda.com", "otherpass123")
    resp = other_client.get(f"/api/executor/accounts/{account_id}/tiny-test")
    assert resp.status_code == 403
