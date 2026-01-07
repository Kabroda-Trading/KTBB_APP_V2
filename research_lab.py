# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v24.1 (FINAL POLISH)
# ==============================================================================
# Fixes:
# 1. PAYLOAD: Added 'war_map_campaign' explicitly to battlebox so UI can read it.
# 2. SAFETY: Added zero-check for fallback gates to prevent UI blanking.
# 3. RETAINED: All v24.0 Strict Doctrine logic (Permission, Timing, etc).
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor
import traceback
import json

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

# --- MATH HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def _resample_candles(candles: List[Dict], timeframe: str) -> List[Dict]:
    """Robust resampling using CLOSE TIME ('last') for strict sequencing."""
    if not candles: return []
    df = pd.DataFrame(candles)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    ohlc = {'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum', 'time':'last'}
    try: return df.resample(timeframe).agg(ohlc).dropna().to_dict('records')
    except: return []

# --- STABLE TEMPLATE ---
def _session_battle_template():
    return {
        "action": "HOLD FIRE",
        "reason": "Waiting for data...",
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {
            "relative_to_triggers": "INSIDE_BAND", 
            "relative_to_value": "INSIDE_VALUE", 
            "relative_to_poc": "AT_POC"
        },
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
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

    note = f"Market is {lean} in {phase} phase."
    if phase == "PULLBACK": note += " Corrective move. Be cautious."
    
    campaign_status = "NOT_ARMED"
    campaign_note = "Structure alignment pending."
    if phase == "PULLBACK" and abs(current_price - ema_21[-1])/current_price < 0.015:
        campaign_status = "ARMING"; campaign_note = "Price near Daily Equilibrium."

    return {
        "status": "LIVE", "lean": lean, "phase": phase, "confidence": 0.9, "note": note,
        "campaign": {"status": campaign_status, "side": "SHORT" if lean=="BEARISH" else "LONG", "note": campaign_note}
    }

# --- ENGINE 2: SESSION BATTLE ---
def _calculate_session_battle(raw_5m: List[Dict], levels: Dict, ledger: Dict) -> Dict[str, Any]:
    sb = _session_battle_template()
    if not raw_5m: return sb
    
    current_price = raw_5m[-1]['close']
    bo, bd = levels.get("breakout_trigger", 0), levels.get("breakdown_trigger", 0)
    val, vah = levels.get("f24_val", 0), levels.get("f24_vah", 0)
    poc = levels.get("f24_poc", 0)

    # 1. LOCATION
    sb["location"]["relative_to_triggers"] = "ABOVE_BAND" if current_price > bo else ("BELOW_BAND" if current_price < bd else "INSIDE_BAND")
    sb["location"]["relative_to_value"] = "ABOVE_VALUE" if current_price > vah else ("BELOW_VALUE" if current_price < val else "INSIDE_VALUE")
    sb["location"]["relative_to_poc"] = "AT_POC" if abs(current_price - poc)/poc < 0.001 else ("ABOVE_POC" if current_price > poc else "BELOW_POC")

    # 2. PERMISSION (PERSISTENT SCAN)
    candles_15m = _resample_candles(raw_5m, '15min')
    
    if ledger.get("permission_status") == "EARNED":
        sb["permission"]["status"] = "EARNED"
        sb["permission"]["side"] = ledger["permission_side"]
        sb["acceptance_progress"]["count"] = 2
        sb["acceptance_progress"]["side_hint"] = ledger["permission_side"]
    else:
        consecutive_bear = 0
        consecutive_bull = 0
        for c in candles_15m:
            if c['close'] < bd: consecutive_bear += 1
            else: consecutive_bear = 0
            
            if c['close'] > bo: consecutive_bull += 1
            else: consecutive_bull = 0
            
            if consecutive_bear == 2:
                sb["permission"] = {"status": "EARNED", "side": "SHORT"}
                sb["acceptance_progress"] = {"count": 2, "required": 2, "side_hint": "SHORT"}
                ledger.update({"permission_status": "EARNED", "permission_side": "SHORT", "permission_time": c['time']})
                break
            if consecutive_bull == 2:
                sb["permission"] = {"status": "EARNED", "side": "LONG"}
                sb["acceptance_progress"] = {"count": 2, "required": 2, "side_hint": "LONG"}
                ledger.update({"permission_status": "EARNED", "permission_side": "LONG", "permission_time": c['time']})
                break
        
        if sb["permission"]["status"] == "NOT_EARNED" and candles_15m:
            last = candles_15m[-1]['close']
            if last < bd: sb["acceptance_progress"] = {"count": 1, "required": 2, "side_hint": "SHORT"}
            elif last > bo: sb["acceptance_progress"] = {"count": 1, "required": 2, "side_hint": "LONG"}

    # 3. GATES & PAUSE
    perm_side = sb["permission"]["side"]
    perm_time = ledger.get("permission_time", 0)
    
    # PATCH 2: Bulletproof Gates (Inside Band Fallback)
    if perm_side == "SHORT" or (perm_side == "NONE" and current_price < bd):
        sb["execution"]["levels"]["failure"] = bd * 1.001
        sb["execution"]["levels"]["continuation"] = current_price * 0.999
    elif perm_side == "LONG" or (perm_side == "NONE" and current_price > bo):
        sb["execution"]["levels"]["failure"] = bo * 0.999
        sb["execution"]["levels"]["continuation"] = current_price * 1.001
    else:
        # Inside Band Fallback
        if bo > 0 and bd > 0:
            sb["execution"]["levels"]["failure"] = bo
            sb["execution"]["levels"]["continuation"] = bd
        else:
            # Ultra-safe fallback (No Zeros)
            sb["execution"]["levels"]["failure"] = current_price
            sb["execution"]["levels"]["continuation"] = current_price

    if sb["permission"]["status"] == "NOT_EARNED":
        sb["action"] = "HOLD FIRE"
        sb["reason"] = f"Permission not earned ({sb['acceptance_progress']['count']}/2)."
        return sb

    # 4. EXECUTION
    exec_candles = [c for c in raw_5m if c['time'] > perm_time]
    
    if len(exec_candles) < 2:
        sb["action"] = "PREPARE"
        sb["reason"] = "Permission earned. Waiting for structure."
        return sb
        
    p_high = max(c['high'] for c in exec_candles)
    p_low = min(c['low'] for c in exec_candles)
    range_pct = (p_high - p_low) / current_price
    
    pause_state = "FORMING" if range_pct < 0.0035 else "NONE"
    if len(exec_candles) >= 4 and pause_state == "FORMING": pause_state = "CONFIRMED"
    
    sb["execution"]["pause_state"] = pause_state
    
    if pause_state == "NONE":
        sb["action"] = "HOLD FIRE"
        sb["reason"] = "Permission earned, but no 5m pause yet."
        if perm_side == "SHORT":
            sb["execution"]["levels"]["failure"] = p_high
            sb["execution"]["levels"]["continuation"] = p_low
        else:
            sb["execution"]["levels"]["failure"] = p_low
            sb["execution"]["levels"]["continuation"] = p_high
        return sb

    if perm_side == "SHORT":
        sb["execution"]["levels"]["failure"] = p_high
        sb["execution"]["levels"]["continuation"] = p_low
        if current_price < p_low * 0.9995:
            sb["action"] = "GREENLIGHT"
            sb["reason"] = "Pause resolved lower. Continuation active."
            sb["execution"]["resumption_state"] = "CONFIRMED"
        else:
            sb["action"] = "PREPARE"
            sb["reason"] = "Pause detected. Wait for break of Low."
    else: # LONG
        sb["execution"]["levels"]["failure"] = p_low
        sb["execution"]["levels"]["continuation"] = p_high
        if current_price > p_high * 1.0005:
            sb["action"] = "GREENLIGHT"
            sb["reason"] = "Pause resolved higher. Continuation active."
            sb["execution"]["resumption_state"] = "CONFIRMED"
        else:
            sb["action"] = "PREPARE"
            sb["reason"] = "Pause detected. Wait for break of High."

    return sb

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

def _compute_session_packet(raw_5m: List[Dict], raw_1h: List[Dict], anchor_idx: int) -> Dict[str, Any]:
    if anchor_idx == -1 or anchor_idx + 6 > len(raw_5m): return {"error": "Insufficient data"}
    calibration_slice = raw_5m[anchor_idx : anchor_idx + 6]
    context_slice = raw_5m[max(0, anchor_idx - 288) : anchor_idx + 6]
    daily_structure = _resample_candles(raw_1h, '1D')
    
    sse_input = {
        "raw_15m_candles": context_slice, "raw_daily_candles": daily_structure,
        "slice_24h": context_slice, "slice_4h": context_slice[-48:],
        "session_open_price": calibration_slice[0]['open'],
        "r30_high": max(c['high'] for c in calibration_slice), "r30_low": min(c['low'] for c in calibration_slice),
        "last_price": context_slice[-1]['close']
    }
    computed = sse_engine.compute_sse_levels(sse_input)
    return {"levels": computed["levels"], "bias_score": computed["bias_model"]["daily_lean"]["score"]}

def _get_active_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> Dict[str, Any]:
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
async def run_live_pulse(symbol: str, session_mode: str="AUTO", manual_id: str=None, risk_mode="fixed_margin", capital=1000, leverage=1) -> Dict[str, Any]:
    try:
        raw_5m = await fetch_5m_granular(symbol)
        raw_1h = await fetch_1h_context(symbol)
        if not raw_5m: return {"status": "ERROR", "message": "No Data"}
        
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        session_info = _get_active_session(now_utc, session_mode, manual_id)
        anchor_ts = session_info["anchor_time"]
        
        session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
        if session_key not in LOCKED_SESSIONS:
            if now_ts < anchor_ts + 1800:
                return {"status": "CALIBRATING", "message": "Calibrating...", "session": session_info, "price": raw_5m[-1]['close'], "battlebox": {}}
            
            anchor_idx = next((i for i, c in enumerate(raw_5m) if c['time'] >= anchor_ts), -1)
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
            if "error" in packet: return {"status": "ERROR", "message": packet["error"]}
            LOCKED_SESSIONS[session_key] = packet
            packet["ledger"] = {}
            
        levels = LOCKED_SESSIONS[session_key]["levels"]
        ledger = LOCKED_SESSIONS[session_key]["ledger"]
        
        battle_slice = [c for c in raw_5m if c['time'] >= (anchor_ts - 3600)]
        session_battle = _calculate_session_battle(battle_slice, levels, ledger)
        
        strategies = []
        try:
            risk = {"mode": risk_mode, "value": float(capital), "leverage": float(leverage)}
            for code in ["S1","S2","S4","S7"]:
                strategies.append({"code": code, "name": STRATEGY_DISPLAY.get(code, code), "status": "Inactive", "msg": "Legacy Mode"})
        except: pass

        # PATCH 1: Extract WM and Add Campaign Explicitly
        wm = _calculate_war_map(raw_1h)
        
        battlebox = {
            "war_map_context": wm,
            "war_map_campaign": wm.get("campaign"), # <--- PATCH 1 APPLIED
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