# cme_signals.py
# ==============================================================================
# CME SIGNALS -- pure functions, no I/O, no DB. Candles are plain dicts with
# "epoch"/"open"/"high"/"low"/"close" keys (the same shape the Brain repo's
# own calibration_data/cme_futures/*.csv files use), matching gate_traveler.py's
# established convention of recomputing everything fresh from a passed-in
# candle window each call, no carried state between calls.
#
# PARITY FOR AUDIT: this module's D1 lock formula and D2 entry-confirmation
# logic were written to reproduce, bar-for-bar, two things independently
# verified against the Kabroda AI Brain repo's own committed evidence before
# any of this was trusted (CC audit, 2026-11-04, CC_INTERFACE.md item M3):
#
#   1. scripts/reconstruct_locks_cme_symbol.py -- the ACTUAL script that
#      generated locks_cme_nq.csv/locks_cme_es.csv, the files the verified
#      10.4-year backtest and the 4,049-trade joint-basket ledger both read.
#      Reading it directly (not relaying a description) surfaced THREE real
#      divergences from both of the Brain repo's own "reference" engines
#      (scripts/run_cme_live_daemon.py and brain/engine/cme_futures_engine.py),
#      none previously disclosed:
#        a. The TPO value area is built from 1-MINUTE bars across the full
#           09:30-10:00 ET window (>=15 required, up to ~31), NOT six 5-
#           minute bars -- a materially finer histogram than either
#           "reference" engine uses.
#        b. Row size is 0.05% of the window's low price (`overall_low *
#           0.0005`), NOT a fixed 0.25-point tick -- both reference engines
#           hardcode 0.25.
#        c. The value-area expansion walks by PAIRS of rows, comparing the
#           sum of the next two rows on each side (classic Steidlmayer
#           "expand by pairs"), NOT one row at a time -- both reference
#           engines expand one row at a time.
#      bo = poc + 0.5*box, bd = poc - 0.5*box (its own lines 148-149) is the
#      one piece that DOES match cme_futures_engine.py -- but the other
#      three differences change what box, poc, vah, val actually equal in
#      the first place, which cascades into bo/bd too. Since this is the
#      only implementation that actually produced the files the verified
#      backtest and the joint-basket ledger were built from, this module
#      matches it exactly, not either "reference" engine.
#   2. **Stop rule is symbol-specific, confirmed by direct measurement of
#      the committed ledger, not assumed:** every one of 2,046 MES/ES rows
#      in joint_basket_cme_ledger.csv has stop_distance/box == 1.0 exactly
#      (stdev 0.0) -- a fixed 1.0x box stop. Every one of 2,003 MNQ/NQ rows
#      has a VARYING ratio (mean 0.96, range 0.54-1.47) -- a VA-boundary
#      stop (val for long, vah for short), which isn't a fixed box multiple.
#      STOP_MODE_BY_SYMBOL below encodes this -- do not default both symbols
#      to the same stop rule. NOTE: locks_cme_nq.csv/locks_cme_es.csv's OWN
#      baked-in stop_long/stop_short columns (r30 -/+ 0.12*box -- the same
#      formula gate_traveler.py uses for BTC) are confirmed VESTIGIAL --
#      verified by direct reproduction (2026-11-04: ported this module,
#      ran it against a real 2016-06-09 MNQ session, matched the CSV's own
#      r30h/r30l/poc/vah/val/box/bo/bd/reachability to 2 decimal places, but
#      stop_long/stop_short did NOT match val/vah -- they matched r30-0.12*
#      box instead, which is NOT what either audit script actually reads:
#      both read `val`/`vah` directly off the locks dataframe for the
#      VA_boundary stop mode, never the CSV's own stop_long/stop_short
#      columns). Do not "fix" this module to match those two columns --
#      they were never consumed by the backtest that produced the verified
#      numbers.
#   3. scripts/audit_cme_universe_comparison.py (lines 64-95) and scripts/
#      calibrate_cme_grid_complete.py (lines 143-153) -- both backing the
#      now-verified M3 numbers -- implement entry as: confirmed 5m bar CLOSE
#      beyond bo/bd (checked across up to 12 bars post-lock) THEN a bounded
#      passive retest back to the trigger price within a further 12 bars (60
#      min); no retest within that window means STAND DOWN, never enter.
#      CC_INTERFACE.md §8D's "Stop-Limit Entry" wording describes a
#      materially different mechanic (fires the instant price first reaches
#      the trigger, no close-confirmation, no retest, no stand-down path)
#      that was never actually backtested by anything behind M3's numbers.
#      check_cross()/check_retest_fill() below implement the mechanic that
#      was actually measured. (This one IS 5-minute bars, matching both
#      scripts -- only the D1 lock itself needed the 1-minute correction
#      above.)
#
# The BE-ratchet causality fix (M3's own root-cause finding) matters here
# too: check_exit() below applies a ratchet-triggered stop move starting
# from the NEXT bar after the trigger is touched, never the same bar --
# checking the same bar's low/high against a stop that bar itself just
# moved is exactly the intra-bar look-ahead bug M3 found and fixed.
# ==============================================================================

import math
from typing import Any, Dict, List, Optional

ROW_SIZE_PCT = 0.0005        # row size = 0.05% of the window's low price (reconstruct_locks_cme_symbol.py)
MIN_CALIBRATION_BARS = 15    # minimum 1m bars required in the 09:30-10:00 ET window
VALUE_AREA_PCT = 0.70        # Steidlmayer TPO 70% value area
REACHABILITY_MAX = 0.55      # box / dailyATR14 <= 0.55
TARGET_MULT = 2.0            # +/- 2.0x box
BE_TRIGGER_MULT = 0.8        # ratchet fires at +/- 0.8x box
BE_STOP_MULT = 0.1           # locks in +/- 0.1x box profit
CROSS_WINDOW_BARS = 12       # 12 x 5m = 60 min to find a confirmed close beyond bo/bd
RETEST_WINDOW_BARS = 12      # a further 60 min to find the retest fill

POINT_MULTIPLIER = {"MNQ": 2.0, "MES": 5.0}   # $/point, CANON §2:48

# Stop rule is symbol-specific -- confirmed by direct measurement of
# joint_basket_cme_ledger.csv (see module header). "VA_BOUNDARY": val for
# long / vah for short. "BOX_1X": trigger -/+ 1.0*box, which algebraically
# equals bd/bo since box == bo - bd (kept as its own branch for clarity and
# so a future symbol with a different multiple is a one-line change).
STOP_MODE_BY_SYMBOL = {"MNQ": "VA_BOUNDARY", "MES": "BOX_1X"}
DEFAULT_STOP_MODE = "BOX_1X"


def compute_tpo_value_area(bars_1m: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Steidlmayer TPO 70% value area from the 9:30-10:00 ET calibration
    window's 1-MINUTE bars (NOT 5-minute -- see module header finding 1a).
    Returns None if fewer than MIN_CALIBRATION_BARS are present (a closed/
    holiday session, or a late/incomplete fetch) -- the caller stands down
    for the day entirely rather than calibrating off a partial window
    (matching reconstruct_locks_cme_symbol.py's own `if len(group) < 15:
    continue` -- no lock row at all for that date, not a reachable=False
    row)."""
    if not bars_1m or len(bars_1m) < MIN_CALIBRATION_BARS:
        return None

    highs = [float(b["high"]) for b in bars_1m]
    lows = [float(b["low"]) for b in bars_1m]
    r30h = max(highs)
    r30l = min(lows)
    if r30h <= r30l:
        return None

    row_size = r30l * ROW_SIZE_PCT
    if row_size <= 0:
        row_size = 0.25

    num_rows = int(math.ceil((r30h - r30l) / row_size)) + 1
    tpo_counts = [0] * num_rows
    for lo, hi in zip(lows, highs):
        idx_lo = max(0, min(int((lo - r30l) / row_size), num_rows - 1))
        idx_hi = max(0, min(int((hi - r30l) / row_size), num_rows - 1))
        for r in range(idx_lo, idx_hi + 1):
            tpo_counts[r] += 1

    total_tpos = sum(tpo_counts)
    if total_tpos <= 0:
        return None
    poc_idx = max(range(num_rows), key=lambda i: tpo_counts[i])
    target_tpos = total_tpos * VALUE_AREA_PCT

    # Expand by PAIRS of rows on whichever side has the larger two-row sum
    # (classic Steidlmayer "expand by pairs" convention) -- NOT one row at
    # a time. Exact mirror of reconstruct_locks_cme_symbol.py::tpo_value_area().
    accum = tpo_counts[poc_idx]
    up_idx, dn_idx = poc_idx + 1, poc_idx - 1
    while accum < target_tpos and (up_idx < num_rows or dn_idx >= 0):
        up_sum = (tpo_counts[up_idx] if up_idx < num_rows else 0) + \
                 (tpo_counts[up_idx + 1] if up_idx + 1 < num_rows else 0)
        dn_sum = (tpo_counts[dn_idx] if dn_idx >= 0 else 0) + \
                 (tpo_counts[dn_idx - 1] if dn_idx - 1 >= 0 else 0)

        if up_sum > dn_sum or (up_sum == dn_sum and up_idx < num_rows):
            if up_idx < num_rows:
                accum += tpo_counts[up_idx]
                up_idx += 1
            if accum < target_tpos and up_idx < num_rows:
                accum += tpo_counts[up_idx]
                up_idx += 1
        else:
            if dn_idx >= 0:
                accum += tpo_counts[dn_idx]
                dn_idx -= 1
            if accum < target_tpos and dn_idx >= 0:
                accum += tpo_counts[dn_idx]
                dn_idx -= 1

    val_idx = max(0, dn_idx + 1)
    vah_idx = min(num_rows - 1, up_idx - 1)
    poc = r30l + (poc_idx + 0.5) * row_size
    vah = r30l + (vah_idx + 1.0) * row_size
    val = r30l + val_idx * row_size
    box = vah - val
    if box <= 0:
        return None

    return {"r30h": r30h, "r30l": r30l, "poc": poc, "vah": vah, "val": val, "box": box}


def reachability_ratio(box: float, daily_atr14: float) -> float:
    """box / dailyATR14 -- the single strongest D1 signal from the original
    backtest. Returns a large sentinel (always fails the gate) if ATR14 is
    missing/non-positive rather than dividing by zero or silently passing."""
    if daily_atr14 is None or daily_atr14 <= 0:
        return 999.0
    return box / daily_atr14


def calculate_session_lock(symbol: str, bars_1m: List[Dict[str, Any]], daily_atr14: Optional[float]) -> Optional[Dict[str, Any]]:
    """Full D1: TPO 70% value area (from 1-minute calibration bars -- see
    compute_tpo_value_area()) + reachability gate + bo/bd/stop/target/be
    fields for both sides. Returns a dict with status="ARMED" (reachable) or
    status="STAND_DOWN_UNREACHABLE" (box too wide relative to ATR14) --
    callers persist this as a CMEPlan row either way (every lock attempt
    gets a row, win or stand-down, same convention SessionLock/TravelerPlan
    use for BTC). Returns None only if the TPO calculation itself couldn't
    run at all (fewer than MIN_CALIBRATION_BARS 1m bars in the window)."""
    tpo = compute_tpo_value_area(bars_1m)
    if tpo is None:
        return None

    box = tpo["box"]
    poc = tpo["poc"]
    reach = reachability_ratio(box, daily_atr14)

    plan: Dict[str, Any] = {
        "symbol": symbol,
        "r30h": tpo["r30h"], "r30l": tpo["r30l"],
        "poc": poc, "vah": tpo["vah"], "val": tpo["val"], "box": box,
        "atr14": daily_atr14, "reachability": reach,
    }

    if reach > REACHABILITY_MAX:
        plan["status"] = "STAND_DOWN_UNREACHABLE"
        plan["status_reason"] = f"box {box:.2f} / ATR14 {daily_atr14} = {reach:.3f} > {REACHABILITY_MAX}"
        return plan

    # bo/bd = poc +/- 0.5*box -- verified against the real backtest
    # generator (reconstruct_locks_cme_symbol.py), NOT vah/val directly.
    bo = poc + 0.5 * box
    bd = poc - 0.5 * box

    # Stop rule is symbol-specific -- see STOP_MODE_BY_SYMBOL's own comment.
    stop_mode = STOP_MODE_BY_SYMBOL.get(symbol.upper(), DEFAULT_STOP_MODE)
    if stop_mode == "VA_BOUNDARY":
        stop_long, stop_short = tpo["val"], tpo["vah"]
    else:
        stop_long, stop_short = bd, bo   # trigger -/+ 1.0*box, algebraically == bd/bo

    plan.update({
        "status": "ARMED",
        "status_reason": None,
        "bo": bo, "bd": bd,
        "stop_long": stop_long, "stop_short": stop_short,
        "target_long": bo + TARGET_MULT * box, "target_short": bd - TARGET_MULT * box,
        "be_trigger_long": bo + BE_TRIGGER_MULT * box, "be_trigger_short": bd - BE_TRIGGER_MULT * box,
        "be_stop_long": bo + BE_STOP_MULT * box, "be_stop_short": bd - BE_STOP_MULT * box,
    })
    return plan


def check_cross(bars_5m_confirmed: List[Dict[str, Any]], bo: float, bd: float) -> Optional[Dict[str, Any]]:
    """D2a: scans up to CROSS_WINDOW_BARS confirmed 5m closes (post-lock)
    for the first one beyond bo (LONG) or bd (SHORT). Returns None if no
    cross happens within the window -- the caller marks the plan DONE/
    NO_CROSS, never enters."""
    for i, b in enumerate(bars_5m_confirmed[:CROSS_WINDOW_BARS]):
        close = float(b["close"])
        if close > bo:
            return {"side": "LONG", "trigger": bo, "cross_bar": b, "cross_index": i}
        if close < bd:
            return {"side": "SHORT", "trigger": bd, "cross_bar": b, "cross_index": i}
    return None


def check_retest_fill(bars_5m_confirmed_since_cross: List[Dict[str, Any]], side: str, trigger: float) -> Optional[Dict[str, Any]]:
    """D2b: scans up to RETEST_WINDOW_BARS confirmed 5m bars AFTER the cross
    for the first bar whose wick touches back to `trigger` (low<=trigger for
    LONG, high>=trigger for SHORT) -- the passive resting-limit-fill model
    actually backtested. Returns None if no retest happens within the
    window -- the caller marks the order NO_RETEST, never enters."""
    for i, b in enumerate(bars_5m_confirmed_since_cross[:RETEST_WINDOW_BARS]):
        if side == "LONG" and float(b["low"]) <= trigger:
            return {"fill_price": trigger, "fill_bar": b, "fill_index": i}
        if side == "SHORT" and float(b["high"]) >= trigger:
            return {"fill_price": trigger, "fill_bar": b, "fill_index": i}
    return None


def check_breakeven_ratchet(current_price: float, direction: str, be_trigger: float) -> bool:
    """D3: True once price has traded to/through the +/-0.8x box ratchet
    trigger. Pure comparison -- callers amend the exchange-side stop to
    be_stop (+/-0.1x box) exactly once (ExecutorOrder/AltMatrixOrder/CMEOrder
    all use their own be_amended boolean to enforce 'exactly once')."""
    if direction.upper() == "LONG":
        return current_price >= be_trigger
    return current_price <= be_trigger


def check_exit(
    bars_5m_since_fill: List[Dict[str, Any]],
    direction: str,
    stop_price: float,
    target_price: float,
    be_trigger: Optional[float] = None,
    be_stop_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """D3, pure replay form (parity tests / DRY_RUN only -- the live
    executor detects STOP/TARGET passively via exchange order state instead,
    the same way executor_live_e1_engine.py does for Traveler). Wick-based,
    first-touch-wins scan, CAUSALLY CORRECT per M3's own root-cause fix: if
    bar k's wick touches the ratchet trigger, the stop only moves to be_stop
    starting at bar k+1 -- bar k's OWN low/high is still checked against the
    stop price that was active BEFORE this bar, never the one it just
    moved to. (The bug M3 found and fixed was checking the same bar against
    the just-moved stop -- that falsely stops out winners, since breakout
    bars almost always dip before rallying.) Returns None if still open
    through every bar in the window."""
    sgn = 1 if direction.upper() == "LONG" else -1
    cur_stop = stop_price
    ratcheted = False
    for b in bars_5m_since_fill:
        lo, hi = float(b["low"]), float(b["high"])

        if (direction.upper() == "LONG" and lo <= cur_stop) or (direction.upper() == "SHORT" and hi >= cur_stop):
            return {
                "exit_reason": "CLOSED_BE_STOP" if ratcheted else "CLOSED_STOP",
                "exit_price": cur_stop, "exit_bar": b,
            }

        if (direction.upper() == "LONG" and hi >= target_price) or (direction.upper() == "SHORT" and lo <= target_price):
            return {"exit_reason": "CLOSED_TARGET", "exit_price": target_price, "exit_bar": b}

        # Ratchet check happens LAST, so a trigger touched this same bar
        # only takes effect starting next bar's stop check above.
        if not ratcheted and be_trigger is not None and be_stop_price is not None:
            if (direction.upper() == "LONG" and hi >= be_trigger) or (direction.upper() == "SHORT" and lo <= be_trigger):
                ratcheted = True
                cur_stop = be_stop_price

    return None


def dynamic_contract_count(symbol: str, box_points: float, daily_risk_budget_usd: float = 350.0, contracts_cap: int = 3) -> int:
    """CANON §2:48: max(1, min(cap, floor(risk_budget / (box_points *
    point_multiplier)))). Never returns 0 -- a reachability-gated box is
    already known to be small enough that at least 1 contract fits within
    the budget in every historical case; this floors at 1 rather than
    silently standing down on a pathological box/budget combination."""
    multiplier = POINT_MULTIPLIER.get(symbol.upper(), 2.0)
    if box_points <= 0:
        return 1
    raw = int(daily_risk_budget_usd // (box_points * multiplier))
    return max(1, min(contracts_cap, raw))
