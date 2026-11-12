# cme_notify.py
# ==============================================================================
# CME EMAIL NOTIFICATIONS -- mirrors alt_matrix_notify.py's own proven shape
# exactly (same transport, notify.send_admin_email()/send_account_email(),
# never raises; same non-blocking own-try/except call-site convention). Same
# L4 routing rule applies: plan-level events (the session lock, the
# reachability verdict) go through send_admin_email() (the merged radar
# list); anything describing ONE account's own fill/ratchet/exit/error goes
# through send_account_email() (that account's real owner only).
#
# Two classes of event:
#   PLAN-LEVEL (radar class): the D1 lock outcome -- ARMED (reachable) or
#     STAND_DOWN_UNREACHABLE. CME has its own lock-time phase to report
#     (unlike Alt Matrix, which has none) -- this is closer to traveler_
#     plan_notify.py's own build_traveler_lock_email() in spirit.
#   PER-ORDER (trade-execution class): entry fill, the breakeven-ratchet
#     amendment, the terminal exit (STOP/BE_STOP/TARGET/TIME_FLUSH/MANUAL/
#     ERROR), a trailing-drawdown-buffer warning, and the emergency-flatten
#     confirmation -- the last two have no Traveler/Alt-Matrix analog at
#     all (new copy, not adapted from an existing builder).
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _fmt(value: Optional[float], spec: str = ",.2f") -> str:
    return format(value, spec) if value is not None else "?"


def _date_tag(date_key: Optional[str]) -> str:
    return f"[{date_key}] " if date_key else ""


# ------------------------------------------------------------------ PLAN-LEVEL (radar class)

def build_cme_lock_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once per CMEPlan row, right after the 10:00 ET lock -- ARMED
    (reachable, BO/BD set) or STAND_DOWN_UNREACHABLE (box too wide relative
    to ATR14, standing down for the day)."""
    symbol = plan.get("symbol", "")
    status = plan.get("status")
    box, reach = plan.get("box"), plan.get("reachability")

    if status == "ARMED":
        subject = f"KABRODA - {_date_tag(plan.get('date_key'))}{symbol} - Session Locked, ARMED (CME)"
        headline = f"{symbol} 10:00 ET lock: box {_fmt(box)} pts, reachability {_fmt(reach, '.3f')} -- REACHABLE."
        detail = f"BO {_fmt(plan.get('bo'))} / BD {_fmt(plan.get('bd'))}. Watching for a confirmed 5m close beyond either trigger.\n\n"
    else:
        subject = f"KABRODA - {_date_tag(plan.get('date_key'))}{symbol} - Session STAND DOWN (CME)"
        headline = f"{symbol} 10:00 ET lock: box {_fmt(box)} pts, reachability {_fmt(reach, '.3f')} -- UNREACHABLE."
        detail = "No trade today: box too wide relative to ATR14.\n\n"

    body = f"{headline}\n\n{detail}  Ref: #{plan.get('id')}"
    return subject, body


def notification_for_cme_plan(plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Dispatcher -- called once, right after a new CMEPlan row is created."""
    if plan.get("status") in ("ARMED", "STAND_DOWN_UNREACHABLE"):
        return build_cme_lock_email(plan)
    return None


# ------------------------------------------------------------------ PER-ORDER (trade-execution class)

def build_cme_entry_email(order: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once the resting bracket fills -- the atomic OSO already
    attached the stop/target server-side, so this is the one email per
    order that confirms a real position now exists."""
    symbol, direction = order.get("symbol", ""), order.get("direction") or "?"
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} {direction} - Entry Filled (CME, {account_label})"
    body = (
        f"{symbol} {direction} filled at {_fmt(order.get('entry_fill_price'))} "
        f"({order.get('contracts')} contracts, stop {_fmt(order.get('sl_price_initial'))}).\n\n"
        f"Risk: ${_fmt(order.get('risk_dollars_used'))}\n\n"
        f"Account: {account_label}\n\n"
        f"Ref: #{order.get('id')}"
    )
    return subject, body


def build_cme_breakeven_email(order: Dict[str, Any]) -> Tuple[str, str]:
    """The ONE management event with no Traveler analog anywhere in this
    codebase (MGMT_E1_STACK's stop never moves, by design -- see CLAUDE.md
    'What Must Never Be Changed'). Mirrors Alt Matrix's own build_alt_
    matrix_breakeven_email()'s role for this subsystem instead."""
    symbol, direction = order.get("symbol", ""), order.get("direction") or "?"
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} {direction} - Stop Moved to Breakeven (CME, {account_label})"
    body = (
        f"{symbol} {direction} stop amended to breakeven at {_fmt(order.get('be_price'))}. "
        f"Risk on this trade is now eliminated.\n\n"
        f"Account: {account_label}\n\nRef: #{order.get('id')}"
    )
    return subject, body


_EXIT_LABELS = {
    "STOP": "Full Stop", "BE_STOP": "Breakeven Stop", "TARGET": "Target Hit",
    "TIME_FLUSH": "16:00 ET Session Flush", "MANUAL": "Manually Closed", "ERROR": "Closed (Error)",
    "NEWS_BLACKOUT": "Tier-1 News Blackout Flatten",
}


def build_cme_exit_email(order: Dict[str, Any]) -> Tuple[str, str]:
    symbol, direction = order.get("symbol", ""), order.get("direction") or "?"
    reason = order.get("exit_reason") or "?"
    label = _EXIT_LABELS.get(reason, reason)
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - {_date_tag(order.get('date_key'))}{symbol} {direction} - {label} (CME, {account_label})"
    body = (
        f"{symbol} {direction} closed: {label} at ~{_fmt(order.get('exit_price'))} "
        f"(approximate -- a market-close fill carries no reliable exact price).\n\n"
        f"Dollar P&L: ${_fmt(order.get('dollar_pnl'))}\n\n"
        f"Account: {account_label}\n\nReceipt: {order.get('receipt_id') or '?'}\n\nRef: #{order.get('id')}"
    )
    return subject, body


def build_cme_trailing_dd_warning_email(order: Dict[str, Any], buffer_remaining_usd: float, limit_usd: float) -> Tuple[str, str]:
    """No Traveler/Alt-Matrix analog -- the Apex evaluation's own trailing-
    drawdown rule is the one genuinely new risk concept this subsystem
    needed (see cme_risk.py's own header). Fires once the remaining buffer
    drops to or below half the configured limit."""
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    subject = f"KABRODA - CME Trailing Drawdown Warning ({account_label})"
    body = (
        f"Trailing drawdown buffer is down to ${_fmt(buffer_remaining_usd)} "
        f"of a ${_fmt(limit_usd)} limit.\n\n"
        f"Account: {account_label}\n\nCheck the Mission Control dashboard for current equity."
    )
    return subject, body


def build_cme_emergency_flatten_email(account_label: str, cancelled: list, closed: list, errors: list) -> Tuple[str, str]:
    """No Traveler/Alt-Matrix analog -- emergency_flatten() is new
    functionality (see cme_executor.py's own header)."""
    subject = f"KABRODA - CME Fleet Emergency Flatten Executed ({account_label})"
    status = "with errors -- CHECK TRADOVATE DIRECTLY" if errors else "cleanly"
    body = (
        f"Emergency flatten executed {status}.\n\n"
        f"Orders cancelled: {cancelled}\nPositions closed: {closed}\nErrors: {errors}\n\n"
        f"Account: {account_label}"
    )
    return subject, body
