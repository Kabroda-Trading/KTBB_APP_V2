# ==============================================================================
# SESSION MONITOR — v1 (observe-and-log only)
# Kabroda intraday state transition tracker.
#
# Polls five discrete indicator states every 15 minutes during the active
# session window (lock_time → 4:00 PM ET). Logs one row per poll to
# monitor_event_log. Transitions are the primary data product.
#
# Governing principle: "These things are happening, this is the outcome."
# The monitor observes and records. It does not anticipate or assume.
# Every behavioral estimate from Phase A research is a HYPOTHESIS stored
# in the event log — not a hard-coded decision rule.
#
# Hard wall: writes ONLY to monitor_event_log and monitor_config.
# No FK to session_locks or campaign_logs. No UPDATE of any live column.
# Every DB write is wrapped in try/except — a failed row never stops the loop.
#
# Notification machinery: BUILT but DISABLED in v1.
# Three gates must ALL simultaneously clear before notifications can fire:
#   Gate A: 30+ resolved-session transition events (evidence threshold)
#   Gate B: human harness review confirms signal plausibility
#   Gate C: explicit human notification_enabled flag in monitor_config
# The monitor cannot enable itself. All three gates require human action.
# ==============================================================================

import asyncio
import json
import datetime
from datetime import timezone
from typing import Any, Dict, List, Optional

import pytz

from battlebox_pipeline import (
    fetch_live_15m,
    fetch_live_1h,
    fetch_live_4h,
    _build_synthetic_jewel,
    _calculate_harmonic_matrix,
    _calc_adx,
)
from database import SessionLocal, SessionAuditLog, SessionLock, MonitorEventLog, MonitorConfig

_SYMBOL = "BTC/USDT"
_NY_TZ = pytz.timezone("America/New_York")
_POLL_INTERVAL_SEC = 900  # 15 minutes — 28 polls per session window

# Discrete state variables tracked for transition detection.
# Categorical changes are the meaningful event unit.
# Numeric values (rsi, ribbon_spread_pct) are stored in state_snapshot_json
# for context but are NOT compared as transitions.
_DISCRETE_STATES = frozenset({
    "kinematic_grade",   # PRIMED / OVEREXTENDED / TANGLED
    "micro_state",       # SWEET_ZONE / SWEET_ZONE_BEAR / PULLBACK / HOSTILE_CEILING / EXHAUSTION / CHOP
    "1h_fuel_status",    # STRONG / OVEREXTENDED / REFUELING / CHOP_RISK
    "4h_adx_strength",   # STRONG / WEAK
    "1h_adx_strength",   # STRONG / WEAK
})


# ==============================================================================
# STATE FETCH — ONE CANONICAL COMPUTATION PER POLL
# ==============================================================================

async def _fetch_monitor_states() -> Dict[str, Any]:
    """
    Fetches candles and computes the full state snapshot for this poll.
    Uses the same battlebox_pipeline functions as the MAS flow — exact same
    computation, ensuring consistency between session lock states and poll states.

    Returns a flat dict of state values plus price and numeric context.
    Returns a safe fallback dict on any fetch or compute failure.
    """
    try:
        raw_15m, raw_1h, raw_4h = await asyncio.gather(
            fetch_live_15m(_SYMBOL, limit=300),
            fetch_live_1h(_SYMBOL, limit=300),
            fetch_live_4h(_SYMBOL, limit=280),
        )

        if not raw_15m or not raw_1h or not raw_4h:
            return _empty_states("FETCH_EMPTY")

        adx_4h = _calc_adx(raw_4h)
        adx_1h = _calc_adx(raw_1h)

        jewel = _build_synthetic_jewel(raw_15m, adx_4h=adx_4h)
        harmonic = _calculate_harmonic_matrix(raw_1h, raw_4h)

        price = float(raw_15m[-1]["close"])

        return {
            "price": price,
            # Discrete states (compared for transitions)
            "kinematic_grade": jewel.get("kinematic_grade", "TANGLED"),
            "micro_state": harmonic.get("micro_state", "CHOP"),
            "1h_fuel_status": harmonic.get("1h_fuel_status", "UNKNOWN"),
            "4h_adx_strength": "STRONG" if adx_4h.get("adx", 0.0) >= 25.0 else "WEAK",
            "1h_adx_strength": "STRONG" if adx_1h.get("adx", 0.0) >= 25.0 else "WEAK",
            # Numeric context (snapshot only — NOT compared as transitions)
            "kinematic_rsi": jewel.get("rsi", 50.0),
            "ribbon_spread_pct": jewel.get("ribbon_spread_pct", 0.0),
            "deviation_from_mean_pct": jewel.get("deviation_from_mean_pct", 0.0),
            "4h_adx_value": round(adx_4h.get("adx", 0.0), 2),
            "1h_adx_value": round(adx_1h.get("adx", 0.0), 2),
            "error": None,
        }
    except Exception as e:
        return _empty_states(str(e))


def _empty_states(reason: str) -> Dict[str, Any]:
    return {
        "price": 0.0,
        "kinematic_grade": "TANGLED",
        "micro_state": "CHOP",
        "1h_fuel_status": "UNKNOWN",
        "4h_adx_strength": "WEAK",
        "1h_adx_strength": "WEAK",
        "kinematic_rsi": 50.0,
        "ribbon_spread_pct": 0.0,
        "deviation_from_mean_pct": 0.0,
        "4h_adx_value": 0.0,
        "1h_adx_value": 0.0,
        "error": reason,
    }


# ==============================================================================
# TRANSITION DETECTION
# ==============================================================================

def _detect_transitions(prior: Dict, current: Dict) -> List[Dict]:
    """
    Returns only the discrete state variables that changed between polls.
    First poll of the day: prior is empty, so no transitions are reported
    (there is no reference point to compare against).
    """
    transitions = []
    for var in _DISCRETE_STATES:
        prior_val = prior.get(var)
        current_val = current.get(var)
        if prior_val is not None and current_val is not None and current_val != prior_val:
            transitions.append({
                "variable": var,
                "prior_state": prior_val,
                "new_state": current_val,
            })
    return transitions


# ==============================================================================
# CONDITION MANAGEMENT
# ==============================================================================

def _re_derive_conditions(audit_record) -> Dict[str, Any]:
    """
    Re-applies blocking condition logic to the frozen inputs stored in
    session_audit_log. Called once at session start so the monitor knows
    which conditions were blocking at lock time.

    V1 note: CONDITION 1's full definition uses micro_state (HOSTILE_CEILING
    or CHOP). micro_state_lock is now stored for new sessions but may be NULL
    for older rows. Until back-filled, energy_status == CHOP_RISK is the proxy.

    CONDITION 3 (choked target) never clears intraday — the box is frozen.
    If it is the sole active condition, _check_conditions_clear always returns False.
    """
    if audit_record is None:
        return {"cond_1": False, "cond_2": False, "cond_3": False, "any_active": False}

    approval = audit_record.approval_status or "UNKNOWN"
    if approval not in ("STAND_DOWN", "REJECTED"):
        return {"cond_1": False, "cond_2": False, "cond_3": False, "any_active": False}

    energy = audit_record.energy_status or "UNKNOWN"
    kg = audit_record.kinematic_grade or "PRIMED"
    micro_lock = getattr(audit_record, "micro_state_lock", None) or ""
    bo = audit_record.bo_trigger or 0.0
    t1 = audit_record.t1 or 0.0

    # CONDITION 1: Structural chop — 1H tide and wave in conflict.
    # Full: micro_state in (HOSTILE_CEILING, CHOP) AND 1h_fuel_status == CHOP_RISK.
    # V1 proxy when micro_state_lock absent: energy_status == CHOP_RISK.
    if micro_lock:
        cond_1 = (
            energy == "CHOP_RISK"
            and micro_lock in ("HOSTILE_CEILING", "CHOP")
        )
    else:
        cond_1 = (energy == "CHOP_RISK")

    # CONDITION 2: Multi-TF exhaustion — both energy and kinematic signal stretched.
    # Full: 2+ of (4H ADX weak, 1h_fuel OVEREXTENDED, kinematic_grade OVEREXTENDED).
    # V1 proxy: energy OVEREXTENDED AND kinematic_grade OVEREXTENDED.
    cond_2 = (energy == "OVEREXTENDED" and kg == "OVEREXTENDED")

    # CONDITION 3: Choked target — T1 structurally too close to entry at lock time.
    if bo > 0 and t1 > 0:
        t1_dist_pct = abs(t1 - bo) / bo * 100.0
        cond_3 = (t1_dist_pct < 0.35)
    else:
        cond_3 = False

    any_active = cond_1 or cond_2 or cond_3
    return {
        "cond_1": cond_1,
        "cond_2": cond_2,
        "cond_3": cond_3,
        "any_active": any_active,
    }


def _check_conditions_clear(current_states: Dict, conditions_active: Dict) -> bool:
    """
    Returns True when ALL blocking conditions that were active at lock time
    are simultaneously clear in the current poll.

    CONDITION 3 (choked target) is structural — it never clears intraday.
    """
    if not conditions_active.get("any_active", False):
        return False

    cond_1_active = conditions_active.get("cond_1", False)
    cond_2_active = conditions_active.get("cond_2", False)
    cond_3_active = conditions_active.get("cond_3", False)

    # CONDITION 1 clears when energy is no longer CHOP_RISK AND
    # micro_state is no longer in the conflicting zone.
    cond_1_clear = True
    if cond_1_active:
        fuel = current_states.get("1h_fuel_status", "UNKNOWN")
        micro = current_states.get("micro_state", "CHOP")
        cond_1_clear = (
            fuel not in ("CHOP_RISK",)
            and micro not in ("HOSTILE_CEILING", "CHOP")
        )

    # CONDITION 2 clears when kinematic_grade is PRIMED AND fuel has rebuilt.
    cond_2_clear = True
    if cond_2_active:
        kg = current_states.get("kinematic_grade", "TANGLED")
        fuel = current_states.get("1h_fuel_status", "UNKNOWN")
        cond_2_clear = (kg == "PRIMED" and fuel in ("STRONG", "REFUELING"))

    # CONDITION 3 never clears intraday.
    cond_3_clear = not cond_3_active

    all_checks = []
    if cond_1_active:
        all_checks.append(cond_1_clear)
    if cond_2_active:
        all_checks.append(cond_2_clear)
    if cond_3_active:
        all_checks.append(cond_3_clear)

    return all(all_checks) if all_checks else False


# ==============================================================================
# SESSION WINDOW HELPERS
# ==============================================================================

def _get_ny_close_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """Returns today's 4:00 PM ET as a UTC-aware datetime."""
    ny_now = now_utc.astimezone(_NY_TZ)
    ny_close = ny_now.replace(hour=16, minute=0, second=0, microsecond=0)
    return ny_close.astimezone(timezone.utc)


def _get_session_lock(date_key: str) -> Optional[object]:
    """Returns today's SessionLock row for us_ny_futures, or None."""
    try:
        db = SessionLocal()
        try:
            return (
                db.query(SessionLock)
                .filter(
                    SessionLock.session_id == "us_ny_futures",
                    SessionLock.date_key == date_key,
                )
                .first()
            )
        finally:
            db.close()
    except Exception:
        return None


def _get_audit_record(date_key: str) -> Optional[object]:
    """Returns today's SessionAuditLog row, or None."""
    try:
        db = SessionLocal()
        try:
            return (
                db.query(SessionAuditLog)
                .filter(
                    SessionAuditLog.symbol == _SYMBOL,
                    SessionAuditLog.session_id == "us_ny_futures",
                    SessionAuditLog.date_key == date_key,
                )
                .first()
            )
        finally:
            db.close()
    except Exception:
        return None


# ==============================================================================
# DB WRITE
# ==============================================================================

def _write_monitor_event(
    *,
    session_date: str,
    session_id: str,
    poll_sequence: int,
    poll_timestamp: datetime.datetime,
    btc_price: float,
    pct_from_bo: Optional[float],
    pct_from_bd: Optional[float],
    mas_verdict: str,
    state_snapshot: Dict,
    transitions: List[Dict],
    conditions_active: Dict,
    stand_down_conds_all_clear: bool,
    consecutive_clears: int,
) -> None:
    """
    Writes one monitor_event_log row. Non-blocking — failure is logged
    but never propagated to the caller. The loop continues regardless.
    """
    try:
        db = SessionLocal()
        try:
            row = MonitorEventLog(
                symbol=_SYMBOL,
                session_date=session_date,
                session_id=session_id,
                poll_sequence=poll_sequence,
                poll_timestamp=poll_timestamp,
                btc_price=btc_price if btc_price else None,
                pct_from_bo=round(pct_from_bo, 4) if pct_from_bo is not None else None,
                pct_from_bd=round(pct_from_bd, 4) if pct_from_bd is not None else None,
                mas_verdict=mas_verdict,
                state_snapshot_json=json.dumps(state_snapshot, default=str),
                transitions_json=json.dumps(transitions),
                any_transition=len(transitions) > 0,
                transition_count=len(transitions),
                conditions_active_json=json.dumps(conditions_active),
                stand_down_conds_all_clear=stand_down_conds_all_clear,
                consecutive_clears=consecutive_clears,
                notification_sent=False,
            )
            db.add(row)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[MONITOR] DB write error: {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"[MONITOR] DB session error: {e}")


# ==============================================================================
# NOTIFICATION MACHINERY — BUILT, DISABLED
# ==============================================================================

def _gate_a_resolved_event_count() -> int:
    """
    Counts monitor_event_log rows where the corresponding session's
    session_audit_log row has a non-null outcome_type.
    This is the evidentiary denominator for Gate A.
    """
    try:
        db = SessionLocal()
        try:
            from sqlalchemy import text as _sqla_text
            result = db.execute(_sqla_text(
                "SELECT COUNT(*) FROM monitor_event_log mel "
                "WHERE EXISTS ("
                "  SELECT 1 FROM session_audit_log sal "
                "  WHERE sal.session_id = mel.session_id "
                "  AND sal.date_key = mel.session_date "
                "  AND sal.symbol = mel.symbol "
                "  AND sal.outcome_type IS NOT NULL"
                ")"
            )).scalar()
            return int(result or 0)
        finally:
            db.close()
    except Exception:
        return 0


def _is_notification_enabled() -> bool:
    """
    Returns True only if all three gates simultaneously clear:
      Gate A: resolved event count >= gate_a_min_events
      Gate B: harness review completed
      Gate C: explicit notification_enabled flag set by a human
    The monitor cannot enable itself — this function is read-only.
    """
    try:
        db = SessionLocal()
        try:
            cfg = db.query(MonitorConfig).filter_by(config_key="btc_session_monitor").first()
            if cfg is None:
                return False
            return (
                cfg.notification_enabled
                and cfg.gate_b_harness_reviewed
                and _gate_a_resolved_event_count() >= cfg.gate_a_min_events
            )
        finally:
            db.close()
    except Exception:
        return False


def _send_notification_stub(
    *,
    context: Dict,
    session_date: str,
    poll_sequence: int,
    consecutive_clears: int,
) -> None:
    """
    DISABLED — built for future activation only.

    When all three gates clear and a human enables notifications, this
    function will dispatch:
      - Primary: browser push notification (Web Push API)
      - Fallback: email to ADMIN_EMAIL

    Context packet delivered:
      - time_since_lock_min: minutes elapsed since session lock
      - current_price, pct_from_bo, pct_from_bd
      - micro_state, kinematic_grade, 1h_fuel_status at time of notification
      - consecutive_clears: number of clean polls confirming the signal
      - transitions_log: all transitions seen this session with timestamps

    Cooldown: one notification per session maximum (controlled by
    _notification_sent_today flag in the main loop). last_notification_sent_at
    is written to monitor_config after a successful send.

    TODO: implement push + email dispatch here once Gate B + Gate C clear.
    """
    print(
        f"[MONITOR] NOTIFICATION STUB (disabled) — "
        f"session {session_date}, poll #{poll_sequence}, "
        f"{consecutive_clears} consecutive clear polls. "
        f"price={context.get('price', 0):,.0f}, "
        f"micro_state={context.get('micro_state')}, "
        f"kinematic_grade={context.get('kinematic_grade')}, "
        f"pct_from_bo={context.get('pct_from_bo')}. "
        f"Enable via monitor_config.notification_enabled to activate."
    )


# ==============================================================================
# MAIN LOOP
# ==============================================================================

async def run_session_monitor_loop() -> None:
    """
    Background task registered in main.py lifespan().
    Polls every 15 minutes. Active only during the session window.
    All per-session state resets at midnight (new date_key).
    """
    _prior_states: Dict[str, str] = {}
    _current_session_date: Optional[str] = None
    _conditions_active: Dict = {"cond_1": False, "cond_2": False, "cond_3": False, "any_active": False}
    _mas_verdict: Optional[str] = None
    _session_bo: Optional[float] = None
    _session_bd: Optional[float] = None
    _poll_sequence: int = 0
    _consecutive_clears: int = 0
    _notification_sent_today: bool = False

    print("[MONITOR] Session monitor loop started (v1 — observe-and-log only).")

    while True:
        try:
            now_utc = datetime.datetime.now(timezone.utc)
            today_key = now_utc.strftime("%Y-%m-%d")

            # New day — reset all per-session state
            if today_key != _current_session_date:
                _prior_states = {}
                _current_session_date = today_key
                _conditions_active = {"cond_1": False, "cond_2": False, "cond_3": False, "any_active": False}
                _mas_verdict = None
                _session_bo = None
                _session_bd = None
                _poll_sequence = 0
                _consecutive_clears = 0
                _notification_sent_today = False

            # Check if there is a session lock for today
            session_lock = _get_session_lock(today_key)
            if session_lock is None:
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                continue

            lock_dt = datetime.datetime.fromtimestamp(session_lock.lock_time, tz=timezone.utc)
            ny_close_utc = _get_ny_close_utc(now_utc)

            # Only poll inside the active window
            if not (lock_dt <= now_utc <= ny_close_utc):
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                continue

            # On first poll inside the window: load session context from audit record.
            # The audit record may not exist yet if the MAS hasn't fired (first minutes
            # after lock). In that case, fall back to packet_data for bo/bd.
            if _mas_verdict is None:
                audit_record = _get_audit_record(today_key)
                if audit_record:
                    _mas_verdict = audit_record.approval_status or "UNKNOWN"
                    _session_bo = audit_record.bo_trigger
                    _session_bd = audit_record.bd_trigger
                    _conditions_active = _re_derive_conditions(audit_record)
                else:
                    _mas_verdict = "PENDING"
                    try:
                        pkt = json.loads(session_lock.packet_data)
                        lvls = pkt.get("levels", {})
                        _session_bo = lvls.get("breakout_trigger")
                        _session_bd = lvls.get("breakdown_trigger")
                    except Exception:
                        _session_bo = None
                        _session_bd = None
                    _conditions_active = {"cond_1": False, "cond_2": False, "cond_3": False, "any_active": False}

            # If we polled as PENDING but audit record has since appeared, update context.
            if _mas_verdict == "PENDING":
                audit_record = _get_audit_record(today_key)
                if audit_record:
                    _mas_verdict = audit_record.approval_status or "UNKNOWN"
                    _session_bo = audit_record.bo_trigger
                    _session_bd = audit_record.bd_trigger
                    _conditions_active = _re_derive_conditions(audit_record)

            # Fetch current indicator states
            current_states = await _fetch_monitor_states()
            price = current_states.get("price", 0.0)

            # Separate discrete states for transition comparison
            discrete_current = {k: current_states[k] for k in _DISCRETE_STATES if k in current_states}

            # Detect transitions vs previous poll
            transitions = _detect_transitions(_prior_states, discrete_current)

            # Check if all blocking conditions have cleared
            all_clear = _check_conditions_clear(discrete_current, _conditions_active)
            _consecutive_clears = _consecutive_clears + 1 if all_clear else 0

            # Price relative to the frozen session box
            pct_from_bo = (
                ((price - _session_bo) / _session_bo * 100.0)
                if _session_bo and price
                else None
            )
            pct_from_bd = (
                ((price - _session_bd) / _session_bd * 100.0)
                if _session_bd and price
                else None
            )

            # Write the event row (non-blocking)
            _poll_sequence += 1
            _write_monitor_event(
                session_date=today_key,
                session_id="us_ny_futures",
                poll_sequence=_poll_sequence,
                poll_timestamp=now_utc,
                btc_price=price,
                pct_from_bo=pct_from_bo,
                pct_from_bd=pct_from_bd,
                mas_verdict=_mas_verdict,
                state_snapshot=current_states,
                transitions=transitions,
                conditions_active=_conditions_active,
                stand_down_conds_all_clear=all_clear,
                consecutive_clears=_consecutive_clears,
            )

            # Notification check — disabled until all three gates clear
            if (
                _conditions_active.get("any_active", False)
                and all_clear
                and _consecutive_clears >= 2
                and not _notification_sent_today
                and _is_notification_enabled()
            ):
                _send_notification_stub(
                    context={**current_states, "pct_from_bo": pct_from_bo, "pct_from_bd": pct_from_bd},
                    session_date=today_key,
                    poll_sequence=_poll_sequence,
                    consecutive_clears=_consecutive_clears,
                )
                _notification_sent_today = True

            # Advance state
            _prior_states = discrete_current

            if transitions:
                print(
                    f"[MONITOR] Poll #{_poll_sequence} | {len(transitions)} transition(s) | "
                    f"price=${price:,.0f} | "
                    f"micro_state={current_states.get('micro_state')} | "
                    f"kg={current_states.get('kinematic_grade')} | "
                    f"fuel={current_states.get('1h_fuel_status')}"
                )

        except Exception as e:
            print(f"[MONITOR] Unhandled loop error: {e}")

        await asyncio.sleep(_POLL_INTERVAL_SEC)
