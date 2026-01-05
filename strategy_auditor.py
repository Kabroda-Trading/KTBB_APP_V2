# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE v2.0 (S4 HARDENED)
# ==============================================================================
# Updates:
# - Added Regime Gating (Disable S4 in Directional)
# - Added Structural Gating (Disable S4 if Triggers Overlap)
# - Added "Implied R" calculation for all trades
# - New Classification Buckets (VALID_S4_A, INVALID_S4_REGIME, etc)
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

# ==========================================
# 2. STRATEGY S4: MID-BAND FADE (v2.0)
# ==========================================

def run_s4_logic(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], 
                 leverage: float, capital: float, market_regime: str) -> Dict[str, Any]:
    
    c15 = _to_candles(candles_15m)
    c5  = _to_candles(candles_5m)
    
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    poc = levels.get("f24_poc", 0)
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    
    # --- A. SCANNER (Find the Setup) ---
    direction = "NONE"
    setup_time = 0
    entry_price = 0.0
    
    for i in range(1, len(c15)):
        prev, curr = c15[i-1], c15[i]
        # Short: Poke above VAH, Close inside
        if prev.high > vah and curr.close < vah:
            direction = "SHORT"; setup_time = curr.timestamp; entry_price = curr.close; break
        # Long: Poke below VAL, Close inside
        if prev.low < val and curr.close > val:
            direction = "LONG"; setup_time = curr.timestamp; entry_price = curr.close; break
            
    if direction == "NONE":
        return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0, "audit": {"valid": False, "reason": "No edge rejection found"}}

    # --- B. AUDITOR (Forensic Check) ---
    hist_15 = _slice_history(c15, setup_time, 120)
    hist_5  = _slice_history(c5, setup_time, 30)
    
    audit = {
        "valid": False, "code": "UNKNOWN", "reason": "", 
        "quality": 0, "stop_loss": 0.0, "target": poc, "implied_rr": 0.0
    }
    
    # 1. REGIME GATE (Hard Stop)
    # S4 is Balance-Only. Directional kills it.
    regime_fail = False
    if market_regime == "DIRECTIONAL":
        audit["code"] = "INVALID_S4_REGIME"
        audit["reason"] = "Market is DIRECTIONAL. S4 (Balance) Disabled."
        regime_fail = True
    
    # 2. STRUCTURAL GATE (Ambiguity)
    # If VAH is basically the Breakout Trigger, there is no 'fade zone'.
    overlap_fail = False
    overlap_tol = poc * 0.0015 # 0.15% tolerance
    if not regime_fail:
        if direction == "SHORT" and abs(vah - bo) < overlap_tol:
            audit["code"] = "INVALID_S4_STRUCTURE"
            audit["reason"] = "VAH overlaps Breakout Trigger. No fade zone."
            overlap_fail = True
        elif direction == "LONG" and abs(val - bd) < overlap_tol:
            audit["code"] = "INVALID_S4_STRUCTURE"
            audit["reason"] = "VAL overlaps Breakdown Trigger. No fade zone."
            overlap_fail = True

    # 3. ACCEPTANCE CHECK
    accept_fail = False
    if not regime_fail and not overlap_fail:
        if direction == "SHORT" and _check_acceptance(hist_15, bo, "long"):
            audit["code"] = "INVALID_ACCEPTED_BREAKOUT"; accept_fail = True
        elif direction == "LONG" and _check_acceptance(hist_15, bd, "short"):
            audit["code"] = "INVALID_ACCEPTED_BREAKDOWN"; accept_fail = True

    # 4. LOCATION CHECK
    loc_fail = False
    tolerance = poc * 0.002
    if not (regime_fail or overlap_fail or accept_fail):
        if direction == "SHORT":
            hard_ceiling = max(vah, bo)
            if entry_price > hard_ceiling + tolerance: 
                audit["code"] = "INVALID_CHASE_HIGH"; loc_fail = True
            elif entry_price < (vah - tolerance):
                audit["code"] = "INVALID_LOCATION_MID"; loc_fail = True
        else:
            hard_floor = min(val, bd)
            if entry_price < hard_floor - tolerance:
                audit["code"] = "INVALID_CHASE_LOW"; loc_fail = True
            elif entry_price > (val + tolerance):
                audit["code"] = "INVALID_LOCATION_MID"; loc_fail = True

    # 5. RISK CALCULATION (Run for ALL trades, even invalid ones)
    stop_buff = 25.0
    if direction == "SHORT":
        audit["stop_loss"] = max(vah, bo) + stop_buff
    else:
        audit["stop_loss"] = min(val, bd) - stop_buff
        
    risk = abs(entry_price - audit["stop_loss"])
    reward = abs(entry_price - audit["target"])
    audit["implied_rr"] = round(reward / risk, 2) if risk > 0 else 0

    # 6. FINAL CLASSIFICATION
    grade = _check_5m_alignment(hist_5, direction.lower())
    
    if regime_fail or overlap_fail or accept_fail or loc_fail:
        audit["valid"] = False
        # Code/Reason already set above
    else:
        audit["valid"] = True
        if audit["implied_rr"] < 0.25:
            audit["code"] = "VALID_S4_LOW_EFFICIENCY"
            audit["quality"] = 50
            audit["reason"] = f"Valid structure but low R ({audit['implied_rr']}R)"
        elif grade == "A":
            audit["code"] = "VALID_S4_A"
            audit["quality"] = 100
            audit["reason"] = "Textbook S4. Correct Regime, Structure, and Trigger."
        else:
            audit["code"] = f"VALID_S4_GRADE_{grade}"
            audit["quality"] = 75
            audit["reason"] = "Valid S4 with minor timing imperfection."

    # --- C. EXECUTOR (Simulate PnL) ---
    # We execute all to show "What happened", but the UI will highlight validity.
    exit_price = entry_price
    status = "OPEN"
    stop = audit["stop_loss"]
    target = audit["target"]
    
    for c in c5:
        if c.timestamp <= setup_time: continue
        if direction == "LONG":
            if c.low <= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.high >= target: exit_price = target; status = "TAKE_PROFIT"; break
        else:
            if c.high >= stop: exit_price = stop; status = "STOPPED_OUT"; break
            if c.low <= target: exit_price = target; status = "TAKE_PROFIT"; break
            
    # Report PnL
    raw_pct = (exit_price - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_price) / entry_price
    pnl = capital * (raw_pct * leverage)
    
    return {
        "pnl": round(pnl, 2),
        "status": status,
        "entry": entry_price,
        "exit": exit_price,
        "audit": audit
    }