# signal_accuracy_tracker.py
# ---------------------------------------------------------
# PHASE 1: Signal Accuracy Tracker
# Runs every 4 hours. For every signal the system generates,
# records what it predicted and what actually happened.
# First run backfills the last 7 days of data.
# ---------------------------------------------------------

import json
import datetime
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session

from database import (
    SessionLocal,
    SignalAccuracyLog,
    JewelSnapshotLog,
    DecisionJournal,
    CampaignLog,
    SessionAuditLog,
)
import battlebox_pipeline


# ---------------------------------------------------------------------------
# OUTCOME DETERMINATION
# ---------------------------------------------------------------------------

def _determine_outcome(prediction_price: float, current_price: float, predicted_direction: str) -> dict:
    """
    Given a prediction price and current price, determine:
    - outcome_direction: UP / DOWN / SIDEWAYS (based on actual price move, independent of prediction)
    - outcome_pct_move: % change
    - outcome_correct: True/False/None (None if direction is NEUTRAL or move is SIDEWAYS)
    
    Thresholds:
    - > 0.5% up = UP
    - > 0.5% down = DOWN
    - < 0.5% either way = SIDEWAYS
    """
    if not prediction_price or prediction_price <= 0:
        return {"direction": "UNKNOWN", "pct_move": 0.0, "correct": None}
    
    pct_move = ((current_price - prediction_price) / prediction_price) * 100.0
    
    # Determine actual direction from price move alone
    if abs(pct_move) < 0.5:
        actual_direction = "SIDEWAYS"
    elif pct_move > 0:
        actual_direction = "UP"
    else:
        actual_direction = "DOWN"
    
    # Determine correctness based on predicted direction vs actual move
    if actual_direction == "SIDEWAYS":
        correct = None
    elif predicted_direction in ("BULLISH", "LONG"):
        correct = actual_direction == "UP"
    elif predicted_direction in ("BEARISH", "SHORT"):
        correct = actual_direction == "DOWN"
    else:
        correct = None  # NEUTRAL prediction — no correctness to judge
    
    return {"direction": actual_direction, "pct_move": round(pct_move, 4), "correct": correct}


def _get_current_price() -> float:
    """Fetch current BTC price from the last 15M candle close.
    
    NOTE: This function uses asyncio.run() internally and should only be called
    from contexts where no event loop is running (e.g., from asyncio.to_thread).
    The scheduler in main.py passes the price directly to avoid thread-safety
    issues with the shared ccxt client.
    """
    try:
        import asyncio
        candles = asyncio.run(battlebox_pipeline.fetch_live_15m("BTCUSDT", limit=2))
        return float(candles[-1]["close"]) if candles else 0.0
    except Exception as e:
        print(f"[SIGNAL TRACKER] Price fetch failed: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# SIGNAL CAPTURE — scan each source table for new signals
# ---------------------------------------------------------------------------

def _capture_jewel_signals(db: Session, since: datetime, current_price: float) -> int:
    """Capture JEWEL gate signals from jewel_snapshot_log."""
    rows = db.query(JewelSnapshotLog).filter(
        JewelSnapshotLog.timestamp >= since,
    ).all()
    
    # Check which ones we've already captured — use (signal_name, source_id) tuples
    existing = set()
    for rec in db.query(SignalAccuracyLog).filter(
        SignalAccuracyLog.signal_name.in_(["jewel_gate", "jewel_exit_warning"]),
        SignalAccuracyLog.timestamp >= since,
    ).all():
        if rec.source_id:
            existing.add((rec.signal_name, rec.source_id))
    
    count = 0
    for row in rows:
        # JEWEL Gate signal
        if row.jewel_gate_open is not None and ("jewel_gate", row.id) not in existing:
            gate_value = "OPEN" if row.jewel_gate_open else "CLOSED"
            direction = row.dominant_direction or "NEUTRAL"
            strength = row.jewel_conviction or "MODERATE"
            
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.timestamp,
                signal_name="jewel_gate",
                signal_value=gate_value,
                signal_strength=strength,
                prediction_direction=direction,
                prediction_price=row.asset_price,
                lookahead_hours=4,
                source_table="jewel_snapshot_log",
                source_id=row.id,
            ))
            count += 1
        
        # JEWEL Exit Warning signal
        if row.jewel_exit_warning is not None and ("jewel_exit_warning", row.id) not in existing:
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.timestamp,
                signal_name="jewel_exit_warning",
                signal_value="ACTIVE" if row.jewel_exit_warning else "INACTIVE",
                signal_strength=row.jewel_conviction or "MODERATE",
                prediction_direction="BEARISH",
                prediction_price=row.asset_price,
                lookahead_hours=4,
                source_table="jewel_snapshot_log",
                source_id=row.id,
            ))
            count += 1
    
    if count > 0:
        db.commit()
        print(f"[SIGNAL TRACKER] Captured {count} JEWEL signals")
    
    return count


def _capture_decision_signals(db: Session, since: datetime, current_price: float) -> int:
    """Capture energy grade, kinematic grade, and confluence signals from decision_journal."""
    rows = db.query(DecisionJournal).filter(
        DecisionJournal.timestamp >= since,
    ).all()
    
    existing = set()
    for rec in db.query(SignalAccuracyLog).filter(
        SignalAccuracyLog.signal_name.in_(["energy_grade", "kinematic_grade", "confluence_score"]),
        SignalAccuracyLog.timestamp >= since,
    ).all():
        if rec.source_id:
            existing.add((rec.signal_name, rec.source_id))
    
    count = 0
    for row in rows:
        direction = row.confluence_direction or "NEUTRAL"
        
        # Energy Grade
        if row.energy_status and (("energy_grade", row.id) not in existing):
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.timestamp,
                signal_name="energy_grade",
                signal_value=row.energy_status,
                signal_strength=None,
                prediction_direction=direction,
                prediction_price=row.asset_price,
                lookahead_hours=4,
                source_table="decision_journal",
                source_id=row.id,
            ))
            count += 1
        
        # Kinematic Grade
        if row.kinematic_grade and (("kinematic_grade", row.id) not in existing):
            # PRIMED predicts continuation, OVEREXTENDED predicts reversal
            if row.kinematic_grade == "PRIMED":
                pred_dir = direction
            elif row.kinematic_grade == "OVEREXTENDED":
                pred_dir = "BEARISH" if direction == "BULLISH" else "BULLISH" if direction == "BEARISH" else "NEUTRAL"
            else:
                pred_dir = "NEUTRAL"
            
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.timestamp,
                signal_name="kinematic_grade",
                signal_value=row.kinematic_grade,
                signal_strength=None,
                prediction_direction=pred_dir,
                prediction_price=row.asset_price,
                lookahead_hours=4,
                source_table="decision_journal",
                source_id=row.id,
            ))
            count += 1
        
        # Confluence Score
        if row.confluence_score is not None and (("confluence_score", row.id) not in existing):
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.timestamp,
                signal_name="confluence_score",
                signal_value=str(row.confluence_score),
                signal_strength=None,
                prediction_direction=direction,
                prediction_price=row.asset_price,
                lookahead_hours=4,
                source_table="decision_journal",
                source_id=row.id,
            ))
            count += 1
    
    if count > 0:
        db.commit()
        print(f"[SIGNAL TRACKER] Captured {count} Decision Journal signals")
    
    return count


def _capture_campaign_signals(db: Session, since: datetime, current_price: float) -> int:
    """Capture macro bias and energy grade signals from campaign_logs (4H/1H candidates)."""
    rows = db.query(CampaignLog).filter(
        CampaignLog.created_at >= since,
        CampaignLog.is_canonical == True,
    ).all()
    
    existing = set()
    for rec in db.query(SignalAccuracyLog).filter(
        SignalAccuracyLog.signal_name.in_(["macro_bias", "campaign_energy_grade"]),
        SignalAccuracyLog.timestamp >= since,
    ).all():
        if rec.source_id:
            existing.add((rec.signal_name, rec.source_id))
    
    count = 0
    for row in rows:
        # Macro Bias
        if row.macro_bias and (("macro_bias", row.id) not in existing):
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.created_at,
                signal_name="macro_bias",
                signal_value=row.macro_bias,
                signal_strength=None,
                prediction_direction=row.macro_bias,
                prediction_price=row.entry_price,
                lookahead_hours=4,
                source_table="campaign_logs",
                source_id=row.id,
            ))
            count += 1
        
        # Energy Grade (4H/1H candidates)
        if row.energy_grade and (("campaign_energy_grade", row.id) not in existing):
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.created_at,
                signal_name="campaign_energy_grade",
                signal_value=row.energy_grade,
                signal_strength=None,
                prediction_direction=row.bias,
                prediction_price=row.entry_price,
                lookahead_hours=4,
                source_table="campaign_logs",
                source_id=row.id,
            ))
            count += 1
    
    if count > 0:
        db.commit()
        print(f"[SIGNAL TRACKER] Captured {count} Campaign signals")
    
    return count


def _capture_session_audit_signals(db: Session, since: datetime, current_price: float) -> int:
    """Capture BBWP/PMARP signals from session_audit_log."""
    rows = db.query(SessionAuditLog).filter(
        SessionAuditLog.created_at >= since,
    ).all()
    
    existing = set()
    for rec in db.query(SignalAccuracyLog).filter(
        SignalAccuracyLog.signal_name.in_(["bbwp_state", "pmarp_state"]),
        SignalAccuracyLog.timestamp >= since,
    ).all():
        if rec.source_id:
            existing.add((rec.signal_name, rec.source_id))
    
    count = 0
    for row in rows:
        direction = row.bias or "NEUTRAL"
        
        # BBWP State — compression predicts breakout in bias direction
        if row.bbwp_state and (("bbwp_state", row.id) not in existing):
            # Compression = predicts breakout, Expansion = predicts reversal
            if "COMPRESSION" in (row.bbwp_state or ""):
                pred_dir = direction
            elif "EXPANSION" in (row.bbwp_state or ""):
                pred_dir = "BEARISH" if direction == "BULLISH" else "BULLISH" if direction == "BEARISH" else "NEUTRAL"
            else:
                pred_dir = "NEUTRAL"
            
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.created_at,
                signal_name="bbwp_state",
                signal_value=row.bbwp_state,
                signal_strength=None,
                prediction_direction=pred_dir,
                prediction_price=row.bo_trigger,
                lookahead_hours=4,
                source_table="session_audit_log",
                source_id=row.id,
            ))
            count += 1
        
        # PMARP State — overextension predicts reversal
        if row.pmarp_state and (("pmarp_state", row.id) not in existing):
            if "OVEREXTENDED" in (row.pmarp_state or ""):
                pred_dir = "BEARISH" if direction == "BULLISH" else "BULLISH" if direction == "BEARISH" else "NEUTRAL"
            elif "DEPRESSED" in (row.pmarp_state or ""):
                pred_dir = direction  # depressed = bounce in bias direction
            else:
                pred_dir = "NEUTRAL"
            
            db.add(SignalAccuracyLog(
                symbol=row.symbol,
                timestamp=row.created_at,
                signal_name="pmarp_state",
                signal_value=row.pmarp_state,
                signal_strength=None,
                prediction_direction=pred_dir,
                prediction_price=row.bo_trigger,
                lookahead_hours=4,
                source_table="session_audit_log",
                source_id=row.id,
            ))
            count += 1
    
    if count > 0:
        db.commit()
        print(f"[SIGNAL TRACKER] Captured {count} Session Audit signals")
    
    return count


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def run_signal_accuracy_tick(current_price: float = None) -> dict:
    """
    Main entry point. Two-pass architecture:
    
    PASS 1 (CAPTURE): Scans all signal sources for new predictions,
    inserts rows with prediction_* fields set and outcome_* fields NULL.
    
    PASS 2 (CHECK): Queries for rows where outcome_checked_at IS NULL
    and now - timestamp >= lookahead_hours, then fills in outcome fields
    using the current price.
    
    Args:
        current_price: Optional current BTC price. If None, fetches it internally.
                       Pass from the scheduler to avoid thread-safety issues
                       with the shared ccxt client.
    
    Returns a dict with counts of captured and checked signals.
    """
    if current_price is None or current_price <= 0:
        current_price = _get_current_price()
    if current_price <= 0:
        print("[SIGNAL TRACKER] Cannot fetch price — skipping tick")
        return {"status": "ERROR", "reason": "no_price"}
    
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        
        # ── PASS 1: Capture new predictions ──────────────────────────
        last_run = db.query(SignalAccuracyLog).order_by(
            SignalAccuracyLog.created_at.desc()
        ).first()
        
        if last_run:
            since = last_run.created_at - timedelta(hours=1)  # overlap window
        else:
            since = now - timedelta(days=7)
            print(f"[SIGNAL TRACKER] First run — backfilling from {since.date()}")
        
        captured = {
            "jewel": _capture_jewel_signals(db, since, current_price),
            "decision_journal": _capture_decision_signals(db, since, current_price),
            "campaign_logs": _capture_campaign_signals(db, since, current_price),
            "session_audit": _capture_session_audit_signals(db, since, current_price),
        }
        
        # ── PASS 2: Check outcomes for signals that are now due ──────
        # Find rows where outcome hasn't been checked yet and enough time has passed
        due_rows = db.query(SignalAccuracyLog).filter(
            SignalAccuracyLog.outcome_checked_at.is_(None),
            SignalAccuracyLog.timestamp <= now - timedelta(hours=4),
        ).all()
        
        checked = 0
        for row in due_rows:
            outcome = _determine_outcome(
                row.prediction_price or current_price,
                current_price,
                row.prediction_direction or "NEUTRAL",
            )
            row.outcome_direction = outcome["direction"]
            row.outcome_price = current_price
            row.outcome_pct_move = outcome["pct_move"]
            row.outcome_correct = outcome["correct"]
            row.outcome_checked_at = now
            checked += 1
        
        if checked > 0:
            db.commit()
            print(f"[SIGNAL TRACKER] Checked outcomes for {checked} due signals")
        
        total_captured = sum(captured.values())
        print(f"[SIGNAL TRACKER] Tick complete: {total_captured} captured, {checked} checked")
        
        return {
            "status": "OK",
            "captured": captured,
            "total_captured": total_captured,
            "checked": checked,
        }
    except Exception as e:
        print(f"[SIGNAL TRACKER] Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return {"status": "ERROR", "reason": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ACCURACY QUERIES — for the dashboard
# ---------------------------------------------------------------------------

def get_signal_accuracy(signal_name: str = None, days: int = 7) -> List[Dict]:
    """
    Get accuracy stats for signals over the last N days.
    Returns a list of dicts with signal_name, signal_value, total, correct, accuracy.
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = db.query(SignalAccuracyLog).filter(
            SignalAccuracyLog.timestamp >= cutoff,
            SignalAccuracyLog.outcome_correct.isnot(None),
        )
        
        if signal_name:
            query = query.filter(SignalAccuracyLog.signal_name == signal_name)
        
        rows = query.all()
        
        # Group by signal_name + signal_value
        groups: Dict[str, dict] = {}
        for row in rows:
            key = f"{row.signal_name}|{row.signal_value}"
            if key not in groups:
                groups[key] = {
                    "signal_name": row.signal_name,
                    "signal_value": row.signal_value,
                    "total": 0,
                    "correct": 0,
                    "incorrect": 0,
                    "neutral": 0,
                }
            groups[key]["total"] += 1
            if row.outcome_correct is True:
                groups[key]["correct"] += 1
            elif row.outcome_correct is False:
                groups[key]["incorrect"] += 1
            else:
                groups[key]["neutral"] += 1
        
        results = []
        for key, g in groups.items():
            results.append({
                **g,
                "accuracy_pct": round(g["correct"] / g["total"] * 100, 1) if g["total"] > 0 else 0.0,
            })
        
        return sorted(results, key=lambda x: x["accuracy_pct"], reverse=True)
    
    finally:
        db.close()


def get_signal_timeline(signal_name: str, days: int = 7) -> List[Dict]:
    """Get the raw timeline of a specific signal for charting."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = db.query(SignalAccuracyLog).filter(
            SignalAccuracyLog.signal_name == signal_name,
            SignalAccuracyLog.timestamp >= cutoff,
        ).order_by(SignalAccuracyLog.timestamp.asc()).all()
        
        return [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "signal_value": r.signal_value,
                "prediction_direction": r.prediction_direction,
                "outcome_direction": r.outcome_direction,
                "outcome_correct": r.outcome_correct,
                "outcome_pct_move": r.outcome_pct_move,
            }
            for r in rows
        ]
    finally:
        db.close()
