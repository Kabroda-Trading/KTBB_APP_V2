"""
Unit coverage for traveler_plan_notify.py -- Ruling C (DeepSeek, relayed by
Andy 2026-09-15): GATE_TRAVELER's own email notification hook, following
the trade_plan_notify.py pattern that module was originally modeled on
(trade_plan_notify.py itself deleted 2026-09-24, V2 Crown retirement).
Pure-function module (each builder takes a plain plan dict, no DB/network).

2026-09-26 rewrite (Andy's own request, real production email -- see
AGENT_LOG.md this date): every LOCK/ARMED/DONE body used to claim
"TRAVELER (evaluation lineage, DRY_RUN only)" unconditionally -- true only
because no LIVE GATE_TRAVELER account existed yet when this module was
built; false as of today (Andy_Bitunix and dawson_bitu are both LIVE).
Rewritten to plain trader language with no DRY_RUN/lineage claim on those
three (they're plan-level, not per-account, so the claim was never really
correct even before today). CLOSED keeps its real is_live distinction --
that one's per-order, and the claim there was always true.
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
        "cross_price": None, "rsi_4h_at_cross": None,
        "last_transition_reason": None,
    }
    d.update(extra)
    return d


# ------------------------------------------------------------------ build_traveler_lock_email

def test_lock_email_always_fires_with_the_same_shape():
    subject, body = tpn.build_traveler_lock_email(_plan("WAITING_CROSS"))
    assert subject == "KABRODA - BTCUSDT - Levels Locked"
    assert "TRAVELER" not in body        # 2026-09-26: no internal jargon in the trader-facing body
    assert "DRY_RUN" not in body         # 2026-09-26: no longer a true claim at the plan level
    assert "50,000.00" in body           # breakout trigger surfaced
    assert "49,700.00" in body           # breakdown trigger surfaced
    assert "55.0" in body                # rsi_4h_at_lock surfaced
    assert "Ref: #7" in body


def test_lock_email_handles_missing_levels_without_crashing():
    plan = _plan("WAITING_CROSS", breakout_trigger=None, breakdown_trigger=None,
                 r30_high=None, r30_low=None, rsi_4h_at_lock=None)
    subject, body = tpn.build_traveler_lock_email(plan)
    assert subject == "KABRODA - BTCUSDT - Levels Locked"
    assert "?" in body


# ------------------------------------------------------------------ build_traveler_armed_email

def test_armed_email_format():
    subject, body = tpn.build_traveler_armed_email(_plan("FILLED"))
    assert subject == "KABRODA - BTCUSDT LONG - Position Opened @ 49,900"
    assert "TRAVELER" not in body
    assert "DRY_RUN" not in body
    assert "simulation" not in body.lower()   # 2026-09-26: no longer a true claim at the plan level
    assert "49,900.00" in body
    assert "49,664.00" in body   # stop
    assert "50,300.00" in body   # target
    assert "Ref: #7" in body


# ------------------------------------------------------------------ build_traveler_done_email

def test_done_email_tercile_skipped_uses_plain_language_not_the_raw_reason():
    plan = _plan("TERCILE_SKIPPED", cross_price=50100.0, rsi_4h_at_cross=45.0,
                 last_transition_reason="LONG cross confirmed at 50,100.00 -- tercile-skipped (RSI-4h-at-cross 45.0) -- not taken, no trade")
    subject, body = tpn.build_traveler_done_email(plan)
    assert subject == "KABRODA - BTCUSDT - No Trade"
    assert "tercile-skipped" not in body   # 2026-09-26: jargon removed from the trader-facing body
    assert "outside system guidelines" in body
    assert "50,100.00" in body
    assert "45.0" in body
    assert "Ref: #7" in body


def test_done_email_opposite_trigger_break_uses_a_generic_headline_plus_detail():
    plan = _plan("DONE", cross_price=50100.0,
                 last_transition_reason="opposite trigger (49,700.00) broke before any trigger touch fill -- journey ended, not taken")
    subject, body = tpn.build_traveler_done_email(plan)
    assert subject == "KABRODA - BTCUSDT - No Trade"
    assert "No trade taken this session." in body
    assert "opposite trigger" in body   # kept as a secondary detail line, not the headline


def test_done_email_journey_cap_uses_a_generic_headline_plus_detail():
    plan = _plan("DONE", last_transition_reason="7-day journey cap reached with no trigger touch fill -- not taken")
    _, body = tpn.build_traveler_done_email(plan)
    assert "No trade taken this session." in body
    assert "7-day journey cap" in body


# ------------------------------------------------------------------ build_traveler_management_event_email (2026-09-23)

def _order(**extra):
    d = {
        "symbol": "BTC/USDT", "direction": "LONG", "exit_reason": "T1",
        "exit_price": 106.18, "realized_pnl_r": 1.0, "traveler_plan_id": 42,
        "approximated": False,
    }
    d.update(extra)
    return d


def test_management_event_email_dry_run_shape():
    subject, body = tpn.build_traveler_management_event_email(_order(exit_reason="STOP", exit_price=90.0, realized_pnl_r=-1.0), is_live=False)
    assert subject == "KABRODA - BTCUSDT LONG - Closed (stop hit) @ 90"
    assert "Simulated close -- no real order was placed." in body
    assert "90.00" in body
    assert "-1.0000R" in body
    assert "Ref: #42" in body


def test_management_event_email_includes_account_label_when_given():
    # 2026-09-27 item 4 -- which account this closure applies to.
    _, body = tpn.build_traveler_management_event_email(_order(account_label="andy_bitunix_main"), is_live=True)
    assert "Account: andy_bitunix_main" in body


def test_management_event_email_falls_back_to_account_id_when_no_label():
    _, body = tpn.build_traveler_management_event_email(_order(account_id=7), is_live=True)
    assert "Account: account #7" in body


def test_management_event_email_omits_account_line_when_neither_given():
    _, body = tpn.build_traveler_management_event_email(_order(), is_live=True)
    assert "Account:" not in body


def test_management_event_email_live_shape():
    subject, body = tpn.build_traveler_management_event_email(_order(), is_live=True)
    assert subject == "KABRODA - BTCUSDT LONG - Closed (target hit (T1)) @ 106"
    assert "Real order -- live money." in body
    assert "106.18" in body
    assert "+1.0000R" in body


def test_management_event_email_every_exit_reason_gets_a_human_label():
    labels = {
        "STOP": "stop hit",
        "C5_EXIT": "momentum-decay exhaustion (C5) exit",
        "BBWP_EXIT": "BBWP volatility-burnout exit",
        "T1": "target hit (T1)",
        "TIME": "journey time-cap exit",
    }
    for reason, label in labels.items():
        _, body = tpn.build_traveler_management_event_email(_order(exit_reason=reason), is_live=True)
        assert label in body


def test_management_event_email_unknown_exit_reason_falls_back_to_the_raw_value():
    _, body = tpn.build_traveler_management_event_email(_order(exit_reason="SOMETHING_NEW"), is_live=True)
    assert "SOMETHING_NEW" in body   # never a fabricated label for a reason this module doesn't recognize


def test_management_event_email_approximated_caveat_only_when_flagged_and_live():
    # Validated mapping (Plan-agent pass, 2026-09-23): approximated only
    # ever applies on LIVE, and this function trusts the caller's flag
    # rather than re-deriving it -- so even a DRY_RUN order incorrectly
    # passed approximated=True must NOT show the caveat (is_live=False
    # gates it, matching "DRY_RUN never approximates" being structurally
    # true regardless of what a caller passes).
    _, live_approx = tpn.build_traveler_management_event_email(_order(exit_reason="C5_EXIT", approximated=True), is_live=True)
    assert "approximated" in live_approx.lower()

    _, live_not_approx = tpn.build_traveler_management_event_email(_order(exit_reason="STOP", approximated=False), is_live=True)
    assert "approximated" not in live_not_approx.lower()

    _, dry_run_even_if_flagged = tpn.build_traveler_management_event_email(_order(exit_reason="C5_EXIT", approximated=True), is_live=False)
    assert "approximated" not in dry_run_even_if_flagged.lower()


def test_management_event_email_handles_missing_values_without_crashing():
    order = _order(exit_price=None, realized_pnl_r=None)
    subject, body = tpn.build_traveler_management_event_email(order, is_live=True)
    assert "?" in body   # _fmt()'s own None-safe placeholder, not a crash


# ------------------------------------------------------------------ build_traveler_real_fill_email (2026-09-27)
# Andy ruling 14:55 CT, items 5/6: the genuinely real, per-account
# "position opened" event -- fires only once executor_live_e1_engine.py's
# check_traveler_entry_fill_and_protect() confirms a real exchange fill
# (get_order_detail() status=="FILLED"), never from gate_traveler.py's own
# candle-only touch simulation (which the shared, plan-level ARMED email
# describes and cannot honestly attribute to a specific account).

def _fill_order(**extra):
    d = {
        "symbol": "BTC/USDT", "direction": "LONG",
        "entry_fill_price": 85123.71, "stop_price": 84657.86, "t1_price": 86311.56,
        "risk_dollars_used": 100.0, "traveler_plan_id": 11,
        "account_id": 1, "account_label": "andy_bitunix_main",
    }
    d.update(extra)
    return d


def test_real_fill_email_format():
    subject, body = tpn.build_traveler_real_fill_email(_fill_order())
    assert subject == "KABRODA - BTCUSDT LONG - Real Fill Confirmed @ 85,124 (andy_bitunix_main)"
    assert "andy_bitunix_main" in body
    assert "85,123.71" in body
    assert "84,657.86" in body   # stop
    assert "86,311.56" in body   # target
    assert "$100.00" in body     # risk dollars
    assert "Ref: #11" in body


def test_real_fill_email_falls_back_to_account_id_when_no_label():
    subject, body = tpn.build_traveler_real_fill_email(_fill_order(account_label=None, account_id=11))
    assert "account #11" in subject
    assert "account #11" in body


def test_real_fill_email_handles_missing_values_without_crashing():
    order = _fill_order(entry_fill_price=None, stop_price=None, t1_price=None, risk_dollars_used=None)
    subject, body = tpn.build_traveler_real_fill_email(order)
    assert "?" in body


# ------------------------------------------------------------------ notification_for_traveler_transition dispatch

def test_dispatch_filled_to_armed():
    mail = tpn.notification_for_traveler_transition("WAITING_TOUCH", _plan("FILLED"))
    assert mail is not None
    assert "Position Opened" in mail[0]


def test_dispatch_tercile_skipped_to_done_family():
    mail = tpn.notification_for_traveler_transition("WAITING_CROSS", _plan("TERCILE_SKIPPED"))
    assert mail is not None
    assert "No Trade" in mail[0]


def test_dispatch_done():
    mail = tpn.notification_for_traveler_transition("WAITING_TOUCH", _plan("DONE"))
    assert mail is not None
    assert "No Trade" in mail[0]


def test_dispatch_waiting_cross_to_waiting_touch_produces_no_email():
    # A real, logged transition, but not one of the three required events --
    # same "not everything gets emailed" philosophy as v2's own STOPPED/
    # REENTRY_ARMED transitions.
    assert tpn.notification_for_traveler_transition("WAITING_CROSS", _plan("WAITING_TOUCH")) is None


# ------------------------------------------------------------------ session-date subject tag (2026-10-07, Andy directive 08:28 CT)
# Every traveler email subject carries [date_key] so a reader can never
# confuse a stale/delayed alert with today's -- the exact confusion the
# 2026-10-02 -> 2026-10-07 incident (traveler_plans.id=16) produced. One
# per-builder assertion, plus one proving the tag is cleanly omitted (not
# a literal "[None]") when date_key is genuinely absent.

def test_lock_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_lock_email(_plan("WAITING_CROSS", date_key="2026-10-07"))
    assert subject == "KABRODA - [2026-10-07] BTCUSDT - Levels Locked"


def test_armed_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_armed_email(_plan("FILLED", date_key="2026-10-07"))
    assert "[2026-10-07]" in subject


def test_done_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_done_email(_plan("DONE", date_key="2026-10-07"))
    assert "[2026-10-07]" in subject


def test_management_event_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_management_event_email(_order(date_key="2026-10-07"), is_live=True)
    assert "[2026-10-07]" in subject


def test_real_fill_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_real_fill_email(_fill_order(date_key="2026-10-07"))
    assert "[2026-10-07]" in subject


def test_rearm_watch_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_rearm_watch_email(_plan("FILLED", date_key="2026-10-07"))
    assert "[2026-10-07]" in subject


def test_rearm_armed_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_rearm_armed_email(_plan("FILLED", date_key="2026-10-07", rearm_fill_price=86200.0))
    assert "[2026-10-07]" in subject


def test_rearm_done_email_carries_the_date_tag():
    subject, _ = tpn.build_traveler_rearm_done_email(_plan("FILLED", date_key="2026-10-07", rearm_status="REARM_WINDOW_CLOSED"))
    assert "[2026-10-07]" in subject


def test_date_tag_cleanly_omitted_not_a_literal_none_when_absent():
    subject, _ = tpn.build_traveler_lock_email(_plan("WAITING_CROSS"))  # no date_key at all
    assert subject == "KABRODA - BTCUSDT - Levels Locked"
    assert "None" not in subject
