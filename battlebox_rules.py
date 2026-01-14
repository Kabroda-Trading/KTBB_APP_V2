# battlebox_rules.py
# ==============================================================================
# BATTLEBOX RULE LAYER v8.2 (AUDITED: STRICT MANUAL CONTROL)
# ==============================================================================
from __future__ import annotations
from typing import Dict, List, Any, Optional

# --- CONFIGURATION ---
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 3
OB = 80.0
OS = 20.0
DEFAULT_ZONE_TOL = 0.0010
MAX_GO_BARS_5M = 48         

# --- HELPERS ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n: return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def compute_stoch(candles: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    k, d, smooth = STOCH_K, STOCH_D, STOCH_SMOOTH
    if not candles or len(candles) < k: return {"k_smooth": None, "d": None}
    try:
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        k_series = []
        for i in range(k - 1, len(candles)):
            hh = max(highs[i - k + 1 : i + 1])
            ll = min(lows[i - k + 1 : i + 1])
            denom = (hh - ll)
            k_val = 50.0 if denom <= 0 else ((closes[i] - ll) / denom) * 100.0
            k_series.append(k_val)
        
        k_smooth = []
        for i in range(len(k_series)): k_smooth.append(_sma(k_series[: i + 1], smooth))
        ds = []
        for i in range(len(k_smooth)): ds.append(_sma(k_smooth[: i + 1], d))
        
        return {"k_smooth": float(k_smooth[-1]), "d": float(ds[-1])}
    except: return {"k_smooth": None, "d": None}

def stoch_aligned(side: str, st: Dict[str, Optional[float]]) -> bool:
    k, d = st.get("k_smooth"), st.get("d")
    if k is None or d is None: return False
    # LONG: We want oversold (Cheap) unless ignored
    if side == "LONG": return k <= OS or d <= OS
    if side == "SHORT": return k >= OB or d >= OB
    return False

def check_volume_pressure(candles: List[Dict[str, Any]]) -> bool:
    if len(candles) < 21: return True
    try:
        curr = float(candles[-1].get("volume", 0) or 0)
        hist = candles[-21:-1]
        avg = sum(float(c.get("volume", 0) or 0) for c in hist) / len(hist)
        if avg == 0: return True
        return curr > (avg * 1.5)
    except: return True

# --- MAIN SIGNAL LOGIC ---
def detect_pullback_go(
    side: str,
    levels: Dict[str, float],
    post_accept_5m: List[Dict[str, Any]],
    stoch_15m_at_accept: Dict[str, Optional[float]],
    use_zone: str = "TRIGGER",
    require_volume: bool = False,
    require_divergence: bool = False,
    fusion_mode: bool = False, 
    zone_tol: float = DEFAULT_ZONE_TOL,
    ignore_15m: bool = False,       # Checkbox 1 (15m Safety)
    ignore_5m_stoch: bool = False,  # Checkbox 2 (5m Safety)
    confirmation_mode: str = "TOUCH",
    **kwargs 
) -> Dict[str, Any]:
    
    if side not in ("LONG", "SHORT") or not post_accept_5m:
        return {"ok": False, "go_type": "NONE", "go_ts": None}

    # 1. GET LEVELS
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 9999999.0))
    ds = float(levels.get("daily_support", 0.0))
    
    zone = bo if side == "LONG" else bd
    if zone <= 0: return {"ok": False, "go_type": "NONE"}

    # 2. CONTEXT CHECK (Just for reporting, does NOT override logic)
    is_blue_sky = False
    if side == "LONG" and bo > dr: is_blue_sky = True
    if side == "SHORT" and bd < ds: is_blue_sky = True

    # 3. ALIGNMENT CHECK (15m)
    # The logic here is absolute. If ignore_15m is True, we pass. Period.
    if ignore_15m:
        campaign_base_ok = True
    else:
        campaign_base_ok = stoch_aligned(side, stoch_15m_at_accept)
        # If strict mode (Volvo) and alignment fails, we kill it here.
        if not campaign_base_ok:
            return {"ok": False, "go_type": "NONE", "reason": "NO_ALIGNMENT_15M"}

    # 4. SCAN CANDLES
    for i in range(min(len(post_accept_5m), MAX_GO_BARS_5M)):
        window = post_accept_5m[: i + 1]
        c = window[-1]
        px = float(c["close"])

        # Trigger Check
        is_triggered = False
        if confirmation_mode == "1_CLOSE":
            if side == "LONG" and px > zone: is_triggered = True
            if side == "SHORT" and px < zone: is_triggered = True
        
        if not is_triggered: continue

        # Filter Check (5m Stoch)
        # The logic here is absolute. If ignore_5m_stoch is True, we skip the check.
        if not ignore_5m_stoch:
            st5 = compute_stoch(window)
            if not stoch_aligned(side, st5): continue
        
        if require_volume and not check_volume_pressure(window): continue

        # 5. RESULT & TAGGING
        # We create a detailed reason string so you can compare A/B tests easily.
        go_tag = "STRICT_GO"
        reason_tag = "ALIGNED"

        if ignore_15m or ignore_5m_stoch:
            go_tag = "OVERRIDE_GO"
            reason_tag = "FORCED"
            if ignore_15m: reason_tag += "_15M"
            if ignore_5m_stoch: reason_tag += "_5M"

        # Add context to the reason so you know WHY it was a good/bad override
        if is_blue_sky: reason_tag += "|BLUESKY"
        elif campaign_base_ok: reason_tag += "|STRUCT"

        return {
            "ok": True, 
            "go_type": go_tag,
            "go_ts": int(c["time"]), 
            "reason": reason_tag
        }

    return {"ok": False, "go_type": "NONE", "reason": "NO_TRIGGER"}