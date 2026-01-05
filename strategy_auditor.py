# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE v3.2 (TREND EXITS)
# ==============================================================================
# Updates:
# - Added 5m 21 EMA Calculation
# - S1/S2: Switched to "TRAILING" exit mode (Exit on 21 EMA break)
# - S4/S5: Kept "FIXED" exit mode (Exit at Target)
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

def _calculate_ema(candles: List[Candle], period: int = 21) -> List[float]:
    """Calculates EMA array matching the candle list length."""
    if not candles: return []
    prices = [c.close for c in candles]
    ema = []
    multiplier = 2 / (period + 1)
    
    # Simple SMA for first value
    if len(prices) > 0:
        ema.append(prices[0])
    
    for i in range(1, len(prices)):
        val = (prices[i] - ema[-1]) * multiplier + ema[-1]
        ema.append(val)
    return ema

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
    
    if risk_settings['mode'] == 'fixed_risk':
        risk_dollars = risk_settings['value']
        dist_to_stop = abs(entry - stop)
        if dist_to_stop == 0: return 0.0
        pos_size = risk_dollars / dist_to_stop
        price_diff = (exit - entry) if direction == "LONG" else (entry - exit)
        return round(pos_size * price_diff, 2)
    else:
        # Fixed Margin Calculation
        raw_pct = (exit - entry) / entry if direction == "LONG" else (entry - exit) / entry
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
        "target": round(target, 2), # Reference target only for S1/S2
        "implied_rr": rr
    }

# ==========================================
# 2. STRATEGY LOGIC MODULES
# ==========================================

# --- S0: HOLD FIRE ---
def run_s0_logic(levels, c15, c5, risk, regime):
    return {
        "pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0,
        "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", "Capital preserved.", 0, 0, 0)
    }

# --- S1: BREAKOUT ACCEPTANCE (LONG) - TRAILING EXIT ---
def run_s1_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0)
    vah = levels.get("f24_vah", 0)
    poc = levels.get("f24_poc", 0)
    
    # 1. SCANNER
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close > bo: streak += 1
        else: streak = 0
        if streak >= 2:
            direction = "LONG"; setup_time = c.timestamp; entry = c.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # 2. AUDITOR
    hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S1_A"; grade = "A"; reason = "Valid Directional Breakout."
    
    if regime != "DIRECTIONAL":
        valid = False; code = "INVALID_S1_REGIME"; reason = f"S1 Forbidden in {regime} regime."
        
    overlap_tolerance = poc * 0.002
    if abs(bo - vah) < overlap_tolerance:
        valid = False; code = "INVALID_S1_AMBIGUOUS"; reason = "Trigger overlaps Value High."

    max_chase_pct = 0.003
    if entry > bo * (1 + max_chase_pct):
        valid = False; code = "INVALID_S1_CHASE"; reason = "Entry too far above trigger (FOMO)."
    
    grade = _check_5m_alignment(hist_5, "long")
    
    stop = bo * 0.998 
    # Target is REFERENCE ONLY for S1 audit (implied R:R), not exit.
    target = levels.get("daily_resistance", entry * 1.04) 
    
    # 3. EXECUTOR (Trailing Mode)
    return _execute_simulation(c5, setup_time, entry, stop, target, "LONG", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S2: BREAKDOWN ACCEPTANCE (SHORT) - TRAILING EXIT ---
def run_s2_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bd = levels.get("breakdown_trigger", 0)
    val = levels.get("f24_val", 0)
    poc = levels.get("f24_poc", 0)
    
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close < bd: streak += 1
        else: streak = 0
        if streak >= 2:
            direction = "SHORT"; setup_time = c.timestamp; entry = c.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S2_A"; grade = "A"; reason = "Valid Directional Breakdown."
    
    if regime != "DIRECTIONAL":
        valid = False; code = "INVALID_S2_REGIME"; reason = f"S2 Forbidden in {regime} regime."
        
    overlap_tolerance = poc * 0.002
    if abs(bd - val) < overlap_tolerance:
        valid = False; code = "INVALID_S2_AMBIGUOUS"; reason = "Trigger overlaps Value Low."

    max_chase_pct = 0.003
    if entry < bd * (1 - max_chase_pct):
        valid = False; code = "INVALID_S2_CHASE"; reason = "Entry too far below trigger (FOMO)."

    grade = _check_5m_alignment(hist_5, "short")

    stop = bd * 1.002
    target = levels.get("daily_support", entry * 0.96)
    
    return _execute_simulation(c5, setup_time, entry, stop, target, "SHORT", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S4: MID-BAND FADE (FIXED TARGET) ---
def run_s4_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        prev, curr = c15[i-1], c15[i]
        if prev.high > vah and curr.close < vah:
            direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        if prev.low < val and curr.close > val:
            direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
    
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

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
    
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S5: RANGE EXTREMES (FIXED TARGET) ---
def run_s5_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    dr = levels.get("daily_resistance", 0); ds = levels.get("daily_support", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        curr = c15[i]
        if curr.high >= dr and curr.close < dr:
            direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        if curr.low <= ds and curr.close > ds:
            direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    valid = True; code = "VALID_S5_A"; grade = "A"; reason = "Valid Hard Edge Fade."
    if direction == "SHORT" and dr < vah:
        valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Res inside Value."
    if direction == "LONG" and ds > val:
        valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Sup inside Value."
    if regime == "DIRECTIONAL":
        valid = False; code = "INVALID_S5_TREND_DAY"; reason = "Don't fade hard edges on Trend Days."

    stop = (dr * 1.003) if direction == "SHORT" else (ds * 0.997)
    target = poc
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S6: VALUE ROTATION (FIXED TARGET) ---
def run_s6_logic(levels, raw_c15, raw_c5, risk, regime):
    res = run_s4_logic(levels, raw_c15, raw_c5, risk, regime)
    if res["entry"] == 0: return res
    res["audit"]["code"] = res["audit"]["code"].replace("S4", "S6")
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0)
    
    if regime != "ROTATIONAL":
        res["audit"]["valid"] = False; res["audit"]["code"] = "INVALID_S6_NO_BALANCE"; res["audit"]["reason"] = "S6 requires pure rotation."
        
    entry = res["entry"]; stop = res["audit"]["stop_loss"]
    target = val if entry > levels.get("f24_poc") else vah
    c5 = _to_candles(raw_c5)
    return _execute_simulation(c5, 0, entry, stop, target, "SHORT" if entry > target else "LONG", risk, res["audit"]["valid"], res["audit"]["code"], "A", res["audit"]["reason"], exit_mode="FIXED")

# --- S7, S8, S9 ---
def run_s7_logic(levels, raw_c15, raw_c5, risk, regime): return {"pnl":0, "status":"S7_PENDING", "entry":0, "exit":0, "audit":{}}
def run_s8_logic(levels, raw_c15, raw_c5, risk, regime): return {"pnl":0, "status":"S8_PENDING", "entry":0, "exit":0, "audit":{}}
def run_s9_logic(levels, raw_c15, raw_c5, risk, regime): return {"pnl":0, "status":"S9_PENDING", "entry":0, "exit":0, "audit":{}}

# ==========================================
# 3. SHARED EXECUTOR
# ==========================================

def _execute_simulation(candles_5m: List[Candle], setup_time: int, entry: float, stop: float, target: float, 
                        direction: str, risk: Dict, valid: bool, code: str, grade: str, reason: str, exit_mode: str = "FIXED"):
    
    exit_price = entry; status = "OPEN"
    
    # Calculate EMA Series for Trailing Logic
    ema_series = _calculate_ema(candles_5m) if exit_mode == "TRAILING" else []
    
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        if c.timestamp <= setup_time: continue
        
        # 1. HARD STOP (Always active)
        if direction == "LONG":
            if c.low <= stop: exit_price = stop; status = "STOPPED_OUT"; break
        else:
            if c.high >= stop: exit_price = stop; status = "STOPPED_OUT"; break
            
        # 2. EXIT LOGIC
        if exit_mode == "FIXED":
            # Target Hit Logic
            if direction == "LONG":
                if c.high >= target: exit_price = target; status = "TAKE_PROFIT"; break
            else:
                if c.low <= target: exit_price = target; status = "TAKE_PROFIT"; break
                
        elif exit_mode == "TRAILING":
            # 5m Trend Structure Exit (21 EMA)
            # Must wait for CLOSE to confirm break
            current_ema = ema_series[i]
            if direction == "LONG":
                if c.close < current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            else:
                if c.close > current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            
    pnl = _calculate_pnl_dynamic(entry, exit_price, stop, direction, risk)
    audit = _build_audit_packet(valid, code, grade, reason, stop, target, entry)
    
    return {
        "pnl": pnl, "status": status, "entry": entry, "exit": exit_price, "audit": audit
    }