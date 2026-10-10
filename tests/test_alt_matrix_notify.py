"""Unit coverage for alt_matrix_notify.py -- pure-function email builders
(plain dicts in, (subject, body) tuples out, no DB/network), mirroring
tests/test_traveler_plan_notify.py's own style."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alt_matrix_notify as amn


def _plan(status, **extra):
    d = {
        "id": 7, "symbol": "SOL/USDT", "status": status, "date_key": "2026-10-09",
        "ema21": 101.5, "ema55": 100.0, "atr14": 2.5,
    }
    d.update(extra)
    return d


def _order(**extra):
    d = {
        "id": 42, "alt_matrix_plan_id": 7, "symbol": "SOL/USDT", "account_id": 3,
        "entry_fill_price": 101.0, "sl_price_initial": 98.0, "risk_dollars_used": 150.0,
        "date_key": "2026-10-09",
    }
    d.update(extra)
    return d


# ------------------------------------------------------------------ build_alt_matrix_signal_email / notification_for_alt_matrix_plan

def test_armed_signal_email_shape():
    subject, body = amn.build_alt_matrix_signal_email(_plan("ARMED"))
    assert "Silver Cross Confirmed" in subject
    assert "SOLUSDT" in subject
    assert "101.50" in body and "100.00" in body
    assert "Macro trend and funding both passed" in body
    assert "Ref: #7" in body


def test_skipped_macro_email_shape():
    subject, body = amn.build_alt_matrix_signal_email(_plan("SKIPPED_MACRO"))
    assert "Cross Filtered" in subject
    assert "macro trend down" in body
    assert "No trade" in body


def test_skipped_funding_email_shape():
    subject, body = amn.build_alt_matrix_signal_email(_plan("SKIPPED_FUNDING"))
    assert "funding rate too high" in body


def test_signal_email_handles_missing_levels_without_crashing():
    plan = _plan("ARMED", ema21=None, ema55=None, atr14=None)
    subject, body = amn.build_alt_matrix_signal_email(plan)
    assert "?" in body


def test_notification_dispatcher_fires_for_armed_and_skipped_not_others():
    assert amn.notification_for_alt_matrix_plan(_plan("ARMED")) is not None
    assert amn.notification_for_alt_matrix_plan(_plan("SKIPPED_MACRO")) is not None
    assert amn.notification_for_alt_matrix_plan(_plan("SKIPPED_FUNDING")) is not None
    assert amn.notification_for_alt_matrix_plan(_plan("DONE")) is None
    assert amn.notification_for_alt_matrix_plan(_plan("CONCURRENCY_SKIPPED")) is None


# ------------------------------------------------------------------ build_alt_matrix_entry_email

def test_entry_email_shape_with_account_label():
    order = _order(account_label="andy_bitunix_main")
    subject, body = amn.build_alt_matrix_entry_email(order)
    assert "Position Opened" in subject
    assert "andy_bitunix_main" in subject and "andy_bitunix_main" in body
    assert "101.00" in body
    assert "98.00" in body
    assert "150.00" in body
    assert "plan #7, order #42" in body


def test_entry_email_falls_back_to_account_id_label_when_no_label_given():
    order = _order(account_label=None)
    subject, body = amn.build_alt_matrix_entry_email(order)
    assert "account #3" in subject


# ------------------------------------------------------------------ build_alt_matrix_breakeven_email -- the one event with no Traveler analog

def test_breakeven_email_shape():
    order = _order(be_price=101.2, account_label="andy_bitunix_main")
    subject, body = amn.build_alt_matrix_breakeven_email(order)
    assert "Stop Moved to Breakeven" in subject
    assert "+2R" in body
    assert "101.20" in body
    assert "EMA" in body


# ------------------------------------------------------------------ build_alt_matrix_exit_email

def test_exit_email_dry_run_shape():
    order = _order(exit_reason="EMA21_TRAIL", exit_price=115.0, realized_pnl_r=1.8, account_label="andy_bitunix_main")
    subject, body = amn.build_alt_matrix_exit_email(order, is_live=False)
    assert "EMA21 trail exit" in subject
    assert "115.00" in subject
    assert "+1.8000R" in body
    assert "Simulated close" in body
    assert "approximated" not in body.lower() or "Note:" not in body


def test_exit_email_live_shape_no_approximation_note_by_default():
    order = _order(exit_reason="STOP", exit_price=95.0, realized_pnl_r=-0.5)
    subject, body = amn.build_alt_matrix_exit_email(order, is_live=True)
    assert "stop hit" in subject
    assert "Real order -- live money." in body
    assert "Note:" not in body


def test_exit_email_live_with_approximation_note():
    order = _order(exit_reason="EMA55_CLOSE", exit_price=99.0, realized_pnl_r=0.1, approximated=True)
    subject, body = amn.build_alt_matrix_exit_email(order, is_live=True)
    assert "Note:" in body
    assert "last known live price" in body


def test_exit_email_unknown_reason_falls_back_to_the_raw_string():
    order = _order(exit_reason="SOME_FUTURE_REASON", exit_price=100.0, realized_pnl_r=0.0)
    subject, body = amn.build_alt_matrix_exit_email(order, is_live=False)
    assert "SOME_FUTURE_REASON" in subject
