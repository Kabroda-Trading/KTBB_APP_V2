# battlebox_rules.py
# ==============================================================================
# BATTLEBOX RULE LAYER v9.1 (FULL RESTORATION + SIMULATOR)
# ==============================================================================
from __future__ import annotations
from typing import Dict, List, Any, Optional

# --- CONFIGURATION ---
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 3
RSI_PERIOD = 14
DIV_LOOKBACK = 10 

# Thresholds
OB = 80.0
OS = 20.0
RSI_OB = 70.0
RSI_OS = 30.0

DEFAULT_ZONE_TOL = 0.0010
MAX_GO_BARS_5M = 48         

# --- HELPERS ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n: return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def compute_stoch(candles: List[Dict[str, Any]], k: int = STOCH_K, d: int = STOCH_D, smooth: int = STOCH_SMOOTH) -> Dict[str, Optional[float]]:
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

def compute_rsi(candles: List[Dict[str, Any]], period: int = RSI_PERIOD) -> float:
    if not candles or len(candles) < period + 1: return 50.0
    try:
        closes = [float(c["close"]) for c in candles]
        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    except: return 50.0

# --- CHECKS ---
def stoch_aligned(side: str, st: Dict[str, Optional[float]]) -> bool:
    k = st.get("k_smooth")
    d = st.get("d")
    if k is None or d is None: return False
    # LONG: We want oversold (Cheap) unless ignored
    if side == "LONG": return k <= OS or d <= OS
    if side == "SHORT": return k >= OB or d >= OB
    return False

def rsi_aligned(side: str, rsi_val: float) -> bool:
    if side == "SHORT": return rsi_val >= RSI_OB
    if side == "LONG": return rsi_val <= RSI_OS
    return False

def check_divergence(side: str, candles: List[Dict[str, Any]]) -> bool:
    if len(candles) < DIV_LOOKBACK + 5: return False
    try:
        current = candles[-1]
        prev_window = candles[-(DIV_LOOKBACK+1):-1]
        curr_rsi = compute_rsi(candles)
        if side == "LONG":
            curr_price = float(current["low"])
            prev_low = min(prev_window, key=lambda x: float(x["low"]))
            if curr_price < float(prev_low["low"]) and (curr_rsi > RSI_OS and curr_rsi < 50): return True
        elif side == "SHORT":
            curr_price = float(current["high"])
            prev_high = max(prev_window, key=lambda x: float(x["high"]))
            if curr_price > float(prev_high["high"]) and (curr_rsi < RSI_OB and curr_rsi > 50): return True
        return False
    except: return False

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

    # 2. CONTEXT CHECK
    is_blue_sky = False
    if side == "LONG" and bo > dr: is_blue_sky = True
    if side == "SHORT" and bd < ds: is_blue_sky = True

    # 3. ALIGNMENT CHECK (15m)
    # This matches your expectation: Checked Box = Override.
    if ignore_15m:
        campaign_base_ok = True
    else:
        campaign_base_ok = stoch_aligned(side, stoch_15m_at_accept)
        if not campaign_base_ok:
            return {"ok": False, "go_type": "NONE", "reason": "NO_ALIGNMENT_15M"}

    # 4. SCAN CANDLES
    for i in range(min(len(post_accept_5m), MAX_GO_BARS_5M)):
        window = post_accept_5m[: i + 1]
        c = window[-1]
        px = float(c["close"])
        lo = float(c["low"])
        hi = float(c["high"])

        # Trigger Check
        is_triggered = False
        if confirmation_mode == "TOUCH":
            tol = zone * zone_tol
            if side == "LONG" and lo <= zone + tol: is_triggered = True
            if side == "SHORT" and hi >= zone - tol: is_triggered = True  
        elif confirmation_mode == "1_CLOSE":
            if side == "LONG" and px > zone: is_triggered = True
            if side == "SHORT" and px < zone: is_triggered = True
        
        if not is_triggered: continue

        # Filter Check (5m Stoch / RSI / Fusion)
        if not ignore_5m_stoch:
            st5 = compute_stoch(window)
            
            # FUSION MODE: If Stoch fails, check RSI
            if fusion_mode:
                if not stoch_aligned(side, st5):
                    rsi_val = compute_rsi(window)
                    if not rsi_aligned(side, rsi_val): continue
            else:
                # STANDARD MODE: Strict Stoch check
                if not stoch_aligned(side, st5): continue
        
        if require_volume and not check_volume_pressure(window): continue
        if require_divergence and not check_divergence(side, window): continue

        # 5. RESULT & TAGGING
        go_tag = "STRICT_GO"
        reason_tag = "ALIGNED"

        if ignore_15m or ignore_5m_stoch:
            go_tag = "OVERRIDE_GO"
            reason_tag = "FORCED"
            if ignore_15m: reason_tag += "_15M"
            if ignore_5m_stoch: reason_tag += "_5M"

        if is_blue_sky: reason_tag += "|BLUESKY"
        elif campaign_base_ok: reason_tag += "|STRUCT"

        return {
            "ok": True, 
            "go_type": go_tag, 
            "go_ts": int(c["time"]), 
            "reason": reason_tag
        }

    return {"ok": False, "go_type": "NONE", "reason": "NO_TRIGGER"}

# --- TRADE SIMULATOR (RESTORED) ---
def simulate_trade(
    entry_price: float,
    entry_ts: int,
    stop_price: float,
    direction: str,
    levels: Dict[str, float],
    future_candles: List[Dict[str, Any]]
) -> Dict[str, Any]:
    
    if not future_candles:
        return {"outcome": "OPEN", "r_mult": 0.0, "tp_hits": []}

    dr = levels.get("daily_resistance", 0.0)
    ds = levels.get("daily_support", 0.0)
    energy = abs(dr - ds)
    if energy == 0: energy = entry_price * 0.01

    range_bps = (energy / entry_price) * 10000 if entry_price > 0 else 0
    rel_pos = 0.0
    if energy > 0:
        if direction == "LONG":
            rel_pos = (entry_price - ds) / energy
        else: 
            rel_pos = (dr - entry_price) / energy
            
    is_blue_sky = rel_pos > 1.0
    is_compressed = range_bps < 150 

    targets = []
    if direction == "LONG":
        if not is_blue_sky: 
            targets.append({"name": "TP1", "price": dr})
            targets.append({"name": "TP2", "price": entry_price + energy})
            targets.append({"name": "TP3", "price": entry_price + (energy * 2.5)})
        else:
            targets.append({"name": "TP1", "price": entry_price + (energy * 0.5)})
            targets.append({"name": "TP2", "price": entry_price + energy})
            targets.append({"name": "TP3", "price": entry_price + (energy * 3.0)})
    else: # SHORT
        if not is_blue_sky:
            targets.append({"name": "TP1", "price": ds})
            targets.append({"name": "TP2", "price": entry_price - energy})
            targets.append({"name": "TP3", "price": entry_price - (energy * 2.5)})
        else:
            targets.append({"name": "TP1", "price": entry_price - (energy * 0.5)})
            targets.append({"name": "TP2", "price": entry_price - energy})
            targets.append({"name": "TP3", "price": entry_price - (energy * 3.0)})

    hits = []
    stopped_out = False
    stop_hit_price = 0.0
    risk_dist = abs(entry_price - stop_price)
    exit_price = future_candles[-1]["close"] 

    for c in future_candles:
        c_high = float(c["high"])
        c_low = float(c["low"])

        if direction == "LONG":
            if c_low <= stop_price:
                stopped_out = True
                stop_hit_price = stop_price
                exit_price = stop_price
                break 
            for t in targets:
                if t["name"] not in hits and c_high >= t["price"]:
                    hits.append(t["name"])
        else: # SHORT
            if c_high >= stop_price:
                stopped_out = True
                stop_hit_price = stop_price
                exit_price = stop_price
                break
            for t in targets:
                if t["name"] not in hits and c_low <= t["price"]:
                    hits.append(t["name"])

    pnl = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    r_mult = pnl / risk_dist if risk_dist > 0 else 0

    return {
        "outcome": "LOSS" if stopped_out else "WIN/OPEN",
        "r_realized": round(r_mult, 2),
        "targets_hit": hits,
        "trade_type": "BLUE SKY" if is_blue_sky else "STRUCTURE",
        "context": {
            "range_bps": round(range_bps, 0),
            "trigger_loc": round(rel_pos, 2),
            "is_compressed": is_compressed
        }
    }