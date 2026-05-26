# performance_auditor.py
# ==============================================================================
# KABRODA PERFORMANCE AUDITOR — Phase 3D
# Fires weekly at Sunday 23:00 UTC. Reads 7 days of data from CampaignLog,
# DecisionJournal, JewelSnapshotLog, and MacroNarrativeLog. Computes all
# statistics programmatically (no LLM), then calls agent_core._call_agent()
# to synthesize a ~200-word performance_note. Writes the note to the latest
# authored_by="senior_analyst" row in macro_narrative_log.
#
# The Senior Analyst reads this note every morning as the final line of
# _read_narrative_context() — it shapes the next week's tone and focus.
# ==============================================================================

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from database import (
    SessionLocal,
    CampaignLog,
    DecisionJournal,
    JewelSnapshotLog,
    MacroNarrativeLog,
)
from agent_core import _call_agent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are the Kabroda Performance Auditor. Your job is to write a weekly performance_note \
that the Senior Analyst reads before writing each morning brief.

Your note must:
- Lead with the single most important finding from this week's data
- Identify the strongest confirmed pattern (which JEWEL configuration correlated with best outcomes)
- Identify the biggest gap or failure mode observed this week
- End with one specific actionable recommendation for the Senior Analyst next week

Voice rules — identical to Senior Analyst:
- Declarative statements only. "The BBWP gate worked" not "the BBWP gate may have worked."
- Every statement must be derived from the stats block provided. No speculation.
- Banned words: could, might, may, perhaps, potentially, consider, possibly
- No generic market commentary. Week-specific findings only.
- If data is sparse (new system, few trades), state that directly and focus on what IS available.
- Target ~200 words. Tight. No padding.\
"""


# ------------------------------------------------------------------------------
# STEP 1 — DATA COLLECTION (pure Python, no LLM)
# ------------------------------------------------------------------------------

def _collect_stats(symbol: str, cutoff: datetime) -> Dict[str, Any]:
    """
    Query all four tables for the last 7 days and return a structured stats dict.
    Uses naive UTC datetimes throughout to match SQLAlchemy stored values.
    """
    db = SessionLocal()
    try:
        # ---- CampaignLog ----
        campaigns = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.created_at >= cutoff,
            )
            .all()
        )
        approved       = [c for c in campaigns if c.mas_approval_status == "APPROVED"]
        closed_win     = [c for c in campaigns if c.status == "CLOSED_WIN"]
        closed_loss    = [c for c in campaigns if c.status == "CLOSED_LOSS"]
        closed_all     = closed_win + closed_loss
        pending        = [c for c in campaigns if c.status == "PENDING"]

        win_rate = (
            round(len(closed_win) / len(closed_all) * 100.0, 1)
            if closed_all else None
        )
        pnl_vals = [c.realized_pnl for c in closed_all if c.realized_pnl is not None]
        avg_pnl  = round(sum(pnl_vals) / len(pnl_vals), 4) if pnl_vals else None

        approved_date_keys = {c.date_key for c in approved}

        # ---- DecisionJournal ----
        decisions = (
            db.query(DecisionJournal)
            .filter(
                DecisionJournal.symbol == symbol,
                DecisionJournal.timestamp >= cutoff,
            )
            .all()
        )
        dir_correct  = sum(1 for d in decisions if d.outcome_direction_correct is True)
        dir_wrong    = sum(1 for d in decisions if d.outcome_direction_correct is False)
        dir_null     = sum(1 for d in decisions if d.outcome_direction_correct is None)
        total_dir    = dir_correct + dir_wrong
        dir_accuracy = round(dir_correct / total_dir * 100.0, 1) if total_dir > 0 else None
        stand_down   = sum(1 for d in decisions if d.decision_type == "STAND_DOWN")

        # ---- JewelSnapshotLog ----
        snapshots = (
            db.query(JewelSnapshotLog)
            .filter(
                JewelSnapshotLog.symbol == symbol,
                JewelSnapshotLog.timestamp >= cutoff,
            )
            .all()
        )

        def _day(ts: Optional[datetime]) -> Optional[str]:
            if ts is None:
                return None
            # Handle both naive and aware datetimes stored by SQLite
            return ts.strftime("%Y-%m-%d")

        gate_open_days: set = set()
        all_snap_days:  set = set()
        for s in snapshots:
            day = _day(s.timestamp)
            if day:
                all_snap_days.add(day)
                if s.jewel_gate_open is True:
                    gate_open_days.add(day)

        total_days   = len(all_snap_days)
        gate_open_pct = (
            round(len(gate_open_days) / total_days * 100.0, 1)
            if total_days > 0 else 0.0
        )
        strong_conv_count = sum(1 for s in snapshots if s.jewel_conviction == "STRONG")

        # Gate vs approval correlation (by calendar day)
        gate_closed_days = all_snap_days - gate_open_days
        gate_open_approved     = len(gate_open_days   & approved_date_keys)
        gate_open_not_approved = len(gate_open_days   - approved_date_keys)
        gate_closed_approved   = len(gate_closed_days & approved_date_keys)
        gate_closed_not_approved = len(gate_closed_days - approved_date_keys)

        # ---- MacroNarrativeLog — wave stability ----
        wave_rows = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == symbol,
                MacroNarrativeLog.authored_by == "elliott_wave_specialist",
                MacroNarrativeLog.created_at >= cutoff,
            )
            .order_by(MacroNarrativeLog.id.asc())
            .all()
        )
        wave_labels   = [r.wave_label for r in wave_rows if r.wave_label]
        unique_labels = list(dict.fromkeys(wave_labels))  # ordered, deduplicated
        wave_stable   = len(unique_labels) <= 1

        comp_pcts   = [r.completion_pct for r in wave_rows if r.completion_pct is not None]
        comp_start  = round(comp_pcts[0],  2) if comp_pcts else None
        comp_end    = round(comp_pcts[-1], 2) if comp_pcts else None
        comp_change = (
            round(comp_end - comp_start, 2)
            if comp_start is not None and comp_end is not None else None
        )

        return {
            "campaign": {
                "approved":        len(approved),
                "closed_win":      len(closed_win),
                "closed_loss":     len(closed_loss),
                "pending":         len(pending),
                "win_rate_pct":    win_rate,
                "avg_realized_pnl": avg_pnl,
            },
            "decisions": {
                "total_calls":              len(decisions),
                "direction_correct":        dir_correct,
                "direction_wrong":          dir_wrong,
                "direction_unresolved":     dir_null,
                "directional_accuracy_pct": dir_accuracy,
                "stand_down_count":         stand_down,
            },
            "jewel": {
                "total_snapshots":          len(snapshots),
                "gate_open_count":          len(gate_open_days),
                "gate_open_pct":            gate_open_pct,
                "strong_conviction_count":  strong_conv_count,
                "gate_open_approved":       gate_open_approved,
                "gate_open_not_approved":   gate_open_not_approved,
                "gate_closed_approved":     gate_closed_approved,
                "gate_closed_not_approved": gate_closed_not_approved,
            },
            "wave": {
                "wave_labels_seen":    unique_labels,
                "wave_stable":         wave_stable,
                "completion_pct_start":  comp_start,
                "completion_pct_end":    comp_end,
                "completion_pct_change": comp_change,
            },
        }
    finally:
        db.close()


def _format_stats_block(symbol: str, date_key: str, stats: Dict[str, Any]) -> str:
    """Format the computed stats into the structured context block sent to the LLM."""
    c = stats["campaign"]
    d = stats["decisions"]
    j = stats["jewel"]
    w = stats["wave"]

    lines = [
        f"PERFORMANCE AUDIT — {symbol} — Week ending {date_key}",
        "=" * 60,
        "",
        "TRADE OUTCOMES (CampaignLog — last 7 days):",
        f"  MAS-approved sessions: {c['approved']}",
        f"  Closed wins: {c['closed_win']}  |  Closed losses: {c['closed_loss']}  |  Pending: {c['pending']}",
    ]
    if c["win_rate_pct"] is not None:
        lines.append(f"  Win rate: {c['win_rate_pct']}%")
    else:
        lines.append("  Win rate: Insufficient closed trades for calculation")
    if c["avg_realized_pnl"] is not None:
        lines.append(f"  Avg realized PnL: {c['avg_realized_pnl']}")
    else:
        lines.append("  Avg realized PnL: No closed trades this week")

    lines += [
        "",
        "DIRECTIONAL ACCURACY (DecisionJournal — last 7 days):",
        f"  Total directional calls: {d['total_calls']}",
        f"  Correct: {d['direction_correct']}  |  Wrong: {d['direction_wrong']}  |  Unresolved: {d['direction_unresolved']}",
    ]
    if d["directional_accuracy_pct"] is not None:
        lines.append(f"  Directional accuracy: {d['directional_accuracy_pct']}%")
    else:
        lines.append("  Directional accuracy: No resolved directional calls yet")
    lines.append(f"  STAND_DOWN calls: {d['stand_down_count']}")

    lines += [
        "",
        "JEWEL GATE ANALYSIS (JewelSnapshotLog — last 7 days):",
        f"  Total snapshots captured: {j['total_snapshots']}",
        f"  Days with gate open (BBWP compressed): {j['gate_open_count']} ({j['gate_open_pct']}% of days with snapshots)",
        f"  Snapshots with STRONG conviction: {j['strong_conviction_count']}",
        f"  Gate open -> Approved trade: {j['gate_open_approved']} day(s)",
        f"  Gate open -> No trade: {j['gate_open_not_approved']} day(s)",
        f"  Gate closed -> Approved trade: {j['gate_closed_approved']} day(s) [filter miss — review]",
        f"  Gate closed -> No trade: {j['gate_closed_not_approved']} day(s) [correct filter]",
        "",
        "WAVE STRUCTURE (MacroNarrativeLog — last 7 days):",
    ]

    if w["wave_labels_seen"]:
        labels_str  = " -> ".join(w["wave_labels_seen"])
        stable_str  = "STABLE" if w["wave_stable"] else "CHANGED mid-week"
        lines.append(f"  Wave label: {labels_str} ({stable_str})")
    else:
        lines.append("  Wave label: No Elliott Wave Specialist data this week")

    if w["completion_pct_start"] is not None:
        sign = "+" if (w["completion_pct_change"] or 0) >= 0 else ""
        lines.append(
            f"  Completion: {w['completion_pct_start']}% -> {w['completion_pct_end']}% "
            f"({sign}{w['completion_pct_change']}% advance this week)"
        )
    else:
        lines.append("  Completion: No wave completion data this week")

    return "\n".join(lines)


# ------------------------------------------------------------------------------
# PUBLIC FUNCTION
# ------------------------------------------------------------------------------

def run_performance_audit(
    symbol: str,
    date_key: str,
) -> Dict[str, Any]:
    """
    Weekly performance audit pipeline.

    Steps:
    1. Collect last 7 days of data from all four tables (no LLM)
    2. Format structured stats block
    3. Call LLM to synthesize ~200-word performance_note
    4. Write performance_note to latest authored_by='senior_analyst' row
    5. Return status, note text, target row id, and full stats dict

    Always produces a performance_note even if data is sparse —
    the LLM is instructed to acknowledge insufficient data directly.
    """
    # Naive UTC to match SQLAlchemy stored datetimes
    cutoff = datetime.utcnow() - timedelta(days=7)

    logger.info(f"[PERF_AUDIT] Collecting stats — {symbol} — ending {date_key}")
    stats = _collect_stats(symbol, cutoff)

    context_block = _format_stats_block(symbol, date_key, stats)

    logger.info("[PERF_AUDIT] Calling LLM for performance_note synthesis...")
    try:
        performance_note = _call_agent(
            agent_name="performance_auditor",
            system_prompt=_SYSTEM_PROMPT,
            context_text=context_block,
            triggered_by=date_key,
            max_tokens=512,
        )
    except RuntimeError as e:
        return {"status": "BUDGET_BLOCKED", "error": str(e)}
    except Exception as e:
        logger.error(f"[PERF_AUDIT] LLM call failed: {e}")
        return {"status": "ERROR", "error": str(e)}

    # Write to latest senior_analyst row
    db = SessionLocal()
    target_row_id = None
    try:
        target = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == symbol,
                MacroNarrativeLog.authored_by == "senior_analyst",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )

        if target:
            target.performance_note = performance_note
            db.commit()
            target_row_id = target.id
            logger.info(
                f"[PERF_AUDIT] Written to MacroNarrativeLog id={target_row_id} "
                f"(analyst date_key={target.date_key})"
            )
        else:
            logger.warning(
                f"[PERF_AUDIT] No senior_analyst row found for {symbol}. "
                "performance_note generated but not persisted — "
                "Senior Analyst must run at least once first."
            )

    except Exception as e:
        db.rollback()
        logger.error(f"[PERF_AUDIT] DB write failed: {e}")
        return {
            "status": "ERROR",
            "error": str(e),
            "performance_note": performance_note,
            "stats": stats,
        }
    finally:
        db.close()

    return {
        "status": "SUCCESS",
        "performance_note": performance_note,
        "target_row_id": target_row_id,
        "stats": stats,
    }
