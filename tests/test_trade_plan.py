"""
Unit coverage for trade_plan.py (KABRODA_COM_TRADE_PLAN_SPEC.md SS3/SS4).
Pure-function module (build_trade_plan/render_brief take plain data, no
DB/network) -- straightforward to test with hand-constructed decision
dicts and precisely known expected outputs.
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


def _pass_decision(reason="box/ATR ratio 1.42 > 0.55"):
    return {
        "verdict_state": "PASS", "side": None, "tier": None,
        "entry_price": 0.0, "stop_loss": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0,
        "tactical_brief": reason,
    }


def test_no_plan_on_pass_state_carries_the_gate_reason():
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=_pass_decision("box/ATR ratio 1.42 > 0.55"),
        anchor_time=ANCHOR, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
    )
    assert plan["status"] == "NO_PLAN"
    assert plan["no_plan_reason"] == "box/ATR ratio 1.42 > 0.55"
    assert plan["commit_after"] == ANCHOR + datetime.timedelta(minutes=45)


def test_waiting_plan_on_take_long_with_good_rr():
    # Entry 100, wide flat candles below entry give a fallback stop at
    # 1.5xATR = 3.0 below entry -> stop=97. T1=112 -> risk=3, reward=12,
    # ratio=4.0 -- comfortably passes the 1:1 floor.
    decision = _take_decision(side="LONG", tier="STANDARD", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=150.0),
        r30_high=160.0, r30_low=155.0, f24_vah=170.0, f24_val=165.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["direction"] == "LONG"
    assert plan["tier"] == "STANDARD"
    assert plan["trigger_price"] == 100.0
    assert plan["stop_price"] == pytest.approx(97.0)  # 100 - 1.5*2.0
    assert plan["entry_mode"] is None  # decided at commit, not at generation
    assert plan["rr_floor_ok"] is True
    assert plan["rr_ratio"] > 1.0
    assert plan["t1"] == 112.0 and plan["t2"] == 120.0 and plan["t3"] == 132.0
    assert "30%" in plan["management"] and "runner-stop" in plan["management"]
    assert "not tier-dependent" in plan["management"].lower()


def test_no_plan_when_core_zone_stop_kills_rr():
    # A swing low FAR below entry forces a wide stop, killing R:R to T1.
    entry, t1 = 100.0, 101.0  # T1 is very close -- easy to blow the floor
    candles = (
        [{"open": 98, "high": 99, "low": 97, "close": 98} for _ in range(3)]
        + [{"open": 60, "high": 61, "low": 60, "close": 60.5}]  # confirmed swing low far below entry
        + [{"open": 98, "high": 99, "low": 97, "close": 98} for _ in range(3)]
    )
    decision = _take_decision(side="LONG", tier="STANDARD", entry=entry, t1=t1, t2=110.0, t3=120.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=candles,
        r30_high=105.0, r30_low=103.0, f24_vah=110.0, f24_val=108.0, daily_atr14=2.0,
    )
    assert plan["status"] == "NO_PLAN"
    assert "R:R" in plan["no_plan_reason"]
    assert plan["direction"] == "LONG"  # still recorded, even though NO_PLAN


def test_no_plan_when_atr_unavailable():
    decision = _take_decision()
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=0.0,
    )
    assert plan["status"] == "NO_PLAN"
    assert "unavailable" in plan["no_plan_reason"]


def test_short_side_waiting_plan():
    decision = _take_decision(side="SHORT", tier="PREMIUM", entry=100.0, t1=88.0, t2=80.0, t3=68.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=50.0),
        r30_high=55.0, r30_low=52.0, f24_vah=58.0, f24_val=53.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["direction"] == "SHORT"
    assert plan["tier"] == "PREMIUM"
    assert plan["stop_price"] > plan["trigger_price"]  # SHORT stop sits above entry


def test_render_brief_no_plan():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT",
        "status": "NO_PLAN", "no_plan_reason": "box 7.2x ATR -- T1 unreachable",
    }
    text = tp.render_brief(plan)
    assert "NO_PLAN" in text
    assert "box 7.2x ATR" in text
    assert "does not become a plan later" in text.lower() or "does not become a" in text


def test_render_brief_waiting_plan_has_every_number():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "WAITING",
        "direction": "LONG", "tier": "PREMIUM",
        "trigger_price": 79062.43, "stop_price": 78573.37, "stop_basis": "beyond sweep wick low",
        "t1": 79650.0, "t2": 80100.0, "t3": 80800.0,
        "commit_after": ANCHOR + datetime.timedelta(minutes=45),
        "fuel_requirement": tp.FUEL_REQUIREMENT_TEXT,
        "management": tp.MANAGEMENT_TEXT,
    }
    text = tp.render_brief(plan)
    assert "79,062.43" in text
    assert "78,573.37" in text
    assert "79,650.00" in text
    assert "80,100.00" in text
    assert "80,800.00" in text
    assert "BUY" in text
    assert "PREMIUM" in text
    assert "ORDER 2" in text  # retest-limit fallback always mentioned
