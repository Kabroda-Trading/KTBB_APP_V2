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
# CANDLE_HISTORY PERSISTENCE — best-effort upsert, never blocks a live fetch
# See UNIFIED_AUDIT_SYSTEM_PLAN.md Phase 1. Imports `database` lazily (not at
# module level) so this module's own "zero dependency on other root-level
# modules" guarantee (see header) still holds for the normal import graph —
# database.py has no dependency back on market_data.py, so this is safe, but
# keeping it a runtime import avoids widening this module's blast radius.
# ---------------------------------------------------------------------------
def _persist_candles(symbol: str, timeframe: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        import datetime as _dt
        from database import SessionLocal, CandleHistory

        timestamps = [_dt.datetime.utcfromtimestamp(r["time"]) for r in rows]
        db = SessionLocal()
        try:
            existing = (
                db.query(CandleHistory.timestamp)
                .filter(
                    CandleHistory.symbol == symbol,
                    CandleHistory.timeframe == timeframe,
                    CandleHistory.timestamp >= min(timestamps),
                    CandleHistory.timestamp <= max(timestamps),
                )
                .all()
            )
            existing_ts = {t for (t,) in existing}
            new_rows = [
                CandleHistory(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r["volume"],
                )
                for r, ts in zip(rows, timestamps)
                if ts not in existing_ts
            ]
            if new_rows:
                db.bulk_save_objects(new_rows)
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[CANDLE_HISTORY] persist failed ({timeframe} {symbol}): {e}")


# ---------------------------------------------------------------------------
# LIVE OHLCV FETCHERS — one per timeframe, all using _exchange_live
# ---------------------------------------------------------------------------
async def fetch_live_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "5m", limit=limit)
        result = [
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
        _persist_candles(s, "5M", result)
        return result
    except Exception:
        return []


async def fetch_live_15m(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "15m", limit=limit)
        result = [
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
        _persist_candles(s, "15M", result)
        return result
    except Exception:
        return []


async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        result = [
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
        _persist_candles(s, "1H", result)
        return result
    except Exception:
        return []


async def fetch_live_4h(symbol: str, limit: int = 200) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "4h", limit=limit)
        result = [
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
        _persist_candles(s, "4H", result)
        return result
    except Exception:
        return []


async def fetch_live_daily(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1d", limit=limit)
        result = [
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
        _persist_candles(s, "1D", result)
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CALCULATION HELPERS — pure functions, no external dependencies
# ---------------------------------------------------------------------------
def _calc_daily_atr14(candles_1d: List[Dict[str, Any]], period: int = 14) -> float:
    """Daily ATR(14) — simple mean of (high - low) over the last `period` DAILY
    candles. KABRODA_REBUILD_SPEC.md §3/§12: the gate's reachability condition
    (box / dailyATR14 <= 0.55) needs this specifically, not the short-timeframe
    ATR already in the locked packet (~0.2% of price, wrong scale). The spec
    is explicit: the backtest validated the simple mean-range, not Wilder's
    smoothed ATR — use the same method that was actually measured, not a
    fancier one that wasn't."""
    if not candles_1d or len(candles_1d) < period:
        return 0.0
    window = candles_1d[-period:]
    ranges = [float(c["high"]) - float(c["low"]) for c in window]
    return round(sum(ranges) / len(ranges), 4)


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


# ---------------------------------------------------------------------------
# BBWP / PMARP — the single, shared, corrected implementation.
# Moved here 2026-08-26 (Phase 4 build) because `mtf_confluence_scanner.py`
# had its OWN separate, never-corrected copy (period=20, EMA21-based PMARP,
# no real zone thresholds) that drifted silently after `battlebox_pipeline.py`
# got the real fix on 2026-08-17 -- exactly the kind of duplication this
# module's "single shared calc" pattern (see `_calc_ema_series`/`_calc_adx`
# above) already exists to prevent. Both callers now import from here; do
# not let a third copy happen -- any file that needs BBWP/PMARP imports these.
#
# Values verified directly against Trading Knowledge/knowledge/01_INDICATORS/
# {bbwp,pmarp}/README.md and cross-checked by EXTERNAL_VALIDATION_REPORT.md
# (2026-08-26, library-citation audit): BBWP length=13, SMA-5 smoothing of
# the width series (not the raw current-bar width), lookback=252. PMARP
# ma_period=20 (VWMA), lookback=350.
# ---------------------------------------------------------------------------
def _calc_bbwp(closes: List[float], bb_period: int = 13, bb_std: float = 2.0,
               lookback: int = 252, smooth: int = 5) -> float:
    """BB Width Percentile: percentile rank of the `smooth`-bar SMA of BB
    width over `lookback` bars -- not the raw current-bar width. Returns
    50.0 if insufficient data."""
    if len(closes) < bb_period + smooth:
        return 50.0
    bbw: List[Optional[float]] = [None] * len(closes)
    for i in range(bb_period - 1, len(closes)):
        window = closes[i - bb_period + 1: i + 1]
        sma = sum(window) / bb_period
        if sma == 0:
            continue
        variance = sum((x - sma) ** 2 for x in window) / bb_period
        std = variance ** 0.5
        bbw[i] = (sma + bb_std * std - (sma - bb_std * std)) / sma
    valid = [v for v in bbw if v is not None]
    if len(valid) < smooth:
        return 50.0
    smoothed = [
        sum(valid[i - smooth + 1: i + 1]) / smooth
        for i in range(smooth - 1, len(valid))
    ]
    cur = smoothed[-1]
    start = max(0, len(smoothed) - lookback)
    hist = smoothed[start:]
    if not hist:
        return 50.0
    return round(sum(1 for v in hist if v < cur) / len(hist) * 100.0, 2)


def _calc_pmarp(candles: List[Dict], ma_period: int = 20, lookback: int = 350) -> float:
    """Price MA Ratio Percentile: percentile rank of (close/VWMA) over
    `lookback` bars. Falls back to a plain SMA if volume data is
    unavailable/zero for a window. Returns 50.0 if insufficient data."""
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume") or 0.0) for c in candles]
    if len(closes) < ma_period + 1:
        return 50.0
    pmar: List[Optional[float]] = [None] * len(closes)
    for i in range(ma_period - 1, len(closes)):
        price_window = closes[i - ma_period + 1: i + 1]
        vol_window = volumes[i - ma_period + 1: i + 1]
        vol_sum = sum(vol_window)
        vwma = (
            sum(p * v for p, v in zip(price_window, vol_window)) / vol_sum
            if vol_sum > 0 else sum(price_window) / ma_period
        )
        if vwma > 0:
            pmar[i] = closes[i] / vwma
    cur = pmar[-1]
    if cur is None:
        return 50.0
    start = max(0, len(closes) - lookback)
    hist = [v for v in pmar[start:] if v is not None]
    if not hist:
        return 50.0
    return round(sum(1 for v in hist if v < cur) / len(hist) * 100.0, 2)
