"""Unit coverage for gate_traveler.py -- GATE_TRAVELER's D1 (tercile skip)
and D2 (cross detection + pullback fill) pure functions. Hand-computed
scenarios, matching recipe_assembled.py::pullback_fill()/tercile_skip()
semantics verbatim (confirmed against the frozen source directly, Kabroda
AI Brain repo, 2026-09-15 -- see gate_traveler.py's own header)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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

def _cross_plan(**extra):
    d = {
        "status": "WAITING_CROSS",
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "r30_high": 100.0, "r30_low": 90.0,
        "rsi_4h_at_lock": 55.0,  # in-zone for LONG, in-zone for SHORT too at this value? check below
    }
    d.update(extra)
    return d


def test_advance_waiting_cross_no_cross_yet_returns_none():
    plan = _cross_plan()
    candles = [{"close": 95.0}] * 10  # inside the box
    assert gt.advance_waiting_cross(plan, candles, NOW) is None


def test_advance_waiting_cross_ignores_non_waiting_status():
    plan = _cross_plan(status="WAITING_PULLBACK")
    candles = [{"close": 105.0, "time": 1000}] * 10
    assert gt.advance_waiting_cross(plan, candles, NOW) is None


def test_advance_waiting_cross_long_not_skipped_goes_to_waiting_pullback():
    plan = _cross_plan(rsi_4h_at_lock=55.0)  # not skipped for LONG (in zone)
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": 1700000000}]
    result = gt.advance_waiting_cross(plan, candles, NOW)
    assert result is not None
    assert result["status"] == "WAITING_PULLBACK"
    assert result["direction"] == "LONG"
    assert result["box"] == 10.0
    assert result["opposite_trigger"] == 90.0
    assert result["tercile_skipped"] is False
    # stop = r30_low - 0.12*box = 90 - 1.2 = 88.8
    assert result["stop_price"] == 88.8
    # t1 = trigger + 1.0*box = 100 + 10 = 110
    assert result["t1_price"] == 110.0
    assert result["journey_cap_at"] == result["cross_time"] + datetime.timedelta(days=7)


def test_advance_waiting_cross_long_tercile_skipped_goes_to_done():
    plan = _cross_plan(rsi_4h_at_lock=45.0)  # < 51.49 -> skipped for LONG
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": 1700000000}]
    result = gt.advance_waiting_cross(plan, candles, NOW)
    assert result["status"] == "TERCILE_SKIPPED"
    assert result["tercile_skipped"] is True
    assert "not taken" in result["last_transition_reason"]


def test_advance_waiting_cross_short_side():
    plan = _cross_plan(rsi_4h_at_lock=44.0)  # in-zone for SHORT
    candles = [{"close": 105.0}] * 5 + [{"close": 85.0, "time": 1700000000}]
    result = gt.advance_waiting_cross(plan, candles, NOW)
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
