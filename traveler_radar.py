# traveler_radar.py
# ==============================================================================
# Public, Traveler-native radar data source -- Step 1 of the V2 Crown
# retirement + radar rebuild (Andy's ruling, Kabroda AI Brain repo
# AGENT_LOG.md 2026-09-23; site CLAUDE.md "Strategic Direction" section;
# V2_RETIREMENT_MAP.md).
#
# Deliberately a standalone module, not added to market_radar.py -- that
# file is V2-only (its _build_dossier() calls decision_engine.py directly)
# and is on Step 3's deletion list; this module has zero import of
# decision_engine.py, market_radar.py, or any other V2-only code, so
# deleting market_radar.py later needs no surgery here.
#
# The one behavior change this module embodies, unavoidably: V2's old
# /api/radar/snapshot showed a speculative directional plan (entry/stop/
# T1/T2/T3) for a "favored" side BEFORE any cross happened. The Traveler
# is symmetrical by design (gate_traveler.py) -- TravelerPlan.direction/
# stop_price/t1_price are genuinely NULL until a confirmed 5m close
# actually breaks BO or BD. This module reports that honestly (`plan` is
# None, or has direction/stop/t1 as None, until the real data exists)
# rather than fabricating a pre-cross guess. There is also no T2/T3 --
# MGMT_E1_STACK is a single full-exit design (mgmt_e1_stack.py).
# ==============================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

import session_manager
from database import SessionLock, TravelerPlan, ExecutorOrder

SYMBOL_NORM = "BTC/USDT"
SYMBOL_RAW = "BTCUSDT"
SESSION_ID = "us_ny_futures"


def _current_session_date_key() -> str:
    return session_manager.resolve_current_session(datetime.now(timezone.utc), "AUTO")["date_key"]


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _mgmt_fields(db: Session, plan_id: int, is_rearm: bool = False) -> Dict[str, Any]:
    """Same LIVE-preferred order-selection main.py's own
    /api/admin/traveler-plan-status route uses -- see that route's
    docstring for the full 'why' (ExecutorOrder's real unique constraint
    is (traveler_plan_id, account_id, is_rearm), so multiple accounts can
    each hold their own order against one journey; a blind
    order_by(id.desc()) could silently flip between accounts).

    is_rearm (2026-10-06, R1 re-arm): REAL bug fix, not just an extension
    -- before this parameter existed, this function's own order_by(id.
    desc()) would silently start returning the RE-ARM order's state (its
    id is always higher) the moment one existed, making the PRIMARY
    order's own final state vanish from this surface. Now explicitly
    scoped -- the caller gets each leg's own state by calling this twice
    (see get_public_traveler_snapshot() below), never a blind "whichever
    is latest" pick across both legs."""
    key_prefix = "rearm_mgmt" if is_rearm else "mgmt"
    order = (
        db.query(ExecutorOrder)
        .filter(ExecutorOrder.traveler_plan_id == plan_id, ExecutorOrder.mode == "LIVE", ExecutorOrder.is_rearm == is_rearm)
        .order_by(ExecutorOrder.id.desc()).first()
        or db.query(ExecutorOrder)
        .filter(ExecutorOrder.traveler_plan_id == plan_id, ExecutorOrder.mode == "DRY_RUN", ExecutorOrder.is_rearm == is_rearm)
        .order_by(ExecutorOrder.id.desc()).first()
    )
    if order is None:
        return {
            f"{key_prefix}_mode": None, f"{key_prefix}_state": None,
            f"{key_prefix}_entry_fill_price": None, f"{key_prefix}_entry_fill_time": None,
            f"{key_prefix}_exit_reason": None, f"{key_prefix}_exit_price": None, f"{key_prefix}_exit_time": None,
            f"{key_prefix}_realized_pnl_r": None, f"{key_prefix}_exit_approximated": False,
        }
    return {
        f"{key_prefix}_mode": order.mode,
        f"{key_prefix}_state": order.management_state,
        f"{key_prefix}_entry_fill_price": order.entry_fill_price,
        f"{key_prefix}_entry_fill_time": _iso(order.entry_fill_time),
        f"{key_prefix}_exit_reason": order.exit_reason,
        f"{key_prefix}_exit_price": order.exit_price,
        f"{key_prefix}_exit_time": _iso(order.exit_time),
        f"{key_prefix}_realized_pnl_r": order.realized_pnl_r,
        # Computed here, not stored -- exit_reason alone fully and
        # permanently determines this (STOP/T1 are real fills on both
        # lineages; C5_EXIT/BBWP_EXIT/TIME are only ever approximated on
        # LIVE -- see executor_live_e1_engine.py::_finalize_traveler_close()'s
        # own approximated parameter, the authoritative source this mirrors).
        f"{key_prefix}_exit_approximated": (
            order.mode == "LIVE" and order.exit_reason in ("C5_EXIT", "BBWP_EXIT", "TIME")
        ),
    }


def get_public_traveler_snapshot(db: Session) -> Dict[str, Any]:
    """The full public-radar payload: today's locked levels + today's
    TravelerPlan state + (once filled) its management/exit detail.
    Queried by (symbol, session_id, date_key) -- the same write-side key
    kabroda_mas_flow.py's _inject_traveler_plan_to_database() uses -- not
    a blind "most recent row" shortcut (that only happens to work today
    because this site is single-symbol, market_radar.py's own
    TARGETS = ["BTCUSDT"]).

    `price` is the lock-time anchor price only (same "price_as_of: lock"
    honesty convention the old /api/radar/snapshot documented, main.py's
    own comment on that route) -- not a fresh exchange call. The existing
    public /api/live-price endpoint (main.py) already covers live ticking
    with its own single-candle fetch; this route doesn't duplicate that."""
    date_key = _current_session_date_key()

    lock = db.query(SessionLock).filter(
        SessionLock.symbol == SYMBOL_NORM,
        SessionLock.session_id == SESSION_ID,
        SessionLock.date_key == date_key,
    ).first()

    levels: Dict[str, Any] = {}
    lock_time_utc = None
    price = None
    if lock is not None:
        try:
            pkt = json.loads(lock.packet_data)
            levels = pkt.get("levels", {}) or {}
        except Exception:
            levels = {}
        price = levels.get("anchor_price")
        lock_time_utc = datetime.fromtimestamp(lock.lock_time, tz=timezone.utc).isoformat()

    plan_row = db.query(TravelerPlan).filter(
        TravelerPlan.symbol == SYMBOL_NORM,
        TravelerPlan.session_id == SESSION_ID,
        TravelerPlan.date_key == date_key,
    ).first()

    out: Dict[str, Any] = {
        "ok": True,
        "symbol": SYMBOL_RAW,
        "locked": lock is not None,
        "lock_time_utc": lock_time_utc,
        "price": price,
        "price_as_of": "lock",
        "live_price_endpoint": "/api/live-price",
        "levels": {
            "breakout_trigger": levels.get("breakout_trigger"),
            "breakdown_trigger": levels.get("breakdown_trigger"),
            "range30m_high": levels.get("range30m_high"),
            "range30m_low": levels.get("range30m_low"),
            # daily_resistance/daily_support: same shared SessionLock levels
            # blob (sse_engine.py), not Traveler- or V2-specific -- kept here
            # so the radar frontend's existing "COPY TRIGGERS" button (6
            # chart levels: bo/bd/daily S+R/range30m H+L) still has all 6.
            "daily_resistance": levels.get("daily_resistance"),
            "daily_support": levels.get("daily_support"),
        },
        "plan": None,
    }

    if plan_row is not None:
        out["plan"] = {
            "id": plan_row.id,
            "status": plan_row.status,
            "direction": plan_row.direction,
            "stop_price": plan_row.stop_price,
            "t1_price": plan_row.t1_price,
            "cross_time": _iso(plan_row.cross_time),
            "cross_price": plan_row.cross_price,
            "tercile_skipped": plan_row.tercile_skipped,
            "fill_time": _iso(plan_row.fill_time),
            "fill_price": plan_row.fill_price,
            "journey_cap_at": _iso(plan_row.journey_cap_at),
            "last_transition_reason": plan_row.last_transition_reason,
            **_mgmt_fields(db, plan_row.id, is_rearm=False),
            # R1 re-arm (2026-10-06) -- a SEPARATE field set, never
            # conflated with the primary's own fields above (see
            # database.py's TravelerPlan.rearm_status comment for why).
            # rearm_status is None for every journey that never re-armed --
            # the frontend's own job to decide whether to render this
            # section at all, same as the primary's direction/stop_price/
            # t1_price already being None pre-cross.
            "rearm_status": plan_row.rearm_status,
            "rearm_cross_time": _iso(plan_row.rearm_cross_time),
            "rearm_cross_price": plan_row.rearm_cross_price,
            "rearm_tercile_skipped": plan_row.rearm_tercile_skipped,
            "rearm_fill_time": _iso(plan_row.rearm_fill_time),
            "rearm_fill_price": plan_row.rearm_fill_price,
            "rearm_entry_expires_at": _iso(plan_row.rearm_entry_expires_at),
            "rearm_last_transition_reason": plan_row.rearm_last_transition_reason,
            **_mgmt_fields(db, plan_row.id, is_rearm=True),
        }

    return out
