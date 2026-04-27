# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE — v9.1 (GRAVITY SSOT INTEGRATION)
# ==============================================================================
# Purpose:
# - Computes Multi-Timeframe Fuel (1H/4H EMAs, MACD, RSI).
# - Locks session open + 30m anchor range.
# - NEW: Now permanently locks the Live Gravity KDE Map to prevent Radar Drift.
# - SERVES AS EXCLUSIVE DATA ROUTER FOR ALL KABRODA ENGINES
# ==============================================================================

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import traceback
import asyncio
import os

import ccxt.async_support as ccxt

import session_manager
import sse_engine
import structure_state_engine
import gravity_engine
import gravity_math

SESSION_CONFIGS = session_manager.SESSION_CONFIGS

_LOCKED_PACKETS: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

# ----------------------------------------------------------------------
# Exchange (MEXC - No Proxy Required)
# ----------------------------------------------------------------------
_exchange_live = ccxt.mexc({"enableRateLimit": True})

def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

async def close_exchange() -> None:
    global _exchange_live
    try:
        if _exchange_live is not None:
            await _exchange_live.close()
    except Exception:
        pass

# --- DATA FETCHERS ---
async def fetch_live_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "5m", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception: return []

async def fetch_live_15m(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "15m", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception: return []

async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception: return []

async def fetch_live_4h(symbol: str, limit: int = 200) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "4h", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception: return []

async def fetch_live_daily(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1d", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception: return []

# --- PURE PYTHON MATH ENGINES (NO EXTERNAL DEPENDENCIES) ---
def _calc_ema_series(prices: List[float], period: int) -> List[float]:
    if not prices or len(prices) < period: return []
    ema = [sum(prices[:period]) / period]
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def _calc_macd(prices: List[float], fast=12, slow=26, signal=9) -> dict:
    if len(prices) < slow + signal: return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    fast_ema = _calc_ema_series(prices, fast)
    slow_ema = _calc_ema_series(prices, slow)
    macd_line = [f - s for f, s in zip(fast_ema[-(len(slow_ema)):], slow_ema)]
    signal_line = _calc_ema_series(macd_line, signal)
    if not signal_line: return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    return {"macd": macd_line[-1], "signal": signal_line[-1], "hist": macd_line[-1] - signal_line[-1]}

def _calc_rsi(prices: List[float], period=14) -> float:
    if len(prices) < period + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _build_fuel_gauge(raw_1h: List[Dict], raw_4h: List[Dict]) -> Dict:
    def analyze_tf(candles):
        if not candles or len(candles) < 50:
            return {"trend": "NEUTRAL", "momentum": "NEUTRAL", "rsi": 50.0}
        
        closes = [float(c["close"]) for c in candles]
        
        ema_series_30 = _calc_ema_series(closes, 30)
        ema_series_50 = _calc_ema_series(closes, 50)
        
        ema30 = ema_series_30[-1] if ema_series_30 else closes[-1]
        ema50 = ema_series_50[-1] if ema_series_50 else closes[-1]
        
        trend = "BULLISH" if ema30 > ema50 else "BEARISH"
        macd_data = _calc_macd(closes)
        momentum = "POSITIVE" if macd_data["hist"] > 0 else "NEGATIVE"
        rsi = _calc_rsi(closes)
        
        return {"trend": trend, "momentum": momentum, "rsi": round(rsi, 2), "ema30": round(ema30, 2), "ema50": round(ema50, 2)}
        
    return {"1H": analyze_tf(raw_1h), "4H": analyze_tf(raw_4h)}

def _calculate_weekly_force(raw_daily: List[Dict[str, Any]], anchor_ts: int) -> str:
    if not raw_daily: return "NEUTRAL"
    anchor_dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc)
    
    days_since_sunday = (anchor_dt.weekday() + 1) % 7
    if days_since_sunday == 0: days_since_sunday = 7
        
    last_sunday_dt = anchor_dt - timedelta(days=days_since_sunday)
    last_sunday_dt = last_sunday_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    last_monday_dt = last_sunday_dt - timedelta(days=6)
    last_monday_dt = last_monday_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    start_ts = int(last_monday_dt.timestamp())
    end_ts = int(last_sunday_dt.timestamp())
    week_candles = [c for c in raw_daily if start_ts <= int(c["time"]) <= end_ts]
    
    if not week_candles: return "NEUTRAL"
    op, cl = float(week_candles[0]["open"]), float(week_candles[-1]["close"])
    
    if cl > op: return "BULLISH"
    if cl < op: return "BEARISH"
    return "NEUTRAL"

def _calculate_168h_micro_bias(raw_1h: List[Dict[str, Any]]) -> str:
    if not raw_1h or len(raw_1h) < 168: return "NEUTRAL"
    pct_change = ((float(raw_1h[-1]["close"]) - float(raw_1h[-168]["close"])) / float(raw_1h[-168]["close"])) * 100.0
    if pct_change > 1.00: return "BULLISH"
    elif pct_change < -1.00: return "BEARISH"
    return "NEUTRAL"

def _safe_placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {"action": "HOLD FIRE", "reason": reason, "permission": {"status": "NOT_EARNED", "side": "NONE"}, "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"}, "location": {"relative_to_triggers": "INSIDE"}, "execution": {"pause_state": "NONE", "resumption_state": "NONE", "gates_mode": "PREVIEW", "locked_at": None, "levels": {"failure": 0.0, "continuation": 0.0}}, "diagnostics": {"fail_reason": "WAITING"}}

def _war_map_from_1h(raw_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw_1h: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No 1h data."}
    closes = [float(c["close"]) for c in raw_1h]
    if len(closes) < 22: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "note": "Insufficient history."}
    alpha = 2.0 / (21.0 + 1.0)
    ema = closes[0]
    for px in closes[1:]: ema = (px * alpha) + (ema * (1 - alpha))
    lean = "BULLISH" if closes[-1] > ema else "BEARISH"
    return {"status": "LIVE", "lean": lean, "phase": "TRANSITION", "note": f"Pressure is {lean}."}

def _compute_sse_packet(
    raw_5m: List[Dict], anchor_ts: int, macro_bias: str, micro_bias: str, fuel_gauge: Dict, kde_data: Dict, macro_fibs: Dict, tuning: Optional[Dict] = None, raw_daily: List[Dict] = None  
) -> Dict[str, Any]:
    lock_end_ts = int(anchor_ts) + 1800
    calibration = [c for c in raw_5m if anchor_ts <= int(c["time"]) < lock_end_ts]
    
    if len(calibration) < 6: return {"error": "Insufficient calibration data.", "lock_end_ts": lock_end_ts}
    
    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= int(c["time"]) < lock_end_ts]
    
    session_open = float(calibration[0]["open"])
    r30_high = max(float(c["high"]) for c in calibration)
    r30_low = min(float(c["low"]) for c in calibration)
    last_price = float(context_24h[-1]["close"]) if context_24h else session_open

    d_ema20, d_ema30, d_ema50 = 0.0, 0.0, 0.0
    if raw_daily and len(raw_daily) > 50:
        closes = [float(c["close"]) for c in raw_daily]
        d_ema20 = _calc_ema_series(closes, 20)[-1]
        d_ema30 = _calc_ema_series(closes, 30)[-1]
        d_ema50 = _calc_ema_series(closes, 50)[-1]

    sse_input = {
        "locked_history_5m": context_24h, 
        "slice_24h_5m": context_24h, 
        "slice_4h_5m": context_24h[-48:],
        "raw_daily_candles": raw_daily or [], 
        "session_open_price": session_open, 
        "r30_high": r30_high, 
        "r30_low": r30_low, 
        "last_price": last_price, 
        "tuning": tuning or {},
    }

    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed: return computed

    if "levels" in computed:
        computed["levels"]["daily_ema20"], computed["levels"]["daily_ema30"], computed["levels"]["daily_ema50"] = d_ema20, d_ema30, d_ema50

    if "context" not in computed: computed["context"] = {}
    computed["context"]["macro_bias"] = macro_bias
    computed["context"]["micro_bias"] = micro_bias
    computed["context"]["fuel_gauge"] = fuel_gauge
    computed["context"]["kde_peaks"] = kde_data.get("peaks", [])
    computed["context"]["macro_fibs"] = macro_fibs

    return {
        "levels": computed["levels"], 
        "context": computed.get("context", {}), 
        "bias_model": computed.get("bias_model", {}), 
        "htf_shelves": computed.get("htf_shelves", {}), 
        "lock_time": lock_end_ts, 
        "meta": computed.get("meta", {})
    }

async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: Optional[str] = None, operator_flex: bool = False, tuning: Optional[Dict] = None) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    raw_1h = await fetch_live_1h(symbol)
    raw_4h = await fetch_live_4h(symbol)
    raw_daily = await fetch_live_daily(symbol)

    if not raw_5m: return {"status": "ERROR", "message": "No Data"}

    now_utc = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now_utc, session_mode, manual_id)
    anchor_ts = int(session["anchor_time"])
    lock_end_ts = anchor_ts + 1800

    macro_bias = _calculate_weekly_force(raw_daily, anchor_ts)
    micro_bias = _calculate_168h_micro_bias(raw_1h)
    fuel_gauge = _build_fuel_gauge(raw_1h, raw_4h)
    
    kde_data = gravity_math.calculate_gravity_kde(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(raw_daily, [])

    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {
            "status": "CALIBRATING", "timestamp": now_utc.strftime("%H:%M UTC"), "price": float(raw_5m[-1]["close"]), "energy": "CALIBRATING", 
            "battlebox": {
                "war_map_context": wm, "session_battle": _safe_placeholder_state("Calibrating..."), "session": session, "levels": {}, "bias_model": {}, 
                "context": {"macro_bias": macro_bias, "micro_bias": micro_bias, "fuel_gauge": fuel_gauge, "kde_peaks": kde_data.get("peaks", []), "macro_fibs": macro_fibs}
            }
        }

    date_key = session["date_key"]
    session_key = f"{_normalize_symbol(symbol)}::{session['id']}::{date_key}"

    async with _CACHE_LOCK:
        if session_key not in _LOCKED_PACKETS:
            pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, tuning=tuning, raw_daily=raw_daily)
            if "error" in pkt: return {"status": "ERROR", "message": pkt["error"], "battlebox": {"war_map_context": _war_map_from_1h(raw_1h), "session_battle": _safe_placeholder_state(pkt["error"]), "session": session, "levels": {}, "bias_model": {}, "context": {}}}
            _LOCKED_PACKETS[session_key] = pkt
            gravity_engine.log_kabroda_bedrock(symbol, pkt["levels"], pkt["lock_time"])
            
        pkt = _LOCKED_PACKETS[session_key]

    levels = pkt["levels"]
    lock_time = int(pkt["lock_time"])
    post_lock = [c for c in raw_5m if int(c["time"]) >= lock_time]
    
    state = structure_state_engine.compute_structure_state(levels=levels, candles_5m_post_lock=post_lock, tuning=tuning or {})

    return {
        "status": "OK", "timestamp": now_utc.strftime("%H:%M UTC"), "price": float(raw_5m[-1]["close"]), "energy": session.get("energy", "ACTIVE"), 
        "battlebox": {
            "war_map_context": _war_map_from_1h(raw_1h), "session_battle": state, "levels": levels, "session": session, 
            "bias_model": pkt.get("bias_model", {}), "context": pkt.get("context", {}), "htf_shelves": pkt.get("htf_shelves", {}), "meta": pkt.get("meta", {})
        }, 
        "candles": post_lock
    }

async def get_session_review(symbol: str, session_id: str = "us_ny_futures", tuning: Optional[Dict] = None) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    if not raw_5m: return {"ok": False, "error": "No Data"}
    
    raw_1h = await fetch_live_1h(symbol)
    raw_4h = await fetch_live_4h(symbol)
    raw_daily = await fetch_live_daily(symbol)
    
    cfg = session_manager.get_session_config(session_id)
    now_utc = datetime.now(timezone.utc)
    anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, now_utc)
    lock_end_ts = anchor_ts + 1800
    
    if anchor_ts > int(now_utc.timestamp()):
        anchor_ts -= 86400
        lock_end_ts -= 86400

    macro_bias = _calculate_weekly_force(raw_daily, anchor_ts)
    micro_bias = _calculate_168h_micro_bias(raw_1h)
    fuel_gauge = _build_fuel_gauge(raw_1h, raw_4h)
    
    kde_data = gravity_math.calculate_gravity_kde(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(raw_daily, [])

    if int(now_utc.timestamp()) < lock_end_ts: return {"ok": True, "mode": "CALIBRATING", "symbol": symbol, "session": {"id": cfg["id"], "name": cfg["name"], "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()}, "message": "Calibrating..."}
    
    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, tuning=tuning, raw_daily=raw_daily)
    if "error" in pkt: return {"ok": False, "error": pkt["error"]}
    
    return {
        "ok": True, "mode": "LOCKED", "symbol": symbol, "price": float(raw_5m[-1]["close"]), "session": {"id": cfg["id"], "name": cfg["name"], "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()}, 
        "levels": pkt["levels"], "bias_model": pkt.get("bias_model", {}), "context": pkt.get("context", {}), "htf_shelves": pkt.get("htf_shelves", {}), 
        "meta": pkt.get("meta", {}), "range_30m": {"high": float(pkt["levels"].get("range30m_high", 0.0)), "low": float(pkt["levels"].get("range30m_low", 0.0))}
    }