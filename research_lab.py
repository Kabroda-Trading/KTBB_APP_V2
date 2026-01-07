# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v21.0 (STRICT DOCTRINE SHIP-READY)
# ==============================================================================
# Rules:
# 1. PERMISSION: 2x 15m Closes. Break=1/2, Confirm=2/2.
# 2. TIMING: 5m Pause detection starts ONLY after permission timestamp.
# 3. TRUTH: Timestamps use 'last' (Close Time) to prevent look-ahead.
# 4. CONTRACT: Stable outputs, no missing keys.
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

STATUS_MAPPING = {
    "BLOCKED": "Unavailable",
    "STANDBY": "Not in Play",
    "ARMED": "Waiting for Setup",
    "HUNTING": "Waiting for Setup",
    "ACTIVE SIGNAL": "Tracking Opportunity",
    "CRITICAL ALERT": "Stand Down",
    "OFF-HOURS": "Monitor Only"
}

# --- MATH HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def _resample_candles(candles: List[Dict], timeframe: str) -> List[Dict]:
    """
    Robust resampling. 
    CRITICAL CHANGE: Uses 'time': 'last' so timestamps represent the CLOSE time.
    This ensures strict sequential logic (Permission at 09:00 -> Pause starts > 09:00).
    """
    if not candles: return []
    df = pd.DataFrame(candles)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    
    ohlc = {
        'open': 'first', 
        'high': 'max', 
        'low': 'min', 
        'close': 'last', 
        'volume': 'sum', 
        'time': 'last' # <--- DOCTRINE FIX: Use Close Time for sequencing
    }
    try:
        return df.resample(timeframe).agg(ohlc).dropna().to_dict('records')
    except: return []

# --- STABLE TEMPLATES (CONTRACT) ---
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
    if not raw_1h:
        return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No Data"}

    daily = _resample_candles(raw_1h, '1D')
    h4 = _resample_candles(raw_1h, '4h')
    
    current_price = raw_1h[-1]['close']
    ema_21 = calculate_ema([c['close'] for c in daily], 21)
    
    if not ema_21:
        return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "confidence": 0.0, "note": "Insufficient daily data."}

    # 1. LEAN
    lean = "BULLISH" if current_price > ema_21[-1] else "BEARISH"

    # 2. PHASE
    phase = "ROTATION"
    if h4 and len(h4) > 2:
        last = h4[-1]; prev = h4[-2]
        if lean == "BULLISH":
            if last['close'] > prev['high']: phase = "ADVANCE"
            elif last['close'] < prev['low']: phase = "PULLBACK"
            else: phase = "TRANSITION"
        else:
            if last['close'] < prev['low']: phase = "ADVANCE"
            elif last['close'] > prev['high']: phase = "PULLBACK"
            else: phase = "TRANSITION"

    note = f"Market is {lean} in {phase} phase."
    if phase == "PULLBACK": note += " Corrective move. Be cautious of traps."
    
    # 3. CAMPAIGN (Hidden in Drawer)
    campaign_status = "NOT_ARMED"
    campaign_note = "Structure alignment pending."
    if phase == "PULLBACK" and abs(current_price - ema_21[-1])/current_price < 0.015:
        campaign_status = "ARMING"
        campaign_note = "Price near Daily Equilibrium."

    return {
        "status": "LIVE",
        "lean": lean,
        "phase": phase,
        "confidence": 0.9,
        "note": note,
        "campaign": {"status": campaign_status, "side": "SHORT" if lean=="BEARISH" else "LONG", "note": campaign_note}
    }

# --- ENGINE 2: SESSION BATTLE (STRICT DOCTRINE) ---
def _calculate_session_battle(raw_5m: List[Dict], levels: Dict, ledger: Dict) -> Dict[str, Any]:
    sb = _session_battle_template()
    if not raw_5m: return sb
    
    current_price = raw_5m[-1]['close']
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    val = levels.get("f24_val", 0)
    vah = levels.get("f24_vah", 0)
    poc = levels.get("f24_poc", 0)

    # 1. LOCATION TRUTH
    if current_price > bo: sb["location"]["relative_to_triggers"] = "ABOVE_BAND"
    elif current_price < bd: sb["location"]["relative_to_triggers"] = "BELOW_BAND"
    
    if current_price > vah: sb["location"]["relative_to_value"] = "ABOVE_VALUE"
    elif current_price < val: sb["location"]["relative_to_value"] = "BELOW_VALUE"
    
    if abs(current_price - poc) / poc < 0.001: sb["location"]["relative_to_poc"] = "AT_POC"
    elif current_price > poc: sb["location"]["relative_to_poc"] = "ABOVE_POC"
    else: sb["location"]["relative_to_poc"] = "BELOW_POC"

    # 2. PERMISSION GATE (Strict 15m)
    candles_15m = _resample_candles(raw_5m, '15min')
    
    # Needs at least 2 closed candles to judge 2/2
    if len(candles_15m) >= 2:
        c1 = candles_15m[-1]['close'] # Most recent closed
        c2 = candles_15m[-2]['close'] # Previous closed
        
        # PERSISTENCE CHECK: If already earned, keep it unless invalidated
        if ledger.get("permission_status") == "EARNED":
            sb["permission"]["status"] = "EARNED"
            sb["permission"]["side"] = ledger["permission_side"]
            sb["acceptance_progress"]["count"] = 2
            sb["acceptance_progress"]["side_hint"] = ledger["permission_side"]
        else:
            # CHECK FRESH PERMISSION
            count = 0
            side = "NONE"
            
            # Bear Logic
            if c1 < bd:
                count = 1; side = "SHORT"
                if c2 < bd: count = 2
            
            # Bull Logic (Override)
            elif c1 > bo:
                count = 1; side = "LONG"
                if c2 > bo: count = 2
            
            sb["acceptance_progress"]["count"] = count
            sb["acceptance_progress"]["side_hint"] = side
            
            if count == 2:
                sb["permission"]["status"] = "EARNED"
                sb["permission"]["side"] = side
                # SAVE TO LEDGER
                ledger["permission_status"] = "EARNED"
                ledger["permission_side"] = side
                # IMPORTANT: Permission time is the CLOSE time of the confirming candle
                ledger["permission_time"] = candles_15m[-1]['time']

    if sb["permission"]["status"] == "NOT_EARNED":
        sb["action"] = "HOLD FIRE"
        cnt = sb["acceptance_progress"]["count"]
        if cnt == 0: sb["reason"] = "No acceptance yet. Waiting for 15m close beyond trigger."
        elif cnt == 1: sb["reason"] = "Acceptance 1/2 printed. Waiting for second 15m close."
        else: sb["reason"] = "Permission not earned. Session is rotational."
        return sb

    # 3. EXECUTION GATE (5m Pause)
    # DOCTRINE: Only use candles strictly AFTER permission earned
    perm_time = ledger.get("permission_time", 0)
    exec_candles = [c for c in raw_5m if c['time'] > perm_time]
    
    if len(exec_candles) < 2:
        sb["action"] = "PREPARE"
        sb["reason"] = "Permission earned. Now waiting for 5m pause structure."
        return sb
        
    pause_high = max(c['high'] for c in exec_candles)
    pause_low = min(c['low'] for c in exec_candles)
    range_pct = (pause_high - pause_low) / current_price
    
    pause_state = "NONE"
    if range_pct < 0.0035: # Tight range (0.35%)
        pause_state = "CONFIRMED" if len(exec_candles) >= 4 else "FORMING"
        
    sb["execution"]["pause_state"] = pause_state
    
    # Map Levels
    side = sb["permission"]["side"]
    if side == "SHORT":
        sb["execution"]["levels"]["failure"] = pause_high
        sb["execution"]["levels"]["continuation"] = pause_low
    else:
        sb["execution"]["levels"]["failure"] = pause_low
        sb["execution"]["levels"]["continuation"] = pause_high

    # 4. RESUMPTION (Decision)
    if pause_state == "NONE":
        sb["action"] = "HOLD FIRE"
        sb["reason"] = "Permission earned, but price is volatile. Wait for 5m pause."
    elif side == "SHORT":
        if current_price < pause_low * 0.9995:
            sb["action"] = "GREENLIGHT"
            sb["reason"] = "Pause resolved lower. Continuation confirmed."
            sb["execution"]["resumption_state"] = "CONFIRMED"
        else:
            sb["action"] = "PREPARE"
            sb["reason"] = "Pause forming. Continuation requires break below pause low."
    elif side == "LONG":
        if current_price > pause_high * 1.0005:
            sb["action"] = "GREENLIGHT"
            sb["reason"] = "Pause resolved higher. Continuation confirmed."
            sb["execution"]["resumption_state"] = "CONFIRMED"
        else:
            sb["action"] = "PREPARE"
            sb["reason"] = "Pause forming. Continuation requires break above pause high."

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

def _compute_session_packet(raw_5m: List[Dict], raw_1h: List[Dict], anchor_idx: int) -> Dict[str, Any]:
    if anchor_idx == -1 or anchor_idx + 6 > len(raw_5m): return {"error": "Insufficient data"}
    calibration_slice = raw_5m[anchor_idx : anchor_idx + 6]
    lock_idx = anchor_idx + 6
    context_start = max(0, lock_idx - 288)
    context_slice = raw_5m[context_start : lock_idx]
    
    daily_structure = _resample_candles(raw_1h, '1D')
    
    r30_high = max(c['high'] for c in calibration_slice)
    r30_low = min(c['low'] for c in calibration_slice)
    
    sse_input = {
        "raw_15m_candles": context_slice, "raw_daily_candles": daily_structure,
        "slice_24h": context_slice, "slice_4h": context_slice[-48:],
        "session_open_price": calibration_slice[0]['open'],
        "r30_high": r30_high, "r30_low": r30_low,
        "last_price": context_slice[-1]['close']
    }
    
    computed = sse_engine.compute_sse_levels(sse_input)
    return {
        "levels": computed["levels"],
        "bias_score": computed["bias_model"]["daily_lean"]["score"],
        "r30_high": r30_high, "r30_low": r30_low
    }

# --- MAIN LIVE RUNNER ---
async def run_live_pulse(symbol: str, session_mode: str="AUTO", manual_id: str=None, risk_mode="fixed_margin", capital=1000, leverage=1) -> Dict[str, Any]:
    try:
        raw_5m = await fetch_5m_granular(symbol)
        raw_1h = await fetch_1h_context(symbol)
        if not raw_5m: return {"status": "ERROR", "message": "No Data"}
        
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        # 1. SESSION LOCK
        session_info = _get_active_session(now_utc, session_mode, manual_id)
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800
        session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
        
        packet = {}
        if session_key in LOCKED_SESSIONS:
            packet = LOCKED_SESSIONS[session_key]
        elif now_ts < lock_end_ts:
            return {"status": "CALIBRATING", "message": "Market Opening...", "session": session_info, "price": raw_5m[-1]['close'], "battlebox": {}}
        else:
            anchor_idx = next((i for i, c in enumerate(raw_5m) if c['time'] >= anchor_ts), -1)
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
            if "error" in packet: return {"status": "ERROR", "message": packet["error"]}
            LOCKED_SESSIONS[session_key] = packet
            packet["ledger"] = {}
            
        levels = packet["levels"]
        ledger = packet.setdefault("ledger", {})
        
        # 2. ENGINES
        war_map = _calculate_war_map(raw_1h)
        
        anchor_idx_live = next((i for i, c in enumerate(raw_5m) if c['time'] >= anchor_ts), 0)
        live_slice = raw_5m[anchor_idx_live:]
        session_battle = _calculate_session_battle(live_slice, levels, ledger)
        
        # 3. LEGACY STRATEGIES (For Drawer)
        risk = {"mode": risk_mode, "value": float(capital), "leverage": float(leverage)}
        auditors = {
            "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic,
            "S4": strategy_auditor.run_s4_logic, "S7": strategy_auditor.run_s7_logic
        }
        strategies = []
        for code, func in auditors.items():
            try:
                res = func(levels, _resample_candles(raw_5m, '15min')[-30:], live_slice, risk, "ROTATIONAL")
                strategies.append({
                    "code": code, "name": STRATEGY_DISPLAY.get(code, code),
                    "status": "Available" if res['audit']['valid'] else "Wait",
                    "msg": res['audit']['reason']
                })
            except: pass

        # 4. STORY & BATTLEBOX
        story_now = f"{session_battle['action']}. {session_battle['reason']}"
        story_wait = "Monitoring permission and 5m structure."
        
        battlebox = {
            "war_map_context": war_map,
            "war_map_campaign": war_map.get("campaign"),
            "session_battle": session_battle,
            "session": session_info,
            "levels": levels,
            "strategies_summary": strategies,
            "story_now": story_now,
            "story_wait": story_wait,
            "ladder": {
                "permission": session_battle["permission"]["status"],
                "pullback": "QUALIFIED" if session_battle["execution"]["pause_state"] == "CONFIRMED" else ("FORMING" if session_battle["execution"]["pause_state"] == "FORMING" else "NOT STARTED"),
                "resumption": session_battle["execution"]["resumption_state"]
            }
        }

        return {
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "price": raw_5m[-1]['close'],
            "energy": session_info["energy"],
            "levels": levels,
            "battlebox": battlebox
        }

    except Exception as e:
        print(f"API ERROR: {e}")
        traceback.print_exc()
        return {"status": "ERROR", "message": str(e)}

# --- HISTORICAL (Legacy) ---
def detect_regime(candles_15m, bias_score, levels): return "ROTATIONAL"
async def run_historical_analysis(inputs, session_keys, leverage, capital, strategy_mode, risk_mode): return {}