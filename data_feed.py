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
    if exchange_id == "kraken" and s == "BTCUSDT": return "BTC/USDT"
    if exchange_id == "coinbase" and s == "BTCUSDT": return "BTC/USD"
    
    if "USDT" in s and "/" not in s: return s.replace("USDT", "/USDT")
    if "USD" in s and "/" not in s and "USDT" not in s: return s.replace("USD", "/USD")
        
    return s if "/" in s else f"{s}/USDT"

def _ms(dt: datetime) -> int: return int(dt.timestamp() * 1000)

@dataclass
class Candle:
    open_time_ms: int
    o: float; h: float; l: float; c: float; v: float

def _fetch_calendar_stub() -> List[str]:
    # STATIC DIRECTIVE - Forces user to check real source
    return [
        "ℹ️ CRITICAL: Verify Impact Events before execution.",
        "🔗 SOURCE: https://www.forexfactory.com/calendar",
        "⚠️ ACTION: Do not trade 5 mins before/after High Impact (Red) news."
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
        'options': {'defaultType': 'spot'} 
    })
    return exchange

# ----------------------------
# 3. TIME WINDOW LOGIC
# ----------------------------
def _session_window(tz_name, open_hhmm):
    z = ZoneInfo(tz_name)
    now = datetime.now(z)
    hh, mm = map(int, open_hhmm.split(":"))
    
    candidate_open = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    lock_point = candidate_open + timedelta(minutes=30)
    
    if now < lock_point:
        candidate_open -= timedelta(days=1)
    
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
    exchange_id = os.getenv("EXCHANGE_ID", DEFAULT_EXCHANGE_ID).lower()
    raw_symbol = resolve_symbol(symbol, exchange_id)
    spec = SESSION_SPECS.get(session_tz, SESSION_SPECS["UTC"])
    
    exchange = get_exchange_client(exchange_id)
    
    last_price = 0.0
    range_high = range_low = session_open = None
    
    h1_supply = h1_demand = 0.0
    h4_supply = h4_demand = 0.0
    
    try:
        try:
            ticker = exchange.fetch_ticker(raw_symbol)
            last_price = float(ticker['last'])
        except: pass

        start_utc, end_utc = _session_window(spec["tz"], spec["open"])
        start_ms = _ms(start_utc)
        
        since_4h = _ms(datetime.now(timezone.utc) - timedelta(days=7))
        ohlcv_4h = exchange.fetch_ohlcv(raw_symbol, timeframe='4h', since=since_4h, limit=50)
        
        if ohlcv_4h:
            h4_supply = max(c[2] for c in ohlcv_4h)
            h4_demand = min(c[3] for c in ohlcv_4h)

        since_1h = _ms(datetime.now(timezone.utc) - timedelta(hours=24))
        ohlcv_1h = exchange.fetch_ohlcv(raw_symbol, timeframe='1h', since=since_1h, limit=24)
        
        if ohlcv_1h:
            h1_supply = max(c[2] for c in ohlcv_1h)
            h1_demand = min(c[3] for c in ohlcv_1h)

        ohlcv_30m = exchange.fetch_ohlcv(raw_symbol, timeframe='30m', since=start_ms, limit=3)
        target_candle = next((c for c in ohlcv_30m if c[0] == start_ms), None)
        
        if target_candle:
            session_open = target_candle[1]
            range_high = target_candle[2]
            range_low = target_candle[3]
        elif ohlcv_30m:
            session_open = ohlcv_30m[0][1]
            range_high = ohlcv_30m[0][2]
            range_low = ohlcv_30m[0][3]
            
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
        
        "weekly_poc": None, 
        "f24_poc": None, 
        "morn_poc": None,
        "h1_supply": h1_supply,
        "h1_demand": h1_demand,
        "h4_supply": h4_supply,
        "h4_demand": h4_demand,
        
        "news": _fetch_calendar_stub(),
        "events": _fetch_calendar_stub()
    }
# ----------------------------
# 5. NEW: INVESTING DATA FEED (S Jan)
# ----------------------------
# In data_feed.py

def get_investing_inputs(symbol: str) -> Dict[str, Any]:
    """
    Fetches Long-Term data (Monthly/Weekly) for the S Jan Investing Engine.
    INCLUDES TIMESTAMPS for plotting.
    """
    exchange_id = os.getenv("EXCHANGE_ID", DEFAULT_EXCHANGE_ID).lower()
    raw_symbol = resolve_symbol(symbol, exchange_id)
    
    exchange = get_exchange_client(exchange_id)
    
    monthly_candles = []
    weekly_candles = []
    current_price = 0.0
    
    try:
        ticker = exchange.fetch_ticker(raw_symbol)
        current_price = float(ticker['last'])
        
        # Fetch Monthly (Limit 24)
        ohlcv_m = exchange.fetch_ohlcv(raw_symbol, timeframe='1M', limit=24)
        monthly_candles = [
            # time must be in seconds for Lightweight Charts
            {'time': int(c[0]/1000), 'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4]} 
            for c in ohlcv_m
        ]
        
        # Fetch Weekly (Limit 52)
        ohlcv_w = exchange.fetch_ohlcv(raw_symbol, timeframe='1w', limit=52)
        weekly_candles = [
            {'time': int(c[0]/1000), 'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4]} 
            for c in ohlcv_w
        ]
        
    except Exception as e:
        print(f"Data Feed Error (Investing): {e}")
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "monthly_candles": monthly_candles,
        "weekly_candles": weekly_candles
    }