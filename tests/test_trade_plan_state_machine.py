"""
Unit coverage for trade_plan.py's intraday state machine
(KABRODA_COM_TRADE_PLAN_SPEC.md SS5/SS7/SS8): advance_waiting_plan,
mirror_campaign_outcome, check_reentry_eligibility.

Rewritten 2026-09-15 for v2 (Krown Cross + 4H RSI gate, no fuel). v1's fuel-
based tests (FUELED/CONFLICTED/NO_FUEL verdicts via fuel_gate.py, tier
stamping, VETOED-then-retest) exercised behavior that no longer exists --
advance_waiting_plan() now checks `live_price` directly against the trigger
(no more fuel_gate.evaluate_fuel_gate() call at all) and re-checks the real
4-condition v2 gate via _confirm_v2_gate_at_cross() once touched. The
opposite-trigger-break detection and check_wide_stop_or_t1()/
mirror_campaign_outcome() tests are unchanged -- neither was ever fuel-
specific.

check_reentry_eligibility()/advance_reentry_plan() are RETIRED in v2 (SS8
re-entry-after-wick-fake had no v2-consistent replacement signal -- see
trade_plan_engine.py's own header comment, "no leg 2" reasoning). The
functions still exist in trade_plan.py, unreachable from trade_plan_engine.py,
kept in case a future v2-native re-entry design is built -- their tests stay
as pure-function coverage of code that still exists, just isn't called live.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import trade_plan as tp

NOW = datetime.datetime(2026, 8, 31, 14, 0, 0, tzinfo=datetime.timezone.utc)
COMMIT_AFTER = NOW - datetime.timedelta(minutes=1)  # already past commit by default
SESSION_EXPIRES = NOW + datetime.timedelta(hours=6)


def _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, baseline_n=250, push_n=6,
             trigger=100.0, touched=True):
    """v2 no longer reads candles_5m for the touch check (live_price decides
    that directly) -- kept only because advance_waiting_plan()'s signature
    still accepts candles_5m for call-site compatibility. Content is inert."""
    near = 95.0 if side == "LONG" else 105.0
    beyond = 105.0 if side == "LONG" else 95.0
    candles = [{"close": near, "volume": baseline_vol} for _ in range(baseline_n)]
    if touched:
        candles += [{"close": beyond, "volume": push_vol} for _ in range(push_n)]
    else:
        candles += [{"close": near, "volume": push_vol} for _ in range(push_n)]
    return candles


def _plan(status="WAITING", direction="LONG", trigger=100.0, commit_after=COMMIT_AFTER,
          entry_mode=None, **extra):
    d = {"status": status, "direction": direction, "trigger_price": trigger,
         "commit_after": commit_after, "entry_mode": entry_mode}
    d.update(extra)
    return d


def _pass_gate(monkeypatch, votes=2, aligned=2):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": aligned})
    monkeypatch.setattr(_htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": votes})


# ------------------------------------------------------------------ advance_waiting_plan: touch / opposite-break (unchanged by v2)

def test_advance_waiting_held_before_commit_after():
    plan = _plan(commit_after=NOW + datetime.timedelta(minutes=30))
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(touched=True), live_price=105.0)
    assert result is None


def test_advance_waiting_no_touch_returns_none():
    plan = _plan()
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=95.0)
    assert result is None


# P0 regression (2026-09-01, confirmed live -- Kabroda AI Brain repo
# AGENT_LOG.md "CONFIRMED P0: state machine missed a live cross"): a
# LONG-anticipated plan sat WAITING forever while price broke DOWN through
# the OPPOSITE trigger -- a real, expected scenario the anticipation
# heuristic doesn't cover on its own.

def test_advance_waiting_detects_opposite_side_break_p0():
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=85.0)
    assert result is not None
    assert result["status"] == "DONE"
    assert "OPPOSITE trigger" in result["last_transition_reason"]
    assert "90.00" in result["last_transition_reason"]
    assert "SHORT" in result["last_transition_reason"]


def test_advance_waiting_opposite_break_short_side():
    plan = _plan(direction="SHORT", trigger=90.0, t2=80.0)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=105.0)
    assert result["status"] == "DONE"
    assert "OPPOSITE trigger" in result["last_transition_reason"]
    assert "100.00" in result["last_transition_reason"]
    assert "LONG" in result["last_transition_reason"]


def test_advance_waiting_neither_side_touched_still_returns_none():
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=95.0)
    assert result is None


def test_advance_waiting_opposite_check_safe_without_t2():
    plan = _plan(direction="LONG", trigger=100.0)  # no t2
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=85.0)
    assert result is None


# ------------------------------------------------------------------ advance_waiting_plan: gate re-check at the real cross (v2)

def test_advance_waiting_fills_when_gate_passes(monkeypatch):
    _pass_gate(monkeypatch, votes=2, aligned=2)
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,  # box=10 -> ratio=0.25
    )
    assert result is not None
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 100.0
    assert result["fill_time"] == NOW
    assert result["cross_time"] == NOW
    assert result["entry_mode"] == "TRIGGER_AT_LEVEL"  # live_price == trigger, not beyond it
    assert result["faked_first"] is False
    assert "gate passed" in result["last_transition_reason"]


def test_advance_waiting_done_when_gate_declines(monkeypatch):
    _pass_gate(monkeypatch, votes=1, aligned=2)  # Krown Cross short one vote
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,
    )
    assert result is not None
    assert result["status"] == "DONE"
    assert "Krown Cross" in result["last_transition_reason"]
    assert result["cross_time"] == NOW  # preserved even on the decline path


def test_advance_waiting_no_gate_recheck_without_1h4h_data_returns_none():
    # Missing candles_1h/4h/daily_atr14 -- can't re-check the gate this poll,
    # must return None (try again next poll), not guess FILLED or DONE.
    plan = _plan(direction="LONG", trigger=100.0)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0)
    assert result is None


def test_advance_waiting_already_broken_out_uses_retest_mode(monkeypatch):
    _pass_gate(monkeypatch, votes=2, aligned=2)
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(), live_price=104.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,
    )
    assert result["entry_mode"] == "RETEST_LIMIT_AT_LINE"


def test_advance_waiting_entry_mode_not_recomputed_once_set(monkeypatch):
    _pass_gate(monkeypatch, votes=2, aligned=2)
    plan = _plan(direction="LONG", trigger=100.0, t2=110.0, rsi_4h_at_lock=70.0,
                 entry_mode="TRIGGER_AT_LEVEL")
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(), live_price=104.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,
    )
    assert "entry_mode" not in result  # already set on the plan -- not re-decided


def test_advance_waiting_short_side_fills(monkeypatch):
    _pass_gate(monkeypatch, votes=2, aligned=2)
    plan = _plan(direction="SHORT", trigger=100.0, t2=90.0, rsi_4h_at_lock=30.0)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(side="SHORT"), live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,
    )
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 100.0


def test_advance_waiting_legacy_vetoed_status_still_gets_checked(monkeypatch):
    # A row left over from before v2 (status=="VETOED", no more code path
    # writes this) is treated the same as WAITING -- there's nothing left
    # to distinguish them by (see advance_waiting_plan()'s own docstring).
    _pass_gate(monkeypatch, votes=2, aligned=2)
    plan = _plan(status="VETOED", direction="LONG", trigger=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=40.0,
    )
    assert result["status"] == "FILLED"


def test_advance_waiting_session_expiry_no_cross_done():
    plan = _plan(commit_after=NOW - datetime.timedelta(hours=1))
    result = tp.advance_waiting_plan(plan, SESSION_EXPIRES, SESSION_EXPIRES, _candles(touched=False), live_price=95.0)
    assert result == {"status": "DONE", "last_transition_reason": "session ended, trigger never crossed"}


def test_advance_ignores_non_waiting_vetoed_statuses():
    for status in ("NO_PLAN", "FILLED", "STOPPED", "DONE", "REENTRY_ARMED", "ARMED"):
        plan = _plan(status=status)
        assert tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0) is None


# ------------------------------------------------------------------ check_wide_stop_or_t1 (unchanged by v2)

def _c1m(l, h):
    return {"l": l, "h": h, "ts": 0}


def test_wide_stop_ignores_non_filled_plan():
    plan = _plan(status="WAITING", stop_price=90.0, t1=112.0)
    assert tp.check_wide_stop_or_t1(plan, [_c1m(89.0, 91.0)]) is None


def test_wide_stop_missing_fields_returns_none():
    plan = _plan(status="FILLED")  # no stop_price/t1 set
    assert tp.check_wide_stop_or_t1(plan, [_c1m(89.0, 91.0)]) is None


def test_wide_stop_hit_before_t1_long():
    plan = _plan(status="FILLED", direction="LONG", trigger=100.0, stop_price=90.0, t1=112.0)
    candles = [_c1m(98, 101), _c1m(89.0, 99.0), _c1m(113.0, 114.0)]  # stop touched on candle 2, t1 on candle 3
    assert tp.check_wide_stop_or_t1(plan, candles) == "WIDE_STOP_FIRST"


def test_wide_stop_t1_reached_first_long():
    plan = _plan(status="FILLED", direction="LONG", trigger=100.0, stop_price=90.0, t1=112.0)
    candles = [_c1m(98, 101), _c1m(111.0, 113.0), _c1m(89.0, 91.0)]  # t1 touched before the later stop dip
    assert tp.check_wide_stop_or_t1(plan, candles) == "T1_FIRST"


def test_wide_stop_neither_touched_yet():
    plan = _plan(status="FILLED", direction="LONG", trigger=100.0, stop_price=90.0, t1=112.0)
    candles = [_c1m(98, 101), _c1m(97.0, 102.0)]
    assert tp.check_wide_stop_or_t1(plan, candles) == "NEITHER_YET"


def test_wide_stop_same_candle_ambiguity_stop_wins_long():
    plan = _plan(status="FILLED", direction="LONG", trigger=100.0, stop_price=90.0, t1=112.0)
    candles = [_c1m(89.0, 113.0)]  # one wild candle touches both -- conservative stop-first
    assert tp.check_wide_stop_or_t1(plan, candles) == "WIDE_STOP_FIRST"


def test_wide_stop_short_side():
    plan = _plan(status="FILLED", direction="SHORT", trigger=100.0, stop_price=110.0, t1=88.0)
    candles = [_c1m(99.0, 101.0), _c1m(87.0, 89.0)]  # t1 touched, stop never approached
    assert tp.check_wide_stop_or_t1(plan, candles) == "T1_FIRST"


# ------------------------------------------------------------------ mirror_campaign_outcome (unchanged by v2)

def test_mirror_ignores_non_filled_plan():
    plan = _plan(status="WAITING")
    assert tp.mirror_campaign_outcome(plan, "CLOSED_LOSS") is None


def test_mirror_ignores_still_open_campaign():
    plan = _plan(status="FILLED")
    assert tp.mirror_campaign_outcome(plan, "PENDING") is None
    assert tp.mirror_campaign_outcome(plan, None) is None


def test_mirror_refuses_reentry_fills_even_with_a_terminal_campaign():
    plan = _plan(status="FILLED", reentry_used=True)
    assert tp.mirror_campaign_outcome(plan, "CLOSED_LOSS") is None
    assert tp.mirror_campaign_outcome(plan, "CLOSED_WIN") is None


def test_mirror_never_produces_stopped():
    plan = _plan(status="FILLED")
    for campaign_status in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"):
        result = tp.mirror_campaign_outcome(plan, campaign_status)
        assert result["status"] == "DONE"
        assert campaign_status in result["last_transition_reason"]


# ------------------------------------------------------------------ check_reentry_eligibility / advance_reentry_plan / resolve_reentry_fill
# RETIRED from the live flow (trade_plan_engine.py no longer calls any of
# these -- see that file's own header comment), kept as pure-function
# coverage of code that still exists in trade_plan.py unreachable, in case
# a v2-native re-entry design is built on top of it later. Unchanged by v2
# since none of it ever depended on tier.

def test_reentry_armed_when_fuel_still_fueled():
    plan = _plan(status="STOPPED")
    result = tp.check_reentry_eligibility(plan, fuel_still_fueled=True)
    assert result["status"] == "REENTRY_ARMED"


def test_reentry_done_when_fuel_not_fueled():
    plan = _plan(status="STOPPED")
    result = tp.check_reentry_eligibility(plan, fuel_still_fueled=False)
    assert result["status"] == "DONE"


def test_reentry_done_when_already_used():
    plan = _plan(status="STOPPED", reentry_used=True)
    result = tp.check_reentry_eligibility(plan, fuel_still_fueled=True)
    assert result["status"] == "DONE"
    assert "already used" in result["last_transition_reason"]


def test_reentry_done_when_not_stopped():
    plan = _plan(status="FILLED")
    result = tp.check_reentry_eligibility(plan, fuel_still_fueled=True)
    assert result["status"] == "DONE"
    assert "not eligible" in result["last_transition_reason"]


def test_reentry_advance_ignores_non_armed_status():
    for status in ("WAITING", "VETOED", "FILLED", "STOPPED", "DONE", "NO_PLAN"):
        plan = _plan(status=status)
        assert tp.advance_reentry_plan(plan, NOW, SESSION_EXPIRES, _candles()) is None


def test_reentry_advance_session_expiry_no_cross():
    plan = _plan(status="REENTRY_ARMED", direction="LONG", trigger=100.0)
    result = tp.advance_reentry_plan(plan, SESSION_EXPIRES, SESSION_EXPIRES, _candles(touched=False))
    assert result["status"] == "DONE"
    assert result["reentry_used"] is True
    assert "window closed" in result["last_transition_reason"]


def test_reentry_advance_no_touch_returns_none():
    plan = _plan(status="REENTRY_ARMED", direction="LONG", trigger=100.0)
    result = tp.advance_reentry_plan(plan, NOW, SESSION_EXPIRES, _candles(side="LONG", touched=False))
    assert result is None


def test_reentry_advance_fueled_cross_fills():
    plan = _plan(status="REENTRY_ARMED", direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)  # ratio 1.0 -> FUELED
    result = tp.advance_reentry_plan(plan, NOW, SESSION_EXPIRES, candles)
    assert result["status"] == "FILLED"
    assert result["reentry_used"] is True
    assert result["reentry_fill_price"] == 100.0
    assert result["reentry_cross_time"] == NOW
    assert result["fill_price"] == 100.0
    assert "one attempt used" in result["last_transition_reason"]


def test_reentry_advance_unfueled_cross_goes_straight_to_done():
    plan = _plan(status="REENTRY_ARMED", direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=2.0, touched=True)  # thin
    result = tp.advance_reentry_plan(plan, NOW, SESSION_EXPIRES, candles)
    assert result["status"] == "DONE"
    assert result["reentry_used"] is True


def test_resolve_reentry_ignores_non_reentry_plans():
    plan = _plan(status="FILLED", reentry_used=False)
    assert tp.resolve_reentry_fill(plan, "T1_FIRST", NOW, SESSION_EXPIRES) is None


def test_resolve_reentry_ignores_non_filled_status():
    plan = _plan(status="STOPPED", reentry_used=True)
    assert tp.resolve_reentry_fill(plan, "T1_FIRST", NOW, SESSION_EXPIRES) is None


def test_resolve_reentry_t1_first_is_done_with_documented_gap():
    plan = _plan(status="FILLED", reentry_used=True)
    result = tp.resolve_reentry_fill(plan, "T1_FIRST", NOW, SESSION_EXPIRES)
    assert result["status"] == "DONE"
    assert "documented gap" in result["last_transition_reason"]


def test_resolve_reentry_neither_yet_keeps_polling():
    plan = _plan(status="FILLED", reentry_used=True)
    assert tp.resolve_reentry_fill(plan, "NEITHER_YET", NOW, SESSION_EXPIRES) is None


def test_resolve_reentry_session_expired_becomes_done():
    plan = _plan(status="FILLED", reentry_used=True)
    result = tp.resolve_reentry_fill(plan, "NEITHER_YET", SESSION_EXPIRES, SESSION_EXPIRES)
    assert result["status"] == "DONE"
    assert "unresolved" in result["last_transition_reason"]


def test_resolve_reentry_does_not_handle_wide_stop_first():
    plan = _plan(status="FILLED", reentry_used=True)
    assert tp.resolve_reentry_fill(plan, "WIDE_STOP_FIRST", NOW, SESSION_EXPIRES) is None
