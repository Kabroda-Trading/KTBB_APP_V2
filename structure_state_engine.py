# structure_state_engine.py
# ==============================================================================
# STRUCTURE STATE ENGINE (DETERMINISTIC)
# Consumes:
#   - locked levels (from battlebox_pipeline locked truth)
#   - post-lock candles (live 5m candles AFTER the lock time)
# Produces:
#   - stable action/reason/permission package for UI + downstream engines
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional

def placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {
        "action": "HOLD FIRE",
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {"relative_to_triggers": "INSIDE_BAND"},
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "PREVIEW",
            "locked_at": None,
            "levels": {"failure": 0.0, "continuation": 0.0},
        },
    }

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _candle_close(c: Dict[str, Any]) -> float:
    return _safe_float(c.get("close", 0.0))

def _side_from_close(px: float, bo: float, bd: float) -> str:
    if bo and px > bo:
        return "LONG"
    if bd and px < bd:
        return "SHORT"
    return "NONE"

def _location(px: float, bo: float, bd: float) -> str:
    if bo and px > bo:
        return "ABOVE_BO"
    if bd and px < bd:
        return "BELOW_BD"
    return "INSIDE_BAND"

def compute_structure_state(
    levels: Dict[str, Any],
    post_lock_candles: List[Dict[str, Any]],
    tuning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Standard structure logic (clean & stable):
      - Track 2-candle acceptance beyond BO/BD
      - Provide a simple permission state:
          NOT_EARNED -> WATCHING -> EARNED
      - Provide basic failure / continuation rails derived from r30 high/low
    """
    tuning = tuning or {}

    bo = _safe_float(levels.get("breakout_trigger", 0.0))
    bd = _safe_float(levels.get("breakdown_trigger", 0.0))
    r30_high = _safe_float(levels.get("range30m_high", 0.0))
    r30_low = _safe_float(levels.get("range30m_low", 0.0))

    if bo <= 0 or bd <= 0 or not post_lock_candles:
        return placeholder_state("Waiting for locked levels / post-lock candles...")

    closes = [_candle_close(c) for c in post_lock_candles if c]
    if not closes:
        return placeholder_state("No usable post-lock closes yet.")

    last_px = closes[-1]
    loc = _location(last_px, bo, bd)

    # Acceptance logic: count last N closes beyond trigger
    # Required defaults to 2, but you can tune it in research
    required = int(tuning.get("acceptance_required", 2) or 2)
    required = max(1, min(required, 5))

    # Determine current side based on last close
    side_hint = _side_from_close(last_px, bo, bd)

    # Count consecutive closes in that direction
    count = 0
    if side_hint == "LONG":
        for px in reversed(closes):
            if px > bo:
                count += 1
            else:
                break
    elif side_hint == "SHORT":
        for px in reversed(closes):
            if px < bd:
                count += 1
            else:
                break

    # Permission
    if side_hint == "NONE":
        perm = {"status": "NOT_EARNED", "side": "NONE"}
        action = "HOLD FIRE"
        reason = "Inside triggers — no acceptance"
    else:
        if count >= required:
            perm = {"status": "EARNED", "side": side_hint}
            action = f"GO {side_hint}"
            reason = f"Acceptance confirmed ({count}/{required} closes)"
        else:
            perm = {"status": "WATCHING", "side": side_hint}
            action = f"WATCH {side_hint}"
            reason = f"Acceptance building ({count}/{required} closes)"

    # Rails (simple + consistent)
    # - If long: failure = r30_low, continuation = r30_high
    # - If short: failure = r30_high, continuation = r30_low
    if side_hint == "LONG":
        failure = r30_low
        continuation = r30_high
    elif side_hint == "SHORT":
        failure = r30_high
        continuation = r30_low
    else:
        failure = 0.0
        continuation = 0.0

    return {
        "action": action,
        "reason": reason,
        "permission": perm,
        "acceptance_progress": {"count": count, "required": required, "side_hint": side_hint},
        "location": {"relative_to_triggers": loc},
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "LIVE",
            "locked_at": None,
            "levels": {"failure": failure, "continuation": continuation},
        },
    }
