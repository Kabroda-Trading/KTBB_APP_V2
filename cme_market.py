# cme_market.py
# ==============================================================================
# CME MARKET DATA -- fetches real-time confirmed candles for D1 (1-minute
# calibration bars), D2 (5-minute breakout cross/retest bars), and D3
# (5-minute ratchet monitoring).
#
# Unified Continuous Real-Time Engine (Architecture A):
# Uses continuous CME Globex futures (MNQ=F / MES=F, fallback NQ=F / ES=F),
# matching the exact underlying dataset calibrated in Kabroda AI Brain's
# 10.4-year CME study (DATA_ASSETS.md Rows 21-22).
#
# Eliminates batch ingestion lag, HTTP 422 errors, and external API key
# requirements for market data.
# ==============================================================================

from __future__ import annotations

import asyncio
import datetime
from typing import Any, Dict, List, Optional

import cme_clock
import yfinance as yf


_TICKER_MAP = {
    "MNQ": ("MNQ=F", "NQ=F"),
    "MES": ("MES=F", "ES=F"),
}


def _strip_to_signal_shape(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """cme_signals.py's functions only ever read open/high/low/close --
    drops timestamps and extra metadata."""
    return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]} for b in bars]


def _aggregate_to_confirmed_5m(bars_1m: List[Dict[str, Any]], now_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Aggregates 1-minute bars into 5-minute buckets aligned to real
    wall-clock 5-minute boundaries. CONFIRMED means fully closed: a bucket
    is only included once its own 5-minute window has fully elapsed relative
    to now_utc."""
    buckets: Dict[datetime.datetime, List[Dict[str, Any]]] = {}
    for bar in bars_1m:
        ts = bar["ts_event"]
        bucket_start = ts - datetime.timedelta(minutes=ts.minute % 5, seconds=ts.second, microseconds=ts.microsecond)
        buckets.setdefault(bucket_start, []).append(bar)

    confirmed = []
    for bucket_start in sorted(buckets):
        bucket_end = bucket_start + datetime.timedelta(minutes=5)
        if bucket_end > now_utc:
            continue
        minute_bars = buckets[bucket_start]
        confirmed.append({
            "open": minute_bars[0]["open"],
            "high": max(b["high"] for b in minute_bars),
            "low": min(b["low"] for b in minute_bars),
            "close": minute_bars[-1]["close"],
        })
    return confirmed


def _download_1m_bars(symbol: str) -> Optional[Any]:
    tickers = _TICKER_MAP.get(symbol.upper(), (f"{symbol.upper()}=F",))
    for ticker in tickers:
        try:
            df = yf.download(ticker, period="5d", interval="1m", progress=False)
            if df is not None and not df.empty:
                if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
                    df.columns = df.columns.get_level_values(0)
                return df
        except Exception as e:
            print(f"|| CME MARKET || download failed for {ticker}: {e}")
    return None


def _fetch_calibration_1m_sync(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    df = _download_1m_bars(symbol)
    if df is None or df.empty:
        return []

    try:
        df = df.tz_convert(cme_clock.NY_TZ)
    except Exception:
        pass

    cal_df = df[
        (df.index.strftime("%Y-%m-%d") == date_key)
        & (df.index.time >= datetime.time(cme_clock.OPEN_H, cme_clock.OPEN_M))
        & (df.index.time < datetime.time(cme_clock.LOCK_H, cme_clock.LOCK_M))
    ]
    return [
        {
            "open": float(r["Open"]),
            "high": float(r["High"]),
            "low": float(r["Low"]),
            "close": float(r["Close"]),
        }
        for _, r in cal_df.iterrows()
    ]


def _fetch_confirmed_5m_sync(symbol: str, since_utc: datetime.datetime, now_utc: datetime.datetime) -> List[Dict[str, Any]]:
    df = _download_1m_bars(symbol)
    if df is None or df.empty:
        return []

    try:
        df = df.tz_convert(datetime.timezone.utc)
    except Exception:
        pass

    if since_utc.tzinfo is None:
        since_utc = since_utc.replace(tzinfo=datetime.timezone.utc)

    df_since = df[df.index >= since_utc]
    bars_1m = [
        {
            "ts_event": idx.to_pydatetime(),
            "open": float(r["Open"]),
            "high": float(r["High"]),
            "low": float(r["Low"]),
            "close": float(r["Close"]),
        }
        for idx, r in df_since.iterrows()
    ]
    return _aggregate_to_confirmed_5m(bars_1m, now_utc)


def _fetch_daily_atr14_sync(symbol: str, date_key: str) -> Optional[float]:
    tickers = _TICKER_MAP.get(symbol.upper(), (f"{symbol.upper()}=F",))
    df = None
    for ticker in tickers:
        try:
            df = yf.download(ticker, period="1mo", interval="1d", progress=False)
            if df is not None and not df.empty:
                if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
                    df.columns = df.columns.get_level_values(0)
                break
        except Exception as e:
            print(f"|| CME MARKET || daily download failed for {ticker}: {e}")

    if df is None or df.empty:
        return None

    df_prior = df[df.index.strftime("%Y-%m-%d") < date_key]
    if len(df_prior) < 14:
        return None

    last14 = df_prior.iloc[-14:]
    ranges = [float(r["High"] - r["Low"]) for _, r in last14.iterrows()]
    return sum(ranges) / len(ranges)


async def fetch_calibration_1m(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    """Real 1-minute bars for `date_key`'s 9:30-10:00 ET calibration
    window, in the shape cme_signals.compute_tpo_value_area() expects."""
    bars = await asyncio.to_thread(_fetch_calibration_1m_sync, symbol, date_key)
    if not bars:
        raise RuntimeError(f"Failed to fetch calibration 1m bars for {symbol} on {date_key}")
    return bars


async def fetch_confirmed_5m(symbol: str, since_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Confirmed (fully closed) 5-minute bars for `symbol` since `since_utc`,
    in the shape cme_signals.check_cross()/check_retest_fill() expect."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    bars_5m = await asyncio.to_thread(_fetch_confirmed_5m_sync, symbol, since_utc, now_utc)
    return _strip_to_signal_shape(bars_5m)


async def fetch_daily_atr14(symbol: str, date_key: str) -> Optional[float]:
    """Simple 14-day mean of Daily High - Daily Low, ending the day
    BEFORE date_key (today's own daily bar isn't closed yet at the
    10:00 ET lock -- using it would leak an incomplete range)."""
    return await asyncio.to_thread(_fetch_daily_atr14_sync, symbol, date_key)
