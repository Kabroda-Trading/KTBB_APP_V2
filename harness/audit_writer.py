# harness/audit_writer.py
# =============================================================================
# KABRODA FORWARD-AUDIT LOOP — Audit Record Writer
#
# Two public functions:
#
#   write_decision_record(...)
#     Called from kabroda_mas_flow.py after _inject_brief_to_database().
#     Writes the frozen-at-decision fields to session_audit_log.
#     Never called twice for the same (symbol, date_key, session_id) — the
#     function checks for an existing row and skips if one is found.
#
#   backfill_outcome(...)
#     Called from ledger_closing_engine.py when a trade resolves.
#     Writes the outcome_* fields to the existing session_audit_log row.
#     Only fires if the row exists and outcome_type is still NULL.
#
# ADJUSTMENT 1 (RAG reuse): rag_memory_snapshot receives the already-computed
#   string that _fetch_cro_memory() returned during the MAS run. It is a
#   REUSED REFERENCE — not a re-fetch. A re-fetch after the crew run could
#   produce a different result if a trade closed between the two calls,
#   defeating the capture-at-decision-time principle.
#
# ADJUSTMENT 3 (non-blocking): both functions wrap every DB operation in
#   try/except. On any failure they print an error and return without raising.
#   The calling decision/close path continues normally whether or not the
#   audit write succeeds. A missed audit record is an acceptable loss;
#   a blocked trade is not.
#
# WRITE PATH: session_audit_log ONLY.
# No FK to live config tables. No UPDATE of any live column. Hard wall.
# =============================================================================

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, SessionAuditLog


def _compute_box_pct(bo: Optional[float], bd: Optional[float]) -> Optional[float]:
    if not bo or not bd or bo <= 0:
        return None
    return round((bo - bd) / bo * 100.0, 4)


def _tier_label_for_n(n: int) -> str:
    """Four-tier label based on record count at write time. Updated at N milestones."""
    if n < 30:
        return "DIRECTIONAL_OBSERVATION"
    if n < 50:
        return "PRELIMINARY_SIGNAL"
    if n < 100:
        return "PROVISIONAL_FINDING"
    return "VALIDATED_EDGE"


def _current_audit_n(db) -> int:
    """Count existing session_audit_log rows to determine current tier label."""
    try:
        return db.query(SessionAuditLog).count()
    except Exception:
        return 0


def write_decision_record(
    *,
    symbol: str,
    date_key: str,
    session_id: str,
    # decision outputs
    approval_status: str,
    bias: str,
    entry_price: Optional[float],
    stop_loss: Optional[float],
    t1: Optional[float],
    t2: Optional[float],
    t3: Optional[float],
    # frozen inputs
    bo_trigger: Optional[float],
    bd_trigger: Optional[float],
    energy_status: Optional[str],
    kinematic_grade: Optional[str],
    kde_peaks: Optional[Any],             # list or None — serialized to JSON
    rag_memory_snapshot: Optional[str],   # REUSED reference from _fetch_cro_memory(); see Adj. 1
    agent_chain: Optional[Dict[str, str]], # {"msa":..,"mls":..,"kmq":..,"cro":..,"cco":..}
    model_version: Optional[str],
    # optional links
    campaign_log_id: Optional[int] = None,
    decision_journal_id: Optional[int] = None,
    jewel_snapshot_id: Optional[int] = None,
    jewel_gate_open: Optional[bool] = None,
    jewel_conviction: Optional[str] = None,
    micro_state: Optional[str] = None,
) -> None:
    """
    Write a frozen-at-decision audit record to session_audit_log.
    Idempotent: skips if a row already exists for (symbol, date_key, session_id).
    Non-blocking: any DB error is logged and silently swallowed (Adj. 3).
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(SessionAuditLog)
            .filter(
                SessionAuditLog.symbol == symbol,
                SessionAuditLog.date_key == date_key,
                SessionAuditLog.session_id == session_id,
            )
            .first()
        )
        if existing:
            print(f"|| AUDIT WRITER || Row already exists for {symbol} {date_key} — skipping.")
            return

        n_current = _current_audit_n(db)

        kde_json = None
        if kde_peaks is not None:
            try:
                kde_json = json.dumps(kde_peaks, default=str)
            except Exception:
                kde_json = str(kde_peaks)

        agent_json = None
        if agent_chain is not None:
            try:
                agent_json = json.dumps(agent_chain, default=str)
            except Exception:
                agent_json = str(agent_chain)

        row = SessionAuditLog(
            symbol=symbol,
            date_key=date_key,
            session_id=session_id,
            campaign_log_id=campaign_log_id,
            decision_journal_id=decision_journal_id,
            jewel_snapshot_id=jewel_snapshot_id,
            # frozen decision fields
            decision_timestamp_utc=datetime.now(timezone.utc),
            approval_status=approval_status,
            bias=bias,
            bo_trigger=bo_trigger,
            bd_trigger=bd_trigger,
            box_size_pct=_compute_box_pct(bo_trigger, bd_trigger),
            energy_status=energy_status,
            kinematic_grade=kinematic_grade,
            jewel_gate_open=jewel_gate_open,
            jewel_conviction=jewel_conviction,
            kde_peaks_json=kde_json,
            rag_memory_snapshot=rag_memory_snapshot,
            agent_chain_json=agent_json,
            model_version=model_version,
            entry_price=entry_price,
            stop_loss=stop_loss,
            t1=t1,
            t2=t2,
            t3=t3,
            micro_state_lock=micro_state,
            # label tier at write time
            label_tier=_tier_label_for_n(n_current),
        )
        db.add(row)
        db.commit()
        print(f"|| AUDIT WRITER || Decision record written for {symbol} {date_key} [{approval_status}].")
    except Exception as e:
        print(f"AUDIT WRITER ERROR (write_decision_record): {e}")
    finally:
        db.close()


def backfill_outcome(
    *,
    symbol: str,
    date_key: str,
    session_id: str,
    outcome_type: str,                        # CLOSED_WIN / CLOSED_LOSS / EXPIRED / STAND_DOWN_SAVED / etc.
    outcome_direction_correct: Optional[bool] = None,
    realized_pnl_r: Optional[float] = None,  # PnL in R units; None for stand-downs
    resolution_notes: Optional[str] = None,
) -> None:
    """
    Back-fill outcome fields on an existing session_audit_log row.
    Only writes if: row exists AND outcome_type is still NULL (write-once).
    Non-blocking: any DB error is logged and silently swallowed (Adj. 3).
    """
    db = SessionLocal()
    try:
        row = (
            db.query(SessionAuditLog)
            .filter(
                SessionAuditLog.symbol == symbol,
                SessionAuditLog.date_key == date_key,
                SessionAuditLog.session_id == session_id,
            )
            .first()
        )

        if not row:
            print(f"AUDIT WRITER WARNING (backfill_outcome): No audit row found for {symbol} {date_key}. Skipping.")
            return

        if row.outcome_type is not None:
            print(f"|| AUDIT WRITER || Outcome already set for {symbol} {date_key} — skipping back-fill.")
            return

        row.outcome_type = outcome_type
        row.outcome_direction_correct = outcome_direction_correct
        row.realized_pnl_r = realized_pnl_r
        row.resolution_notes = resolution_notes
        row.outcome_resolved_at_utc = datetime.now(timezone.utc)
        row.outcome_set_at = datetime.now(timezone.utc)

        db.commit()
        print(f"|| AUDIT WRITER || Outcome back-filled for {symbol} {date_key} [{outcome_type}].")
    except Exception as e:
        print(f"AUDIT WRITER ERROR (backfill_outcome): {e}")
    finally:
        db.close()
