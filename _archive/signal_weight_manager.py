# signal_weight_manager.py
# ---------------------------------------------------------
# PHASE 2: Signal Weight Manager
# Manages accuracy-based weights for each signal.
# Currently in FLAG_ONLY mode — weights are informational only.
# Weights will be wired into pipeline confluence scoring in Phase 3,
# after 2+ weeks of data collection.
# ---------------------------------------------------------

import json
import datetime
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

from database import (
    SessionLocal,
    SignalWeight,
    SignalAccuracyLog,
)
from accuracy_decay_tracker import compute_accuracy_trend
from config import load_config


def get_weight(signal_name: str, signal_value: str = None) -> float:
    """
    Get the current accuracy weight for a signal.
    Returns 1.0 (neutral) if no weight is set.
    """
    db = SessionLocal()
    try:
        query = db.query(SignalWeight).filter(
            SignalWeight.signal_name == signal_name,
        )
        if signal_value:
            query = query.filter(SignalWeight.signal_value == signal_value)
        
        entry = query.first()
        return entry.weight if entry else 1.0
    finally:
        db.close()


def get_all_weights() -> List[Dict]:
    """Get all signal weights."""
    db = SessionLocal()
    try:
        rows = db.query(SignalWeight).order_by(
            SignalWeight.signal_name, SignalWeight.signal_value
        ).all()
        
        return [
            {
                "signal_name": r.signal_name,
                "signal_value": r.signal_value,
                "weight": r.weight,
                "experiment_path": r.experiment_path,
                "last_updated": r.last_updated.isoformat() if r.last_updated else None,
                "last_accuracy_pct": r.last_accuracy_pct,
                "last_sample_count": r.last_sample_count,
                "is_quarantined": r.is_quarantined,
                "quarantined_at": r.quarantined_at.isoformat() if r.quarantined_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def quarantine_signal(signal_name: str, signal_value: str = None) -> bool:
    """
    Manually quarantine a signal (set weight to 0.0).
    """
    db = SessionLocal()
    try:
        query = db.query(SignalWeight).filter(
            SignalWeight.signal_name == signal_name,
        )
        if signal_value:
            query = query.filter(SignalWeight.signal_value == signal_value)
        
        entry = query.first()
        if entry:
            entry.weight = 0.0
            entry.is_quarantined = True
            entry.quarantined_at = datetime.now(timezone.utc)
            entry.last_updated = datetime.now(timezone.utc)
        else:
            db.add(SignalWeight(
                signal_name=signal_name,
                signal_value=signal_value,
                weight=0.0,
                is_quarantined=True,
                quarantined_at=datetime.now(timezone.utc),
            ))
        
        db.commit()
        print(f"[WEIGHT MANAGER] Quarantined: {signal_name} ({signal_value or 'any'})")
        return True
    except Exception as e:
        print(f"[WEIGHT MANAGER] Quarantine error: {e}")
        db.rollback()
        return False
    finally:
        db.close()
