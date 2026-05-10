# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE — v11.0 (SYNTHETIC JEWEL KINEMATICS UPGRADE)
# Purpose: Calculates 15m ADX Volatility & Full EMA Alignment/Mean Deviation.
# UPGRADE: Harmonic Alignment Matrix & 21-Day Macro Baseline (SSOT Enforced).
# ==============================================================================

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import traceback
import asyncio
import os
import json

import ccxt.async_support as ccxt

import session_manager
import sse_engine
import structure_state_engine
import gravity_engine
import gravity_math
from database import SessionLocal, SessionLock

SESSION_CONFIGS = session_manager.SESSION_CONFIGS

_LOCKED_PACKETS: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

_exchange_live = ccxt.mexc({"enableRateLimit": True})

def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

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

def _calc_sma(prices: List[float], period: int) -> float:
    if len(prices) < period: return 0.0
    return sum(prices[-period:]) / period

# --- NEW: SYNTHETIC JEWEL ENGINE (ADX MATH) ---
def _calc_adx(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period * 2: return 0.0
    
    tr_list, pDM_list, nDM_list = [], [], []
    
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        prev_h = float(candles[i-1]["high"])
        prev_l = float(candles[i-1]["low"])
        prev_c = float(candles[i-1]["close"])
        
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
        
        up_move = h - prev_h
        down_move = prev_l - l
        
        pDM = up_move if up_move > down_move and up_move > 0 else 0
        nDM = down_move if down_move > up_move and down_move > 0 else 0
        
        pDM_list.append(pDM)
        nDM_list.append(nDM)

    atr = sum(tr_list[:period])
    smoothed_pDM = sum(pDM_list[:period])
    smoothed_nDM = sum(nDM_list[:period])
    dx_list = []
    
    for i in range(period, len(tr_list)):
        atr = atr - (atr / period) + tr_list[i]
        smoothed_pDM = smoothed_pDM - (smoothed_pDM / period) + pDM_list[i]
        smoothed_nDM = smoothed_nDM - (smoothed_nDM / period) + nDM_list[i]
        
        di_plus = (smoothed_pDM / atr) * 100 if atr > 0 else 0
        di_minus = (smoothed_nDM / atr) * 100 if atr > 0 else 0
        
        dx = (abs(di_plus - di_minus) / (di_plus + di_minus)) * 100 if (di_plus + di_minus) > 0 else 0
        dx_list.append(dx)

    adx = sum(dx_list[:period]) / period if len(dx_list) >= period else 0
    for i in range(period, len(dx_list)):
        adx = ((adx * (period - 1)) + dx_list[i]) / period
        
    return adx

def _build_synthetic_jewel(raw_15m: List[Dict]) -> Dict:
    """Calculates 15m RSI, ADX Volatility, 9/21/35/55 EMA Alignment, and 200 SMA Deviation."""
    if not raw_15m or len(raw_15m) < 200:
        return {"rsi": 50.0, "adx": 0.0, "kinematic_grade": "TANGLED"}
        
    closes = [float(c["close"]) for c in raw_15m]
    current_price = closes[-1]
    
    rsi = _calc_rsi(closes, period=14)
    adx = _calc_adx(raw_15m, period=14)
    
    # 1. Calculate the Institutional Ribbon (EMAs)
    ema9 = _calc_ema_series(closes, 9)[-1]
    ema21 = _calc_ema_series(closes, 21)[-1]
    ema35 = _calc_ema_series(closes, 35)[-1]
    ema55 = _calc_ema_series(closes, 55)[-1]
    
    # 2. Calculate the Concrete Floor (200 SMA)
    sma200 = _calc_sma(closes, 200)
    
    # 3. Kinematic Physics Calculations
    deviation_from_mean = abs(current_price - sma200) / sma200 * 100
    ribbon_spread = abs(ema9 - ema55) / ema55 * 100
    
    # 4. The Grading Logic (Single Source of Truth)
    if deviation_from_mean > 1.5:
        kinematic_grade = "OVEREXTENDED"
    elif ribbon_spread < 0.15:
        kinematic_grade = "TANGLED"
    else:
        kinematic_grade = "PRIMED"
        
    return {
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "kinematic_grade": kinematic_grade,
        "deviation_from_mean_pct": round(deviation_from_mean, 2),
        "ribbon_spread_pct": round(ribbon_spread, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema35": round(ema35, 2),
        "ema55": round(ema55, 2),
        "sma200": round(sma200, 2)
    }

def _build_fuel_gauge(raw_1h: List[Dict], raw_4h: List[Dict], raw_15m: List[Dict]) -> Dict:
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
        
    return {
        "1H": analyze_tf(raw_1h), 
        "4H": analyze_tf(raw_4h),
        "15M_JEWEL": _build_synthetic_jewel(raw_15m) # Inject local 15m fuel state
    }

# --- SSOT UPGRADE: 21-Day Macro Baseline ---
def _calculate_weekly_force(daily_candles: list) -> str:
    """
    SSOT MACRO COMPASS: Determines Macro Bias using a 21-Day structural baseline.
    Prevents localized 1H/4H pullbacks from falsely flipping the macro trend.
    """
    if not daily_candles or len(daily_candles) < 21:
        return "NEUTRAL"

    closes = [float(c["close"]) for c in daily_candles]
    current_price = closes[-1]

    # Establish the Macro Baseline (21 Days)
    macro_baseline_21 = sum(closes[-21:]) / 21
    
    # Establish a Micro Momentum Check (7 Days)
    micro_baseline_7 = sum(closes[-7:]) / 7

    # Logic: Price must be structurally above the 3-week floor to be Bullish.
    if current_price > macro_baseline_21 and micro_baseline_7 > macro_baseline_21:
        return "BULLISH"
    elif current_price < macro_baseline_21 and micro_baseline_7 < macro_baseline_21:
        return "BEARISH"
    
    return "NEUTRAL"

# --- SSOT UPGRADE: Harmonic Alignment Matrix ---
def _calculate_harmonic_matrix(candles_1h: list, candles_4h: list) -> dict:
    """
    SSOT ALIGNMENT MATRIX: Evaluates 1H and 4H momentum to dictate 15m engine state.
    Accounts for the 'gray area' by measuring slope and relative positioning.
    """
    def get_ema(prices, period):
        if len(prices) < period: return 0.0
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = (p - ema) * multiplier + ema
        return ema

    if len(candles_1h) < 50 or len(candles_4h) < 50:
        return {"micro_state": "CHOP", "1h_fuel_status": "UNKNOWN"}

    # 4H Tide Physics
    closes_4h = [float(c["close"]) for c in candles_4h]
    ema20_4h = get_ema(closes_4h, 20)
    ema50_4h = get_ema(closes_4h, 50)
    tide_bullish = ema20_4h > ema50_4h

    # 1H Wave Physics
    closes_1h = [float(c["close"]) for c in candles_1h]
    ema20_1h = get_ema(closes_1h, 20)
    ema50_1h = get_ema(closes_1h, 50)
    wave_bullish = ema20_1h > ema50_1h

    # 1H Fuel Exhaustion Proxy (Distance between EMAs indicates overextension)
    spread_1h = abs(ema20_1h - ema50_1h) / ema50_1h
    is_exhausted = spread_1h > 0.015  # If EMAs are stretched > 1.5% apart, fuel is burning out

    # The Matrix Logic
    if tide_bullish and wave_bullish:
        if is_exhausted:
            return {"micro_state": "EXHAUSTION", "1h_fuel_status": "OVEREXTENDED"}
        return {"micro_state": "SWEET_ZONE", "1h_fuel_status": "STRONG"}
    
    elif tide_bullish and not wave_bullish:
        return {"micro_state": "PULLBACK", "1h_fuel_status": "REFUELING"}
        
    elif not tide_bullish and wave_bullish:
        return {"micro_state": "HOSTILE_CEILING", "1h_fuel_status": "CHOP_RISK"}
        
    else:
        # Both Bearish
        if is_exhausted:
            return {"micro_state": "EXHAUSTION", "1h_fuel_status": "OVEREXTENDED"}
        return {"micro_state": "SWEET_ZONE_BEAR", "1h_fuel_status": "STRONG"}

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
    raw_5m: List[Dict], anchor_ts: int, macro_bias: str, micro_bias: str, fuel_gauge: Dict, kde_data: Dict, macro_fibs: Dict, harmonic_data: Dict, tuning: Optional[Dict] = None, raw_daily: List[Dict] = None  
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
    
    # INJECT NEW SSOT KINEMATIC DATA
    computed["context"]["micro_state"] = harmonic_data.get("micro_state", "CHOP")
    computed["context"]["1h_fuel_status"] = harmonic_data.get("1h_fuel_status", "UNKNOWN")

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
    raw_15m = await fetch_live_15m(symbol)
    raw_1h = await fetch_live_1h(symbol)
    raw_4h = await fetch_live_4h(symbol)
    raw_daily = await fetch_live_daily(symbol)

    if not raw_5m: return {"status": "ERROR", "message": "No Data"}

    now_utc = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now_utc, session_mode, manual_id)
    anchor_ts = int(session["anchor_time"])
    lock_end_ts = anchor_ts + 1800

    macro_bias = _calculate_weekly_force(raw_daily)
    micro_bias = _calculate_168h_micro_bias(raw_1h)
    
    fuel_gauge = _build_fuel_gauge(raw_1h, raw_4h, raw_15m)
    harmonic_data = _calculate_harmonic_matrix(raw_1h, raw_4h)
    
    kde_data = gravity_math.calculate_gravity_kde(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(raw_daily, [])

    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {
            "status": "CALIBRATING", "timestamp": now_utc.strftime("%H:%M UTC"), "price": float(raw_5m[-1]["close"]), "energy": "CALIBRATING", 
            "battlebox": {
                "raw_15m": raw_15m,
                "war_map_context": wm, "session_battle": _safe_placeholder_state("Calibrating..."), "session": session, "levels": {}, "bias_model": {}, 
                "context": {
                    "macro_bias": macro_bias, 
                    "micro_bias": micro_bias, 
                    "fuel_gauge": fuel_gauge, 
                    "kde_peaks": kde_data.get("peaks", []), 
                    "macro_fibs": macro_fibs,
                    "micro_state": harmonic_data.get("micro_state"),
                    "1h_fuel_status": harmonic_data.get("1h_fuel_status")
                }
            }
        }

    date_key = session["date_key"]
    session_key = f"{_normalize_symbol(symbol)}::{session['id']}::{date_key}"

    async with _CACHE_LOCK:
        if session_key not in _LOCKED_PACKETS:
            db = SessionLocal()
            try:
                existing_lock = db.query(SessionLock).filter(
                    SessionLock.symbol == symbol,
                    SessionLock.session_id == session['id'],
                    SessionLock.date_key == date_key
                ).first()

                if existing_lock:
                    _LOCKED_PACKETS[session_key] = json.loads(existing_lock.packet_data)
                else:
                    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, harmonic_data, tuning=tuning, raw_daily=raw_daily)
                    if "error" in pkt: 
                        return {"status": "ERROR", "message": pkt["error"], "battlebox": {"raw_15m": raw_15m, "war_map_context": _war_map_from_1h(raw_1h), "session_battle": _safe_placeholder_state(pkt["error"]), "session": session, "levels": {}, "bias_model": {}, "context": {}}}
                    
                    _LOCKED_PACKETS[session_key] = pkt
                    
                    new_lock = SessionLock(
                        symbol=symbol,
                        session_id=session['id'],
                        date_key=date_key,
                        lock_time=int(pkt["lock_time"]),
                        packet_data=json.dumps(pkt)
                    )
                    db.add(new_lock)
                    db.commit()
                    
                    gravity_engine.log_kabroda_bedrock(symbol, pkt["levels"], pkt["lock_time"])
            except Exception as e:
                print(f"DATABASE VAULT ERROR: {e}")
                traceback.print_exc()
                if session_key not in _LOCKED_PACKETS:
                    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, harmonic_data, tuning=tuning, raw_daily=raw_daily)
                    if "error" not in pkt:
                        _LOCKED_PACKETS[session_key] = pkt
            finally:
                db.close()
            
        pkt = _LOCKED_PACKETS.get(session_key)
        if not pkt:
            return {"status": "ERROR", "message": "Failed to initialize and lock session data."}

    levels = pkt["levels"]
    lock_time = int(pkt["lock_time"])
    post_lock = [c for c in raw_5m if int(c["time"]) >= lock_time]
    
    state = structure_state_engine.compute_structure_state(levels=levels, candles_5m_post_lock=post_lock, tuning=tuning or {})

    return {
        "status": "OK", "timestamp": now_utc.strftime("%H:%M UTC"), "price": float(raw_5m[-1]["close"]), "energy": session.get("energy", "ACTIVE"), 
        "battlebox": {
            "raw_15m": raw_15m,
            "war_map_context": _war_map_from_1h(raw_1h), "session_battle": state, "levels": levels, "session": session, 
            "bias_model": pkt.get("bias_model", {}), "context": pkt.get("context", {}), "htf_shelves": pkt.get("htf_shelves", {}), "meta": pkt.get("meta", {})
        }, 
        "candles": post_lock
    }