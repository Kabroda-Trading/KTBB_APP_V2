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
    assert result["faked_first"] is False  # clean first-cross fill, not a retest
    assert "filled" in result["last_transition_reason"]


def test_advance_waiting_fueled_cross_stamps_tier_when_none(monkeypatch):
    # 2026-08-31 fix: a pre-cross-anticipated plan (tier=None at generation)
    # gets its tier stamped at the real cross, once HTF/box-ATR data is
    # actually available.
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BULLISH", "trend_4h": "BULLISH", "aligned": 2, "opposed": 0,
    })
    plan = _plan(direction="LONG", trigger=100.0, tier=None, t2=110.0)  # box=10, atr=25 -> ratio=0.4 -> PREMIUM
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, candles, live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=25.0,
    )
    assert result["status"] == "FILLED"
    assert result["tier"] == "PREMIUM"


def test_advance_waiting_fueled_cross_never_overrides_existing_tier(monkeypatch):
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BULLISH", "trend_4h": "BULLISH", "aligned": 2, "opposed": 0,
    })
    plan = _plan(direction="LONG", trigger=100.0, tier="STANDARD", t2=110.0)  # already known, from the post-cross path
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(
        plan, NOW, SESSION_EXPIRES, candles, live_price=100.0,
        candles_1h=[{}], candles_4h=[{}], daily_atr14=25.0,
    )
    assert "tier" not in result  # never re-decided


def test_advance_waiting_fueled_cross_no_tier_stamp_without_1h4h_data():
    # Missing candles_1h/4h/daily_atr14 -- must not crash, tier stays
    # unset (matches pre-fix behavior for any caller not yet passing them).
    plan = _plan(direction="LONG", trigger=100.0, tier=None)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=10.0, touched=True)
    result = tp.advance_waiting_plan(plan, NOW, SESSION_EXPIRES, candles, live_price=100.0)
    assert result["status"] == "FILLED"
    assert "tier" not in result


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
    assert result["faked_first"] is True  # first cross wicked back (VETOED) before this retest filled
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


# ------------------------------------------------------------------ check_wide_stop_or_t1

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


# ------------------------------------------------------------------ mirror_campaign_outcome

def test_mirror_ignores_non_filled_plan():
    plan = _plan(status="WAITING")
    assert tp.mirror_campaign_outcome(plan, "CLOSED_LOSS") is None


def test_mirror_ignores_still_open_campaign():
    plan = _plan(status="FILLED")
    assert tp.mirror_campaign_outcome(plan, "PENDING") is None
    assert tp.mirror_campaign_outcome(plan, None) is None


def test_mirror_refuses_reentry_fills_even_with_a_terminal_campaign():
    # Regression: CampaignLog has no re-entry concept and is almost always
    # already terminal (from the ORIGINAL fill's own stop-out) by the time
    # a re-entry fills -- mirroring it here would silently close the
    # re-entry out on a stale, unrelated verdict.
    plan = _plan(status="FILLED", reentry_used=True)
    assert tp.mirror_campaign_outcome(plan, "CLOSED_LOSS") is None
    assert tp.mirror_campaign_outcome(plan, "CLOSED_WIN") is None


def test_mirror_never_produces_stopped():
    # CampaignLog's own tighter (r30) stop firing is NOT TradePlan's wide-
    # stop wick-fake -- see the 2026-08-31 CORRECTION in trade_plan.py.
    # mirror_campaign_outcome() must map every terminal CampaignLog status
    # to DONE, never STOPPED -- only check_wide_stop_or_t1() can do that.
    plan = _plan(status="FILLED")
    for campaign_status in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"):
        result = tp.mirror_campaign_outcome(plan, campaign_status)
        assert result["status"] == "DONE"
        assert campaign_status in result["last_transition_reason"]


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


# ------------------------------------------------------------------ advance_reentry_plan

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
    # "One attempt max" -- unlike advance_waiting_plan, an unfueled re-entry
    # cross does NOT arm a second retest watch.
    plan = _plan(status="REENTRY_ARMED", direction="LONG", trigger=100.0)
    candles = _candles(side="LONG", baseline_vol=10.0, push_vol=2.0, touched=True)  # thin
    result = tp.advance_reentry_plan(plan, NOW, SESSION_EXPIRES, candles)
    assert result["status"] == "DONE"
    assert result["reentry_used"] is True


# ------------------------------------------------------------------ resolve_reentry_fill

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
    # WIDE_STOP_FIRST is deliberately routed elsewhere (the same STOPPED
    # transition every FILLED plan gets) -- this function must not produce
    # a transition for it, to avoid a second, conflicting STOPPED path.
    plan = _plan(status="FILLED", reentry_used=True)
    assert tp.resolve_reentry_fill(plan, "WIDE_STOP_FIRST", NOW, SESSION_EXPIRES) is None
