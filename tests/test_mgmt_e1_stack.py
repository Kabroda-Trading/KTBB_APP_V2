"""Unit coverage for mgmt_e1_stack.py -- GATE_TRAVELER's D3 management walk
(STOP -> C5-or-BBWP -> T1 -> TIME, verbatim priority from the frozen
lab_touchfill_arms.py::walk_from_fill(), CANON section 9d -- see that
module's own header comment for the two confirmed corrections vs the
original handoff prose)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mgmt_e1_stack as e1

ENTRY_TIME = datetime.datetime(2026, 9, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
ENTRY_EPOCH = ENTRY_TIME.timestamp()
NOW = ENTRY_TIME + datetime.timedelta(hours=6)


def _order(**extra):
    d = {
        "direction": "LONG", "entry_price": 100.0, "stop_price": 90.0, "t1_price": 110.0,
        "entry_fill_time": ENTRY_TIME,
    }
    d.update(extra)
    return d


def _bar(close, ts, high=None, low=None):
    return {"close": close, "high": high if high is not None else close, "low": low if low is not None else close, "time": ts}


def _flat_1h4h(n=20, price=100.0):
    """RSI-neutral (flat closes -> no C5 momentum decay) candle series --
    the 'nothing else fires' baseline for isolating one condition per test.
    2026-09-22: no longer BBWP-relevant on its own -- BBWP now reads its own
    separate candles_4h_bbwp feed (see check_c5_or_bbwp()'s docstring); this
    fixture is far too short for BBWP_PERIOD/BBWP_LOOKBACK regardless."""
    return [{"close": price} for _ in range(n)]


def _bbwp_burn_candles_4h(n_total=865):
    """A real (not monkeypatched) 4H series, long enough for BBWP_PERIOD(96)+
    BBWP_LOOKBACK(768)=864, constructed to drive a genuine bbwp_burn() True
    through the real math: ~150 bars of high volatility set a high 768-bar
    rolling-max; a long low-volatility grind keeps recent std well under it;
    a 90-bar burst pushes recent std back up near that peak; a smaller final
    move eases it off (the 'falling' half of bbwp_burn's condition). Verified
    directly: bbwp[-1]=95.229... > 70 and < bbwp[-2]=96.511... -> True."""
    closes = [100.0]
    for i in range(150):
        closes.append(closes[-1] + (12.0 if i % 2 == 0 else -11.0))
    n_phase2 = n_total - len(closes) - 90 - 1
    for i in range(n_phase2):
        closes.append(closes[-1] + (0.4 if i % 2 == 0 else -0.3))
    for i in range(90):
        closes.append(closes[-1] + (40.0 if i % 2 == 0 else -39.0))
    closes.append(closes[-1] + 4.0)
    # 865 4H bars spans ~144 days -- these must END safely BEFORE `now_ts`
    # (NOW, 6h after ENTRY_TIME) or check_c5_or_bbwp's own confirmed_closes()
    # strip discards the last bar and shifts which close bbwp[-1]/bbwp[-2]
    # actually land on, silently invalidating the verified values above.
    last_open = int(NOW.timestamp()) - 14400 - 1
    n = len(closes)
    return [{"close": c, "time": last_open - (n - 1 - i) * 14400} for i, c in enumerate(closes)]


def _decaying_rsi_candles():
    """A real price path (strong up-move, then a deceleration/chop phase)
    that produces a genuine RSI momentum decay (rsi[-1] < rsi[-7]) via the
    real rsi_series() formula -- NOT a monotonic move (which pins RSI at a
    constant floor/ceiling with dn_ewm or up_ewm stuck at exactly 0,
    producing None forever per rsi_series()'s own pandas-matching
    dn.replace(0, np.nan) behavior, never a real decay signal). Verified
    directly: rsi[-1]=92.53 < rsi[-7]=97.43 -> c5_momentum_decay() True."""
    closes = [100.0]
    for i in range(15):
        closes.append(closes[-1] + 2.0 - (0.3 if i % 4 == 0 else 0))
    for i in range(10):
        closes.append(closes[-1] + 0.2 - (0.5 if i % 2 == 0 else 0))
    return [{"close": c} for c in closes]


def test_stop_fires_first_even_when_c5_also_true():
    # STOP is checked BEFORE C5/BBWP in the frozen walk (confirmed
    # discrepancy #3 vs the original handoff prose) -- verify the priority
    # directly: construct candles where the stop is touched, and ALSO make
    # rsi_1h/rsi_4h so obviously decaying that c5 would fire if checked
    # first; STOP must still win.
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(85.0, ENTRY_EPOCH + 600, high=86.0, low=84.0),  # stop (90) touched via low
    ]
    decaying = _decaying_rsi_candles()  # would fire C5 if checked before STOP
    result = e1.advance(order, candles_5m, decaying, decaying, NOW)
    assert result is not None
    assert result["exit_reason"] == "STOP"
    assert result["exit_price"] == 90.0
    assert result["c5_fired"] is False
    assert result["bbwp_fired"] is False


def test_c5_exit_fires_before_t1_when_both_conditions_present():
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(115.0, ENTRY_EPOCH + 600, high=116.0, low=114.0),  # T1 (110) also touched here
    ]
    decaying = _decaying_rsi_candles()
    result = e1.advance(order, candles_5m, decaying, decaying, NOW)
    assert result["exit_reason"] == "C5_EXIT"
    assert result["exit_price"] == 115.0   # exits at the CURRENT bar's close, not T1's price
    assert result["c5_fired"] is True
    assert result["bbwp_fired"] is False


def test_bbwp_exit_fires_when_only_bbwp_condition_true():
    order = _order()
    candles_5m = [_bar(100.0, ENTRY_EPOCH + 300)]
    flat = _flat_1h4h()  # no C5 (flat RSI)
    # A cheap smoke test via a monkeypatched bbwp_series -- proves advance()'s
    # own dispatch/priority wiring around a BBWP_EXIT, not the real math (see
    # test_bbwp_exit_fires_through_the_real_math_on_its_own_bitunix_feed
    # below for a real, non-monkeypatched, correctly-fed version of this).
    import study_indicators as si
    real_bbwp = si.bbwp_series
    try:
        si.bbwp_series = lambda closes, period=si.BBWP_PERIOD, lookback=si.BBWP_LOOKBACK: [None, 80.0, 75.0]
        result = e1.advance(order, candles_5m, flat, flat, NOW)
    finally:
        si.bbwp_series = real_bbwp
    assert result["exit_reason"] == "BBWP_EXIT"
    assert result["bbwp_fired"] is True
    assert result["c5_fired"] is False


def test_bbwp_exit_fires_through_the_real_math_on_its_own_bitunix_feed():
    # 2026-09-22 (CC_INTERFACE.md item 3): the real, non-monkeypatched test
    # the dangling comment above used to promise but never delivered (grep
    # confirmed no such test existed anywhere in this file before today).
    # candles_4h (Kraken, C5's own leg) stays flat/no-signal; the real BBWP
    # trigger arrives ONLY via candles_4h_bbwp (Bitunix's own feed) -- this
    # proves the two feeds are genuinely wired to the right legs, not just
    # that BBWP_EXIT can be dispatched at all.
    order = _order()
    candles_5m = [_bar(100.0, ENTRY_EPOCH + 300)]
    flat = _flat_1h4h()
    bbwp_feed = _bbwp_burn_candles_4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW, candles_4h_bbwp=bbwp_feed)
    assert result["exit_reason"] == "BBWP_EXIT"
    assert result["bbwp_fired"] is True
    assert result["c5_fired"] is False


def test_bbwp_never_fires_from_the_old_kraken_fed_candles_4h_anymore():
    # The regression guard, at the check_c5_or_bbwp() level directly (not
    # advance()) so it isolates exactly one claim: candles_4h (Kraken, C5's
    # own leg) is ITSELF a real bbwp-triggering series -- proving the
    # retired Kraken-fed BBWP path is genuinely gone, not just supplemented,
    # per Andy's own wording ("drop the dead Kraken-based BBWP path"). This
    # series ALSO happens to trigger C5 (real RSI decay is present in it
    # too) -- c5_hit is allowed to be True or False here, that's not this
    # test's claim; bbwp_hit is what must stay False with candles_4h_bbwp
    # omitted entirely (a transient Bitunix-feed gap), never falling back
    # to candles_4h.
    bbwp_triggering = _bbwp_burn_candles_4h()
    _c5_hit, bbwp_hit = e1.check_c5_or_bbwp(bbwp_triggering, bbwp_triggering, now_ts=NOW.timestamp())
    assert bbwp_hit is False


def test_t1_fires_when_neither_stop_nor_c5_nor_bbwp():
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(105.0, ENTRY_EPOCH + 600, high=111.0, low=104.0),  # T1 (110) touched via high
    ]
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW)
    assert result["exit_reason"] == "T1"
    assert result["exit_price"] == 110.0


def test_short_side_stop_and_t1_directions_are_mirrored():
    order = _order(direction="SHORT", entry_price=100.0, stop_price=110.0, t1_price=90.0)
    candles_5m = [_bar(95.0, ENTRY_EPOCH + 300, high=111.0, low=94.0)]  # stop (110) touched via high
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW)
    assert result["exit_reason"] == "STOP"
    assert result["exit_price"] == 110.0


def test_time_exit_fires_when_journey_cap_reached_with_no_other_exit():
    order = _order()
    candles_5m = [_bar(102.0, ENTRY_EPOCH + 300), _bar(103.0, ENTRY_EPOCH + 600)]
    flat = _flat_1h4h()
    cap = NOW - datetime.timedelta(minutes=1)  # already passed
    result = e1.advance(order, candles_5m, flat, flat, NOW, journey_cap_at=cap)
    assert result["exit_reason"] == "TIME"
    assert result["exit_price"] == 103.0  # last confirmed close


def test_still_open_returns_none_when_nothing_fires_and_no_cap():
    order = _order()
    candles_5m = [_bar(102.0, ENTRY_EPOCH + 300)]
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW, journey_cap_at=NOW + datetime.timedelta(days=6))
    assert result is None


def test_missing_entry_fill_time_returns_none():
    order = _order(entry_fill_time=None)
    assert e1.advance(order, [_bar(100.0, ENTRY_EPOCH)], [], [], NOW) is None


def test_no_bars_since_entry_returns_none():
    order = _order()
    candles_5m = [_bar(100.0, ENTRY_EPOCH - 300)]  # before entry, not after
    assert e1.advance(order, candles_5m, [], [], NOW) is None


# ---- 2026-09-21: forming-bar strip (AGENT_LOG 2026-09-21 10:15) ----------------
# A rising series (real RSI, with genuine down bars so RSI is defined) whose
# LAST bar is a sharp dip: including it gives C5 True (67.77 < 91.51),
# confirmed-only gives False (91.97 vs 90.74, still rising) -- the exact
# shape of the 09-21 live exit, where a 5-minute dip inside a forming 1H bar
# fired a C5 exit the backtest (bar closes only) would never have produced.
_BASE_OPEN = 1_800_000_000 - (1_800_000_000 % 14400)


def _rising_then_dip(interval, dip=6.0, n_up=40):
    closes = [100.0]
    for i in range(n_up - 1):
        closes.append(closes[-1] + (-0.5 if i % 4 == 3 else 1.5))
    closes.append(closes[-1] - dip)
    return [{"close": c, "time": _BASE_OPEN + i * interval} for i, c in enumerate(closes)]


def _clean_rising(interval, n=40):
    # Every bar sits well before _BASE_OPEN, so it is confirmed at any now_ts
    # the tests use, and n=40 ends on an up-step (RSI still rising -> no C5).
    closes = [100.0]
    for i in range(n - 1):
        closes.append(closes[-1] + (-0.5 if i % 4 == 3 else 1.5))
    return [{"close": c, "time": _BASE_OPEN - (n - i) * interval} for i, c in enumerate(closes)]


def test_check_c5_ignores_a_forming_1h_bar_dip():
    h1 = _rising_then_dip(3600)
    h4 = _clean_rising(14400)
    mid_hour = h1[-1]["time"] + 1800
    assert e1.check_c5_or_bbwp(h1, h4, now_ts=mid_hour) == (False, False)


def test_check_c5_fires_once_that_1h_bar_is_confirmed():
    h1 = _rising_then_dip(3600)
    h4 = _clean_rising(14400)
    after_close = h1[-1]["time"] + 3600
    c5, bbwp = e1.check_c5_or_bbwp(h1, h4, now_ts=after_close)
    assert c5 is True and bbwp is False


def test_check_c5_ignores_a_forming_4h_bar_dip():
    h1 = _clean_rising(3600)
    h4 = _rising_then_dip(14400)
    assert e1.check_c5_or_bbwp(h1, h4, now_ts=h4[-1]["time"] + 3600) == (False, False)
    assert e1.check_c5_or_bbwp(h1, h4, now_ts=h4[-1]["time"] + 14400)[0] is True


def test_advance_does_not_exit_on_a_forming_1h_bar_dip():
    # End to end through advance(): fill, a confirmed 5m bar after it, and a
    # forming 1H dip -- must stay open (None), not book a C5_EXIT.
    h1 = _rising_then_dip(3600)
    h4 = _clean_rising(14400)
    fill = datetime.datetime.fromtimestamp(h1[-1]["time"] + 60, tz=datetime.timezone.utc)
    now = fill + datetime.timedelta(minutes=10)
    m5 = [_bar(100.0, fill.timestamp() + 60, high=100.5, low=99.5)]
    order = _order(entry_fill_time=fill)
    assert e1.advance(order, m5, h1, h4, now) is None
    # ...and once that hour is over the same series does decay-exit.
    later = datetime.datetime.fromtimestamp(h1[-1]["time"] + 3600 + 60, tz=datetime.timezone.utc)
    result = e1.advance(order, m5, h1, h4, later)
    assert result is not None and result["exit_reason"] == "C5_EXIT"
