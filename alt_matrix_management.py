# alt_matrix_management.py
# ==============================================================================
# ALT MATRIX D3 -- pure, stateless management function, mirroring mgmt_e1_
# stack.py's own proven shape exactly (one pure function, shared by BOTH
# the DRY_RUN simulation and the real LIVE poll, so the two can never
# drift apart -- "promoted to its own module so there's one source, not
# two copies," same reasoning that file's own header states). No I/O, no
# DB, no imports of/from gate_traveler.py, traveler_plan_engine.py,
# mgmt_e1_stack.py, or any other Traveler module -- the BTC Iron Wall
# (ALT_MATRIX_D1_D2_D3_SPEC.md acceptance criterion 5) applies here too,
# even though this module's own logic looks structurally similar.
#
# Priority per confirmed 4H bar, oldest-since-entry first (mirrors the
# bar-by-bar order of operations in the real walk-forward backtest,
# brain/audit_evidence/altcoin_study_sol/walkforward_htf_sol.py:66-94,
# EXCEPT that script never implements the breakeven amendment at all --
# that's new behavior, ruled on by Andy 2026-10-09, inserted here in the
# same position the backtest's own MFE-threshold branch occupies):
#   1. STOP -- bar's own low touches the CURRENT effective stop (initial
#      or breakeven, whichever is in effect going into this bar).
#   2. BREAKEVEN AMENDMENT TRIGGER -- MFE (from bar highs since entry)
#      first reaches +2.0R and the position hasn't been amended yet.
#      NOT an exit -- returns an action for the caller to actually amend
#      the live exchange stop, then persist be_amended=True/the new
#      stop_price before calling again.
#   3. TRAIL EXIT -- MFE>=2R: bar closes below the 4H 21 EMA
#      (EMA21_TRAIL). MFE<2R: bar closes below the 4H 55 EMA
#      (EMA55_CLOSE).
#
# Returns on the FIRST actionable event found, exactly like mgmt_e1_
# stack.advance()'s own "return on first hit" convention -- never
# resolves more than one event per call. A caller that finds multiple
# confirmed bars since its last poll (e.g. after a restart gap) simply
# calls again after applying the first result; the next call re-walks
# from entry with the now-updated order state (be_amended/stop_price),
# so nothing is silently skipped.
# ==============================================================================

import datetime
from typing import Any, Dict, List, Optional

BREAKEVEN_TRIGGER_R = 2.0
BREAKEVEN_OFFSET_R = 0.1


def _epoch_to_dt(epoch) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)


def mfe_through(candles_since_entry: List[Dict[str, Any]], entry_price: float, r_distance: float) -> float:
    """Running max favorable excursion, in R, using each bar's own HIGH
    (LONG-only system -- spec never describes a short leg). Pure,
    recomputed fresh each call from the full bar history since entry,
    matching this module's own stateless convention -- never trusts a
    stored mfe_r field as ground truth, only as a cache the caller may
    keep for display."""
    if r_distance <= 0:
        return 0.0
    mfe = 0.0
    for c in candles_since_entry:
        cur_r = (float(c["high"]) - entry_price) / r_distance
        if cur_r > mfe:
            mfe = cur_r
    return mfe


def advance(
    order: Dict[str, Any],
    candles_4h_confirmed: List[Dict[str, Any]],
    now_utc: datetime.datetime,
) -> Optional[Dict[str, Any]]:
    """order: {"entry_price", "stop_price" (CURRENT effective stop),
    "entry_fill_time", "be_amended" (bool), "ema21_series"/"ema55_series"
    -- OPTIONAL pre-computed EMA series aligned 1:1 with
    candles_4h_confirmed (callers already have these from alt_matrix_
    signals.py's own ema_series(), recomputing them here would be
    wasteful and risks a second source of truth drifting from the
    first); if omitted, this function computes them itself from the
    candle closes via alt_matrix_signals.ema_series() for callers that
    don't already have them (e.g. a quick test).}.

    candles_4h_confirmed: the FULL confirmed 4H series this symbol's own
    D1 signal evaluation already fetched -- NOT pre-filtered to "since
    entry" by the caller (unlike mgmt_e1_stack.advance()'s own
    candles_5m convention) because this function needs bars BEFORE entry
    too, to compute EMA21/55 with their real warm-up history intact; it
    does its own "since entry" filtering internally.

    Returns None (still open), {"action": "AMEND_TO_BE", "be_price":
    ..., "at_time": ...}, or {"action": "EXIT", "exit_reason": "STOP"|
    "BE_STOP"|"EMA21_TRAIL"|"EMA55_CLOSE", "exit_price": ..., "exit_time":
    ...}.
    """
    entry_price = order.get("entry_price")
    stop_price = order.get("stop_price")
    entry_fill_time = order.get("entry_fill_time")
    be_amended = bool(order.get("be_amended", False))
    if entry_price is None or stop_price is None or entry_fill_time is None:
        return None

    # R is always measured against the position's ORIGINAL risk distance,
    # frozen at entry -- never against whatever the stop happens to be
    # right now (same convention Traveler's own gate_traveler.py/mgmt_e1_
    # stack.py use). Once be_amended is True, `stop_price` IS the
    # breakeven price, so `abs(entry_price - stop_price)` would silently
    # compute the wrong (much smaller) distance -- r_distance MUST come
    # from the caller (frozen at sizing time, same field the AltMatrixOrder
    # row itself stores), never re-derived here. No fallback: a missing
    # r_distance means "can't evaluate," not "guess one."
    r_distance = order.get("r_distance")
    if not r_distance or r_distance <= 0:
        return None

    entry_fill_epoch = entry_fill_time.timestamp() if hasattr(entry_fill_time, "timestamp") else entry_fill_time
    since_entry_idx = [i for i, c in enumerate(candles_4h_confirmed) if c.get("time") is not None and c["time"] > entry_fill_epoch]
    if not since_entry_idx:
        return None

    ema21_series = order.get("ema21_series")
    ema55_series = order.get("ema55_series")
    if ema21_series is None or ema55_series is None:
        import alt_matrix_signals
        closes = [float(c["close"]) for c in candles_4h_confirmed]
        ema21_series = alt_matrix_signals.ema_series(closes, 21)
        ema55_series = alt_matrix_signals.ema_series(closes, 55)

    running_mfe = 0.0
    # Reconstruct MFE accumulated strictly BEFORE the first since-entry
    # bar is impossible (there is none -- entry is the start), so
    # running_mfe starts at 0 and accumulates only from since-entry bars,
    # matching mfe_through()'s own definition exactly.
    for idx in since_entry_idx:
        c = candles_4h_confirmed[idx]
        hi, lo, cl = float(c["high"]), float(c["low"]), float(c["close"])

        cur_r = (hi - entry_price) / r_distance
        if cur_r > running_mfe:
            running_mfe = cur_r

        if lo <= stop_price:
            return {
                "action": "EXIT",
                "exit_reason": "BE_STOP" if be_amended else "STOP",
                "exit_price": stop_price,
                "exit_time": _epoch_to_dt(c["time"]),
            }

        if not be_amended and running_mfe >= BREAKEVEN_TRIGGER_R:
            be_price = entry_price + BREAKEVEN_OFFSET_R * r_distance
            return {
                "action": "AMEND_TO_BE",
                "be_price": be_price,
                "at_time": _epoch_to_dt(c["time"]),
            }

        ema21_now, ema55_now = ema21_series[idx], ema55_series[idx]
        if running_mfe >= BREAKEVEN_TRIGGER_R:
            if ema21_now is not None and cl < ema21_now:
                return {
                    "action": "EXIT", "exit_reason": "EMA21_TRAIL",
                    "exit_price": cl, "exit_time": _epoch_to_dt(c["time"]),
                }
        else:
            if ema55_now is not None and cl < ema55_now:
                return {
                    "action": "EXIT", "exit_reason": "EMA55_CLOSE",
                    "exit_price": cl, "exit_time": _epoch_to_dt(c["time"]),
                }

    return None
