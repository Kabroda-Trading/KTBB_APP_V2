#!/usr/bin/env python3
# db_cleanup.py — One-time purge of legacy non-BTC / BOUNCE_ENGINE CampaignLog rows.
# Run once, then this file can be deleted.
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

with engine.begin() as conn:
    total_before = conn.execute(text("SELECT COUNT(*) FROM campaign_logs")).scalar()

    bounce = conn.execute(
        text("DELETE FROM campaign_logs WHERE session_id = 'BOUNCE_ENGINE'")
    ).rowcount

    non_btc = conn.execute(
        text("DELETE FROM campaign_logs WHERE symbol != 'BTC/USDT'")
    ).rowcount

    total_after = conn.execute(text("SELECT COUNT(*) FROM campaign_logs")).scalar()

print(f"DB Cleanup Complete:")
print(f"  Rows before:              {total_before}")
print(f"  BOUNCE_ENGINE deleted:    {bounce}")
print(f"  Non-BTC/USDT deleted:     {non_btc}")
print(f"  Rows remaining:           {total_after}")
