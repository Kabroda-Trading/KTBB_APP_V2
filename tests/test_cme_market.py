"""Coverage for cme_market.py's own logic -- the NY-local-to-UTC window
computation (DST correctness, same category of bug this project has
already found and fixed once in cme_clock.py's own history) and the
1-minute -> 5-minute bar aggregation (Databento has no native 5-minute
schema). cme_databento_client.py's HTTP I/O is deliberately NOT mocked
here -- this codebase has no aiohttp-mocking precedent for any exchange/
data client (confirmed 2026-11-04 while building cme_crosstrade_client.py);
these tests exercise cme_market.py's own pure logic instead, the same way
cme_tradovate_client.py/cme_crosstrade_client.py are only tested at the
route/branching level."""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_databento_client as dbc
import cme_market as cm

UTC = datetime.timezone.utc


def _bar(ts, o, h, l, c):
    return {"ts_event": ts, "open": o, "high": h, "low": l, "close": c}


# ------------------------------------------------------------------ _ny_local_to_utc (DST correctness)

def test_ny_local_to_utc_resolves_edt_correctly():
    # 2026-07-15 is deep in EDT (UTC-4) -- 9:30 AM ET = 13:30 UTC
    result = cm._ny_local_to_utc(2026, 7, 15, 9, 30)
    assert result == datetime.datetime(2026, 7, 15, 13, 30, tzinfo=UTC)


def test_ny_local_to_utc_resolves_est_correctly():
    # 2026-01-15 is deep in EST (UTC-5) -- 9:30 AM ET = 14:30 UTC
    result = cm._ny_local_to_utc(2026, 1, 15, 9, 30)
    assert result == datetime.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_ny_local_to_utc_handles_the_november_dst_fallback_boundary():
    # 2026-11-01 is the real US DST fallback date -- the day right after
    # (2026-11-04, the same date this feature was built on) must already
    # resolve as EST (UTC-5), not a stale EDT offset.
    result = cm._ny_local_to_utc(2026, 11, 4, 10, 0)
    assert result == datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


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


# ------------------------------------------------------------------ _require_api_key

def test_require_api_key_raises_a_clear_error_when_unset(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATABENTO_API_KEY"):
        cm._require_api_key()


def test_require_api_key_returns_the_env_value_when_set(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    assert cm._require_api_key() == "db-test-key"


# ------------------------------------------------------------------ fetch_daily_atr14
# 2026-11-05 -- added same day a real gap was found live: cme_engine.py's
# call site had been hardcoding daily_atr14=None since this module's own
# Databento rebuild, forcing every lock into the reachability safety
# sentinel's STAND_DOWN path. These tests cover the simple-mean math and
# the "never fabricate a fallback on failure" contract, not the real HTTP
# call (no aiohttp-mocking precedent in this codebase, per this file's
# own header).

async def _fake_daily_bars_14(self, symbol, start_utc, end_utc, schema="ohlcv-1m"):
    base = datetime.datetime(2026, 10, 1, tzinfo=UTC)
    # 14 daily bars with ranges 1..14 -- mean range = 7.5
    return [
        {"ts_event": base + datetime.timedelta(days=i), "open": 100.0, "high": 100.0 + (i + 1), "low": 100.0, "close": 100.0}
        for i in range(14)
    ]


def test_fetch_daily_atr14_computes_the_simple_mean_range(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    monkeypatch.setattr(dbc.DatabentoClient, "get_ohlcv_1m_bars", _fake_daily_bars_14)
    result = asyncio.run(cm.fetch_daily_atr14("MNQ", "2026-11-05"))
    assert result == pytest.approx(7.5)


async def _fake_daily_bars_too_few(self, symbol, start_utc, end_utc, schema="ohlcv-1m"):
    return [{"ts_event": datetime.datetime.now(UTC), "open": 100.0, "high": 105.0, "low": 100.0, "close": 102.0}]


def test_fetch_daily_atr14_returns_none_on_too_few_bars_never_fabricates(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    monkeypatch.setattr(dbc.DatabentoClient, "get_ohlcv_1m_bars", _fake_daily_bars_too_few)
    assert asyncio.run(cm.fetch_daily_atr14("MNQ", "2026-11-05")) is None


async def _fake_daily_bars_raises(self, symbol, start_utc, end_utc, schema="ohlcv-1m"):
    raise RuntimeError("Databento bars query failed (502): simulated outage")


def test_fetch_daily_atr14_returns_none_on_fetch_failure_never_fabricates(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    monkeypatch.setattr(dbc.DatabentoClient, "get_ohlcv_1m_bars", _fake_daily_bars_raises)
    assert asyncio.run(cm.fetch_daily_atr14("MNQ", "2026-11-05")) is None


def test_fetch_daily_atr14_uses_the_ohlcv_1d_schema_and_excludes_todays_bar(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    seen = {}

    async def _capture(self, symbol, start_utc, end_utc, schema="ohlcv-1m"):
        seen["schema"] = schema
        seen["end_utc"] = end_utc
        return await _fake_daily_bars_14(self, symbol, start_utc, end_utc, schema)

    monkeypatch.setattr(dbc.DatabentoClient, "get_ohlcv_1m_bars", _capture)
    asyncio.run(cm.fetch_daily_atr14("MNQ", "2026-11-05"))
    assert seen["schema"] == "ohlcv-1d"
    # end_utc must be midnight ET on date_key -- excludes today's own
    # not-yet-closed daily bar, never leaks an incomplete range in.
    assert seen["end_utc"] == cm._ny_local_to_utc(2026, 11, 5, 0, 0)
