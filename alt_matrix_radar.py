# alt_matrix_radar.py
# ==============================================================================
# ALT MATRIX RADAR -- Step 5, read-only data source for the public and
# admin surfaces. Mirrors traveler_radar.py's own role and conventions
# exactly (same "report what's real, never fabricate a pre-signal guess"
# honesty), but a standalone module with zero import of traveler_radar.py
# or any Traveler table -- the BTC Iron Wall applies to display code
# too, not just the decision/execution path.
#
# Public view (get_public_alt_matrix_snapshot): per symbol, the most
# recent AltMatrixPlan's own state plus a SYMBOL-LEVEL summary of any
# currently open position (direction/entry/stop/MFE) -- no account
# identity, matching how traveler_radar.py's own public route never
# leaks which account is trading. Admin view (get_admin_alt_matrix_
# status): adds the real per-account AltMatrixOrder rows, AltMatrixConfig
# state, and recent AltMatrixTransition entries for audit visibility --
# same "D3 close detail belongs in the admin surface" split main.py's
# own /api/admin/traveler-plan-status route already uses.
# ==============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database import AltMatrixPlan, AltMatrixOrder, AltMatrixConfig, AltMatrixTransition, ExecutorAccount

SYMBOLS = ["SOL/USDT", "ETH/USDT"]
_OPEN_MGMT_STATES = ("FILLED", "TRAILING")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _latest_plan(db: Session, symbol: str) -> Optional[AltMatrixPlan]:
    return (
        db.query(AltMatrixPlan)
        .filter(AltMatrixPlan.symbol == symbol)
        .order_by(AltMatrixPlan.signal_bar_time.desc())
        .first()
    )


def _open_orders_for_symbol(db: Session, symbol: str) -> List[AltMatrixOrder]:
    return (
        db.query(AltMatrixOrder)
        .filter(AltMatrixOrder.symbol == symbol, AltMatrixOrder.management_state.in_(_OPEN_MGMT_STATES))
        .all()
    )


def get_public_alt_matrix_snapshot(db: Session) -> Dict[str, Any]:
    """Public, no login -- same openness precedent as /api/radar/traveler-
    snapshot and /api/gravity/scan. Reports the real, current state
    honestly: a symbol with no plan yet gets `plan: null` (never a
    fabricated pre-signal guess, matching traveler_radar.py's own
    stated honesty rule for TravelerPlan's pre-cross fields). `position`
    is a SYMBOL-LEVEL summary (which account, if any, is irrelevant to a
    public viewer) -- None if nothing is currently open on that symbol,
    regardless of which specific account holds it."""
    out: Dict[str, Any] = {"ok": True, "symbols": {}}
    for symbol in SYMBOLS:
        plan = _latest_plan(db, symbol)
        plan_dict = None
        if plan is not None:
            plan_dict = {
                "id": plan.id,
                "signal_bar_time": _iso(plan.signal_bar_time),
                "date_key": plan.date_key,
                "status": plan.status,
                "status_reason": plan.status_reason,
                "ema21": plan.ema21, "ema55": plan.ema55,
                "atr14": plan.atr14, "daily_close": plan.daily_close, "sma200": plan.sma200,
                "funding_rate": plan.funding_rate,
            }

        open_orders = _open_orders_for_symbol(db, symbol)
        position = None
        if open_orders:
            # More than one open order on the same symbol only happens if
            # more than one account is configured (Andy's Option A ruling
            # means realistically zero or one) -- report the first, most
            # informative one rather than fabricating an aggregate.
            o = open_orders[0]
            position = {
                "direction": o.direction,
                "entry_fill_price": o.entry_fill_price,
                "stop_price": o.sl_price_current,
                "be_amended": o.be_amended,
                "mfe_r": o.mfe_r,
                "management_state": o.management_state,
                "entry_fill_time": _iso(o.entry_fill_time),
            }

        out["symbols"][symbol.replace("/", "")] = {"plan": plan_dict, "position": position}
    return out


def _order_detail(o: AltMatrixOrder) -> Dict[str, Any]:
    return {
        "id": o.id, "alt_matrix_plan_id": o.alt_matrix_plan_id, "account_id": o.account_id,
        "mode": o.mode, "symbol": o.symbol, "direction": o.direction,
        "decision": o.decision, "decision_reason": o.decision_reason,
        "qty": o.qty, "risk_dollars_used": o.risk_dollars_used, "leverage_used": o.leverage_used,
        "entry_fill_price": o.entry_fill_price, "entry_fill_time": _iso(o.entry_fill_time),
        "sl_price_initial": o.sl_price_initial, "sl_price_current": o.sl_price_current,
        "be_amended": o.be_amended, "be_amended_at": _iso(o.be_amended_at), "be_price": o.be_price,
        "mfe_r": o.mfe_r, "mfe_price": o.mfe_price, "mfe_updated_at": _iso(o.mfe_updated_at),
        "management_state": o.management_state,
        "exit_reason": o.exit_reason, "exit_price": o.exit_price, "exit_time": _iso(o.exit_time),
        "realized_pnl_r": o.realized_pnl_r,
    }


def get_admin_alt_matrix_status(db: Session, recent_transitions_limit: int = 20) -> Dict[str, Any]:
    """Admin-only (caller checks login) -- the fuller D1/D2/D3 picture:
    every symbol's latest plan, every non-NOT_ENTERED order (open or
    closed, so a just-closed trade stays visible), per-account config,
    and the most recent transitions across the whole system for audit
    visibility. Same read-only, zero-decision-logic convention as
    traveler_radar.py -- this module never influences what the engine
    does, only reports what it already did."""
    out: Dict[str, Any] = {"ok": True, "symbols": {}, "configs": [], "recent_transitions": []}

    for symbol in SYMBOLS:
        plan = _latest_plan(db, symbol)
        orders = (
            db.query(AltMatrixOrder)
            .filter(AltMatrixOrder.symbol == symbol, AltMatrixOrder.decision.isnot(None))
            .order_by(AltMatrixOrder.id.desc())
            .limit(10)
            .all()
        )
        out["symbols"][symbol.replace("/", "")] = {
            "plan": {
                "id": plan.id, "status": plan.status, "status_reason": plan.status_reason,
                "signal_bar_time": _iso(plan.signal_bar_time),
            } if plan is not None else None,
            "recent_orders": [_order_detail(o) for o in orders],
        }

    for cfg in db.query(AltMatrixConfig).all():
        account = db.query(ExecutorAccount).filter_by(id=cfg.account_id).first()
        out["configs"].append({
            "account_id": cfg.account_id,
            "account_label": account.label if account is not None else None,
            "account_mode": account.mode if account is not None else None,
            "sol_enabled": cfg.sol_enabled, "eth_enabled": cfg.eth_enabled,
            "symbol_priority": cfg.symbol_priority,
            "catchup_window_seconds": cfg.catchup_window_seconds,
        })

    for t in db.query(AltMatrixTransition).order_by(AltMatrixTransition.id.desc()).limit(recent_transitions_limit).all():
        out["recent_transitions"].append({
            "id": t.id, "alt_matrix_plan_id": t.alt_matrix_plan_id, "alt_matrix_order_id": t.alt_matrix_order_id,
            "from_state": t.from_state, "to_state": t.to_state, "price": t.price,
            "realized_pnl_r": t.realized_pnl_r, "created_at": _iso(t.created_at),
        })

    return out
