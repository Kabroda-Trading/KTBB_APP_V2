# structure_state_engine.py
# ==============================================================================
# STRUCTURE STATE ENGINE (The Law Layer) - SHIP-READY
# ==============================================================================
# Doctrine:
# 1) Acceptance = 2 consecutive 15m closes outside trigger.
# 2) Revocation = 15m close back inside band OR opposite-trigger violation.
# 3) Alignment  = 5m role-flip evidence near trigger (post-acceptance).
# ==============================================================================

from typing import List, Dict, Any, Optional

# --- HELPERS ---
def _resample_15m(candles_5m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive 15m candles from 5m feed for acceptance/revocation checks."""
    if not candles_5m:
        return []

    resampled: List[Dict[str, Any]] = []
    curr: Optional[Dict[str, Any]] = None
    curr_start: Optional[int] = None

    for c in sorted(candles_5m, key=lambda x: x["time"]):
        ts = int(c["time"])
        start_15 = ts - (ts % 900)

        if curr is None or curr_start != start_15:
            if curr is not None:
                resampled.append(curr)
            curr_start = start_15
            curr = {
                "time": start_15,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
        else:
            curr["high"] = max(curr["high"], float(c["high"]))
            curr["low"] = min(curr["low"], float(c["low"]))
            curr["close"] = float(c["close"])

    if curr is not None:
        resampled.append(curr)

    return resampled

def _trigger_zone(trigger: float, bps: int) -> float:
    return trigger * (bps / 10_000.0)

def _rel_to_triggers(px: float, bo: float, bd: float) -> str:
    if px > bo:
        return "ABOVE"
    if px < bd:
        return "BELOW"
    return "INSIDE"

def _return_state(action: str, reason: str, side: str) -> Dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": side},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {"relative_to_triggers": "INSIDE"},
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "PREVIEW",
            "locked_at": None,
            "levels": {"failure": 0.0, "continuation": 0.0},
        },
    }

# --- CORE STATE MACHINE ---
def compute_structure_state(
    levels: Dict[str, float],
    candles_5m_post_lock: List[Dict[str, Any]],
    trigger_zone_bps: int = 15,
    align_window_minutes: int = 120,
) -> Dict[str, Any]:

    bo = float(levels.get("breakout_trigger", 0.0) or 0.0)
    bd = float(levels.get("breakdown_trigger", 0.0) or 0.0)

    if bo <= 0 or bd <= 0 or not candles_5m_post_lock:
        return _return_state("HOLD FIRE", "Waiting for levels or candles...", "NONE")

    candles_15m = _resample_15m(candles_5m_post_lock)
    if len(candles_15m) < 2:
        px = float(candles_5m_post_lock[-1]["close"])
        return {
            **_return_state("HOLD FIRE", "Building 15m structure...", "NONE"),
            "location": {"relative_to_triggers": _rel_to_triggers(px, bo, bd)},
        }

    # ------------------------------------------------------------------
    # 1) Walk forward on 15m to determine CURRENT acceptance state
    # ------------------------------------------------------------------
    state = "BALANCE"
    side = "NONE"
    acceptance_ts = 0
    cons_long = 0
    cons_short = 0

    for c in candles_15m:
        close = float(c["close"])

        # Opposite-trigger violation (hard flip)
        if state in ("ACCEPT_LONG", "ALIGN_LONG") and close < bd:
            state = "TEST_SHORT"
            side = "SHORT"
            acceptance_ts = 0
            cons_long = 0
            cons_short = 0  # restart from here
        elif state in ("ACCEPT_SHORT", "ALIGN_SHORT") and close > bo:
            state = "TEST_LONG"
            side = "LONG"
            acceptance_ts = 0
            cons_long = 0
            cons_short = 0

        # Band reclaim revocation (revokes acceptance/alignment)
        if state in ("ACCEPT_LONG", "ALIGN_LONG") and close <= bo:
            # if reclaimed inside band, go balance; if below bd, would have flipped above
            state = "BALANCE" if bd <= close <= bo else "TEST_SHORT"
            side = "NONE" if state == "BALANCE" else "SHORT"
            acceptance_ts = 0
            cons_long = 0
            cons_short = 0

        if state in ("ACCEPT_SHORT", "ALIGN_SHORT") and close >= bd:
            state = "BALANCE" if bd <= close <= bo else "TEST_LONG"
            side = "NONE" if state == "BALANCE" else "LONG"
            acceptance_ts = 0
            cons_long = 0
            cons_short = 0

        # Acceptance counting (only counts when outside band)
        if close > bo:
            cons_long += 1
            cons_short = 0
            state = "TEST_LONG"
            side = "LONG"
            if cons_long >= 2:
                state = "ACCEPT_LONG"
                side = "LONG"
                acceptance_ts = int(c["time"])
        elif close < bd:
            cons_short += 1
            cons_long = 0
            state = "TEST_SHORT"
            side = "SHORT"
            if cons_short >= 2:
                state = "ACCEPT_SHORT"
                side = "SHORT"
                acceptance_ts = int(c["time"])
        else:
            cons_long = 0
            cons_short = 0
            if state not in ("ACCEPT_LONG", "ACCEPT_SHORT", "ALIGN_LONG", "ALIGN_SHORT"):
                state = "BALANCE"
                side = "NONE"

    # ------------------------------------------------------------------
    # 2) Alignment on 5m: role-flip evidence near trigger
    # ------------------------------------------------------------------
    gates_mode = "PREVIEW"
    action = "HOLD FIRE"
    reason = "Structure building..."
    pause_state = "NONE"
    fail_level = 0.0
    cont_level = 0.0

    px_now = float(candles_5m_post_lock[-1]["close"])
    rel = _rel_to_triggers(px_now, bo, bd)

    # Acceptance progress for UI (based on last seen counts)
    progress_count = 0
    side_hint = "NONE"
    if state in ("TEST_LONG", "ACCEPT_LONG", "ALIGN_LONG"):
        progress_count = min(cons_long, 2)
        side_hint = "LONG"
    elif state in ("TEST_SHORT", "ACCEPT_SHORT", "ALIGN_SHORT"):
        progress_count = min(cons_short, 2)
        side_hint = "SHORT"

    # Permission status should be earned ONLY after acceptance (not during TEST)
    permission_status = "EARNED" if state in ("ACCEPT_LONG", "ACCEPT_SHORT", "ALIGN_LONG", "ALIGN_SHORT") else "NOT_EARNED"

    if state == "BALANCE":
        return {
            "action": "HOLD FIRE",
            "reason": "Market is balanced. Waiting for a clean test and acceptance.",
            "permission": {"status": "NOT_EARNED", "side": "NONE"},
            "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
            "location": {"relative_to_triggers": rel},
            "execution": {
                "pause_state": "NONE",
                "resumption_state": "NONE",
                "gates_mode": "PREVIEW",
                "locked_at": None,
                "levels": {"failure": 0.0, "continuation": 0.0},
            },
        }

    if state in ("TEST_LONG", "TEST_SHORT"):
        return {
            "action": "HOLD FIRE",
            "reason": "Testing the band. No acceptance yet.",
            "permission": {"status": "NOT_EARNED", "side": side},
            "acceptance_progress": {"count": progress_count, "required": 2, "side_hint": side_hint},
            "location": {"relative_to_triggers": rel},
            "execution": {
                "pause_state": "NONE",
                "resumption_state": "NONE",
                "gates_mode": "PREVIEW",
                "locked_at": None,
                "levels": {"failure": 0.0, "continuation": 0.0},
            },
        }

    # ACCEPT_* : look for alignment
    action = "ACCEPTANCE PROVISIONAL"
    reason = "Acceptance earned on 15m. Waiting for 5m role-flip evidence."
    zone = _trigger_zone(bo if side == "LONG" else bd, trigger_zone_bps)
    align_window_sec = align_window_minutes * 60

    post_accept = [c for c in candles_5m_post_lock if int(c["time"]) > acceptance_ts]
    post_accept = [c for c in post_accept if int(c["time"]) <= acceptance_ts + align_window_sec]

    if post_accept:
        trigger = bo if side == "LONG" else bd

        # Step 1: did we trade into zone?
        in_zone_idx = None
        for i, c in enumerate(post_accept):
            px = float(c["close"])
            if side == "LONG":
                if abs(px - trigger) <= zone or float(c["low"]) <= trigger + zone:
                    in_zone_idx = i
                    break
            else:
                if abs(px - trigger) <= zone or float(c["high"]) >= trigger - zone:
                    in_zone_idx = i
                    break

        # Step 2: after zone touch, did we FAIL to reclaim (defense), then push away?
        if in_zone_idx is not None:
            tail = post_accept[in_zone_idx:]

            # require two closes that do NOT cross back inside in the wrong direction
            def_ok = False
            if len(tail) >= 2:
                c1 = float(tail[0]["close"])
                c2 = float(tail[1]["close"])
                if side == "LONG":
                    def_ok = (c1 >= trigger) and (c2 >= trigger)
                else:
                    def_ok = (c1 <= trigger) and (c2 <= trigger)

            if def_ok:
                # push away: any subsequent close beyond the zone boundary in the trend direction
                pushed = False
                push_idx = None
                for j in range(2, len(tail)):
                    px = float(tail[j]["close"])
                    if side == "LONG" and px >= (trigger + zone):
                        pushed = True
                        push_idx = j
                        break
                    if side == "SHORT" and px <= (trigger - zone):
                        pushed = True
                        push_idx = j
                        break

                if pushed:
                    state = "ALIGN_LONG" if side == "LONG" else "ALIGN_SHORT"
                    gates_mode = "LOCKED"
                    action = "ALIGNMENT EARNED"
                    reason = "Role-flip confirmed on 5m. Gates locked."
                    pause_state = "CONFIRMED"

                    # Levels: failure = worst excursion against direction after acceptance, continuation = best excursion with direction
                    # Use post-accept range from zone-touch onward
                    window = tail[: max(push_idx + 1, 3)]
                    highs = [float(x["high"]) for x in window]
                    lows = [float(x["low"]) for x in window]
                    if side == "LONG":
                        fail_level = min(lows)   # below here = failed defense
                        cont_level = max(highs)  # above here = continuation strength
                    else:
                        fail_level = max(highs)
                        cont_level = min(lows)

    # If not aligned, keep preview
    if gates_mode != "LOCKED":
        action = "FORMING STRUCTURE"
        reason = "Acceptance is valid, but 5m has not shown role-flip evidence yet."
        pause_state = "FORMING"
        # Provide reasonable preview levels: failure at trigger, continuation as current extreme
        if side == "LONG":
            fail_level = bo
            cont_level = max(float(c["high"]) for c in post_accept) if post_accept else px_now
        else:
            fail_level = bd
            cont_level = min(float(c["low"]) for c in post_accept) if post_accept else px_now

    return {
        "action": action,
        "reason": reason,
        "permission": {"status": permission_status if state.startswith("ACCEPT") or state.startswith("ALIGN") else "NOT_EARNED", "side": side},
        "acceptance_progress": {"count": 2, "required": 2, "side_hint": side},
        "location": {"relative_to_triggers": rel},
        "execution": {
            "pause_state": pause_state,
            "resumption_state": "NONE",
            "gates_mode": gates_mode,
            "locked_at": acceptance_ts if gates_mode == "LOCKED" else None,
            "levels": {"failure": float(fail_level), "continuation": float(cont_level)},
        },
    }