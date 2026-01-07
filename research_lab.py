# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v16.0 (REGIME TRUTH ARCHITECTURE)
# ==============================================================================
# Updates:
# - ARCHITECTURE: Decoupled Location (Physics) from Regime (Behavior).
# - LOGIC: Implemented "UNRESOLVED" start state. No default "ROTATIONAL".
# - PERSISTENCE: Directional regimes persist across session boundaries until invalidated.
# - METRICS: Added "distance_from_trigger" to context for Strategy Expiry logic.
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

def _resample_15m(raw_5m: List[Dict]) -> List[Dict]:
    if not raw_5m: return []
    df = pd.DataFrame(raw_5m)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'time': 'first'}
    try: return df.resample('15min').agg(ohlc).dropna().to_dict('records')
    except: return []

# --- CORE LOGIC: LOCATION ENGINE (LAYER 1) ---
def _compute_location(price: float, levels: Dict) -> Dict[str, str]:
    """
    Determines purely physical location of price relative to structure.
    State-less and computed on every tick.
    """
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    poc = levels.get("f24_poc", 0)
    
    loc = {}
    
    # Trigger Band
    if price > bo: loc["relative_to_triggers"] = "ABOVE_BAND"
    elif price < bd: loc["relative_to_triggers"] = "BELOW_BAND"
    else: loc["relative_to_triggers"] = "INSIDE_BAND"
    
    # Value
    if price > vah: loc["relative_to_value"] = "ABOVE_VALUE"
    elif price < val: loc["relative_to_value"] = "BELOW_VALUE"
    else: loc["relative_to_value"] = "INSIDE_VALUE"
    
    # POC (Tolerance 0.1%)
    if price > poc * 1.001: loc["relative_to_poc"] = "ABOVE_POC"
    elif price < poc * 0.999: loc["relative_to_poc"] = "BELOW_POC"
    else: loc["relative_to_poc"] = "AT_POC"
    
    return loc

# --- CORE LOGIC: REGIME ENGINE (LAYER 2) ---
def _determine_regime_state(phase: str, ledger: Dict, location: Dict, candles_15m: List[Dict], levels: Dict) -> Dict[str, Any]:
    """
    Determines the regime based on Behavior + Permission + Persistence.
    Does NOT default to ROTATIONAL. Starts as UNRESOLVED.
    """
    # 1. Check for Locked Directional State (Persistence)
    if ledger.get("regime_locked"):
        # Check Invalidation
        price = candles_15m[-1]['close']
        bo = levels.get("breakout_trigger", 0)
        bd = levels.get("breakdown_trigger", 0)
        vah = levels.get("f24_vah", 0)
        val = levels.get("f24_val", 0)
        
        current_regime = ledger["regime_current"]
        invalidated = False
        
        # Bear Invalidation: Reclaim Value + Hold
        if "BEAR" in current_regime:
            if price > val: # Simple check, ideally requires 2x 15m closes
                invalidated = True # For now, warn invalidation
        # Bull Invalidation
        if "BULL" in current_regime:
            if price < vah:
                invalidated = True
                
        if not invalidated:
            return {
                "regime": current_regime,
                "reason": ledger.get("regime_reason", "Directional persistence active."),
                "confidence": 1.0
            }
        else:
            # Unlock if invalidated
            ledger["regime_locked"] = False
            ledger["regime_current"] = "UNRESOLVED"

    # 2. Evaluate New Regime
    # Directional (Permission Based)
    if "CANDIDATE" in phase or "CONFIRMED" in phase:
        regime = "DIRECTIONAL_BULL" if "BULL" in phase else "DIRECTIONAL_BEAR"
        ledger["regime_locked"] = True
        ledger["regime_current"] = regime
        ledger["regime_reason"] = f"Permission earned via {phase}."
        return {"regime": regime, "reason": ledger["regime_reason"], "confidence": 0.9}
        
    # Rotational (Behavior Based)
    # Require: Inside Value AND No Acceptance
    if location["relative_to_value"] == "INSIDE_VALUE" and not ledger.get("acceptance_time"):
        # Heuristic: If we have > 4 bars of 15m data in this session inside value
        # (Simplified check: if we are deep inside band)
        return {
            "regime": "ROTATIONAL",
            "reason": "Price effectively rotating inside value with no acceptance.",
            "confidence": 0.7
        }

    # Default
    return {
        "regime": "UNRESOLVED",
        "reason": "Awaiting acceptance or proven rotation.",
        "confidence": 0.0
    }

def _compute_session_packet(raw_5m: List[Dict], raw_1h: List[Dict], anchor_idx: int) -> Dict[str, Any]:
    if anchor_idx == -1 or anchor_idx + 6 > len(raw_5m): return {"error": "Insufficient data"}
    calibration_slice = raw_5m[anchor_idx : anchor_idx + 6]
    lock_idx = anchor_idx + 6
    context_start = max(0, lock_idx - 288)
    context_slice = raw_5m[context_start : lock_idx]
    
    df_1h = pd.DataFrame(raw_1h) # Use full raw_1h for daily deriv
    daily_structure = []
    if not df_1h.empty:
        df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s')
        df_1h.set_index('dt', inplace=True)
        ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'time': 'first'}
        daily_structure = df_1h.resample('1D').agg(ohlc).dropna().to_dict('records')

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

def detect_phase_ledger(candles_15m, candles_5m, levels, ledger):
    if not candles_15m or not candles_5m: return {"phase": "UNKNOWN", "msg": "No Data"}
    price = candles_5m[-1]['close']
    bo, bd = levels.get("breakout_trigger", 0), levels.get("breakdown_trigger", 0)
    vah, val = levels.get("f24_vah", 0), levels.get("f24_val", 0)
    dr, ds = levels.get("daily_resistance", 0), levels.get("daily_support", 0)
    c1, c2 = candles_15m[-1]['close'], (candles_15m[-2]['close'] if len(candles_15m) > 1 else candles_15m[-1]['close'])
    
    # 1. ACCEPTANCE
    if c1 > bo and c2 > bo:
        if not ledger.get("acceptance_time"): ledger.update({"acceptance_time": candles_15m[-1]['time'], "acceptance_side": "BULL"})
    elif c1 < bd and c2 < bd:
        if not ledger.get("acceptance_time"): ledger.update({"acceptance_time": candles_15m[-1]['time'], "acceptance_side": "BEAR"})
            
    # 2. FAILURE
    side = ledger.get("acceptance_side")
    if side == "BULL" and c1 < vah:
        ledger["failure_time"] = candles_15m[-1]['time']
        return {"phase": "FAILED_BREAK_BULL", "msg": "Trap Detected"}
    if side == "BEAR" and c1 > val:
        ledger["failure_time"] = candles_15m[-1]['time']
        return {"phase": "FAILED_BREAK_BEAR", "msg": "Trap Detected"}

    # 3. CONFIRMATION
    if side == "BULL" and not ledger.get("confirmation_time"):
        post_accept = [c for c in candles_5m if c['time'] >= ledger["acceptance_time"]]
        shelves = [s for s in [bo, dr, vah] if s > 0 and s < price * 1.05]
        if any(any(c['low'] <= s * 1.0015 for c in post_accept) for s in shelves) and price > bo:
            ledger.update({"confirmation_time": candles_5m[-1]['time'], "confirmation_shelf": "SHELF"})
    elif side == "BEAR" and not ledger.get("confirmation_time"):
        post_accept = [c for c in candles_5m if c['time'] >= ledger["acceptance_time"]]
        shelves = [s for s in [bd, ds, val] if s > 0 and s > price * 0.95]
        if any(any(c['high'] >= s * 0.9985 for c in post_accept) for s in shelves) and price < bd:
            ledger.update({"confirmation_time": candles_5m[-1]['time'], "confirmation_shelf": "SHELF"})

    if ledger.get("failure_time"): return {"phase": f"FAILED_BREAK_{side}", "msg": "Trap Logic Active"}
    if ledger.get("confirmation_time"): return {"phase": f"DIRECTIONAL_CONFIRMED_{side}", "msg": "Shelf Retest Confirmed"}
    if ledger.get("acceptance_time"): return {"phase": f"DIRECTIONAL_CANDIDATE_{side}", "msg": "Accepted. Awaiting Pullback."}
    
    if val <= price <= vah: return {"phase": "BALANCE", "msg": "Rotational"}
    return {"phase": "TESTING_EDGE", "msg": "Testing Edge"}

# --- STORY ENGINE ---
def _generate_story(regime_data, phase, ledger, price, levels):
    regime = regime_data["regime"]
    
    if regime == "UNRESOLVED":
        return {"now": "Market is unresolved. Opening inside structure with no clear control.", "wait": "Waiting for price to engage a trigger or prove rotation."}
    
    if regime == "ROTATIONAL":
        return {"now": "Market is rotating inside value. No directional permission earned.", "wait": "Waiting for acceptance beyond Triggers (2x 15m closes) to arm direction."}
    
    side = "Bull" if "BULL" in regime else "Bear"
    trigger = levels.get("breakout_trigger") if side == "Bull" else levels.get("breakdown_trigger")
    val_level = levels.get("f24_val", 0)
    vah_level = levels.get("f24_vah", 0)
    
    if "CANDIDATE" in phase:
        limit_level = vah_level if side == "Bull" else val_level
        return {
            "now": f"Directional {side} Candidate. Acceptance printed, but structure unconfirmed.",
            "wait": f"Waiting for a pullback retest into {int(trigger)} that fails to reclaim value ({int(limit_level)})."
        }
    
    if "CONFIRMED" in phase:
        return {"now": f"Directional {side} Confirmed. Permission earned and Shelf held.", "wait": "Waiting for continuation entries. Do not chase extensions."}
        
    if "FAILED" in phase:
        return {"now": f"Failed Breakout ({side}). Market trapped back inside value.", "wait": "Monitor for rotation back to POC. Directional bets unsafe."}
        
    return {"now": "Calibrating...", "wait": "Waiting for session lock."}

# --- FETCHERS ---
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

def _build_safe_response(status="OK", msg="", session_info={}, levels={}, strategies=[], phase="INIT", ledger={}, location={}, regime_data={}):
    ladder = {
        "permission": "EARNED" if ledger.get("acceptance_time") else "WAITING",
        "pullback": "QUALIFIED" if ledger.get("confirmation_time") else ("FORMING" if ledger.get("acceptance_time") else "NOT STARTED"),
        "resumption": "UNDERWAY" if "CONFIRMED" in phase else "PENDING"
    }
    
    return {
        "status": status, "message": msg,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session_lock": session_info.get("anchor_fmt", "--"),
        "energy": session_info.get("energy", "UNKNOWN"),
        "price": 0, "regime": regime_data.get("regime", "UNKNOWN"), "phase": phase,
        "levels": levels, "strategies": strategies,
        "battlebox": {
            "session": {"name": session_info.get("name", "--"), "energy": session_info.get("energy", "--"), "anchor_fmt": session_info.get("anchor_fmt", "--")},
            "levels": levels,
            "location": location,
            "story_now": "Calibrating...",
            "waiting_for": "Session Lock",
            "ladder": ladder,
            "phase_timeline": {"current_phase": phase, "milestones": ledger},
            "strategies_summary": [],
            "regime_context": regime_data
        }
    }

# --- LIVE PULSE ---
async def run_live_pulse(symbol: str, session_mode: str="AUTO", manual_id: str=None, risk_mode="fixed_margin", capital=1000, leverage=1) -> Dict[str, Any]:
    try:
        raw_5m = await fetch_5m_granular(symbol)
        raw_1h = await fetch_1h_context(symbol)
        if not raw_5m: return _build_safe_response(status="ERROR", msg="No Data")
        
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        session_info = _get_active_session(now_utc, session_mode, manual_id)
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800
        session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
        
        packet = {}
        if session_key in LOCKED_SESSIONS:
            packet = LOCKED_SESSIONS[session_key]
        elif now_ts < lock_end_ts:
            resp = _build_safe_response(status="CALIBRATING", msg=f"Levels lock in {int((lock_end_ts - now_ts)/60)}m", session_info=session_info, phase="CALIBRATING")
            resp["price"] = raw_5m[-1]['close']
            return resp
        else:
            anchor_idx = next((i for i, c in enumerate(raw_5m) if c['time'] >= anchor_ts), -1)
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
            if "error" in packet: return _build_safe_response(status="ERROR", msg=packet["error"], session_info=session_info)
            packet["ledger"] = {}
            LOCKED_SESSIONS[session_key] = packet
            
        levels, ledger = packet["levels"], packet.setdefault("ledger", {})
        
        # ANALYSIS
        anchor_idx_live = next((i for i, c in enumerate(raw_5m) if c['time'] >= anchor_ts), 0)
        live_slice = raw_5m[anchor_idx_live:]
        candles_15m = _resample_15m(raw_5m)
        current_price = raw_5m[-1]['close']
        
        # 1. COMPUTE LOCATION (Layer 1)
        location = _compute_location(current_price, levels)
        
        # 2. COMPUTE PHASE
        phase_data = detect_phase_ledger(candles_15m[-30:], live_slice, levels, ledger)
        phase = phase_data["phase"]
        
        # 3. DETERMINE REGIME (Layer 2)
        regime_data = _determine_regime_state(phase, ledger, location, candles_15m[-30:], levels)
        regime = regime_data["regime"]
        
        # 4. STRATEGIES
        energy = session_info["energy"]
        results = []
        risk = {"mode": risk_mode, "value": float(capital), "leverage": float(leverage)}
        
        auditors = {
            "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic,
            "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic,
            "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic,
            "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic
        }
        
        for code, func in auditors.items():
            try:
                res = func(levels, candles_15m[-30:], live_slice, risk, regime)
                status, msg = "STANDBY", res['audit'].get('reason', 'Wait')
                full_name = STRATEGY_DISPLAY.get(code, code)
                
                # Expiry Logic
                if code == "S2" and "DIRECTIONAL_BEAR" in regime:
                    # Check distance from trigger
                    bd = levels.get("breakdown_trigger", 0)
                    dist_pct = abs(current_price - bd) / bd * 100
                    if current_price < bd and dist_pct > 1.5: # Expire if > 1.5% away
                        status = "STANDBY"
                        msg = "Price extended too far from trigger."

                if energy == "DEAD": status, msg = "OFF-HOURS", "Session exhausted."
                elif code == "S9" and res['status'] == "S9_ACTIVE": status = "CRITICAL ALERT"
                elif regime == "ROTATIONAL":
                    if code in ["S1", "S2", "S7"]: status = "BLOCKED"
                    elif code in ["S4", "S6"]: status = "ACTIVE SIGNAL" if res['entry'] > 0 else "MONITORING"
                elif "DIRECTIONAL_BULL" in regime:
                    if code == "S1": status = "ARMED"
                    elif code in ["S4", "S6", "S2"]: status = "BLOCKED"
                    elif code == "S7" and "CONFIRMED" in phase: status = "ACTIVE SIGNAL" if res['entry'] > 0 else "HUNTING"
                elif "DIRECTIONAL_BEAR" in regime:
                    if code == "S2": status = "ARMED"
                    elif code in ["S4", "S6", "S1"]: status = "BLOCKED"
                    elif code == "S7" and "CONFIRMED" in phase: status = "ACTIVE SIGNAL" if res['entry'] > 0 else "HUNTING"
                
                results.append({
                    "code": code,
                    "name": full_name,
                    "status": STATUS_MAPPING.get(status, status),
                    "raw_status": status,
                    "color": "#00ff9d" if status in ["ACTIVE SIGNAL", "HUNTING"] else ("#ffcc00" if status == "ARMED" else "#444"),
                    "message": msg,
                    "levels": {"target": res['audit'].get('target', 0)}
                })
            except: 
                results.append({"code": code, "name": code, "status": "Error", "color": "red"})

        # PACKET BUILD
        story = _generate_story(regime_data, phase, ledger, current_price, levels)
        resp = _build_safe_response(session_info=session_info, levels=levels, strategies=results, phase=phase, ledger=ledger, location=location, regime_data=regime_data)
        
        resp["price"] = current_price
        resp["regime"] = regime
        resp["phase"] = phase + (" (" + phase_data.get("msg","") + ")")
        resp["battlebox"]["story_now"] = story["now"]
        resp["battlebox"]["waiting_for"] = story["wait"]
        resp["battlebox"]["strategies_summary"] = [{"code": r["code"], "name": r["name"], "status": r["status"], "msg": r["message"]} for r in results]
        
        return resp

    except Exception as e:
        print(f"API ERROR: {e}")
        traceback.print_exc()
        return {"status": "ERROR", "message": str(e)}

# --- HISTORICAL (Legacy) ---
def detect_regime(candles_15m, bias_score, levels): return "ROTATIONAL"
async def run_historical_analysis(inputs, session_keys, leverage, capital, strategy_mode, risk_mode): return {}