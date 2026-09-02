"""
Unit coverage for trade_plan_notify.py (Andy's build request, Kabroda AI
Brain repo AGENT_LOG.md 2026-08-31, "trade-plan email notifications").
Pure-function module (each builder takes a plain plan dict, no DB/network)
-- straightforward to test with hand-constructed plan dicts.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import trade_plan as tp
import trade_plan_notify as tpn


def _plan(status, **extra):
    d = {
        "id": 42, "symbol": "BTC/USDT", "date_key": "2026-08-31", "status": status,
        "direction": "LONG", "tier": "PREMIUM",
        "trigger_price": 79062.43, "stop_price": 78573.37, "stop_basis": "beyond sweep wick low",
        "t1": 79650.0, "t2": 80100.0, "t3": 80800.0,
        "commit_after": None, "fuel_requirement": tp.FUEL_REQUIREMENT_TEXT,
        "management": tp.MANAGEMENT_TEXT, "last_transition_reason": None,
        "reentry_used": False,
    }
    d.update(extra)
    return d


# ------------------------------------------------------------------ build_lock_email

def test_lock_email_fires_for_no_plan_too():
    # 2026-09-02 fix: NO_PLAN mornings used to send nothing (the day-4
    # email-delivery incident, Kabroda AI Brain repo AGENT_LOG.md,
    # 2026-09-02 11:00 CT) -- LOCK now always fires, framed as a briefing.
    mail = tpn.build_lock_email(_plan(
        "NO_PLAN", no_plan_reason="counter-trend on a GOOD daily table",
        breakout_trigger=65500.0, breakdown_trigger=64200.0,
    ))
    assert mail is not None
    subject, body = mail
    assert subject == "KABRODA STAND DOWN - BTCUSDT - do not watch"
    assert "counter-trend on a GOOD daily table" in body
    assert "65500" in body or "65,500" in body  # BO level surfaced
    assert "64200" in body or "64,200" in body  # BD level surfaced
    assert "Plan ID: 42" in body


def test_lock_email_no_plan_without_levels_still_sends():
    # Legacy/no-precross-inputs path -- no breakout_trigger/breakdown_
    # trigger available. Must still send, just without the levels lines.
    mail = tpn.build_lock_email(_plan("NO_PLAN", no_plan_reason="gate state: STAND_DOWN"))
    assert mail is not None
    subject, body = mail
    assert subject == "KABRODA STAND DOWN - BTCUSDT - do not watch"
    assert "gate state: STAND_DOWN" in body


def test_lock_email_no_plan_mentions_possible_followup():
    # 2026-09-02 (Andy's poll-routing decision, Kabroda AI Brain repo
    # AGENT_LOG.md): NO_PLAN rows ARE now polled (trade_plan_engine.py),
    # and a later real cross that clears the full gate promotes to FILLED
    # and emails -- so this copy must NOT claim silence is guaranteed (an
    # earlier version of this test asserted the opposite; that promise
    # became false the moment poll-routing was added and had to be
    # reverted here too).
    _, body = tpn.build_lock_email(_plan("NO_PLAN", no_plan_reason="box/ATR ratio 0.62 > 0.55"))
    assert "armed email" in body.lower()
    assert "clears the full gate" in body.lower()


def test_lock_email_waiting_has_full_brief_and_plan_id():
    mail = tpn.build_lock_email(_plan("WAITING"))
    assert mail is not None
    subject, body = mail
    assert "BTCUSDT" in subject and "LONG" in subject
    assert "79062" in subject
    assert "79,062.43" in body  # the full rendered brief
    assert "Plan ID: 42" in body


# ------------------------------------------------------------------ build_armed_email

def test_armed_email_format():
    subject, body = tpn.build_armed_email(_plan("FILLED"))
    assert subject == "KABRODA ARMED - BTCUSDT LONG @ 79062 - PREMIUM"
    assert "Place/confirm your order now." in body
    assert "Plan ID: 42" in body


def test_armed_email_reentry_note():
    subject, body = tpn.build_armed_email(_plan("FILLED", reentry_used=True))
    assert "(RE-ENTRY)" in subject
    assert "RE-ENTRY: this is the second-break entry" in body


def test_armed_email_falls_back_to_standard_when_tier_missing():
    subject, _ = tpn.build_armed_email(_plan("FILLED", tier=None))
    assert "STANDARD" in subject


# ------------------------------------------------------------------ build_vetoed_email

def test_vetoed_email_format():
    subject, body = tpn.build_vetoed_email(
        _plan("VETOED", last_transition_reason="cross unfueled (CONFLICTED) -- waiting for the retest")
    )
    assert subject == "KABRODA VETOED - BTCUSDT LONG @ 79062 - stand down"
    assert "cross unfueled (CONFLICTED)" in body
    assert "Plan ID: 42" in body


# ------------------------------------------------------------------ build_done_email

def test_done_email_with_direction():
    subject, body = tpn.build_done_email(_plan("DONE", last_transition_reason="management complete (CLOSED_WIN)"))
    assert subject == "KABRODA DONE - BTCUSDT - LONG traded"
    assert "management complete (CLOSED_WIN)" in body
    assert "Plan ID: 42" in body


def test_done_email_no_trade_today():
    subject, _ = tpn.build_done_email(_plan("DONE", direction=None))
    assert "no trade today" in subject


def test_done_email_opposite_break_is_framed_as_vetoed():
    # 2026-09-01 P0 follow-up: an opposite-trigger break with a full-gate
    # verdict attached must read as the VETOED call it actually is, not a
    # generic "nothing happened" DONE.
    plan = _plan(
        "DONE", direction="LONG",
        last_transition_reason="opposite side crossed -- full gate ran: SHORT against a UP daily trend on a good table.",
        opposite_side="SHORT", opposite_trigger=77751.2, gate_headline="SHORT against a UP daily trend...",
    )
    subject, body = tpn.build_done_email(plan)
    assert subject == "KABRODA VETOED - BTCUSDT SHORT @ 77751 - counter-trend"
    assert "full gate ran" in body
    assert "anticipated side (LONG)" in body
    assert "Plan ID: 42" in body


# ------------------------------------------------------------------ notification_for_transition dispatch

def test_dispatch_fills_to_armed():
    mail = tpn.notification_for_transition("WAITING", _plan("FILLED"))
    assert mail is not None
    assert mail[0].startswith("KABRODA ARMED")


def test_dispatch_vetoed():
    mail = tpn.notification_for_transition("WAITING", _plan("VETOED"))
    assert mail is not None
    assert mail[0].startswith("KABRODA VETOED")


def test_dispatch_done():
    mail = tpn.notification_for_transition("STOPPED", _plan("DONE"))
    assert mail is not None
    assert mail[0].startswith("KABRODA DONE")


def test_dispatch_stopped_produces_no_email():
    # STOPPED and REENTRY_ARMED are real, logged transitions -- just not
    # ones in the required-events list.
    assert tpn.notification_for_transition("FILLED", _plan("STOPPED")) is None


def test_dispatch_reentry_armed_produces_no_email():
    assert tpn.notification_for_transition("STOPPED", _plan("REENTRY_ARMED")) is None


def test_dispatch_no_plan_to_done_at_expiry_produces_no_email():
    # 2026-09-02 poll-routing decision: a NO_PLAN row that never gets a
    # real cross transitions to DONE at session expiry purely so it stops
    # being polled forever -- the STAND DOWN lock email already told Andy
    # nothing would follow unless a real cross changed it, so this specific
    # transition must NOT email (that would contradict the promise).
    assert tpn.notification_for_transition("NO_PLAN", _plan("DONE")) is None


def test_dispatch_done_from_other_statuses_still_emails():
    # Only the NO_PLAN->DONE bookkeeping transition is suppressed -- every
    # other path to DONE (a filled trade concluding, a stopped plan's
    # session ending, etc.) is a real event and must still email.
    for prev in ("FILLED", "STOPPED", "VETOED", "REENTRY_ARMED", "WAITING"):
        mail = tpn.notification_for_transition(prev, _plan("DONE"))
        assert mail is not None
        assert mail[0].startswith("KABRODA DONE")
