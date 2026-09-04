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
from database import SessionLocal, UserModel, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig
import auth
import executor_accounts as ea
from main import app


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
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount, ExecutorGlobalConfig):
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

    before = executor_plan_builder.build_hypothetical_order(db, plan, account, state)
    assert before["decision"] == "WOULD_PLACE"

    client = _login("exec_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/kill-switch", json={"reason": "toggle test"})
    assert resp.status_code == 200

    db.refresh(account)
    after = executor_plan_builder.build_hypothetical_order(db, plan, account, state)
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
