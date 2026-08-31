"""
Unit coverage for stop_planner.py (KABRODA_COM_TRADE_PLAN_SPEC.md SS6).
Pure-function module, no DB/network involved -- straightforward to test
with hand-constructed candle sequences and precisely known expected zones.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import stop_planner as sp


def _c(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_find_swing_points_detects_obvious_high_and_low():
    # A clean V-then-inverted-V shape: candle 5 is a confirmed swing low,
    # candle 10 is a confirmed swing high, with 3 bars of confirmation on
    # each side (left=right=3, the default).
    candles = []
    for i in range(20):
        px = 100.0
        if i == 5:
            px = 80.0   # swing low
        elif i == 10:
            px = 130.0  # swing high
        else:
            px = 100.0 + (5 if i % 2 == 0 else -5)
        candles.append(_c(px, px + 1, px - 1, px))

    highs, lows = sp._find_swing_points(candles, left=3, right=3)
    assert 79.0 in lows      # candle 5's low = 80-1 = 79
    assert 131.0 in highs    # candle 10's high = 130+1 = 131


def test_find_sweep_wicks_flags_long_wick_small_body():
    # A candle with a long lower wick and a tiny body near the top --
    # classic liquidity sweep shape.
    sweep_candle = _c(o=100.0, h=100.5, l=90.0, c=100.2)   # wick=10, range=10.5 -> ratio ~0.95
    normal_candle = _c(o=100.0, h=101.0, l=99.5, c=100.5)  # wick=0.5, range=1.5 -> ratio ~0.33

    upper, lower = sp._find_sweep_wicks([sweep_candle, normal_candle], wick_ratio=0.5)
    assert 90.0 in lower
    assert 99.5 not in lower  # normal_candle's wick ratio is below the threshold


def test_plan_stop_long_picks_nearest_zone_below_entry():
    entry = 100.0
    atr = 2.0
    # Two candidate zones below entry: a swing low at 90 (far) and f24_val
    # at 96 (nearer). Nearest should win.
    candles = [_c(95, 96, 90, 95), _c(96, 97, 95, 96)]
    # inject a clean, confirmed swing low at 90 via a proper V shape
    v_shape = [_c(98, 99, 97, 98) for _ in range(3)] + [_c(90, 91, 90, 90.5)] + [_c(98, 99, 97, 98) for _ in range(3)]

    result = sp.plan_stop(
        candles_24h=v_shape,
        entry_price=entry, is_long=True,
        r30_high=105.0, r30_low=99.0,   # r30_low (99) is above entry? no -- must be < entry to count; keep below
        f24_vah=110.0, f24_val=96.0,
        daily_atr14=atr,
    )
    # f24_val (96) is nearer to entry (100) than the swing low (90.5) or
    # r30_low (99 is actually nearer still -- but r30_low=99 < entry=100,
    # so it should actually win as the nearest). Recompute expectation:
    # candidates below 100: swing low ~90.5, f24_val=96, r30_low=99.
    # nearest (max of candidates) = r30_low = 99.
    assert result["zone_price"] == 99.0
    assert "30M range low" in result["stop_basis"]
    assert result["stop_price"] == pytest.approx(99.0 - sp.BUFFER_ATR * atr)
    assert result["stop_dist_atr"] == pytest.approx(abs(entry - result["stop_price"]) / atr, rel=1e-6)


def test_plan_stop_long_fallback_when_no_zone_below_entry():
    entry = 100.0
    atr = 2.0
    flat_candles = [_c(150, 151, 149, 150) for _ in range(10)]  # everything far above entry

    result = sp.plan_stop(
        candles_24h=flat_candles,
        entry_price=entry, is_long=True,
        r30_high=160.0, r30_low=155.0,   # both above entry -- no candidate
        f24_vah=170.0, f24_val=165.0,    # both above entry -- no candidate
        daily_atr14=atr,
    )
    assert result["zone_price"] is None
    assert "fallback" in result["stop_basis"]
    assert result["stop_price"] == pytest.approx(entry - sp.FALLBACK_ATR_MULT * atr)


def test_plan_stop_short_picks_nearest_zone_above_entry():
    entry = 100.0
    atr = 2.0
    flat_candles = [_c(100, 101, 99, 100) for _ in range(10)]

    result = sp.plan_stop(
        candles_24h=flat_candles,
        entry_price=entry, is_long=False,
        r30_high=103.0, r30_low=95.0,    # r30_high (103) > entry -- candidate
        f24_vah=101.0, f24_val=90.0,     # f24_vah (101) > entry -- candidate, nearer than r30_high
        daily_atr14=atr,
    )
    assert result["zone_price"] == 101.0
    assert "value area high" in result["stop_basis"]
    assert result["stop_price"] == pytest.approx(101.0 + sp.BUFFER_ATR * atr)


def test_plan_stop_unavailable_when_atr_zero():
    result = sp.plan_stop(
        candles_24h=[], entry_price=100.0, is_long=True,
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0,
        daily_atr14=0.0,
    )
    assert result["stop_price"] is None
    assert "unavailable" in result["stop_basis"]


def test_rr_floor_ok_passes_at_exactly_1to1():
    entry, stop, t1 = 100.0, 90.0, 110.0  # 10 risk, 10 reward -> ratio 1.0
    result = sp.rr_floor_ok(entry, stop, t1, is_long=True)
    assert result["ok"] is True
    assert result["ratio"] == pytest.approx(1.0)


def test_rr_floor_ok_fails_when_stop_too_wide():
    entry, stop, t1 = 100.0, 70.0, 110.0  # 30 risk, 10 reward -> ratio 0.33
    result = sp.rr_floor_ok(entry, stop, t1, is_long=True)
    assert result["ok"] is False
    assert result["ratio"] == pytest.approx(10.0 / 30.0, abs=1e-4)  # module rounds to 4dp


def test_rr_floor_ok_short_side():
    entry, stop, t1 = 100.0, 110.0, 85.0  # 10 risk, 15 reward -> ratio 1.5
    result = sp.rr_floor_ok(entry, stop, t1, is_long=False)
    assert result["ok"] is True
    assert result["ratio"] == pytest.approx(1.5)


def test_rr_floor_zero_stop_distance_handled_safely():
    result = sp.rr_floor_ok(100.0, 100.0, 110.0, is_long=True)
    assert result["ok"] is False
    assert result["ratio"] is None
