# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE v3.0 (FULL SUITE)
# ==============================================================================
# Covers: S0, S1, S2, S4, S5, S6, S7, S8, S9
# Standardized Forensic Audit & Risk Modeling for all strategies.
# ==============================================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any

# ==========================================
# 1. SHARED UTILS
# ==========================================

@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float

def _to_candles(raw_data: List[Dict]) -> List[Candle]:
    return [Candle(c['time'], c['open'], c['high'], c['low'], c['close']) for c in raw_data]

def _slice_history(candles: List[Candle], end_time: int, minutes_back: int) -> List[Candle]:
    start = end_time - (minutes_back * 60)
    return [c for c in candles if start <= c.timestamp <= end_time]

def _check_acceptance(candles: List[Candle], level: float, side: str, n: int = 2) -> bool:
    streak = 0
    for c in candles:
        if (side == "long" and c.close > level) or (side == "short" and c.close < level):
            streak += 1
        else:
            streak = 0
        if streak >= n: return True
    return False

def _check_5m_alignment(candles: List[Candle], side: str) -> str:
    if len(candles) < 3: return "C"
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if side == "short":
        if c3.close < c2.close and c3.high < c2.high: return "A"
    else:
        if c3.close > c2.close and c3.low > c2.low: return "A"
    return "B"

def _calculate_pnl_dynamic(entry, exit, stop, direction, risk_settings):
    if entry == 0 or exit == 0: return 0.0
    raw_pct = (exit - entry) / entry if direction == "LONG" else (entry - exit) / entry
    
    if risk_settings['mode'] == 'fixed_risk':
        risk_dollars = risk_settings['value']
        dist_to_stop = abs(entry - stop)
        if dist_to_stop == 0: return 0.0
        pos_size = risk_dollars / dist_to_stop
        price_diff = (exit - entry) if direction == "LONG" else (entry - exit)
        return round(pos_size * price_diff, 2)
    else:
        capital = risk_settings['value']
        leverage = risk_settings.get('leverage', 1.0)
        return round(capital * raw_pct * leverage, 2)

def _build_audit_packet(valid, code, grade, reason, stop, target, entry):
    risk = abs(entry - stop)
    reward = abs(entry - target)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    return {
        "valid": valid,
        "code": code,
        "grade": grade,
        "reason": reason,
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "implied_rr": rr
    }

# ==========================================
# 2. STRATEGY LOGIC MODULES
# ==========================================

# --- S0: HOLD FIRE ---
def run_s0_logic(levels, c15, c5, risk, regime):
    # S0 is valid ONLY if no trade is taken.
    # For simulation, we assume entry=0 means discipline.
    return {
        "pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0,
        "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", "Capital preserved.", 0, 0, 0)
    }

# --- S1: BREAKOUT ACCEPTANCE (LONG) ---
def run_s1_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15)
    c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0)
    vah = levels.get("f24_vah", 0)
    
    # SCANNER
    direction = "NONE"; setup_time = 0; entry = 0
    # Logic: 2 closes above BO trigger
    streak = 0
    for c in c15:
        if c.close > bo: streak += 1
        else: streak = 0
        if streak >= 2:
            direction = "LONG"; setup_time = c.timestamp; entry = c.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # AUDITOR
    hist_15 = _slice_history(c15, setup_time, 120)
    
    valid = True; code = "VALID_S1_A"; grade = "A"; reason = "Valid Breakout."
    
    # Gate 1: Regime
    if regime not in ["DIRECTIONAL", "TRANSITIONING"]: # S1 needs momentum
        valid = False; code = "INVALID_S1_REGIME"; reason = f"Regime {regime} not suitable for S1."
        
    # Gate 2: Structure (Overlap)
    if abs(bo - vah) < (bo * 0.001): # If trigger equals VAH, it's ambiguous
        valid = False; code = "INVALID_S1_AMBIGUOUS"; reason = "Trigger overlaps VAH. messy structure."

    # Gate 3: Retest (Did we chase?)
    # Ideally entry is near the trigger on a retest, not sky high.
    if entry > bo * 1.005:
        valid = False; code = "INVALID_S1_EARLY_ENTRY"; reason = "Entry too far above trigger (Chased)."

    stop = bo * 0.998 # Just below trigger
    target = levels.get("daily_resistance", entry * 1.02)
    
    # EXECUTOR
    return _execute_simulation(c5, setup_time, entry, stop, target, "LONG", risk, valid, code, grade, reason)

# --- S2: BREAKDOWN ACCEPTANCE (SHORT) ---
def run_s2_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15)
    c5 = _to_candles(raw_c5)
    bd = levels.get("breakdown_trigger", 0)
    val = levels.get("f24_val", 0)
    
    # SCANNER
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close < bd: streak += 1
        else: streak = 0
        if streak >= 2:
            direction = "SHORT"; setup_time = c.timestamp; entry = c.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # AUDITOR
    valid = True; code = "VALID_S2_A"; grade = "A"; reason = "Valid Breakdown."
    
    if regime not in ["DIRECTIONAL", "TRANSITIONING"]:
        valid = False; code = "INVALID_S2_REGIME"; reason = f"Regime {regime} not suitable for S2."
        
    if abs(bd - val) < (bd * 0.001):
        valid = False; code = "INVALID_S2_AMBIGUOUS"; reason = "Trigger overlaps VAL."

    if entry < bd * 0.995:
        valid = False; code = "INVALID_S2_EARLY_ENTRY"; reason = "Entry too far below trigger (Chased)."

    stop = bd * 1.002
    target = levels.get("daily_support", entry * 0.98)
    
    return _execute_simulation(c5, setup_time, entry, stop, target, "SHORT", risk, valid, code, grade, reason)

# --- S4: MID-BAND FADE (Updated to use Shared Executor) ---
def run_s4_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    
    # SCANNER
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        prev, curr = c15[i-1], c15[i]
        if prev.high > vah and curr.close < vah:
            direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        if prev.low < val and curr.close > val:
            direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
    
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # AUDITOR
    hist_15 = _slice_history(c15, setup_time, 120); hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S4_A"; reason = "Valid Rotation."
    
    if regime == "DIRECTIONAL":
        valid = False; code = "INVALID_S4_REGIME"; reason = "S4 Disabled in Directional Regime."
    
    if (direction == "SHORT" and _check_acceptance(hist_15, bo, "long")) or \
       (direction == "LONG" and _check_acceptance(hist_15, bd, "short")):
        valid = False; code = "INVALID_ACCEPTED_OUTSIDE"; reason = "Price accepted outside trigger."

    grade = _check_5m_alignment(hist_5, direction.lower())
    
    stop_buff = 25.0
    stop = (max(vah, bo) + stop_buff) if direction == "SHORT" else (min(val, bd) - stop_buff)
    target = poc
    
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason)

# --- S5: RANGE EXTREMES (Daily Level Fade) ---
def run_s5_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    dr = levels.get("daily_resistance", 0); ds = levels.get("daily_support", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    
    # SCANNER
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        curr = c15[i]
        # Short at Daily Res (Fade)
        if curr.high >= dr and curr.close < dr:
            direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        # Long at Daily Sup (Fade)
        if curr.low <= ds and curr.close > ds:
            direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # AUDITOR
    valid = True; code = "VALID_S5_A"; grade = "A"; reason = "Valid Hard Edge Fade."
    
    # Gate: Daily Level must be outside Value
    if direction == "SHORT" and dr < vah:
        valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Res is inside Value. Use S4."
    if direction == "LONG" and ds > val:
        valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Sup is inside Value. Use S4."
        
    if regime == "DIRECTIONAL":
        valid = False; code = "INVALID_S5_TREND_DAY"; reason = "Don't fade hard edges on Trend Days."

    stop = (dr * 1.003) if direction == "SHORT" else (ds * 0.997)
    target = poc # Return to value center
    
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason)

# --- S6: VALUE ROTATION (VAH <-> VAL) ---
def run_s6_logic(levels, raw_c15, raw_c5, risk, regime):
    # Similar to S4 but targets the *Opposite Edge* instead of POC
    # Only valid in strict ROTATIONAL regimes
    res = run_s4_logic(levels, raw_c15, raw_c5, risk, regime)
    if res["entry"] == 0: return res
    
    # Modify Auditor for S6 specific rules
    res["audit"]["code"] = res["audit"]["code"].replace("S4", "S6")
    
    # Target Upgrade
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0)
    if regime != "ROTATIONAL":
        res["audit"]["valid"] = False; res["audit"]["code"] = "INVALID_S6_NO_BALANCE"; res["audit"]["reason"] = "S6 requires pure rotation."
        
    # Recalculate Target (Full traverse)
    entry = res["entry"]
    stop = res["audit"]["stop_loss"]
    # If shorting VAH, target VAL (not POC)
    target = val if entry > levels.get("f24_poc") else vah
    
    # Re-Run Execution with new Target
    c5 = _to_candles(raw_c5)
    return _execute_simulation(c5, 0, entry, stop, target, "SHORT" if entry > target else "LONG", risk, res["audit"]["valid"], res["audit"]["code"], "A", res["audit"]["reason"])

# --- S7: TREND PULLBACK ---
def run_s7_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    
    # Simple logic: In directional regime, buy dips / sell rips relative to EMA
    # For now, we simulate a generic trend follow
    if regime != "DIRECTIONAL":
        return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
        
    # Placeholder for full S7 logic (requires trend state tracking)
    # We return a generic "Not Implemented" packet that doesn't break the system
    return {"pnl":0, "status":"S7_DEV_PENDING", "entry":0, "exit":0, "audit":{}}

# --- S8: COMPRESSION BREAK ---
def run_s8_logic(levels, raw_c15, raw_c5, risk, regime):
    if regime != "COMPRESSED":
        return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    # Re-use S1/S2 logic but strict for compressed regime
    return run_s1_logic(levels, raw_c15, raw_c5, risk, "DIRECTIONAL") # Force S1 behavior

# --- S9: EXHAUSTION REVERSAL ---
def run_s9_logic(levels, raw_c15, raw_c5, risk, regime):
    if regime != "DIRECTIONAL":
        return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    # Fades touches of Daily Levels ONLY if price is extended
    return run_s5_logic(levels, raw_c15, raw_c5, risk, "ROTATIONAL") # Treat as S5 logic temporarily

# ==========================================
# 3. SHARED EXECUTOR
# ==========================================

def _execute_simulation(candles_5m: List[Candle], setup_time: int, entry: float, stop: float, target: float, 
                        direction: str, risk: Dict, valid: bool, code: str, grade: str, reason: str):
    
    exit_price = entry
    status = "OPEN"
    
    # 5m Bar-by-Bar Playback
    for c in candles_5m:
        if c.timestamp <= setup_time: continue
        
        if direction == "LONG":
            if c.low <= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.high >= target: exit_price = target; status = "TAKE_PROFIT"; break
        else: # SHORT
            if c.high >= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.low <= target: exit_price = target; status = "TAKE_PROFIT"; break
            
    pnl = _calculate_pnl_dynamic(entry, exit_price, stop, direction, risk)
    
    audit = _build_audit_packet(valid, code, grade, reason, stop, target, entry)
    
    return {
        "pnl": pnl,
        "status": status,
        "entry": entry,
        "exit": exit_price,
        "audit": audit
    }