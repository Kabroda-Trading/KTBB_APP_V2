# strategy_auditor.py
# ==============================================================================
# KABRODA STRATEGY AUDIT MASTER CORE
# ==============================================================================
# This file contains the "Deep Forensic" logic for all S-Strategies.
# It is used by the Research Lab to grade historical trade quality.
#
# SECTIONS:
# 1. SHARED UTILS (Geometry, math)
# 2. S4: MID-BAND FADE (Rotational)
# [Future S-Strategies will be added here]
# ==============================================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# ==========================================
# 1. SHARED UTILS & DATA STRUCTURES
# ==========================================

@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float

def _get_candles_in_window(candles: List[Dict], end_time: int, minutes_back: int) -> List[Candle]:
    """Helper to slice history relative to a trade entry."""
    start_time = end_time - (minutes_back * 60)
    # Convert dicts to objects for easier math
    sliced = []
    for c in candles:
        if start_time <= c['time'] <= end_time:
            sliced.append(Candle(c['time'], c['open'], c['high'], c['low'], c['close']))
    return sliced

def _check_acceptance(candles: List[Candle], level: float, side: str, n: int = 2) -> bool:
    """Checks for N consecutive closes beyond a level."""
    streak = 0
    for c in candles:
        if (side == "long" and c.close > level) or (side == "short" and c.close < level):
            streak += 1
        else:
            streak = 0
        if streak >= n: return True
    return False

def _check_5m_alignment(candles: List[Candle], side: str) -> str:
    """Grades the 5m entry trigger quality (A, B, or C)."""
    if len(candles) < 3: return "C"
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    
    if side == "short":
        # Lower Highs + Downward momentum
        if c3.close < c2.close and c3.high < c2.high: return "A"
    else:
        # Higher Lows + Upward momentum
        if c3.close > c2.close and c3.low > c2.low: return "A"
    return "B"

# ==========================================
# 2. STRATEGY S4: MID-BAND FADE
# ==========================================
# Logic: Fade Value Edges (VAH/VAL) back to Center (POC).
# Critical: Must NOT be accepted beyond Breakout/Breakdown triggers.

def audit_s4(levels: Dict, trade_entry: float, trade_time: int, trade_side: str, 
             raw_15m: List[Dict], raw_5m: List[Dict]) -> Dict[str, Any]:
    
    # 1. SETUP DATA
    history_15m = _get_candles_in_window(raw_15m, trade_time, 120) # Look back 2 hours
    history_5m = _get_candles_in_window(raw_5m, trade_time, 30)    # Look back 30 mins
    
    vah = levels["f24_vah"]
    val = levels["f24_val"]
    poc = levels["f24_poc"]
    bo = levels["breakout_trigger"]
    bd = levels["breakdown_trigger"]
    dr = levels.get("daily_resistance", 0)
    ds = levels.get("daily_support", 0)

    audit = {
        "valid": False,
        "quality": 0,
        "code": "UNKNOWN",
        "reason": "",
        "stop_loss": 0.0,
        "target": poc
    }

    # 2. REGIME CHECK (Hard Gate)
    # If price accepted outside triggers, S4 is INVALID.
    if trade_side == "short":
        if _check_acceptance(history_15m, bo, "long"):
            audit["code"] = "INVALID_REGIME_BREAKOUT"
            audit["reason"] = "Market accepted above Breakout Trigger. Rotation invalid."
            return audit
    else:
        if _check_acceptance(history_15m, bd, "short"):
            audit["code"] = "INVALID_REGIME_BREAKDOWN"
            audit["reason"] = "Market accepted below Breakdown Trigger. Rotation invalid."
            return audit

    # 3. LOCATION CHECK (The "No Man's Land" Gate)
    # Entry must be near the Value Edge (VAH for short, VAL for long).
    tolerance = poc * 0.002 # 0.2% tolerance
    
    if trade_side == "short":
        # Invalid if entering way above resistance (Chasing)
        hard_ceiling = max(vah, bo, dr)
        if trade_entry > hard_ceiling + tolerance:
            audit["code"] = "INVALID_CHASE_HIGH"
            audit["reason"] = f"Entry {trade_entry} is too high above resistance structure."
            return audit
        
        # Ideally entering near VAH or between VAH/Trigger
        if not (trade_entry >= (vah - tolerance)):
            audit["code"] = "INVALID_LOCATION_MID"
            audit["reason"] = "Short entry is too deep inside value (Middle of range). Wait for VAH."
            return audit

    else: # Long
        hard_floor = min(val, bd, ds)
        if trade_entry < hard_floor - tolerance:
            audit["code"] = "INVALID_CHASE_LOW"
            audit["reason"] = f"Entry {trade_entry} is too far below support structure."
            return audit
            
        if not (trade_entry <= (val + tolerance)):
            audit["code"] = "INVALID_LOCATION_MID"
            audit["reason"] = "Long entry is too deep inside value (Middle of range). Wait for VAL."
            return audit

    # 4. ALIGNMENT CHECK (Grading)
    grade = _check_5m_alignment(history_5m, trade_side)
    
    # 5. RISK CALCULATION
    # Stop Placement: Just beyond the invalidation point
    stop_buffer = 25.0 # Points
    
    if trade_side == "short":
        # Invalidation is acceptance above VAH/Breakout
        # If Daily Res is close, use that as the hard wall
        base = max(vah, bo)
        if dr > base and (dr - base) < 300: base = dr
        audit["stop_loss"] = base + stop_buffer
    else:
        base = min(val, bd)
        if ds < base and (base - ds) < 300: base = ds
        audit["stop_loss"] = base - stop_buffer

    # 6. FINAL PASS
    audit["valid"] = True
    audit["quality"] = 100 if grade == "A" else 75
    audit["code"] = f"VALID_S4_GRADE_{grade}"
    audit["reason"] = "Valid rotational structure. Edge faded back to value."
    
    return audit