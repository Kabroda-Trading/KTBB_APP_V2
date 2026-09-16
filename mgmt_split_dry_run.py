# mgmt_split_dry_run.py
# ==============================================================================
# MGMT_SPLIT's DRY_RUN candle-only walk -- v1/v2's real, audited management
# rule (50% off at T1, stop stays at the original level for BOTH tiers, the
# runner exits at T3 or its stop, the stop NEVER moves -- the PREMIUM-only
# breakeven-move-at-T2 mechanism was deleted outright 2026-09-15, see
# CC_QUESTION_T2_BREAKEVEN.md) simulated from candles for orders that have
# no real exchange position to poll.
#
# Ruling B (DeepSeek, relayed by Andy 2026-09-15, AGENT_LOG.md both repos):
# v1/v2's own DRY_RUN orders sat at management_state="PENDING_ENTRY" forever
# with no exit ever recorded -- executor_live_engine.py's
# run_executor_position_loop() only ever picks up orders with a real
# entry_exchange_order_id (a LIVE fill), so the ingestion spec's "every
# exit" requirement was unmet for v1/v2's own DRY_RUN population. This is
# the additive fix: same candle-only-walk STYLE as mgmt_e1_stack.py
# (GATE_TRAVELER's own DRY_RUN walk), but MGMT_SPLIT's real two-leg rule
# instead of E1's single full exit. This module has ZERO dependency on
# executor_live_engine.py -- a completely separate module, driven by a
# separate engine loop (dry_run_split_engine.py), per the ruling's explicit
# "own step, additive only" scope. Does not touch the LIVE code paths.
#
# Pure functions only, same convention as gate_traveler.py/mgmt_e1_stack.py
# -- the caller (dry_run_split_engine.py) owns persistence and candle
# fetching.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

_LONG = "LONG"


def _r_multiple(price: float, entry: float, stop: float) -> float:
    """Identical formula to executor_live_engine.py's own _r_multiple() --
    signed R-multiple of `price` relative to entry, in units of the
    original stop distance. Duplicated rather than imported (same cross-
    module convention traveler_plan_engine.py already uses for its own R
    math) so this module has zero dependency on executor_live_engine.py,
    keeping the "don't touch the LIVE module" boundary clean."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return (price - entry) / risk if entry >= stop else (entry - price) / risk


def advance(order: Dict[str, Any], candles_5m: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """order: {"direction", "entry_price", "stop_price", "t1_price",
    "t3_price", "entry_fill_time", "t1_status", "t1_fill_price",
    "t1_fill_time", "t1_leg_r"} (a plain dict, not the ORM row -- same
    pure-function convention as this codebase's other state-machine
    modules). entry_price here means the CONFIRMED fill price (callers
    should pass entry_fill_price), matching executor_live_engine.py's own
    R-multiple convention.

    candles_5m: confirmed closes (caller strips the still-forming trailing
    candle, market_data.confirmed_5m_closes()) covering from
    entry_fill_time through now.

    Returns None if nothing new to report this poll. Otherwise a dict of
    field updates for the caller to apply to the ExecutorOrder row. This
    replays the FULL known candle history every poll (same style as
    mgmt_e1_stack.py), so a trade that fully resolves between two poll
    cycles is still caught correctly in one pass -- the return value can
    carry BOTH the T1-partial fields AND a terminal closure at once.

    Priority mirrors executor_live_engine.py's poll_open_position()
    exactly, translated from real-position polling to candle wicks: BEFORE
    T1 fills, a stop touch is a full, un-blended -1R loss
    (STOP_BEFORE_T1) -- checked first (same STOP-first convention as
    mgmt_e1_stack.py). AFTER T1 fills (50% off, stop unchanged -- the T2
    breakeven move never applied to any tier as of 2026-09-15), the runner
    closes at T3 or the original stop, whichever wick touches first; a
    same-bar tie resolves STOP-first (conservative).
    """
    direction = order.get("direction")
    is_long = direction == _LONG
    entry = order.get("entry_price")
    stop = order.get("stop_price")
    t1 = order.get("t1_price")
    t3 = order.get("t3_price")
    entry_fill_time = order.get("entry_fill_time")
    if entry is None or stop is None or entry_fill_time is None:
        return None
    entry_fill_epoch = entry_fill_time.timestamp() if hasattr(entry_fill_time, "timestamp") else entry_fill_time

    since_entry = [c for c in candles_5m if c.get("time") is not None and c["time"] > entry_fill_epoch]
    since_entry.sort(key=lambda c: c["time"])
    if not since_entry:
        return None

    updates: Dict[str, Any] = {}
    t1_status = order.get("t1_status")
    t1_fill_time = order.get("t1_fill_time")
    t1_fill_epoch = t1_fill_time.timestamp() if hasattr(t1_fill_time, "timestamp") else t1_fill_time

    if t1_status != "FILLED":
        for c in since_entry:
            hi, lo = float(c["high"]), float(c["low"])
            stop_touched = (lo <= stop) if is_long else (hi >= stop)
            if stop_touched:
                return {
                    "management_state": "CLOSED_STOP_BEFORE_T1",
                    "close_reason": "STOP_BEFORE_T1", "exit_reason": "STOP_BEFORE_T1",
                    "exit_price": stop, "exit_time": _epoch_to_dt(c["time"]),
                    "realized_pnl_r": -1.0,
                }
            t1_touched = t1 is not None and ((hi >= t1) if is_long else (lo <= t1))
            if t1_touched:
                t1_fill_epoch = c["time"]
                updates.update({
                    "t1_status": "FILLED", "t1_fill_price": t1,
                    "t1_fill_time": _epoch_to_dt(t1_fill_epoch),
                    "t1_leg_r": 0.5 * _r_multiple(t1, entry, stop),
                    "management_state": "T1_FILLED",
                })
                break
        else:
            return updates or None   # walked every bar -- no stop/T1 touch yet

    if t1_fill_epoch is None:
        return updates or None   # defensive -- unreachable given the block above

    t1_leg_r = updates.get("t1_leg_r", order.get("t1_leg_r"))
    runner_bars = [c for c in since_entry if c["time"] > t1_fill_epoch]
    for c in runner_bars:
        hi, lo = float(c["high"]), float(c["low"])
        stop_touched = (lo <= stop) if is_long else (hi >= stop)
        t3_touched = t3 is not None and ((hi >= t3) if is_long else (lo <= t3))
        if stop_touched or t3_touched:
            exit_price = stop if stop_touched else t3
            reason = "RUNNER_STOP" if stop_touched else "T3"
            runner_r = 0.5 * _r_multiple(exit_price, entry, stop)
            updates.update({
                "management_state": f"CLOSED_{reason}",
                "close_reason": reason, "exit_reason": reason,
                "exit_price": exit_price, "exit_time": _epoch_to_dt(c["time"]),
                "runner_r": runner_r,
                "realized_pnl_r": (t1_leg_r or 0.0) + runner_r,
            })
            return updates

    return updates or None


def _epoch_to_dt(epoch: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
