# cme_market.py
# ==============================================================================
# CME MARKET DATA -- fetches real-time confirmed candles for D1 (1-minute
# calibration bars), D2 (5-minute breakout cross/retest bars), and D3
# (5-minute ratchet monitoring).
#
# CrossTrade zero-lag venue-true feed (2026-11-10): every fetch path reads
# CME bars from CrossTrade's GetBars RPC (POST /v1/api/market/bars), which
# queries the NinjaTrader 8 add-on streaming on the St. Louis VPS. This
# replaces Architecture A's delayed yfinance continuous-futures proxy
# (measured ~10-minute Lag, unacceptable for D2 cross confirmation and
# D3 ratchet management).
#
# NO FALLBACK. If CrossTrade is unconfigured (no CROSSTRADE_BEARER_TOKEN
# and no active crosstrade ExecutorAccount) or its GetBars call errors,
# these functions RAISE a clear RuntimeError instead of silently
# substituting delayed yfinance data. A day that can't be fed zero-lag
# data must stand down loudly (MISSED_WINDOW), never trade on stale bars.
# ==============================================================================

from __future__ import annotations

import datetime
import os
from typing import Any, Dict, List, Optional

import cme_clock
import cme_contracts
import cme_crosstrade_client


def _get_crosstrade_client() -> Optional[cme_crosstrade_client.CrossTradeClient]:
    token = os.environ.get("CROSSTRADE_BEARER_TOKEN")
    if token:
        return cme_crosstrade_client.CrossTradeClient(bearer_token=token, crosstrade_account_id="")
    try:
        import database
        import executor_crypto
        db = database.SessionLocal()
        try:
            acc = db.query(database.ExecutorAccount).filter(
                database.ExecutorAccount.execution_provider == "crosstrade",
                database.ExecutorAccount.is_active == True
            ).first()
            if acc and acc.api_key_encrypted:
                decrypted = executor_crypto.decrypt_secret(acc.api_key_encrypted)
                return cme_crosstrade_client.CrossTradeClient(
                    bearer_token=decrypted,
                    crosstrade_account_id=acc.crosstrade_account_id or ""
                )
        finally:
            db.close()
    except Exception:
        pass
    return None


def _require_crosstrade_client() -> cme_crosstrade_client.CrossTradeClient:
    client = _get_crosstrade_client()
    if client is None:
        raise RuntimeError(
            "CrossTrade market-data client unavailable (no CROSSTRADE_BEARER_TOKEN and no "
            "active crosstrade ExecutorAccount) -- refusing to trade on delayed yfinance; "
            "configure the CrossTrade VPS feed first."
        )
    return client


def _strip_to_signal_shape(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """cme_signals.py's functions only ever read open/high/low/close --
    drops timestamps and extra metadata."""
    return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]} for b in bars]


async def fetch_calibration_1m(symbol: str, date_key: str) -> List[Dict[str, Any]]:
    """Real 1-minute bars for `date_key`'s 9:30-10:00 ET calibration
    window, in the shape cme_signals.compute_tpo_value_area() expects,
    from CrossTrade's zero-lag GetBars feed. RAISES if CrossTrade is
    unconfigured or returns no bars for the window -- no delayed-feed
    fallback exists (the D1 caller turns this into a MISSED_WINDOW row)."""
    client = _require_crosstrade_client()
    instrument = cme_contracts.resolve_contract(symbol)
    bars = await client.get_market_bars(instrument, period_type="minute", period=1, days_back=2, limit=150)
    cal_bars: List[Dict[str, Any]] = []
    for b in bars:
        t = b.get("time")
        if not t:
            continue
        t_ny = t.astimezone(cme_clock.NY_TZ)
        if (
            t_ny.strftime("%Y-%m-%d") == date_key
            and t_ny.time() >= datetime.time(cme_clock.OPEN_H, cme_clock.OPEN_M)
            and t_ny.time() < datetime.time(cme_clock.LOCK_H, cme_clock.LOCK_M)
        ):
            cal_bars.append({
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
            })
    if not cal_bars:
        raise RuntimeError(f"Failed to fetch calibration 1m bars for {symbol} on {date_key} via CrossTrade")
    return cal_bars


async def fetch_confirmed_5m(symbol: str, since_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Confirmed (fully closed) 5-minute bars for `symbol` since `since_utc`,
    in the shape cme_signals.check_cross()/check_retest_fill() expect, from
    CrossTrade's zero-lag GetBars feed (native 5m bars -- no 1m->5m
    aggregation). RAISES if CrossTrade is unconfigured or its call errors.
    An empty window (no bars yet since `since_utc`, including CrossTrade's
    HTTP-408 "NT8 disconnected" quiet gap) returns [] so the D2/D3 watch
    loop retries on its next 30s tick rather than crashing."""
    client = _require_crosstrade_client()
    instrument = cme_contracts.resolve_contract(symbol)
    bars = await client.get_market_bars(instrument, period_type="minute", period=5, days_back=1, limit=50)
    if since_utc.tzinfo is None:
        since_utc = since_utc.replace(tzinfo=datetime.timezone.utc)
    matching = [b for b in bars if b.get("time") and b["time"] >= since_utc]
    return _strip_to_signal_shape(matching)


async def fetch_daily_atr14(symbol: str, date_key: str) -> Optional[float]:
    """Simple 14-day mean of Daily High - Daily Low, ending the day
    BEFORE date_key (today's own daily bar isn't closed yet at the
    10:00 ET lock -- using it would leak an incomplete range). Reads
    CrossTrade's GetBars daily bars (periodType="day") -- no delayed
    yfinance fallback. Returns None only on legitimately insufficient
    history (<14 prior daily bars), which the caller treats as a safe
    stand-down; RAISES when CrossTrade is unconfigured or its call fails."""
    client = _require_crosstrade_client()
    instrument = cme_contracts.resolve_contract(symbol)
    bars = await client.get_market_bars(instrument, period_type="day", period=1, days_back=30, limit=30)
    prior: List[Dict[str, Any]] = []
    for b in bars:
        t = b.get("time")
        if not t:
            continue
        if t.astimezone(cme_clock.NY_TZ).strftime("%Y-%m-%d") < date_key:
            prior.append(b)
    if len(prior) < 14:
        return None
    last14 = prior[-14:]
    ranges = [float(b["high"]) - float(b["low"]) for b in last14]
    return sum(ranges) / len(ranges)
