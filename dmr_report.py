# dmr_report.py
from __future__ import annotations
from typing import Any, Dict
from datetime import datetime, timedelta
import pytz
import trade_logic_v2
import sse_engine

# --- CONFIGURATION ---
SESSION_CONFIGS = {
    "us_ny_futures": {"label": "US NY Futures (08:30 ET)", "tz": "America/New_York", "h": 8, "m": 30},
    "us_ny_equity":  {"label": "US NY Equity (09:30 ET)",  "tz": "America/New_York", "h": 9, "m": 30},
    "europe_london": {"label": "London (08:00 GMT)",       "tz": "Europe/London",    "h": 8, "m": 0},
    "asia_tokyo":    {"label": "Tokyo (09:00 JST)",        "tz": "Asia/Tokyo",       "h": 9, "m": 0},
    "australia_sydney": {"label": "Sydney (10:00 AEDT)",  "tz": "Australia/Sydney", "h": 10, "m": 0},
    "utc":           {"label": "UTC Core",                 "tz": "UTC",              "h": 0,  "m": 0}
}

def _normalize_session_key(key: str) -> str:
    k = key.lower()
    if "york_early" in k or "futures" in k: return "us_ny_futures"
    if "york" in k: return "us_ny_equity"
    if "london" in k: return "europe_london"
    if "tokyo" in k: return "asia_tokyo"
    if "sydney" in k: return "australia_sydney"
    return "utc"

def _get_anchor_times(raw_15m: list, session_key: str):
    """Calculates the immutable session anchor timestamps."""
    config = SESSION_CONFIGS.get(session_key, SESSION_CONFIGS["utc"])
    tz = pytz.timezone(config["tz"])
    
    # Default to now if no data
    if not raw_15m:
        now_anchor = datetime.now(tz)
        return -1, now_anchor, config

    # Find the specific 30m anchor candle in the dataset
    for i in range(len(raw_15m) - 1, -1, -1):
        c = raw_15m[i]
        dt_utc = datetime.fromtimestamp(c['time'], tz=pytz.UTC)
        dt_anchor = dt_utc.astimezone(tz)
        
        if dt_anchor.hour == config['h'] and dt_anchor.minute == config['m']:
            return i, dt_anchor, config
            
    # Fallback: Use last candle's date but force the session time
    last_utc = datetime.fromtimestamp(raw_15m[-1]['time'], tz=pytz.UTC)
    last_anchor = last_utc.astimezone(tz)
    # Reset to target hour/min
    target_anchor = last_anchor.replace(hour=config['h'], minute=config['m'], second=0, microsecond=0)
    
    return -1, target_anchor, config

def generate_report_from_inputs(inputs: Dict[str, Any], session_tz_key: str = "UTC") -> Dict[str, Any]:
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    
    # 1. Determine Anchor
    norm_key = _normalize_session_key(session_tz_key)
    idx, anchor_dt, config = _get_anchor_times(raw_15m, norm_key)
    
    # 2. Slice Data based on Anchor (Immutable Window)
    if idx >= 0:
        anchor_candle = raw_15m[idx]
        start_24 = max(0, idx - 96)
        slice_24h = raw_15m[start_24 : idx]
        start_4 = max(0, idx - 16)
        slice_4h = raw_15m[start_4 : idx]
        is_locked = True
    else:
        # Pre-market or missing data
        anchor_candle = raw_15m[-1] if raw_15m else {}
        slice_24h = raw_15m[-96:] if raw_15m else []
        slice_4h = raw_15m[-16:] if raw_15m else []
        is_locked = False

    # 3. Run Math Engine (SSE)
    sse_input = {
        "raw_15m_candles": raw_15m,
        "raw_daily_candles": raw_daily,
        "slice_24h": slice_24h,
        "slice_4h": slice_4h,
        "session_open_price": anchor_candle.get("open", inputs.get("current_price", 0.0)),
        "r30_high": anchor_candle.get("high", 0.0),
        "r30_low": anchor_candle.get("low", 0.0),
        "last_price": inputs.get("current_price", 0.0)
    }
    
    computed = sse_engine.compute_sse_levels(sse_input)
    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs={**inputs, **computed})

    # 4. Construct Contract v1.2 JSON
    now_utc = datetime.now(pytz.UTC).isoformat()
    
    report = {
        "contract_version": "1.2",
        "generated_at_utc": now_utc,
        "symbol": symbol,
        "price": inputs.get("current_price"),
        
        "session": {
            "session_id": norm_key.upper(),
            "label": config["label"],
            "anchor_tz": config["tz"],
            "session_date_anchor": anchor_dt.strftime("%Y-%m-%d"),
            "open_anchor": anchor_dt.isoformat(),
            "calibration": {
                "window_minutes": 30,
                "locked": is_locked
            },
            "mode": "debrief" if is_locked else "live"
        },
        
        # Core Data
        "levels": computed["levels"],
        "range_30m": {
            "high": computed["levels"]["range30m_high"], 
            "low": computed["levels"]["range30m_low"]
        },
        
        # The Brain
        "bias_model": computed["bias_model"],
        "htf_shelves": computed["htf_shelves"],
        "trade_logic": trade_logic,
        
        # Context
        "context": computed["context"]
    }
    
    return report

run_auto_raw = generate_report_from_inputs