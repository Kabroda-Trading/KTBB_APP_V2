# performance_auditor.py
# ==============================================================================
# KABRODA SYSTEMIC ADVISER — Performance Auditor v2
# Fires weekly at Sunday 23:00 UTC via run_weekly_scheduler().
#
# Upgrade from v1: three new structural analysis blocks injected into the
# LLM context — harmonic breakdown, box size breakdown, STAND_DOWN validation.
# Output written to SystemAuditLog (permanent vault) instead of being stapled
# to macro_narrative_log. No dependency on a senior_analyst row existing.
#
# Data sources (no LLM in collection phase):
#   CampaignLog       — closed trade outcomes
#   DecisionJournal   — structural context + 4h directional outcomes
#   JewelSnapshotLog  — gate state and conviction history
#   MacroNarrativeLog — Elliott Wave stability
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
    SystemAuditLog,
)
from agent_core import _call_agent

logger = logging.getLogger(__name__)


# ==============================================================================
# SYSTEM PROMPT — Systemic Adviser mandate
# ==============================================================================

_SYSTEM_PROMPT = """\
You are the Kabroda Systemic Adviser. Your weekly audit note is read by the \
Senior Analyst before every morning brief for the coming week. It directly \
shapes the Senior Analyst's risk posture, veto thresholds, and configuration \
confidence. This note is not a history lesson. It is a calibration instrument.

Write exactly four sections in this order. Use plain numbered headers \
(1. OUTCOME SUMMARY, etc.). No markdown formatting — no ##, no **, no * bullets. \
Use plain dashes or numbers for sub-items.

1. OUTCOME SUMMARY
One to two sentences. State win rate, total approved sessions, and net R. \
If data is sparse (early system), state that directly and work with what exists.

2. STRUCTURAL PATTERN ANALYSIS
Analyse the three structural breakdowns provided in the stats block. \
Every finding must cite the specific numbers given — no speculation.

- HARMONIC BREAKDOWN: Which energy_status and kinematic_grade configurations \
  produced the highest directional accuracy? Which produced the lowest? \
  Name the best-performing configuration explicitly. Name the worst. \
  If one state had 0% accuracy, state that directly.

- BOX SIZE BREAKDOWN: Which session box size bucket had the best directional \
  accuracy? If narrow boxes underperformed wide boxes, state the threshold \
  and whether the minimum box floor should be raised. If data is sparse \
  or unresolved, say so.

- STAND_DOWN VALIDATION: Were the veto calls directionally correct? \
  If STAND_DOWN fired and price moved against the indicated direction \
  (veto saved a loss), the gate is calibrated correctly — confirm it. \
  If STAND_DOWN fired and price moved in the indicated direction \
  (veto may have missed a valid setup), flag this as a calibration \
  error requiring review. State counts exactly.

3. SYSTEMIC RECOMMENDATION
One specific, actionable recommendation for the Senior Analyst. \
Name an exact configuration, threshold, or rule change. \
Examples of required specificity:
  "Narrow box sessions (<0.5%) produced 0 correct calls from 2 attempts \
   this week. Raise the minimum box floor to 0.6% in the STAND_DOWN \
   Condition 3 threshold."
  "HOSTILE_CEILING produced 0 correct calls from 2 attempts. \
   Treat HOSTILE_CEILING as a hard STAND_DOWN trigger regardless \
   of other conditions."
  "The STAND_DOWN gate was overcautious on 1 of 2 calls this week. \
   Review whether Condition 1 (CHOP + CHOP_RISK) requires both signals \
   simultaneously or either alone."
End this section with two sentences: one stating what confidence to INCREASE \
next week, one stating what confidence to DECREASE.

4. SYSTEM HEALTH
One sentence on wave structure stability this week. \
One sentence on JEWEL gate frequency and whether compression was predictive. \
One sentence flagging any operational issues observed \
(double-fires, API errors, missing data, orphaned rows). \
If no operational issues: "No operational anomalies detected this week."

Voice rules:
- Declarative statements only. No hedging.
- Every claim derived from the stats block. No speculation.
- Banned: could, might, may, perhaps, potentially, consider, possibly
- No generic market commentary. Week-specific findings only.
- Target ~300 words. Tight. No padding.\
"""


# ==============================================================================
# STEP 1 — DATA COLLECTION (pure Python, no LLM)
# ==============================================================================

def _collect_stats(symbol: str, cutoff: datetime) -> Dict[str, Any]:
    """
    Query all four tables for the last 7 days and return a structured stats dict.
    Uses naive UTC datetimes throughout to match SQLAlchemy stored values.
    """
    db = SessionLocal()
    try:
        # ── CampaignLog ──────────────────────────────────────────────────────
        campaigns = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.created_at >= cutoff,
                CampaignLog.is_canonical == True,
            )
            .all()
        )
        approved    = [c for c in campaigns if c.mas_approval_status == "APPROVED"]
        closed_win  = [c for c in campaigns if c.status == "CLOSED_WIN"]
        closed_loss = [c for c in campaigns if c.status == "CLOSED_LOSS"]
        closed_all  = closed_win + closed_loss
        pending     = [c for c in campaigns if c.status not in ("CLOSED_WIN", "CLOSED_LOSS")]

        win_rate = (
            round(len(closed_win) / len(closed_all) * 100.0, 1)
            if closed_all else None
        )
        pnl_vals = [c.realized_pnl for c in closed_all if c.realized_pnl is not None]
        avg_pnl  = round(sum(pnl_vals) / len(pnl_vals), 4) if pnl_vals else None
        net_r    = round(sum(pnl_vals), 2) if pnl_vals else None

        # ── DecisionJournal ──────────────────────────────────────────────────
        decisions = (
            db.query(DecisionJournal)
            .filter(
                DecisionJournal.symbol == symbol,
                DecisionJournal.timestamp >= cutoff,
                DecisionJournal.source == "mas_flow",
            )
            .all()
        )
        dir_correct   = sum(1 for d in decisions if d.outcome_direction_correct is True)
        dir_wrong     = sum(1 for d in decisions if d.outcome_direction_correct is False)
        dir_null      = sum(1 for d in decisions if d.outcome_direction_correct is None)
        total_dir     = dir_correct + dir_wrong
        dir_accuracy  = round(dir_correct / total_dir * 100.0, 1) if total_dir > 0 else None
        stand_down    = sum(1 for d in decisions if d.decision_type == "MAS_STAND_DOWN")

        # ── Block A: Harmonic Breakdown ──────────────────────────────────────
        # Cross-references energy_status and kinematic_grade with 4h outcomes.
        # outcome_direction_correct = True  → price moved in indicated direction
        # outcome_direction_correct = False → price moved against indicated direction
        energy_breakdown: Dict[str, Dict[str, int]] = {}
        grade_breakdown:  Dict[str, Dict[str, int]] = {}

        for d in decisions:
            est = d.energy_status   or "UNKNOWN"
            kg  = d.kinematic_grade or "UNKNOWN"

            if d.outcome_direction_correct is True:    okey = "correct"
            elif d.outcome_direction_correct is False: okey = "wrong"
            else:                                      okey = "unresolved"

            for breakdown, key in [(energy_breakdown, est), (grade_breakdown, kg)]:
                if key not in breakdown:
                    breakdown[key] = {"correct": 0, "wrong": 0, "unresolved": 0}
                breakdown[key][okey] += 1

        # Compute accuracy % for each group
        def _acc(group: Dict[str, int]) -> Optional[float]:
            total = group["correct"] + group["wrong"]
            return round(group["correct"] / total * 100.0, 1) if total > 0 else None

        energy_summary = {
            k: {**v, "accuracy_pct": _acc(v)}
            for k, v in energy_breakdown.items()
        }
        grade_summary = {
            k: {**v, "accuracy_pct": _acc(v)}
            for k, v in grade_breakdown.items()
        }

        # ── Block B: Box Size Breakdown ──────────────────────────────────────
        def _box_bucket(pct: float) -> str:
            if pct < 0.5:  return "NARROW (<0.5%)"
            if pct < 1.0:  return "MEDIUM (0.5-1.0%)"
            return "WIDE (>1.0%)"

        box_breakdown: Dict[str, Dict] = {}

        for d in decisions:
            if not d.bo_price or not d.bd_price or d.bo_price <= 0:
                continue
            pct    = (d.bo_price - d.bd_price) / d.bo_price * 100
            bucket = _box_bucket(pct)

            if d.outcome_direction_correct is True:    okey = "correct"
            elif d.outcome_direction_correct is False: okey = "wrong"
            else:                                      okey = "unresolved"

            if bucket not in box_breakdown:
                box_breakdown[bucket] = {
                    "correct": 0, "wrong": 0, "unresolved": 0, "_samples": []
                }
            box_breakdown[bucket][okey] += 1
            box_breakdown[bucket]["_samples"].append(round(pct, 3))

        box_summary: Dict[str, Dict] = {}
        for bucket, data in box_breakdown.items():
            samples = data.pop("_samples")
            box_summary[bucket] = {
                **data,
                "count":    len(samples),
                "avg_pct":  round(sum(samples) / len(samples), 3) if samples else 0.0,
                "accuracy_pct": _acc(data),
            }

        # ── Block C: STAND_DOWN Validation ───────────────────────────────────
        # For each STAND_DOWN decision with a resolved outcome:
        #   outcome_direction_correct = False → direction was wrong → veto saved a loss
        #   outcome_direction_correct = True  → direction was right → veto may have been overcautious
        # Note: even if direction was right, the structural veto (narrow box, chop) may
        # still have been valid — but this is flagged for human review.
        sd_decisions       = [d for d in decisions if d.decision_type == "MAS_STAND_DOWN"]
        sd_saved           = sum(1 for d in sd_decisions if d.outcome_direction_correct is False)
        sd_overcautious    = sum(1 for d in sd_decisions if d.outcome_direction_correct is True)
        sd_unresolved      = sum(1 for d in sd_decisions if d.outcome_direction_correct is None)
        sd_total           = len(sd_decisions)
        sd_accuracy        = (
            round(sd_saved / (sd_saved + sd_overcautious) * 100.0, 1)
            if (sd_saved + sd_overcautious) > 0 else None
        )

        # ── JewelSnapshotLog ─────────────────────────────────────────────────
        snapshots = (
            db.query(JewelSnapshotLog)
            .filter(
                JewelSnapshotLog.symbol == symbol,
                JewelSnapshotLog.timestamp >= cutoff,
            )
            .all()
        )

        def _day(ts: Optional[datetime]) -> Optional[str]:
            return ts.strftime("%Y-%m-%d") if ts else None

        gate_open_days: set = set()
        all_snap_days:  set = set()
        approved_date_keys = {c.date_key for c in approved}

        for s in snapshots:
            day = _day(s.timestamp)
            if day:
                all_snap_days.add(day)
                if s.jewel_gate_open is True:
                    gate_open_days.add(day)

        total_days    = len(all_snap_days)
        gate_open_pct = (
            round(len(gate_open_days) / total_days * 100.0, 1)
            if total_days > 0 else 0.0
        )
        strong_conv_count = sum(1 for s in snapshots if s.jewel_conviction == "STRONG")

        gate_closed_days          = all_snap_days - gate_open_days
        gate_open_approved        = len(gate_open_days & approved_date_keys)
        gate_open_not_approved    = len(gate_open_days - approved_date_keys)
        gate_closed_approved      = len(gate_closed_days & approved_date_keys)
        gate_closed_not_approved  = len(gate_closed_days - approved_date_keys)

        # ── MacroNarrativeLog — wave stability ───────────────────────────────
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
        unique_labels = list(dict.fromkeys(wave_labels))
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
                "approved":         len(approved),
                "closed_win":       len(closed_win),
                "closed_loss":      len(closed_loss),
                "pending":          len(pending),
                "win_rate_pct":     win_rate,
                "avg_realized_pnl": avg_pnl,
                "net_r":            net_r,
            },
            "decisions": {
                "total_calls":              len(decisions),
                "direction_correct":        dir_correct,
                "direction_wrong":          dir_wrong,
                "direction_unresolved":     dir_null,
                "directional_accuracy_pct": dir_accuracy,
                "stand_down_count":         stand_down,
            },
            "harmonic": {
                "energy_breakdown": energy_summary,
                "grade_breakdown":  grade_summary,
            },
            "box_size": box_summary,
            "stand_down_validation": {
                "total":          sd_total,
                "saved":          sd_saved,
                "overcautious":   sd_overcautious,
                "unresolved":     sd_unresolved,
                "accuracy_pct":   sd_accuracy,
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
                "wave_labels_seen":      unique_labels,
                "wave_stable":           wave_stable,
                "completion_pct_start":  comp_start,
                "completion_pct_end":    comp_end,
                "completion_pct_change": comp_change,
            },
        }
    finally:
        db.close()


def _format_stats_block(symbol: str, date_key: str, stats: Dict[str, Any]) -> str:
    """Format the computed stats into the structured context block sent to the LLM."""
    c  = stats["campaign"]
    d  = stats["decisions"]
    h  = stats["harmonic"]
    b  = stats["box_size"]
    sd = stats["stand_down_validation"]
    j  = stats["jewel"]
    w  = stats["wave"]

    resolved_dir      = d["direction_correct"] + d["direction_wrong"]
    insufficient_data = resolved_dir == 0
    INSUFFICIENT_MSG  = f"  INSUFFICIENT DATA — {resolved_dir} resolved directional outcomes this week. Accuracy metric not computable."

    lines = [
        f"PERFORMANCE AUDIT — {symbol} — Week ending {date_key}",
        "=" * 60,
        "",
        "TRADE OUTCOMES (CampaignLog — last 7 days):",
        f"  Approved sessions: {c['approved']}",
        f"  Closed wins: {c['closed_win']}  |  Closed losses: {c['closed_loss']}  |  Pending/open: {c['pending']}",
    ]
    lines.append(
        f"  Win rate: {c['win_rate_pct']}%"
        if c["win_rate_pct"] is not None
        else "  Win rate: Insufficient closed trades"
    )
    lines.append(
        f"  Net R this week: {c['net_r']:+.2f}R  |  Avg PnL per closed trade: {c['avg_realized_pnl']}"
        if c["avg_realized_pnl"] is not None
        else "  Net R / Avg PnL: No closed trades this week"
    )

    lines += [
        "",
        "DIRECTIONAL ACCURACY (DecisionJournal — last 7 days):",
        f"  Total calls: {d['total_calls']}",
        f"  Correct: {d['direction_correct']}  |  Wrong: {d['direction_wrong']}  |  Unresolved (<4h): {d['direction_unresolved']}",
    ]
    lines.append(
        f"  Overall directional accuracy: {d['directional_accuracy_pct']}%"
        if d["directional_accuracy_pct"] is not None
        else "  Overall directional accuracy: No resolved calls yet"
    )
    lines.append(f"  STAND_DOWN calls this week: {d['stand_down_count']}")

    # ── Block A: Harmonic Breakdown ─────────────────────────────────────────
    lines += ["", "HARMONIC BREAKDOWN — Energy Status vs 4h Outcome:"]
    energy = h.get("energy_breakdown", {})
    if insufficient_data:
        lines.append(INSUFFICIENT_MSG)
    elif energy:
        for state, v in sorted(energy.items()):
            acc = f"{v['accuracy_pct']}%" if v["accuracy_pct"] is not None else "unresolved"
            lines.append(
                f"  {state:20}  correct:{v['correct']}  wrong:{v['wrong']}"
                f"  unresolved:{v['unresolved']}  accuracy:{acc}"
            )
    else:
        lines.append("  No energy_status data available this week.")

    lines += ["", "HARMONIC BREAKDOWN — Kinematic Grade vs 4h Outcome:"]
    grade = h.get("grade_breakdown", {})
    if insufficient_data:
        lines.append(INSUFFICIENT_MSG)
    elif grade:
        for g, v in sorted(grade.items()):
            acc = f"{v['accuracy_pct']}%" if v["accuracy_pct"] is not None else "unresolved"
            lines.append(
                f"  {g:20}  correct:{v['correct']}  wrong:{v['wrong']}"
                f"  unresolved:{v['unresolved']}  accuracy:{acc}"
            )
    else:
        lines.append("  No kinematic_grade data available this week.")

    # ── Block B: Box Size Breakdown ─────────────────────────────────────────
    lines += ["", "BOX SIZE BREAKDOWN — Session Box Width vs 4h Outcome:"]
    if insufficient_data:
        lines.append(INSUFFICIENT_MSG)
    elif b:
        for bucket in ["NARROW (<0.5%)", "MEDIUM (0.5-1.0%)", "WIDE (>1.0%)"]:
            if bucket in b:
                v   = b[bucket]
                acc = f"{v['accuracy_pct']}%" if v["accuracy_pct"] is not None else "unresolved"
                lines.append(
                    f"  {bucket:22}  count:{v['count']}  avg:{v['avg_pct']:.3f}%"
                    f"  correct:{v['correct']}  wrong:{v['wrong']}  accuracy:{acc}"
                )
    else:
        lines.append("  No bo_price/bd_price data in decisions this week.")

    # ── Block C: STAND_DOWN Validation ─────────────────────────────────────
    lines += ["", "STAND_DOWN VALIDATION:"]
    if sd["total"] > 0:
        lines += [
            f"  Total STAND_DOWN calls: {sd['total']}",
            f"  Veto saved a loss (direction was wrong):       {sd['saved']}",
            f"  Veto may have been overcautious (dir correct): {sd['overcautious']}",
            f"  Unresolved (outcome not yet computed):         {sd['unresolved']}",
        ]
        lines.append(
            f"  STAND_DOWN accuracy (saved / resolved): {sd['accuracy_pct']}%"
            if sd["accuracy_pct"] is not None
            else "  STAND_DOWN accuracy: No resolved calls yet"
        )
    else:
        lines.append("  No STAND_DOWN calls recorded this week.")

    # ── JEWEL ───────────────────────────────────────────────────────────────
    lines += [
        "",
        "JEWEL GATE ANALYSIS (JewelSnapshotLog — last 7 days):",
        f"  Total snapshots: {j['total_snapshots']}",
        f"  Days with gate open: {j['gate_open_count']} ({j['gate_open_pct']}%)",
        f"  STRONG conviction snapshots: {j['strong_conviction_count']}",
        f"  Gate OPEN  → trade approved: {j['gate_open_approved']}  |  no trade: {j['gate_open_not_approved']}",
        f"  Gate CLOSED → trade approved: {j['gate_closed_approved']} [filter miss]  |  no trade: {j['gate_closed_not_approved']} [correct]",
    ]

    # ── Wave ────────────────────────────────────────────────────────────────
    lines += ["", "WAVE STRUCTURE (MacroNarrativeLog — last 7 days):"]
    if w["wave_labels_seen"]:
        stable = "STABLE" if w["wave_stable"] else "CHANGED mid-week"
        lines.append(f"  Wave label: {' -> '.join(w['wave_labels_seen'])} ({stable})")
    else:
        lines.append("  Wave label: No Elliott Wave Specialist data this week")

    if w["completion_pct_start"] is not None:
        sign = "+" if (w["completion_pct_change"] or 0) >= 0 else ""
        lines.append(
            f"  Completion: {w['completion_pct_start']}% -> {w['completion_pct_end']}%"
            f" ({sign}{w['completion_pct_change']}% this week)"
        )
    else:
        lines.append("  Completion: No wave completion data this week")

    return "\n".join(lines)


# ==============================================================================
# PUBLIC FUNCTION
# ==============================================================================

def run_performance_audit(
    symbol: str,
    date_key: str,
) -> Dict[str, Any]:
    """
    Weekly systemic audit pipeline.

    Steps:
    1. Collect 7-day stats (no LLM) — includes three new structural blocks
    2. Format structured stats block
    3. LLM synthesizes ~300-word audit note (Systemic Adviser mandate)
    4. Write to SystemAuditLog (permanent vault — no dependency on senior_analyst row)
    5. Return status, note text, vault row id, and full stats dict
    """
    cutoff = datetime.utcnow() - timedelta(days=7)

    logger.info(f"[PERF_AUDIT] Collecting stats — {symbol} — ending {date_key}")
    stats = _collect_stats(symbol, cutoff)

    context_block = _format_stats_block(symbol, date_key, stats)

    logger.info("[PERF_AUDIT] Calling LLM for audit synthesis...")
    try:
        audit_note = _call_agent(
            agent_name="performance_auditor",
            system_prompt=_SYSTEM_PROMPT,
            context_text=context_block,
            triggered_by=date_key,
            max_tokens=900,
        )
    except RuntimeError as e:
        return {"status": "BUDGET_BLOCKED", "error": str(e)}
    except Exception as e:
        logger.error(f"[PERF_AUDIT] LLM call failed: {e}")
        return {"status": "ERROR", "error": str(e)}

    # Write to SystemAuditLog vault — always succeeds regardless of whether
    # a senior_analyst row exists in macro_narrative_log.
    db = SessionLocal()
    vault_row_id = None
    try:
        row = SystemAuditLog(
            symbol=symbol,
            date_key=date_key,
            audit_md=audit_note,
        )
        db.add(row)
        db.commit()
        vault_row_id = row.id
        logger.info(f"[PERF_AUDIT] Written to SystemAuditLog id={vault_row_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"[PERF_AUDIT] Vault write failed: {e}")
        return {
            "status": "ERROR",
            "error": str(e),
            "audit_note": audit_note,
            "stats": stats,
        }
    finally:
        db.close()

    return {
        "status": "SUCCESS",
        "audit_note": audit_note,
        "vault_row_id": vault_row_id,
        "stats": stats,
    }
