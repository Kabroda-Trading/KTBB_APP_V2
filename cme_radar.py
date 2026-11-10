# cme_radar.py
# ==============================================================================
# CME RADAR -- read-only data source for the public and admin surfaces.
# Mirrors alt_matrix_radar.py's own role, conventions, and honesty rule
# exactly (report what's real, never fabricate a pre-lock guess) -- a
# standalone module with zero import of traveler_radar.py, alt_matrix_
# radar.py, or any Traveler/Alt Matrix table. Public view is a SYMBOL-LEVEL
# summary (no account identity); admin view adds real per-account CMEOrder
# rows, CMEConfig state, and recent CMETransition entries.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import cme_clock
from database import CMEPlan, CMEOrder, CMEConfig, CMETransition, ExecutorAccount
from cme_signals import REACHABILITY_MAX

SYMBOLS = ["MNQ", "MES"]
_OPEN_MGMT_STATES = ("PENDING_ENTRY", "WAITING_RETEST", "FILLED", "RATCHETED", "ENTRY_FILLED_UNPROTECTED")


def _iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _today_date_key() -> str:
    """Today's date in NY local wall-clock time -- the same date basis the
    CME lock uses (a session runs 9:30-16:00 ET, so "today's lock" is keyed
    on the NY calendar date, not UTC)."""
    return datetime.datetime.now(datetime.timezone.utc).astimezone(cme_clock.NY_TZ).strftime("%Y-%m-%d")


def _latest_plan(db: Session, symbol: str) -> Optional[CMEPlan]:
    # 2026-11-10 (CME_AND_TRAVELER_LIVE_AUDIT_2026_11_10.md Bug 3) -- the
    # prior ORDER BY lock_time DESC LIMIT 1 had no date filter, so when a
    # day's lock had not yet run (or silently failed to write a row), the
    # desk leaked YESTERDAY's plan (including yesterday's Databento 422
    # error string) as if it were today's live state. Filtering to today's
    # date_key means a not-yet-checked day honestly reports `plan: null`
    # ("Awaiting Today's Lock") instead of a stale row.
    return (
        db.query(CMEPlan)
        .filter(CMEPlan.symbol == symbol, CMEPlan.date_key == _today_date_key())
        .order_by(CMEPlan.lock_time.desc())
        .first()
    )


def _open_orders_for_symbol(db: Session, symbol: str) -> List[CMEOrder]:
    return (
        db.query(CMEOrder)
        .filter(CMEOrder.symbol == symbol, CMEOrder.management_state.in_(_OPEN_MGMT_STATES))
        .all()
    )


def get_public_cme_snapshot(db: Session) -> Dict[str, Any]:
    """Public, no login -- same openness precedent as /api/radar/traveler-
    snapshot, /api/radar/alt-matrix-snapshot, and /api/gravity/scan.
    A symbol with no lock yet today gets `plan: null`, never a fabricated
    guess. `position` is a symbol-level summary, no account identity."""
    out: Dict[str, Any] = {"ok": True, "symbols": {}}
    for symbol in SYMBOLS:
        plan = _latest_plan(db, symbol)
        plan_dict = None
        if plan is not None:
            plan_dict = {
                "id": plan.id, "date_key": plan.date_key, "lock_time": _iso(plan.lock_time),
                "status": plan.status, "status_reason": plan.status_reason,
                "box": plan.box, "reachability": plan.reachability,
                "bo": plan.bo, "bd": plan.bd,
                # Desk-page build (2026-11-xx) -- DeepSeek's "Executive CME
                # Dashboard" spec (AGENT_LOG, 2026-11-05 third entry),
                # verified against the real CMEPlan columns and
                # cme_signals.REACHABILITY_MAX before adding: the checklist/
                # verdict UI needs these to show WHY a lock armed or stood
                # down, not just the bare status string.
                "atr14": plan.atr14,
                "reachability_max": REACHABILITY_MAX,
                "box_max_allowed": round(plan.atr14 * REACHABILITY_MAX, 2) if plan.atr14 else None,
                "stop_long": plan.stop_long, "stop_short": plan.stop_short,
                "target_long": plan.target_long, "target_short": plan.target_short,
            }

        open_orders = _open_orders_for_symbol(db, symbol)
        position = None
        if open_orders:
            o = open_orders[0]
            position = {
                "direction": o.direction,
                "entry_fill_price": o.entry_fill_price,
                "stop_price": o.sl_price_current,
                "be_amended": o.be_amended,
                "management_state": o.management_state,
                "entry_fill_time": _iso(o.entry_fill_time),
            }

        out["symbols"][symbol] = {"plan": plan_dict, "position": position}
    return out


def _order_detail(o: CMEOrder) -> Dict[str, Any]:
    return {
        "id": o.id, "cme_plan_id": o.cme_plan_id, "account_id": o.account_id,
        "mode": o.mode, "symbol": o.symbol, "direction": o.direction,
        "contracts": o.contracts, "decision": o.decision, "decision_reason": o.decision_reason,
        "risk_dollars_used": o.risk_dollars_used,
        "entry_fill_price": o.entry_fill_price, "entry_fill_time": _iso(o.entry_fill_time),
        "cross_confirmed_at": _iso(o.cross_confirmed_at),
        "sl_price_initial": o.sl_price_initial, "sl_price_current": o.sl_price_current,
        "be_amended": o.be_amended, "be_amended_at": _iso(o.be_amended_at), "be_price": o.be_price,
        "management_state": o.management_state,
        "exit_reason": o.exit_reason, "exit_price": o.exit_price, "exit_time": _iso(o.exit_time),
        "realized_pnl_r": o.realized_pnl_r, "dollar_pnl": o.dollar_pnl, "receipt_id": o.receipt_id,
    }


def get_admin_cme_status(db: Session, recent_transitions_limit: int = 20) -> Dict[str, Any]:
    """Admin-only (caller checks login) -- the fuller D1/D2/D3 picture,
    same read-only, zero-decision-logic convention as alt_matrix_radar.py."""
    out: Dict[str, Any] = {"ok": True, "symbols": {}, "configs": [], "recent_transitions": []}

    for symbol in SYMBOLS:
        plan = _latest_plan(db, symbol)
        orders = (
            db.query(CMEOrder)
            .filter(CMEOrder.symbol == symbol, CMEOrder.decision.isnot(None))
            .order_by(CMEOrder.id.desc())
            .limit(10)
            .all()
        )
        out["symbols"][symbol] = {
            "plan": {
                "id": plan.id, "status": plan.status, "status_reason": plan.status_reason,
                "lock_time": _iso(plan.lock_time),
            } if plan is not None else None,
            "recent_orders": [_order_detail(o) for o in orders],
        }

    for cfg in db.query(CMEConfig).all():
        account = db.query(ExecutorAccount).filter_by(id=cfg.account_id).first()
        out["configs"].append({
            "account_id": cfg.account_id,
            "account_label": account.label if account is not None else None,
            "account_mode": account.mode if account is not None else None,
            "mnq_enabled": cfg.mnq_enabled, "mes_enabled": cfg.mes_enabled,
            "daily_risk_budget_usd": cfg.daily_risk_budget_usd, "contracts_cap": cfg.contracts_cap,
            "trailing_dd_limit_usd": cfg.trailing_dd_limit_usd, "profit_target_usd": cfg.profit_target_usd,
            "high_water_mark_usd": account.high_water_mark_usd if account is not None else None,
            # 2026-11-05 -- execution_provider/crosstrade_account_id were
            # added to ExecutorAccount the same night CrossTrade was wired
            # in as a working execution path, but never surfaced here --
            # the CME desk page needs to show which path an account is
            # actually using.
            "execution_provider": (account.execution_provider or "tradovate") if account is not None else None,
            "crosstrade_account_id": account.crosstrade_account_id if account is not None else None,
            "tradovate_account_id": account.tradovate_account_id if account is not None else None,
            # Desk-page build (2026-11-xx) -- kill_switch_engaged is already
            # a generic ExecutorAccount column (not Traveler-specific), just
            # never surfaced here before; the CME desk page needs its own
            # pause display independent of Traveler/Alt Matrix's shared one.
            "kill_switch_engaged": account.kill_switch_engaged if account is not None else None,
            "kill_switch_reason": account.kill_switch_reason if account is not None else None,
        })

    for t in db.query(CMETransition).order_by(CMETransition.id.desc()).limit(recent_transitions_limit).all():
        out["recent_transitions"].append({
            "id": t.id, "cme_plan_id": t.cme_plan_id, "cme_order_id": t.cme_order_id,
            "from_state": t.from_state, "to_state": t.to_state, "price": t.price,
            "realized_pnl_r": t.realized_pnl_r, "created_at": _iso(t.created_at),
        })

    return out
