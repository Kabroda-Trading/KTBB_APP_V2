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


def test_public_snapshot_reflects_a_real_armed_plan(env):
    db = env["db"]
    db.add(CMEPlan(symbol="MNQ", date_key="2026-11-04", lock_time=datetime.datetime(2026, 11, 4, 15, 0, 0),
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
