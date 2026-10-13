# metasignals_radar.py
# ==============================================================================
# METASIGNALS RADAR -- read-only data access layer for the public snapshot
# API and the admin dashboard. Mirrors alt_matrix_radar.py's own role and
# conventions exactly: pure DB reads, zero decision logic, zero live
# network fetch of its own (the watcher already persists last_mark_price/
# unrealized_pnl onto each order every tick -- see that module's header
# for why this is safe to just read back here).
#
# Public vs admin split, same reasoning as alt_matrix_radar.py's own
# header: the public snapshot is openness-by-precedent (this is a $500
# PAPER account, not real capital or any real user's position -- strictly
# less sensitive than Traveler/Alt Matrix's own real-money public radar
# feeds, which are already public). The admin view adds the full triage
# log (including every VETO_* reason) and the raw paper ledger, which is
# operationally noisy but not actually sensitive -- kept admin-only
# anyway since nothing requires it be public and a quieter public surface
# is simpler to keep honest over time.
# ==============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database import MetaSignalsPaperAccount, MetaSignalsPlan, MetaSignalsPaperOrder, MetaSignalsPaperLedger
import metasignals_paper_engine

_OPEN_STATES = ("PENDING_LIMIT", "OPEN", "HALF_CLOSED_BE")
_CLOSED_STATES = ("CLOSED_STOP", "CLOSED_TP2")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _account_dict(account: MetaSignalsPaperAccount) -> Dict[str, Any]:
    win_rate = (account.winning_trades / account.total_trades) if account.total_trades else None
    return {
        "starting_balance": account.starting_balance,
        "cash_balance": account.cash_balance,
        "equity": account.equity,
        "risk_unit_usd": account.risk_unit_usd,
        "total_trades": account.total_trades,
        "winning_trades": account.winning_trades,
        "losing_trades": account.losing_trades,
        "win_rate": win_rate,
        "total_realized_pnl": account.total_realized_pnl,
        # execution_mode is display-only here -- see metasignals_paper_
        # engine.py's own header for why it is not yet wired to anything.
        "execution_mode": account.execution_mode,
        "linked_account_id": account.linked_account_id,
    }


def _order_dict(o: MetaSignalsPaperOrder) -> Dict[str, Any]:
    return {
        "id": o.id, "plan_id": o.plan_id, "symbol": o.symbol, "side": o.side,
        "entry_mode": o.entry_mode, "entry_price": o.entry_price,
        "hard_stop_initial": o.hard_stop_initial, "current_stop": o.current_stop,
        "tp1_price": o.tp1_price, "tp2_price": o.tp2_price,
        "initial_qty": o.initial_qty, "remaining_qty": o.remaining_qty,
        "last_mark_price": o.last_mark_price, "unrealized_pnl": o.unrealized_pnl,
        "state": o.state, "exit_price": o.exit_price, "realized_pnl": o.realized_pnl, "fees_paid": o.fees_paid,
        "opened_at": _iso(o.opened_at), "filled_at": _iso(o.filled_at), "closed_at": _iso(o.closed_at),
    }


def _plan_dict(p: MetaSignalsPlan, full: bool = False) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "id": p.id, "symbol": p.symbol, "side": p.side, "strategy": p.strategy, "timeframe": p.timeframe,
        "alert_price": p.alert_price, "triage_verdict": p.triage_verdict, "triage_reason": p.triage_reason,
        "status": p.status, "created_at": _iso(p.created_at),
    }
    if full:
        d.update({
            "alert_soft_stop": p.alert_soft_stop, "alert_tp1": p.alert_tp1, "alert_tp2": p.alert_tp2, "alert_tp3": p.alert_tp3,
            "btc_daily_close": p.btc_daily_close, "btc_sma200": p.btc_sma200,
            "asset_daily_close": p.asset_daily_close, "asset_sma200": p.asset_sma200, "asset_ema55": p.asset_ema55,
            "rsi_4h": p.rsi_4h, "live_price_at_alert": p.live_price_at_alert, "extension_r": p.extension_r,
            "funding_rate": p.funding_rate, "atr14_4h": p.atr14_4h,
            "entry_mode": p.entry_mode, "hard_stop": p.hard_stop, "risk_usd": p.risk_usd,
            "position_size": p.position_size, "tp1_price": p.tp1_price, "tp2_price": p.tp2_price,
        })
    return d


def get_public_snapshot(db: Session, recent_limit: int = 20, history_limit: int = 20) -> Dict[str, Any]:
    """Public, no login -- same openness precedent as /api/radar/traveler-
    snapshot and /api/radar/alt-matrix-snapshot (this is a paper account,
    strictly less sensitive than either of those real-money feeds).
    Account stats, every currently-open position, the most recent
    triage verdicts (YES and NO alike), and closed-trade history for
    the dashboard's own Trade History section."""
    account = metasignals_paper_engine.get_or_init_paper_account(db)
    open_orders = db.query(MetaSignalsPaperOrder).filter(
        MetaSignalsPaperOrder.state.in_(_OPEN_STATES),
    ).order_by(MetaSignalsPaperOrder.id.desc()).all()
    closed_orders = db.query(MetaSignalsPaperOrder).filter(
        MetaSignalsPaperOrder.state.in_(_CLOSED_STATES),
    ).order_by(MetaSignalsPaperOrder.closed_at.desc()).limit(history_limit).all()
    recent_plans = db.query(MetaSignalsPlan).order_by(MetaSignalsPlan.id.desc()).limit(recent_limit).all()

    return {
        "ok": True,
        "account": _account_dict(account),
        "positions": [_order_dict(o) for o in open_orders],
        "trade_history": [_order_dict(o) for o in closed_orders],
        "recent_triage": [_plan_dict(p) for p in recent_plans],
    }


def get_admin_status(db: Session, recent_ledger_limit: int = 30) -> Dict[str, Any]:
    """Admin-only (caller checks login/is_admin) -- adds the full
    geometry on every recent plan and the raw ledger rows for real
    audit visibility, same "D3 close detail belongs in the admin
    surface" split main.py's other admin radar routes already use."""
    account = metasignals_paper_engine.get_or_init_paper_account(db)
    all_orders = db.query(MetaSignalsPaperOrder).order_by(MetaSignalsPaperOrder.id.desc()).limit(30).all()
    recent_plans = db.query(MetaSignalsPlan).order_by(MetaSignalsPlan.id.desc()).limit(30).all()
    ledger_rows = db.query(MetaSignalsPaperLedger).order_by(MetaSignalsPaperLedger.id.desc()).limit(recent_ledger_limit).all()

    return {
        "ok": True,
        "account": _account_dict(account),
        "orders": [_order_dict(o) for o in all_orders],
        "recent_triage": [_plan_dict(p, full=True) for p in recent_plans],
        "ledger": [
            {
                "id": r.id, "plan_id": r.plan_id, "order_id": r.order_id,
                "event_type": r.event_type, "amount": r.amount,
                "cash_balance_after": r.cash_balance_after, "equity_after": r.equity_after,
                "notes": r.notes, "created_at": _iso(r.created_at),
            }
            for r in ledger_rows
        ],
    }
