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


def advance_waiting_cross(
    plan: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    now_utc: datetime.datetime,
    candles_4h: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """WAITING_CROSS -> TERCILE_SKIPPED (terminal, no trade) | WAITING_TOUCH.

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
    """
    if plan.get("status") != "WAITING_CROSS":
        return None
    bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
    if not bo or not bd or bo <= bd:
        return None  # can't evaluate without real levels -- stay WAITING_CROSS
    side = _confirmed_side(candles_5m, bo, bd)
    if side is None:
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
