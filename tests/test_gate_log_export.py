"""
Regression coverage for GET /api/export/gate-log.csv -- the SS9c export
path (KABRODA_COM_TRADE_PLAN_SPEC.md, division-of-labor resolution,
AGENT_LOG.md 2026-08-31): kabroda.com's ONE remaining SS9 obligation is a
stable, auth-gated way for the Kabroda AI Brain to pull GateLog rows
without touching site internals. No drift-check or reconciliation logic
lives on the site side -- this endpoint is the full stop of that scope.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_gate_log_export.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
os.environ["GATE_LOG_EXPORT_API_KEY"] = "test-export-key"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import csv
import io

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, GateLog
from main import app


def _clean_db_files():
    for path in ["kabroda_test_gate_log_export.db", "kabroda_test_gate_log_export.db-journal",
                 "kabroda_test_gate_log_export.db-shm", "kabroda_test_gate_log_export.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def export_env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(GateLog).delete()
    db.commit()

    def make_row(symbol="BTC/USDT", date_key="2026-08-31", state="TAKE_STANDARD", **kwargs):
        row = GateLog(symbol=symbol, date_key=date_key, state=state, **kwargs)
        db.add(row)
        db.commit()
        return row

    client = TestClient(app)
    yield {"db": db, "make_row": make_row, "client": client}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def test_export_requires_api_key(export_env):
    resp = export_env["client"].get("/api/export/gate-log.csv")
    assert resp.status_code == 401


def test_export_rejects_wrong_api_key(export_env):
    resp = export_env["client"].get("/api/export/gate-log.csv", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_export_returns_full_csv_with_every_column(export_env):
    export_env["make_row"](
        push_vol_ratio=1.2, fuel_state="FUELED", daily_support=100.0, pressure=None,
    )
    resp = export_env["client"].get("/api/export/gate-log.csv", headers={"X-API-Key": "test-export-key"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    columns = [c.name for c in GateLog.__table__.columns]
    assert reader.fieldnames == columns  # every column, declaration order, nothing hand-picked
    assert rows[0]["fuel_state"] == "FUELED"
    assert rows[0]["push_vol_ratio"] == "1.2"
    assert rows[0]["symbol"] == "BTC/USDT"


def test_export_since_filters_by_date_key(export_env):
    export_env["make_row"](date_key="2026-08-01")
    export_env["make_row"](date_key="2026-08-31")

    resp = export_env["client"].get(
        "/api/export/gate-log.csv?since=2026-08-15", headers={"X-API-Key": "test-export-key"},
    )
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["date_key"] == "2026-08-31"


def test_export_symbol_filters(export_env):
    export_env["make_row"](symbol="BTC/USDT")
    export_env["make_row"](symbol="ETH/USDT")

    resp = export_env["client"].get(
        "/api/export/gate-log.csv?symbol=ETH/USDT", headers={"X-API-Key": "test-export-key"},
    )
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"


def test_export_empty_still_returns_header_only(export_env):
    resp = export_env["client"].get("/api/export/gate-log.csv", headers={"X-API-Key": "test-export-key"})
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert list(reader) == []
    assert reader.fieldnames == [c.name for c in GateLog.__table__.columns]
