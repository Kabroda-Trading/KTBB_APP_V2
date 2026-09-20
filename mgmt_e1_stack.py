# mgmt_e1_stack.py
# ==============================================================================
# MGMT_E1_STACK -- D3 management for the traveler-candidate lineage. Pure
# functions only (same convention as gate_traveler.py/trade_plan.py), the
# caller (executor_live_engine.py's DRY_RUN walk) owns persistence and
# candle fetching.
#
# Built 2026-09-15 per CC_WORK_ORDER_PHASE2.md step 4, against the frozen
# source -- `lab_touchfill_arms.py::walk_from_fill()` (Kabroda AI Brain
# repo, CANON §9d) -- not the handoff's original prose, which had two
# confirmed errors here (rulings fcfb19a/74344cd/7ee590d, AGENT_LOG.md both
# repos):
#   - bar granularity: ALL of STOP/C5/BBWP/T1/TIME evaluate on 5-MINUTE
#     bars, not "15M" (the frozen walk's own bars5/times5/highs5/lows5/
#     closes5 throughout).
#   - per-bar priority: STOP is checked FIRST, then C5-or-BBWP (exit at
#     the CURRENT 5m bar's close), then T1, then TIME at journey end.
#     (walk_from_fill() lines 199-213, verbatim.)
#
# C5_T-M_EXIT / BBWP_BURN_70 construction: study_indicators.py's
# rsi_series()/bbwp_series()/c5_momentum_decay()/bbwp_burn() -- verified
# byte-identical to the study's own functions on real data (AGENT_LOG.md,
# 2026-09-15).
#
# LIVE-POLL TRANSLATION NOTE: the backtest walk_from_fill() replays a
# COMPLETE, already-known candle array in one pass. A live poll instead
# checks "as of right now, has the condition become true" every 60s --
# the exit fires on the poll that FIRST observes it true, which can lag
# the theoretical 1H/4H bar-close instant by up to one poll cycle (same
# tolerance every other 60s-cadence poll in this codebase already
# accepts, e.g. trade_plan_engine.py's own cross detection). Not a
# formula difference -- a real-time-vs-replay timing tolerance.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import study_indicators as si

_LONG = "LONG"

BBWP_BURN_THRESHOLD = 70.0
C5_LOOKBACK_BARS = 6

# P3 (2026-09-20, executor_live_e1_engine.py): the ONE shared vocabulary for
# MGMT_E1_STACK's terminal management_state values, used by BOTH the DRY_RUN
# walk (traveler_plan_engine.py) and the live execution engine
# (executor_live_e1_engine.py) -- CC_INTERFACE.md audit item 6 ("same rules,
# same vocabulary, same state names" across DRY_RUN and LIVE). Was private
# and defined only in traveler_plan_engine.py before this; promoted here so
# there is one source, not two copies that could drift.
MGMT_E1_TERMINAL_STATES = ("CLOSED_STOP", "CLOSED_C5_EXIT", "CLOSED_BBWP_EXIT", "CLOSED_T1", "CLOSED_TIME", "CLOSED_ERROR")


def advance(
    order: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    candles_1h: List[Dict[str, Any]],
    candles_4h: List[Dict[str, Any]],
    now_utc: datetime.datetime,
    journey_cap_at: Optional[datetime.datetime] = None,
) -> Optional[Dict[str, Any]]:
    """order: {"direction", "entry_price", "stop_price", "t1_price",
    "entry_fill_time"} (a plain dict, not the ORM row -- same pure-
    function convention as this codebase's other state-machine modules).

    candles_5m/candles_1h/candles_4h: confirmed closes (caller strips the
    still-forming trailing candle -- market_data.confirmed_5m_closes()),
    covering from entry_fill_time through now.

    Returns None if still open (keep polling), or a dict with exit_price/
    exit_time/exit_reason/c5_fired/bbwp_fired once resolved.
    """
    direction = order.get("direction")
    is_long = direction == _LONG
    entry = order.get("entry_price")
    stop = order.get("stop_price")
    t1 = order.get("t1_price")
    entry_fill_time = order.get("entry_fill_time")
    if entry is None or stop is None or entry_fill_time is None:
        return None
    entry_fill_epoch = entry_fill_time.timestamp() if hasattr(entry_fill_time, "timestamp") else entry_fill_time

    since_entry = [c for c in candles_5m if c.get("time") is not None and c["time"] > entry_fill_epoch]
    since_entry.sort(key=lambda c: c["time"])
    if not since_entry:
        return None

    # (1) STOP -- wick-based, checked first, same priority as the frozen walk.
    for c in since_entry:
        hi, lo = float(c["high"]), float(c["low"])
        touched = (lo <= stop) if is_long else (hi >= stop)
        if touched:
            return {
                "exit_reason": "STOP", "exit_price": stop,
                "exit_time": _epoch_to_dt(c["time"]),
                "c5_fired": False, "bbwp_fired": False,
            }

    # (2) C5-or-BBWP -- exit at the CURRENT (latest confirmed) 5m bar's
    # close, the instant either condition is observed true. Freshly
    # recomputed each poll from the 1H/4H closes since entry -- cheap at
    # live candle-window sizes (the site's own limit=100 fetches).
    c5_hit, bbwp_hit = check_c5_or_bbwp(candles_1h, candles_4h)
    if c5_hit or bbwp_hit:
        last = since_entry[-1]
        return {
            "exit_reason": "C5_EXIT" if c5_hit else "BBWP_EXIT",
            "exit_price": float(last["close"]), "exit_time": _epoch_to_dt(last["time"]),
            "c5_fired": bool(c5_hit), "bbwp_fired": bool(bbwp_hit),
        }

    # (3) T1 -- wick-based full exit, E1's own semantics (100%, no partial).
    if t1 is not None:
        for c in since_entry:
            hi, lo = float(c["high"]), float(c["low"])
            touched = (hi >= t1) if is_long else (lo <= t1)
            if touched:
                return {
                    "exit_reason": "T1", "exit_price": t1,
                    "exit_time": _epoch_to_dt(c["time"]),
                    "c5_fired": False, "bbwp_fired": False,
                }

    # (4) TIME -- journey end (opposite-trigger break or 7-day cap, tracked
    # on the linked TravelerPlan) with none of the above having fired.
    if journey_cap_at is not None and now_utc >= journey_cap_at:
        last = since_entry[-1]
        return {
            "exit_reason": "TIME", "exit_price": float(last["close"]),
            "exit_time": _epoch_to_dt(last["time"]),
            "c5_fired": False, "bbwp_fired": False,
        }
    return None


def check_c5_or_bbwp(candles_1h: List[Dict[str, Any]], candles_4h: List[Dict[str, Any]]) -> Tuple[bool, bool]:
    """The C5_T-M_EXIT / BBWP_BURN_70 condition check, extracted from
    advance() (P3, 2026-09-20) so it has ONE implementation shared by both
    the DRY_RUN walk (this module's own advance(), below) and the live
    execution engine (executor_live_e1_engine.py) -- CC_INTERFACE.md audit
    item 6. Pure function, no behavior change from what advance() already
    did inline. Returns (c5_hit, bbwp_hit)."""
    h1_closes = [float(c["close"]) for c in candles_1h]
    h4_closes = [float(c["close"]) for c in candles_4h]
    rsi_1h = si.rsi_series(h1_closes, period=si.RSI_PERIOD)
    rsi_4h = si.rsi_series(h4_closes, period=si.RSI_PERIOD)
    bbwp_4h = si.bbwp_series(h4_closes, period=si.BBWP_PERIOD, lookback=si.BBWP_LOOKBACK)

    c5_hit = (
        (len(rsi_1h) > 0 and si.c5_momentum_decay(rsi_1h, len(rsi_1h) - 1, C5_LOOKBACK_BARS))
        or (len(rsi_4h) > 0 and si.c5_momentum_decay(rsi_4h, len(rsi_4h) - 1, C5_LOOKBACK_BARS))
    )
    bbwp_hit = len(bbwp_4h) > 0 and si.bbwp_burn(bbwp_4h, len(bbwp_4h) - 1, BBWP_BURN_THRESHOLD)
    return bool(c5_hit), bool(bbwp_hit)


def _epoch_to_dt(epoch: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
