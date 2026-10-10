"""
Regression coverage for Alt Matrix's three new HTTP routes (Step 5):
GET /api/radar/alt-matrix-snapshot (public), GET /api/admin/alt-matrix-status
(admin-only), POST /api/executor/accounts/{id}/alt-matrix-config
(owner-or-admin). TestClient-based, same style as
tests/test_executor_admin_routes.py and tests/test_admin_plan_status.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_alt_matrix_routes.db"
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
from main import app


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_routes.db", "kabroda_test_alt_matrix_routes.db-journal",
                 "kabroda_test_alt_matrix_routes.db-shm", "kabroda_test_alt_matrix_routes.db-wal"]:
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
        "am_admin@kabroda.com", "am_owner@kabroda.com", "am_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="am_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="amadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="am_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="amowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="am_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="amother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()
    owner_id, other_id = owner.id, other.id

    account = ea.create_account(db, user_id=owner_id, label="am_owner_bitunix")
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


# ------------------------------------------------------------------ GET /api/radar/alt-matrix-snapshot (public)

def test_public_snapshot_works_with_no_login(env):
    client = TestClient(app)
    resp = client.get("/api/radar/alt-matrix-snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["symbols"].keys()) == {"SOLUSDT", "ETHUSDT"}


def test_public_snapshot_reflects_a_real_armed_plan(env):
    db = env["db"]
    db.add(AltMatrixPlan(symbol="SOL/USDT", signal_bar_time=__import__("datetime").datetime(2026, 10, 9, 4, 0, 0),
                          date_key="2026-10-09", status="ARMED", ema21=101.0, ema55=100.0, atr14=2.0))
    db.commit()

    client = TestClient(app)
    resp = client.get("/api/radar/alt-matrix-snapshot")
    assert resp.status_code == 200
    assert resp.json()["symbols"]["SOLUSDT"]["plan"]["status"] == "ARMED"


# ------------------------------------------------------------------ GET /api/admin/alt-matrix-status

def test_admin_status_requires_login(env):
    client = TestClient(app)
    resp = client.get("/api/admin/alt-matrix-status")
    assert resp.status_code == 403


def test_admin_status_rejects_non_admin_owner(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/admin/alt-matrix-status")
    assert resp.status_code == 403


def test_admin_status_allows_admin_and_returns_configs(env):
    db = env["db"]
    db.add(AltMatrixConfig(account_id=env["account_id"], sol_enabled=True, eth_enabled=False))
    db.commit()

    client = _login("am_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/alt-matrix-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["configs"]) == 1
    assert body["configs"][0]["account_id"] == env["account_id"]
    assert body["configs"][0]["sol_enabled"] is True
    assert body["configs"][0]["eth_enabled"] is False


# ------------------------------------------------------------------ GET /api/executor/accounts/{id}/alt-matrix-config

def test_get_config_non_owner_gets_403(env):
    client = _login("am_other@kabroda.com", "otherpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config")
    assert resp.status_code == 403


def test_get_config_reports_schema_defaults_with_no_row_and_creates_nothing(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config")
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["sol_enabled"] is True and cfg["eth_enabled"] is True
    assert env["db"].query(AltMatrixConfig).count() == 0   # a read must never create a row


def test_get_config_reflects_a_real_saved_row(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": False})
    resp = client.get(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config")
    assert resp.json()["config"]["sol_enabled"] is False


# ------------------------------------------------------------------ POST /api/executor/accounts/{id}/alt-matrix-config

def test_config_route_non_owner_non_admin_gets_403(env):
    client = _login("am_other@kabroda.com", "otherpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": False})
    assert resp.status_code == 403


def test_config_route_rejects_unknown_account_with_404(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/executor/accounts/999999/alt-matrix-config", json={"sol_enabled": False})
    assert resp.status_code == 404


def test_config_route_owner_creates_row_on_first_touch_with_defaults(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": False})
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["sol_enabled"] is False
    assert cfg["eth_enabled"] is True   # schema default, untouched by this partial update


def test_config_route_partial_update_leaves_other_field_unchanged(env):
    client = _login("am_owner@kabroda.com", "ownerpass123")
    client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": False, "eth_enabled": False})
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": True})
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["sol_enabled"] is True
    assert cfg["eth_enabled"] is False   # not touched by the second call -- stayed at what the first call set


def test_config_route_admin_has_no_bypass_on_someone_elses_account(env):
    # _executor_owner_or_admin() deliberately does NOT give admin a
    # bypass on this route family -- Andy's explicit 2026-09-07 mutual-
    # isolation ruling (see that function's own docstring): "I don't
    # wanna be able to see his stuff, he doesn't care about mine." Only
    # the account's real owner can touch its own Alt Matrix config.
    client = _login("am_admin@kabroda.com", "adminpass123")
    resp = client.post(f"/api/executor/accounts/{env['account_id']}/alt-matrix-config", json={"sol_enabled": False})
    assert resp.status_code == 403
