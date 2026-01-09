# battle_control.py
# ==============================================================================
# KABRODA BATTLE CONTROL ENGINE v2.1 (FINAL DEPLOY)
# ==============================================================================
# 1. LOCKING: Explicit 30m window (anchor_ts + 1800).
# 2. SLICING: Strict timestamp boundaries for Context vs Execution.
# 3. SAFETY: Stable payloads & Consistent Vocabulary (INSIDE vs INSIDE_BAND).
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import pandas as pd
import sse_engine
import structure_state_engine # The Law Layer
import ccxt.async_support as ccxt
import pytz
import traceback

# --- GLOBAL PERSISTENCE ---
LOCKED_SESSIONS = {}

# --- CONFIGURATION ---
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity", "name": "NY Equity", "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london", "name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo", "name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"id": "au_sydney", "name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

STRATEGY_DISPLAY = {
    "S1": "Breakout Expansion",
    "S2": "Breakdown Expansion",
    "S4": "Mid-Band Fade",
    "S5": "Range Extremes",
    "S6": "Value Rotation",
    "S7": "Trend Continuation",
    "S8": "Trap Condition",
    "S9": "Stand Down Filter"
}

# --- HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def _resample_candles(candles: List[Dict], timeframe: str) -> List[Dict]:
    if not candles: return []
    df = pd.DataFrame(candles)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    ohlc = {'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum', 'time':'last'}
    try: return df.resample(timeframe).agg(ohlc).dropna().to_dict('records')
    except: return []

def _safe_placeholder_state(reason="Waiting..."):
    return {
        "action": "HOLD FIRE",
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        # FIXED: Vocabulary alignment with Structure State Engine (ABOVE/INSIDE/BELOW)
        "location": {"relative_to_triggers": "INSIDE"}, 
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "PREVIEW",
            "locked_at": None,
            "levels": {"failure": 0.0, "continuation": 0.0}
        }
    }

# --- ENGINE 1: WAR MAP CONTEXT ---
def _calculate_war_map(raw_1h: List[Dict]) -> Dict[str, Any]:
    if not raw_1h: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No Data"}
    daily = _resample_candles(raw_1h, '1D')
    h4 = _resample_candles(raw_1h, '4h')
    current_price = raw_1h[-1]['close']
    ema_21 = calculate_ema([c['close'] for c in daily], 21)
    
    if not ema_21: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "confidence": 0.0, "note": "Insufficient data."}

    lean = "BULLISH" if current_price > ema_21[-1] else "BEARISH"
    phase = "ROTATION"
    if h4 and len(h4) > 2:
        last = h4[-1]; prev = h4[-2]
        if lean == "BULLISH": phase = "ADVANCE" if last['close'] > prev['high'] else ("PULLBACK" if last['close'] < prev['low'] else "TRANSITION")
        else: phase = "ADVANCE" if last['close'] < prev['low'] else ("PULLBACK" if last['close'] > prev['high'] else "TRANSITION")

    # MANDATORY REFRAMING: Context, NOT Permission
    note = f"Pressure reads {lean} with {phase} behavior. This is context, not permission."
    
    campaign_status = "NOT_ARMED"
    campaign_note = "Structure alignment pending."
    if phase == "PULLBACK" and abs(current_price - ema_21[-1])/current_price < 0.015:
        campaign_status = "ARMING"; campaign_note = "Price near Daily Equilibrium."

    return {
        "status": "LIVE", "lean": lean, "phase": phase, "confidence": 0.9, "note": note,
        "campaign": {"status": campaign_status, "side": "SHORT" if lean=="BEARISH" else "LONG", "note": campaign_note}
    }

# --- CORE SESSION FETCH ---
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=1500)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

async def fetch_1h_context(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '1h', limit=720)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

# --- PACKET COMPUTATION (STRICT TIMESTAMP SLICING) ---
def _compute_session_packet(raw_5m: List[Dict], raw_1h: List[Dict], anchor_ts: int) -> Dict[str, Any]:
    # 1. DEFINE LOCK TIME
    lock_end_ts = anchor_ts + 1800 # Exactly 30 mins
    
    # 2. SLICE BY TIMESTAMP
    calibration_slice = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
    
    # Guard: Need at least 6 candles (30m)
    if len(calibration_slice) < 6:
        # If we are slightly past lock time but data is laggy, we might fail here.
        # But for 'compute_packet', we assume data is present.
        return {"error": "Insufficient calibration window (need 30m of data)."}

    # Context Slices (Ending exactly at lock_end_ts)
    context_slice_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
    slice_4h_5m = [c for c in raw_5m if (lock_end_ts - 14400) <= c["time"] < lock_end_ts]
    
    daily_structure = _resample_candles(raw_1h, '1D')

    # 3. COMPUTE ANCHORS
    session_open_price = calibration_slice[0]['open']
    r30_high = max(c['high'] for c in calibration_slice)
    r30_low = min(c['low'] for c in calibration_slice)
    
    # Last price at the moment of locking (for context display only)
    last_price = context_slice_24h[-1]['close'] if context_slice_24h else session_open_price

    # 4. PASS TO SSE v2.0 (5m NATIVE)
    sse_input = {
        "locked_history_5m": context_slice_24h, # Primary context
        "slice_24h_5m": context_slice_24h,
        "slice_4h_5m": slice_4h_5m,
        "raw_daily_candles": daily_structure,
        "session_open_price": session_open_price,
        "r30_high": r30_high,
        "r30_low": r30_low,
        "last_price": last_price
    }
    
    computed = sse_engine.compute_sse_levels(sse_input)
    
    # 3A. SSE ERROR GUARD
    if "error" in computed:
        return {"error": computed["error"], "meta": computed.get("meta", {})}

    return {
        "levels": computed["levels"], 
        "lock_time": lock_end_ts # Store the explicit lock timestamp
    }

def _get_active_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> Dict[str, Any]:
    # (Same as before - kept brief)
    if mode == "MANUAL" and manual_id:
        cfg = next((s for s in SESSION_CONFIGS if s["id"] == manual_id), None)
        if cfg:
            tz = pytz.timezone(cfg["tz"])
            now_local = now_utc.astimezone(tz)
            open_time = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
            if now_local < open_time: open_time = open_time - timedelta(days=1)
            elapsed = (now_local - open_time).total_seconds() / 60
            if elapsed < 30: energy = "CALIBRATING"
            elif elapsed < 240: energy = "PRIME"
            elif elapsed < 420: energy = "LATE"
            else: energy = "DEAD"
            return {"name": cfg["name"], "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()), "anchor_fmt": open_time.strftime("%H:%M") + " " + cfg["name"].upper(), "date_key": open_time.strftime("%Y-%m-%d"), "energy": energy, "manual": True}

    candidates = []
    for s in SESSION_CONFIGS:
        tz = pytz.timezone(s["tz"])
        now_local = now_utc.astimezone(tz)
        open_time = now_local.replace(hour=s["open_h"], minute=s["open_m"], second=0, microsecond=0)
        if now_local < open_time: open_time = open_time - timedelta(days=1)
        elapsed = (now_local - open_time).total_seconds() / 60
        if elapsed < 30: energy = "CALIBRATING"
        elif elapsed < 240: energy = "PRIME"
        elif elapsed < 420: energy = "LATE"
        else: energy = "DEAD"
        candidates.append({"name": s["name"], "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()), "anchor_fmt": open_time.strftime("%H:%M") + " " + s["name"].upper(), "date_key": open_time.strftime("%Y-%m-%d"), "energy": energy, "diff": elapsed, "manual": False})
    candidates.sort(key=lambda x: x['diff'])
    return candidates[0]

# --- MAIN RUNNER ---
async def run_live_pulse(symbol: str, session_mode: str="AUTO", manual_id: str=None, operator_flex: bool=False, risk_mode="fixed_margin", capital=1000, leverage=1) -> Dict[str, Any]:
    try:
        raw_5m = await fetch_5m_granular(symbol)
        raw_1h = await fetch_1h_context(symbol)
        if not raw_5m: return {"status": "ERROR", "message": "No Data"}
        
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        session_info = _get_active_session(now_utc, session_mode, manual_id)
        anchor_ts = session_info["anchor_time"]
        
        # 3B. STABLE PAYLOAD DURING CALIBRATION
        if now_ts < anchor_ts + 1800:
            wm = _calculate_war_map(raw_1h)
            safe_battlebox = {
                "war_map_context": wm,
                "war_map_campaign": wm.get("campaign"), 
                "session_battle": _safe_placeholder_state("Calibrating... levels not locked yet."),
                "session": session_info,
                "levels": {}, # Empty levels is safe for UI
                "strategies_summary": [],
                "story_now": "Market Calibrating.",
                "story_wait": "Hold fire."
            }
            return {
                "status": "CALIBRATING",
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
                "price": raw_5m[-1]['close'],
                # FIXED: Energy matches logic (prevents UI drift)
                "energy": session_info["energy"], 
                "battlebox": safe_battlebox
            }
        
        session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
        
        # Compute if not locked
        if session_key not in LOCKED_SESSIONS:
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_ts)
            
            if "error" in packet: 
                # Return stable error state
                wm = _calculate_war_map(raw_1h)
                err_battlebox = {
                    "war_map_context": wm, "war_map_campaign": wm.get("campaign"), 
                    "session_battle": _safe_placeholder_state(f"Error: {packet['error']}"),
                    "session": session_info, "levels": {}, "strategies_summary": []
                }
                return {"status": "ERROR", "message": packet["error"], "battlebox": err_battlebox}
            
            LOCKED_SESSIONS[session_key] = packet
            
        levels = LOCKED_SESSIONS[session_key]["levels"]
        lock_time = LOCKED_SESSIONS[session_key]["lock_time"] # Guaranteed by _compute_session_packet
        
        # SLICE 5M HISTORY FROM LOCK TIME FORWARD
        # (This feeds the State Engine Law Layer)
        post_lock_candles = [c for c in raw_5m if c['time'] >= lock_time]
        
        # 3C. EMPTY SLICE GUARD
        if not post_lock_candles:
            session_battle = _safe_placeholder_state("Waiting for post-lock candles.")
        else:
            session_battle = structure_state_engine.compute_structure_state(levels, post_lock_candles)
        
        strategies = []
        try:
            for code in ["S1","S2","S4","S7"]:
                strategies.append({"code": code, "name": STRATEGY_DISPLAY.get(code, code), "status": "Inactive", "msg": "Legacy Mode"})
        except: pass

        wm = _calculate_war_map(raw_1h)
        battlebox = {
            "war_map_context": wm,
            "war_map_campaign": wm.get("campaign"), 
            "session_battle": session_battle,
            "session": session_info,
            "levels": levels,
            "strategies_summary": strategies,
            "story_now": f"{session_battle['action']}. {session_battle['reason']}",
            "story_wait": "Monitoring execution gates."
        }

        return {
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "price": raw_5m[-1]['close'],
            "energy": session_info["energy"],
            "battlebox": battlebox
        }

    except Exception as e:
        print(f"API ERROR: {e}")
        traceback.print_exc()
        return {"status": "ERROR", "message": str(e)}

async def run_historical_analysis(inputs, session_keys, leverage, capital, strategy_mode, risk_mode): return {}