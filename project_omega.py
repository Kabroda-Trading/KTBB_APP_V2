# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE v6.2 (FULL RESTORATION)
# Connected to: session_manager.py
# ==============================================================================
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import traceback
import pytz
import asyncio
import ccxt.async_support as ccxt

import sse_engine
import structure_state_engine
import session_manager  # <--- NEW AUTHORITY

# ----------------------------
# CONFIG (DELEGATED)
# ----------------------------
# We expose this reference so existing code doesn't break
SESSION_CONFIGS = session_manager.SESSION_CONFIGS 

LOCKED_SESSIONS: Dict[str, Dict[str, Any]] = {}
_exchange_live = ccxt.kucoin({"enableRateLimit": True})

# ----------------------------
# LOCAL HELPERS (RSI & DIV for Public View)
# ----------------------------
def _calculate_rsi(prices: List[float], period=14) -> List[float]:
    if len(prices) < period + 1: return [50.0] * len(prices)
    gains, losses = [], []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [50.0] * period 
    
    for i in range(period, len(prices)-1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0: rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        rsis.append(rsi)
    return rsis

def _check_public_structure(levels: Dict[str, float], candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Logic: 1-Candle Close + RSI Divergence
    Ignores Stochastics entirely.
    """
    if not candles or len(candles) < 20: 
        return {"active": False}

    bo = levels.get("breakout_trigger", 0.0)
    bd = levels.get("breakdown_trigger", 0.0)
    if bo == 0 or bd == 0: return {"active": False}

    current = candles[-1]
    px = float(current["close"])
    
    # 1. TRIGGER CHECK (1-Candle Close)
    side = "NONE"
    if px > bo: side = "LONG"
    elif px < bd: side = "SHORT"
    
    if side == "NONE": return {"active": False}

    # 2. RSI CALCULATION
    closes = [float(c["close"]) for c in candles]
    rsis = _calculate_rsi(closes)
    curr_rsi = rsis[-1]

    # 3. DIVERGENCE CHECK (Simplified Lookback)
    lookback = candles[-15:-1] # Prior 15 bars excluding current
    has_divergence = False
    
    if side == "LONG":
        min_price_idx = min(range(len(lookback)), key=lambda i: lookback[i]["low"])
        recent_low_rsi = rsis[len(candles) - 15 + min_price_idx]
        if float(current["low"]) < float(lookback[min_price_idx]["low"]) and curr_rsi > recent_low_rsi:
            has_divergence = True
        if curr_rsi < 35: has_divergence = True 

    elif side == "SHORT":
        max_price_idx = max(range(len(lookback)), key=lambda i: lookback[i]["high"])
        recent_high_rsi = rsis[len(candles) - 15 + max_price_idx]
        if float(current["high"]) > float(lookback[max_price_idx]["high"]) and curr_rsi < recent_high_rsi:
            has_divergence = True
        if curr_rsi > 65: has_divergence = True

    if has_divergence:
        return {
            "active": True,
            "action": f"STRUCTURE {side}",
            "reason": "BREAKOUT + RSI DIV",
            "side": side
        }
    
    return {"active": False}

# ----------------------------
# HELPERS
# ----------------------------
def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

def _safe_placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {
        "action": "HOLD FIRE",
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {"relative_to_triggers": "INSIDE_BAND"},
        "execution": {
            "pause_state": "NONE", "resumption_state": "NONE", "gates_mode": "PREVIEW",
            "locked_at": None, "levels": {"failure": 0.0, "continuation": 0.0},
        },
    }

def _infer_energy(elapsed_min: float) -> str:
    if elapsed_min < 30: return "CALIBRATING"
    if elapsed_min < 240: return "PRIME"
    if elapsed_min < 420: return "LATE"
    return "DEAD"

# --- REPLACED WITH SESSION MANAGER CALLS ---
def anchor_ts_for_utc_date(cfg: Dict, utc_date: datetime) -> int:
    return session_manager.anchor_ts_for_utc_date(cfg, utc_date)

def resolve_session(now_utc: datetime, mode: str = "AUTO", manual_id: Optional[str] = None) -> Dict[str, Any]:
    return session_manager.resolve_current_session(now_utc, mode, manual_id)

# ----------------------------
# DATA FETCHING
# ----------------------------
async def fetch_live_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "5m", limit=limit)
        return [{"time": int(r[0]/1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except:
        traceback.print_exc()
        return []

async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        return [{"time": int(r[0]/1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except: return []

async def fetch_historical_pagination(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    exchange = ccxt.kucoin({'enableRateLimit': True})
    s = _normalize_symbol(symbol)
    all_candles = []
    try:
        current = start_ts * 1000
        end = end_ts * 1000
        while current < end:
            ohlcv = await exchange.fetch_ohlcv(s, '5m', current, 1500) 
            if not ohlcv: break
            all_candles.extend(ohlcv)
            current = ohlcv[-1][0] + (5*60*1000)
            if len(ohlcv) < 1500: break
    finally: await exchange.close()
    
    formatted = []
    for c in all_candles:
        ts = int(c[0]/1000)
        if start_ts <= ts <= end_ts:
            formatted.append({"time": ts, "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])})
    return formatted

def _war_map_from_1h(raw_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw_1h: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No 1h data."}
    closes = [c["close"] for c in raw_1h]
    if len(closes) < 22: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "note": "Insufficient history."}
    alpha = 2.0 / (21.0 + 1.0)
    ema = closes[0]
    for px in closes[1:]: ema = (px * alpha) + (ema * (1 - alpha))
    lean = "BULLISH" if closes[-1] > ema else "BEARISH"
    return {"status": "LIVE", "lean": lean, "phase": "TRANSITION", "note": f"Pressure is {lean}."}

# ----------------------------
# PUBLIC API (LIVE & REVIEW)
# ----------------------------
async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: str = None, operator_flex: bool = False) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    raw_1h = await fetch_live_1h(symbol)
    if not raw_5m: return {"status": "ERROR", "message": "No Data"}
    
    now_utc = datetime.now(timezone.utc)
    session = resolve_session(now_utc, session_mode, manual_id)
    anchor_ts = session["anchor_time"]
    lock_end_ts = anchor_ts + 1800

    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {"status": "CALIBRATING", "timestamp": now_utc.strftime("%H:%M UTC"), "price": raw_5m[-1]["close"], "energy": "CALIBRATING", "battlebox": {"war_map_context": wm, "session_battle": _safe_placeholder_state("Calibrating..."), "session": session, "levels": {}}}

    session_key = f"{symbol}_{session['id']}_{session['date_key']}"
    if session_key not in LOCKED_SESSIONS:
        pkt = _compute_sse_packet(raw_5m, anchor_ts)
        if "error" in pkt:
             wm = _war_map_from_1h(raw_1h)
             return {"status": "ERROR", "message": pkt["error"], "battlebox": {"war_map_context": wm, "session_battle": _safe_placeholder_state(pkt["error"]), "levels": {}}}
        LOCKED_SESSIONS[session_key] = pkt

    pkt = LOCKED_SESSIONS[session_key]
    levels = pkt["levels"]
    lock_time = pkt["lock_time"]
    post_lock = [c for c in raw_5m if c["time"] >= lock_time]
    
    # 1. Base State (Standard Logic)
    state = structure_state_engine.compute_structure_state(levels, post_lock)
    
    # 2. PUBLIC STRUCTURE CHECK (The Update)
    public_chk = _check_public_structure(levels, post_lock)
    
    if public_chk["active"]:
        state["action"] = public_chk["action"]  # e.g., "STRUCTURE LONG"
        state["reason"] = public_chk["reason"]  # e.g., "BREAKOUT + RSI DIV"
        state["permission"]["status"] = "WATCHING"
        state["permission"]["side"] = public_chk["side"]

    wm = _war_map_from_1h(raw_1h)

    return {
        "status": "OK",
        "timestamp": now_utc.strftime("%H:%M UTC"),
        "price": raw_5m[-1]["close"],
        "energy": session["energy"],
        "battlebox": {
            "war_map_context": wm,
            "session_battle": state,
            "levels": levels,
            "session": session,
            "bias_model": pkt.get("bias_model", {}),
            "context": pkt.get("context", {}),
        }
    }

async def get_session_review(symbol: str, session_tz: str) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    if not raw_5m: return {"ok": False, "error": "No Data"}
    # Use session_manager configs
    cfg = next((s for s in SESSION_CONFIGS if s["tz"] == session_tz), SESSION_CONFIGS[0])
    
    # Use session_manager logic
    anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, datetime.now(timezone.utc))
    
    lock_end_ts = anchor_ts + 1800
    if anchor_ts > datetime.now(timezone.utc).timestamp():
         anchor_ts -= 86400
         lock_end_ts -= 86400
    pkt = _compute_sse_packet(raw_5m, anchor_ts)
    if "error" in pkt:
        if int(datetime.now(timezone.utc).timestamp()) < lock_end_ts:
             return {"ok": True, "mode": "CALIBRATING", "symbol": symbol, "session": {"name": cfg["name"]}, "message": "Calibrating..."}
        return {"ok": False, "error": pkt["error"]}
    open_time = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()
    return {"ok": True, "mode": "LOCKED", "symbol": symbol, "price": raw_5m[-1]["close"], "session": {"name": cfg["name"], "anchor_time": open_time}, "levels": pkt["levels"], "bias_model": pkt.get("bias_model", {}), "context": pkt.get("context", {}), "htf_shelves": pkt.get("htf_shelves", {}), "range_30m": {"high": pkt["levels"].get("range30m_high", 0), "low": pkt["levels"].get("range30m_low", 0)}}

# Core Computation Wrapper
def _compute_sse_packet(raw_5m: List[Dict], anchor_ts: int, tuning: Dict = None) -> Dict[str, Any]:
    lock_end_ts = anchor_ts + 1800
    calibration = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
    if len(calibration) < 6: return {"error": "Insufficient calibration data.", "lock_end_ts": lock_end_ts}
    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
    session_open = calibration[0]["open"]
    r30_high = max(c["high"] for c in calibration)
    r30_low = min(c["low"] for c in calibration)
    last_price = context_24h[-1]["close"] if context_24h else session_open
    sse_input = {"locked_history_5m": context_24h, "slice_24h_5m": context_24h, "slice_4h_5m": context_24h[-48:], "session_open_price": session_open, "r30_high": r30_high, "r30_low": r30_low, "last_price": last_price, "tuning": tuning or {}}
    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed: return computed
    return {"levels": computed["levels"], "context": computed.get("context", {}), "bias_model": computed.get("bias_model", {}), "htf_shelves": computed.get("htf_shelves", {}), "lock_time": lock_end_ts}