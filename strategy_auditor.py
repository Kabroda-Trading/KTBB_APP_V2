# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE v3.5 (S6 HARDENED)
# ==============================================================================
# Updates:
# - S6: Added "Balanced Value" Gate (POC Pinned check)
# - S6: Added "Trigger Overlap" Gate
# - S6: Added Data Integrity Invariant Checks (Stop/Entry/Target Geometry)
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
    return {
        "pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0,
        "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", "Capital preserved.", 0, 0, 0)
    }

# --- S1: BREAKOUT ACCEPTANCE (LONG) ---
def run_s1_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bo = levels.get("breakout_trigger", 0); vah = levels.get("f24_vah", 0); poc = levels.get("f24_poc", 0)
    
    direction = "NONE"; setup_time = 0; entry = 0
    streak = 0
    for c in c15:
        if c.close > bo: streak += 1
        else: streak = 0
        if streak >= 2:
            direction = "LONG"; setup_time = c.timestamp; entry = c.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    hist_5 = _slice_history(c5, setup_time, 30)
    valid = True; code = "VALID_S1_A"; grade = "A"; reason = "Valid Directional Breakout."
    
    if regime != "DIRECTIONAL": valid = False; code = "INVALID_S1_REGIME"; reason = f"S1 Forbidden in {regime} regime."
    if abs(bo - vah) < (poc * 0.002): valid = False; code = "INVALID_S1_AMBIGUOUS"; reason = "Trigger overlaps Value High."
    if entry > bo * 1.003: valid = False; code = "INVALID_S1_CHASE"; reason = "Entry too far above trigger."
    
    grade = _check_5m_alignment(hist_5, "long")
    stop = bo * 0.998 
    target = levels.get("daily_resistance", entry * 1.04) 
    
    return _execute_simulation(c5, setup_time, entry, stop, target, "LONG", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S2: BREAKDOWN ACCEPTANCE (SHORT) ---
def run_s2_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    bd = levels.get("breakdown_trigger", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    
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
    
    if regime != "DIRECTIONAL": valid = False; code = "INVALID_S2_REGIME"; reason = f"S2 Forbidden in {regime} regime."
    if abs(bd - val) < (poc * 0.002): valid = False; code = "INVALID_S2_AMBIGUOUS"; reason = "Trigger overlaps Value Low."
    if entry < bd * 0.997: valid = False; code = "INVALID_S2_CHASE"; reason = "Entry too far below trigger."

    grade = _check_5m_alignment(hist_5, "short")
    stop = bd * 1.002
    target = levels.get("daily_support", entry * 0.96)
    
    return _execute_simulation(c5, setup_time, entry, stop, target, "SHORT", risk, valid, code, grade, reason, exit_mode="TRAILING")

# --- S3: STRUCTURAL STAND-DOWN ---
def run_s3_logic(levels, raw_c15, raw_c5, risk, regime):
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    
    reasons = []
    is_messy = False
    tol = poc * 0.002 
    if abs(bo - vah) < tol: is_messy = True; reasons.append("VAH overlaps Breakout Trigger")
    if abs(bd - val) < tol: is_messy = True; reasons.append("VAL overlaps Breakdown Trigger")
    rng_pct = (bo - bd) / poc
    if rng_pct < 0.005: is_messy = True; reasons.append("Compressed Range (<0.5%)")
    if regime == "ROTATIONAL": is_messy = True; reasons.append("Rotational Regime")
    
    if is_messy:
        return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": _build_audit_packet(True, "VALID_S0_DISCIPLINE", "A", f"Stand Down Validated: {', '.join(reasons)}", 0, 0, 0)}
    else:
        return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": _build_audit_packet(False, "INVALID_S3_PASSIVE", "C", "Market was Directional/Clean. S3 was too passive.", 0, 0, 0)}

# --- S4: MID-BAND FADE ---
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
    
    if regime == "DIRECTIONAL": valid = False; code = "INVALID_S4_REGIME"; reason = "S4 Disabled in Directional Regime."
    if (direction == "SHORT" and _check_acceptance(hist_15, bo, "long")) or \
       (direction == "LONG" and _check_acceptance(hist_15, bd, "short")):
        valid = False; code = "INVALID_ACCEPTED_OUTSIDE"; reason = "Price accepted outside trigger."

    grade = _check_5m_alignment(hist_5, direction.lower())
    stop = (max(vah, bo) + 25) if direction == "SHORT" else (min(val, bd) - 25)
    target = poc
    
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S5: RANGE EXTREMES ---
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
    if regime != "ROTATIONAL": valid = False; code = "INVALID_S5_TREND_DAY"; reason = f"S5 Forbidden in {regime} regime."
    if direction == "SHORT" and dr <= vah: valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Res is inside/at Value High."
    if direction == "LONG" and ds >= val: valid = False; code = "INVALID_S5_INSIDE_VALUE"; reason = "Daily Sup is inside/at Value Low."

    stop = (dr * 1.003) if direction == "SHORT" else (ds * 0.997)
    target = poc 
    return _execute_simulation(c5, setup_time, entry, stop, target, direction, risk, valid, code, grade, reason, exit_mode="FIXED")

# --- S6: VALUE ROTATION (HARDENED) ---
def run_s6_logic(levels, raw_c15, raw_c5, risk, regime):
    c15 = _to_candles(raw_c15); c5 = _to_candles(raw_c5)
    vah = levels.get("f24_vah", 0); val = levels.get("f24_val", 0); poc = levels.get("f24_poc", 0)
    bo = levels.get("breakout_trigger", 0); bd = levels.get("breakdown_trigger", 0)
    
    # 1. SCANNER (Edge Rejection)
    direction = "NONE"; setup_time = 0; entry = 0
    for i in range(1, len(c15)):
        prev, curr = c15[i-1], c15[i]
        # Short: Poke above VAH, Close inside
        if prev.high > vah and curr.close < vah:
            direction = "SHORT"; setup_time = curr.timestamp; entry = curr.close; break
        # Long: Poke below VAL, Close inside
        if prev.low < val and curr.close > val:
            direction = "LONG"; setup_time = curr.timestamp; entry = curr.close; break
            
    if direction == "NONE": return {"pnl":0, "status":"NO_SETUP", "entry":0, "exit":0, "audit":{}}

    # 2. AUDITOR (Hardened)
    hist_5 = _slice_history(c5, setup_time, 30)
    audit = {"valid": False, "code": "UNKNOWN", "grade": "C", "reason": "", "stop_loss": 0.0, "target": 0.0, "implied_rr": 0.0}
    
    def _finalize(valid, code, grade, reason):
        audit["valid"] = valid; audit["code"] = code; audit["grade"] = grade; audit["reason"] = reason
        
        # STOP / TARGET LOGIC
        stop_buff = 25.0
        if direction == "SHORT":
            stop_anchor = max(vah, bo)
            audit["stop_loss"] = stop_anchor + stop_buff
            audit["target"] = val # Full Rotation
        else:
            stop_anchor = min(val, bd)
            audit["stop_loss"] = stop_anchor - stop_buff
            audit["target"] = vah # Full Rotation
            
        # DATA INTEGRITY INVARIANTS
        if direction == "SHORT" and not (audit["target"] < entry < audit["stop_loss"]):
             audit["valid"] = False; audit["code"] = "INVALID_PACKET_INVARIANT"; audit["reason"] = "Stop/Target Geometry Invalid."
        if direction == "LONG" and not (audit["stop_loss"] < entry < audit["target"]):
             audit["valid"] = False; audit["code"] = "INVALID_PACKET_INVARIANT"; audit["reason"] = "Stop/Target Geometry Invalid."
             
        # RR Calculation
        risk_amt = abs(entry - audit["stop_loss"])
        reward_amt = abs(entry - audit["target"])
        audit["implied_rr"] = round(reward_amt / risk_amt, 2) if risk_amt > 0 else 0.0
        
        return _execute_simulation(c5, setup_time, entry, audit["stop_loss"], audit["target"], direction, risk, audit["valid"], audit["code"], audit["grade"], audit["reason"], exit_mode="FIXED")

    # GATE 1: REGIME
    if regime != "ROTATIONAL":
        return _finalize(False, "INVALID_S6_NO_BALANCE", "C", f"S6 requires Rotational regime (was {regime}).")

    # GATE 2: BALANCED VALUE (POC Pinned?)
    pinned_tol = (vah - val) * 0.1 # 10% tolerance
    if abs(poc - vah) < pinned_tol or abs(poc - val) < pinned_tol:
        return _finalize(False, "INVALID_S6_POC_PINNED", "C", "POC pinned to edge. Value not balanced.")

    # GATE 3: TRIGGER OVERLAP
    overlap_tol = poc * 0.0015
    if abs(bo - vah) < overlap_tol or abs(bd - val) < overlap_tol:
        return _finalize(False, "INVALID_S6_TRIGGER_OVERLAP", "C", "Triggers overlap Value. Structural mess.")

    # GATE 4: ENTRY LOCATION
    edge_tol = (vah - val) * 0.15 # 15% tolerance
    if direction == "SHORT" and entry < (vah - edge_tol):
        return _finalize(False, "INVALID_S6_LOCATION_MID", "C", "Entry too deep inside value.")
    if direction == "LONG" and entry > (val + edge_tol):
        return _finalize(False, "INVALID_S6_LOCATION_MID", "C", "Entry too deep inside value.")

    # PASS
    grade = _check_5m_alignment(hist_5, direction.lower())
    return _finalize(True, f"VALID_S6_A" if grade=="A" else f"VALID_S6_GRADE_{grade}", grade, "Valid Value Rotation.")

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
    ema_series = _calculate_ema(candles_5m) if exit_mode == "TRAILING" else []
    
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        if c.timestamp <= setup_time: continue
        
        # 1. HARD STOP
        if direction == "LONG":
            if c.low <= stop: exit_price = stop; status = "STOPPED_OUT"; break
        else:
            if c.high >= stop: exit_price = stop; status = "STOPPED_OUT"; break
            
        # 2. EXIT LOGIC
        if exit_mode == "FIXED":
            if direction == "LONG":
                if c.high >= target: exit_price = target; status = "TAKE_PROFIT"; break
            else:
                if c.low <= target: exit_price = target; status = "TAKE_PROFIT"; break
                
        elif exit_mode == "TRAILING":
            current_ema = ema_series[i]
            if direction == "LONG" and c.close < current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            if direction == "SHORT" and c.close > current_ema: exit_price = c.close; status = "TRAIL_EXIT"; break
            
    pnl = _calculate_pnl_dynamic(entry, exit_price, stop, direction, risk)
    audit = _build_audit_packet(valid, code, grade, reason, stop, target, entry)
    
    return {
        "pnl": pnl, "status": status, "entry": entry, "exit": exit_price, "audit": audit
    }