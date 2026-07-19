# harness/unified_audit_writer.py
# =============================================================================
# UNIFIED AUDIT SYSTEM — Phase 1 dual-write.
#
# Writes decision_log + decision_gauge_reading. Full design history in
# UNIFIED_AUDIT_SYSTEM_PLAN.md (v1.0-v1.6). ADDITIVE ONLY: this runs
# alongside session_audit_log (harness/audit_writer.py) and campaign_logs
# (gravity_engine.py's CampaignLog writes), which remain the source of
# truth through Phase 2. Nothing here gates or modifies any live decision.
#
# Non-blocking (same Adj. 3 discipline as harness/audit_writer.py): any DB
# failure here is logged and swallowed. A missed audit row is an acceptable
# loss; a blocked trade or stand-down decision is not.
# =============================================================================

import os
import sys
from datetime import datetime
from typing import Any, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, DecisionLog, DecisionGaugeReading


def backfill_decision_outcome(*, campaign_log_id: Optional[int], outcome_status: str, realized_r: Optional[float]) -> None:
    """Back-fill outcome_status/realized_r on the decision_log row matching
    a resolved CampaignLog row, via the campaign_log_id soft FK set at write
    time. Write-once: skips if outcome_status is already set (matches
    harness/audit_writer.backfill_outcome()'s own write-once discipline).
    Non-blocking: any failure is logged and swallowed -- called from
    ledger_closing_engine.py's close loop, which must never be blocked by
    an audit-table failure. No-op if campaign_log_id is None (row wasn't
    linked, e.g. written before Phase 1 shipped)."""
    if campaign_log_id is None:
        return
    db = SessionLocal()
    try:
        row = (
            db.query(DecisionLog)
            .filter(DecisionLog.campaign_log_id == campaign_log_id)
            .order_by(DecisionLog.id.desc())
            .first()
        )
        if not row:
            return
        if row.outcome_status is not None:
            return
        row.outcome_status = outcome_status
        row.realized_r = realized_r
        db.commit()
    except Exception as e:
        print(f"[UNIFIED AUDIT] decision_log outcome backfill failed (campaign_log_id={campaign_log_id}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()

GaugeTuple = Tuple[str, str, Optional[float], Optional[str]]


def gauge(timeframe: str, name: str, value: Any) -> Optional[GaugeTuple]:
    """Build a (timeframe, gauge_name, value_numeric, value_label) tuple from
    a raw source value, classifying booleans/numbers/labels automatically.
    Returns None for a None value (nothing gets written for an absent gauge —
    no silent zero/empty-string placeholder)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return (timeframe, name, 1.0 if value else 0.0, "TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        return (timeframe, name, float(value), None)
    return (timeframe, name, None, str(value))


def _pct_distance(a: Optional[float], b: Optional[float], base: Optional[float]) -> Optional[float]:
    """abs(a - b) / abs(base) * 100, or None if any input is missing/zero-base."""
    if a is None or b is None or base is None or base == 0:
        return None
    return round(abs(a - b) / abs(base) * 100.0, 4)


def write_decision_log(
    *,
    symbol: str,
    decision_timeframe: str,          # "15M" / "1H" / "4H"
    decision_type: str,               # "TRADE" / "STAND_DOWN"
    date_key: str,
    decided_at: datetime,
    session_id: Optional[str] = None,
    bias: Optional[str] = None,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    t1: Optional[float] = None,
    t2: Optional[float] = None,
    t3: Optional[float] = None,
    atr_pct_at_decision: Optional[float] = None,
    candle_window_start: Optional[datetime] = None,
    candle_window_end: Optional[datetime] = None,
    stand_down_reason: Optional[str] = None,
    campaign_log_id: Optional[int] = None,
    session_audit_log_id: Optional[int] = None,
    gauge_readings: Optional[List[GaugeTuple]] = None,
) -> None:
    """Write one decision_log row plus its associated decision_gauge_reading
    rows. gauge_readings is a list of (timeframe, gauge_name, value_numeric,
    value_label) tuples — build them with gauge() above, which drops None
    values automatically. Non-blocking: any failure is logged and swallowed.
    """
    db = SessionLocal()
    try:
        row = DecisionLog(
            symbol=symbol,
            decision_timeframe=decision_timeframe,
            decision_type=decision_type,
            session_id=session_id,
            date_key=date_key,
            decided_at=decided_at,
            bias=bias,
            entry_price=entry_price,
            stop_loss=stop_loss,
            t1=t1,
            t2=t2,
            t3=t3,
            stop_distance_pct=_pct_distance(entry_price, stop_loss, entry_price),
            target_distance_pct=_pct_distance(t1, entry_price, entry_price),
            atr_pct_at_decision=atr_pct_at_decision,
            candle_window_start=candle_window_start,
            candle_window_end=candle_window_end,
            stand_down_reason=stand_down_reason,
            campaign_log_id=campaign_log_id,
            session_audit_log_id=session_audit_log_id,
        )
        db.add(row)
        db.flush()  # populate row.id without committing yet

        for tf, gauge_name, value_numeric, value_label in (gauge_readings or []):
            db.add(
                DecisionGaugeReading(
                    decision_id=row.id,
                    timeframe=tf,
                    gauge_name=gauge_name,
                    value_numeric=value_numeric,
                    value_label=value_label,
                )
            )

        db.commit()
        print(
            f"|| UNIFIED AUDIT || decision_log #{row.id}: {symbol} {decision_timeframe} "
            f"{decision_type} ({date_key}){' — ' + stand_down_reason if stand_down_reason else ''}"
        )
    except Exception as e:
        print(f"[UNIFIED AUDIT WRITER ERROR] {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
