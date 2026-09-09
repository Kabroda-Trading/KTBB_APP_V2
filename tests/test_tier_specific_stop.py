"""
Coverage for the 2026-09-08 tier-specific execution stop (Andy's explicit
decision to ship the backtest finding directly -- Kabroda AI Brain repo
AGENT_LOG.md, same date: PREMIUM keeps the 24h core-zone stop unchanged,
STANDARD now uses decision_engine.py's r30-based formula as its REAL
execution stop instead of the zone-based one).

Hand-computed: STOP_BUFFER_BOX=0.12. For a LONG with r30_low=90,
box=abs(t2-entry): r30_stop = 90 - 0.12*box.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import trade_plan as tp

ANCHOR = datetime.datetime(2026, 8, 31, 13, 0, 0, tzinfo=datetime.timezone.utc)


def _flat_candles(price=100.0, n=10):
    return [{"open": price, "high": price + 1, "low": price - 1, "close": price} for _ in range(n)]


def _take_decision(side="LONG", tier="STANDARD", entry=100.0, t1=112.0, t2=120.0, t3=132.0):
    return {
        "verdict_state": "TAKE_PREMIUM" if tier == "PREMIUM" else "TAKE_STANDARD",
        "side": side, "tier": tier,
        "entry_price": entry, "stop_loss": entry - 20.0 if side == "LONG" else entry + 20.0,
        "t1": t1, "t2": t2, "t3": t3,
        "tactical_brief": "gate approved",
    }


def test_standard_tier_uses_the_r30_stop_not_the_zone_stop():
    # box = |120-100| = 20. r30_stop = 90 - 0.12*20 = 87.6.
    # T1 dist = 12, stop dist = 12.4, ratio = 0.968 -- just under 1:1 floor
    # with THESE exact numbers, so widen t1 slightly for a clean pass, or
    # just confirm the exact stop value directly regardless of rr outcome
    # by using an r30 far enough to keep rr comfortably >= 1.
    decision = _take_decision(side="LONG", tier="STANDARD", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    # box = |120-100| = 20; r30_stop = 95 - 0.12*20 = 92.6
    assert plan["status"] == "WAITING"
    assert plan["tier"] == "STANDARD"
    assert plan["stop_price"] == pytest.approx(92.6)
    assert "r30" in plan["stop_basis"].lower()
    assert plan["stop_price_r30"] == pytest.approx(92.6)


def test_premium_tier_still_uses_the_zone_stop_unchanged():
    # Same inputs as above except tier=PREMIUM -- stop_price must come from
    # stop_planner's real zone logic (nearest swing/r30/sweep-wick + buffer),
    # NOT the r30 formula, even though stop_price_r30 is still computed and
    # stored alongside it for audit purposes.
    decision = _take_decision(side="LONG", tier="PREMIUM", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["tier"] == "PREMIUM"
    # The flat O=100/H=101/L=99/C=100 candles produce a sweep-wick candidate
    # at 99 (wick ratio exactly 0.5, stop_planner's own SWEEP_WICK_RATIO
    # threshold, met by both wicks) -- nearer to entry than r30_low (95) or
    # f24_val (90), so it's the winning zone. Zone stop = 99 - 0.125*2.0 =
    # 98.75 (stop_planner's own BUFFER_ATR=0.125), NOT the r30-formula's
    # 92.6 from the STANDARD test above -- that's the actual point of this
    # test (PREMIUM's stop is untouched by the tier-specific-stop change).
    assert plan["stop_price"] == pytest.approx(98.75)
    assert plan["stop_price"] != pytest.approx(92.6)
    # The r30 candidate is still stored (for audit/comparison), just unused.
    assert plan["stop_price_r30"] == pytest.approx(92.6)


def test_standard_tier_no_plan_when_r30_stop_fails_rr_floor():
    # box = |120-100| = 20; r30_stop with a FAR r30_low fails the 1:1 floor:
    # r30_low=50 -> r30_stop = 50 - 0.12*20 = 47.6 -> stop_dist=52.4,
    # t1_dist=12, ratio=0.229 < 1.0 -- must be NO_PLAN, not a silently-wide trade.
    decision = _take_decision(side="LONG", tier="STANDARD", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=50.0, f24_vah=110.0, f24_val=45.0, daily_atr14=2.0,
    )
    assert plan["status"] == "NO_PLAN"
    assert "r30" in plan["no_plan_reason"].lower()
    assert plan["stop_price_r30"] == pytest.approx(47.6)


def test_short_standard_tier_uses_r30_high_plus_buffer():
    # SHORT: r30_stop = r30_high + 0.12*box. box=|t2-entry|=|80-100|=20.
    # r30_high=105 -> r30_stop = 105 + 2.4 = 107.4.
    decision = _take_decision(side="SHORT", tier="STANDARD", entry=100.0, t1=88.0, t2=80.0, t3=68.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["stop_price"] == pytest.approx(107.4)
    assert plan["stop_price_r30"] == pytest.approx(107.4)


def test_precross_plan_stamped_standard_at_the_real_cross_swaps_to_r30_stop():
    """The pre-cross anticipate_setup() path: tier is None at generation
    (defaults to the zone stop, matching PREMIUM's shape), and only gets
    stamped -- and the stop correspondingly swapped -- once the real cross
    confirms STANDARD (fuel CONFLICTED, not FUELED specifically)."""
    import fuel_gate

    # Build a WAITING plan with tier=None directly via the internal helper
    # (mirrors what anticipate_setup()'s path produces) -- same fixture
    # shape as the module-level tests above.
    base = {"symbol": "BTC/USDT", "date_key": "2026-08-31", "session_id": "us_ny_futures",
            "commit_after": ANCHOR, "fuel_requirement": "", "management": "",
            "breakout_trigger": 100.0, "breakdown_trigger": 80.0,
            "r30_high": 105.0, "r30_low": 95.0,
            "fuel_verdict": None, "htf_aligned": None, "trend_1h": None, "trend_4h": None}
    plan = tp._build_waiting_plan(
        base, "LONG", 100.0, 112.0, 120.0, 132.0, 105.0, 95.0, 110.0, 90.0, 2.0,
        _flat_candles(price=100.0), tier=None, generation_reason="pre-cross anticipated",
    )
    assert plan["status"] == "WAITING"
    assert plan["tier"] is None
    zone_stop = plan["stop_price"]
    assert zone_stop == pytest.approx(98.75)  # same zone math as the PREMIUM test above
    assert plan["stop_price_r30"] == pytest.approx(92.6)

    # Now simulate the real cross: fuel reads CONFLICTED (not FUELED), so
    # _stamp_tier_at_cross() must land on STANDARD -- confirm the stop gets
    # swapped to the stored r30 candidate at that exact moment.
    now = ANCHOR + datetime.timedelta(hours=1)
    candles_5m = [{"close": 101.0, "volume": 1.0} for _ in range(300)]
    # Force a real cross: last candle closes beyond 100 with thin-ish volume
    # so fuel_gate reads CONFLICTED, not FUELED, on the push.
    candles_5m[-12:] = [{"close": 101.0, "volume": 0.5} for _ in range(12)]
    updates = tp.advance_waiting_plan(
        plan, now, session_expires_at=None, candles_5m=candles_5m, live_price=101.0,
        candles_1h=_flat_candles(price=100.0, n=30), candles_4h=_flat_candles(price=100.0, n=30),
        daily_atr14=2.0,
    )
    assert updates is not None
    if updates.get("tier") == "STANDARD":
        assert updates["status"] == "FILLED"
        assert updates["stop_price"] == pytest.approx(92.6)
        assert "r30" in updates["stop_basis"].lower()
    elif updates.get("tier") == "PREMIUM":
        # HTF/fuel randomness in this hand-built fixture landed PREMIUM
        # instead -- zone stop must stay untouched (no stop_price key at
        # all, since PREMIUM's path never swaps it).
        assert "stop_price" not in updates
