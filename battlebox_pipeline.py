# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE — v11.4 (MACRO ORACLE UPGRADE)
# Purpose: Calculates Full EMA Alignment & Mean Deviation.
# UPGRADE: Injected market_context_oracle into the SSOT payload.
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
import kabroda_mas_flow
import market_context_oracle  # <-- NEW: Import the Macro Oracle
from database import SessionLocal, SessionLock, GravityMemory 

SESSION_CONFIGS = session_manager.SESSION_CONFIGS

_LOCKED_PACKETS: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

_exchange_live = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})

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

def _calc_adx(candles: List[Dict], period: int = 14) -> Dict:
    """Wilder's Average Directional Index (+DI, -DI, ADX, rising flag)."""
    if len(candles) < period * 2 + 1:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "rising": False}
    plus_dm_vals, minus_dm_vals, tr_vals = [], [], []
    for i in range(1, len(candles)):
        h  = float(candles[i]["high"]);   l  = float(candles[i]["low"])
        ph = float(candles[i-1]["high"]); pl = float(candles[i-1]["low"]); pc = float(candles[i-1]["close"])
        up = h - ph;  dn = pl - l
        plus_dm_vals.append(up if (up > dn and up > 0) else 0.0)
        minus_dm_vals.append(dn if (dn > up and dn > 0) else 0.0)
        tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
    def _wilder(vals: List[float]) -> List[float]:
        if len(vals) < period: return []
        s = [sum(vals[:period]) / period]
        for v in vals[period:]: s.append(s[-1] - s[-1] / period + v / period)
        return s
    sm_pdm = _wilder(plus_dm_vals); sm_mdm = _wilder(minus_dm_vals); sm_tr = _wilder(tr_vals)
    if not sm_tr: return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "rising": False}
    dx_vals, pdi_vals, mdi_vals = [], [], []
    for i in range(len(sm_tr)):
        tr = sm_tr[i]
        if tr == 0: dx_vals.append(0.0); pdi_vals.append(0.0); mdi_vals.append(0.0); continue
        pdi = 100 * sm_pdm[i] / tr; mdi = 100 * sm_mdm[i] / tr
        pdi_vals.append(pdi); mdi_vals.append(mdi)
        dsum = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / dsum if dsum > 0 else 0.0)
    adx_vals = _wilder(dx_vals)
    if not adx_vals: return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "rising": False}
    return {
        "adx": round(adx_vals[-1], 2),
        "plus_di": round(pdi_vals[-1] if pdi_vals else 0.0, 2),
        "minus_di": round(mdi_vals[-1] if mdi_vals else 0.0, 2),
        "rising": len(adx_vals) >= 2 and adx_vals[-1] > adx_vals[-2],
    }

def _calc_stochastic(candles: List[Dict], k_period: int = 14, d_period: int = 3) -> Dict:
    """Stochastic Oscillator %K and %D."""
    if len(candles) < k_period: return {"k": 50.0, "d": 50.0}
    k_vals = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1 : i + 1]
        hh = max(float(c["high"]) for c in window)
        ll = min(float(c["low"])  for c in window)
        cl = float(candles[i]["close"])
        k_vals.append(100 * (cl - ll) / (hh - ll) if hh != ll else 50.0)
    d = sum(k_vals[-d_period:]) / min(d_period, len(k_vals))
    return {"k": round(k_vals[-1], 2), "d": round(d, 2)}

def _build_jewel_reading(candles: List[Dict]) -> Dict:
    """
    JEWEL indicator: EMA21/55 crossover state, ADX trend strength,
    Stochastic + RSI zone classification.
    Value zone = 38.2–61.8. Extended = <20 or >80.
    Signal is BOUNCE_PRIMED when price hugs slow EMA inside value zone with ADX rising.
    """
    if not candles or len(candles) < 56:
        return {"signal": "INSUFFICIENT_DATA"}
    closes = [float(c["close"]) for c in candles]
    ema21 = _calc_ema_series(closes, 21)[-1]
    ema55 = _calc_ema_series(closes, 55)[-1]
    ema21_prev = _calc_ema_series(closes[:-1], 21)[-1] if len(closes) > 56 else ema21
    ema55_prev = _calc_ema_series(closes[:-1], 55)[-1] if len(closes) > 56 else ema55
    gap_now  = ema21 - ema55
    gap_prev = ema21_prev - ema55_prev
    spread_pct = round(abs(gap_now) / ema55 * 100, 3) if ema55 != 0 else 0.0
    if spread_pct < 0.10:
        ema_state = "AT_SLOW_EMA"
    elif gap_now > 0:
        ema_state = "BULLISH_EXPANDING" if gap_now >= gap_prev else "BULLISH_COMPRESSING"
    else:
        ema_state = "BEARISH_EXPANDING" if gap_now <= gap_prev else "BEARISH_COMPRESSING"
    rsi = _calc_rsi(closes)
    if rsi < 20:         rsi_zone = "OVERSOLD_EXTREME"
    elif rsi < 38.2:     rsi_zone = "OVERSOLD_VALUE"
    elif rsi <= 61.8:    rsi_zone = "VALUE_ZONE"
    elif rsi <= 80:      rsi_zone = "OVERBOUGHT_VALUE"
    else:                rsi_zone = "OVERBOUGHT_EXTREME"
    stoch = _calc_stochastic(candles)
    if stoch["k"] < 20:     stoch_zone = "OVERSOLD"
    elif stoch["k"] <= 80:  stoch_zone = "NEUTRAL"
    else:                   stoch_zone = "OVERBOUGHT"
    adx_data = _calc_adx(candles)
    in_value = rsi_zone == "VALUE_ZONE" and stoch_zone == "NEUTRAL"
    bounce   = ema_state == "AT_SLOW_EMA" and in_value and adx_data["rising"]
    trending = ema_state in ("BULLISH_EXPANDING", "BEARISH_EXPANDING") and adx_data["adx"] > 25 and adx_data["rising"]
    if bounce:       signal = "BOUNCE_PRIMED"
    elif trending:   signal = "TRENDING_STRONG"
    elif in_value:   signal = "VALUE_ZONE_NEUTRAL"
    else:            signal = "EXTENDED"
    return {
        "ema21":          round(ema21, 2),
        "ema55":          round(ema55, 2),
        "ema_state":      ema_state,
        "ema_spread_pct": spread_pct,
        "rsi":            round(rsi, 2),
        "rsi_zone":       rsi_zone,
        "stoch_k":        stoch["k"],
        "stoch_d":        stoch["d"],
        "stoch_zone":     stoch_zone,
        "adx":            adx_data["adx"],
        "adx_rising":     adx_data["rising"],
        "adx_trending":   adx_data["adx"] > 25,
        "signal":         signal,
    }

def _build_synthetic_jewel(raw_15m: List[Dict], adx_4h: Optional[Dict] = None) -> Dict:
    if not raw_15m or len(raw_15m) < 200:
        return {"rsi": 50.0, "kinematic_grade": "TANGLED", "exit_warning": False}
        
    closes = [float(c["close"]) for c in raw_15m]
    current_price = closes[-1]
    
    rsi = _calc_rsi(closes, period=14)
    
    ema9 = _calc_ema_series(closes, 9)[-1]
    ema21 = _calc_ema_series(closes, 21)[-1]
    ema35 = _calc_ema_series(closes, 35)[-1]
    ema55 = _calc_ema_series(closes, 55)[-1]
    
    sma200 = _calc_sma(closes, 200)
    
    deviation_from_mean = abs(current_price - sma200) / sma200 * 100
    ribbon_spread = abs(ema9 - ema55) / ema55 * 100
    
    # CALIBRATION CHOICE B: gate OVEREXTENDED on 4H trend strength, not 15M ADX.
    # Reason: after a sharp directional move the 15M ADX decays quickly (confirmed Jun 3:
    # 15M ADX=14.3 despite 4H ADX=57.2) while the 4H trend is still fully intact.
    # A 15M-only gate would re-block the fix for any session where the prior day was the
    # big move. The 4H gate correctly distinguishes a ranging intraday spike (low 4H ADX)
    # from a continuation session after a real multi-day trend (high 4H ADX).
    adx_4h_strong = (adx_4h is not None and
                     adx_4h.get("rising", False) and
                     adx_4h.get("adx", 0.0) >= 25)

    if deviation_from_mean > 1.5 and not adx_4h_strong:
        kinematic_grade = "OVEREXTENDED"
    elif ribbon_spread < 0.15:
        kinematic_grade = "TANGLED"
    else:
        kinematic_grade = "PRIMED"

    exit_warning = kinematic_grade == "OVEREXTENDED" or (ribbon_spread < 0.10 and kinematic_grade == "TANGLED")

    return {
        "rsi": round(rsi, 2),
        "kinematic_grade": kinematic_grade,
        "deviation_from_mean_pct": round(deviation_from_mean, 2),
        "ribbon_spread_pct": round(ribbon_spread, 2),
        "exit_warning": exit_warning,
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema35": round(ema35, 2),
        "ema55": round(ema55, 2),
        "sma200": round(sma200, 2),
        "jewel": _build_jewel_reading(raw_15m),
    }

def _build_fuel_gauge(raw_1h: List[Dict], raw_4h: List[Dict], raw_15m: List[Dict]) -> Dict:
    def analyze_tf(candles):
        if not candles or len(candles) < 50:
            return {"trend": "NEUTRAL", "momentum": "NEUTRAL", "rsi": 50.0, "jewel": {"signal": "INSUFFICIENT_DATA"}}

        closes = [float(c["close"]) for c in candles]
        ema_series_30 = _calc_ema_series(closes, 30)
        ema_series_50 = _calc_ema_series(closes, 50)
        ema30 = ema_series_30[-1] if ema_series_30 else closes[-1]
        ema50 = ema_series_50[-1] if ema_series_50 else closes[-1]

        trend = "BULLISH" if ema30 > ema50 else "BEARISH"
        macd_data = _calc_macd(closes)
        momentum = "POSITIVE" if macd_data["hist"] > 0 else "NEGATIVE"
        rsi = _calc_rsi(closes)

        return {"trend": trend, "momentum": momentum, "rsi": round(rsi, 2), "ema30": round(ema30, 2), "ema50": round(ema50, 2), "jewel": _build_jewel_reading(candles)}
        
    return {
        "1H": analyze_tf(raw_1h),
        "4H": analyze_tf(raw_4h),
        "15M_JEWEL": _build_synthetic_jewel(raw_15m, adx_4h=_calc_adx(raw_4h)),
    }

def _calculate_weekly_force(daily_candles: list) -> str:
    if not daily_candles or len(daily_candles) < 21:
        return "NEUTRAL"

    closes = [float(c["close"]) for c in daily_candles]
    current_price = closes[-1]

    macro_baseline_21 = sum(closes[-21:]) / 21
    micro_baseline_7 = sum(closes[-7:]) / 7

    if current_price > macro_baseline_21 and micro_baseline_7 > macro_baseline_21:
        return "BULLISH"
    elif current_price < macro_baseline_21 and micro_baseline_7 < macro_baseline_21:
        return "BEARISH"
    
    return "NEUTRAL"

def _calculate_harmonic_matrix(candles_1h: list, candles_4h: list) -> dict:
    def get_ema(prices, period):
        if len(prices) < period: return 0.0
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = (p - ema) * multiplier + ema
        return ema

    if len(candles_1h) < 50 or len(candles_4h) < 50:
        return {"micro_state": "CHOP", "1h_fuel_status": "UNKNOWN"}

    closes_4h = [float(c["close"]) for c in candles_4h]
    ema20_4h = get_ema(closes_4h, 20)
    ema50_4h = get_ema(closes_4h, 50)
    tide_bullish = ema20_4h > ema50_4h

    closes_1h = [float(c["close"]) for c in candles_1h]
    ema20_1h = get_ema(closes_1h, 20)
    ema50_1h = get_ema(closes_1h, 50)
    wave_bullish = ema20_1h > ema50_1h

    spread_1h = abs(ema20_1h - ema50_1h) / ema50_1h
    spread_wide = spread_1h > 0.015
    adx_4h = _calc_adx(candles_4h)
    # CALIBRATION CHOICE A: threshold 20 (vs 25 — both pass all 4 replay days; 20 catches weaker trends)
    trend_is_strong = adx_4h["rising"] and adx_4h["adx"] >= 20

    if tide_bullish and wave_bullish:
        if spread_wide and not trend_is_strong: return {"micro_state": "EXHAUSTION", "1h_fuel_status": "OVEREXTENDED"}
        return {"micro_state": "SWEET_ZONE", "1h_fuel_status": "STRONG"}
    elif tide_bullish and not wave_bullish:
        return {"micro_state": "PULLBACK", "1h_fuel_status": "REFUELING"}
    elif not tide_bullish and wave_bullish:
        return {"micro_state": "HOSTILE_CEILING", "1h_fuel_status": "CHOP_RISK"}
    else:
        if spread_wide and not trend_is_strong: return {"micro_state": "EXHAUSTION", "1h_fuel_status": "OVEREXTENDED"}
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

def _fetch_macro_structure(symbol: str) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
        anchors = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.source == "MACRO_ENGINE_CLASS_0"
        ).all()
        return [{"type": a.level_type, "price": a.price} for a in anchors]
    except Exception as e:
        print(f"Macro DB Fetch Error: {e}")
        return []
    finally:
        db.close()

def _compute_sse_packet(
    raw_5m: List[Dict], anchor_ts: int, macro_bias: str, micro_bias: str, fuel_gauge: Dict, kde_data: Dict, macro_fibs: Dict, harmonic_data: Dict, macro_structure: List[Dict], macro_context: Dict, tuning: Optional[Dict] = None, raw_daily: List[Dict] = None  
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
    computed["context"]["micro_state"] = harmonic_data.get("micro_state", "CHOP")
    computed["context"]["1h_fuel_status"] = harmonic_data.get("1h_fuel_status", "UNKNOWN")
    computed["context"]["macro_structure"] = macro_structure 
    computed["context"]["macro_environment"] = macro_context # <-- NEW: External Context

    return {
        "levels": computed["levels"], 
        "context": computed.get("context", {}), 
        "bias_model": computed.get("bias_model", {}), 
        "htf_shelves": computed.get("htf_shelves", {}), 
        "lock_time": lock_end_ts, 
        "meta": computed.get("meta", {})
    }

async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: Optional[str] = None, operator_flex: bool = False, tuning: Optional[Dict] = None) -> Dict[str, Any]:
    # Concurrent fetching of required data arrays to prevent blocking
    fetch_tasks = [
        fetch_live_5m(symbol),
        fetch_live_15m(symbol),
        fetch_live_1h(symbol),
        fetch_live_4h(symbol),
        fetch_live_daily(symbol),
        market_context_oracle.get_global_macro_context() # <-- NEW: Fetch the Oracle data
    ]
    
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    raw_5m = results[0] if not isinstance(results[0], Exception) else []
    raw_15m = results[1] if not isinstance(results[1], Exception) else []
    raw_1h = results[2] if not isinstance(results[2], Exception) else []
    raw_4h = results[3] if not isinstance(results[3], Exception) else []
    raw_daily = results[4] if not isinstance(results[4], Exception) else []
    macro_context = results[5] if not isinstance(results[5], Exception) else {}

    if not raw_5m: return {"status": "ERROR", "message": "No Data"}

    now_utc = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now_utc, session_mode, manual_id)
    anchor_ts = int(session["anchor_time"])
    lock_end_ts = anchor_ts + 1800

    macro_bias = _calculate_weekly_force(raw_daily)
    micro_bias = _calculate_168h_micro_bias(raw_1h)
    
    fuel_gauge = _build_fuel_gauge(raw_1h, raw_4h, raw_15m)
    harmonic_data = _calculate_harmonic_matrix(raw_1h, raw_4h)
    macro_structure = _fetch_macro_structure(symbol) 

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
                    "1h_fuel_status": harmonic_data.get("1h_fuel_status"),
                    "macro_structure": macro_structure,
                    "macro_environment": macro_context
                }
            }
        }

    date_key = session["date_key"]
    norm_sym = _normalize_symbol(symbol)
    session_key = f"{norm_sym}::{session['id']}::{date_key}"

    async with _CACHE_LOCK:
        if session_key not in _LOCKED_PACKETS:
            db = SessionLocal()
            try:
                existing_lock = db.query(SessionLock).filter(
                    SessionLock.symbol == norm_sym,
                    SessionLock.session_id == session['id'],
                    SessionLock.date_key == date_key
                ).first()

                if existing_lock:
                    _LOCKED_PACKETS[session_key] = json.loads(existing_lock.packet_data)
                else:
                    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, harmonic_data, macro_structure, macro_context, tuning=tuning, raw_daily=raw_daily)
                    if "error" in pkt:
                        return {"status": "ERROR", "message": pkt["error"], "battlebox": {"raw_15m": raw_15m, "war_map_context": _war_map_from_1h(raw_1h), "session_battle": _safe_placeholder_state(pkt["error"]), "session": session, "levels": {}, "bias_model": {}, "context": {}}}

                    _LOCKED_PACKETS[session_key] = pkt

                    # Persist lock to DB in its own try/except so a write failure
                    # never silently blocks gravity logging or the Senior Analyst fire.
                    try:
                        new_lock = SessionLock(
                            symbol=norm_sym,
                            session_id=session['id'],
                            date_key=date_key,
                            lock_time=int(pkt["lock_time"]),
                            packet_data=json.dumps(pkt, default=str)
                        )
                        db.add(new_lock)
                        db.commit()
                        print(f"[BATTLEBOX] Session lock persisted to DB: {session_key}")
                    except Exception as lock_err:
                        print(f"[BATTLEBOX] Lock DB write failed (in-memory only): {lock_err}")
                        db.rollback()

                    gravity_engine.log_kabroda_bedrock(norm_sym, pkt["levels"], pkt["lock_time"])

                    asyncio.create_task(
                        asyncio.to_thread(
                            kabroda_mas_flow.run_mas_analysis,
                            symbol=norm_sym,
                            session_id=session['id'],
                            date_key=date_key,
                            battlebox_payload=pkt
                        )
                    )

            except Exception as e:
                print(f"DATABASE VAULT ERROR: {e}")
                traceback.print_exc()
                if session_key not in _LOCKED_PACKETS:
                    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, fuel_gauge, kde_data, macro_fibs, harmonic_data, macro_structure, macro_context, tuning=tuning, raw_daily=raw_daily)
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