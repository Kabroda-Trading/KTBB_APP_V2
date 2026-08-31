# structure_state_engine.py
# ==============================================================================
# KABRODA STRUCTURE STATE ENGINE — v2.0 (LAW LAYER)
# ------------------------------------------------------------------------------
# Purpose:
# - Takes LOCKED levels (computed once per session after 30m lock)
# - Evaluates ONLY post-lock 5m candles to prevent drift
# - Produces a deterministic "what now?" packet the UI can render
#
# This is NOT the SSE level computation. This is the "structure + acceptance" logic.
# ==============================================================================

from __future__ import annotations

from typing import Dict, Any, List, Optional


def _side_from_price(last_close: float, bo: float, bd: float) -> str:
    if bo and last_close > bo:
        return "LONG"
    if bd and last_close < bd:
        return "SHORT"
    return "NONE"


def _relative_location(last_close: float, bo: float, bd: float) -> str:
    if bo and last_close > bo:
        return "ABOVE_BO"
    if bd and last_close < bd:
        return "BELOW_BD"
    return "INSIDE"


def compute_structure_state(
    levels: Dict[str, Any],
    candles_5m_post_lock: List[Dict[str, Any]],
    tuning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns a stable, UI-friendly packet.

    tuning options (optional):
      - acceptance_required (int) default 2
      - acceptance_mode: "CLOSES" (default)
    """
    tuning = tuning or {}

    bo = float(levels.get("breakout_trigger", 0.0) or 0.0)
    bd = float(levels.get("breakdown_trigger", 0.0) or 0.0)

    r30h = float(levels.get("range30m_high", 0.0) or 0.0)
    r30l = float(levels.get("range30m_low", 0.0) or 0.0)

    acceptance_required = int(tuning.get("acceptance_required", 2) or 2)

    if not candles_5m_post_lock:
        return {
            "action": "HOLD FIRE",
            "reason": "No post-lock candles yet.",
            "permission": {"status": "NOT_EARNED", "side": "NONE"},
            "acceptance_progress": {"count": 0, "required": acceptance_required, "side_hint": "NONE"},
            "location": {"relative_to_triggers": "INSIDE"},
            "execution": {
                "pause_state": "NONE",
                "resumption_state": "NONE",
                "gates_mode": "PREVIEW",
                "locked_at": None,
                "levels": {"failure": r30l, "continuation": r30h},
            },
            "diagnostics": {"fail_reason": "WAITING_POST_LOCK"},
        }

    # Evaluate last candle close
    last = candles_5m_post_lock[-1]
    last_close = float(last.get("close", 0.0) or 0.0)
    last_ts = int(last.get("time", 0) or 0)

    side_hint = _side_from_price(last_close, bo, bd)
    location = _relative_location(last_close, bo, bd)

    # Acceptance counting: count consecutive closes beyond trigger
    count = 0
    if side_hint in ("LONG", "SHORT"):
        for c in reversed(candles_5m_post_lock[-(acceptance_required + 6):]):
            close = float(c.get("close", 0.0) or 0.0)
            if side_hint == "LONG" and bo and close > bo:
                count += 1
            elif side_hint == "SHORT" and bd and close < bd:
                count += 1
            else:
                break

    earned = count >= acceptance_required

    # Output action
    if side_hint == "NONE":
        action = "HOLD FIRE"
        reason = "Inside triggers. Waiting for breakout/breakdown acceptance."
    elif not earned:
        action = "WAIT"
        reason = f"{side_hint} pressure, but acceptance not earned ({count}/{acceptance_required})."
    else:
        action = "GO"
        reason = f"{side_hint} acceptance earned ({count}/{acceptance_required})."

    # Failure/continuation reference: for LONG, failure=r30_low, continuation=r30_high (and vice versa)
    failure = r30l if side_hint != "SHORT" else r30h
    continuation = r30h if side_hint != "SHORT" else r30l

    return {
        "action": action,
        "reason": reason,
        "permission": {"status": "EARNED" if earned else "NOT_EARNED", "side": side_hint},
        "acceptance_progress": {"count": count, "required": acceptance_required, "side_hint": side_hint},
        "location": {"relative_to_triggers": location},
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "LIVE",
            "locked_at": last_ts,
            "levels": {"failure": float(failure), "continuation": float(continuation)},
        },
        "diagnostics": {
            "fail_reason": "NONE" if earned or side_hint == "NONE" else "NO_ACCEPTANCE",
            "bo": bo,
            "bd": bd,
            "last_close": last_close,
        },
    }
