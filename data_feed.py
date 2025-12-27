# data_feed.py
from __future__ import annotations

import os
import re
import ccxt # Universal Crypto Library
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
from zoneinfo import ZoneInfo

# ----------------------------
# 1. CONFIGURATION
# ----------------------------
# Options: 'kraken', 'coinbase', 'binance', 'mexc', 'kucoin'
# We default to 'kraken' because it is a reliable Global Spot reference.
DEFAULT_EXCHANGE_ID = "kraken" 

SESSION_SPECS = {
    "America/New_York":       {"tz": "America/New_York", "open": "09:30"},
    "America/New_York_Early": {"tz": "America/New_York", "open": "08:30"},
    "Europe/London":          {"tz": "Europe/London",    "open": "08:00"},
    "Asia/Tokyo":             {"tz": "Asia/Tokyo",       "open": "09:00"},
    "Australia/Sydney":       {"tz": "Australia/Sydney", "open": "10:00"},
    "UTC":                    {"tz": "UTC",              "open": "00:00"},
}

def resolve_symbol(symbol: str, exchange_id: str) -> str:
    s = (symbol or "").strip().upper()
    # Exchange-specific normalizations
    if exchange_id == "kraken" and s == "BTCUSDT": return "BTC/USDT"
    if exchange_id == "coinbase" and s == "BTCUSDT": return "BTC/USD"
    
    # Generic CCXT format
    if "USDT" in s and "/" not in s: return s.replace("USDT", "/USDT")
    if "USD" in s and "/" not in s and "USDT" not in s: return s.replace("USD", "/USD")
        
    return s if "/" in s else f"{s}/USDT"

def _ms(dt: datetime) -> int: return int(dt.timestamp() * 1000)

@dataclass
class Candle:
    open_time_ms: int
    o: float; h: float; l: float; c: float; v: float

def _fetch_calendar_stub() -> List[str]:
    return [
        "⚠️ 08:30 ET: USD High Impact Data (CPI / NFP / PPI)",
        "⚠️ 14:00 ET: FOMC / Rates Decision (If scheduled)",
        "ℹ️ Monitor 10:00 ET for secondary reversal"
    ]

# ----------------------------
# 2. UNIVERSAL CCXT PROVIDER
# ----------------------------
def get_exchange_client(exchange_id: str):
    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"Exchange '{exchange_id}' not found in CCXT")
    
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'} # Always Spot ("The Truth")
    })
    return exchange

# ----------------------------
# 3. FIXED TIME WINDOW LOGIC
# ----------------------------
def _session_window(tz_name, open_hhmm):
    z = ZoneInfo(tz_name)
    now = datetime.now(z)
    hh, mm = map(int, open_hhmm.split(":"))
    
    # 1. Target Open Time Today
    candidate_open = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    
    # 2. Check if the 30m candle has closed yet
    lock_point = candidate_open + timedelta(minutes=30)
    
    # 3. If NOT closed, use yesterday's session
    if now < lock_point:
        candidate_open -= timedelta(days=1)
    
    # 4. Define Window (NO OFFSET - Exact Open Time)
    start_local = candidate_open
    end_local = candidate_open + timedelta(minutes=30)
    
    return (start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))

# ----------------------------
# 4. MAIN PIPELINE
# ----------------------------
def get_inputs(*, symbol: str, date: Optional[str] = None, session_tz: str = "UTC") -> Dict[str, Any]:
    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    import sse_engine
    sse = sse_engine.compute_sse_levels(inputs)
    inputs.update(sse)
    return inputs

def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    # 1. Setup
    exchange_id = os.getenv("EXCHANGE_ID", DEFAULT_EXCHANGE_ID).lower()
    raw_symbol = resolve_symbol(symbol, exchange_id)
    spec = SESSION_SPECS.get(session_tz, SESSION_SPECS["UTC"])
    
    # 2. Initialize Exchange
    exchange = get_exchange_client(exchange_id)
    
    last_price = 0.0
    range_high = range_low = session_open = None
    
    try:
        # A. Fetch Ticker
        try:
            ticker = exchange.fetch_ticker(raw_symbol)
            last_price = float(ticker['last'])
        except: pass

        # B. Fetch 30m Candle (Exact Window)
        start_utc, end_utc = _session_window(spec["tz"], spec["open"])
        start_ms = _ms(start_utc)
        
        # Fetch 3 candles starting from our time to ensure match
        ohlcv = exchange.fetch_ohlcv(raw_symbol, timeframe='30m', since=start_ms, limit=3)
        
        # Find exact timestamp match
        target_candle = next((c for c in ohlcv if c[0] == start_ms), None)
        
        if target_candle:
            # CCXT Format: [timestamp, open, high, low, close, volume]
            session_open = target_candle[1]
            range_high = target_candle[2]
            range_low = target_candle[3]
        elif ohlcv:
            # Fallback
            session_open = ohlcv[0][1]
            range_high = ohlcv[0][2]
            range_low = ohlcv[0][3]
            
    except Exception as e:
        print(f"CCXT Error ({exchange_id}): {e}")
        pass

    return {
        "date": datetime.now(ZoneInfo(spec["tz"])).strftime("%Y-%m-%d"),
        "symbol": symbol, 
        "exchange": exchange_id, 
        "session_tz": session_tz,
        "last_price": last_price,
        "session_open_price": session_open,
        "r30_high": range_high, "r30_low": range_low,
        "range_30m": {"high": range_high, "low": range_low},
        
        # Context Stubs
        "weekly_poc": None, "weekly_val": None, "weekly_vah": None,
        "f24_poc": None, "f24_val": None, "f24_vah": None,
        "morn_poc": None, "morn_val": None, "morn_vah": None,
        "h1_supply": None, "h1_demand": None,
        "h4_supply": None, "h4_demand": None,
        
        "news": _fetch_calendar_stub(),
        "events": _fetch_calendar_stub()
    }