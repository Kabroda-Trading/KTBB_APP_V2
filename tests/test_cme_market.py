"""Coverage for cme_market.py's own logic under Architecture A -- the
continuous-feed (yfinance) market-data engine adopted 2026-11-09 to remove
the Databento historical REST dependency from the live execution path (see
AGENT_LOG.md Session 9).

Covered here:
  * 1-minute -> 5-minute aggregation (_aggregate_to_confirmed_5m), the
    "confirmed == fully closed bucket" contract;
  * the 09:30-10:00 ET calibration window isolation (_fetch_calibration_1m_sync);
  * the daily ATR14 simple-mean math and "never fabricate a fallback on
    failure" contract (_fetch_daily_atr14_sync / fetch_daily_atr14);
  * the async public wrappers' empty-result error handling.

yfinance's HTTP I/O is deliberately NOT mocked (this codebase has no
network-mock precedent for exchange/data clients) -- the _download_1m_bars
seam is monkeypatched instead, so these exercise cme_market.py's own logic
the same way cme_tradovate_client.py/cme_crosstrade_client.py are only
tested at the route/branching level.
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import pandas as pd

import cme_market as cm

UTC = datetime.timezone.utc


def _bar(ts, o, h, l, c):
    return {"ts_event": ts, "open": o, "high": h, "low": l, "close": c}


def _df(rows):
    """rows: list of (ts_datetime, open, high, low, close)."""
    index = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
        },
        index=index,
    )


# ------------------------------------------------------------------ _aggregate_to_confirmed_5m

def test_aggregate_groups_five_one_minute_bars_into_one_five_minute_bar():
    base = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)   # 10:00 ET lock instant
    bars_1m = [
        _bar(base + datetime.timedelta(minutes=0), 100.0, 101.0, 99.5, 100.5),
        _bar(base + datetime.timedelta(minutes=1), 100.5, 102.0, 100.0, 101.5),
        _bar(base + datetime.timedelta(minutes=2), 101.5, 101.8, 101.0, 101.2),
        _bar(base + datetime.timedelta(minutes=3), 101.2, 103.0, 101.0, 102.8),
        _bar(base + datetime.timedelta(minutes=4), 102.8, 103.5, 102.5, 103.0),
    ]
    now_utc = base + datetime.timedelta(minutes=10)   # well past the bucket's close
    result = cm._aggregate_to_confirmed_5m(bars_1m, now_utc)
    assert len(result) == 1
    bar = result[0]
    assert bar["open"] == 100.0      # first bar's open
    assert bar["close"] == 103.0     # last bar's close
    assert bar["high"] == 103.5      # max of all highs
    assert bar["low"] == 99.5        # min of all lows


def test_aggregate_excludes_an_in_progress_bucket():
    base = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    bars_1m = [_bar(base, 100.0, 101.0, 99.5, 100.5)]
    now_utc = base + datetime.timedelta(minutes=2)   # bucket (15:00-15:05) hasn't closed yet
    result = cm._aggregate_to_confirmed_5m(bars_1m, now_utc)
    assert result == []   # never returns an unconfirmed/in-progress bar


def test_aggregate_includes_a_bucket_the_instant_it_closes():
    base = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    bars_1m = [_bar(base, 100.0, 101.0, 99.5, 100.5)]
    now_utc = base + datetime.timedelta(minutes=5)   # exactly at the bucket's close instant
    result = cm._aggregate_to_confirmed_5m(bars_1m, now_utc)
    assert len(result) == 1


def test_aggregate_handles_multiple_consecutive_confirmed_buckets_in_order():
    base = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    bars_1m = [_bar(base + datetime.timedelta(minutes=i), 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i) for i in range(12)]
    now_utc = base + datetime.timedelta(minutes=14)   # two full 5m buckets confirmed, third (10-14) still in progress
    result = cm._aggregate_to_confirmed_5m(bars_1m, now_utc)
    assert len(result) == 2
    assert result[0]["open"] == 100.0
    assert result[1]["open"] == 105.0


def test_aggregate_floors_to_the_real_five_minute_boundary_not_the_first_bar():
    # Bars starting mid-bucket (e.g. a fetch beginning at :02) must still
    # group onto the real :00-:05 wall-clock boundary, not a boundary
    # relative to whichever bar happened to arrive first.
    base = datetime.datetime(2026, 11, 4, 15, 2, tzinfo=UTC)
    bars_1m = [
        _bar(base, 100.0, 101.0, 99.5, 100.5),
        _bar(base + datetime.timedelta(minutes=1), 100.5, 102.0, 100.0, 101.5),
    ]
    now_utc = base + datetime.timedelta(minutes=10)
    result = cm._aggregate_to_confirmed_5m(bars_1m, now_utc)
    assert len(result) == 1
    assert result[0]["open"] == 100.0
    assert result[0]["close"] == 101.5


# ------------------------------------------------------------------ _strip_to_signal_shape

def test_strip_to_signal_shape_drops_ts_event():
    bars = [_bar(datetime.datetime.now(UTC), 1.0, 2.0, 0.5, 1.5)]
    result = cm._strip_to_signal_shape(bars)
    assert result == [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]


# ------------------------------------------------------------------ _fetch_calibration_1m_sync

def test_calibration_isolates_the_0930_to_1000_et_window(monkeypatch):
    # Bars from 09:20 through 10:09 ET -- only 09:30 <= t < 10:00 ET survive.
    # 2026-11-04 is EST (UTC-5), so 09:20 ET == 14:20 UTC.
    date_key = "2026-11-04"
    rows = []
    t = datetime.datetime(2026, 11, 4, 14, 20, tzinfo=UTC)   # 09:20 ET (EST)
    for i in range(50):   # 50 minutes: 09:20 -> 10:09 ET
        ts = t + datetime.timedelta(minutes=i)
        rows.append((ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i))

    monkeypatch.setattr(cm, "_download_1m_bars", lambda symbol: _df(rows))
    bars = cm._fetch_calibration_1m_sync("MNQ", date_key)

    # 30 bars: 09:30 .. 09:59 ET inclusive; 10:00 ET excluded.
    assert len(bars) == 30
    # first surviving bar is 09:30 ET (i=10), last is 09:59 ET (i=39).
    assert bars[0]["open"] == 100.0 + 10
    assert bars[-1]["open"] == 100.0 + 39


def test_calibration_returns_empty_on_no_data(monkeypatch):
    monkeypatch.setattr(cm, "_download_1m_bars", lambda symbol: None)
    assert cm._fetch_calibration_1m_sync("MNQ", "2026-11-04") == []


# ------------------------------------------------------------------ _fetch_daily_atr14_sync

def _fake_daily_download(rows):
    def _download(*args, **kwargs):
        return _df(rows)
    return _download


def test_daily_atr14_computes_the_simple_mean_range(monkeypatch):
    # 15 daily bars strictly before date_key; last 14 have ranges 2..15 -> mean 8.5.
    date_key = "2026-11-05"
    rows = []
    base = datetime.datetime(2026, 10, 20, tzinfo=UTC)
    for i in range(15):
        rows.append((base + datetime.timedelta(days=i), 100.0, 100.0 + (i + 1), 100.0, 100.0))

    monkeypatch.setattr(cm.yf, "download", _fake_daily_download(rows))
    result = cm._fetch_daily_atr14_sync("MNQ", date_key)
    # All 15 bars are before date_key, so last 14 = ranges 2..15 -> mean 8.5.
    assert result == pytest.approx(8.5)


def test_daily_atr14_excludes_todays_forming_bar(monkeypatch):
    # Include a bar exactly ON date_key -- it must be excluded from the mean.
    date_key = "2026-11-05"
    rows = []
    base = datetime.datetime(2026, 10, 20, tzinfo=UTC)
    for i in range(14):
        rows.append((base + datetime.timedelta(days=i), 100.0, 100.0 + (i + 1), 100.0, 100.0))
    # a 15th bar ON date_key (range 999) -- must NOT be counted.
    rows.append((datetime.datetime(2026, 11, 5, tzinfo=UTC), 100.0, 1099.0, 100.0, 100.0))

    monkeypatch.setattr(cm.yf, "download", _fake_daily_download(rows))
    result = cm._fetch_daily_atr14_sync("MNQ", date_key)
    # Only the 14 pre-date_key bars (ranges 1..14) -> mean 7.5.
    assert result == pytest.approx(7.5)


def test_daily_atr14_returns_none_on_too_few_bars_never_fabricates(monkeypatch):
    monkeypatch.setattr(cm.yf, "download", _fake_daily_download([]))
    assert cm._fetch_daily_atr14_sync("MNQ", "2026-11-05") is None


def test_daily_atr14_returns_none_on_fetch_failure_never_fabricates(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated download outage")
    monkeypatch.setattr(cm.yf, "download", _raise)
    # _fetch_daily_atr14_sync catches and returns None on failure.
    assert cm._fetch_daily_atr14_sync("MNQ", "2026-11-05") is None


# ------------------------------------------------------------------ async public wrappers

def test_fetch_calibration_1m_raises_when_no_bars_returned(monkeypatch):
    monkeypatch.setattr(cm, "_download_1m_bars", lambda symbol: None)
    with pytest.raises(RuntimeError, match="Failed to fetch calibration"):
        asyncio.run(cm.fetch_calibration_1m("MNQ", "2026-11-04"))


def test_fetch_daily_atr14_computes_mean_via_async_wrapper(monkeypatch):
    date_key = "2026-11-05"
    rows = []
    base = datetime.datetime(2026, 10, 20, tzinfo=UTC)
    for i in range(14):
        rows.append((base + datetime.timedelta(days=i), 100.0, 100.0 + (i + 1), 100.0, 100.0))
    monkeypatch.setattr(cm.yf, "download", _fake_daily_download(rows))
    assert asyncio.run(cm.fetch_daily_atr14("MNQ", date_key)) == pytest.approx(7.5)
