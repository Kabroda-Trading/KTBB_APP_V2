# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime, timedelta
import pytz
import pandas as pd
import sse_engine

# ---------------------------------------------------------
# HISTORICAL ANALYZER
# ---------------------------------------------------------
def _get_session_config(session_key: str):
    # REUSE: Same logic as dmr_report to ensure consistency
    key = session_key.lower()
    if "london" in key: return "Europe/London", 8, 0
    if "york_early" in key or "futures" in key: return "America/New_York", 8, 30
    if "york" in key: return "America/New_York", 9, 30
    if "tokyo" in key: return "Asia/Tokyo", 9, 0
    if "sydney" in key: return "Australia/Sydney", 7, 0
    return "UTC", 0, 0

def _find_all_anchors(intraday_candles: list, session_key: str, days_back: int = 5) -> List[int]:
    """
    Scans data to find ALL matching session opens in the last X days.
    Returns a list of INDICES in the candle array.
    """
    if not intraday_candles: return []
    
    tz_name, h, m = _get_session_config(session_key)
    try: market_tz = pytz.timezone(tz_name)
    except: market_tz = pytz.UTC
    
    indices = []
    
    # Iterate backwards through the data
    for i in range(len(intraday_candles) - 1, -1, -1):
        c = intraday_candles[i]
        utc_dt = datetime.fromtimestamp(c['time'], tz=pytz.UTC)
        market_dt = utc_dt.astimezone(market_tz)
        
        # Strict Match: Hour & Minute
        if market_dt.hour == h and market_dt.minute == m:
            indices.append(i)
            
        if len(indices) >= days_back:
            break
            
    return indices # Returns newest to oldest

def _analyze_session_outcome(levels: Dict, future_candles: List[Dict]) -> Dict:
    """
    Checks if the session was a 'Success'.
    Did price trigger BO/BD? Did it reach Targets?
    """
    if not future_candles:
        return {"result": "NO_DATA", "max_run": 0.0}
    
    bo = levels["breakout_trigger"]
    bd = levels["breakdown_trigger"]
    res = levels["daily_resistance"]
    sup = levels["daily_support"]
    
    triggered = "NONE"
    max_favorable = 0.0
    
    # Simple check of the next 4 hours (16 candles)
    for c in future_candles[:16]:
        h = c["high"]
        l = c["low"]
        
        if triggered == "NONE":
            if h > bo: triggered = "BREAKOUT"
            if l < bd: triggered = "BREAKDOWN"
            
        if triggered == "BREAKOUT":
            run = h - bo
            if run > max_favorable: max_favorable = run
            
        if triggered == "BREAKDOWN":
            run = bd - l
            if run > max_favorable: max_favorable = run
            
    return {
        "status": triggered,
        "max_pnl_pts": round(max_favorable, 2)
    }

def run_historical_analysis(inputs: Dict[str, Any], session_key: str, days: int = 5) -> List[Dict]:
    """
    The Main Loop: Generates a report for the last X sessions.
    """
    raw_15m = inputs.get("intraday_candles", [])
    if not raw_15m: return []
    
    anchor_indices = _find_all_anchors(raw_15m, session_key, days)
    history = []
    
    for idx in anchor_indices:
        anchor_candle = raw_15m[idx]
        
        # 1. RECREATE THE PAST (Slicing data relative to THAT moment)
        start_24 = max(0, idx - 96)
        slice_24h = raw_15m[start_24 : idx]
        start_4 = max(0, idx - 16)
        slice_4h = raw_15m[start_4 : idx]
        
        # 2. RUN SSE ENGINE (Exactly as it runs live)
        sse_input = {
            "raw_15m_candles": raw_15m[:idx], # Only give it data up to that point!
            "slice_24h": slice_24h,
            "slice_4h": slice_4h,
            "session_open_price": anchor_candle.get("open", 0.0),
            "r30_high": anchor_candle.get("high", 0.0),
            "r30_low": anchor_candle.get("low", 0.0),
            "last_price": anchor_candle.get("close", 0.0) # Price at open
        }
        
        computed = sse_engine.compute_sse_levels(sse_input)
        
        # 3. ANALYZE OUTCOME (Look at future candles)
        future_data = raw_15m[idx+1:]
        outcome = _analyze_session_outcome(computed["levels"], future_data)
        
        # 4. LOG IT
        ts = anchor_candle["time"]
        dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        
        history.append({
            "date": dt_str,
            "levels": computed["levels"],
            "outcome": outcome
        })
        
    return history