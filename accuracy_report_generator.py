# accuracy_report_generator.py
# ---------------------------------------------------------
# PHASE 2: Accuracy Report Generator
# Auto-generated weekly summaries of signal accuracy trends.
# FLAG_ONLY mode: tracks which signals are underperforming.
# After 2+ weeks of data, we'll decide on auto-adjustment.
# ---------------------------------------------------------

import json
import datetime
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

from database import (
    SessionLocal,
    AccuracyReport,
    SignalAccuracyLog,
    SignalWeight,
    SystemAlertLog,
)
from accuracy_decay_tracker import get_all_signal_health, get_decayed_signals, get_improving_signals
from config import load_config


def generate_weekly_report() -> dict:
    """
    Generate a weekly accuracy report.
    
    Sections:
    - Top 5 most accurate signals
    - Bottom 5 least accurate signals
    - Trending up (improving)
    - Trending down (decaying)
    - New signals with insufficient data
    - Flagged signals summary
    - Summary
    
    Returns the report dict and persists to DB.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        
        # Get all signal health
        all_health = get_all_signal_health(days=30)
        
        # Filter to signals with sufficient data
        with_data = [h for h in all_health if h["sample_count"] >= 5]
        insufficient = [h for h in all_health if h["sample_count"] < 5]
        
        # Sort by accuracy
        sorted_by_accuracy = sorted(
            with_data,
            key=lambda x: x.get("accuracy_pct", 0) or 0,
            reverse=True,
        )
        
        top_5 = sorted_by_accuracy[:5]
        bottom_5 = sorted_by_accuracy[-5:] if len(sorted_by_accuracy) >= 5 else sorted_by_accuracy
        
        # Trending
        improving = [h for h in with_data if h.get("trend_direction") == "IMPROVING"]
        decaying = [h for h in with_data if h.get("trend_direction") == "DECAYING"]
        
        # Flagged signals summary
        flagged_summary = _get_flagged_signals_summary(db)
        
        # Overall stats
        total_signals = len(with_data)
        avg_accuracy = round(
            sum(h.get("accuracy_pct", 0) or 0 for h in with_data) / max(total_signals, 1),
            1,
        )
        
        # Build report
        report = {
            "generated_at": now.isoformat(),
            "report_period": "Last 30 days",
            "overall": {
                "total_signals_with_data": total_signals,
                "signals_with_insufficient_data": len(insufficient),
                "average_accuracy": avg_accuracy,
                "improving_count": len(improving),
                "decaying_count": len(decaying),
            },
            "top_5": [
                {
                    "signal_name": s["signal_name"],
                    "signal_value": s.get("signal_value"),
                    "accuracy_pct": s.get("accuracy_pct"),
                    "sample_count": s.get("sample_count"),
                    "trend_direction": s.get("trend_direction"),
                }
                for s in top_5
            ],
            "bottom_5": [
                {
                    "signal_name": s["signal_name"],
                    "signal_value": s.get("signal_value"),
                    "accuracy_pct": s.get("accuracy_pct"),
                    "sample_count": s.get("sample_count"),
                    "trend_direction": s.get("trend_direction"),
                }
                for s in bottom_5
            ],
            "improving": [
                {
                    "signal_name": s["signal_name"],
                    "signal_value": s.get("signal_value"),
                    "accuracy_pct": s.get("accuracy_pct"),
                    "sample_count": s.get("sample_count"),
                }
                for s in sorted(improving, key=lambda x: x.get("trend_slope", 0) or 0, reverse=True)[:10]
            ],
            "decaying": [
                {
                    "signal_name": s["signal_name"],
                    "signal_value": s.get("signal_value"),
                    "accuracy_pct": s.get("accuracy_pct"),
                    "sample_count": s.get("sample_count"),
                }
                for s in sorted(decaying, key=lambda x: x.get("trend_slope", 0) or 0)[:10]
            ],
            "flagged_signals": flagged_summary,
        }
        
        # Summary text
        summary_parts = []
        if total_signals > 0:
            summary_parts.append(f"{total_signals} signals tracked, average accuracy {avg_accuracy}%")
        if improving:
            summary_parts.append(f"{len(improving)} signals improving")
        if decaying:
            summary_parts.append(f"{len(decaying)} signals decaying")
        if flagged_summary:
            summary_parts.append(f"{flagged_summary.get('total_flagged', 0)} signals flagged for review")
        
        report["summary"] = " | ".join(summary_parts)
        
        # Persist to DB
        db.add(AccuracyReport(
            report_type="WEEKLY",
            report_data=json.dumps(report),
            top_signals=json.dumps(top_5),
            bottom_signals=json.dumps(bottom_5),
            trending_up=json.dumps(improving[:10]),
            trending_down=json.dumps(decaying[:10]),
            summary=report["summary"],
        ))
        db.commit()
        
        print(f"[ACCURACY REPORT] Weekly report generated: {report['summary']}")
        
        return report
    except Exception as e:
        print(f"[ACCURACY REPORT] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "ERROR", "reason": str(e)}
    finally:
        db.close()


def _get_flagged_signals_summary(db) -> Dict:
    """
    Get summary of currently flagged signals.
    """
    try:
        active_alerts = db.query(SystemAlertLog).filter(
            SystemAlertLog.resolved_at.is_(None),
        ).all()
        
        if not active_alerts:
            return {}
        
        critical = sum(1 for a in active_alerts if a.severity == "CRITICAL")
        warnings = sum(1 for a in active_alerts if a.severity == "WARN")
        
        return {
            "total_flagged": len(active_alerts),
            "critical": critical,
            "warnings": warnings,
            "signals": list(set(a.source for a in active_alerts)),
        }
    except Exception:
        return {}


def get_latest_report() -> Optional[Dict]:
    """Get the most recent accuracy report."""
    db = SessionLocal()
    try:
        report = db.query(AccuracyReport).order_by(
            AccuracyReport.created_at.desc()
        ).first()
        
        if not report:
            return None
        
        return {
            "id": report.id,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "report_type": report.report_type,
            "report_data": json.loads(report.report_data) if report.report_data else None,
            "summary": report.summary,
        }
    finally:
        db.close()
