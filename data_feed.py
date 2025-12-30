# data_feed.py
from __future__ import annotations

import os
import ccxt
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
from zoneinfo import ZoneInfo

# ----------------------------
# 1. SHARED CONFIGURATION
# ----------------------------
DEFAULT_EXCHANGE_ID = "kucoin" 

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
    # Fallback to KuCoin if the requested ID is invalid or missing
    if not exchange_id or not hasattr(ccxt, exchange_id):
        exchange_id = 'kucoin'
    
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'} 
    })
    return exchange

# ----------------------------
# 3. DAY TRADING ENGINE (Restored)
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

def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    exchange_id = os.getenv("EXCHANGE_ID", DEFAULT_EXCHANGE_ID).lower()
    raw_symbol = resolve_symbol(symbol, exchange_id)
    spec = SESSION_SPECS.get(session_tz, SESSION_SPECS["UTC"])
    
    exchange = get_exchange_client(exchange_id)
    
    last_price = 0.0
    range_high = range_low = session_open = None
    h1_supply = h1_demand = h4_supply = h4_demand = 0.0
    
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
            # Fallback if exact timestamp miss
            session_open = ohlcv_30m[0][1]
            range_high = ohlcv_30m[0][2]
            range_low = ohlcv_30m[0][3]
            
    except Exception as e:
        print(f"CCXT Error ({exchange_id}): {e}")

    return {
        "date": datetime.now(ZoneInfo(spec["tz"])).strftime("%Y-%m-%d"),
        "symbol": symbol, 
        "exchange": exchange_id, 
        "session_tz": session_tz,
        "last_price": last_price,
        "session_open_price": session_open,
        "r30_high": range_high, "r30_low": range_low,
        "range_30m": {"high": range_high, "low": range_low},
        
        "weekly_poc": None, "f24_poc": None, "morn_poc": None,
        "h1_supply": h1_supply, "h1_demand": h1_demand,
        "h4_supply": h4_supply, "h4_demand": h4_demand,
        "news": _fetch_calendar_stub(), "events": _fetch_calendar_stub()
    }

def get_inputs(*, symbol: str, date: Optional[str] = None, session_tz: str = "UTC") -> Dict[str, Any]:
    """
    Main Entry Point for Day Trading Suite (DMR Report).
    """
    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    
    # Import here to avoid circular dependency issues
    import sse_engine
    
    # Compute the Day Trading Levels
    sse = sse_engine.compute_sse_levels(inputs)
    inputs.update(sse)
    return inputs

# ----------------------------
# 4. INVESTING ENGINE (Wealth OS)
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
                # print(f"✅ Data Feed: Found {len(ohlcv)} candles for {s} ({timeframe})")
                return [
                    {'time': int(c[0]/1000), 'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4]} 
                    for c in ohlcv
                ]
        except Exception:
            continue
    return []

def get_investing_inputs(symbol: str) -> Dict[str, Any]:
    """
    Main Entry Point for Wealth OS (S Jan).
    """
    exchange_id = "kucoin"
    raw_symbol = resolve_symbol(symbol, exchange_id)
    exchange = get_exchange_client(exchange_id)
    
    monthly_candles = []
    weekly_candles = []
    current_price = 0.0
    
    try:
        try:
            ticker = exchange.fetch_ticker(raw_symbol)
            current_price = float(ticker['last'])
        except:
            ticker = exchange.fetch_ticker(raw_symbol.replace("/", "-"))
            current_price = float(ticker['last'])

        # 1. Fetch "Monthly" (Uses 1w as proxy)
        monthly_candles = fetch_candles_safe(exchange, raw_symbol, '1w', 100)
        
        # 2. Fetch "Weekly" (Uses 1d as proxy)
        weekly_candles = fetch_candles_safe(exchange, raw_symbol, '1d', 365)
        
    except Exception as e:
        print(f"Data Feed Error (Investing): {e}")
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "monthly_candles": monthly_candles,
        "weekly_candles": weekly_candles
    }