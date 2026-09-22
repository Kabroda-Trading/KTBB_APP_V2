# market_data.py
# ==============================================================================
# KABRODA MARKET DATA — shared data-fetching and calculation layer
# Extracted from battlebox_pipeline.py to break the circular import chain:
#   battlebox_pipeline → gravity_engine → mtf_confluence_scanner → battlebox_pipeline
# This module has ZERO dependencies on battlebox_pipeline, gravity_engine,
# or any other root-level module — it only depends on ccxt, aiohttp, and
# Python stdlib (aiohttp added 2026-09-22 for fetch_bitunix_4h() below --
# a third-party library, not a root-level module, so the real concern this
# header protects against -- a circular import through this project's own
# code -- still doesn't apply).
# ==============================================================================

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

import aiohttp
import ccxt.async_support as ccxt

# ---------------------------------------------------------------------------
# EXCHANGE CLIENT — one Kraken instance per asyncio event loop
# ---------------------------------------------------------------------------
# 2026-08-30 fix: this used to be a single module-level ccxt instance shared
# across every caller. ccxt's async client lazily binds its aiohttp
# ClientSession to whichever event loop first uses it. kabroda_mas_flow.py's
# run_mas_analysis() -- the function that writes CampaignLog/GateLog/the
# whole audit trail -- runs its own candle fetch via asyncio.run(_fetch_all())
# inside asyncio.to_thread() (by design: it needs its own fresh loop in a
# background thread). If the MAIN loop had already touched the shared client
# first (which it always does in production -- an HTTP request handler calls
# battlebox_pipeline.get_live_battlebox() and THAT fires the background
# thread), the background thread's fresh loop trying to reuse that same
# aiohttp session hangs indefinitely: no exception, no timeout, not even
# cancellable via asyncio.wait_for() (confirmed reproducible, twice, with a
# real live process -- the underlying OS thread stays blocked past 45s and
# has to be killed). Keying the client by the running loop (a WeakKeyDictionary
# so a loop's entry is dropped once the loop itself is garbage-collected)
# gives every event loop its own client, fixing the hang with zero call-site
# changes anywhere in the codebase. The client for a given loop is still
# reused across repeated calls within that same loop's lifetime -- this is
# not "recreate every call," it only creates a second client when a genuinely
# different loop shows up.
_exchange_clients: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_exchange_clients_lock = threading.Lock()


def _get_exchange():
    loop = asyncio.get_running_loop()
    with _exchange_clients_lock:
        client = _exchange_clients.get(loop)
        if client is None:
            client = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})
            _exchange_clients[loop] = client
        return client


class _ExchangeLiveProxy:
    """Backward-compatible attribute proxy -- existing code (this module's
    own fetch functions, battlebox_pipeline.py's re-export) refers to
    `_exchange_live.fetch_ohlcv(...)` etc.; this resolves to the correct
    per-loop client on every attribute access instead of a single fixed
    instance, so no call site needs to change."""

    def __getattr__(self, name):
        return getattr(_get_exchange(), name)


async def close_exchange_for_current_loop() -> None:
    """Closes and forgets the exchange client bound to the CURRENTLY running
    event loop, if one was created. The long-lived main event loop (serving
    HTTP requests) should never call this -- it wants its client to persist
    for the process lifetime. This exists for genuinely short-lived loops
    (e.g. kabroda_mas_flow.py's asyncio.run(_fetch_all()) inside
    asyncio.to_thread(), which creates a brand-new loop on every
    run_mas_analysis() call): without an explicit close, each such loop
    leaves its aiohttp connector unclosed when the loop exits (ccxt's own
    warning: "kraken requires ... an explicit call to the .close()
    coroutine"), leaking one open connection per session lock over the
    life of a long-running server. Safe to call even if no client was ever
    created for this loop."""
    loop = asyncio.get_running_loop()
    with _exchange_clients_lock:
        client = _exchange_clients.pop(loop, None)
    if client is not None:
        try:
            await client.close()
        except Exception:
            pass


_exchange_live = _ExchangeLiveProxy()


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
def _candle_values_differ(stored: tuple, fresh: tuple) -> bool:
    for a, b in zip(stored, fresh):
        if a is None or b is None:
            if a is not b:
                return True
            continue
        if abs(a - b) > 1e-9 * max(1.0, abs(b)):
            return True
    return False


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
                db.query(
                    CandleHistory.timestamp, CandleHistory.open, CandleHistory.high,
                    CandleHistory.low, CandleHistory.close, CandleHistory.volume,
                )
                .filter(
                    CandleHistory.symbol == symbol,
                    CandleHistory.timeframe == timeframe,
                    CandleHistory.timestamp >= min(timestamps),
                    CandleHistory.timestamp <= max(timestamps),
                )
                .all()
            )
            stored = {t: (o, h, l, c, v) for (t, o, h, l, c, v) in existing}
            new_rows = []
            refreshed = 0
            for r, ts in zip(rows, timestamps):
                fresh = (r["open"], r["high"], r["low"], r["close"], r["volume"])
                prior = stored.get(ts)
                if prior is None:
                    new_rows.append(CandleHistory(
                        symbol=symbol, timeframe=timeframe, timestamp=ts,
                        open=r["open"], high=r["high"], low=r["low"], close=r["close"], volume=r["volume"],
                    ))
                elif _candle_values_differ(prior, fresh):
                    # A bar is first seen while still forming, so its stored
                    # close is a mid-bar snapshot; insert-only left that
                    # snapshot in place forever (2026-09-21 audit). Refresh
                    # it whenever a later fetch carries different values, so
                    # the row converges on the bar's final OHLCV -- and any
                    # early-stored bar inside the fetched window self-heals.
                    db.query(CandleHistory).filter(
                        CandleHistory.symbol == symbol,
                        CandleHistory.timeframe == timeframe,
                        CandleHistory.timestamp == ts,
                    ).update({
                        "open": r["open"], "high": r["high"], "low": r["low"],
                        "close": r["close"], "volume": r["volume"],
                    })
                    refreshed += 1
            if new_rows:
                db.bulk_save_objects(new_rows)
            if new_rows or refreshed:
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


def confirmed_5m_closes(candles_5m: List[Dict[str, Any]], now_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Strips a trailing STILL-FORMING 5m candle, if present.

    2026-09-04 P0 (Kabroda AI Brain repo AGENT_LOG.md, DeepSeek + Andy):
    ccxt's fetch_ohlcv() returns the current, in-progress bar as its last
    row -- its "close" field is really the live/last-traded price,
    updating in real time as the bar forms, not a confirmed close. Every
    cross-detection consumer that reads candles_5m[-1]["close"] as if it
    were a finished bar's close was vulnerable to a wick momentarily
    poking through a trigger during a poll being read as a real, confirmed
    5m-close cross -- exactly what happened live: an 8:35 CT bar wicked
    through BD (low 78,973) but closed back above it (79,349, confirmed
    against real Kraken 5m OHLC), and the system evaluated it as a cross
    anyway because whatever candle was last in the fetched list at poll
    time got trusted regardless of whether its 5-minute window had
    actually elapsed.

    Callers that fetch 5m candles for CROSS/TRIGGER detection (decision_
    engine.py's side determination, fuel_gate.py's push-volume/NO_PUSH
    read, trade_plan_engine.py's live_price) MUST call this on the result
    before using the last candle's close for any trigger comparison.
    Callers that want the live/current price for DISPLAY purposes (e.g.
    a "current price" ticker) should NOT use this -- they want the
    unfiltered list, including the in-progress bar.

    A candle is "confirmed" once its full 5-minute window has elapsed:
    now_ts >= candle["time"] (open time, seconds) + 300. Drops at most the
    one trailing candle -- every earlier candle in the list is already a
    real historical bar with a real close, untouched.
    """
    return confirmed_closes(candles_5m, 300, now_ts)


def confirmed_closes(candles: List[Dict[str, Any]], interval_seconds: int, now_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Timeframe-generic form of confirmed_5m_closes(): strips a trailing
    STILL-FORMING candle of any interval (1h = 3600, 4h = 14400, ...).

    2026-09-21: fetch_live_1h()/fetch_live_4h() return ccxt's in-progress
    bar as their last row exactly like fetch_live_5m() does, and nothing
    stripped it -- so C5 ran on a live-price "close" mid-bar, an input the
    backtest (bar closes only) never used, and fired a real DRY_RUN exit on
    a 5-minute dip (AGENT_LOG 2026-09-21 10:15). A candle is confirmed once
    now_ts >= candle["time"] (open time, seconds) + interval_seconds.
    """
    if not candles:
        return candles
    if now_ts is None:
        now_ts = time.time()
    candle_open = candles[-1].get("time")
    if candle_open is not None and now_ts < candle_open + interval_seconds:
        return candles[:-1]
    return candles


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
# BITUNIX 4H FEED — BBWP-only (2026-09-22, CC_INTERFACE.md item 3, Andy
# ruling 2026-09-21 14:06 CT: "compute the live BBWP leg on Bitunix data...
# drop the dead Kraken-based BBWP path"). Kraken's ~721-bar depth cannot
# satisfy bbwp_series()'s 864-confirmed-bar floor (BBWP_PERIOD 96 +
# BBWP_LOOKBACK 768), so BBWP has been structurally dead (always False)
# since it shipped. Bitunix's own public kline endpoint has 260+ days of
# real history (verified live), so this feed is usable immediately, no
# ramp-up. BBWP-ONLY: do NOT repurpose this as a general 4H feed for C5 --
# nobody has ruled on moving C5 off Kraken; check_c5_or_bbwp() keeps C5 on
# the existing Kraken-sourced candles_4h and uses this feed for BBWP only.
#
# A duplicated BASE_URL (not an import of executor_bitunix_client) on
# purpose -- that module is per-account/credentialed and this is a public,
# credential-free endpoint; importing it would be a new, real cross-module
# dependency this module's own header explicitly disclaims. Small stable
# constant, same "duplicate rather than couple" convention this codebase
# already uses elsewhere (e.g. executor_live_e1_engine.py's own duplicated
# helpers).
# ---------------------------------------------------------------------------
_BITUNIX_BASE_URL = "https://fapi.bitunix.com"
_BITUNIX_KLINE_PATH = "/api/v1/futures/market/kline"
BITUNIX_BBWP_MIN_BARS = 865  # BBWP_PERIOD(96)+BBWP_LOOKBACK(768)=864 CONFIRMED
                              # closes needed; confirmed_closes() strips one
                              # possibly-forming bar first, so the raw fetch
                              # floor is 865, not 864.


async def _bitunix_kline_page(
    session: aiohttp.ClientSession, symbol: str, interval: str, limit: int,
    end_time_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """One page of Bitunix's public kline endpoint. Returns the raw `data`
    rows (still string-typed, still descending) on success, or [] on ANY
    failure -- network exception, a non-zero `code`, or a malformed body.
    Verified live (2026-09-22): a malformed request returns HTTP 200 with
    `{"code": 2, "data": None, "msg": "must not be null"}` -- a bare
    `except Exception` around a naive `for row in resp["data"]` would let
    the resulting TypeError degrade this to zero bars silently forever,
    recreating the exact "silently dead" failure this feed exists to fix,
    just on a different exchange. Checking `code` explicitly and logging
    loudly is the whole point."""
    params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    try:
        async with session.get(_BITUNIX_BASE_URL + _BITUNIX_KLINE_PATH, params=params,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            body = await resp.json()
    except Exception as e:
        print(f"[BITUNIX_4H] page request failed (network): {e}")
        return []
    if body.get("code") not in (0, None) or body.get("data") is None:
        print(f"[BITUNIX_4H] page request failed (code={body.get('code')} msg={body.get('msg')!r})")
        return []
    return body["data"]


async def fetch_bitunix_4h(symbol: str, target_bars: int = 900) -> List[Dict[str, Any]]:
    """Live 4H candles from Bitunix itself (BBWP's own feed only -- see the
    module note above). Paginates backward via `endTime` (verified live:
    the boundary is exact -- the bar AT endTime is excluded, so re-using
    the prior page's own oldest `time` as the next page's `endTime` produces
    no duplicates and no gaps) until `target_bars` raw bars are assembled, a
    page comes back shorter than the requested page size (history
    exhausted), or a page fails (partial results are kept, never discarded --
    real, already-fetched bars are worth more than an all-or-nothing retry).
    Returns ascending by time, same convention as every fetch_live_* above.
    Never raises -- a total failure returns []."""
    bitunix_symbol = (symbol or "").replace("/", "").upper()
    page_size = 200  # Bitunix's own documented max per request
    all_rows: List[Dict[str, Any]] = []
    end_time_ms: Optional[int] = None
    try:
        async with aiohttp.ClientSession() as session:
            while len(all_rows) < target_bars:
                page = await _bitunix_kline_page(session, bitunix_symbol, "4h", page_size, end_time_ms)
                if not page:
                    break
                all_rows.extend(page)
                oldest_ms = int(page[-1]["time"])
                if end_time_ms is not None and oldest_ms >= end_time_ms:
                    break  # safety: never spin if the exchange doesn't move backward
                end_time_ms = oldest_ms
                if len(page) < page_size:
                    break  # short page -- history exhausted
    except Exception as e:
        print(f"[BITUNIX_4H] fetch failed: {e}")
    if not all_rows:
        return []
    seen_ms = set()
    result = []
    for r in all_rows:
        ms = int(r["time"])
        if ms in seen_ms:
            continue
        seen_ms.add(ms)
        result.append({
            "time": ms // 1000,
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["baseVol"]),
        })
    result.sort(key=lambda c: c["time"])  # descending -> ascending
    if len(result) < BITUNIX_BBWP_MIN_BARS:
        print(f"[BITUNIX_4H] only {len(result)} bars fetched (need {BITUNIX_BBWP_MIN_BARS} for BBWP) -- "
              f"BBWP will stay undefined this poll")
    _persist_candles(_normalize_symbol(symbol), "4H_BITUNIX", result)
    return result


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
