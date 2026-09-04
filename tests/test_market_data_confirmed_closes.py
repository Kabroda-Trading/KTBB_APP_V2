"""
Unit coverage for market_data.confirmed_5m_closes() -- the 2026-09-04 P0
fix (Kabroda AI Brain repo AGENT_LOG.md, DeepSeek + Andy). Pure function,
no DB/network -- straightforward to test with hand-constructed candle
lists and a fixed now_ts.

Real incident this fixes: an 8:35 CT 5m bar wicked through BD (low
78,973) but closed back above it (79,349, confirmed against real Kraken
5m OHLC) -- the system evaluated it as a real cross anyway because
whatever candle was last in the fetched list at poll time got trusted as
a confirmed close, when ccxt's fetch_ohlcv() returns the current,
still-forming bar as its last row with a live-updating "close".
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import market_data as md


def _candle(open_ts, close):
    return {"time": open_ts, "open": close, "high": close, "low": close, "close": close, "volume": 1.0}


def test_confirmed_5m_closes_drops_still_forming_last_candle():
    # A 5m candle opened at t=1000 is only confirmed once now >= 1000+300.
    candles = [_candle(700, 100.0), _candle(1000, 78973.0)]  # the wick low, still forming
    result = md.confirmed_5m_closes(candles, now_ts=1200.0)  # only 200s elapsed, not yet 300
    assert len(result) == 1
    assert result[-1]["time"] == 700


def test_confirmed_5m_closes_keeps_last_candle_once_its_window_elapsed():
    candles = [_candle(700, 100.0), _candle(1000, 79349.0)]  # the real, confirmed close
    result = md.confirmed_5m_closes(candles, now_ts=1300.0)  # exactly 300s elapsed -- confirmed
    assert len(result) == 2
    assert result[-1]["close"] == 79349.0


def test_confirmed_5m_closes_wick_through_close_back_inside_does_not_trigger():
    # THE exact incident: DeepSeek's spec case (1) -- wick-through-close-
    # above must NOT read as a cross. BD=79200; the forming candle wicked
    # to 78,973 (below BD) but hasn't closed yet -- confirmed_5m_closes()
    # must drop it, leaving the prior confirmed candle (which stayed above
    # BD) as the real "last close" a cross-detector would see.
    bd = 79200.0
    candles = [_candle(700, 79241.0), _candle(1000, 78973.0)]  # forming candle's CURRENT price is mid-wick
    result = md.confirmed_5m_closes(candles, now_ts=1150.0)  # bar still forming
    assert result[-1]["close"] == 79241.0
    assert result[-1]["close"] > bd  # no cross by the confirmed-close rule


def test_confirmed_5m_closes_real_close_through_trigger_does_trigger():
    # DeepSeek's spec case (2) -- a genuine confirmed close beyond the
    # trigger must still be visible as the last candle.
    bd = 79200.0
    candles = [_candle(700, 79241.0), _candle(1000, 79150.0)]  # confirmed close BELOW BD
    result = md.confirmed_5m_closes(candles, now_ts=1301.0)  # bar's window has elapsed
    assert result[-1]["close"] == 79150.0
    assert result[-1]["close"] < bd  # a real cross by the confirmed-close rule


def test_confirmed_5m_closes_empty_list_is_safe():
    assert md.confirmed_5m_closes([], now_ts=1000.0) == []


def test_confirmed_5m_closes_defaults_to_wallclock_when_now_ts_omitted():
    # A candle opened "now" (via time.time()) is always still forming --
    # confirms the default path (no now_ts arg) doesn't crash/misbehave.
    import time
    candles = [_candle(700, 100.0), _candle(int(time.time()), 200.0)]
    result = md.confirmed_5m_closes(candles)
    assert len(result) == 1


def test_confirmed_5m_closes_only_ever_drops_the_trailing_candle():
    # A mid-list "unconfirmed-looking" candle (shouldn't happen in real
    # data, but the function must never scan/drop more than the last one).
    candles = [_candle(700, 100.0), _candle(1000, 200.0), _candle(1300, 300.0)]
    result = md.confirmed_5m_closes(candles, now_ts=1601.0)  # the LAST candle's window (1300+300) has now elapsed
    assert len(result) == 3
