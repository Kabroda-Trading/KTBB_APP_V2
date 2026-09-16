"""
Unit coverage for traveler_plan_notify.py -- Ruling C (DeepSeek, relayed by
Andy 2026-09-15): GATE_TRAVELER's own email notification hook, following
the exact trade_plan_notify.py pattern. Pure-function module (each builder
takes a plain plan dict, no DB/network) -- same test style as
tests/test_trade_plan_notify.py.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import traveler_plan_notify as tpn


def _plan(status, **extra):
    d = {
        "id": 7, "symbol": "BTC/USDT", "status": status,
        "breakout_trigger": 50000.0, "breakdown_trigger": 49700.0,
        "r30_high": 50000.0, "r30_low": 49700.0, "rsi_4h_at_lock": 55.0,
        "direction": "LONG", "fill_price": 49900.0,
        "stop_price": 49664.0, "t1_price": 50300.0,
        "last_transition_reason": None,
    }
    d.update(extra)
    return d


# ------------------------------------------------------------------ build_traveler_lock_email

def test_lock_email_always_fires_with_the_same_shape():
    subject, body = tpn.build_traveler_lock_email(_plan("WAITING_CROSS"))
    assert subject == "KABRODA TRAVELER LOCK - BTCUSDT - watching for a cross"
    assert "TRAVELER" in body
    assert "50,000.00" in body   # breakout trigger surfaced
    assert "49,700.00" in body   # breakdown trigger surfaced
    assert "55.0" in body        # rsi_4h_at_lock surfaced
    assert "Plan ID: 7" in body


def test_lock_email_handles_missing_levels_without_crashing():
    plan = _plan("WAITING_CROSS", breakout_trigger=None, breakdown_trigger=None,
                 r30_high=None, r30_low=None, rsi_4h_at_lock=None)
    subject, body = tpn.build_traveler_lock_email(plan)
    assert subject == "KABRODA TRAVELER LOCK - BTCUSDT - watching for a cross"
    assert "?" in body


# ------------------------------------------------------------------ build_traveler_armed_email

def test_armed_email_format():
    subject, body = tpn.build_traveler_armed_email(_plan("FILLED"))
    assert subject == "KABRODA TRAVELER ARMED - BTCUSDT LONG @ 49,900"
    assert "TRAVELER" in body
    assert "no real order was placed" in body
    assert "49,900.00" in body
    assert "49,664.00" in body   # stop
    assert "50,300.00" in body   # T1
    assert "Plan ID: 7" in body


def test_armed_email_tagged_as_simulation_not_a_live_signal():
    _, body = tpn.build_traveler_armed_email(_plan("FILLED"))
    assert "simulation" in body.lower()


# ------------------------------------------------------------------ build_traveler_done_email

def test_done_email_tercile_skipped_uses_the_real_reason():
    plan = _plan("TERCILE_SKIPPED", last_transition_reason="LONG cross confirmed at 50,100.00 -- tercile-skipped (RSI-4h-at-lock 45.0) -- not taken, no trade")
    subject, body = tpn.build_traveler_done_email(plan)
    assert subject == "KABRODA TRAVELER DONE - BTCUSDT - stand down"
    assert "tercile-skipped" in body
    assert "Plan ID: 7" in body


def test_done_email_opposite_trigger_break_uses_the_real_reason():
    plan = _plan("DONE", last_transition_reason="opposite trigger (49,700.00) broke before any pullback fill -- journey ended, not taken")
    subject, body = tpn.build_traveler_done_email(plan)
    assert subject == "KABRODA TRAVELER DONE - BTCUSDT - stand down"
    assert "opposite trigger" in body


def test_done_email_journey_cap_uses_the_real_reason():
    plan = _plan("DONE", last_transition_reason="7-day journey cap reached with no pullback fill -- not taken")
    _, body = tpn.build_traveler_done_email(plan)
    assert "7-day journey cap" in body


# ------------------------------------------------------------------ notification_for_traveler_transition dispatch

def test_dispatch_filled_to_armed():
    mail = tpn.notification_for_traveler_transition("WAITING_PULLBACK", _plan("FILLED"))
    assert mail is not None
    assert mail[0].startswith("KABRODA TRAVELER ARMED")


def test_dispatch_tercile_skipped_to_done_family():
    mail = tpn.notification_for_traveler_transition("WAITING_CROSS", _plan("TERCILE_SKIPPED"))
    assert mail is not None
    assert mail[0].startswith("KABRODA TRAVELER DONE")


def test_dispatch_done():
    mail = tpn.notification_for_traveler_transition("WAITING_PULLBACK", _plan("DONE"))
    assert mail is not None
    assert mail[0].startswith("KABRODA TRAVELER DONE")


def test_dispatch_waiting_cross_to_waiting_pullback_produces_no_email():
    # A real, logged transition, but not one of the three required events --
    # same "not everything gets emailed" philosophy as v2's own STOPPED/
    # REENTRY_ARMED transitions.
    assert tpn.notification_for_traveler_transition("WAITING_CROSS", _plan("WAITING_PULLBACK")) is None
