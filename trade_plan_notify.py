# trade_plan_notify.py
# ==============================================================================
# TRADE PLAN EMAIL NOTIFICATIONS
# Andy's build request (Kabroda AI Brain repo AGENT_LOG.md, 2026-08-31,
# "trade-plan email notifications"): he should not have to watch the radar
# all day. Reuses notify.send_admin_email() -- the SAME proven SMTP path
# already exercised by the 4H/1H candidate open/close emails -- no new
# channel built.
#
# REQUIRED EVENTS, and only these (anti-spam is the point of the state
# machine, per the request):
#   1. LOCK  -- sent once, at plan generation, ONLY when a plan is
#      tradeable (status WAITING). NO_PLAN days get no email by default
#      (the request left this "optional, Andy can decide" -- default to
#      the less-spammy choice; easy to flip later).
#   2. ARMED -- the fuel check confirms at the cross: "place/confirm your
#      order." This is the one that matters. In THIS system's actual state
#      machine (trade_plan.py's advance_waiting_plan()/advance_reentry_
#      plan()), ARMED and FILLED are the SAME transition by construction
#      (a resting order AT the trigger level fills the instant price
#      touches it -- there's no meaningful gap at candle-poll granularity,
#      see that module's own docstring) -- so this fires on the real
#      WAITING/VETOED/REENTRY_ARMED -> FILLED transition. The spec's
#      separately-requested "FILLED" email (detect the resting order
#      touching on the retest) would fire on the exact same moment in this
#      system -- sending it too would be a DUPLICATE, not a second real
#      event, so it is deliberately not built (the request itself allowed
#      skipping FILLED detection rather than fabricating it).
#   3. VETOED -- cross happened, fuel failed: short "stand down" notice.
#   4. DONE -- one-line "no trade today" / "session complete", from
#      whatever prior status (no fill, a vetoed retest exhausted, a filled
#      trade's management concluded, a re-entry resolved).
#
# NOT emailed (not in the request): STOPPED, REENTRY_ARMED. Real, logged
# state transitions -- just not ones Andy asked to be paged for.
#
# Every email includes the plan's row ID, per the request, for SS9c's
# plan-ID reconciliation between kabroda.com and the Brain.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import trade_plan as tp


def _symbol_compact(symbol: str) -> str:
    """BTC/USDT -> BTCUSDT, matching the subject-line format in the
    request's own example ("KABRODA ARMED - BTCUSDT LONG @ 79062 - PREMIUM")."""
    return (symbol or "").replace("/", "")


def build_lock_email(plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Only for a tradeable plan (status WAITING) -- a NO_PLAN day gets no
    email by default (see module header)."""
    if plan.get("status") != "WAITING":
        return None
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    trigger = plan.get("trigger_price")
    subject = f"KABRODA PLAN - {symbol} {direction} @ {trigger:.0f}" if trigger else f"KABRODA PLAN - {symbol} {direction}"
    body = tp.render_brief(plan) + f"\n\n  Plan ID: {plan.get('id')}"
    return subject, body


def build_armed_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    trigger = plan.get("trigger_price")
    tier = plan.get("tier") or "STANDARD"
    reentry_note = " (RE-ENTRY)" if plan.get("reentry_used") else ""
    trig_str = f"{trigger:.0f}" if trigger else "?"
    subject = f"KABRODA ARMED{reentry_note} - {symbol} {direction} @ {trig_str} - {tier}"
    body = (
        tp.render_brief(plan)
        + f"\n\n  {'RE-ENTRY: this is the second-break entry, one attempt max.' if plan.get('reentry_used') else 'Place/confirm your order now.'}"
        + f"\n  Plan ID: {plan.get('id')}"
    )
    return subject, body


def build_vetoed_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    trigger = plan.get("trigger_price")
    trig_str = f"{trigger:.0f}" if trigger else "?"
    subject = f"KABRODA VETOED - {symbol} {direction} @ {trig_str} - stand down"
    reason = plan.get("last_transition_reason") or "cross unfueled"
    body = (
        f"Stand down -- {reason}.\n\n"
        f"Waiting for the retest, or DONE if the second cross is also unfueled.\n\n"
        f"  Plan ID: {plan.get('id')}"
    )
    return subject, body


def build_done_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction")
    label = f"{direction} traded" if direction else "no trade today"
    subject = f"KABRODA DONE - {symbol} - {label}"
    reason = plan.get("last_transition_reason") or "session complete"
    body = f"Session complete -- {reason}.\n\n  Plan ID: {plan.get('id')}"
    return subject, body


def notification_for_transition(prev_status: str, plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Given the status BEFORE this poll's update and the plan dict AFTER
    it, decide which (if any) email fires -- called once per real
    transition (the caller already gates on prev_status != new status).
    Returns None for any transition not in the required-events list
    (STOPPED, REENTRY_ARMED) -- one email per state transition max, per
    the request's own anti-spam constraint.
    """
    status = plan.get("status")
    if status == "FILLED":
        return build_armed_email(plan)
    if status == "VETOED":
        return build_vetoed_email(plan)
    if status == "DONE":
        return build_done_email(plan)
    return None
