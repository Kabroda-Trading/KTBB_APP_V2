"""
timeframe_analyzer.py — KQAL Timeframe-Specific Trade Analyzer

Reads campaign_logs and decision_journal to answer the core question:
"How are 15m vs 1h vs 4h trades actually performing?"

This is the feedback loop Kabroda has been missing. It produces:
  - Win rate by timeframe
  - Average stop distance by timeframe
  - Average target distance by timeframe
  - Best energy states per timeframe
  - Best kinematic grades per timeframe
  - Strategy effectiveness by timeframe
  - Session box size vs outcome correlation

Usage:
    from kqal.timeframe_analyzer import analyze_timeframes
    report = analyze_timeframes(days=60)
    print(report["summary"])
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from kqal.db_reader import _fetch_all

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_campaigns(days: int = 60) -> Optional[list[dict[str, Any]]]:
    """Fetch closed canonical trades with timeframe info.

    NOTE: is_canonical is deliberately False for every 4H/1H candidate row
    (gravity_engine.py's detectors set it that way to keep candidates out of
    the 15M production track record's KPIs). We must explicitly include them
    via session_timeframe — same pattern as audit_ai.py's _real_btc_row().
    """
    query = """
        SELECT
            id,
            symbol,
            date_key,
            session_id,
            session_timeframe,
            bias,
            entry_price,
            stop_loss,
            t1,
            t2,
            t3,
            status,
            realized_pnl,
            mas_approval_status,
            target_logic_version,
            target_hit,
            max_target_reached,
            t2_reached,
            t3_reached,
            energy_grade,
            kinematic_grade,
            macro_bias,
            target_too_small_flag,
            closed_at,
            entry_filled_at,
            structure_reasoning
        FROM campaign_logs
        WHERE (is_canonical = TRUE OR session_timeframe IN ('4H', '1H'))
          AND symbol = 'BTC/USDT'
          AND date_key >= %s
        ORDER BY date_key DESC, id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


def _fetch_decisions(days: int = 60) -> Optional[list[dict[str, Any]]]:
    """Fetch decision journal entries with outcome data."""
    query = """
        SELECT
            id,
            symbol,
            session_date,
            session_id,
            decision_type,
            confluence_score,
            confluence_direction,
            energy_status,
            kinematic_grade,
            bo_price,
            bd_price,
            asset_price,
            source,
            outcome_price_4h,
            outcome_pct_move_4h,
            outcome_direction_correct,
            decision_reason
        FROM decision_journal
        WHERE source = 'mas_flow'
          AND session_date >= %s
        ORDER BY session_date DESC, id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_float(val) -> Optional[float]:
    """Convert to float or return None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _calc_stop_distance_pct(entry: Optional[float], stop: Optional[float]) -> Optional[float]:
    """Calculate stop distance as percentage of entry price."""
    entry_f = _safe_float(entry)
    stop_f = _safe_float(stop)
    if entry_f is None or stop_f is None or entry_f == 0:
        return None
    return abs(entry_f - stop_f) / entry_f * 100


def _calc_target_distance_pct(entry: Optional[float], target: Optional[float]) -> Optional[float]:
    """Calculate target distance as percentage of entry price."""
    entry_f = _safe_float(entry)
    target_f = _safe_float(target)
    if entry_f is None or target_f is None or entry_f == 0:
        return None
    return abs(target_f - entry_f) / entry_f * 100


def _calc_box_size_pct(bo: Optional[float], bd: Optional[float], price: Optional[float]) -> Optional[float]:
    """Calculate session box size as percentage of price."""
    bo_f = _safe_float(bo)
    bd_f = _safe_float(bd)
    price_f = _safe_float(price or bo_f or bd_f)
    if bo_f is None or bd_f is None or price_f is None or price_f == 0:
        return None
    return abs(bo_f - bd_f) / price_f * 100


def _bucket_box_size(pct: Optional[float]) -> str:
    """Classify box size into buckets."""
    if pct is None:
        return "UNKNOWN"
    if pct < 0.35:
        return "TINY (<0.35%)"
    if pct < 0.5:
        return "NARROW (0.35-0.5%)"
    if pct < 0.75:
        return "MEDIUM (0.5-0.75%)"
    if pct < 1.0:
        return "WIDE (0.75-1.0%)"
    return "VERY_WIDE (>1.0%)"


def _bucket_stop_distance(pct: Optional[float]) -> str:
    """Classify stop distance into buckets."""
    if pct is None:
        return "UNKNOWN"
    if pct < 0.5:
        return "VERY_TIGHT (<0.5%)"
    if pct < 1.0:
        return "TIGHT (0.5-1.0%)"
    if pct < 1.5:
        return "MODERATE (1.0-1.5%)"
    if pct < 2.5:
        return "WIDE (1.5-2.5%)"
    return "VERY_WIDE (>2.5%)"


def _bucket_target_distance(pct: Optional[float]) -> str:
    """Classify target distance into buckets."""
    if pct is None:
        return "UNKNOWN"
    if pct < 0.5:
        return "VERY_TIGHT (<0.5%)"
    if pct < 1.0:
        return "TIGHT (0.5-1.0%)"
    if pct < 1.5:
        return "MODERATE (1.0-1.5%)"
    if pct < 2.5:
        return "WIDE (1.5-2.5%)"
    return "VERY_WIDE (>2.5%)"


# ═══════════════════════════════════════════════════════════════════════════════
# PER-TIMEFRAME ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _analyze_timeframe(trades: list[dict[str, Any]], tf_label: str) -> dict[str, Any]:
    """Analyze a set of trades for a single timeframe."""
    total = len(trades)
    if total == 0:
        return {
            "timeframe": tf_label,
            "total_trades": 0,
            "status": "NO_DATA",
        }

    # Status breakdown
    closed_wins = [t for t in trades if t.get("status") == "CLOSED_WIN"]
    closed_losses = [t for t in trades if t.get("status") == "CLOSED_LOSS"]
    closed_all = closed_wins + closed_losses
    pending = [t for t in trades if t.get("status") == "PENDING"]
    expired = [t for t in trades if t.get("status") == "EXPIRED"]
    stand_down = [t for t in trades if t.get("mas_approval_status") == "STAND_DOWN"]

    # Win rate
    win_count = len(closed_wins)
    loss_count = len(closed_losses)
    total_closed = win_count + loss_count
    win_rate = round(win_count / total_closed * 100, 1) if total_closed > 0 else None

    # PnL analysis
    pnl_values = [_safe_float(t.get("realized_pnl")) for t in closed_all
                  if _safe_float(t.get("realized_pnl")) is not None]
    net_r = round(sum(pnl_values), 2) if pnl_values else None
    avg_r = round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else None

    # Stop distance analysis
    stop_distances = []
    for t in trades:
        d = _calc_stop_distance_pct(t.get("entry_price"), t.get("stop_loss"))
        if d is not None:
            stop_distances.append(d)

    avg_stop_pct = round(sum(stop_distances) / len(stop_distances), 2) if stop_distances else None
    min_stop_pct = round(min(stop_distances), 2) if stop_distances else None
    max_stop_pct = round(max(stop_distances), 2) if stop_distances else None

    # Target distance analysis (T1)
    target_distances = []
    for t in trades:
        d = _calc_target_distance_pct(t.get("entry_price"), t.get("t1"))
        if d is not None:
            target_distances.append(d)

    avg_target_pct = round(sum(target_distances) / len(target_distances), 2) if target_distances else None
    min_target_pct = round(min(target_distances), 2) if target_distances else None
    max_target_pct = round(max(target_distances), 2) if target_distances else None

    # R:R analysis (stop vs target ratio)
    rr_ratios = []
    for t in trades:
        stop_d = _calc_stop_distance_pct(t.get("entry_price"), t.get("stop_loss"))
        target_d = _calc_target_distance_pct(t.get("entry_price"), t.get("t1"))
        if stop_d is not None and target_d is not None and stop_d > 0:
            rr_ratios.append(round(target_d / stop_d, 2))

    avg_rr = round(sum(rr_ratios) / len(rr_ratios), 2) if rr_ratios else None

    # Energy grade breakdown
    energy_grades: dict[str, dict[str, int]] = {}
    for t in trades:
        eg = t.get("energy_grade") or "UNKNOWN"
        if eg not in energy_grades:
            energy_grades[eg] = {"total": 0, "wins": 0, "losses": 0}
        energy_grades[eg]["total"] += 1
        if t.get("status") == "CLOSED_WIN":
            energy_grades[eg]["wins"] += 1
        elif t.get("status") == "CLOSED_LOSS":
            energy_grades[eg]["losses"] += 1

    energy_summary = {}
    for eg, counts in energy_grades.items():
        closed = counts["wins"] + counts["losses"]
        energy_summary[eg] = {
            "total": counts["total"],
            "closed": closed,
            "wins": counts["wins"],
            "losses": counts["losses"],
            "win_rate": round(counts["wins"] / closed * 100, 1) if closed > 0 else None,
        }

    # Kinematic grade breakdown
    kinematic_grades: dict[str, dict[str, int]] = {}
    for t in trades:
        kg = t.get("kinematic_grade") or "UNKNOWN"
        if kg not in kinematic_grades:
            kinematic_grades[kg] = {"total": 0, "wins": 0, "losses": 0}
        kinematic_grades[kg]["total"] += 1
        if t.get("status") == "CLOSED_WIN":
            kinematic_grades[kg]["wins"] += 1
        elif t.get("status") == "CLOSED_LOSS":
            kinematic_grades[kg]["losses"] += 1

    kinematic_summary = {}
    for kg, counts in kinematic_grades.items():
        closed = counts["wins"] + counts["losses"]
        kinematic_summary[kg] = {
            "total": counts["total"],
            "closed": closed,
            "wins": counts["wins"],
            "losses": counts["losses"],
            "win_rate": round(counts["wins"] / closed * 100, 1) if closed > 0 else None,
        }

    # Target hit analysis
    target_hits: dict[str, int] = {}
    for t in closed_all:
        th = t.get("target_hit") or "UNKNOWN"
        target_hits[th] = target_hits.get(th, 0) + 1

    # Max target reached analysis
    max_targets: dict[str, int] = {}
    for t in closed_all:
        mt = t.get("max_target_reached") or "NONE"
        max_targets[mt] = max_targets.get(mt, 0) + 1

    # Bias breakdown
    bias_breakdown: dict[str, dict] = {}
    for t in trades:
        b = t.get("bias") or "UNKNOWN"
        if b not in bias_breakdown:
            bias_breakdown[b] = {"total": 0, "wins": 0, "losses": 0}
        bias_breakdown[b]["total"] += 1
        if t.get("status") == "CLOSED_WIN":
            bias_breakdown[b]["wins"] += 1
        elif t.get("status") == "CLOSED_LOSS":
            bias_breakdown[b]["losses"] += 1

    bias_summary = {}
    for b, counts in bias_breakdown.items():
        closed = counts["wins"] + counts["losses"]
        bias_summary[b] = {
            "total": counts["total"],
            "closed": closed,
            "wins": counts["wins"],
            "losses": counts["losses"],
            "win_rate": round(counts["wins"] / closed * 100, 1) if closed > 0 else None,
        }

    return {
        "timeframe": tf_label,
        "total_trades": total,
        "status_breakdown": {
            "closed_wins": win_count,
            "closed_losses": loss_count,
            "pending": len(pending),
            "expired": len(expired),
            "stand_down": len(stand_down),
        },
        "performance": {
            "win_rate_pct": win_rate,
            "net_r": net_r,
            "avg_r": avg_r,
            "total_closed": total_closed,
        },
        "stop_analysis": {
            "avg_distance_pct": avg_stop_pct,
            "min_distance_pct": min_stop_pct,
            "max_distance_pct": max_stop_pct,
            "sample_size": len(stop_distances),
        },
        "target_analysis": {
            "avg_t1_distance_pct": avg_target_pct,
            "min_t1_distance_pct": min_target_pct,
            "max_t1_distance_pct": max_target_pct,
            "avg_rr_ratio": avg_rr,
            "sample_size": len(target_distances),
        },
        "target_hit_breakdown": target_hits,
        "max_target_reached": max_targets,
        "energy_grade_breakdown": energy_summary,
        "kinematic_grade_breakdown": kinematic_summary,
        "bias_breakdown": bias_summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION OUTCOME ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _analyze_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze decision journal entries for outcome accuracy."""
    total = len(decisions)
    if total == 0:
        return {"total_decisions": 0, "status": "NO_DATA"}

    # Decision type breakdown
    approved = [d for d in decisions if d.get("decision_type") == "MAS_APPROVED"]
    stand_down = [d for d in decisions if d.get("decision_type") == "MAS_STAND_DOWN"]
    rejected = [d for d in decisions if d.get("decision_type") == "MAS_REJECTED"]

    # Outcome accuracy
    correct = sum(1 for d in decisions if d.get("outcome_direction_correct") is True)
    wrong = sum(1 for d in decisions if d.get("outcome_direction_correct") is False)
    unresolved = sum(1 for d in decisions if d.get("outcome_direction_correct") is None)
    total_resolved = correct + wrong
    accuracy = round(correct / total_resolved * 100, 1) if total_resolved > 0 else None

    # Stand-down validation
    sd_correct = sum(1 for d in stand_down if d.get("outcome_direction_correct") is True)
    sd_wrong = sum(1 for d in stand_down if d.get("outcome_direction_correct") is False)
    sd_total = sd_correct + sd_wrong
    sd_accuracy = round(sd_correct / sd_total * 100, 1) if sd_total > 0 else None

    # Box size vs outcome
    box_outcomes: dict[str, dict] = {}
    for d in decisions:
        box_pct = _calc_box_size_pct(d.get("bo_price"), d.get("bd_price"), d.get("asset_price"))
        bucket = _bucket_box_size(box_pct)
        if bucket not in box_outcomes:
            box_outcomes[bucket] = {"total": 0, "correct": 0, "wrong": 0}
        box_outcomes[bucket]["total"] += 1
        if d.get("outcome_direction_correct") is True:
            box_outcomes[bucket]["correct"] += 1
        elif d.get("outcome_direction_correct") is False:
            box_outcomes[bucket]["wrong"] += 1

    box_summary = {}
    for bucket, counts in box_outcomes.items():
        resolved = counts["correct"] + counts["wrong"]
        box_summary[bucket] = {
            "total": counts["total"],
            "resolved": resolved,
            "correct": counts["correct"],
            "wrong": counts["wrong"],
            "accuracy": round(counts["correct"] / resolved * 100, 1) if resolved > 0 else None,
        }

    # Energy status vs outcome
    energy_outcomes: dict[str, dict] = {}
    for d in decisions:
        es = d.get("energy_status") or "UNKNOWN"
        if es not in energy_outcomes:
            energy_outcomes[es] = {"total": 0, "correct": 0, "wrong": 0}
        energy_outcomes[es]["total"] += 1
        if d.get("outcome_direction_correct") is True:
            energy_outcomes[es]["correct"] += 1
        elif d.get("outcome_direction_correct") is False:
            energy_outcomes[es]["wrong"] += 1

    energy_summary = {}
    for es, counts in energy_outcomes.items():
        resolved = counts["correct"] + counts["wrong"]
        energy_summary[es] = {
            "total": counts["total"],
            "resolved": resolved,
            "correct": counts["correct"],
            "wrong": counts["wrong"],
            "accuracy": round(counts["correct"] / resolved * 100, 1) if resolved > 0 else None,
        }

    return {
        "total_decisions": total,
        "decision_breakdown": {
            "approved": len(approved),
            "stand_down": len(stand_down),
            "rejected": len(rejected),
        },
        "outcome_accuracy": {
            "correct": correct,
            "wrong": wrong,
            "unresolved": unresolved,
            "accuracy_pct": accuracy,
        },
        "stand_down_validation": {
            "correct": sd_correct,
            "wrong": sd_wrong,
            "accuracy_pct": sd_accuracy,
        },
        "box_size_vs_outcome": box_summary,
        "energy_status_vs_outcome": energy_summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_timeframes(days: int = 60) -> dict[str, Any]:
    """Run the full timeframe analysis and return a structured report.

    Parameters
    ----------
    days : int
        How many days of history to analyze (default 60).

    Returns
    -------
    dict
        Structured report with per-timeframe breakdowns and decision analysis.
    """
    campaigns = _fetch_campaigns(days)
    decisions = _fetch_decisions(days)

    if campaigns is None:
        return {"status": "ERROR", "message": "Failed to fetch campaign data"}
    if decisions is None:
        return {"status": "ERROR", "message": "Failed to fetch decision data"}

    # Split campaigns by timeframe
    tf_15m = [c for c in campaigns if (c.get("session_timeframe") or "15M") == "15M"]
    tf_1h = [c for c in campaigns if c.get("session_timeframe") == "1H"]
    tf_4h = [c for c in campaigns if c.get("session_timeframe") == "4H"]
    tf_unknown = [c for c in campaigns if c.get("session_timeframe") not in ("15M", "1H", "4H")]

    # Analyze each timeframe
    report_15m = _analyze_timeframe(tf_15m, "15M")
    report_1h = _analyze_timeframe(tf_1h, "1H")
    report_4h = _analyze_timeframe(tf_4h, "4H")

    # Analyze decisions
    decision_report = _analyze_decisions(decisions)

    # Build summary
    total_trades = len(campaigns)
    total_closed = sum(
        r.get("performance", {}).get("total_closed", 0)
        for r in [report_15m, report_1h, report_4h]
    )
    total_wins = sum(
        r.get("status_breakdown", {}).get("closed_wins", 0)
        for r in [report_15m, report_1h, report_4h]
    )
    total_losses = sum(
        r.get("status_breakdown", {}).get("closed_losses", 0)
        for r in [report_15m, report_1h, report_4h]
    )
    overall_win_rate = round(total_wins / total_closed * 100, 1) if total_closed > 0 else None

    # Best performing timeframe
    tf_performance = []
    for label, r in [("15M", report_15m), ("1H", report_1h), ("4H", report_4h)]:
        wr = r.get("performance", {}).get("win_rate_pct")
        net = r.get("performance", {}).get("net_r")
        if wr is not None:
            tf_performance.append((label, wr, net))

    tf_performance.sort(key=lambda x: x[1] or 0, reverse=True)
    best_tf = tf_performance[0][0] if tf_performance else None

    return {
        "status": "OK",
        "analysis_period_days": days,
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total_trades": total_trades,
            "total_closed": total_closed,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "overall_win_rate_pct": overall_win_rate,
            "best_performing_timeframe": best_tf,
            "tf_rankings": [
                {"timeframe": label, "win_rate_pct": wr, "net_r": net}
                for label, wr, net in tf_performance
            ],
        },
        "timeframes": {
            "15m": report_15m,
            "1h": report_1h,
            "4h": report_4h,
            "unknown_timeframe": len(tf_unknown),
        },
        "decisions": decision_report,
    }


def print_report(report: dict[str, Any]) -> str:
    """Format the analysis report as a human-readable string.

    Parameters
    ----------
    report : dict
        The report from analyze_timeframes().

    Returns
    -------
    str
        Formatted report string.
    """
    if report.get("status") != "OK":
        return f"ERROR: {report.get('message', 'Unknown error')}"

    lines = []
    s = report.get("summary", {})
    lines.append("=" * 70)
    lines.append("  KQAL TIMEFRAME TRADE ANALYZER")
    lines.append(f"  Period: {report.get('analysis_period_days')} days")
    lines.append(f"  Generated: {report.get('generated_at')}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  Total Trades: {s.get('total_trades')}")
    lines.append(f"  Total Closed: {s.get('total_closed')}")
    lines.append(f"  Overall Win Rate: {s.get('overall_win_rate_pct')}%")
    lines.append(f"  Best Timeframe: {s.get('best_performing_timeframe')}")
    lines.append("")
    lines.append("  Timeframe Rankings:")
    for tf in s.get("tf_rankings", []):
        wr = tf.get("win_rate_pct")
        net = tf.get("net_r")
        lines.append(f"    {tf['timeframe']}: {wr}% win rate, {net} net R")
    lines.append("")

    for tf_label in ["15m", "1h", "4h"]:
        tf_data = report.get("timeframes", {}).get(tf_label, {})
        if tf_data.get("total_trades", 0) == 0:
            lines.append(f"  ── {tf_label.upper()} — NO DATA ──")
            lines.append("")
            continue

        lines.append(f"  ── {tf_label.upper()} — {tf_data.get('total_trades')} trades ──")
        perf = tf_data.get("performance", {})
        lines.append(f"    Win Rate: {perf.get('win_rate_pct')}%  |  Net R: {perf.get('net_r')}  |  Avg R: {perf.get('avg_r')}")
        lines.append(f"    Closed: {perf.get('total_closed')}  |  Wins: {tf_data.get('status_breakdown', {}).get('closed_wins')}  |  Losses: {tf_data.get('status_breakdown', {}).get('closed_losses')}")

        stop = tf_data.get("stop_analysis", {})
        lines.append(f"    Stop Distance: avg {stop.get('avg_distance_pct')}%  |  min {stop.get('min_distance_pct')}%  |  max {stop.get('max_distance_pct')}%")

        target = tf_data.get("target_analysis", {})
        lines.append(f"    T1 Distance: avg {target.get('avg_t1_distance_pct')}%  |  min {target.get('min_t1_distance_pct')}%  |  max {target.get('max_t1_distance_pct')}%")
        lines.append(f"    Avg R:R Ratio: {target.get('avg_rr_ratio')}")

        # Energy grade breakdown
        eg = tf_data.get("energy_grade_breakdown", {})
        if eg:
            lines.append("    Energy Grade Breakdown:")
            for grade, data in sorted(eg.items(), key=lambda x: x[1].get("win_rate", 0) or 0, reverse=True):
                wr = data.get("win_rate")
                wr_str = f"{wr}%" if wr is not None else "N/A"
                lines.append(f"      {grade}: {data['closed']} closed, {data['wins']}W/{data['losses']}L ({wr_str})")

        # Kinematic grade breakdown
        kg = tf_data.get("kinematic_grade_breakdown", {})
        if kg:
            lines.append("    Kinematic Grade Breakdown:")
            for grade, data in sorted(kg.items(), key=lambda x: x[1].get("win_rate", 0) or 0, reverse=True):
                wr = data.get("win_rate")
                wr_str = f"{wr}%" if wr is not None else "N/A"
                lines.append(f"      {grade}: {data['closed']} closed, {data['wins']}W/{data['losses']}L ({wr_str})")

        # Target hit breakdown
        th = tf_data.get("target_hit_breakdown", {})
        if th:
            lines.append(f"    Target Hit: {th}")

        # Max target reached
        mt = tf_data.get("max_target_reached", {})
        if mt:
            lines.append(f"    Max Target Reached: {mt}")

        lines.append("")

    # Decision analysis
    dec = report.get("decisions", {})
    if dec.get("total_decisions", 0) > 0:
        lines.append(f"  ── DECISION ANALYSIS ({dec.get('total_decisions')} entries) ──")
        db = dec.get("decision_breakdown", {})
        lines.append(f"    Approved: {db.get('approved')}  |  Stand Down: {db.get('stand_down')}  |  Rejected: {db.get('rejected')}")

        oa = dec.get("outcome_accuracy", {})
        lines.append(f"    Direction Accuracy: {oa.get('accuracy_pct')}%  ({oa.get('correct')}C/{oa.get('wrong')}W/{oa.get('unresolved')}U)")

        sd = dec.get("stand_down_validation", {})
        if sd.get("correct") is not None or sd.get("wrong") is not None:
            lines.append(f"    Stand-Down Validation: {sd.get('accuracy_pct')}%  ({sd.get('correct')}C/{sd.get('wrong')}W)")

        # Box size vs outcome
        box = dec.get("box_size_vs_outcome", {})
        if box:
            lines.append("    Box Size vs Outcome:")
            for bucket, data in sorted(box.items()):
                acc = data.get("accuracy")
                acc_str = f"{acc}%" if acc is not None else "N/A"
                lines.append(f"      {bucket}: {data['resolved']} resolved, {data['correct']}C/{data['wrong']}W ({acc_str})")

        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)
