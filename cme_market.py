# cme_market.py
# ==============================================================================
# CME MARKET DATA -- fetches confirmed candles for D1 (1-minute calibration
# bars) and D2/D3 (5-minute bars). Originally intended to come straight from
# Tradovate's own quote feed for the actual traded contract, per this
# build's own Part-0 finding: the one live daemon script in the Kabroda AI
# Brain repo computed its decision inputs from yfinance's free, delayed
# NQ=F continuous proxy instead -- the same class of decision-feed-vs-
# execution-venue mismatch already found and ruled on once for BTC
# (CC_INTERFACE.md item L2, Kraken vs Bitunix).
#
# **RESOLVED 2026-11-04, but NOT via direct Tradovate -- a real, confirmed
# gate, not a guess.** Tradovate's own docs show the trading accessToken
# and the market-data mdAccessToken come from the SAME /auth/
# accesstokenrequest call. That call is confirmed blocked outright for this
# account (unfunded/evaluation accounts get neither token -- see
# cme_tradovate_client.py's last_auth_error handling and AGENT_LOG.md, both
# repos). If the account can't get a trading token, it can't get a
# market-data token either -- not a separate problem to solve, the SAME one.
# CrossTrade (the execution path that DOES work for this account) has no
# market-data surface on its Tradovate-linked side either -- confirmed
# directly against its real OpenAPI spec and full endpoint list; bars/
# quotes exist only on CrossTrade's NT8-add-on surface, which would require
# a persistently-running NinjaTrader desktop -- ruled out the same day
# ("we're not using the NinjaTrader desktop or nothing").
#
# **The actual source: Databento** (cme_databento_client.py), an officially
# CME-recognized data vendor, independent of the Tradovate/CrossTrade
# brokerage relationship entirely -- confirmed against Databento's own docs,
# not fabricated. See that module's own header for the full confirmed
# spec (dataset/schema/symbology/field names) and the one remaining
# NOT YET LIVE-VERIFIED detail (the exact raw HTTP endpoint shape).
#
# Deliberately NOT scoped to an ExecutorAccount's own client the way the
# original design assumed (both fetch functions below used to take a
# `client` parameter derived from cme_executor._client_for(account)) --
# market data for MES/MNQ doesn't depend on which Tradovate/CrossTrade
# account is trading it, so these now take symbol/date identifiers only.
# Three call sites in cme_engine.py were updated to match (no longer
# constructing a trading client just to throw it away for this purpose).
# ==============================================================================

from __future__ import annotations

import asyncio
import datetime
import os
from typing import Any, Dict, List, Optional

import cme_clock
import cme_databento_client
import yfinance as yf


def _require_api_key() -> str:
    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DATABENTO_API_KEY not set -- cannot fetch real market data. "
            "See CLAUDE.md's Required Environment Variables."
        )
    return api_key


def _ny_local_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime.datetime:
    """Same DST-correct technique as cme_clock.py's own _local_today_at()
    (pytz localize -> astimezone), parameterized by an explicit calendar
    date instead of 'today relative to now_utc' -- needed here because the
    caller passes date_key (the lock's own calendar date), which may not
    be the same NY calendar date as whatever moment this function happens
    to run at."""
    local_naive = datetime.datetime(year, month, day, hour, minute)
    local_aware = cme_clock.NY_TZ.localize(local_naive)
    return local_aware.astimezone(datetime.timezone.utc)


def _strip_to_signal_shape(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """cme_signals.py's functions only ever read open/high/low/close --
    drops the ts_event field cme_databento_client.py's bars carry (needed
    here for windowing/aggregation, not downstream)."""
    return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]} for b in bars]


def _aggregate_to_confirmed_5m(bars_1m: List[Dict[str, Any]], now_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Databento has no native 5-minute schema (only ohlcv-1s/1m/1h/1d) --
    aggregates 1-minute bars into 5-minute buckets aligned to real
    wall-clock 5-minute boundaries (flooring in UTC is equivalent to
    flooring in ET here -- the US ET/UTC offset is always a whole number
    of hours, never a fraction of 5 minutes, so no DST-aware conversion is
    needed for THIS step, only for the original window selection).
    CONFIRMED means fully closed: a bucket is only included once its own
    5-minute window has fully elapsed relative to now_utc -- never returns
    an in-progress bar, matching check_cross()/check_retest_fill()'s own
    'confirmed bars only' contract."""
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


def _fetch_calibration_1m_yfinance(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    """Real-time 1-minute bars fallback via yfinance continuous proxy
    (MNQ=F / MES=F, fallback NQ=F / ES=F).
    Required because Databento Historical REST API operates on a ~20-minute batch
    processing delay during market hours, causing 422 data_end_after_available_end."""
    ticker = "MNQ=F" if "MNQ" in symbol.upper() else "MES=F"
    try:
        df = yf.download(ticker, period="5d", interval="1m", progress=False)
    except Exception as e:
        print(f"|| CME MARKET || yfinance download failed for {ticker}: {e}")
        df = None

    if df is None or df.empty:
        alt_ticker = "NQ=F" if "MNQ" in symbol.upper() else "ES=F"
        try:
            df = yf.download(alt_ticker, period="5d", interval="1m", progress=False)
        except Exception as e:
            print(f"|| CME MARKET || yfinance download failed for {alt_ticker}: {e}")
            return []

    if df is None or df.empty:
        return []

    if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)

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


def _fetch_confirmed_5m_yfinance(symbol: str, since_utc: datetime.datetime, now_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Real-time confirmed 5-minute bars fallback via yfinance.
    Fetches 1m bars from yfinance and aggregates them using _aggregate_to_confirmed_5m."""
    ticker = "MNQ=F" if "MNQ" in symbol.upper() else "MES=F"
    try:
        df = yf.download(ticker, period="5d", interval="1m", progress=False)
    except Exception as e:
        print(f"|| CME MARKET || yfinance 5m download failed for {ticker}: {e}")
        df = None

    if df is None or df.empty:
        alt_ticker = "NQ=F" if "MNQ" in symbol.upper() else "ES=F"
        try:
            df = yf.download(alt_ticker, period="5d", interval="1m", progress=False)
        except Exception as e:
            print(f"|| CME MARKET || yfinance 5m download failed for {alt_ticker}: {e}")
            return []

    if df is None or df.empty:
        return []

    if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)

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


async def fetch_calibration_1m(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    """Real 1-minute bars for `date_key`'s 9:30-10:00 ET calibration
    window, in the shape cme_signals.compute_tpo_value_area() expects."""
    try:
        client = cme_databento_client.DatabentoClient(_require_api_key())
        year, month, day = (int(x) for x in date_key.split("-"))
        start_utc = _ny_local_to_utc(year, month, day, cme_clock.OPEN_H, cme_clock.OPEN_M)
        end_utc = _ny_local_to_utc(year, month, day, cme_clock.LOCK_H, cme_clock.LOCK_M)
        bars = await client.get_ohlcv_1m_bars(symbol, start_utc, end_utc)
        if len(bars) >= 20:
            return _strip_to_signal_shape(bars)
        print(f"|| CME MARKET || Databento returned only {len(bars)} bars for {symbol} {date_key} -- attempting yfinance fallback")
    except Exception as e:
        print(f"|| CME MARKET || Databento calibration fetch failed for {symbol}: {e} -- falling back to yfinance")

    bars_yf = await asyncio.to_thread(_fetch_calibration_1m_yfinance, symbol, date_key)
    if not bars_yf:
        raise RuntimeError(f"Failed to fetch calibration 1m bars for {symbol} {date_key} from both Databento and yfinance")
    return bars_yf


async def fetch_confirmed_5m(symbol: str, since_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Confirmed (fully closed) 5-minute bars for `symbol` since
    `since_utc`, in the shape cme_signals.check_cross()/
    check_retest_fill() expect. Fetches 1-minute bars from Databento and
    aggregates, with yfinance real-time fallback on Databento lag or failure."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        client = cme_databento_client.DatabentoClient(_require_api_key())
        bars_1m = await client.get_ohlcv_1m_bars(symbol, since_utc, now_utc)
        confirmed_5m = _aggregate_to_confirmed_5m(bars_1m, now_utc)
        if confirmed_5m:
            return _strip_to_signal_shape(confirmed_5m)
        print(f"|| CME MARKET || Databento returned 0 confirmed 5m bars for {symbol} -- attempting yfinance fallback")
    except Exception as e:
        print(f"|| CME MARKET || Databento 5m fetch failed for {symbol}: {e} -- falling back to yfinance")

    confirmed_5m_yf = await asyncio.to_thread(_fetch_confirmed_5m_yfinance, symbol, since_utc, now_utc)
    return _strip_to_signal_shape(confirmed_5m_yf)


async def fetch_daily_atr14(symbol: str, date_key: str) -> Optional[float]:
    """Simple 14-day mean of Daily High - Daily Low, ending the day
    BEFORE date_key (today's own daily bar isn't closed yet at the
    10:00 ET lock -- using it would leak an incomplete range). Matches
    the Brain repo's market_data._calc_daily_atr14 definition exactly:
    a SIMPLE mean, NOT Wilder's smoothed ATR (CLAUDE.md's own
    stop_planner.py note already establishes this convention for this
    codebase -- do not swap to Wilder's without a fresh ruling).

    2026-11-05 -- added to close a real gap found live, same day: D1's
    cme_engine.py call site had been passing a hardcoded daily_atr14=
    None since this module's Databento rebuild, which forces EVERY lock
    to STAND_DOWN_UNREACHABLE via the reachability safety sentinel
    (999.0 > REACHABILITY_MAX) -- a real trading day silently lost, not
    a cosmetic bug. On any fetch failure or fewer than 14 real daily
    bars, returns None -- callers must keep the SAME safe stand-down
    behavior on None, never fabricate a fallback constant instead."""
    client = cme_databento_client.DatabentoClient(_require_api_key())
    year, month, day = (int(x) for x in date_key.split("-"))
    end_utc = _ny_local_to_utc(year, month, day, 0, 0)   # midnight ET on date_key -- excludes today's own incomplete daily bar
    start_utc = end_utc - datetime.timedelta(days=21)    # generous window to guarantee >=14 real trading days (weekends/holidays)
    try:
        bars = await client.get_ohlcv_1m_bars(symbol, start_utc, end_utc, schema="ohlcv-1d")
    except Exception as e:
        print(f"|| CME MARKET || daily ATR14 fetch failed for {symbol}: {e}")
        return None
    if len(bars) < 14:
        return None
    last14 = bars[-14:]
    ranges = [b["high"] - b["low"] for b in last14]
    return sum(ranges) / len(ranges)
