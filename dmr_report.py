# dmr_report.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime, timedelta, timezone
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

def _to_iso_z(dt: datetime) -> str:
    """Helper to enforce Z-suffix UTC timestamps."""
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def _get_anchor_times(raw_15m: list, session_key: str):
    config = SESSION_CONFIGS.get(session_key, SESSION_CONFIGS["utc"])
    tz = pytz.timezone(config["tz"])
    
    # Default if no data
    if not raw_15m:
        now_anchor = datetime.now(tz)
        return -1, now_anchor, config

    # Find specific anchor candle
    for i in range(len(raw_15m) - 1, -1, -1):
        c = raw_15m[i]
        dt_utc = datetime.fromtimestamp(c['time'], tz=pytz.UTC)
        dt_anchor = dt_utc.astimezone(tz)
        
        if dt_anchor.hour == config['h'] and dt_anchor.minute == config['m']:
            return i, dt_anchor, config
            
    # Fallback
    last_utc = datetime.fromtimestamp(raw_15m[-1]['time'], tz=pytz.UTC)
    last_anchor = last_utc.astimezone(tz)
    target_anchor = last_anchor.replace(hour=config['h'], minute=config['m'], second=0, microsecond=0)
    return -1, target_anchor, config

def generate_report_from_inputs(inputs: Dict[str, Any], session_tz_key: str = "UTC") -> Dict[str, Any]:
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    
    # 1. Determine Anchor & Lock State
    norm_key = _normalize_session_key(session_tz_key)
    idx, anchor_dt, config = _get_anchor_times(raw_15m, norm_key)
    
    # Define Calibration Window (First 30m)
    calib_start = anchor_dt
    calib_end = anchor_dt + timedelta(minutes=30)
    
    if idx >= 0:
        anchor_candle = raw_15m[idx]
        start_24 = max(0, idx - 96)
        slice_24h = raw_15m[start_24 : idx]
        start_4 = max(0, idx - 16)
        slice_4h = raw_15m[start_4 : idx]
        is_locked = True
        locked_at = calib_end  # Conceptual lock time
    else:
        anchor_candle = raw_15m[-1] if raw_15m else {}
        slice_24h = raw_15m[-96:] if raw_15m else []
        slice_4h = raw_15m[-16:] if raw_15m else []
        is_locked = False
        locked_at = datetime.now(timezone.utc)

    # 2. Run Math Engine (SSE)
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
    
    # 3. Resolve Conflicts & Permission
    bias_model = computed["bias_model"]
    
    # Add 'requirements_remaining' to permission_state for explainability
    perm = bias_model["permission_state"]
    reqs = []
    if perm["state"] in ["DIRECTIONAL_LONG", "DIRECTIONAL_SHORT"]:
        # Simple logic derivation: if we have direction but no 5m alignment mentioned, imply it's needed
        if not perm.get("evidence", {}).get("alignment_5m", False):
            reqs.append("5m_alignment")
    perm["requirements_remaining"] = reqs
    
    # 4. Generate Trade Logic & Rename Conflicting Fields
    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs={**inputs, **computed})
    
    # RENAME regime -> playbook_regime_label to avoid overriding bias_model
    if "regime" in trade_logic:
        trade_logic["playbook_regime_label"] = trade_logic.pop("regime")
    # REMOVE analysis_bias from trade_logic to ensure bias_model is sole truth
    if "analysis_bias" in trade_logic:
        del trade_logic["analysis_bias"]

    # 5. Construct Hardened Contract v1.3
    session_id = norm_key.upper()
    session_date_str = anchor_dt.strftime("%Y-%m-%d")
    session_key = f"{symbol}|{session_id}|{session_date_str}"
    
    report = {
        "contract_version": "1.3",
        "generated_at_utc": _to_iso_z(datetime.now(timezone.utc)),
        "symbol": symbol,
        "price": inputs.get("current_price"),
        
        "session": {
            "session_key": session_key,  # The Immutable ID
            "session_id": session_id,
            "label": config["label"],
            "anchor_tz": config["tz"],
            "session_date_anchor": session_date_str,
            "open_anchor": anchor_dt.isoformat(),
            "calibration": {
                "window_minutes": 30,
                "start_anchor": calib_start.isoformat(),
                "end_anchor": calib_end.isoformat(),
                "locked": is_locked,
                "locked_at_utc": _to_iso_z(locked_at) if is_locked else None
            },
            "mode": "debrief" if is_locked else "live"
        },
        
        # Source of Truth: Execution & Bias
        "bias_model": bias_model,
        
        # Source of Truth: Levels
        "levels": computed["levels"],
        "range_30m": {
            "high": computed["levels"]["range30m_high"], 
            "low": computed["levels"]["range30m_low"]
        },
        "htf_shelves": computed["htf_shelves"],
        
        # Source of Truth: Strategy Playbook (Renamed conflicts)
        "trade_logic": trade_logic,
        
        # Context
        "context": computed["context"]
    }
    
    return report

run_auto_raw = generate_report_from_inputs