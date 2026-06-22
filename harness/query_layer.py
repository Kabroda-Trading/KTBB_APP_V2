# harness/query_layer.py
# =============================================================================
# KABRODA BATTLE-TEST HARNESS — Production Query Layer
#
# PRODUCTION-ONLY. This module has no value locally.
# The local kabroda.db (SQLite) has 0 campaign_logs — all production data
# lives in PostgreSQL on Render. Run from the Render Shell or any environment
# where DATABASE_URL is set to the production PostgreSQL connection string.
#
# This module is READ-ONLY. It has no write path to any table.
# =============================================================================

import os
import sys

# Resolve parent directory so `database.py` is importable from harness/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, CampaignLog, DecisionJournal, JewelSnapshotLog

CANONICAL_SYMBOL = "BTC/USDT"


def check_production_connection() -> str:
    """
    Verify DATABASE_URL points at PostgreSQL, not local SQLite.
    Raises RuntimeError if SQLite is detected — no data will be found there.
    Returns the active DATABASE_URL on success.
    """
    db_url = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")
    if "sqlite" in db_url:
        raise RuntimeError(
            "\n"
            "DATABASE_URL is pointing at local SQLite — production data is not here.\n"
            "Run this script from the Render Shell where DATABASE_URL is set\n"
            "to the production PostgreSQL connection string.\n"
        )
    return db_url


def fetch_canonical_campaigns(db) -> list:
    """
    All canonical BTC/USDT campaign_log rows, ordered by date_key ascending.
    Canonical = is_canonical=TRUE, the production-quality track record from 2026-05-27 onward.
    Legacy (multi-symbol, placeholder PnL) rows are excluded by this filter.
    """
    return (
        db.query(CampaignLog)
        .filter(
            CampaignLog.symbol == CANONICAL_SYMBOL,
            CampaignLog.is_canonical == True,
        )
        .order_by(CampaignLog.date_key)
        .all()
    )


def fetch_mas_flow_decisions(db) -> list:
    """
    All mas_flow DecisionJournal rows for BTC/USDT, ordered by session_date ascending.
    source='mas_flow' isolates MAS-cycle writes from market_radar page-view events.
    """
    return (
        db.query(DecisionJournal)
        .filter(
            DecisionJournal.symbol == CANONICAL_SYMBOL,
            DecisionJournal.source == "mas_flow",
        )
        .order_by(DecisionJournal.session_date)
        .all()
    )


def fetch_jewel_ny_open_snapshots(db) -> list:
    """
    JEWEL snapshots at session_label='NY_OPEN' for BTC/USDT, ordered by timestamp ascending.
    NY_OPEN is the snapshot closest to session lock time (8:30 AM ET), making it the
    decision-time state. Other session_labels (NY_MIDDAY, ASIA_OPEN, etc.) are excluded.
    """
    return (
        db.query(JewelSnapshotLog)
        .filter(
            JewelSnapshotLog.symbol == CANONICAL_SYMBOL,
            JewelSnapshotLog.session_label == "NY_OPEN",
        )
        .order_by(JewelSnapshotLog.timestamp)
        .all()
    )


def open_db_session():
    """Returns a new database session. Caller is responsible for closing it."""
    return SessionLocal()
