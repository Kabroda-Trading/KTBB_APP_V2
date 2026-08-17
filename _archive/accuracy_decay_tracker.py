# accuracy_decay_tracker.py
# ---------------------------------------------------------
# PHASE 2: Accuracy Decay Tracker
# Tracks per-signal accuracy trends over time.
# Computes: accuracy %, trend slope, health score.
# ---------------------------------------------------------

import json
import datetime
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import (
    SessionLocal,
    SignalAccuracyLog,
    SignalHealthLog,
    SignalWeight,
)


# ---------------------------------------------------------------------------
# TREND COMPUTATION
# ---------------------------------------------------------------------------

def compute_accuracy_trend(signal_name: str, signal_value: str = None, days: int = 30) -> dict:
    """
    Compute accuracy trend for a signal over the lookback period.
    
    Returns:
    - accuracy_pct: overall accuracy %
    - sample_count: total samples
    - trend_slope: linear regression slope (positive = improving, negative = decaying)
    - trend_direction: IMPROVING | DECAYING | STABLE | INSUFFICIENT_DATA
    - health_score: composite 0.0–1.0
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Build query
        query = db.query(SignalAccuracyLog).filter(
            SignalAccuracyLog.signal_name == signal_name,
            SignalAccuracyLog.timestamp >= cutoff,
            SignalAccuracyLog.outcome_correct.isnot(None),
        )
        if signal_value:
            query = query.filter(SignalAccuracyLog.signal_value == signal_value)
        
        rows = query.order_by(SignalAccuracyLog.timestamp.asc()).all()
        
        if not rows:
            return {
                "signal_name": signal_name,
                "signal_value": signal_value,
                "accuracy_pct": None,
                "sample_count": 0,
                "trend_slope": None,
                "trend_direction": "INSUFFICIENT_DATA",
                "health_score": None,
            }
        
        # Overall accuracy
        correct_count = sum(1 for r in rows if r.outcome_correct is True)
        total_count = len(rows)
        accuracy_pct = round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0
        
        # Trend slope: split into first half vs second half
        # If accuracy improved in the second half, trend is positive
        mid = len(rows) // 2
        first_half = rows[:mid]
        second_half = rows[mid:]
        
        first_accuracy = sum(1 for r in first_half if r.outcome_correct is True) / max(len(first_half), 1)
        second_accuracy = sum(1 for r in second_half if r.outcome_correct is True) / max(len(second_half), 1)
        
        trend_slope = round(second_accuracy - first_accuracy, 4)
        
        if total_count < 5:
            trend_direction = "INSUFFICIENT_DATA"
        elif trend_slope > 0.1:
            trend_direction = "IMPROVING"
        elif trend_slope < -0.1:
            trend_direction = "DECAYING"
        else:
            trend_direction = "STABLE"
        
        # Health score: composite of accuracy + trend + sample confidence
        accuracy_score = accuracy_pct / 100.0  # 0.0–1.0
        trend_score = 0.5 + (trend_slope * 2) if trend_slope is not None else 0.5  # centered at 0.5
        trend_score = max(0.0, min(1.0, trend_score))
        sample_confidence = min(1.0, total_count / 50)  # 50+ samples = full confidence
        
        health_score = round(accuracy_score * 0.5 + trend_score * 0.3 + sample_confidence * 0.2, 4)
        
        return {
            "signal_name": signal_name,
            "signal_value": signal_value,
            "accuracy_pct": accuracy_pct,
            "sample_count": total_count,
            "trend_slope": trend_slope,
            "trend_direction": trend_direction,
            "health_score": health_score,
        }
    finally:
        db.close()


def get_all_signal_health(days: int = 30) -> List[Dict]:
    """
    Compute health for all unique signal_name + signal_value combinations.
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Get distinct signal combinations
        combos = db.query(
            SignalAccuracyLog.signal_name,
            SignalAccuracyLog.signal_value,
        ).filter(
            SignalAccuracyLog.timestamp >= cutoff,
        ).distinct().all()
        
        results = []
        for signal_name, signal_value in combos:
            health = compute_accuracy_trend(signal_name, signal_value, days)
            results.append(health)
        
        return sorted(results, key=lambda x: x.get("health_score", 0) or 0)
    finally:
        db.close()


def get_decayed_signals(threshold: float = 0.30, min_samples: int = 10, days: int = 30) -> List[Dict]:
    """
    Return signals that have dropped below the accuracy threshold with enough samples.
    """
    all_health = get_all_signal_health(days=days)
    
    return [
        h for h in all_health
        if h["accuracy_pct"] is not None
        and h["accuracy_pct"] < threshold * 100
        and h["sample_count"] >= min_samples
    ]


def get_improving_signals(threshold: float = 0.70, min_samples: int = 10, days: int = 30) -> List[Dict]:
    """
    Return signals that are performing well above threshold.
    """
    all_health = get_all_signal_health(days=days)
    
    return [
        h for h in all_health
        if h["accuracy_pct"] is not None
        and h["accuracy_pct"] >= threshold * 100
        and h["sample_count"] >= min_samples
    ]


# ---------------------------------------------------------------------------
# HEALTH SNAPSHOT — persist to DB
# ---------------------------------------------------------------------------

def run_health_snapshot(days: int = 30) -> int:
    """
    Compute health for all signals and persist to SignalHealthLog.
    Returns count of health records written.
    """
    db = SessionLocal()
    try:
        all_health = get_all_signal_health(days=days)
        
        count = 0
        for h in all_health:
            db.add(SignalHealthLog(
                signal_name=h["signal_name"],
                signal_value=h.get("signal_value"),
                accuracy_pct=h.get("accuracy_pct"),
                sample_count=h.get("sample_count"),
                trend_slope=h.get("trend_slope"),
                trend_direction=h.get("trend_direction"),
                health_score=h.get("health_score"),
                lookback_days=days,
            ))
            count += 1
        
        if count > 0:
            db.commit()
            print(f"[ACCURACY DECAY] Health snapshot: {count} signals logged")
        
        return count
    except Exception as e:
        print(f"[ACCURACY DECAY] Error: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
