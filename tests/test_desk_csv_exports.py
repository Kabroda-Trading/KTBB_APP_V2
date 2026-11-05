"""Regression coverage for the desk-page build's new in-app CSV exports:
GET /api/export/cme-trades.csv, /alt-matrix-trades.csv, /traveler-trades.csv
(all owner-or-admin, login-scoped -- NOT the existing flat-API-key gate used
by /api/export/gate-log.csv / /api/export/traveler-log.csv, the wrong tool
for a logged-in user's download button) and /api/export/metasignals-trades.csv
(admin-only, no owner concept for that singleton paper account)."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_desk_csv_exports.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
import csv
import io
import datetime
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import database
from database import (
    SessionLocal, UserModel, ExecutorAccount, CMEPlan, CMEOrder, AltMatrixOrder,
    ExecutorOrder, TravelerPlan, MetaSignalsPaperOrder,
)
import auth
import executor_accounts as ea
from main import app


def _clean_db_files():
    for path in ["kabroda_test_desk_csv_exports.db", "kabroda_test_desk_csv_exports.db-journal",
                 "kabroda_test_desk_csv_exports.db-shm", "kabroda_test_desk_csv_exports.db-wal"]:
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
    for model in (CMEOrder, CMEPlan, AltMatrixOrder, ExecutorOrder, TravelerPlan, MetaSignalsPaperOrder, ExecutorAccount):
        db.query(model).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "csv_admin@kabroda.com", "csv_owner@kabroda.com", "csv_other@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="csv_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="csvadmin", tier="admin", is_admin=True, subscription_status="active"))
    owner = UserModel(email="csv_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="csvowner", tier="basic", is_admin=False, subscription_status="active")
    other = UserModel(email="csv_other@kabroda.com", password_hash=auth.hash_password("otherpass123"),
                       username="csvother", tier="basic", is_admin=False, subscription_status="active")
    db.add(owner)
    db.add(other)
    db.commit()

    owner_account = ea.create_account(db, user_id=owner.id, label="csv_owner_acct")
    db.commit()
    owner_account_id = owner_account.id

    yield {"db": db, "owner_account_id": owner_account_id}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


def _parse_csv(text):
    return list(csv.reader(io.StringIO(text)))


# ------------------------------------------------------------------ CME

def test_cme_export_non_owner_gets_403(env):
    client = _login("csv_other@kabroda.com", "otherpass123")
    resp = client.get("/api/export/cme-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 403


def test_cme_export_owner_gets_a_real_csv(env):
    db = env["db"]
    plan = CMEPlan(symbol="MNQ", date_key="2026-11-05", lock_time=datetime.datetime(2026, 11, 5, 15, 0, 0),
                    status="ARMED", box=20.0, bo=4500.0, bd=4480.0)
    db.add(plan)
    db.commit()
    db.add(CMEOrder(cme_plan_id=plan.id, account_id=env["owner_account_id"], mode="LIVE", symbol="MNQ",
                     direction="LONG", contracts=1, management_state="CLOSED_T1",
                     realized_pnl_r=1.0, dollar_pnl=350.0, closed_at=datetime.datetime.utcnow()))
    db.commit()

    client = _login("csv_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/export/cme-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = _parse_csv(resp.text)
    header, data_rows = rows[0], rows[1:]
    assert "dollar_pnl" in header
    assert len(data_rows) == 1


# ------------------------------------------------------------------ Alt Matrix

def test_alt_matrix_export_non_owner_gets_403(env):
    client = _login("csv_other@kabroda.com", "otherpass123")
    resp = client.get("/api/export/alt-matrix-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 403


def test_alt_matrix_export_owner_gets_a_real_csv(env):
    db = env["db"]
    db.add(AltMatrixOrder(alt_matrix_plan_id=1, account_id=env["owner_account_id"], mode="LIVE", symbol="SOL/USDT",
                           direction="LONG", management_state="CLOSED_MANUAL", qty=10.0,
                           realized_pnl_r=1.5, realized_pnl_usd=150.0, closed_at=datetime.datetime.utcnow()))
    db.commit()

    client = _login("csv_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/export/alt-matrix-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    header, data_rows = rows[0], rows[1:]
    assert "realized_pnl_usd" in header
    assert len(data_rows) == 1


# ------------------------------------------------------------------ Traveler

def test_traveler_export_non_owner_gets_403(env):
    client = _login("csv_other@kabroda.com", "otherpass123")
    resp = client.get("/api/export/traveler-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 403


def test_traveler_export_owner_gets_a_real_csv_with_the_new_usd_column(env):
    """Proves the new realized_pnl_usd column (this same build added it to
    ExecutorOrder) actually shows up in the export, not just the schema."""
    db = env["db"]
    plan = TravelerPlan(symbol="BTC/USDT", date_key="2026-11-05", session_id="us_ny_futures", status="FILLED")
    db.add(plan)
    db.commit()
    db.add(ExecutorOrder(
        trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=env["owner_account_id"], mode="LIVE",
        symbol="BTC/USDT", direction="LONG", entry_price=100.0, stop_price=90.0, t1_price=110.0,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="CLOSED_T1",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
        realized_pnl_r=1.0, realized_pnl_usd=100.0, closed_at=datetime.datetime.utcnow(),
    ))
    db.commit()

    client = _login("csv_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/export/traveler-trades.csv", params={"account_id": env["owner_account_id"]})
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    header, data_rows = rows[0], rows[1:]
    assert "realized_pnl_usd" in header
    assert len(data_rows) == 1
    assert data_rows[0][header.index("realized_pnl_usd")] == "100.0"


# ------------------------------------------------------------------ MetaSignals

def test_metasignals_export_non_admin_gets_403(env):
    client = _login("csv_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/export/metasignals-trades.csv")
    assert resp.status_code == 403


def test_metasignals_export_admin_gets_a_real_csv(env):
    db = env["db"]
    db.add(MetaSignalsPaperOrder(
        plan_id=1, account_id=1, symbol="SOLUSDT", side="BUY", entry_mode="MARKET",
        hard_stop_initial=90.0, current_stop=90.0, tp1_price=110.0, tp2_price=120.0,
        initial_qty=10.0, remaining_qty=0.0, state="CLOSED_TP1",
        realized_pnl=50.0, closed_at=datetime.datetime.utcnow(),
    ))
    db.commit()

    client = _login("csv_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/export/metasignals-trades.csv")
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    header, data_rows = rows[0], rows[1:]
    assert "realized_pnl" in header
    assert len(data_rows) == 1
