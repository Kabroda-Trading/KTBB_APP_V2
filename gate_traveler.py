# gate_traveler.py
# ==============================================================================
# GATE_TRAVELER -- D1 (does the taken-gate open?) + D2 (trigger-touch entry) for
# the traveler-candidate lineage. Pure functions only, no DB/network (same
# small-single-purpose-module convention as trade_plan.py) -- the caller
# (traveler_plan_engine.py) owns all persistence.
#
# D2 RESTORED 2026-09-22 (CC_WORK_ORDER_D2_RESTORE_TRIGGER_LIMIT.md, Kabroda AI
# Brain repo, Andy ruling 07:49 CT): the 2026-09-15 build (see below) entered at
# the CLOSE of the first confirmed 5m bar back at/through the trigger after the
# cross -- a real, measured regression (re-verified `lab_confirming_close.py`:
# -0.1056R, negative every year) that drifted from the project's own founding
# audit (AUDIT.md section 2: "Trigger-fill (touch of BO/BD) -- KEEP -- the
# mid-box and deep pullback limit entries LOSE on both exchanges") and
# LIVE_SYSTEM_STATE.md Domain 2 ("Resting POST_ONLY LIMIT at the trigger price,
# placed after the confirmed cross"). The ORIGINAL, profitable D2 core -- and
# what this file now implements -- is: after the confirmed cross (D1, unchanged
# below), a resting limit sits AT THE TRIGGER; it fills on ANY SUBSEQUENT WICK
# TOUCH (high/low), no close-back condition. Measured basis: `lab_touchfill_
# arms.py`'s TF_CROSS arm / `d1_meas_base.py:44-61`'s wick-touch harness,
# +0.1160R taken-only / +0.0744R pooled, PASS 5/5, positive every year
# 2022-2026. See `advance_waiting_touch()` below.
#
# Built 2026-09-15 per CC_WORK_ORDER_PHASE2.md steps 2+3, against the
# frozen source (not the handoff's prose, which had 4 confirmed errors --
# see AGENT_LOG.md both repos, rulings fcfb19a/74344cd/7ee590d):
#   - entry/fill semantics: see the 2026-09-22 restore note above -- this
#     bullet originally cited `recipe_assembled.py::pullback_fill()`'s
#     confirmed-close basis, which is the regression that was reverted.
#   - tercile skip: `recipe_assembled.py::tercile_skip()` / `lab_walkforward_
#     execute.py::skipped()` -- LONG skips the lowest RSI-4h-at-lock
#     tercile, SHORT skips the highest. The walk-forward protocol re-fits
#     these cuts per fold from train data (correct for validating out-of-
#     sample); a live system needs ONE frozen pair. Ruled 2026-09-15
#     (AGENT_LOG.md, Kabroda AI Brain repo, commit fcfb19a): freeze the
#     full-corpus D1 cuts (FULL_D1 below) for production. DeepSeek's stated
#     plan is to add a dated CANON.md row for this -- NOT YET LANDED as of
#     this file's creation (checked directly). Kept here as ONE named
#     constant so swapping it for a real CANON-registry read is a one-line
#     change, not scattered literals -- flag this in any Stage B report as
#     still awaiting its CANON citation, not silently sourced.
#   - stop/box/t1: SAME formulas as v1/v2 (decision_engine.py's
#     STOP_BUFFER_BOX=0.12, T1_BOX=1.0) -- GATE_TRAVELER reuses the site's
#     existing level math, only the GATE and MANAGEMENT differ.
#   - journey cap: 7 days from the cross (journey_recipes.py:190,
#     JOURNEY_CAP = 7*24*3600).
#
# NOT part of GATE_TRAVELER's taken-gate: box/ATR reachability. The
# traveler study's population is EVERY sweep cross row (2,803 journeys),
# unfiltered by reachability -- that is a v1/v2-specific gate condition,
# never applied to this lineage anywhere in the cited sources.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

_LONG, _SHORT = "LONG", "SHORT"

STOP_BUFFER_BOX = 0.12   # decision_engine.py's own constant, same formula
T1_BOX = 1.0             # E1's full-exit target, decision_engine.py's own box multiple
JOURNEY_CAP_SECONDS = 7 * 24 * 3600   # journey_recipes.py:190

# R1 RE-ARM (2026-10-06, Andy ruling 15:24 CT, TRAVELER_D1_D2_D3_SPEC.md "R1
# RE-ARM AMENDMENT" -- Arm A, same-lock-day only, see advance_rearm_watch()/
# advance_rearm_waiting_touch() below). The re-arm's OWN waiting-for-touch
# window: 90 confirmed 5m bars (7.5h) from the re-cross -- "90-bar cap ->
# CLOSED_EXPIRED" per the spec -- computed as simple wall-clock arithmetic
# from the re-cross timestamp, the same style JOURNEY_CAP_SECONDS above
# already uses, NOT a literal bar-counting scan. Deliberately NOT the
# primary's own 7-day JOURNEY_CAP_SECONDS -- a materially tighter window
# for a materially different thing (see TRAVELER_D1_D2_D3_SPEC.md's own
# "re-anchored to re-fill... TIME = journey cap" wording: the FILLED
# re-arm trade's own D3 TIME exit still uses the ORIGINAL journey_cap_at,
# unchanged -- this constant only bounds the entry-order's own resting
# window before any fill happens).
REARM_TOUCH_CAP_SECONDS = 90 * 300

# Frozen production tercile cuts (RSI-4h-at-lock), per the 2026-09-15 ruling
# above -- (lo, hi) per side. LONG skips the LOWEST tercile (rsi < lo);
# SHORT skips the HIGHEST tercile (rsi > hi). Source: lab_d1 dated rows,
# the FULL_D1 fallback pair in lab_walkforward_execute.py/lab_touchfill_
# arms.py -- NOT YET a CANON.md row (see module header).
FULL_D1_CUTS = {"LONG": (51.49, 61.27), "SHORT": (40.45, 48.56)}

# D1 RSI-AT-CROSS (2026-09-21, Andy-approved, CC_WORK_ORDER_D1_RSI_AT_CROSS.md):
# below this many closed 4H bars, RSI is undefined -- the study's own
# convention (lab_walkforward_execute.py:95-97), never a fabricated 50.0.
# Same threshold battlebox_pipeline._calc_rsi() uses internally (period+1).
MIN_RSI_4H_BARS = 15


def rsi_at_cross(candles_4h: Optional[List[Dict[str, Any]]], cross_time_epoch: Optional[float]) -> Optional[float]:
    """RSI-4h at the CROSS moment, closed 4H bars only -- the actual DP0
    convention the traveler's tercile skip and F_A were measured on
    (journey_ledger.py:623-631 `closes_upto` + replay_site.py:236-258
    `wilder_rsi_site`; independently reproduced 2803/2803 to 2dp by two
    separate implementations, AGENT_LOG.md 2026-09-21/22). This is a
    DIFFERENT value from v2's `rsi_4h_at_lock` (frozen at the 13:00 lock,
    still-forming bar included) -- that field and its readers are untouched
    by this function.

    A 4H bar counts as closed at the cross when its OPEN time + 4h has
    already elapsed: `bar["time"] + 14400 <= cross_time_epoch`. This also
    naturally excludes a still-forming bar (its window hasn't elapsed
    either), so no separate strip is needed here.

    Classic Wilder RSI(14), SMA-seeded -- battlebox_pipeline._calc_rsi()'s
    own formula, duplicated (not imported) per this module's own no-DB/
    network convention and that function's own "other callers" note
    (verified byte-identical against it directly, tests/test_gate_
    traveler.py). Returns None below MIN_RSI_4H_BARS closes."""
    if not candles_4h or cross_time_epoch is None:
        return None
    closes = [
        float(c["close"]) for c in candles_4h
        if c.get("time") is not None and c["time"] + 14400 <= cross_time_epoch
    ]
    if len(closes) < MIN_RSI_4H_BARS:
        return None
    period = MIN_RSI_4H_BARS - 1
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(closes) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def tercile_skip(rsi_4h_at_cross: Optional[float], side: str,
                  cuts: Optional[Dict[str, Any]] = None) -> bool:
    """The HARD taken-gate filter (recipe_assembled.py::tercile_skip() /
    lab_walkforward_execute.py::skipped(), verbatim rule): LONG skips
    tercile-1 (rsi < lo), SHORT skips tercile-3 (rsi > hi). No RSI value
    available -> NOT skipped (counted, disclosed -- same convention the
    study itself uses, never silently drops a journey for missing data).
    2026-09-21: takes the cross-moment RSI (rsi_at_cross()), not the
    lock-time one -- see this module's header and CC_WORK_ORDER_D1_RSI_
    AT_CROSS.md for why the two differ."""
    if rsi_4h_at_cross is None:
        return False
    cuts = cuts or FULL_D1_CUTS
    lo, hi = cuts[side]
    if side == _LONG:
        return rsi_4h_at_cross < lo
    return rsi_4h_at_cross > hi


def _confirmed_side(candles_5m: List[Dict[str, Any]], bo: float, bd: float) -> Optional[str]:
    """Same side-determination decision_engine.py's evaluate_15m_decision()
    uses: LONG if the last CONFIRMED close is beyond bo, SHORT if beyond bd.
    Caller must have already stripped a still-forming trailing candle
    (market_data.confirmed_5m_closes())."""
    if not candles_5m:
        return None
    price = float(candles_5m[-1]["close"])
    if bo and price > bo:
        return _LONG
    if bd and price < bd:
        return _SHORT
    return None


_SESSION_EXPIRED_REASON = "session expired at next lock with no cross"


def advance_waiting_cross(
    plan: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    now_utc: datetime.datetime,
    candles_4h: Optional[List[Dict[str, Any]]] = None,
    session_expires_at: Optional[datetime.datetime] = None,
) -> Optional[Dict[str, Any]]:
    """WAITING_CROSS -> TERCILE_SKIPPED (terminal, no trade) | WAITING_TOUCH
    | DONE (terminal -- session expired with no valid cross).

    candles_5m: confirmed 5m closes (caller strips the still-forming
    trailing candle first, same as trade_plan.py's own callers).
    candles_4h: raw (unstripped) 4H candles covering well before the cross
    -- rsi_at_cross() does its own closed-bar filtering against the cross
    timestamp determined below, so this does NOT need market_data.
    confirmed_closes() applied first. Optional: if omitted or a fetch
    failed, rsi_4h_at_cross is None and the tercile skip treats that
    exactly like genuinely-insufficient history (not skipped) -- a
    transient fetch gap is not worth inventing a retry-the-cross state for.
    Returns None if no cross yet (stay WAITING_CROSS), or a dict of field
    updates once a cross is confirmed either way.

    session_expires_at (2026-10-07 P0, Andy directive 08:28 CT): this
    plan's own frozen 24h deadline (TravelerPlan.session_expires_at, see
    its own comment) -- before this parameter existed, a plan with no
    cross just sat in WAITING_CROSS forever, and a LATER day's real price
    action could "cross" its own stale, days-old frozen levels (the real
    2026-10-02 -> 2026-10-07 incident, traveler_plans.id=16). Checked at
    THREE points, mirroring advance_rearm_watch()'s own rearm_watch_
    deadline pattern:
      1. Bad/missing levels -- without this, a plan with corrupt triggers
         would poll forever even past its own deadline, same bug class.
      2. No cross found this poll -- `now_utc` vs the deadline is the
         right comparison here; there's no candle to anchor to yet.
      3. A cross WAS found -- checked against the CROSS CANDLE'S OWN close
         time (`cross_time`), NOT wall-clock `now_utc`. This is
         deliberate, not an oversight: confirmed_5m_closes() guarantees
         cross_time < now_utc always, so a legitimate same-session cross
         that closed just under the deadline but is only discovered by a
         poll running one cycle late (a tolerance this codebase already
         extends everywhere else) must still count. Only a cross whose OWN
         candle time is at/after the deadline -- i.e. today's price
         crossing a stale plan's frozen levels, the actual incident -- is
         rejected.
    Backward compatible: omitted (None) reproduces the exact prior
    behavior with no expiry check at all.
    """
    if plan.get("status") != "WAITING_CROSS":
        return None
    bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
    if not bo or not bd or bo <= bd:
        if session_expires_at is not None and now_utc >= session_expires_at:
            return {"status": "DONE", "last_transition_reason": "session expired -- levels were never valid"}
        return None  # can't evaluate without real levels -- stay WAITING_CROSS
    side = _confirmed_side(candles_5m, bo, bd)
    if side is None:
        if session_expires_at is not None and now_utc >= session_expires_at:
            return {"status": "DONE", "last_transition_reason": _SESSION_EXPIRED_REASON}
        return None

    is_long = side == _LONG
    box = bo - bd
    trigger = bo if is_long else bd
    opposite_trigger = bd if is_long else bo
    r30_high = plan.get("r30_high")
    r30_low = plan.get("r30_low")
    stop = (r30_low - STOP_BUFFER_BOX * box) if is_long else (r30_high + STOP_BUFFER_BOX * box)
    t1 = trigger + (1 if is_long else -1) * T1_BOX * box
    cross_price = float(candles_5m[-1]["close"])
    cross_time_epoch = candles_5m[-1].get("time")
    cross_time = (
        datetime.datetime.fromtimestamp(cross_time_epoch, tz=datetime.timezone.utc)
        if cross_time_epoch is not None else now_utc
    )
    if session_expires_at is not None and cross_time >= session_expires_at:
        return {"status": "DONE", "last_transition_reason": _SESSION_EXPIRED_REASON}

    rsi_4h_at_cross = rsi_at_cross(candles_4h, cross_time_epoch)
    skipped = tercile_skip(rsi_4h_at_cross, side)

    updates: Dict[str, Any] = {
        "direction": side,
        "box": round(box, 4),
        "stop_price": round(float(stop), 2),
        "t1_price": round(float(t1), 2),
        "cross_time": cross_time,
        "cross_price": cross_price,
        "opposite_trigger": opposite_trigger,
        "journey_cap_at": cross_time + datetime.timedelta(seconds=JOURNEY_CAP_SECONDS),
        "rsi_4h_at_cross": rsi_4h_at_cross,
        "tercile_skipped": skipped,
    }
    if skipped:
        updates["status"] = "TERCILE_SKIPPED"
        updates["last_transition_reason"] = (
            f"{side} cross confirmed at {cross_price:,.2f} -- tercile-skipped "
            f"(RSI-4h-at-cross {rsi_4h_at_cross}) -- not taken, no trade"
        )
    else:
        updates["status"] = "WAITING_TOUCH"
        updates["last_transition_reason"] = (
            f"{side} cross confirmed at {cross_price:,.2f} -- resting limit at "
            f"{trigger:,.2f}, watching for a trigger touch"
        )
    return updates


def advance_waiting_touch(
    plan: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    now_utc: datetime.datetime,
) -> Optional[Dict[str, Any]]:
    """WAITING_TOUCH -> FILLED | DONE (opposite trigger broke, or the
    7-day journey cap passed with no trigger touch).

    2026-09-22 restore (CC_WORK_ORDER_D2_RESTORE_TRIGGER_LIMIT.md): the resting
    limit sits AT THE TRIGGER (placed once, at the cross); it fills on ANY
    SUBSEQUENT WICK TOUCH (high/low), no close-back condition -- matching
    `lab_touchfill_arms.py`'s TF_CROSS arm / `d1_meas_base.py:44-61`'s
    wick-touch harness (the measured, positive-every-year basis), not the
    2026-09-15 build's confirmed-close `pullback_fill()` semantics (measured
    -0.1056R, negative every year, once properly re-run -- the regression
    this restores).

    candles_5m: confirmed 5m closes covering AT LEAST the window from just
    after cross_time through now (the caller fetches a wide-enough window,
    same as trade_plan_engine.py's own candle fetch pattern), each carrying
    real high/low fields. Only bars strictly AFTER cross_time are considered
    -- the cross bar itself is excluded.
    """
    if plan.get("status") != "WAITING_TOUCH":
        return None
    direction = plan.get("direction")
    is_long = direction == _LONG
    trigger = plan.get("breakout_trigger") if is_long else plan.get("breakdown_trigger")
    opposite_trigger = plan.get("opposite_trigger")
    cross_time = plan.get("cross_time")
    journey_cap_at = plan.get("journey_cap_at")
    if trigger is None or cross_time is None:
        return None

    after_cross = [c for c in candles_5m if c.get("time") is not None and c["time"] > cross_time.timestamp()]
    after_cross.sort(key=lambda c: c["time"])

    # Journey end #1: the OPPOSITE trigger gets a confirmed close beyond it
    # first -- the setup is invalidated, no trigger touch fill happens. Stays
    # CONFIRMED-CLOSE based (matches journey_recipes.py::first_cross_after(),
    # the measured basis's own journey-invalidation condition) -- only the
    # entry's OWN fill condition below is wick-based, not this one.
    for c in after_cross:
        close = float(c["close"])
        opposite_broken = (close < opposite_trigger) if is_long else (close > opposite_trigger)
        if opposite_broken:
            return {
                "status": "DONE",
                "last_transition_reason": (
                    f"opposite trigger ({opposite_trigger:,.2f}) broke before any trigger touch fill -- "
                    f"journey ended, not taken"
                ),
            }

    # The trigger touch fill itself: first bar whose WICK (high/low) touches
    # the resting limit's own price -- no close-back condition. fill_price is
    # the trigger (the resting limit's own price), never the touching bar's
    # own high/low/close value.
    for c in after_cross:
        lo, hi = float(c["low"]), float(c["high"])
        filled = (lo <= trigger) if is_long else (hi >= trigger)
        if filled:
            fill_time_epoch = c["time"]
            fill_time = datetime.datetime.fromtimestamp(fill_time_epoch, tz=datetime.timezone.utc)
            return {
                "status": "FILLED",
                "fill_time": fill_time,
                "fill_price": trigger,
                "last_transition_reason": f"trigger touch fill at {trigger:,.2f}",
            }

    # Journey end #2: the 7-day cap passed with no trigger touch fill and no
    # opposite-trigger break either -- disclosed as a real, common outcome
    # (matching the study's own "no-touch journeys, 0R, disclosed" convention).
    if journey_cap_at is not None and now_utc >= journey_cap_at:
        return {
            "status": "DONE",
            "last_transition_reason": "7-day journey cap reached with no trigger touch fill -- not taken",
        }
    return None


# ==============================================================================
# R1 RE-ARM (2026-10-06, Andy ruling 15:24 CT) -- TRAVELER_D1_D2_D3_SPEC.md
# "R1 RE-ARM AMENDMENT". Arm A ONLY (same-lock-day window) -- Arm B (7-day
# journey-cap window) was measured but SHELVED, not built (collides with
# next-day primaries, an unresolved design question). Mirrors advance_
# waiting_cross()/advance_waiting_touch() above exactly in mechanics (same
# wick-touch TF_CROSS fill, same tercile gate, same frozen stop/T1 levels)
# -- the only real differences are: (a) eligibility requires the primary's
# own exhaustion condition to have cleared first (checked by the caller via
# mgmt_e1_stack.check_c5_or_bbwp() -- passed in as a plain bool so this
# module stays decoupled from the D3 module, matching this file's own "D1/
# D2 only" header), (b) the re-cross must be the SAME trigger/side the
# primary already took (re-arm never flips sides), and (c) the waiting-for-
# touch window is REARM_TOUCH_CAP_SECONDS (90 bars / 7.5h) instead of the
# primary's own 7-day journey_cap_at.
#
# rearm_status vocabulary is deliberately separate from the primary's own
# `status` column values (REARM_WATCH/REARM_TERCILE_SKIPPED/REARM_WAITING_
# TOUCH/REARM_FILLED/REARM_WINDOW_CLOSED, never bare WAITING_TOUCH/FILLED/
# DONE reused on a second field) -- same "distinct causes get distinct
# names" convention this codebase already applies elsewhere (e.g.
# ExecutorOrder's CLOSED_EXPIRED vs CLOSED_ENTRY_CANCELED, kept separate on
# purpose). REARM_WINDOW_CLOSED covers every "did not result in a trade"
# outcome (no re-cross before the next lock, opposite trigger broke, 90-bar
# touch cap passed) -- the same one-terminal-status-many-reasons shape
# advance_waiting_touch() above already uses for its own DONE outcomes,
# not a new status per cause.
# ==============================================================================

def advance_rearm_watch(
    plan: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    candles_4h: Optional[List[Dict[str, Any]]],
    now_utc: datetime.datetime,
    exhaustion_cleared: bool,
    rearm_watch_deadline: datetime.datetime,
) -> Optional[Dict[str, Any]]:
    """REARM_WATCH -> REARM_TERCILE_SKIPPED (terminal, no re-entry) |
    REARM_WAITING_TOUCH | REARM_WINDOW_CLOSED (terminal -- the window
    closed, next lock arrived, with no re-cross).

    Caller (traveler_plan_engine.py / executor_live_e1_engine.py) only
    invokes this once plan["rearm_status"] == "REARM_WATCH", set right
    after the PRIMARY ExecutorOrder closes with exit_reason=="C5_EXIT"
    specifically -- not T1/STOP/TIME/BBWP_EXIT (the study's own re-arm
    population was C5_EXIT journeys only).

    exhaustion_cleared: the caller's own mgmt_e1_stack.check_c5_or_bbwp()
    result, inverted (True once BOTH c5_hit and bbwp_hit read False on a
    confirmed bar) -- passed in rather than recomputed here so the
    "cleared" check can never drift from the "fired" check it's the
    literal inverse of, and so this module stays decoupled from the D3
    module (mgmt_e1_stack.py), matching this file's own "D1/D2 only"
    design.
    rearm_watch_deadline: the next 13:00 UTC lock, computed once by the
    caller (session_manager.py) at the moment REARM_WATCH is entered --
    kept out of this module for the same decoupling reason.
    plan: needs "direction" (the PRIMARY journey's own confirmed side --
    a re-cross is ALWAYS the same side, "the SAME trigger", never a flip)
    and "breakout_trigger"/"breakdown_trigger" (the journey's frozen
    levels, never recomputed).
    """
    direction = plan.get("direction")
    bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
    if direction not in (_LONG, _SHORT) or not bo or not bd or bo <= bd:
        return None
    if rearm_watch_deadline is not None and now_utc >= rearm_watch_deadline:
        return {
            "rearm_status": "REARM_WINDOW_CLOSED",
            "rearm_last_transition_reason": "re-arm window closed at the next lock with no re-cross",
        }
    if not candles_5m or not exhaustion_cleared:
        return None   # exhaustion still active, or no fresh data this poll -- stay REARM_WATCH

    is_long = direction == _LONG
    trigger = bo if is_long else bd
    price = float(candles_5m[-1]["close"])
    crossed = (price > trigger) if is_long else (price < trigger)
    if not crossed:
        return None

    re_cross_price = price
    re_cross_time_epoch = candles_5m[-1].get("time")
    re_cross_time = (
        datetime.datetime.fromtimestamp(re_cross_time_epoch, tz=datetime.timezone.utc)
        if re_cross_time_epoch is not None else now_utc
    )

    rsi_4h_at_rearm_cross = rsi_at_cross(candles_4h, re_cross_time_epoch)
    skipped = tercile_skip(rsi_4h_at_rearm_cross, direction)

    updates: Dict[str, Any] = {
        "rearm_cross_time": re_cross_time,
        "rearm_cross_price": re_cross_price,
        "rearm_rsi_4h_at_cross": rsi_4h_at_rearm_cross,
        "rearm_tercile_skipped": skipped,
    }
    if skipped:
        updates["rearm_status"] = "REARM_TERCILE_SKIPPED"
        updates["rearm_last_transition_reason"] = (
            f"{direction} re-cross confirmed at {re_cross_price:,.2f} -- tercile-skipped "
            f"(RSI-4h-at-re-cross {rsi_4h_at_rearm_cross}) -- not taken, no re-entry"
        )
    else:
        updates["rearm_status"] = "REARM_WAITING_TOUCH"
        updates["rearm_entry_expires_at"] = re_cross_time + datetime.timedelta(seconds=REARM_TOUCH_CAP_SECONDS)
        updates["rearm_last_transition_reason"] = (
            f"{direction} re-cross confirmed at {re_cross_price:,.2f} -- resting limit at "
            f"{trigger:,.2f}, watching for a trigger touch (90-bar cap)"
        )
    return updates


def advance_rearm_waiting_touch(
    plan: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    now_utc: datetime.datetime,
) -> Optional[Dict[str, Any]]:
    """REARM_WAITING_TOUCH -> REARM_FILLED | REARM_WINDOW_CLOSED (terminal
    -- opposite trigger broke, or the 90-bar/7.5h touch cap passed with no
    fill; both "did not result in a trade", never a loss -- the executor-
    order-level equivalent for the touch-cap case is CLOSED_EXPIRED, the
    SAME terminal state the primary's own never-touched resting order
    already uses, not a new order-level state).

    Same wick-touch TF_CROSS mechanics as advance_waiting_touch() above
    (a resting limit AT the trigger, fills on ANY subsequent wick touch,
    no close-back condition) -- bounded by plan["rearm_entry_expires_at"]
    (90 bars / 7.5h from the re-cross) instead of the primary's own 7-day
    journey_cap_at. plan needs: "direction", "breakout_trigger"/
    "breakdown_trigger", "opposite_trigger" (SAME as the primary -- the
    journey's frozen levels), "rearm_cross_time", "rearm_entry_expires_at".
    """
    direction = plan.get("direction")
    is_long = direction == _LONG
    trigger = plan.get("breakout_trigger") if is_long else plan.get("breakdown_trigger")
    opposite_trigger = plan.get("opposite_trigger")
    rearm_cross_time = plan.get("rearm_cross_time")
    rearm_entry_expires_at = plan.get("rearm_entry_expires_at")
    if trigger is None or rearm_cross_time is None:
        return None

    after_cross = [c for c in candles_5m if c.get("time") is not None and c["time"] > rearm_cross_time.timestamp()]
    after_cross.sort(key=lambda c: c["time"])

    # Re-arm end #1: the OPPOSITE trigger gets a confirmed close beyond it
    # first -- same journey-invalidation condition advance_waiting_touch()
    # uses for the primary, confirmed-close based (not the entry's own
    # wick-based fill condition below).
    for c in after_cross:
        close = float(c["close"])
        opposite_broken = (close < opposite_trigger) if is_long else (close > opposite_trigger)
        if opposite_broken:
            return {
                "rearm_status": "REARM_WINDOW_CLOSED",
                "rearm_last_transition_reason": (
                    f"opposite trigger ({opposite_trigger:,.2f}) broke before any re-arm trigger "
                    f"touch fill -- re-arm ended, not taken"
                ),
            }

    # The re-arm trigger touch fill itself: first bar whose WICK (high/low)
    # touches the resting limit's own price -- no close-back condition,
    # same as the primary's own entry.
    for c in after_cross:
        lo, hi = float(c["low"]), float(c["high"])
        filled = (lo <= trigger) if is_long else (hi >= trigger)
        if filled:
            fill_time_epoch = c["time"]
            fill_time = datetime.datetime.fromtimestamp(fill_time_epoch, tz=datetime.timezone.utc)
            return {
                "rearm_status": "REARM_FILLED",
                "rearm_fill_time": fill_time,
                "rearm_fill_price": trigger,
                "rearm_last_transition_reason": f"re-arm trigger touch fill at {trigger:,.2f}",
            }

    # Re-arm end #2: the 90-bar/7.5h touch cap passed with no fill and no
    # opposite-trigger break either -- disclosed, never a loss (matches
    # TRAVELER_D1_D2_D3_SPEC.md's own "90-bar cap -> CLOSED_EXPIRED,
    # never a loss" wording).
    if rearm_entry_expires_at is not None and now_utc >= rearm_entry_expires_at:
        return {
            "rearm_status": "REARM_WINDOW_CLOSED",
            "rearm_last_transition_reason": "90-bar re-arm touch cap reached with no trigger touch fill -- not taken",
        }
    return None
