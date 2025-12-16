# dmr_report.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import math

from data_feed import build_auto_inputs
from sse_engine import compute_sse_levels
from trade_logic_v2 import build_trade_logic_summary


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s in ("BTC", "BTCUSDT"):
        return "BTCUSDT"
    return s or "BTCUSDT"


# -------------------------
# Indicators (simple + robust)
# -------------------------
def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period or period <= 1:
        return []
    k = 2 / (period + 1)
    ema = []
    # seed with SMA
    sma = sum(values[:period]) / period
    ema.append(sma)
    for v in values[period:]:
        ema.append((v - ema[-1]) * k + ema[-1])
    return ema


def _sma(values: List[float], period: int) -> List[float]:
    if len(values) < period or period <= 1:
        return []
    out = []
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out.append(s / period)
    return out


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        if d >= 0:
            gains += d
        else:
            losses += -d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Wilder smoothing for remaining
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
    return rsi


def _atr(ohlcv: List[List[float]], period: int = 14) -> Optional[float]:
    if len(ohlcv) < period + 1:
        return None
    trs = []
    for i in range(1, len(ohlcv)):
        prev_close = ohlcv[i - 1][4]
        high = ohlcv[i][2]
        low = ohlcv[i][3]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    # Wilder-style ATR
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _structure_bias(closes: List[float], lookback: int = 20) -> str:
    if len(closes) < lookback + 1:
        return "unknown"
    window = closes[-lookback:]
    hi = max(window)
    lo = min(window)
    last = closes[-1]
    mid = (hi + lo) / 2.0
    if last > mid and last > window[-2]:
        return "bullish"
    if last < mid and last < window[-2]:
        return "bearish"
    return "balanced"


def _regime(closes: List[float], atr: Optional[float]) -> str:
    if len(closes) < 30 or atr is None or atr == 0:
        return "unknown"
    rng = max(closes[-30:]) - min(closes[-30:])
    # If 30-bar range is small relative to ATR sum, call it balanced
    # (simple, stable heuristic)
    if rng < atr * 3.0:
        return "balanced"
    return "trending"


# -------------------------
# OHLCV fetch
# -------------------------
def _fetch_ohlcv(symbol: str, timeframe: str, limit: int = 200) -> List[List[float]]:
    """
    Uses data_feed if it exposes a fetch_ohlcv helper, otherwise falls back to ccxt.
    """
    # Try data_feed helper first (if present in your project)
    try:
        import data_feed  # type: ignore
        if hasattr(data_feed, "fetch_ohlcv"):
            return data_feed.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)  # type: ignore
    except Exception:
        pass

    # Fallback: ccxt binance
    import ccxt  # type: ignore
    ex = ccxt.binance({"enableRateLimit": True})
    # If you're using USD-M futures vs spot, you can swap this to binanceusdm()
    return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def _tf_facts(symbol: str, tf: str) -> Dict[str, Any]:
    ohlcv = _fetch_ohlcv(symbol, tf, limit=250)
    closes = [c[4] for c in ohlcv if len(c) >= 5]
    if len(closes) < 60:
        return {"tf": tf, "ok": False, "reason": "insufficient_candles"}

    ema21_series = _ema(closes, 21)
    sma21_series = _sma(closes, 21)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(ohlcv, 14)

    last = closes[-1]
    ema21 = ema21_series[-1] if ema21_series else None
    sma21 = sma21_series[-1] if sma21_series else None

    bias = _structure_bias(closes, 20)
    reg = _regime(closes, atr14)

    ema_slope = None
    if len(ema21_series) >= 5:
        ema_slope = _pct(ema21_series[-1], ema21_series[-5])

    return {
        "tf": tf,
        "ok": True,
        "close": last,
        "ema21": ema21,
        "sma21": sma21,
        "ema21_slope_pct": ema_slope,
        "rsi14": rsi14,
        "atr14": atr14,
        "structure_bias": bias,
        "regime": reg,
    }


def _momentum_bullets(facts: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """
    Returns {"4H": "...", "1H": "...", "15M": "...", "5M": "..."} based on computed facts.
    """
    out: Dict[str, str] = {}
    mapping = {"4H": "4h", "1H": "1h", "15M": "15m", "5M": "5m"}
    for label, tf in mapping.items():
        f = facts.get(tf, {})
        if not f or not f.get("ok"):
            out[label] = "Unknown (insufficient data)"
            continue

        close = f["close"]
        ema21 = f.get("ema21")
        rsi = f.get("rsi14")
        bias = f.get("structure_bias", "unknown")
        reg = f.get("regime", "unknown")
        slope = f.get("ema21_slope_pct")

        pos = "above EMA21" if (ema21 is not None and close > ema21) else "below EMA21" if ema21 is not None else "vs EMA21 unknown"
        rsi_txt = f"RSI≈{rsi:.0f}" if isinstance(rsi, (int, float)) else "RSI unknown"
        slope_txt = f"EMA21 slope {slope:+.2f}%" if isinstance(slope, (int, float)) else "EMA21 slope unknown"

        out[label] = f"{bias.upper()} / {reg} • close {pos} • {rsi_txt} • {slope_txt}"
    return out


# -------------------------
# Public entrypoint
# -------------------------
def run_auto_ai(symbol: str, user_timezone: Optional[str] = None) -> Dict[str, Any]:
    market = _norm_symbol(symbol)
    tz = (user_timezone or "UTC").strip() or "UTC"

    # 1) Your existing computed inputs (levels, OR, shelves)
    inputs = build_auto_inputs(symbol=market, session_tz=tz)
    inputs["r30_high"] = inputs.get("range30m_high")
    inputs["r30_low"] = inputs.get("range30m_low")

    sse = compute_sse_levels(inputs)
    levels = sse.get("levels") or {}
    htf_shelves = sse.get("htf_shelves") or {}
    intraday_shelves = sse.get("intraday_shelves") or {}

    # 2) Multi-timeframe computed facts (NO screenshots)
    tf_facts = {}
momentum = {}

try:
    tf_facts = {
        "4h": _tf_facts(market, "4h"),
        "1h": _tf_facts(market, "1h"),
        "15m": _tf_facts(market, "15m"),
        "5m": _tf_facts(market, "5m"),
    }
    momentum = _momentum_bullets(tf_facts)
except Exception as e:
    # HARD GUARANTEE: DMR never fails due to candle fetch
    tf_facts = {}
    momentum = {
        "4H": "Momentum unavailable (data fetch error)",
        "1H": "Momentum unavailable (data fetch error)",
        "15M": "Momentum unavailable (data fetch error)",
        "5M": "Momentum unavailable (data fetch error)",
    }


    # 3) Trade logic (your deterministic strategy engine)
    range_30m = inputs.get("range_30m") or {
        "high": inputs.get("range30m_high"),
        "low": inputs.get("range30m_low"),
    }

    trade_logic = build_trade_logic_summary(
        symbol=market,
        levels=levels,
        range_30m=range_30m,
        htf_shelves=htf_shelves,
        inputs={
            **inputs,
            "levels": levels,
            "htf_shelves": htf_shelves,
            "intraday_shelves": intraday_shelves,
            "tf_facts": tf_facts,
        },
    )

    # 4) Execution rules (hard-coded so AI can’t “forget”)
    execution_rules = {
        "trigger_confirm_15m_closes": 2,
        "after_confirm_require_5m_alignment": True,
        "hard_exit_rule": "Exit on a 5m close through 21 SMA (long: close below; short: close above).",
    }

    payload: Dict[str, Any] = {
        "symbol": market,
        "date": inputs.get("date") or _utc_day(),
        "session_tz": tz,

        # deterministic
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,

        # computed “facts pack” for AI writing
        "tf_facts": tf_facts,
        "momentum_summary": momentum,
        "execution_rules": execution_rules,
    }

    # Let main.py optionally add AI text; but we can also do it here if you prefer.
    # (Keeping it compatible with your current main.py flow.)
    return payload
