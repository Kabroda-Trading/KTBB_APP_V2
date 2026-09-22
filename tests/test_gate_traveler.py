"""Unit coverage for gate_traveler.py -- GATE_TRAVELER's D1 (tercile skip)
and D2 (cross detection + pullback fill) pure functions. Hand-computed
scenarios, matching recipe_assembled.py::pullback_fill()/tercile_skip()
semantics verbatim (confirmed against the frozen source directly, Kabroda
AI Brain repo, 2026-09-15 -- see gate_traveler.py's own header)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import gate_traveler as gt

NOW = datetime.datetime(2026, 9, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)


# ------------------------------------------------------------------ tercile_skip

def test_tercile_skip_long_below_lo_is_skipped():
    assert gt.tercile_skip(45.0, "LONG") is True   # < FULL_D1_CUTS["LONG"][0]=51.49


def test_tercile_skip_long_in_zone_not_skipped():
    assert gt.tercile_skip(55.0, "LONG") is False


def test_tercile_skip_long_above_hi_not_skipped():
    # LONG only skips the LOWEST tercile -- above the hi cut is a different
    # (higher, richer) tercile, not the skipped one.
    assert gt.tercile_skip(70.0, "LONG") is False


def test_tercile_skip_short_above_hi_is_skipped():
    assert gt.tercile_skip(55.0, "SHORT") is True   # > FULL_D1_CUTS["SHORT"][1]=48.56


def test_tercile_skip_short_in_zone_not_skipped():
    assert gt.tercile_skip(44.0, "SHORT") is False


def test_tercile_skip_no_rsi_value_not_skipped():
    # Missing data -> NOT skipped (counted, disclosed) -- same convention
    # the study itself uses, never silently drops a journey for missing data.
    assert gt.tercile_skip(None, "LONG") is False
    assert gt.tercile_skip(None, "SHORT") is False


def test_tercile_skip_custom_cuts():
    cuts = {"LONG": (40.0, 60.0), "SHORT": (30.0, 50.0)}
    assert gt.tercile_skip(35.0, "LONG", cuts) is True
    assert gt.tercile_skip(45.0, "LONG", cuts) is False


# ------------------------------------------------------------------ advance_waiting_cross

CROSS_EPOCH = 1700000000


def _h4_series(up, down, pattern_mod, closed_bars=20):
    """A real 4H candle series, entirely closed as of CROSS_EPOCH (2026-09-21:
    advance_waiting_cross() now computes RSI-4h-at-cross from candles_4h
    itself via gate_traveler.rsi_at_cross() -- it no longer reads a plan-
    level rsi_4h_at_lock at all). Parameters are tuned (see the module
    comment below) to land the resulting RSI in a specific, asserted zone;
    the test itself verifies the landing value via rsi_at_cross() directly,
    so the fixture's own correctness isn't just assumed."""
    start = CROSS_EPOCH - (closed_bars + 2) * 14400
    closes = [100.0]
    for i in range(closed_bars - 1):
        closes.append(closes[-1] + (up if i % pattern_mod else -down))
    return [{"close": c, "time": start + i * 14400} for i, c in enumerate(closes)]


# Tuned via direct search against gate_traveler.rsi_at_cross() itself (not
# guessed): LONG_IN_ZONE_H4 -> ~56.96 (inside 51.49-61.27); LONG_SKIP_H4 ->
# ~46.87 (below 51.49); SHORT_IN_ZONE_H4 -> ~45.44 (inside 40.45-48.56).
LONG_IN_ZONE_H4 = _h4_series(up=0.3, down=0.2, pattern_mod=2)
LONG_SKIP_H4 = _h4_series(up=0.1, down=0.1, pattern_mod=2)
SHORT_IN_ZONE_H4 = _h4_series(up=0.3, down=0.6, pattern_mod=3, closed_bars=20)


def _cross_plan(**extra):
    d = {
        "status": "WAITING_CROSS",
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "r30_high": 100.0, "r30_low": 90.0,
    }
    d.update(extra)
    return d


def test_rsi_at_cross_fixtures_land_in_the_expected_zones():
    # The fixtures above are asserted here, not just asserted-by-construction
    # -- if the search parameters ever drift, this fails loudly instead of
    # silently changing what the tests below are actually exercising.
    long_in = gt.rsi_at_cross(LONG_IN_ZONE_H4, CROSS_EPOCH)
    long_skip = gt.rsi_at_cross(LONG_SKIP_H4, CROSS_EPOCH)
    short_in = gt.rsi_at_cross(SHORT_IN_ZONE_H4, CROSS_EPOCH)
    lo, hi = gt.FULL_D1_CUTS["LONG"]
    slo, shi = gt.FULL_D1_CUTS["SHORT"]
    assert lo < long_in < hi
    assert long_skip < lo
    assert slo < short_in < shi


def test_advance_waiting_cross_no_cross_yet_returns_none():
    plan = _cross_plan()
    candles = [{"close": 95.0}] * 10  # inside the box
    assert gt.advance_waiting_cross(plan, candles, NOW, candles_4h=LONG_IN_ZONE_H4) is None


def test_advance_waiting_cross_ignores_non_waiting_status():
    plan = _cross_plan(status="WAITING_PULLBACK")
    candles = [{"close": 105.0, "time": 1000}] * 10
    assert gt.advance_waiting_cross(plan, candles, NOW, candles_4h=LONG_IN_ZONE_H4) is None


def test_advance_waiting_cross_long_not_skipped_goes_to_waiting_pullback():
    plan = _cross_plan()
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": CROSS_EPOCH}]
    result = gt.advance_waiting_cross(plan, candles, NOW, candles_4h=LONG_IN_ZONE_H4)
    assert result is not None
    assert result["status"] == "WAITING_PULLBACK"
    assert result["direction"] == "LONG"
    assert result["box"] == 10.0
    assert result["opposite_trigger"] == 90.0
    assert result["tercile_skipped"] is False
    assert result["rsi_4h_at_cross"] == pytest.approx(gt.rsi_at_cross(LONG_IN_ZONE_H4, CROSS_EPOCH))
    # stop = r30_low - 0.12*box = 90 - 1.2 = 88.8
    assert result["stop_price"] == 88.8
    # t1 = trigger + 1.0*box = 100 + 10 = 110
    assert result["t1_price"] == 110.0
    assert result["journey_cap_at"] == result["cross_time"] + datetime.timedelta(days=7)


def test_advance_waiting_cross_long_tercile_skipped_goes_to_done():
    plan = _cross_plan()
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": CROSS_EPOCH}]
    result = gt.advance_waiting_cross(plan, candles, NOW, candles_4h=LONG_SKIP_H4)
    assert result["status"] == "TERCILE_SKIPPED"
    assert result["tercile_skipped"] is True
    assert "not taken" in result["last_transition_reason"]
    assert "RSI-4h-at-cross" in result["last_transition_reason"]


def test_advance_waiting_cross_no_4h_candles_is_not_skipped():
    # A fetch failure/None history at the cross: rsi_4h_at_cross is None,
    # which tercile_skip() treats as "not skipped" -- same convention as
    # genuinely-insufficient history, never a reason to stall cross
    # detection on the already-confirmed 5m data.
    plan = _cross_plan()
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": CROSS_EPOCH}]
    result = gt.advance_waiting_cross(plan, candles, NOW, candles_4h=None)
    assert result["status"] == "WAITING_PULLBACK"
    assert result["rsi_4h_at_cross"] is None


def test_advance_waiting_cross_short_side():
    plan = _cross_plan()
    candles = [{"close": 105.0}] * 5 + [{"close": 85.0, "time": CROSS_EPOCH}]
    result = gt.advance_waiting_cross(plan, candles, NOW, candles_4h=SHORT_IN_ZONE_H4)
    assert result["status"] == "WAITING_PULLBACK"
    assert result["direction"] == "SHORT"
    assert result["opposite_trigger"] == 100.0
    # stop = r30_high + 0.12*box = 100 + 1.2 = 101.2
    assert result["stop_price"] == 101.2
    # t1 = trigger - 1.0*box = 90 - 10 = 80
    assert result["t1_price"] == 80.0


def test_advance_waiting_cross_bad_levels_returns_none():
    plan = _cross_plan(breakout_trigger=0.0, breakdown_trigger=0.0)
    candles = [{"close": 105.0}]
    assert gt.advance_waiting_cross(plan, candles, NOW) is None


# ------------------------------------------------------------------ advance_waiting_pullback

def _pullback_plan(**extra):
    cross_time = NOW - datetime.timedelta(hours=2)
    d = {
        "status": "WAITING_PULLBACK", "direction": "LONG",
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "opposite_trigger": 90.0,
        "cross_time": cross_time,
        "journey_cap_at": cross_time + datetime.timedelta(days=7),
    }
    d.update(extra)
    return d


def _c(close, ts):
    return {"close": close, "time": ts}


def test_advance_waiting_pullback_ignores_non_waiting_status():
    plan = _pullback_plan(status="FILLED")
    assert gt.advance_waiting_pullback(plan, [], NOW) is None


def test_advance_waiting_pullback_no_bars_after_cross_yet_returns_none():
    plan = _pullback_plan()
    ct = plan["cross_time"].timestamp()
    candles = [_c(105.0, ct)]  # the cross bar itself, not after it
    assert gt.advance_waiting_pullback(plan, candles, NOW) is None


def test_advance_waiting_pullback_fills_on_first_close_back_at_trigger():
    plan = _pullback_plan()
    ct = plan["cross_time"].timestamp()
    candles = [
        _c(105.0, ct),           # cross bar -- skipped (win[1:])
        _c(103.0, ct + 300),     # still above trigger -- no fill
        _c(99.5, ct + 600),      # back at/through trigger (<=100) -- FILL
        _c(101.0, ct + 900),     # irrelevant, fill already happened
    ]
    result = gt.advance_waiting_pullback(plan, candles, NOW)
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 99.5
    assert result["fill_time"] == datetime.datetime.fromtimestamp(ct + 600, tz=datetime.timezone.utc)


def test_advance_waiting_pullback_short_side_fills_on_close_back_up_to_trigger():
    plan = _pullback_plan(direction="SHORT", breakout_trigger=100.0, breakdown_trigger=90.0, opposite_trigger=100.0)
    ct = plan["cross_time"].timestamp()
    candles = [
        _c(85.0, ct),
        _c(87.0, ct + 300),
        _c(90.5, ct + 600),  # back at/through trigger (>=90) -- FILL
    ]
    result = gt.advance_waiting_pullback(plan, candles, NOW)
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 90.5


def test_advance_waiting_pullback_no_fill_yet_returns_none():
    plan = _pullback_plan()
    ct = plan["cross_time"].timestamp()
    candles = [_c(105.0, ct), _c(106.0, ct + 300), _c(108.0, ct + 600)]  # never comes back
    assert gt.advance_waiting_pullback(plan, candles, NOW) is None


def test_advance_waiting_pullback_opposite_trigger_breaks_first_ends_journey():
    plan = _pullback_plan()
    ct = plan["cross_time"].timestamp()
    candles = [_c(105.0, ct), _c(89.0, ct + 300)]  # closes below the opposite (90) trigger before any pullback
    result = gt.advance_waiting_pullback(plan, candles, NOW)
    assert result["status"] == "DONE"
    assert "opposite trigger" in result["last_transition_reason"]


def test_advance_waiting_pullback_journey_cap_reached_with_no_fill():
    plan = _pullback_plan(journey_cap_at=NOW - datetime.timedelta(minutes=1))  # already passed
    ct = plan["cross_time"].timestamp()
    candles = [_c(105.0, ct), _c(106.0, ct + 300)]  # never pulls back, opposite never breaks
    result = gt.advance_waiting_pullback(plan, candles, NOW)
    assert result["status"] == "DONE"
    assert "7-day journey cap" in result["last_transition_reason"]


def test_advance_waiting_pullback_not_yet_capped_returns_none():
    plan = _pullback_plan()  # cap is 7 days out, not reached
    ct = plan["cross_time"].timestamp()
    candles = [_c(105.0, ct), _c(106.0, ct + 300)]
    assert gt.advance_waiting_pullback(plan, candles, NOW) is None


# ------------------------------------------------------------------ rsi_at_cross (2026-09-21)

import battlebox_pipeline as bp


def _h4_walk(n, seed=1):
    # A varied, non-monotonic real-looking series (monotonic input pins
    # avg_loss or avg_gain at exactly 0, which both _calc_rsi and
    # rsi_at_cross treat specially -- not a useful identity-check case).
    closes = [100.0]
    for i in range(n - 1):
        step = ((seed * (i + 1)) % 7) - 3   # -3..3, deterministic, not monotonic
        closes.append(closes[-1] + step + 0.1)
    return closes


def test_rsi_at_cross_matches_calc_rsi_byte_identical_across_bar_counts():
    # The whole point of this function is to reproduce battlebox_pipeline.
    # _calc_rsi()'s own formula (duplicated, not imported, per gate_
    # traveler.py's no-heavy-cross-import convention) -- verify against
    # THAT function directly, not just re-derive the same formula twice.
    cross = 2_000_000_000
    for n in (14, 15, 16, 20, 40, 97):
        closes = _h4_walk(n, seed=n)
        candles = [{"close": c, "time": cross - (n - i) * 14400} for i, c in enumerate(closes)]
        expected = bp._calc_rsi(closes) if n >= gt.MIN_RSI_4H_BARS else None
        got = gt.rsi_at_cross(candles, cross)
        if expected is None:
            assert got is None, f"n={n}"
        else:
            assert got == pytest.approx(expected), f"n={n}: {got} != {expected}"


def test_rsi_at_cross_below_minimum_bars_is_none_not_fifty():
    cross = 2_000_000_000
    closes = _h4_walk(gt.MIN_RSI_4H_BARS - 1, seed=3)
    candles = [{"close": c, "time": cross - (len(closes) - i) * 14400} for i, c in enumerate(closes)]
    assert gt.rsi_at_cross(candles, cross) is None


def test_rsi_at_cross_excludes_a_bar_still_forming_at_the_cross():
    cross = 2_000_000_000
    closes = _h4_walk(gt.MIN_RSI_4H_BARS + 5, seed=5)
    candles = [{"close": c, "time": cross - (len(closes) - i) * 14400} for i, c in enumerate(closes)]
    # The bar containing "cross" itself (open = cross - 1) hasn't closed yet.
    forming = {"close": 999.0, "time": cross - 1}
    with_forming = gt.rsi_at_cross(candles + [forming], cross)
    without_forming = gt.rsi_at_cross(candles, cross)
    assert with_forming == without_forming


def test_rsi_at_cross_none_inputs():
    assert gt.rsi_at_cross(None, 1000) is None
    assert gt.rsi_at_cross([{"close": 1.0, "time": 0}] * 20, None) is None
    assert gt.rsi_at_cross([], 1000) is None
