# signal_flagging_engine.py
# ---------------------------------------------------------
# PHASE 2: Signal Flagging Engine
# Flags underperforming signals for human review.
# FLAG_ONLY mode: creates alerts, does NOT auto-adjust weights.
# After 2+ weeks of data collection, we'll decide on auto-adjustment.
# ---------------------------------------------------------

import json
import datetime
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

from database import (
    SessionLocal,
    SystemAlertLog,
    SignalAccuracyLog,
    SignalWeight,
)
from accuracy_decay_tracker import get_decayed_signals, get_improving_signals, run_health_snapshot
from config import load_config


def _load_config() -> dict:
    """Load Phase 2 config."""
    try:
        return load_config()
    except Exception:
        return {
            "action_mode": "FLAG_ONLY",
            "min_samples": 100,
            "accuracy_threshold_warn": 0.30,
            "accuracy_threshold_critical": 0.15,
        }


def _create_alert(
    db,
    alert_type: str,
    source: str,
    source_value: str,
    message: str,
    severity: str,
    accuracy_pct: float = None,
    sample_count: int = None,
    trend_direction: str = None,
) -> SystemAlertLog:
    """Create a new alert entry."""
    alert = SystemAlertLog(
        alert_type=alert_type,
        source=source,
        source_value=source_value,
        message=message,
        severity=severity,
        accuracy_pct=accuracy_pct,
        sample_count=sample_count,
        trend_direction=trend_direction,
        experiment_path="FLAG_ONLY",
    )
    db.add(alert)
    return alert


def _get_active_alerts_for_signal(db, signal_name: str, signal_value: str = None) -> List[SystemAlertLog]:
    """Check if there's already an unresolved alert for this signal."""
    query = db.query(SystemAlertLog).filter(
        SystemAlertLog.source == signal_name,
        SystemAlertLog.resolved_at.is_(None),
    )
    if signal_value:
        query = query.filter(SystemAlertLog.source_value == signal_value)
    return query.all()


def run_flagging_tick() -> dict:
    """
    Main entry point. Runs every 6 hours.
    
    1. Takes a health snapshot of all signals
    2. Flags underperformers (below threshold with enough samples)
    3. Creates alerts for human review — no auto-adjustment
    
    Returns a dict with counts of alerts created.
    """
    config = _load_config()
    min_samples = config.get("min_samples", 100)
    warn_threshold = config.get("accuracy_threshold_warn", 0.30)
    critical_threshold = config.get("accuracy_threshold_critical", 0.15)
    
    db = SessionLocal()
    try:
        # Step 1: Take health snapshot
        snapshot_count = run_health_snapshot(days=30)
        
        # Step 2: Find underperformers
        decayed = get_decayed_signals(
            threshold=warn_threshold,
            min_samples=min_samples,
            days=30,
        )
        
        alerts_created = 0
        
        # Step 3: Process underperformers — flag for human review only
        for signal in decayed:
            signal_name = signal["signal_name"]
            signal_value = signal.get("signal_value")
            accuracy_pct = signal.get("accuracy_pct", 0)
            sample_count = signal.get("sample_count", 0)
            trend_direction = signal.get("trend_direction", "STABLE")
            
            # Check if already flagged — if so, update the existing alert
            # rather than creating a new one, so the dashboard always shows
            # the latest accuracy data for flagged signals.
            existing = _get_active_alerts_for_signal(db, signal_name, signal_value)
            if existing:
                # Update the most recent alert with fresh data
                latest = existing[0]
                latest.accuracy_pct = accuracy_pct
                latest.sample_count = sample_count
                latest.trend_direction = trend_direction
                latest.message = (
                    f"Signal '{signal_name}' (value: {signal_value or 'any'}) "
                    f"at {accuracy_pct}% accuracy over {sample_count} samples "
                    f"— trend: {trend_direction}"
                )
                # Re-evaluate severity
                if accuracy_pct < critical_threshold * 100:
                    latest.severity = "CRITICAL"
                    latest.alert_type = "SIGNAL_QUARANTINED"
                else:
                    latest.severity = "WARN"
                    latest.alert_type = "SIGNAL_DEGRADED"
                continue
            
            # Determine severity
            if accuracy_pct < critical_threshold * 100:
                severity = "CRITICAL"
                alert_type = "SIGNAL_QUARANTINED"
            else:
                severity = "WARN"
                alert_type = "SIGNAL_DEGRADED"
            
            message = (
                f"Signal '{signal_name}' (value: {signal_value or 'any'}) "
                f"at {accuracy_pct}% accuracy over {sample_count} samples "
                f"— trend: {trend_direction}"
            )
            
            # Create alert — no weight adjustment in FLAG_ONLY mode
            _create_alert(
                db,
                alert_type=alert_type,
                source=signal_name,
                source_value=signal_value,
                message=message,
                severity=severity,
                accuracy_pct=accuracy_pct,
                sample_count=sample_count,
                trend_direction=trend_direction,
            )
            alerts_created += 1
        
        if alerts_created > 0:
            db.commit()
        
        print(
            f"[FLAGGING ENGINE] Tick complete: "
            f"{alerts_created} new alerts, "
            f"{snapshot_count} health records"
        )
        
        return {
            "status": "OK",
            "alerts_created": alerts_created,
            "health_records": snapshot_count,
            "decayed_count": len(decayed),
            "action_mode": "FLAG_ONLY",
        }
    except Exception as e:
        print(f"[FLAGGING ENGINE] Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return {"status": "ERROR", "reason": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ALERT QUERIES — for the dashboard
# ---------------------------------------------------------------------------

def get_active_alerts() -> List[Dict]:
    """Get all unresolved alerts."""
    db = SessionLocal()
    try:
        rows = db.query(SystemAlertLog).filter(
            SystemAlertLog.resolved_at.is_(None),
        ).order_by(SystemAlertLog.created_at.desc()).all()
        
        return [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "alert_type": r.alert_type,
                "source": r.source,
                "source_value": r.source_value,
                "message": r.message,
                "severity": r.severity,
                "accuracy_pct": r.accuracy_pct,
                "sample_count": r.sample_count,
                "trend_direction": r.trend_direction,
                "experiment_path": r.experiment_path,
            }
            for r in rows
        ]
    finally:
        db.close()


def resolve_alert(alert_id: int, resolved_by: str = "admin") -> bool:
    """Mark an alert as resolved."""
    db = SessionLocal()
    try:
        alert = db.query(SystemAlertLog).filter(SystemAlertLog.id == alert_id).first()
        if not alert:
            return False
        
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = resolved_by
        db.commit()
        return True
    except Exception as e:
        print(f"[FLAGGING ENGINE] Resolve error: {e}")
        db.rollback()
        return False
    finally:
        db.close()
