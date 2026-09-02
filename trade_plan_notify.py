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
#   1. LOCK -- sent once, at plan generation, for EVERY session regardless
#      of outcome (WAITING or NO_PLAN alike). Originally NO_PLAN mornings
#      sent nothing ("optional, Andy can decide" -- defaulted to the
#      less-spammy choice). That default caused a real, confirmed
#      production incident: zero emails on a NO_PLAN morning despite the
#      site correctly writing the gate-log row and radar verdict (Kabroda
#      AI Brain repo AGENT_LOG.md, 2026-09-02 11:00 CT, "DAY-4 EMAIL
#      FAILURE"). Andy's UX call, confirmed the same day at 12:00 CT: "one
#      lock-time email per morning (levels + the structure being watched +
#      the verdict-so-far), then silence until a transition" -- so LOCK now
#      always fires, framed as a morning briefing rather than a bare
#      NO_PLAN state dump.
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


def build_lock_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once per session lock, for every outcome (see module header --
    this used to be WAITING-only). `render_brief()` is the single source
    of truth for the body either way (SS4's own rule: never recompute a
    number the plan object already has); this function only decides the
    subject line and appends the anti-spam/plan-ID footer."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    if plan.get("status") == "WAITING":
        direction = plan.get("direction") or "?"
        trigger = plan.get("trigger_price")
        subject = f"KABRODA PLAN - {symbol} {direction} @ {trigger:.0f}" if trigger else f"KABRODA PLAN - {symbol} {direction}"
        body = tp.render_brief(plan) + f"\n\n  Plan ID: {plan.get('id')}"
        return subject, body

    # Two-tier disposition (STAND DOWN vs LIVE), per DeepSeek's corpus
    # validation (Kabroda AI Brain repo AGENT_LOG.md, 2026-09-02 13:00 CT):
    # individual gate-miss reasons don't separate paying from dead
    # stand-asides in the backtest (all flat, ~0R either way) -- the
    # disposition is only justified in aggregate, so this stays a plain
    # STAND DOWN, not a graded WATCH tier (that would need the separate,
    # not-yet-built NC-follow-up confirmation logic to be honest).
    #
    # IMPORTANT: do not promise a follow-up email here. trade_plan_engine.py's
    # poll routing does not include NO_PLAN (only WAITING/VETOED/REENTRY_
    # ARMED/FILLED/STOPPED) -- a NO_PLAN row is never re-checked, so there is
    # no transition to send. Saying otherwise would be exactly the kind of
    # confident-but-wrong claim this project's own discipline exists to
    # prevent (see the same AGENT_LOG.md entry, "NO_PLAN poll-routing gap").
    # If that gap ever gets closed, this copy needs to change with it.
    subject = f"KABRODA STAND DOWN - {symbol} - do not watch"
    body = (
        "Nothing to watch this morning -- do not wait by the computer. "
        "This is the full verdict for today; no further email will follow "
        "for this session.\n\n"
        + tp.render_brief(plan)
        + f"\n\n  Plan ID: {plan.get('id')}"
    )
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
    reason = plan.get("last_transition_reason") or "session complete"

    # 2026-09-01 P0 follow-up (Kabroda AI Brain AGENT_LOG.md, "CC's dual-
    # sided question answered from the corpus: track BOTH triggers"): a
    # break through the OPPOSITE (untradeable) trigger still resolves to
    # DONE (one plan per session, only the anticipated side is tradeable
    # -- the veto stays), but trade_plan_engine.py's opposite-break
    # enrichment attaches the REAL full-gate verdict as transient row
    # attributes (never persisted -- see _enrich_opposite_break_with_
    # full_gate()'s own docstring). When present, this is framed as the
    # VETOED-style call it actually is, not a bare "nothing happened"
    # DONE -- the whole point of this fix is Andy getting the message the
    # gate actually produced.
    opposite_side = plan.get("opposite_side")
    if opposite_side:
        opposite_trigger = plan.get("opposite_trigger")
        trig_str = f"{opposite_trigger:.0f}" if opposite_trigger else "?"
        subject = f"KABRODA VETOED - {symbol} {opposite_side} @ {trig_str} - counter-trend"
        body = (
            f"The opposite side crossed and the full gate ran on it -- {reason}.\n\n"
            f"This system only trades the anticipated side ({plan.get('direction')}); "
            f"the veto on {opposite_side} stands, same as it would have if this had "
            f"been the anticipated side from the start.\n\n"
            f"  Plan ID: {plan.get('id')}"
        )
        return subject, body

    direction = plan.get("direction")
    label = f"{direction} traded" if direction else "no trade today"
    subject = f"KABRODA DONE - {symbol} - {label}"
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
