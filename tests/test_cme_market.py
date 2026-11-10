"""Coverage for cme_market.py's own logic under the CrossTrade zero-lag
feed (2026-11-10) -- the delayed yfinance continuous proxy (Architecture A)
has been removed entirely.

Covered here:
  * fetch_calibration_1m: prefers CrossTrade, isolates the 09:30-10:00 ET
    calibration window, and RAISES when CrossTrade is unconfigured or
    returns no bars for the window (no delayed-feed fallback).
  * fetch_confirmed_5m: slices CrossTrade's native 5m bars by since_utc
    and returns [] on an empty window (never crashes the watch loop).
  * fetch_daily_atr14: computes the simple 14-day mean of prior daily
    bars; returns None on <14 prior bars; RAISES when unconfigured.
  * _require_crosstrade_client: hard-fails when no token/account exists.

CrossTrade's HTTP I/O is deliberately NOT mocked -- the
CrossTradeClient.get_market_bars() seam is monkeypatched instead, the same
way cme_tradovate_client.py/cme_crosstrade_client.py are only tested at
the route/branching level.
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_market as cm

UTC = datetime.timezone.utc


def _mk_time(dt):
    """Return a tz-aware datetime, coercing naive -> UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _bar(time, o, h, l, c):
    return {"time": _mk_time(time), "open": o, "high": h, "low": l, "close": c}


def _no_client(monkeypatch):
    monkeypatch.setattr(cm, "_get_crosstrade_client", lambda: None)


def _fake_client(monkeypatch, bars_by_call):
    """Install a fake _get_crosstrade_client whose get_market_bars returns
    the next queued value (or raises it) from bars_by_call, capturing kwargs.
    Also pins resolve_contract so instrument-string assertions don't depend
    on wall-clock rollover."""
    calls = []

    class _C:
        async def get_market_bars(self, *a, **kw):
            calls.append({"args": a, "kwargs": kw})
            item = bars_by_call.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    fake = _C()
    monkeypatch.setattr(cm, "_get_crosstrade_client", lambda: fake)
    monkeypatch.setattr(cm.cme_contracts, "resolve_contract", lambda root: "MNQ 12-26")
    return calls


# ------------------------------------------------------------------ _require_crosstrade_client

def test_require_crosstrade_client_raises_when_unconfigured(monkeypatch):
    _no_client(monkeypatch)
    with pytest.raises(RuntimeError, match="unavailable"):
        cm._require_crosstrade_client()


def test_require_crosstrade_client_returns_client_when_present(monkeypatch):
    fake = object()
    monkeypatch.setattr(cm, "_get_crosstrade_client", lambda: fake)
    assert cm._require_crosstrade_client() is fake


# ------------------------------------------------------------------ fetch_calibration_1m

def test_fetch_calibration_1m_isolates_0930_to_1000_et_window(monkeypatch):
    date_key = "2026-11-04"
    # 09:29, 09:30, 09:59, 10:00 ET == 14:29, 14:30, 14:59, 15:00 UTC (EST).
    bars = [
        _bar(datetime.datetime(2026, 11, 4, 14, 29, tzinfo=UTC), 1.0, 1.0, 1.0, 1.0),   # 09:29 ET -> excluded
        _bar(datetime.datetime(2026, 11, 4, 14, 30, tzinfo=UTC), 10.0, 11.0, 9.0, 10.5),  # 09:30 ET -> included
        _bar(datetime.datetime(2026, 11, 4, 14, 59, tzinfo=UTC), 20.0, 21.0, 19.0, 20.5),  # 09:59 ET -> included
        _bar(datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC), 30.0, 30.0, 30.0, 30.0),   # 10:00 ET -> excluded
    ]
    calls = _fake_client(monkeypatch, [bars])
    result = asyncio.run(cm.fetch_calibration_1m("MNQ", date_key))
    assert [b["open"] for b in result] == [10.0, 20.0]
    # CrossTrade call used the dated instrument and 1-minute period.
    assert calls == [{"args": ("MNQ 12-26",),
                      "kwargs": {"period_type": "minute", "period": 1, "days_back": 2, "limit": 150}}]


def test_fetch_calibration_1m_raises_when_unconfigured(monkeypatch):
    _no_client(monkeypatch)
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(cm.fetch_calibration_1m("MNQ", "2026-11-04"))


def test_fetch_calibration_1m_raises_when_no_bars_in_window(monkeypatch):
    # Bars exist but none fall inside 09:30-10:00 ET on date_key.
    _fake_client(monkeypatch, [[
        _bar(datetime.datetime(2026, 11, 4, 13, 0, tzinfo=UTC), 1.0, 1.0, 1.0, 1.0),
    ]])
    with pytest.raises(RuntimeError, match="Failed to fetch calibration"):
        asyncio.run(cm.fetch_calibration_1m("MNQ", "2026-11-04"))


def test_fetch_calibration_1m_propagates_crosstrade_error(monkeypatch):
    _fake_client(monkeypatch, [RuntimeError("market bars query failed (429)")])
    with pytest.raises(RuntimeError, match="market bars query failed"):
        asyncio.run(cm.fetch_calibration_1m("MNQ", "2026-11-04"))


# ------------------------------------------------------------------ fetch_confirmed_5m

def test_fetch_confirmed_5m_slices_by_since_utc_and_strips(monkeypatch):
    since = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    bars = [
        _bar(datetime.datetime(2026, 11, 4, 14, 55, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5),   # before since -> excluded
        _bar(datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC), 10.0, 11.0, 9.0, 10.5),  # at since -> included
        _bar(datetime.datetime(2026, 11, 4, 15, 5, tzinfo=UTC), 20.0, 21.0, 19.0, 20.5),  # after -> included
    ]
    _fake_client(monkeypatch, [bars])
    result = asyncio.run(cm.fetch_confirmed_5m("MNQ", since))
    assert result == [
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5},
        {"open": 20.0, "high": 21.0, "low": 19.0, "close": 20.5},
    ]


def test_fetch_confirmed_5m_empty_window_returns_empty(monkeypatch):
    since = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    _fake_client(monkeypatch, [[]])   # CrossTrade 408 quiet gap -> [] bars
    assert asyncio.run(cm.fetch_confirmed_5m("MNQ", since)) == []


def test_fetch_confirmed_5m_raises_when_unconfigured(monkeypatch):
    _no_client(monkeypatch)
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(cm.fetch_confirmed_5m("MNQ", datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)))


# ------------------------------------------------------------------ fetch_daily_atr14

def test_fetch_daily_atr14_computes_simple_mean_of_prior_days(monkeypatch):
    date_key = "2026-11-05"
    # 14 prior daily bars with ranges 1..14 -> mean 7.5, plus one bar ON
    # date_key (excluded). All bars use 17:00 UTC (noon ET) so they stay on
    # their own NY calendar day regardless of DST.
    bars = []
    base = datetime.datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
    for i in range(14):
        bars.append(_bar(base + datetime.timedelta(days=i), 100.0, 100.0 + (i + 1), 100.0, 100.0))
    bars.append(_bar(datetime.datetime(2026, 11, 5, 17, 0, tzinfo=UTC), 100.0, 1099.0, 100.0, 100.0))  # ON date_key -> excluded
    _fake_client(monkeypatch, [bars])
    assert asyncio.run(cm.fetch_daily_atr14("MNQ", date_key)) == pytest.approx(7.5)


def test_fetch_daily_atr14_returns_none_on_fewer_than_14_prior_bars(monkeypatch):
    date_key = "2026-11-05"
    _fake_client(monkeypatch, [[_bar(datetime.datetime(2026, 11, 4, 17, 0, tzinfo=UTC), 100.0, 110.0, 100.0, 100.0)]])
    assert asyncio.run(cm.fetch_daily_atr14("MNQ", date_key)) is None


def test_fetch_daily_atr14_raises_when_unconfigured(monkeypatch):
    _no_client(monkeypatch)
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(cm.fetch_daily_atr14("MNQ", "2026-11-05"))
