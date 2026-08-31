import os
import sys
from unittest.mock import MagicMock

# Mock anthropic and yfinance to prevent ModuleNotFoundError when importing main
sys.modules["anthropic"] = MagicMock()
sys.modules["yfinance"] = MagicMock()

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_fixes.db"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from database import (
    init_db,
    SessionLocal,
    engine,
    UserModel,
    CampaignLog,
    AgentRunLog,
)
import auth
from main import app
from datetime import datetime, timezone, timedelta

def clean_db_files():
    for path in ["kabroda_test_fixes.db", "kabroda_test_fixes.db-journal", "kabroda_test_fixes.db-shm", "kabroda_test_fixes.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    clean_db_files()
    init_db()
    
    db = SessionLocal()
    
    # Create test users
    admin_user = UserModel(
        email="admin_fix@kabroda.com",
        password_hash=auth.hash_password("adminpass123"),
        username="adminfix",
        tier="admin",
        is_admin=True,
        subscription_status="active"
    )
    db.add(admin_user)
    
    basic_user = UserModel(
        email="basic_fix@kabroda.com",
        password_hash=auth.hash_password("basicpass123"),
        username="basicfix",
        tier="basic",
        is_admin=False,
        subscription_status="active"
    )
    db.add(basic_user)
    
    # Create CampaignLog records to test win rate, timeframe filter, and closed_at fallback
    c1 = CampaignLog(
        symbol="BTC/USDT",
        is_canonical=True,
        session_timeframe="15M",
        status="CLOSED_WIN",
        realized_pnl=1.5,
        created_at=datetime.utcnow() - timedelta(days=2),
        updated_at=datetime.utcnow() - timedelta(days=2),
        closed_at=None,
        bias="LONG",
        mas_approval_status="APPROVED",
        date_key=(datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d"),
        entry_price=90000.0,
        stop_loss=89000.0,
        t1=91000.0
    )
    
    c2 = CampaignLog(
        symbol="BTC/USDT",
        is_canonical=True,
        session_timeframe="15M",
        status="CLOSED_LOSS",
        realized_pnl=-1.0,
        created_at=datetime.utcnow() - timedelta(days=1),
        updated_at=datetime.utcnow() - timedelta(days=1),
        closed_at=datetime.utcnow() - timedelta(days=1),
        bias="SHORT",
        mas_approval_status="APPROVED",
        date_key=(datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d"),
        entry_price=91000.0,
        stop_loss=92000.0,
        t1=90000.0
    )
    
    c3 = CampaignLog(
        symbol="BTC/USDT",
        is_canonical=True,
        session_timeframe="15M",
        status="CLOSED_AT_EXPIRY",
        realized_pnl=0.5,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        closed_at=None,
        bias="LONG",
        mas_approval_status="REJECTED",
        date_key=datetime.utcnow().strftime("%Y-%m-%d"),
        entry_price=90500.0,
        stop_loss=89500.0,
        t1=92000.0
    )
    
    c4 = CampaignLog(
        symbol="BTC/USDT",
        is_canonical=True,
        session_timeframe="4H",
        status="CLOSED_WIN",
        realized_pnl=2.0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        closed_at=datetime.utcnow(),
        bias="LONG",
        mas_approval_status="APPROVED",
        date_key=datetime.utcnow().strftime("%Y-%m-%d"),
        entry_price=90500.0,
        stop_loss=89500.0,
        t1=92000.0
    )
    
    db.add_all([c1, c2, c3, c4])
    
    r1 = AgentRunLog(
        agent_name="MSA",
        status="SUCCESS",
        model="claude-sonnet-4-6",
        triggered_by="scheduler",
        estimated_cost_usd=None,
        created_at=datetime.utcnow() - timedelta(days=1),
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10
    )
    r2 = AgentRunLog(
        agent_name="K Kult",
        status="SUCCESS",
        model="claude-sonnet-4-6",
        triggered_by="scheduler",
        estimated_cost_usd=0.05,
        created_at=None,
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=25
    )
    db.add_all([r1, r2])

    # JewelSnapshotLog fixture rows removed 2026-08-30 -- the table itself is
    # gone (JewelSnapshotLog's only writer, jewel_specialist.py, is archived;
    # see database.py's removal note).

    db.commit()
    db.close()
    
    yield
    
    engine.dispose()
    clean_db_files()

@pytest.fixture
def admin_client():
    client = TestClient(app)
    client.post("/login", data={"email": "admin_fix@kabroda.com", "password": "adminpass123"})
    return client

@pytest.fixture
def basic_client():
    client = TestClient(app)
    client.post("/login", data={"email": "basic_fix@kabroda.com", "password": "basicpass123"})
    return client

def test_api_dashboard_overview(basic_client):
    response = basic_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["total_sessions"] == 3
    assert data["win_rate"] == 66.7
    assert data["net_r"] == 1.0

def test_api_dashboard_accuracy(basic_client):
    response = basic_client.get("/api/dashboard/accuracy")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True

def test_api_dashboard_costs_admin(admin_client):
    response = admin_client.get("/api/dashboard/costs")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "days" in data
    assert "agents" in data

def test_api_dashboard_costs_basic_forbidden(basic_client):
    response = basic_client.get("/api/dashboard/costs")
    assert response.status_code == 403

def test_api_dashboard_mas_history(basic_client):
    response = basic_client.get("/api/dashboard/mas-history")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["pnl_series"]) == 3

# test_api_dashboard_jewel removed 2026-08-30 -- tested /api/dashboard/jewel,
# already removed from main.py (JewelSnapshotLog's only writer is archived).
