# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE v3.8 (FULL SUITE COMPLETE)
# ==============================================================================
# S0: Hold Fire (Discipline)
# S1: Breakout Acceptance (Long Trend)
# S2: Breakdown Acceptance (Short Trend)
# S3: Structural Stand-Down (Shield)
# S4: Mid-Band Fade (Rotation)
# S5: Range Extremes (Hard Edge Fade)
# S6: Value Rotation (Edge-to-Edge)
# S7: Trend Continuation (Pullback)
# S8: Failed Breakout (Trap)
# S9: Circuit Breaker (Risk Guard)
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
    if not candles: return []
    prices = [c.close for c in candles]
    ema = []
    multiplier = 2 / (period + 1)
    if len(prices) > 0: ema.append(prices[0])
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
        raw_pct = (exit - entry) / entry if direction == "LONG" else (entry - exit) / entry
        capital = risk_settings['value']
        leverage = risk_settings.get('leverage', 1.0)
        return round(capital * raw_pct * leverage, 2)

def _build_audit_packet(valid, code, grade, reason, stop, target, entry):
    risk = abs(entry - stop)
    reward = abs(entry - target)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    return {
        "valid": valid, "code": code, "grade": grade, "reason": reason,
        "stop_loss": round(stop, 2), "target": round(target, 2), "implied_rr": rr
    }

# ==========================================
# 2. STRATEGY LOGIC MODULES
# ==========================================

def run_s0_logic(levels, c15, c5, risk, regime):
    return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", "Capital preserved.", 0, 0, 0)}

# --- S1 (LONG) ---
def run_s1_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0); vah = levels.get("f24_vah", 0); poc = levels.get("f24_poc", 0)
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close > bo: streak += 1
        else: streak = 0
        if streak >= 2: direction = "LONG"; setup_time = c.timestamp; entry = c.close; break
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S1_A"; grade = "A"; reason = "Valid Directional Breakout."
    if regime != "DIRECTIONAL": valid = False; code = "INVALID_S1_REGIME"; reason = f"S1 Forbidden in {regime} regime."
    if abs(bo - vah) < (poc * 0.002): valid = False; code = "INVALID_S1_AMBIGUOUS"; reason = "Trigger overlaps Value High."
    if entry > bo * 1.003: valid = False; code = "INVALID_S1_CHASE"; reason = "Entry too far above trigger."
    grade = _check_5m_alignment(hist_5, "long")
    stop = bo * 0.998; target = levels.get("daily_resistance", entry * 1.04) 
    return _execute_simulation(c5, setup_time, entry, stop, target, "LONG", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S2 (SHORT) ---
def run_s2_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bd = levels.get("breakdown_trigger", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close < bd: streak += 1
        else: streak = 0
        if streak >= 2: direction = "SHORT"; setup_time = c.timestamp; entry = c.close; break
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S2_A"; grade = "A"; reason = "Valid Directional Breakdown."
    if regime != "DIRECTIONAL": valid = False; code = "INVALID_S2_REGIME"; reason = f"S2 Forbidden in {regime} regime."
    if abs(bd - val) < (poc * 0.002): valid = False; code = "INVALID_S2_AMBIGUOUS"; reason = "Trigger overlaps Value Low."
    if entry < bd * 0.997: valid = False; code = "INVALID_S2_CHASE"; reason = "Entry too far below trigger."
    grade = _check_5m_alignment(hist_5, "short")
    stop = bd * 1.002; target = levels.get("daily_support", entry * 0.96)
    return _execute_simulation(c5, setup_time, entry, stop, target, "SHORT", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S3 (SHIELD) ---
def run_s3_logic(levels, raw_c15, raw_c5, risk, regime):
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    reasons = []; is_messy = False; tol = poc * 0.002 
    if abs(bo - vah) < tol: is_messy = True; reasons.append("VAH overlaps Breakout Trigger")
    if abs(bd - val) < tol: is_messy = True; reasons.append("VAL overlaps Breakdown Trigger")
    if ((bo - bd) / poc) < 0.005: is_messy = True; reasons.append("Compressed Range")
    if regime == "ROTATIONAL": is_messy = True; reasons.append("Rotational Regime")
    if is_messy: return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", f"Stand Down Validated: {', '.join(reasons)}", 0, 0, 0)}
    else: return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": _build_audit_packet(False, "INVALID_S3_PASSIVE", "C", "Market was Clean. S3 was passive.", 0, 0, 0)}

# --- S4 (MID-BAND) ---
def run_s4_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        prev, curr = c15[i-1], c15[i]
        if prev.high > vah and curr.close < vah: direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        if prev.low < val and curr.close > val: direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    hist_15 = _slice_history(c15, setup_time, 120); hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S4_A"; reason = "Valid Rotation."
    if regime == "DIRECTIONAL": valid = False; code = "INVALID_S4_REGIME"; reason = "S4 Disabled in Directional Regime."
    if (direction == "SHORT" and _check_acceptance(hist_15, bo, "long")) or (direction == "LONG" and _check_acceptance(hist_15, bd, "short")): valid = False; code = "INVALID_ACCEPTED_OUTSIDE"; reason = "Price accepted outside trigger."
    grade = _check_5m_alignment(hist_5, direction.lower())
    stop = (max(vah, bo) + 25) if direction == "SHORT" else (min(val, bd) - 25); target = poc
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S5 (RANGE EXTREMES) ---
def run_s5_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    dr = levels.get("daily_resistance", 0); ds = levels.get("daily_support", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        curr = c15[i]
        if curr.high >= dr and curr.close < dr: direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        if curr.low <= ds and curr.close > ds: direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}
    valid = True; code = "VALID_S5_A"; grade = "A"; reason = "Valid Hard Edge Fade."
    if regime != "ROTATIONAL": valid = False; code = "INVALID_S5_TREND_DAY"; reason = f"S5 Forbidden in {regime} regime."
    if direction == "SHORT" and dr <= vah: valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Res inside Value."
    if direction == "LONG" and ds >= val: valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Sup inside Value."
    stop = (dr * 1.003) if direction == "SHORT" else (ds * 0.997); target = poc 
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S6 (VALUE ROTATION) ---
def run_s6_logic(levels, raw_c15, raw_c5, risk, regime):
    res = run_s4_logic(levels, raw_c15, raw_c5, risk, regime)
    if res["entry"] == 0: return res
    res["audit"]["code"] = res["audit"]["code"].replace("S4", "S6")
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0); bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    if regime != "ROTATIONAL": return _fail_s6(res, "INVALID_S6_NO_BALANCE", "S6 requires pure rotation.")
    pinned = (vah - val) * 0.1
    if abs(poc - vah) < pinned or abs(poc - val) < pinned: return _fail_s6(res, "INVALID_S6_POC_PINNED", "POC pinned to edge.")
    if abs(bo - vah) < (poc * 0.0015) or abs(bd - val) < (poc * 0.0015): return _fail_s6(res, "INVALID_S6_TRIGGER_OVERLAP", "Triggers overlap Value.")
    entry = res["entry"]; stop = res["audit"]["stop_loss"]; target = val if entry > poc else vah
    c5 = _to_candles(raw_c5)
    direction = "SHORT" if entry > target else "LONG"
    if direction == "SHORT" and not (target < entry < stop): return _fail_s6(res, "INVALID_S6_GEOMETRY", "Geometry Invalid.")
    if direction == "LONG" and not (stop < entry < target): return _fail_s6(res, "INVALID_S6_GEOMETRY", "Geometry Invalid.")
    return _execute_simulation(c5, 0, entry, stop, target, direction, risk, res["audit"]["valid"], res["audit"]["code"], "A", res["audit"]["reason"], exit_mode="FIXED")

def _fail_s6(res, code, reason):
    res["audit"]["valid"] = False; res["audit"]["code"] = code; res["audit"]["reason"] = reason; return res

# --- S7 (CONTINUATION) ---
def run_s7_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    ema_series = _calculate_ema(c5)
    direction = "NONE"; acceptance_time = 0
    streak = 0
    for c in c15:
        if c.close > bo: streak += 1
        else: streak = 0
        if streak >= 2: direction = "LONG"; acceptance_time = c.timestamp; break
    if direction == "NONE":
        streak = 0
        for c in c15:
            if c.close < bd: streak += 1
            else: streak = 0
            if streak >= 2: direction = "SHORT"; acceptance_time = c.timestamp; break
    if direction == "NONE": return {"pnl": 0, "status": "S7_PENDING", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S7_WAIT", "A", "No prior acceptance.", 0, 0, 0)}
    setup_time = 0; entry = 0; stop = 0; pullback = False
    for i in range(len(c5)):
        c = c5[i]; cur_ema = ema_series[i]
        if c.timestamp <= acceptance_time: continue
        if direction == "LONG":
            if not pullback: 
                if c.low <= cur_ema: pullback = True
            elif c.close > cur_ema:
                setup_time = c.timestamp; entry = c.close; stop = min([x.low for x in c5[i-3:i+1]]) - 10; break
        elif direction == "SHORT":
            if not pullback: 
                if c.high >= cur_ema: pullback = True
            elif c.close < cur_ema:
                setup_time = c.timestamp; entry = c.close; stop = max([x.high for x in c5[i-3:i+1]]) + 10; break
    if entry == 0: return {"pnl": 0, "status": "S7_PENDING", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S7_WAIT", "A", "Trend accepted, waiting for pullback.", 0, 0, 0)}
    valid = True; code = "VALID_S7_A"; grade = "A"; reason = "Valid Trend Continuation."
    if regime != "DIRECTIONAL": valid = False; code = "INVALID_S7_REGIME"; reason = "S7 requires Directional Regime."
    target = levels.get("daily_resistance", entry * 1.05) if direction == "LONG" else levels.get("daily_support", entry * 0.95)
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S8: FAILED BREAKOUT / TRAP ---
def run_s8_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    direction = "NONE"; setup_time = 0; entry = 0; stop = 0
    attempt_long = False; attempt_short = False
    trap_high = 0; trap_low = 999999
    for c in c15:
        if c.close > bo: attempt_long = True; trap_high = max(trap_high, c.high)
        if c.close < bd: attempt_short = True; trap_low = min(trap_low, c.low)
        if attempt_long and c.close < vah: direction = "SHORT"; setup_time = c.timestamp; entry = c.close; stop = trap_high + 25; break
        if attempt_short and c.close > val: direction = "LONG"; setup_time = c.timestamp; entry = c.close; stop = trap_low - 25; break
    if direction == "NONE": return {"pnl": 0, "status": "S8_PENDING", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S8_WAIT", "A", "No failed breakout detected.", 0, 0, 0)}
    valid = True; code = "VALID_S8_A"; grade = "A"; reason = "Valid Trap Detected."
    if direction == "SHORT" and entry < poc: valid = False; code = "INVALID_S8_LATE"; reason = "Trap entry below POC (Too late)."
    if direction == "LONG" and entry > poc: valid = False; code = "INVALID_S8_LATE"; reason = "Trap entry above POC (Too late)."
    target = poc 
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S9: CIRCUIT BREAKER ---
def run_s9_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    value_width = vah - val
    if value_width == 0: return {"pnl":0, "status":"S9_PENDING", "entry":0, "exit":0, "audit":{}}
    is_extreme = False
    for c in c15:
        if abs(c.close - poc) > (value_width * 3.5): is_extreme = True; break
    if is_extreme: return {"pnl": 0, "status": "S9_ACTIVE", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "S9_CIRCUIT_BREAKER", "A", "Extreme Displacement. Halted.", 0, 0, 0)}
    else: return {"pnl": 0, "status": "S9_PENDING", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "S9_MONITORING", "A", "Normal operations.", 0, 0, 0)}

# ==========================================
# 3. SHARED EXECUTOR
# ==========================================

def _execute_simulation(candles_5m: List[Candle], setup_time: int, entry: float, stop: float, target: float, 
                        direction: str, risk: Dict, valid: bool, code: str, grade: str, reason: str, exit_mode: str = "FIXED"):
    
    exit_price = entry; status = "OPEN"
    ema_series = _calculate_ema(candles_5m) if exit_mode == "TRAILING" else []
    
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        if c.timestamp <= setup_time: continue
        
        if direction == "LONG":
            if c.low <= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.high >= target: exit_price = target; status = "TAKE_PROFIT"; break
        else:
            if c.high >= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.low <= target: exit_price = target; status = "TAKE_PROFIT"; break
            
        if exit_mode == "TRAILING":
            current_ema = ema_series[i]
            if direction == "LONG" and c.close < current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            if direction == "SHORT" and c.close > current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            
    pnl = _calculate_pnl_dynamic(entry, exit_price, stop, direction, risk)
    audit = _build_audit_packet(valid, code, grade, reason, stop, target, entry)
    
    return { "pnl": pnl, "status": status, "entry": entry, "exit": exit_price, "audit": audit }