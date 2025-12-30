# data_feed.py
from __future__ import annotations

import os
import ccxt
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
from zoneinfo import ZoneInfo

# ----------------------------
# 1. CONFIGURATION
# ----------------------------
DEFAULT_EXCHANGE_ID = "kucoin" 

SESSION_SPECS = {
    "America/New_York":       {"tz": "America/New_York", "open": "09:30"},
    "UTC":                    {"tz": "UTC",              "open": "00:00"},
}

def resolve_symbol(symbol: str, exchange_id: str) -> str:
    s = (symbol or "").strip().upper()
    return s if "/" in s else f"{s}/USDT"

def _ms(dt: datetime) -> int: return int(dt.timestamp() * 1000)

def _fetch_calendar_stub() -> List[str]:
    return [
        "ℹ️ CRITICAL: Verify Impact Events.",
        "🔗 SOURCE: ForexFactory",
        "⚠️ ACTION: No trade 5m before Red News."
    ]

# ----------------------------
# 2. UNIVERSAL CCXT PROVIDER
# ----------------------------
def get_exchange_client(exchange_id: str):
    if not hasattr(ccxt, exchange_id):
        exchange_id = 'kucoin'
    
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'} 
    })
    return exchange

# ----------------------------
# 3. ROBUST FETCHER (The Fix)
# ----------------------------
def fetch_candles_safe(exchange, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    """
    Tries multiple symbol formats to ensure we get data.
    """
    candidates = [symbol, symbol.replace("/", "-"), symbol.replace("/", "")]
    
    for s in candidates:
        try:
            ohlcv = exchange.fetch_ohlcv(s, timeframe=timeframe, limit=limit)
            if ohlcv and len(ohlcv) > 0:
                print(f"✅ Data Feed: Found {len(ohlcv)} candles for {s} ({timeframe})")
                return [
                    {'time': int(c[0]/1000), 'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4]} 
                    for c in ohlcv
                ]
        except Exception:
            continue
    
    print(f"❌ Data Feed: Failed to fetch {timeframe} for {symbol} (tried {candidates})")
    return []

# ----------------------------
# 4. INVESTING DATA FEED (S Jan)
# ----------------------------
def get_investing_inputs(symbol: str) -> Dict[str, Any]:
    exchange_id = "kucoin"
    raw_symbol = resolve_symbol(symbol, exchange_id)
    
    exchange = get_exchange_client(exchange_id)
    
    monthly_candles = []
    weekly_candles = []
    current_price = 0.0
    
    try:
        # Get Price
        try:
            ticker = exchange.fetch_ticker(raw_symbol)
            current_price = float(ticker['last'])
        except:
            # Fallback for ticker
            ticker = exchange.fetch_ticker(raw_symbol.replace("/", "-"))
            current_price = float(ticker['last'])

        # 1. Fetch "Monthly" (Uses 1w as proxy if 1M fails/is unstable)
        # We prefer 1w for reliability on KuCoin via CCXT
        monthly_candles = fetch_candles_safe(exchange, raw_symbol, '1w', 100)
        
        # 2. Fetch "Weekly" (Uses 1d as proxy)
        weekly_candles = fetch_candles_safe(exchange, raw_symbol, '1d', 365)
        
    except Exception as e:
        print(f"Data Feed Critical Error: {e}")
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "monthly_candles": monthly_candles,
        "weekly_candles": weekly_candles
    }