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
    ExecutorAuditLog, ExecutorGlobalConfig, ExecutorMechanismTest, ExecutorSizingPolicy,
)
import auth
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
from main import app, _CONFIRM_ENABLE_LIVE_ORDERS


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
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, ExecutorGlobalConfig, ExecutorMechanismTest):
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


def test_admin_no_longer_sees_other_users_accounts_in_list(env):
    # 2026-09-05: deliberate change, Andy's own explicit request -- each
    # real user runs an independent real exchange account, and admin
    # seeing everyone's mixed together by default risked confusing/
    # misclicking on the wrong one's real money. Admin gets NO listing
    # bypass anymore; only the owner sees their own account here.
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/executor/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert env["account_id"] not in [a["id"] for a in body["accounts"]]


def test_admin_no_longer_sees_other_users_orders_or_audit_log_without_explicit_account_id(env):
    client = _login("exec_admin@kabroda.com", "adminpass123")
    orders_resp = client.get("/api/executor/orders")
    assert orders_resp.status_code == 200
    assert all(o["account_id"] != env["account_id"] for o in orders_resp.json()["orders"])

    audit_resp = client.get("/api/executor/audit-log")
    assert audit_resp.status_code == 200
    assert all(r["account_id"] != env["account_id"] for r in audit_resp.json()["audit_log"])


def test_admin_cannot_act_on_another_users_account_by_id(env):
    # 2026-09-07: Andy's explicit instruction (full mutual isolation between
    # real users running real money on this system, e.g. himself and
    # "Gross Monkey") -- "I don't care about his stuff... he doesn't care
    # about mine." This replaces the old emergency-intervention bypass:
    # _executor_owner_or_admin() no longer has an unconditional is_admin
    # branch, so admin gets 403 here exactly like any other non-owner would.
    client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "admin intervention test"})
    assert resp.status_code == 403

    resp = client.get(f"/api/executor/accounts/{env['account_id']}/risk-state")
    assert resp.status_code == 403

    # Explicitly scoping /api/executor/orders by account_id is likewise
    # refused for a non-owning admin now -- ownership is the only path in.
    orders_resp = client.get(f"/api/executor/orders?account_id={env['account_id']}")
    assert orders_resp.status_code == 403


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


def test_any_logged_in_user_can_self_service_create_their_own_account(env):
    # 2026-09-07, Andy's explicit ask -- "Gross Monkey" should be able to
    # walk in and link up his own exchange account the same way Andy did,
    # without needing an admin to create it for him first.
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/accounts", json={"label": "second_account"})
    assert resp.status_code == 200
    assert resp.json()["account"]["user_id"] == env["owner_id"]


def test_non_admin_cannot_create_an_account_for_someone_else(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/accounts", json={"user_id": env["other_id"], "label": "not yours"})
    assert resp.status_code == 403


def test_admin_can_still_create_an_account_on_behalf_of_another_user(env):
    admin_client = _login("exec_admin@kabroda.com", "adminpass123")
    resp = admin_client.post("/api/executor/accounts", json={"user_id": env["owner_id"], "label": "second_account"})
    assert resp.status_code == 200
    assert resp.json()["account"]["user_id"] == env["owner_id"]


def test_only_admin_can_engage_global_kill_switch(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/global-kill-switch", json={"reason": "test"})
    assert resp.status_code == 403


# ------------------------------------------------------------------ strategy profile (Phase 2 Ruling A, 2026-09-15 22:10 CT)

def test_profile_route_non_owner_non_admin_gets_403(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/profile", json={"gate_profile": "GATE_TRAVELER"})
    assert resp.status_code == 403


def test_profile_route_owner_can_set_on_a_dry_run_account_with_no_confirm(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/profile",
        json={"gate_profile": "GATE_TRAVELER", "mgmt_profile": "MGMT_E1_STACK"},
    )
    assert resp.status_code == 200
    account = resp.json()["account"]
    assert account["gate_profile"] == "GATE_TRAVELER"
    assert account["mgmt_profile"] == "MGMT_E1_STACK"


def test_profile_route_defaults_visible_on_a_fresh_account(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/executor/accounts")
    assert resp.status_code == 200
    account = resp.json()["accounts"][0]
    assert account["gate_profile"] == "GATE_V2"
    assert account["mgmt_profile"] == "MGMT_SPLIT"


# ------------------------------------------------------------------ sizing_confirmed (P0-2, CC_WORK_ORDER_LIVE_DAY_2026-09-19.md)
# account 11 (dawson_bitu) is a real, live example: still steady_grow
# since its 09-06 init, silently blocking every real order it would
# otherwise place (executor_engine.py's own LIVE-mode gate), with nothing
# in the UI ever telling Andy this is why -- this field/badge fixes that.

def test_sizing_confirmed_false_on_a_fresh_account(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/executor/accounts")
    assert resp.status_code == 200
    account = resp.json()["accounts"][0]
    assert account["sizing_confirmed"] is False


def test_sizing_confirmed_true_after_a_real_preset_save(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    save_resp = client.post(f"/api/executor/accounts/{aid}/sizing-policy", json={"preset_name": "stair_step_bands", "band_step_usd": 10000.0, "band_risk_per_step_usd": 1000.0})
    assert save_resp.status_code == 200

    resp = client.get("/api/executor/accounts")
    account = resp.json()["accounts"][0]
    assert account["sizing_confirmed"] is True


def test_sizing_confirmed_stays_false_for_steady_grow_or_conservative(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    client.post(f"/api/executor/accounts/{aid}/sizing-policy", json={"preset_name": "steady_grow"})
    resp = client.get("/api/executor/accounts")
    assert resp.json()["accounts"][0]["sizing_confirmed"] is False


def test_profile_route_rejects_unknown_gate_profile_with_400(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/profile", json={"gate_profile": "GATE_BOGUS"})
    assert resp.status_code == 400


def test_profile_route_on_live_account_requires_confirm_phrase(env):
    admin_client = _login("exec_admin@kabroda.com", "adminpass123")
    owner_client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    owner_client.post(f"/api/executor/accounts/{aid}/credentials", json={"api_key": "k", "api_secret": "s"})
    owner_client.post(f"/api/executor/accounts/{aid}/sizing-policy", json={"preset_name": "fixed_dollar", "base_risk_usd": 100.0})
    live_resp = owner_client.post(f"/api/executor/accounts/{aid}/mode", json={"mode": "LIVE", "confirm": "CONFIRM ENABLE LIVE TRADING"})
    assert live_resp.status_code == 200
    assert live_resp.json()["account"]["mode"] == "LIVE"

    no_confirm = owner_client.post(f"/api/executor/accounts/{aid}/profile", json={"gate_profile": "GATE_TRAVELER"})
    assert no_confirm.status_code == 400

    with_confirm = owner_client.post(
        f"/api/executor/accounts/{aid}/profile",
        json={"gate_profile": "GATE_TRAVELER", "confirm": "CONFIRM ENABLE LIVE TRADING"},
    )
    assert with_confirm.status_code == 200
    assert with_confirm.json()["account"]["gate_profile"] == "GATE_TRAVELER"


# ------------------------------------------------------------------ assumed balance (CC_WORK_ORDER_ASSUMED_BALANCE.md, 2026-09-16)

def test_assumed_balance_route_non_owner_non_admin_gets_403(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/assumed-balance", json={"assumed_balance_usd": 25000.0})
    assert resp.status_code == 403


def test_assumed_balance_route_owner_can_set_it(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/assumed-balance", json={"assumed_balance_usd": 25000.0})
    assert resp.status_code == 200
    assert resp.json()["account"]["assumed_balance_usd"] == 25000.0


def test_assumed_balance_route_null_clears_it(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    client.post(f"/api/executor/accounts/{aid}/assumed-balance", json={"assumed_balance_usd": 25000.0})
    resp = client.post(f"/api/executor/accounts/{aid}/assumed-balance", json={"assumed_balance_usd": None})
    assert resp.status_code == 200
    assert resp.json()["account"]["assumed_balance_usd"] is None


def test_assumed_balance_route_rejects_zero_or_negative_with_400(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    assert client.post(f"/api/executor/accounts/{aid}/assumed-balance", json={"assumed_balance_usd": 0.0}).status_code == 400
    assert client.post(f"/api/executor/accounts/{aid}/assumed-balance", json={"assumed_balance_usd": -50.0}).status_code == 400


def test_assumed_balance_route_allowed_on_a_live_account_no_confirm_needed(env):
    admin_client = _login("exec_admin@kabroda.com", "adminpass123")
    owner_client = _login("exec_owner@kabroda.com", "ownerpass123")
    aid = env["account_id"]
    owner_client.post(f"/api/executor/accounts/{aid}/credentials", json={"api_key": "k", "api_secret": "s"})
    owner_client.post(f"/api/executor/accounts/{aid}/sizing-policy", json={"preset_name": "fixed_dollar", "base_risk_usd": 100.0})
    live_resp = owner_client.post(f"/api/executor/accounts/{aid}/mode", json={"mode": "LIVE", "confirm": "CONFIRM ENABLE LIVE TRADING"})
    assert live_resp.status_code == 200

    resp = owner_client.post(f"/api/executor/accounts/{aid}/assumed-balance", json={"assumed_balance_usd": 100000.0})
    assert resp.status_code == 200
    assert resp.json()["account"]["assumed_balance_usd"] == 100000.0
    assert resp.json()["account"]["mode"] == "LIVE"


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

def test_kill_switch_toggle_reflected_in_next_traveler_plan_build(env):
    # V2 vehicle (build_hypothetical_order()/TradePlan) removed 2026-09-24
    # (V2 Crown retirement, Step 3f-ii) along with build_hypothetical_
    # order() itself -- ported to the Traveler vehicle rather than just
    # deleted, since this test's real subject (does a kill-switch toggle
    # via the real admin ROUTE actually get picked up by the NEXT plan
    # build on the same account object) has no other coverage anywhere;
    # tests/test_executor_plan_builder_traveler.py's own kill-switch test
    # only exercises ea.engage_kill_switch() directly, not this route.
    import asyncio
    import executor_plan_builder
    from database import TravelerPlan

    db = env["db"]
    plan = TravelerPlan(
        symbol="BTC/USDT", date_key="2026-09-04", session_id="us_ny_futures", status="FILLED",
        direction="LONG", fill_price=100.0, stop_price=95.0, t1_price=112.0,
        rsi_4h_at_cross=80.0,  # LONG extreme -- F_A=1.0, isolates this test's real subject
    )
    db.add(plan)
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.assumed_balance_usd = 100000.0
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    db.commit()  # flush() alone leaves an open write transaction, which
    # would block the TestClient's OWN db session (a different thread)
    # from writing -- SQLite single-writer locking, not an app bug.

    before = asyncio.run(executor_plan_builder.build_hypothetical_traveler_order(db, plan, account, state))
    assert before["decision"] == "WOULD_PLACE"
    db.commit()  # build_hypothetical_traveler_order() also lazy-inits an
    # ExecutorSizingPolicy row (flush(), not commit()) -- same open-write-
    # transaction/SQLite-single-writer-lock hazard the comment above
    # already documents, must be closed before the TestClient's own
    # session writes below.

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "toggle test"})
    assert resp.status_code == 200

    db.refresh(account)
    after = asyncio.run(executor_plan_builder.build_hypothetical_traveler_order(db, plan, account, state))
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


# ------------------------------------------------------------------ sizing policy wizard (2026-09-05)

def test_sizing_policy_get_requires_owner_or_admin(env):
    other_client = _login("exec_other@kabroda.com", "otherpass123")
    resp = other_client.get(f"/api/executor/accounts/{env['account_id']}/sizing-policy")
    assert resp.status_code == 403

    owner_client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = owner_client.get(f"/api/executor/accounts/{env['account_id']}/sizing-policy")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # lazy-init seed from the default ExecutorRiskState
    assert resp.json()["sizing_policy"]["base_risk_usd"] == 100.0


def test_sizing_policy_post_persists_and_writes_audit(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy",
        json={"preset_name": "scale_with_account", "base_risk_usd": None, "base_risk_pct": 0.10,
              "tier_threshold_usd": 10000.0, "tier_flat_usd": 1000.0},
    )
    assert resp.status_code == 200
    policy = resp.json()["sizing_policy"]
    assert policy["base_risk_pct"] == 0.10
    assert policy["base_risk_usd"] is None
    assert policy["tier_threshold_usd"] == 10000.0

    get_resp = client.get(f"/api/executor/accounts/{env['account_id']}/sizing-policy")
    assert get_resp.json()["sizing_policy"]["base_risk_pct"] == 0.10

    db = env["db"]
    rows = db.query(ExecutorAuditLog).filter_by(account_id=env["account_id"], event_type="SIZING_POLICY_UPDATED").all()
    assert len(rows) == 1


def test_sizing_policy_post_rejects_invalid_combination_with_400(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy",
        json={"tier_threshold_usd": 10000.0},   # tier_flat_usd missing
    )
    assert resp.status_code == 400
    assert "tier_threshold_usd" in resp.json()["error"]


def test_sizing_policy_non_owner_non_admin_gets_403(env):
    client = _login("exec_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/sizing-policy", json={"base_risk_usd": 200.0})
    assert resp.status_code == 403


def test_sizing_policy_preview_does_not_persist_or_mutate_state(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy/preview",
        json={"base_risk_usd": 100.0, "roll_in_pct": 0.10},
    )
    assert resp.status_code == 200
    preview = resp.json()["preview"]
    assert preview["current"]["stake_usd"] == pytest.approx(100.0)
    # +2R win off a 100 stake -> pnl 200, rolled at 10% -> 100 + 0.10*200 = 120
    assert preview["after_2r_win"]["stake_usd"] == pytest.approx(120.0)
    # -1R loss off a 100 stake -> pnl -100, rolled at 10% -> 100 - 10 = 90,
    # but the default risk_floor_usd is 100 -- floored back up to 100.
    assert preview["after_1r_loss"]["stake_usd"] == pytest.approx(100.0)

    # Nothing persisted -- the real policy/risk-state must be untouched.
    get_resp = client.get(f"/api/executor/accounts/{env['account_id']}/sizing-policy")
    assert get_resp.json()["sizing_policy"]["base_risk_usd"] == 100.0  # seeded default, not overwritten
    risk_resp = client.get(f"/api/executor/accounts/{env['account_id']}/risk-state")
    assert risk_resp.json()["risk_state"]["risk_last_usd"] == 100.0


def test_sizing_policy_preview_percent_of_balance_uses_assumed_balance_with_no_credentials(env):
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.assumed_balance_usd = 2000.0
    db.commit()

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy/preview",
        json={"base_risk_usd": None, "base_risk_pct": 0.10, "roll_in_pct": None},
    )
    assert resp.status_code == 200
    current = resp.json()["preview"]["current"]
    assert current["stake_usd"] == pytest.approx(200.0)  # Andy's own example: 10% of $2,000
    assert "assumed_balance_usd" in current["balance_source"]


def test_sizing_policy_preview_stair_step_bands_steps_with_balance(env):
    # Option F (2026-09-10): the worked-example preview computes the stake
    # straight off the banded schedule and reports which band the balance
    # is in. assumed_balance_usd stands in for a live exchange balance.
    db = env["db"]
    account = db.query(ExecutorAccount).filter_by(id=env["account_id"]).first()
    account.assumed_balance_usd = 34_000.0
    db.commit()

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy/preview",
        json={"band_step_usd": 10_000.0, "band_risk_per_step_usd": 1_000.0,
              "band_below_pct": 0.10, "band_max_risk_usd": 10_000.0},
    )
    assert resp.status_code == 200
    current = resp.json()["preview"]["current"]
    assert current["stake_usd"] == pytest.approx(3_000.0)   # $34k -> band 3 -> $3,000
    assert current["band"] == 3


def test_sizing_policy_post_stair_step_bands_persists_and_clears_base(env):
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    # seed a base first so we can confirm switching to F clears it
    client.post(f"/api/executor/accounts/{env['account_id']}/sizing-policy", json={"base_risk_usd": 100.0})
    resp = client.post(
        f"/api/executor/accounts/{env['account_id']}/sizing-policy",
        json={"band_step_usd": 10_000.0, "band_risk_per_step_usd": 1_000.0,
              "band_below_pct": 0.10, "band_max_risk_usd": 10_000.0,
              "preset_name": "stair_step_bands"},
    )
    assert resp.status_code == 200
    pol = resp.json()["sizing_policy"]
    assert pol["band_step_usd"] == 10_000.0
    assert pol["band_risk_per_step_usd"] == 1_000.0
    assert pol["base_risk_usd"] is None
    assert pol["preset_name"] == "stair_step_bands"


def test_record_trade_result_route_owner_or_admin(env):
    other_client = _login("exec_other@kabroda.com", "otherpass123")
    resp = other_client.post(f"/api/executor/accounts/{env['account_id']}/record-trade-result", json={"pnl_usd": 50.0})
    assert resp.status_code == 403

    owner_client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = owner_client.post(f"/api/executor/accounts/{env['account_id']}/record-trade-result", json={"pnl_usd": 50.0, "trade_plan_id": 9})
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_state"]["last_trade_pnl_usd"] == 50.0
    assert body["risk_state"]["consecutive_losses"] == 0

    db = env["db"]
    rows = db.query(ExecutorAuditLog).filter_by(account_id=env["account_id"], event_type="TRADE_RESULT_RECORDED").all()
    assert len(rows) == 1
    assert rows[0].trade_plan_id == 9


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


def test_owner_page_renders_create_account_form_without_a_user_picker(env):
    # 2026-09-07: the create-account form is now self-service and visible
    # to every logged-in user (a non-admin owner needs it too, to set up
    # their own account) -- but only an admin sees the "pick a user"
    # dropdown, since a non-admin can only ever create for themselves.
    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.get("/admin/executor")
    assert resp.status_code == 200
    assert "CREATE ACCOUNT" in resp.text
    assert 'id="newAccountUserId"' not in resp.text

    admin_client = _login("exec_admin@kabroda.com", "adminpass123")
    admin_resp = admin_client.get("/admin/executor")
    assert admin_resp.status_code == 200
    assert 'id="newAccountUserId"' in admin_resp.text


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


# All "tiny order mechanism test" coverage (test_tiny_test_*/test_place_
# resting_t1_limit_*/test_check_resting_t1_limit_status_*/test_cancel_
# resting_t1_limit_*/test_full_ladder_*, 12 tests) removed 2026-09-23
# (V2 Crown retirement, Andy's ruling: retire
# executor_mechanism_test.py alongside V2) -- their backing routes are
# gone from main.py in the same pass. See tests/test_executor_mechanism_
# test.py's own removal (that whole file is deleted) for the unit-level
# coverage of the module itself.
