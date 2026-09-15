"""
Unit coverage for trade_plan.py (KABRODA_COM_TRADE_PLAN_SPEC.md SS3/SS4) -- v2.

Rewritten 2026-09-15 for the v2 rebuild (Krown Cross + 4H RSI gate, no tier,
no fuel, SPLIT 50/50 management with no BE-move -- see decision_engine.py's
own header comment). v1's tests (PREMIUM/STANDARD tier stamping,
STANDARD_FUEL_RATIO_FLOOR, PROMOTED_PUSH_FLOOR, fuel-based FILLED/VETOED)
exercised behavior that no longer exists. Pure-function module (build_trade_
plan/render_brief take plain data, no DB/network) -- straightforward to test
with hand-constructed decision dicts and precisely known expected outputs.
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


def _take_decision(side="LONG", entry=100.0, t1=112.0, t2=120.0, t3=132.0):
    return {
        "verdict_state": "TAKE",
        "side": side, "tier": None,
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
    # v2: ONE stop formula for every trade -- r30 edge -+ STOP_BUFFER_BOX*box,
    # no more PREMIUM zone-stop / STANDARD split. entry 100, t2=120 -> box=20
    # (t2 = trigger + T2_BOX*box, T2_BOX=1.0 in v2) -> stop = 99 - 0.12*20 = 96.6.
    decision = _take_decision(side="LONG", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=150.0),
        r30_high=101.0, r30_low=99.0, f24_vah=170.0, f24_val=165.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["direction"] == "LONG"
    assert plan["tier"] is None
    assert plan["trigger_price"] == 100.0
    assert plan["stop_price"] == pytest.approx(96.6)
    assert plan["entry_mode"] is None  # decided at commit, not at generation
    assert plan["rr_floor_ok"] is True
    assert plan["rr_ratio"] > 1.0
    assert plan["t1"] == 112.0 and plan["t2"] == 120.0 and plan["t3"] == 132.0
    assert "50%" in plan["management"]
    assert "PREMIUM" not in plan["management"] and "STANDARD" not in plan["management"]


def test_no_plan_when_r30_stop_kills_rr():
    # v2 has no candles-driven zone stop any more -- an r30_low far below
    # entry is now what forces a wide stop and kills R:R to a close T1.
    entry, t1, t2 = 100.0, 101.0, 110.0
    decision = _take_decision(side="LONG", entry=entry, t1=t1, t2=t2, t3=120.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=50.0, f24_vah=110.0, f24_val=108.0, daily_atr14=2.0,
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
    # r30_high/r30_low sit sensibly around entry now (v2 always uses the r30
    # formula directly, no ATR-fallback zone stop to paper over a mismatched
    # fixture) -- entry 100, r30_high=105 -> stop = 105 + 0.12*20 = 107.4.
    decision = _take_decision(side="SHORT", entry=100.0, t1=88.0, t2=80.0, t3=68.0)
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    assert plan["status"] == "WAITING"
    assert plan["direction"] == "SHORT"
    assert plan["tier"] is None
    assert plan["stop_price"] > plan["trigger_price"]  # SHORT stop sits above entry
    assert plan["stop_price"] == pytest.approx(107.4)


def test_render_brief_no_plan():
    # An unclassified NO_PLAN (no no_plan_category) -- lock_disposition()
    # returns None, so render_brief falls back to no_plan_reason verbatim and
    # makes no claim about whether it can ARM later.
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT",
        "status": "NO_PLAN", "no_plan_reason": "box 7.2x ATR -- T1 unreachable",
    }
    text = tp.render_brief(plan)
    assert "NO_PLAN" in text
    assert "box 7.2x ATR" in text
    assert "NO_PLAN is a valid, common outcome." in text
    assert "does not become a plan later" not in text.lower()  # the stale line is gone


def test_render_brief_no_plan_wide_box_says_final():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "NO_PLAN",
        "no_plan_category": "WIDE_BOX", "box_atr_ratio": 0.89,
        "breakout_trigger": 112340.0, "breakdown_trigger": 108900.0,
    }
    text = tp.render_brief(plan)
    assert "0.89x daily ATR" in text
    assert "cannot become a plan later today" in text
    assert "No ARMED email" in text


def test_render_brief_no_plan_standing_by_says_can_still_arm():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "NO_PLAN",
        "no_plan_category": "NO_DIRECTION", "box_atr_ratio": 0.41,
        "breakout_trigger": 112340.0, "breakdown_trigger": 108900.0,
    }
    text = tp.render_brief(plan)
    assert "can still ARM later" in text
    assert "strong-volume push" in text


def test_no_plan_carries_locked_levels_for_the_lock_email():
    # 2026-09-02 fix (day-4 email-delivery incident): NO_PLAN must still
    # carry the locked bo/bd levels through as transient fields, so the
    # lock-time email can show "the structure being watched" even on a
    # no-trade morning.
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=_pass_decision("gate declined"),
        anchor_time=ANCHOR, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
        breakout_trigger=65500.0, breakdown_trigger=64200.0,
    )
    assert plan["status"] == "NO_PLAN"
    assert plan["breakout_trigger"] == 65500.0
    assert plan["breakdown_trigger"] == 64200.0


def test_render_brief_no_plan_shows_levels_when_available():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT",
        "status": "NO_PLAN", "no_plan_reason": "counter-trend on a GOOD daily table",
        "breakout_trigger": 65500.0, "breakdown_trigger": 64200.0,
    }
    text = tp.render_brief(plan)
    assert "65500" in text or "65,500" in text
    assert "64200" in text or "64,200" in text


def test_render_brief_waiting_plan_has_every_number():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "WAITING",
        "direction": "LONG", "tier": None,
        "trigger_price": 79062.43, "stop_price": 78573.37, "stop_basis": "r30 edge",
        "t1": 79650.0, "t2": 80100.0, "t3": 80800.0,
        "commit_after": ANCHOR + datetime.timedelta(minutes=45),
        "fuel_requirement": tp.GATE_REQUIREMENT_TEXT,
        "management": tp.MANAGEMENT_TEXT,
    }
    text = tp.render_brief(plan)
    assert "79,062.43" in text
    assert "78,573.37" in text
    assert "79,650.00" in text
    assert "80,800.00" in text
    assert "BUY" in text
    assert "ORDER 2" in text  # retest-limit fallback always mentioned
    # v2: no tier line, no PREMIUM/STANDARD language anywhere in the brief.
    assert "Tier" not in text
    assert "PREMIUM" not in text and "STANDARD" not in text


def test_render_brief_has_no_tier_line_at_all():
    # v2 (2026-09-15): the old "Tier: TBD" line promised a stamp-at-cross
    # event that no longer happens -- dropped entirely, not replaced.
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "WAITING",
        "direction": "LONG", "tier": None,
        "trigger_price": 100.0, "stop_price": 95.0, "stop_basis": "r30 edge",
        "t1": 110.0, "t2": 120.0, "t3": 130.0,
        "commit_after": ANCHOR + datetime.timedelta(minutes=45),
        "fuel_requirement": tp.GATE_REQUIREMENT_TEXT,
        "management": tp.MANAGEMENT_TEXT,
    }
    text = tp.render_brief(plan)
    assert "Tier" not in text
    assert "TBD" not in text
    assert "None" not in text


# ------------------------------------------------------------------ classify_alignment() / the email alignment-tier line (v2: HTF-only, no fuel)

def test_classify_alignment_fully_aligned():
    assert tp.classify_alignment("FUELED", 2) == "FULLY ALIGNED"


def test_classify_alignment_partial():
    assert tp.classify_alignment("FUELED", 1) == "PARTIAL"


def test_classify_alignment_conflicted_htf():
    assert tp.classify_alignment("FUELED", 0) == "CONFLICTED"


def test_classify_alignment_ignores_fuel_verdict_entirely():
    # v2: fuel is retired -- the fuel_verdict argument is accepted for call-
    # site compatibility only and never changes the result.
    for raw in ("FUELED", "CONFLICTED", "NO_FUEL", "NO_PUSH", None, "anything"):
        assert tp.classify_alignment(raw, 2) == "FULLY ALIGNED"


def test_classify_alignment_none_when_htf_aligned_missing():
    assert tp.classify_alignment("FUELED", None) is None
    assert tp.classify_alignment(None, None) is None


# ------------------------------------------------------------------ build_alignment_email_line() (v2: HTF-only, no "Fuel <word> |" lead-in)

def test_alignment_email_line_fully_aligned_shows_lead_and_trends():
    plan = {"fuel_verdict": "FUELED", "htf_aligned": 2, "trend_1h": "BULLISH", "trend_4h": "BULLISH"}
    line = tp.build_alignment_email_line(plan)
    assert "as of session lock" in line
    assert "1H trend BULLISH | 4H trend BULLISH | FULLY ALIGNED" in line
    assert "Fuel" not in line
    assert f"{tp._T3_RATE_FULLY_ALIGNED_PCT}%" in line
    assert f"{tp._T3_RATE_PARTIAL_ALIGNED_PCT}%" in line
    assert "how far the trade can run, not whether it wins" in line
    assert "sizing" not in line.lower()
    assert "gate" not in line.lower()


def test_alignment_email_line_partial_uses_partial_stat_wording():
    plan = {"htf_aligned": 1}
    line = tp.build_alignment_email_line(plan)
    assert "PARTIAL" in line
    assert "Partially-aligned setups reached T3" in line


def test_alignment_email_line_conflicted_uses_conflicted_wording():
    plan = {"htf_aligned": 0}
    line = tp.build_alignment_email_line(plan)
    assert "CONFLICTED" in line
    assert "Conflicted setups have historically run less far" in line


def test_alignment_email_line_omits_trend_bits_when_unavailable():
    # No trend_1h/trend_4h on the plan at all -- must not print "1H trend None".
    plan = {"htf_aligned": 2}
    line = tp.build_alignment_email_line(plan)
    assert "1H trend" not in line
    assert "4H trend" not in line
    assert "FULLY ALIGNED" in line


def test_alignment_email_line_none_when_htf_aligned_missing():
    assert tp.build_alignment_email_line({}) is None
    assert tp.build_alignment_email_line({"trend_1h": "BULLISH"}) is None


def test_render_brief_includes_alignment_line_when_available():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "WAITING",
        "direction": "LONG", "tier": None,
        "trigger_price": 79062.43, "stop_price": 78573.37, "stop_basis": "r30 edge",
        "t1": 79650.0, "t2": 80100.0, "t3": 80800.0,
        "commit_after": ANCHOR + datetime.timedelta(minutes=45),
        "fuel_requirement": tp.GATE_REQUIREMENT_TEXT, "management": tp.MANAGEMENT_TEXT,
        "htf_aligned": 2, "trend_1h": "BULLISH", "trend_4h": "BULLISH",
    }
    text = tp.render_brief(plan)
    assert "as of session lock" in text
    assert "1H trend BULLISH | 4H trend BULLISH | FULLY ALIGNED" in text
    assert "how far the trade can run, not whether it wins" in text


def test_render_brief_omits_alignment_line_when_unavailable():
    plan = {
        "date_key": "2026-08-31", "symbol": "BTC/USDT", "status": "WAITING",
        "direction": "LONG", "tier": None,
        "trigger_price": 100.0, "stop_price": 95.0, "stop_basis": "r30 edge",
        "t1": 110.0, "t2": 120.0, "t3": 130.0,
        "commit_after": ANCHOR + datetime.timedelta(minutes=45),
        "fuel_requirement": tp.GATE_REQUIREMENT_TEXT, "management": tp.MANAGEMENT_TEXT,
    }
    text = tp.render_brief(plan)
    assert "Setup strength" not in text


def test_build_trade_plan_carries_htf_aligned_transiently():
    decision = _take_decision(side="LONG")
    decision["htf_aligned"] = 2
    plan = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
    )
    assert plan["htf_aligned"] == 2
    assert plan["fuel_verdict"] is None  # always None now -- fuel is retired


# ------------------------------------------------------------------ build_trade_plan(): pre-cross path
# (2026-08-31, WAITING-visibility fix. anticipate_setup() itself is covered in
# tests/test_anticipate_setup.py; these isolate build_trade_plan()'s own
# NEW branch logic by monkeypatching anticipate_setup directly.)

def _precross_kwargs(candles_24h=None):
    return dict(
        symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures",
        decision_dict=_pass_decision("Price is inside the box -- no trigger crossed yet. Waiting for BO/BD."),
        anchor_time=ANCHOR, candles_24h=candles_24h if candles_24h is not None else _flat_candles(price=90.0),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=25.0,
        breakout_trigger=100.0, breakdown_trigger=90.0,
        candles_15m=[{}], candles_1d=[{}], candles_1h=[{}], candles_4h=[{}],
        session_hour_utc=15,
    )


def test_precross_viable_produces_waiting_plan_with_tier_none(monkeypatch):
    monkeypatch.setattr(tp, "anticipate_setup", lambda *a, **k: {
        "viable": True, "side": "LONG", "reason": "anticipating LONG -- test",
        "category": "VIABLE", "box_atr_ratio": 0.41, "htf_backs_side": True,
    })
    plan = tp.build_trade_plan(**_precross_kwargs())
    assert plan["status"] == "WAITING"
    assert plan["direction"] == "LONG"
    assert plan["tier"] is None
    assert plan["trigger_price"] == 100.0  # breakout_trigger, via decision_engine._plan_for_side
    # last_transition_reason now carries the lock_disposition() headline (the
    # SAME text the radar's Trade Plan panel and the lock email show).
    assert "Plan set" in plan["last_transition_reason"]
    assert "LONG" in plan["last_transition_reason"]
    assert plan["t1"] > plan["trigger_price"]  # box-multiple targets computed for real


def test_precross_not_viable_produces_no_plan(monkeypatch):
    monkeypatch.setattr(tp, "anticipate_setup", lambda *a, **k: {
        "viable": False, "reason": "box/ATR ratio 1.42 > 0.55",
    })
    plan = tp.build_trade_plan(**_precross_kwargs())
    assert plan["status"] == "NO_PLAN"
    assert plan["no_plan_reason"] == "box/ATR ratio 1.42 > 0.55"


def test_precross_missing_inputs_falls_back_to_original_behavior():
    # No breakout_trigger/candles_15m/etc supplied -- an older caller, or
    # simply not wired -- must not crash, must match pre-fix behavior.
    kwargs = _precross_kwargs()
    for k in ("breakout_trigger", "breakdown_trigger", "candles_15m", "candles_1d"):
        kwargs[k] = None
    plan = tp.build_trade_plan(**kwargs)
    assert plan["status"] == "NO_PLAN"
    assert plan["no_plan_reason"] == "Price is inside the box -- no trigger crossed yet. Waiting for BO/BD."


def test_precross_still_respects_rr_floor(monkeypatch):
    monkeypatch.setattr(tp, "anticipate_setup", lambda *a, **k: {
        "viable": True, "side": "LONG", "reason": "anticipating LONG -- test",
    })
    kwargs = _precross_kwargs()
    kwargs["breakout_trigger"], kwargs["breakdown_trigger"] = 100.0, 99.0  # tight box -> T1 close, easy to blow the floor
    kwargs["r30_low"] = 50.0  # far below entry -> wide r30 stop kills R:R, same mechanism as the post-cross path
    plan = tp.build_trade_plan(**kwargs)
    assert plan["status"] == "NO_PLAN"
    assert "R:R" in plan["no_plan_reason"]
    assert plan["direction"] == "LONG"  # still recorded, per the existing post-cross behavior


# ------------------------------------------------------------------ _confirm_v2_gate_at_cross() -- replaces _stamp_tier_at_cross()

def test_confirm_v2_gate_passes_when_all_four_conditions_met(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 110.0, "rsi_4h_at_lock": 70.0}  # box=10, atr=25 -> ratio=0.4
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is True
    assert gate["misses"] == []


def test_confirm_v2_gate_fails_when_htf_not_aligned(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 0})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 110.0, "rsi_4h_at_lock": 70.0}
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is False
    assert any("carry" in m for m in gate["misses"])


def test_confirm_v2_gate_fails_when_krown_cross_not_both(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 1})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 110.0, "rsi_4h_at_lock": 70.0}
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is False
    assert any("Krown Cross" in m for m in gate["misses"])


def test_confirm_v2_gate_fails_when_rsi_outside_zone(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 110.0, "rsi_4h_at_lock": 50.0}
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is False
    assert any("RSI" in m for m in gate["misses"])


def test_confirm_v2_gate_fails_when_rsi_missing(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 110.0}  # no rsi_4h_at_lock key at all
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is False


def test_confirm_v2_gate_fails_when_box_too_wide(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "LONG", "trigger_price": 100.0, "t2": 150.0, "rsi_4h_at_lock": 70.0}  # box=50, atr=25 -> ratio=2.0
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is False


def test_confirm_v2_gate_short_side_rsi_zone(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    plan = {"direction": "SHORT", "trigger_price": 100.0, "t2": 90.0, "rsi_4h_at_lock": 30.0}
    gate = tp._confirm_v2_gate_at_cross(plan, [{}], [{}], daily_atr14=25.0)
    assert gate["pass"] is True


# ------------------------------------------------------------------ advance_no_plan (v2: no tier, no PROMOTED_PUSH_FLOOR)

NOW = datetime.datetime(2026, 9, 2, 15, 0, 0, tzinfo=datetime.timezone.utc)


def _declined_decision(side="SHORT", reason="counter-trend on a GOOD daily table"):
    """A REAL cross (side is set), gate declined -- verdict_state PASS,
    distinct from _pass_decision()'s side=None (no cross yet) case."""
    return {
        "verdict_state": "PASS", "side": side, "tier": None,
        "entry_price": 90.0, "stop_loss": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0,
        "tactical_brief": f"{side}: PASS -- {reason}",
    }


def test_advance_no_plan_on_real_take_goes_to_filled_with_the_r30_stop():
    # entry 100, box 20 (t2=120), r30_low 99 -> stop = 99 - 0.12*20 = 96.6.
    decision = _take_decision(side="LONG", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    updates = tp.advance_no_plan(
        decision, candles_24h=_flat_candles(price=100.0),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
        now_utc=NOW,
    )
    assert updates is not None
    assert updates["status"] == "FILLED"
    assert updates["direction"] == "LONG"
    assert updates["tier"] is None
    assert updates["trigger_price"] == 100.0
    assert updates["t1"] == 112.0 and updates["t2"] == 120.0 and updates["t3"] == 132.0
    assert updates["stop_price"] == pytest.approx(96.6)
    assert updates["stop_price_r30"] == pytest.approx(96.6)
    assert "r30" in updates["stop_basis"]
    assert updates["cross_time"] == NOW
    assert updates["fuel_at_cross"] is None  # fuel is retired -- never fabricated
    assert updates["fill_time"] == NOW
    assert updates["fill_price"] == 100.0  # the trigger, not a "live price"
    assert updates["entry_mode"] == "RETEST_LIMIT_AT_LINE"
    assert updates["faked_first"] is False
    assert "real cross" in updates["last_transition_reason"]


def test_advance_no_plan_returns_none_when_no_cross_yet():
    decision = _pass_decision("Price is inside the box -- no trigger crossed yet.")
    updates = tp.advance_no_plan(
        decision, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
        now_utc=NOW,
    )
    assert updates is None


def test_advance_no_plan_on_real_fail_goes_to_done_with_vetoed_framing():
    decision = _declined_decision(side="SHORT", reason="counter-trend on a GOOD daily table")
    updates = tp.advance_no_plan(
        decision, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=2.0,
        now_utc=NOW,
    )
    assert updates is not None
    assert updates["status"] == "DONE"
    assert updates["cross_time"] == NOW
    assert updates["vetoed_cross_side"] == "SHORT"
    assert updates["vetoed_cross_trigger"] == 90.0
    assert "counter-trend on a GOOD daily table" in updates["last_transition_reason"]


def test_advance_no_plan_returns_none_when_atr_unavailable():
    decision = _take_decision()
    updates = tp.advance_no_plan(
        decision, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=99.0, f24_vah=105.0, f24_val=95.0, daily_atr14=0.0,
        now_utc=NOW,
    )
    assert updates is None


def test_advance_no_plan_returns_none_when_rr_floor_fails():
    decision = _take_decision(side="LONG", entry=100.0, t1=101.0, t2=110.0, t3=120.0)
    updates = tp.advance_no_plan(
        decision, candles_24h=_flat_candles(),
        r30_high=101.0, r30_low=50.0, f24_vah=110.0, f24_val=108.0, daily_atr14=2.0,
        now_utc=NOW,
    )
    assert updates is None


# ------------------------------------------------------------------ advance_waiting_plan end-to-end (v2: no fuel verdict, gate re-check)

def test_advance_waiting_plan_goes_done_not_filled_when_gate_fails_at_cross(monkeypatch):
    # End-to-end: the pre-cross anticipate_setup() path (tier=None at
    # generation) hits a real touch, but Krown Cross only has 1 vote at the
    # cross -- must land on DONE, never FILLED.
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 1})

    base = {"symbol": "BTC/USDT", "date_key": "2026-08-31", "session_id": "us_ny_futures",
            "commit_after": ANCHOR, "fuel_requirement": "", "management": "",
            "breakout_trigger": 100.0, "breakdown_trigger": 80.0,
            "r30_high": 105.0, "r30_low": 95.0, "rsi_4h_at_lock": 70.0,
            "fuel_verdict": None, "htf_aligned": None, "trend_1h": None, "trend_4h": None}
    plan = tp._build_waiting_plan(
        base, "LONG", 100.0, 112.0, 120.0, 132.0, 105.0, 95.0, 110.0, 90.0, 2.0,
        _flat_candles(price=100.0), tier=None, generation_reason="pre-cross anticipated",
    )
    assert plan["tier"] is None
    assert plan["rsi_4h_at_lock"] == 70.0

    now = ANCHOR + datetime.timedelta(hours=1)
    updates = tp.advance_waiting_plan(
        plan, now, session_expires_at=None,
        candles_5m=[{"close": 101.0, "volume": 1.0} for _ in range(300)], live_price=101.0,
        candles_1h=_flat_candles(price=100.0, n=30), candles_4h=_flat_candles(price=100.0, n=30),
        daily_atr14=2.0,
    )
    assert updates is not None
    assert updates["status"] == "DONE"
    assert "Krown Cross" in updates["last_transition_reason"]
    assert "tier" not in updates  # never got a tier -- confirms it didn't fall through to FILLED


def test_advance_waiting_plan_fills_when_gate_passes_at_cross(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 2})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})

    base = {"symbol": "BTC/USDT", "date_key": "2026-08-31", "session_id": "us_ny_futures",
            "commit_after": ANCHOR, "fuel_requirement": "", "management": "",
            "breakout_trigger": 100.0, "breakdown_trigger": 80.0,
            "r30_high": 105.0, "r30_low": 95.0, "rsi_4h_at_lock": 70.0,
            "fuel_verdict": None, "htf_aligned": None, "trend_1h": None, "trend_4h": None}
    plan = tp._build_waiting_plan(
        base, "LONG", 100.0, 112.0, 120.0, 132.0, 105.0, 95.0, 110.0, 90.0, 2.0,
        _flat_candles(price=100.0), tier=None, generation_reason="pre-cross anticipated",
    )

    now = ANCHOR + datetime.timedelta(hours=1)
    updates = tp.advance_waiting_plan(
        plan, now, session_expires_at=None,
        candles_5m=[{"close": 101.0, "volume": 1.0} for _ in range(300)], live_price=101.0,
        candles_1h=_flat_candles(price=100.0, n=30), candles_4h=_flat_candles(price=100.0, n=30),
        daily_atr14=40.0,  # box=20 -> ratio=0.5, within the 0.55 reachability ceiling
    )
    assert updates is not None
    assert updates["status"] == "FILLED"
    assert updates["fill_price"] == 100.0
    assert updates["faked_first"] is False
