# database_manager.py
# ==============================================================================
# KABRODA DATABASE MANAGER v1.0 (STATE MACHINE)
# ==============================================================================
# Purpose: Single Source of Truth for multi-day campaign memory.
# Prevents the system from getting chopped up by daily noise by remembering
# the macro liquidity targets from previous sessions.
# ==============================================================================

import os
import asyncpg
from datetime import datetime, timezone

# Safely extract and clean the DB URL for asyncpg compatibility
raw_url = os.getenv("DATABASE_URL", "")
DB_URL = raw_url.replace("postgresql+psycopg://", "postgresql://") if raw_url else None

async def init_db():
    """
    Called on startup to ensure the state table exists.
    """
    if not DB_URL:
        print("[DB WARNING] DATABASE_URL missing. Operating with amnesia.")
        return

    try:
        conn = await asyncpg.connect(DB_URL)
        # Create the table to track active campaigns
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS campaign_state (
                symbol VARCHAR(20) PRIMARY KEY,
                campaign_bias VARCHAR(10) NOT NULL,
                target_price FLOAT NOT NULL,
                status VARCHAR(20) NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
        ''')
        await conn.close()
        print("[DB SUCCESS] Campaign State table verified and locked.")
    except Exception as e:
        print(f"[DB ERROR] Failed to initialize database: {e}")

async def get_campaign_state(symbol: str) -> dict:
    """
    Fetches the active campaign memory for a given asset.
    Returns NEUTRAL if no active campaign exists.
    """
    default_state = {"bias": "NEUTRAL", "target_price": 0.0, "status": "NONE"}
    
    if not DB_URL:
        return default_state

    try:
        conn = await asyncpg.connect(DB_URL)
        row = await conn.fetchrow(
            'SELECT campaign_bias, target_price, status, updated_at FROM campaign_state WHERE symbol = $1',
            symbol
        )
        await conn.close()

        if row:
            return {
                "bias": row["campaign_bias"],
                "target_price": float(row["target_price"]),
                "status": row["status"],
                "updated_at": row["updated_at"]
            }
    except Exception as e:
        print(f"[DB ERROR] Failed to fetch campaign for {symbol}: {e}")
        
    return default_state

async def update_campaign_state(symbol: str, bias: str, target_price: float, status: str):
    """
    Upserts the campaign state. If the symbol exists, it overwrites it.
    Statuses: ACTIVE, COMPLETED, INVALIDATED
    """
    if not DB_URL:
        return

    now = datetime.now(timezone.utc)
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute('''
            INSERT INTO campaign_state (symbol, campaign_bias, target_price, status, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (symbol) DO UPDATE 
            SET campaign_bias = EXCLUDED.campaign_bias,
                target_price = EXCLUDED.target_price,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        ''', symbol, bias, float(target_price), status, now)
        await conn.close()
        print(f"[DB UPDATE] {symbol} Campaign Memory Locked: {bias} -> {target_price} [{status}]")
    except Exception as e:
        print(f"[DB ERROR] Failed to update campaign for {symbol}: {e}")