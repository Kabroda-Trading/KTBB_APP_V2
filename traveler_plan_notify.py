# traveler_plan_notify.py
# ==============================================================================
# GATE_TRAVELER EMAIL NOTIFICATIONS -- Ruling C (DeepSeek, relayed by Andy
# 2026-09-15). Before this, GATE_TRAVELER's own D1/D2 state machine
# (traveler_plan_engine.py) had no email hook at all -- trade_plan_notify.py
# (deleted 2026-09-24, V2 Crown retirement) only ever fired for TradePlan
# (v1/v2) rows. Built following that module's same pattern (same transport,
# notify.send_admin_email(), never-raises; same non-blocking own-try/except
# call-site convention).
#
# 2026-09-26 wording rewrite (Andy's own request, real production email --
# see AGENT_LOG.md this date): every builder here used to unconditionally
# claim "TRAVELER (evaluation lineage, DRY_RUN only)" in the body -- true
# when this module was first built (no live GATE_TRAVELER account existed
# yet), false as of today (Andy_Bitunix and dawson_bitu are both LIVE on
# GATE_TRAVELER/MGMT_E1_STACK). LOCK/ARMED/DONE describe the shared PLAN-
# level state machine (one TravelerPlan can have both a DRY_RUN eval order
# and one or more real LIVE orders linked to it), so a lineage-wide claim
# was never really correct there even before today -- it happened to read
# true only because no LIVE account existed yet to make it false. Rewritten
# to plain, self-contained trader language with no internal jargon
# ("tercile-skipped", "RSI-4h-at-cross", "TRAVELER", "Plan ID") and no
# DRY_RUN/lineage claim on these three. The one place a real/simulated
# distinction IS true and worth keeping -- CLOSED, which is per-order, not
# per-plan -- keeps it, reworded plainly ("Real order -- live money." /
# "Simulated close -- no real order was placed.") using the same is_live
# flag as before; that distinction was never the false part.
#
# THREE plan-level events, same count/shape as the deleted trade_plan_
# notify.py:
#   1. LOCK -- fires once per TravelerPlan row, right after
#      kabroda_mas_flow.py's _inject_traveler_plan_to_database() commits
#      it. GATE_TRAVELER has NO lock-time gate at all (gate_traveler.py's
#      own module header: "GATE_TRAVELER has no lock-time gate to
#      evaluate") -- every plan starts WAITING_CROSS unconditionally, so
#      unlike v2's four-disposition-code lock email, this is always the
#      same shape: levels + "watching for a cross either way."
#   2. ARMED -- fires on the WAITING_TOUCH -> FILLED transition (the
#      trigger touch fill, gate_traveler.py's own advance_waiting_touch())
#      -- described as a plain "position opened," since this event now
#      really can mean a real fill for a LIVE account, not only a
#      simulation (no separate FILLED email here either, same anti-
#      duplicate reasoning v2 used).
#   3. DONE -- fires on WAITING_CROSS -> TERCILE_SKIPPED (a real cross
#      happened, RSI zone excluded it) AND on WAITING_TOUCH -> DONE
#      (opposite trigger broke first, or the 7-day journey cap passed with
#      no fill). Plain-language headline derived from the plan's own
#      structured fields (status/direction/cross_price), not by parsing
#      gate_traveler.py's internal last_transition_reason string -- that
#      string stays exactly as-is in the DB for audit/display elsewhere
#      (e.g. the radar panel, AGENT_LOG citations), this module just
#      doesn't put it verbatim in the trader-facing email body anymore.
#
# Interpretive note, flagged in AGENT_LOG.md (2026-09-15, predates today's
# wording rewrite): the task description named exactly "LOCK/ARMED/DONE"
# as the three events. TERCILE_SKIPPED is a DIFFERENT literal DB status
# than "DONE", so folding it into the DONE-family notification here is a
# judgment call, not a literal reading -- made because leaving a real,
# confirmed cross that got excluded completely silent would reproduce the
# exact "DAY-4 EMAIL FAILURE" anti-pattern (Kabroda AI Brain repo,
# 2026-09-02) that made v2's own LOCK email always-fire in the first
# place. If TERCILE_SKIPPED should instead stay silent, that's a one-line
# removal in notification_for_traveler_transition() below.
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
    subject = f"KABRODA - {symbol} - Levels Locked"
    body = (
        f"Session levels locked for {symbol}.\n\n"
        f"  Breakout level:   {_fmt(bo)}\n"
        f"  Breakdown level:  {_fmt(bd)}\n"
        f"  30-minute range:  {_fmt(r30_low)} - {_fmt(r30_high)}\n"
        f"  RSI (4H):         {_fmt(rsi, ',.1f')}\n\n"
        "Watching for a confirmed close beyond either level. No trade yet.\n\n"
        f"  Ref: #{plan.get('id')}"
    )
    return subject, body


def build_traveler_armed_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires on the trigger touch fill (WAITING_TOUCH -> FILLED) -- the
    same instant v2's own ARMED fired on its own resting-order fill.
    Plan-level event (see module header) -- describes what happened, not
    which specific account(s) it applies to; a real per-account fill/close
    is what build_traveler_management_event_email() below reports."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    fill_price = plan.get("fill_price")
    stop = plan.get("stop_price")
    t1 = plan.get("t1_price")
    subject = f"KABRODA - {symbol} {direction} - Position Opened @ {_fmt(fill_price, ',.0f')}"
    body = (
        f"{symbol} {direction} opened at {_fmt(fill_price)}.\n\n"
        f"  Stop:   {_fmt(stop)}\n"
        f"  Target: {_fmt(t1)}\n\n"
        f"  Ref: #{plan.get('id')}"
    )
    return subject, body


def build_traveler_real_fill_email(order: Dict[str, Any]) -> Tuple[str, str]:
    """2026-09-27 (Andy ruling 14:55 CT, items 5/6): fires once a LIVE
    account's real exchange fill is confirmed -- executor_live_e1_engine.py
    ::check_traveler_entry_fill_and_protect(), only reached after a real
    get_order_detail() call returns status=="FILLED" (never from
    gate_traveler.py's own candle-only touch simulation, which the plan-
    level ARMED email above describes). Andy's own words: "I should be
    able to take that email and it should give me enough information...
    to go do the trade on my own in the exchange" -- carries direction,
    the real fill price, stop, T1, risk dollars, and which account this
    is, since ARMED (plan-level, shared across every account tracking the
    plan) never carried the last two and can't honestly claim a specific
    account's fill at all."""
    symbol = _symbol_compact(order.get("symbol", ""))
    direction = order.get("direction") or "?"
    entry = order.get("entry_fill_price")
    stop = order.get("stop_price")
    t1 = order.get("t1_price")
    risk = order.get("risk_dollars_used")
    account_label = order.get("account_label") or f"account #{order.get('account_id')}"
    leg_tag = " (re-arm)" if order.get("is_rearm") else ""
    subject = f"KABRODA - {symbol} {direction} - Real Fill Confirmed{leg_tag} @ {_fmt(entry, ',.0f')} ({account_label})"
    body = (
        f"{symbol} {direction} filled for real on {account_label}, confirmed by the exchange, at {_fmt(entry)}.\n\n"
        f"  Stop:   {_fmt(stop)}\n"
        f"  Target: {_fmt(t1)}\n"
        f"  Risk:   ${_fmt(risk, ',.2f')}\n\n"
        f"  Ref: #{order.get('traveler_plan_id')}"
    )
    return subject, body


def build_traveler_done_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Covers BOTH real terminal 'no trade' outcomes -- TERCILE_SKIPPED
    (a real cross, excluded) and DONE (opposite trigger broke first, or
    the 7-day journey cap passed with no fill). TERCILE_SKIPPED gets a
    specific plain-language headline (the common, important case); the
    other two DONE causes share a generic honest headline with the real
    last_transition_reason kept as a secondary detail line, rather than
    fragile string-matching on gate_traveler.py's own internal wording to
    tell them apart (that module's reasons were never meant to be parsed,
    only logged verbatim -- see its own header)."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    status = plan.get("status")
    direction = plan.get("direction") or "?"
    cross_price = plan.get("cross_price")
    subject = f"KABRODA - {symbol} - No Trade"

    if status == "TERCILE_SKIPPED":
        rsi = plan.get("rsi_4h_at_cross")
        headline = (
            f"{direction} cross confirmed at {_fmt(cross_price)} -- outside "
            "system guidelines, no trade taken."
        )
        detail = f"  RSI (4H) at cross: {_fmt(rsi, ',.1f')}\n\n"
    else:
        headline = "No trade taken this session."
        reason = plan.get("last_transition_reason") or ""
        detail = f"  Detail: {reason}\n\n" if reason else ""

    body = f"{headline}\n\n{detail}  Ref: #{plan.get('id')}"
    return subject, body


# 2026-09-23 -- the ONE post-fill D3 event this module covers. MGMT_E1_STACK
# (mgmt_e1_stack.py::advance()) is a single full-exit design -- no partial
# T1 leg, no runner -- so there is exactly one terminal management event per
# journey, never a sequence; this is not a feed of several events, just the
# one closure.
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
    "Ref" convention -- the TravelerPlan's id, not the ExecutorOrder's own).

    Unlike LOCK/ARMED/DONE above, `is_live` here is a TRUE and useful
    distinction, not a stale blanket claim -- this event is per-order (one
    specific account's real or simulated close), not per-plan, so it can
    honestly say which one this was. `order.get("approximated")` (bool)
    adds the LIVE-only caveat for a market-close contingency exit (C5/
    BBWP/TIME) whose price isn't an independently confirmed exchange fill
    -- STOP and T1 are real fills on both lineages and never carry this
    caveat. The caller computes `approximated` (exit_reason in
    {"C5_EXIT","BBWP_EXIT","TIME"} AND is_live) rather than this function
    re-deriving it, so there is exactly one place in the codebase that maps
    exit reasons to the approximated flag -- see executor_live_e1_engine.py
    ::_finalize_traveler_close()'s own `approximated` parameter, the
    authoritative source this mirrors."""
    symbol = _symbol_compact(order.get("symbol", ""))
    direction = order.get("direction") or "?"
    exit_reason = order.get("exit_reason") or "?"
    reason_label = _EXIT_REASON_LABELS.get(exit_reason, exit_reason)
    exit_price = order.get("exit_price")
    r = order.get("realized_pnl_r")

    # 2026-10-06 (R1 re-arm): a closure email reads identically otherwise
    # whether it's the primary or the re-arm leg -- the subject tag is the
    # one place a reader can tell which position this was, especially
    # since the primary's own close already happened earlier the same day.
    leg_tag = " (re-arm)" if order.get("is_rearm") else ""
    subject = f"KABRODA - {symbol} {direction} - Closed{leg_tag} ({reason_label}) @ {_fmt(exit_price, ',.0f')}"

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

    # 2026-09-27 (item 4): which account this closure applies to -- absent
    # before, and per-account identity is exactly what makes this email
    # (unlike LOCK/ARMED/DONE) able to carry it honestly at all.
    account_label = order.get("account_label") or (f"account #{order.get('account_id')}" if order.get("account_id") else None)
    account_line = f"  Account: {account_label}\n" if account_label else ""

    body = (
        f"{symbol} {direction} closed: {reason_label}.\n"
        f"{account_line}"
        f"  Exit price: {_fmt(exit_price)}\n"
        f"  Realized:   {_fmt(r, '+.4f')}R\n\n"
        f"{lineage_line}{approx_note}\n\n"
        f"  Ref: #{order.get('traveler_plan_id')}"
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


# ==============================================================================
# R1 RE-ARM (2026-10-06, Andy ruling 15:24 CT) -- the rearm_status
# counterpart to LOCK/ARMED/DONE above. Two new plan-level events (REARM_
# WATCH-entered, REARM done-without-a-trade), reusing build_traveler_real_
# fill_email()/build_traveler_management_event_email() UNCHANGED for the
# per-account fill/close events (both already take a plain order dict, not
# touching plan-level rearm_* fields at all -- genuinely re-arm-agnostic
# already, same L4 per-account routing applies unchanged).
# ==============================================================================

def build_traveler_rearm_watch_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires once, right when a primary journey's C5 exit starts the re-arm
    watch (mgmt_e1_stack.start_rearm_watch_if_eligible()). Plan-level,
    radar-class -- not account-specific (no fill/risk$ to report yet)."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    subject = f"KABRODA - {symbol} {direction} - Re-arm Watch"
    body = (
        f"{symbol} {direction} closed via momentum-decay exhaustion (C5). "
        f"Watching for exhaustion to clear and a re-cross of the same level "
        f"before the next lock -- one re-arm max today.\n\n"
        f"  Ref: #{plan.get('id')}"
    )
    return subject, body


def build_traveler_rearm_armed_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Fires on the re-arm's own trigger touch fill (REARM_WAITING_TOUCH ->
    REARM_FILLED) -- the rearm_status counterpart to build_traveler_armed_
    email() above, reading the rearm_* fields instead of the primary's own
    (already-closed) fill_price/stop_price -- stop/T1 are UNCHANGED from
    the primary (frozen levels), only the fill price/timing are new."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    direction = plan.get("direction") or "?"
    fill_price = plan.get("rearm_fill_price")
    stop = plan.get("stop_price")
    t1 = plan.get("t1_price")
    subject = f"KABRODA - {symbol} {direction} - Re-arm Position Opened @ {_fmt(fill_price, ',.0f')}"
    body = (
        f"{symbol} {direction} re-armed and opened at {_fmt(fill_price)}.\n\n"
        f"  Stop:   {_fmt(stop)}\n"
        f"  Target: {_fmt(t1)}\n\n"
        f"  Ref: #{plan.get('id')}"
    )
    return subject, body


def build_traveler_rearm_done_email(plan: Dict[str, Any]) -> Tuple[str, str]:
    """Covers BOTH real terminal "no re-arm trade" outcomes -- REARM_
    TERCILE_SKIPPED (a real re-cross, excluded) and REARM_WINDOW_CLOSED
    (no re-cross before the next lock, opposite trigger broke, or the
    90-bar touch cap passed) -- same one-headline-plus-detail-line shape
    as build_traveler_done_email() above, reading rearm_* fields."""
    symbol = _symbol_compact(plan.get("symbol", ""))
    rearm_status = plan.get("rearm_status")
    direction = plan.get("direction") or "?"
    rearm_cross_price = plan.get("rearm_cross_price")
    subject = f"KABRODA - {symbol} - No Re-arm Trade"

    if rearm_status == "REARM_TERCILE_SKIPPED":
        rsi = plan.get("rearm_rsi_4h_at_cross")
        headline = (
            f"{direction} re-cross confirmed at {_fmt(rearm_cross_price)} -- outside "
            "system guidelines, no re-arm trade taken."
        )
        detail = f"  RSI (4H) at re-cross: {_fmt(rsi, ',.1f')}\n\n"
    else:
        headline = "No re-arm trade taken this session."
        reason = plan.get("rearm_last_transition_reason") or ""
        detail = f"  Detail: {reason}\n\n" if reason else ""

    body = f"{headline}\n\n{detail}  Ref: #{plan.get('id')}"
    return subject, body


def notification_for_traveler_rearm_transition(prev_rearm_status, plan: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The rearm_status counterpart to notification_for_traveler_
    transition() above. REARM_WATCH fires on entry (prev_rearm_status is
    None -> REARM_WATCH, the only transition INTO this value); REARM_
    WAITING_TOUCH is a real, logged transition but not an emailed one (same
    "intermediate transition" treatment WAITING_CROSS -> WAITING_TOUCH
    already gets above)."""
    rearm_status = plan.get("rearm_status")
    if rearm_status == "REARM_WATCH":
        return build_traveler_rearm_watch_email(plan)
    if rearm_status == "REARM_FILLED":
        return build_traveler_rearm_armed_email(plan)
    if rearm_status in ("REARM_WINDOW_CLOSED", "REARM_TERCILE_SKIPPED"):
        return build_traveler_rearm_done_email(plan)
    return None
