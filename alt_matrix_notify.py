# alt_matrix_notify.py
# ==============================================================================
# ALT MATRIX EMAIL NOTIFICATIONS -- Step 5, mirroring traveler_plan_
# notify.py's own proven shape (same transport, notify.send_admin_email()/
# send_account_email(), never-raises; same non-blocking own-try/except
# call-site convention). Same L4 routing rule (Andy's ruling, Kabroda AI
# Brain CC_INTERFACE.md, "RULED BY ANDY 09-28 ~18:09 CT") applies here
# unchanged: plan-level, no-specific-person's-activity events go through
# send_admin_email() (the merged radar list); anything describing ONE
# account's own fill/open/close/error goes through send_account_email()
# (that account's real owner only, never broadcast).
#
# Two classes of event:
#   PLAN-LEVEL (radar class, send_admin_email): the D1 signal outcome --
#     ARMED (a real Silver Cross fired and passed every gate) or a
#     filtered cross (SKIPPED_MACRO/SKIPPED_FUNDING). Alt Matrix has no
#     lock-time phase to report separately (unlike Traveler's own LOCK
#     email) -- the signal IS the first event there is to report.
#   PER-ORDER (trade-execution class, send_account_email): entry fill,
#     the breakeven-ratchet amendment (genuinely NEW -- Traveler's own
#     MGMT_E1_STACK never moves its stop, so there is no analog anywhere
#     else in this codebase to copy for this one), and the terminal exit
#     (STOP/BE_STOP/EMA21_TRAIL/EMA55_CLOSE/TIME_EXPIRY/ERROR).
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _symbol_compact(symbol: str) -> str:
    return (symbol or "").replace("/", "")


def _fmt(value: Optional[float], spec: str = ",.2f") -> str:
    return format(value, spec) if value is not None else "?"


def _date_tag(date_key: Optional[str]) -> str:
    """Same convention as traveler_plan_notify.py's own _date_tag() --
    never fabricate a date if one isn't available."""
    return f"[{date_key}] " if date_key else ""


# ------------------------------------------------------------------ PLAN-LEVEL (radar class)

_PLAN_STATUS_LABELS = {
    "SKIPPED_MACRO": "filtered -- macro trend down",
    "SKIPPED_FUNDING": "filtered -- funding rate too high",
}


def build_alt_matrix_signal_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once per AltMatrixPlan row, right after it's created --
    ARMED (the cross passed every D1 gate) or a filtered cross
    (SKIPPED_MACRO/SKIPPED_FUNDING). Plan-level -- describes what the
    signal did, not any specific account's own order (that's the entry-
    fill email below, which may not exist at all if no account is
    configured yet)."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    status = plan.get("status")
    ema21, ema55 = plan.get("ema21"), plan.get("ema55")
    atr14 = plan.get("atr14")

    if status == "ARMED":
        subject = f"KABRODA - {_date_tag(plan.get('date_key'))}{symbol} - Silver Cross Confirmed (Alt Matrix)"
        headline = f"{symbol} 4H Silver Cross confirmed -- EMA21 {_fmt(ema21)} crossed above EMA55 {_fmt(ema55)}."
        detail = "Macro trend and funding both passed. Entering per account config.\n\n"
    else:
        label = _PLAN_STATUS_LABELS.get(status, status or "filtered")
        subject = f"KABRODA - {_date_tag(plan.get('date_key'))}{symbol} - Cross Filtered ({label}) (Alt Matrix)"
        headline = f"{symbol} 4H Silver Cross confirmed -- EMA21 {_fmt(ema21)} crossed above EMA55 {_fmt(ema55)}."
        detail = f"No trade: {label}.\n\n"

    body = (
        f"{headline}\n\n"
        f"{detail}"
        f"  ATR14: {_fmt(atr14)}\n\n"
        f"  Ref: #{plan.get('id')}"
    )
    return subject, body


def notification_for_alt_matrix_plan(plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Dispatcher mirroring traveler_plan_notify.py's own notification_
    for_traveler_transition() -- called once, right after a NEW
    AltMatrixPlan row is created (there is no status transition to watch
    for here the way TravelerPlan has one -- a plan row's status is set
    once at creation and only ever updated later to DONE/SKIPPED_IN_TRADE/
    CONCURRENCY_SKIPPED, none of which warrant a second plan-level email)."""
    status = plan.get("status")
    if status in ("ARMED", "SKIPPED_MACRO", "SKIPPED_FUNDING"):
        return build_alt_matrix_signal_email(plan)
    return None


# ------------------------------------------------------------------ PER-ORDER (trade-execution class)

def build_alt_matrix_entry_email(order: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once an entry is filled (real or simulated) -- mirrors
    traveler_plan_notify.py's own build_traveler_real_fill_email()/
    build_traveler_armed_email() combined into one shape, since Alt
    Matrix has no separate plan-level "position opened" event distinct
    from the per-account fill (there is no shared-across-accounts
    candle-touch simulation here the way GATE_TRAVELER's own ARMED is --
    every AltMatrixOrder's own fill is already account-specific)."""
    symbol = _symbol_compact(order.get("symbol", ""))
    entry = order.get("entry_fill_price")
    stop = order.get("sl_price_initial")
    risk = order.get("risk_dollars_used")
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} LONG - Alt Matrix Position Opened @ {_fmt(entry, ',.2f')} ({account_label})"
    body = (
        f"{symbol} LONG opened on {account_label} at {_fmt(entry)}.\n\n"
        f"  Stop: {_fmt(stop)}\n"
        f"  Risk: ${_fmt(risk, ',.2f')}\n\n"
        f"  Ref: plan #{order.get('alt_matrix_plan_id')}, order #{order.get('id')}"
    )
    return subject, body


def build_alt_matrix_breakeven_email(order: Dict[str, Any]) -> Tuple[str, str]:
    """The ONE management event with no Traveler analog anywhere in this
    codebase to copy -- MGMT_E1_STACK's own stop never moves (measured
    harmful for that lineage), so there has never been a prior
    "stop amended to breakeven" email to model this on. Fires once, when
    MFE first reaches +2R and the stop ratchets to entry+0.1R."""
    symbol = _symbol_compact(order.get("symbol", ""))
    be_price = order.get("be_price")
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} LONG - Stop Moved to Breakeven ({account_label})"
    body = (
        f"{symbol} LONG on {account_label} reached +2R -- stop moved to breakeven ({_fmt(be_price)}).\n"
        "Trailing against the 4H 21 EMA from here.\n\n"
        f"  Ref: plan #{order.get('alt_matrix_plan_id')}, order #{order.get('id')}"
    )
    return subject, body


_EXIT_REASON_LABELS = {
    "STOP": "stop hit",
    "BE_STOP": "breakeven stop hit",
    "EMA21_TRAIL": "4H EMA21 trail exit",
    "EMA55_CLOSE": "4H EMA55 close exit",
    "TIME_EXPIRY": "time-cap exit",
    "ERROR": "execution error",
    "MANUAL": "manual intervention or unexplained exchange-side closure",
}


def build_alt_matrix_exit_email(order: Dict[str, Any], is_live: bool) -> Tuple[str, str]:
    """Fires on any terminal close -- mirrors traveler_plan_notify.py's
    own build_traveler_management_event_email() shape exactly, same
    is_live real-vs-simulated distinction (a true, useful one here, not
    a stale blanket claim -- this is always per-order)."""
    symbol = _symbol_compact(order.get("symbol", ""))
    exit_reason = order.get("exit_reason") or "?"
    reason_label = _EXIT_REASON_LABELS.get(exit_reason, exit_reason)
    exit_price = order.get("exit_price")
    r = order.get("realized_pnl_r")
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"

    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} LONG - Alt Matrix Closed ({reason_label}) @ {_fmt(exit_price, ',.2f')} ({account_label})"

    lineage_line = (
        "Real order -- live money." if is_live else
        "Simulated close -- no real order was placed."
    )
    approx_note = ""
    if is_live and order.get("approximated"):
        approx_note = (
            "\n\nNote: this exit price is approximated at the last known live "
            "price at close time, not an independently confirmed exchange fill."
        )

    body = (
        f"{symbol} LONG on {account_label} closed: {reason_label}.\n"
        f"  Exit price: {_fmt(exit_price)}\n"
        f"  Realized:   {_fmt(r, '+.4f')}R\n\n"
        f"{lineage_line}{approx_note}\n\n"
        f"  Ref: plan #{order.get('alt_matrix_plan_id')}, order #{order.get('id')}"
    )
    return subject, body
