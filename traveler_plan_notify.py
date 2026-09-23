# traveler_plan_notify.py
# ==============================================================================
# GATE_TRAVELER EMAIL NOTIFICATIONS -- Ruling C (DeepSeek, relayed by Andy
# 2026-09-15). Before this, GATE_TRAVELER's own D1/D2 state machine
# (traveler_plan_engine.py) had no email hook at all -- trade_plan_notify.py
# only ever fires for TradePlan (v1/v2) rows. Flagged as a gap in the
# Ruling B report; now built, following the EXACT same pattern as that
# module (same transport, notify.send_admin_email(), never-raises; same
# non-blocking own-try/except call-site convention). Every subject/body is
# explicitly tagged TRAVELER so Andy can tell at a glance which lineage
# produced it -- these are DRY_RUN evaluation trades, not real orders, and
# must never be mistaken for a v2 signal.
#
# Deadline reasoning (Ruling C, explicit): profiles are now settable
# (Ruling A) and both lineages simulate management to a real close in
# DRY_RUN (Ruling B), so a real GATE_TRAVELER trade could begin on the
# very next session lock once an account's profile is set to
# GATE_TRAVELER -- this had to ship before that could happen, not after.
#
# THREE events, same count/shape as trade_plan_notify.py:
#   1. LOCK -- fires once per TravelerPlan row, right after
#      kabroda_mas_flow.py's _inject_traveler_plan_to_database() commits
#      it. GATE_TRAVELER has NO lock-time gate at all (gate_traveler.py's
#      own module header: "GATE_TRAVELER has no lock-time gate to
#      evaluate") -- every plan starts WAITING_CROSS unconditionally, so
#      unlike v2's four-disposition-code lock email, this is always the
#      same shape: levels + "watching for a cross either way."
#   2. ARMED -- fires on the WAITING_TOUCH -> FILLED transition (the
#      trigger touch fill, gate_traveler.py's own advance_waiting_touch())
#      -- "this is a simulation-only fill," matching v2's own ARMED-is-the-
#      fill-instant framing (no separate FILLED email here either, same
#      anti-duplicate reasoning v2 uses).
#   3. DONE -- fires on WAITING_CROSS -> TERCILE_SKIPPED (a real cross
#      happened, RSI zone excluded it) AND on WAITING_TOUCH -> DONE
#      (opposite trigger broke first, or the 7-day journey cap passed with
#      no fill). One line each, using the real last_transition_reason
#      gate_traveler.py already writes -- never a fabricated reason.
#
# Interpretive note, flagged in AGENT_LOG.md: the task description named
# exactly "LOCK/ARMED/DONE" as the three events. TERCILE_SKIPPED is a
# DIFFERENT literal DB status than "DONE", so folding it into the DONE-
# family notification here is a judgment call, not a literal reading --
# made because leaving a real, confirmed cross that got excluded
# completely silent would reproduce the exact "DAY-4 EMAIL FAILURE"
# anti-pattern (Kabroda AI Brain repo, 2026-09-02) that made v2's own LOCK
# email always-fire in the first place, and matches v2's own precedent of
# emailing a real-but-declined cross (build_done_email()'s vetoed_cross_
# side/opposite_side handling) rather than staying silent. If TERCILE_
# SKIPPED should instead stay silent, that's a one-line removal in
# notification_for_traveler_transition() below.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _symbol_compact(symbol: str) -> str:
    return (symbol or "").replace("/", "")


def _fmt(value: Optional[float], spec: str = ",.2f") -> str:
    return format(value, spec) if value is not None else "?"


def build_traveler_lock_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once per TravelerPlan row, unconditionally -- GATE_TRAVELER
    has no lock-time gate/disposition to evaluate (see this module's own
    header), so there is only one shape, not v2's four disposition codes."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
    r30_high, r30_low = plan.get("r30_high"), plan.get("r30_low")
    rsi = plan.get("rsi_4h_at_lock")
    subject = f"KABRODA TRAVELER LOCK - {symbol} - watching for a cross"
    body = (
        "TRAVELER (evaluation lineage, DRY_RUN only -- not a live order signal)\n\n"
        f"Session levels locked for {symbol}:\n"
        f"  Breakout trigger:   {_fmt(bo)}\n"
        f"  Breakdown trigger:  {_fmt(bd)}\n"
        f"  30M range:          {_fmt(r30_low)} - {_fmt(r30_high)}\n"
        f"  RSI-4h at lock:     {_fmt(rsi, ',.1f')}\n\n"
        "No pre-committed direction -- watching for a confirmed close beyond "
        "either trigger. A cross that gets excluded (RSI tercile) or "
        "invalidated sends a short stand-down email; a taken cross sends an "
        "ARMED email at the trigger touch fill.\n\n"
        f"  Plan ID: {plan.get('id')}"
    )
    return subject, body


def build_traveler_armed_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires on the trigger touch fill (WAITING_TOUCH -> FILLED) -- the
    same instant v2's own ARMED fires on its own resting-order fill.
    Simulation-only: no real order was placed."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    fill_price = plan.get("fill_price")
    stop = plan.get("stop_price")
    t1 = plan.get("t1_price")
    subject = f"KABRODA TRAVELER ARMED - {symbol} {direction} @ {_fmt(fill_price, ',.0f')}"
    body = (
        "TRAVELER (evaluation lineage, DRY_RUN only -- no real order was placed)\n\n"
        f"{symbol} {direction} trigger touch fill confirmed at {_fmt(fill_price)}.\n"
        f"  Stop: {_fmt(stop)}\n"
        f"  T1:   {_fmt(t1)}\n\n"
        "This is a simulation fill for the evaluation harness -- no action needed.\n\n"
        f"  Plan ID: {plan.get('id')}"
    )
    return subject, body


def build_traveler_done_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Covers BOTH real terminal 'no trade' outcomes -- TERCILE_SKIPPED
    (a real cross, excluded) and DONE (opposite trigger broke first, or
    the 7-day journey cap passed with no fill) -- one line, using the
    real last_transition_reason gate_traveler.py already writes."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    reason = plan.get("last_transition_reason") or "no trade"
    subject = f"KABRODA TRAVELER DONE - {symbol} - stand down"
    body = (
        "TRAVELER (evaluation lineage, DRY_RUN only)\n\n"
        f"{reason}.\n\n"
        f"  Plan ID: {plan.get('id')}"
    )
    return subject, body


# 2026-09-23 -- the ONE post-fill D3 event this module covers. MGMT_E1_STACK
# (mgmt_e1_stack.py::advance()) is a single full-exit design -- no partial
# T1 leg, no runner -- so there is exactly one terminal management event per
# journey, never a sequence; this is not a feed of several events, just the
# one closure. Andy's own ask, radar-rebuild-around-Traveler-communication
# work: the same closure information the radar panel now also shows.
_EXIT_REASON_LABELS = {
    "STOP": "stop hit",
    "C5_EXIT": "momentum-decay exhaustion (C5) exit",
    "BBWP_EXIT": "BBWP volatility-burnout exit",
    "T1": "target hit (T1)",
    "TIME": "journey time-cap exit",
}


def build_traveler_management_event_email(order: Dict[str, Any], is_live: bool) -> Tuple[str, str]:
    """`order` is a plain dict of the closing ExecutorOrder's own fields --
    symbol, direction, exit_reason, exit_price, realized_pnl_r, and
    traveler_plan_id (for the footer, matching this module's other builders'
    "Plan ID" convention -- the TravelerPlan's id, not the ExecutorOrder's
    own). `is_live` distinguishes real money from the DRY_RUN evaluation
    harness, same caveat convention this file's other builders already use.
    `order.get("approximated")` (bool) adds the LIVE-only caveat for a
    market-close contingency exit (C5/BBWP/TIME) whose price isn't an
    independently confirmed exchange fill -- STOP and T1 are real fills on
    both lineages and never carry this caveat. The caller computes
    `approximated` (exit_reason in {"C5_EXIT","BBWP_EXIT","TIME"} AND
    is_live) rather than this function re-deriving it, so there is exactly
    one place in the codebase that maps exit reasons to the approximated
    flag -- see executor_live_e1_engine.py::_finalize_traveler_close()'s own
    `approximated` parameter, the authoritative source this mirrors."""
    symbol = _symbol_compact(order.get("symbol", ""))
    direction = order.get("direction") or "?"
    exit_reason = order.get("exit_reason") or "?"
    reason_label = _EXIT_REASON_LABELS.get(exit_reason, exit_reason)
    exit_price = order.get("exit_price")
    r = order.get("realized_pnl_r")

    subject = f"KABRODA TRAVELER CLOSED - {symbol} {direction} - {reason_label} @ {_fmt(exit_price, ',.0f')}"

    lineage_line = (
        "TRAVELER (real order, live money)" if is_live else
        "TRAVELER (evaluation lineage, DRY_RUN only -- bookkeeping close, no real order)"
    )

    approx_note = ""
    if is_live and order.get("approximated"):
        approx_note = (
            "\n\nNote: this exit price is approximated at the last known live price at close "
            "time, not an independently confirmed exchange fill -- market-close contingency "
            "exits (C5/BBWP/TIME) can't get a guaranteed fill price the way a resting-limit "
            "T1 or the exchange's own stop trigger can."
        )

    body = (
        f"{lineage_line}\n\n"
        f"{symbol} {direction} closed: {reason_label}.\n"
        f"  Exit price: {_fmt(exit_price)}\n"
        f"  Realized:   {_fmt(r, '+.4f')}R{approx_note}\n\n"
        f"  Plan ID: {order.get('traveler_plan_id')}"
    )
    return subject, body


def notification_for_traveler_transition(prev_status: str, plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Given the status BEFORE this poll's update and the plan dict AFTER
    it, decide which (if any) email fires -- called once per real
    transition (the caller already gates on prev_status != new status).
    WAITING_CROSS -> WAITING_TOUCH is a real, logged transition but not
    one of the three required events -- returns None, same as v2's own
    non-emailed intermediate transitions."""
    status = plan.get("status")
    if status == "FILLED":
        return build_traveler_armed_email(plan)
    if status in ("DONE", "TERCILE_SKIPPED"):
        return build_traveler_done_email(plan)
    return None
