# market_data.py
# ==============================================================================
# KABRODA MARKET DATA — shared data-fetching and calculation layer
# Extracted from battlebox_pipeline.py to break the circular import chain:
#   battlebox_pipeline → gravity_engine → mtf_confluence_scanner → battlebox_pipeline
# This module has ZERO dependencies on battlebox_pipeline, gravity_engine,
# or any other root-level module — it only depends on ccxt and Python stdlib.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt

# ---------------------------------------------------------------------------
# EXCHANGE CLIENT — single Kraken instance shared by all fetch functions
# ---------------------------------------------------------------------------
_exchange_live = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})


# ---------------------------------------------------------------------------
# SYMBOL NORMALIZATION
# ---------------------------------------------------------------------------
def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"):
        return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"):
        return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s:
        return s.replace("USDT", "/USDT")
    return s


# ---------------------------------------------------------------------------
# LIVE OHLCV FETCHERS — one per timeframe, all using _exchange_live
# ---------------------------------------------------------------------------
async def fetch_live_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "5m", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


async def fetch_live_15m(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "15m", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


async def fetch_live_4h(symbol: str, limit: int = 200) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "4h", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


async def fetch_live_daily(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1d", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CALCULATION HELPERS — pure functions, no external dependencies
# ---------------------------------------------------------------------------
def _calc_ema_series(prices: List[float], period: int) -> List[float]:
    if not prices or len(prices) < period:
        return []
    ema = [sum(prices[:period]) / period]
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


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
