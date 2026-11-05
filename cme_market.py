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

import datetime
import os
from typing import Any, Dict, List

import cme_clock
import cme_databento_client


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


async def fetch_calibration_1m(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    """Real 1-minute bars for `date_key`'s 9:30-10:00 ET calibration
    window, in the shape cme_signals.compute_tpo_value_area() expects."""
    client = cme_databento_client.DatabentoClient(_require_api_key())
    year, month, day = (int(x) for x in date_key.split("-"))
    start_utc = _ny_local_to_utc(year, month, day, cme_clock.OPEN_H, cme_clock.OPEN_M)
    end_utc = _ny_local_to_utc(year, month, day, cme_clock.LOCK_H, cme_clock.LOCK_M)
    bars = await client.get_ohlcv_1m_bars(symbol, start_utc, end_utc)
    return _strip_to_signal_shape(bars)


async def fetch_confirmed_5m(symbol: str, since_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Confirmed (fully closed) 5-minute bars for `symbol` since
    `since_utc`, in the shape cme_signals.check_cross()/
    check_retest_fill() expect. Fetches 1-minute bars from Databento and
    aggregates -- see _aggregate_to_confirmed_5m()'s own docstring."""
    client = cme_databento_client.DatabentoClient(_require_api_key())
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    bars_1m = await client.get_ohlcv_1m_bars(symbol, since_utc, now_utc)
    confirmed_5m = _aggregate_to_confirmed_5m(bars_1m, now_utc)
    return _strip_to_signal_shape(confirmed_5m)
