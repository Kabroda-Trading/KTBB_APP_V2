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

def test_lock_email_only_for_waiting_status():
    assert tpn.build_lock_email(_plan("NO_PLAN")) is None
    assert tpn.build_lock_email(_plan("VETOED")) is None


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
