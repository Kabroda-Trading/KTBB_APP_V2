"""
Unit coverage for trade_plan.py's intraday state machine
(KABRODA_COM_TRADE_PLAN_SPEC.md SS5/SS7/SS8): advance_waiting_plan,
mirror_campaign_outcome, check_reentry_eligibility.

Pure-function coverage -- each function takes plain dicts/lists and returns
a plain dict of field updates (or None), so candle sequences are hand-built
to land on specific fuel_gate.py verdicts (NO_PUSH/FUELED/CONFLICTED) via
its documented ratio math (median push volume / prior baseline).
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
    """Baseline bars sit on the near side of trigger, push bars beyond it
    (or, if touched=False, both sides stay near/below -- producing NO_PUSH)."""
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


# ------------------------------------------------------------------ advance_waiting_plan

def test_advance_waiting_held_before_commit_after():
    plan = _plan(commit_after=NOW + datetime.timedelta(minutes=30))
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(touched=True), live_price=105.0)
    assert result is None


def test_advance_waiting_no_touch_returns_none():
    plan = _plan()
    candles = _candles(side="LONG", touched=False)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=95.0)
    assert result is None


def test_advance_waiting_fueled_cross_fills():
    plan = _plan(direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)  # ratio 1.0 -> FUELED
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result is not None
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 100.0
    assert result["fill_time"] == NOW
    assert result["cross_time"] == NOW
    assert result["fuel_at_cross"] == "FUELED"
    assert result["entry_mode"] == "TRIGGER_AT_LEVEL"  # live_price == trigger, not beyond it
    assert "filled" in result["last_transition_reason"]


def test_advance_waiting_fueled_cross_already_broken_out_uses_retest_mode():
    plan = _plan(direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=104.0)
    assert result["entry_mode"] == "RETEST_LIMIT_AT_LINE"


def test_advance_waiting_unfueled_cross_vetoes():
    plan = _plan(direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=2.0, touched=True)  # ratio 0.2 -> thin
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result is not None
    assert result["status"] == "VETOED"
    assert result["fuel_at_cross"] in ("CONFLICTED", "NO_FUEL")
    assert result["entry_mode"] is not None


def test_advance_vetoed_second_cross_fueled_fills():
    plan = _plan(status="VETOED", direction="LONG", trigger=100.0, entry_mode="TRIGGER_AT_LEVEL")
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result["status"] == "FILLED"
    assert "second" in result["last_transition_reason"]
    # entry_mode already set on the plan -- must not be re-decided/overwritten
    assert "entry_mode" not in result


def test_advance_vetoed_second_cross_still_unfueled_done():
    plan = _plan(status="VETOED", direction="LONG", trigger=100.0, entry_mode="TRIGGER_AT_LEVEL")
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=2.0, touched=True)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result["status"] == "DONE"
    assert "no energy" in result["last_transition_reason"]


def test_advance_waiting_session_expiry_no_cross_done():
    plan = _plan(commit_after=NOW - datetime.timedelta(hours=1))
    candles = _candles(side="LONG", touched=False)
    result = tp.advance_waiting_plan(plan, SESSION_EXPIRES, SESSION_EXPIRES, candles, live_price=95.0)
    assert result == {"status": "DONE", "last_transition_reason": "session ended, trigger never crossed"}


def test_advance_ignores_non_waiting_vetoed_statuses():
    for status in ("NO_PLAN", "FILLED", "STOPPED", "DONE", "REENTRY_ARMED", "ARMED"):
        plan = _plan(status=status)
        assert tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, _candles(), live_price=100.0) is None


def test_advance_waiting_short_side_fueled():
    plan = _plan(direction="SHORT", trigger=100.0)
    candles = _candles(side="SHORT", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 100.0


# ------------------------------------------------------------------ mirror_campaign_outcome

def test_mirror_ignores_non_filled_plan():
    plan = _plan(status="WAITING")
    assert tp.mirror_campaign_outcome(plan, "CLOSED_LOSS", "STOP") is None


def test_mirror_ignores_still_open_campaign():
    plan = _plan(status="FILLED")
    assert tp.mirror_campaign_outcome(plan, "PENDING", None) is None
    assert tp.mirror_campaign_outcome(plan, None, None) is None


def test_mirror_stopped_before_t1():
    plan = _plan(status="FILLED")
    result = tp.mirror_campaign_outcome(plan, "CLOSED_LOSS", "STOP")
    assert result["status"] == "STOPPED"
    assert "wick-fake" in result["last_transition_reason"]
    assert isinstance(result["stopped_time"], datetime.datetime)


def test_mirror_done_on_win():
    plan = _plan(status="FILLED")
    result = tp.mirror_campaign_outcome(plan, "CLOSED_WIN", "T3")
    assert result["status"] == "DONE"


def test_mirror_done_on_runner_stop_after_t1():
    plan = _plan(status="FILLED")
    result = tp.mirror_campaign_outcome(plan, "CLOSED_LOSS", "RUNNER_STOP")
    assert result["status"] == "DONE"  # T1 was reached first -- not a wick-fake, not re-entry eligible


def test_mirror_done_on_expiry():
    plan = _plan(status="FILLED")
    result = tp.mirror_campaign_outcome(plan, "CLOSED_AT_EXPIRY", None)
    assert result["status"] == "DONE"


# ------------------------------------------------------------------ check_reentry_eligibility

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
