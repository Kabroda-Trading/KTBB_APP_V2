# battlebox_rules.py
# ==============================================================================
# BATTLEBOX RULE LAYER v7.0 (SMART STRUCTURE TARGETING)
# ==============================================================================
from __future__ import annotations
from typing import Dict, List, Any, Optional

print(">>> LOADING BATTLEBOX RULES v7.0 (SMART TARGETS ENABLED) <<<")

# CONFIG
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

# Defaults
DEFAULT_ZONE_TOL = 0.0010
PUSH_MIN_PCT = 0.0008
MAX_GO_BARS_5M = 48        

# --- HELPERS ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n: return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def compute_stoch(candles: List[Dict[str, Any]], k: int = STOCH_K, d: int = STOCH_D, smooth: int = STOCH_SMOOTH) -> Dict[str, Optional[float]]:
    if not candles or len(candles) < k: return {"k_raw": None, "k_smooth": None, "d": None}
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
        return {"k_raw": float(k_series[-1]), "k_smooth": float(k_smooth[-1]), "d": float(ds[-1])}
    except: return {"k_raw": None, "k_smooth": None, "d": None}

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
    if side == "SHORT": return k >= OB or d >= OB
    if side == "LONG": return k <= OS or d <= OS
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
    if len(candles) < 21: return False
    try:
        curr = float(candles[-1].get("volume", 0) or 0)
        hist = candles[-21:-1]
        avg = sum(float(c.get("volume", 0) or 0) for c in hist) / len(hist)
        if avg == 0: return True
        return curr > (avg * 1.5)
    except: return False

# --- UNIFIED SIGNAL DETECTOR ---
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
    ignore_15m: bool = False,
    ignore_5m_stoch: bool = False,
    confirmation_mode: str = "TOUCH",
    **kwargs 
) -> Dict[str, Any]:
    
    if side not in ("LONG", "SHORT") or not post_accept_5m:
        return {"ok": False, "go_type": "NONE", "go_ts": None}

    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    vah = float(levels.get("f24_vah", 0.0))
    val = float(levels.get("f24_val", 0.0))

    if use_zone == "VALUE":
        zone = val if side == "LONG" else vah
    else:
        zone = bo if side == "LONG" else bd

    if zone <= 0: return {"ok": False, "go_type": "NONE"}

    campaign_base_ok = True if ignore_15m else stoch_aligned(side, stoch_15m_at_accept)

    for i in range(min(len(post_accept_5m), MAX_GO_BARS_5M)):
        window = post_accept_5m[: i + 1]
        c = window[-1]
        px = float(c["close"])
        lo = float(c["low"])
        hi = float(c["high"])

        # --- TRIGGER LOGIC ---
        is_triggered = False
        
        if confirmation_mode == "TOUCH":
            tol = zone * zone_tol
            if side == "LONG" and lo <= zone + tol: is_triggered = True
            if side == "SHORT" and hi >= zone - tol: is_triggered = True
            
        elif confirmation_mode == "1_CLOSE":
            if side == "LONG" and px > zone: is_triggered = True
            if side == "SHORT" and px < zone: is_triggered = True
            
        elif confirmation_mode == "2_CLOSE":
            if i > 0:
                prev_c = window[-2]
                prev_px = float(prev_c["close"])
                if side == "LONG":
                    if px > zone and prev_px > zone: is_triggered = True
                else: 
                    if px < zone and prev_px < zone: is_triggered = True
        
        if not is_triggered: continue

        # --- FILTERS ---
        if not ignore_5m_stoch:
            st5 = compute_stoch(window)
            if not stoch_aligned(side, st5): continue
        
        if fusion_mode:
            rsi_val = compute_rsi(window)
            if not rsi_aligned(side, rsi_val): continue

        if require_volume and not check_volume_pressure(window): continue
        if require_divergence and not check_divergence(side, window): continue

        go_type = "CAMPAIGN_GO" if campaign_base_ok else "SCALP_GO"
        reason_str = confirmation_mode
        if not ignore_5m_stoch: reason_str += "+STOCH"
        if require_volume: reason_str += "+VOL"
        if require_divergence: reason_str += "+DIV"
        
        return {
            "ok": True, 
            "go_type": go_type, 
            "go_ts": int(c["time"]), 
            "reason": reason_str
        }

    return {"ok": False, "go_type": "NONE", "go_ts": None}

# --- SMART TRADE SIMULATOR ---
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

    # 1. Calculate Market Energy (Daily Range)
    dr = levels.get("daily_resistance", 0.0)
    ds = levels.get("daily_support", 0.0)
    energy = abs(dr - ds)
    if energy == 0: energy = entry_price * 0.01

    # 2. Determine Trade Targets (Compression vs Blue Sky)
    is_blue_sky = False
    targets = []

    if direction == "LONG":
        if entry_price < dr: 
            # COMPRESSION: TP1 is DR
            targets.append({"name": "TP1", "price": dr})
            targets.append({"name": "TP2", "price": entry_price + energy})
            targets.append({"name": "TP3", "price": entry_price + (energy * 2.5)})
        else:
            # BLUE SKY: No Resistance
            is_blue_sky = True
            targets.append({"name": "TP1", "price": entry_price + (energy * 0.5)})
            targets.append({"name": "TP2", "price": entry_price + energy})
            targets.append({"name": "TP3", "price": entry_price + (energy * 3.0)})

    else: # SHORT
        if entry_price > ds:
            # COMPRESSION: TP1 is DS
            targets.append({"name": "TP1", "price": ds})
            targets.append({"name": "TP2", "price": entry_price - energy})
            targets.append({"name": "TP3", "price": entry_price - (energy * 2.5)})
        else:
            # BLUE SKY
            is_blue_sky = True
            targets.append({"name": "TP1", "price": entry_price - (energy * 0.5)})
            targets.append({"name": "TP2", "price": entry_price - energy})
            targets.append({"name": "TP3", "price": entry_price - (energy * 3.0)})

    # 3. Run Simulation
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

    # 4. Results
    pnl = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    r_mult = pnl / risk_dist if risk_dist > 0 else 0

    return {
        "outcome": "LOSS" if stopped_out else "WIN/OPEN",
        "r_realized": round(r_mult, 2),
        "targets_hit": hits,
        "trade_type": "BLUE SKY" if is_blue_sky else "STRUCTURE",
        "energy_pts": int(energy)
    }