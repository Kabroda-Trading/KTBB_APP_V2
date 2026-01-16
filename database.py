# database.py — Core data access layer
from typing import List, Dict
from datetime import datetime

# Simulate a database with dummy values
# Replace with actual DB logic when ready

def get_locked_candles(session: str, symbol: str) -> List[Dict]:
    # This should query your candle/session lock system
    return [
        {"timestamp": "2024-01-15T10:00:00Z", "locked": True},
        {"timestamp": "2024-01-15T10:15:00Z", "locked": False},
    ]

def get_r30_levels(symbol: str) -> Dict[str, float]:
    # R30 = 30-min high/low levels
    return {
        "high": 1.1450,
        "low": 1.1320
    }

def get_price(symbol: str) -> float:
    # This is your current live price from feed / snapshot
    return 1.1382

def get_daily_levels(symbol: str) -> Dict[str, float]:
    # Daily support/resistance levels
    return {
        "support": 1.1300,
        "resistance": 1.1480
    }
