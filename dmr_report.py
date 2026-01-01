# dmr_report.py
from __future__ import annotations
from typing import Any, Dict
from datetime import datetime
import pytz
import trade_logic_v2
import sse_engine

def _get_session_config(session_key: str):
    key = session_key.lower()
    if "london" in key: return "Europe/London", 8, 0
    if "york_early" in key or "futures" in key: return "America/New_York", 8, 30
    if "york" in key: return "America/New_York", 9, 30
    if "tokyo" in key: return "Asia/Tokyo", 9, 0
    if "sydney" in key: return "Australia/Sydney", 7, 0
    return "UTC", 0, 0

def _find_anchor_index(intraday_candles: list, session_key: str) -> int:
    if not intraday_candles: return -1
    tz_name, h, m = _get_session_config(session_key)
    try: market_tz = pytz.timezone(tz_name)
    except: market_tz = pytz.UTC
    
    for i in range(len(intraday_candles) - 1, -1, -1):
        c = intraday_candles[i]
        utc_dt = datetime.fromtimestamp(c['time'], tz=pytz.UTC)
        market_dt = utc_dt.astimezone(market_tz)
        if market_dt.hour == h and market_dt.minute == m:
            return i
    return len(intraday_candles) - 1

def generate_report_from_inputs(inputs: Dict[str, Any], session_tz: str = "UTC") -> Dict[str, Any]:
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    
    idx = _find_anchor_index(raw_15m, session_tz)
    
    if idx >= 0:
        anchor_candle = raw_15m[idx]
        start_24 = max(0, idx - 96)
        slice_24h = raw_15m[start_24 : idx]
        start_4 = max(0, idx - 16)
        slice_4h = raw_15m[start_4 : idx]
    else:
        anchor_candle = raw_15m[-1] if raw_15m else {}
        slice_24h = raw_15m[-96:] if raw_15m else []
        slice_4h = raw_15m[-16:] if raw_15m else []

    sse_input = {
        "raw_15m_candles": raw_15m,
        "raw_daily_candles": raw_daily, # Passed for HTF Trend
        "slice_24h": slice_24h,
        "slice_4h": slice_4h,
        "session_open_price": anchor_candle.get("open", 0.0),
        "r30_high": anchor_candle.get("high", 0.0),
        "r30_low": anchor_candle.get("low", 0.0),
        "last_price": inputs.get("current_price", 0.0)
    }

    # RUN ENGINE
    computed = sse_engine.compute_sse_levels(sse_input)
    
    # HYDRATE INPUTS
    inputs["levels"] = computed["levels"]
    inputs["htf_shelves"] = computed["htf_shelves"]
    inputs["bias_model"] = computed["bias_model"]
    inputs["context"] = computed["context"]
    
    # Legacy
    inputs["range_30m"] = {
        "high": computed["levels"]["range30m_high"], 
        "low": computed["levels"]["range30m_low"]
    }
    inputs["bias_label"] = computed["bias_model"]["daily_lean"]["direction"]

    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs=inputs)
    
    return {
        "symbol": symbol,
        "date": inputs.get("date"),
        "last_price": inputs.get("current_price"),
        "session_tz": session_tz,
        "levels": computed["levels"],
        "range_30m": inputs["range_30m"],
        "bias_model": computed["bias_model"],
        "context": computed["context"],
        "trade_logic": trade_logic,
        "inputs": inputs,
        "htf_shelves": computed["htf_shelves"],
        "intraday_shelves": {},
        "news": inputs.get("news", []),
        "events": inputs.get("events", [])
    }

run_auto_raw = generate_report_from_inputs