# harness/baseline.py
# =============================================================================
# KABRODA BATTLE-TEST HARNESS — Data-Collection-Mode Baseline
#
# The ONLY harness component that runs now. Produces a descriptive snapshot
# of the canonical dataset — NOT a validation test. Every cell prints its N.
# Any subgroup with N < 10 is automatically labeled CANDIDATE.
# Nothing is ever labeled a finding at current volume.
#
# Run from Render Shell:
#   cd /path/to/KTBB_APP_V2
#   python harness/baseline.py
#
# Re-run weekly to watch N climb toward the 30-event finding threshold.
# READ-ONLY. No write path to any table or config.
# =============================================================================

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.query_layer import (
    check_production_connection,
    open_db_session,
    fetch_canonical_campaigns,
    fetch_mas_flow_decisions,
    fetch_jewel_ny_open_snapshots,
)
from harness.join_logic import build_approved_stream, build_standdown_stream
from harness.tier_labels import tier_label, pct_with_n, BOUNDARY_PRELIMINARY
from harness.binomial_checkpoint import run_checkpoint, format_checkpoint_block, MILESTONES
from harness.snapshot_report import build_flag_block

# ── thresholds ────────────────────────────────────────────────────────────────
FINDING_THRESHOLD  = BOUNDARY_PRELIMINARY  # 30 — minimum N for statistical inference
SUBGROUP_THRESHOLD = 10                    # minimum N within a subgroup cell


def _label(n: int) -> str:
    """Four-tier label for overall-stream cells."""
    return tier_label(n)


def _subgroup_label(n: int) -> str:
    """Four-tier label for subgroup cells — based on the cell's own N."""
    return tier_label(n)


def _pct_with_n(numerator: int, denominator: int) -> str:
    """Always formats percentage with its N — structurally impossible to omit N."""
    return pct_with_n(numerator, denominator)


# ── breakdown builders ────────────────────────────────────────────────────────

def _categorical_breakdown(
    events: List[Dict[str, Any]],
    field: str,
    outcome_field: str,
    win_value,
    loss_value,
) -> Dict[str, Dict]:
    """
    Generic breakdown of events by a categorical field vs. a binary outcome.
    Returns {category: {n, wins, losses, rate_str, label}}.
    """
    buckets: Dict[str, Dict] = {}
    for e in events:
        key = e.get(field) or "UNKNOWN"
        outcome = e.get(outcome_field)
        if key not in buckets:
            buckets[key] = {"n": 0, "wins": 0, "losses": 0}
        buckets[key]["n"] += 1
        if outcome == win_value:
            buckets[key]["wins"] += 1
        elif outcome == loss_value:
            buckets[key]["losses"] += 1

    result = {}
    for key, data in sorted(buckets.items()):
        n = data["n"]
        result[key] = {
            **data,
            "rate_str": _pct_with_n(data["wins"], data["wins"] + data["losses"])
                        if (data["wins"] + data["losses"]) > 0 else "— (no resolved)",
            "label": _subgroup_label(n),
        }
    return result


# ── renderer ──────────────────────────────────────────────────────────────────

def _render_categorical_block(
    title: str,
    breakdown: Dict[str, Dict],
    n_total: int,
    col_header: str = "Win Rate (N)",
) -> str:
    lines = [
        f"\n{'─' * 72}",
        f"{title}  (stream N={n_total})",
        f"{'─' * 72}",
    ]
    col_w = max((len(k) for k in breakdown), default=12) + 2
    header = f"  {'Category':<{col_w}} {'N':>4}  {'Win':>4}  {'Loss':>4}  {col_header:<20}  Label"
    lines.append(header)
    lines.append("  " + "─" * (len(header) - 2))
    for key, data in breakdown.items():
        lines.append(
            f"  {key:<{col_w}} {data['n']:>4}  {data['wins']:>4}  {data['losses']:>4}"
            f"  {data['rate_str']:<20}  {data['label']}"
        )
    if not breakdown:
        lines.append("  (no data)")
    return "\n".join(lines)


# ── main snapshot ─────────────────────────────────────────────────────────────

def run_baseline() -> str:
    """
    Execute the full data-collection-mode baseline and return the snapshot as a string.
    Raises RuntimeError if not connected to production PostgreSQL.
    """
    check_production_connection()

    db = open_db_session()
    try:
        campaigns = fetch_canonical_campaigns(db)
        decisions = fetch_mas_flow_decisions(db)
        jewels    = fetch_jewel_ny_open_snapshots(db)
    finally:
        db.close()

    approved_result   = build_approved_stream(campaigns, decisions, jewels)
    standdown_result  = build_standdown_stream(campaigns, decisions, jewels)

    app  = approved_result["events"]
    sds  = standdown_result["events"]
    n_evaluable = len(app) + len(sds)

    # date range helpers
    all_dates = [e["date_key"] for e in app + sds if e.get("date_key")]
    date_range = f"{min(all_dates)} → {max(all_dates)}" if all_dates else "—"

    # double-fire anomaly
    double_fires = approved_result.get("double_fire_dates", [])

    wins   = sum(1 for e in app if e["outcome"] == "CLOSED_WIN")
    losses = sum(1 for e in app if e["outcome"] == "CLOSED_LOSS")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "=" * 72,
        "KABRODA BATTLE-TEST HARNESS — DATA-COLLECTION-MODE BASELINE",
        f"Generated: {timestamp}",
        "=" * 72,
        "",
        "WORKING DATASET",
        f"  Canonical total (is_canonical=TRUE):  {len([c for c in campaigns])} rows",
        f"  Approved stream:  {len(app)} events ({wins} CLOSED_WIN / {losses} CLOSED_LOSS)",
        f"  Stand-down stream: {standdown_result['n_total']} total  "
        f"({standdown_result['n_scoreable']} scoreable / "
        f"{standdown_result['n_unscoreable']} unscoreable — outcome_direction_correct=NULL)",
        f"  Total evaluable:  {n_evaluable} events",
        f"  Date range:       {date_range}",
        "",
        "JOIN PROVENANCE",
        f"  Approved → DJ join:    {approved_result['n_dj_joined']}/{len(app)}",
        f"  Approved → JEWEL join: {approved_result['n_jewel_joined']}/{len(app)}",
    ]

    if double_fires:
        lines.append(f"  ANOMALY — double-fire dates (multiple DJ rows, later row used): {double_fires}")

    lines += [
        "",
        f"FINDING THRESHOLD: {FINDING_THRESHOLD} resolved events required.",
        f"CURRENT N: {n_evaluable}. ALL subgroup results below are CANDIDATE.",
        "No result at this volume is a finding or a recommendation.",
        "",
    ]

    # ── Block 1: Energy Status vs Outcome (approved stream) ──────────────────
    energy_breakdown = _categorical_breakdown(
        [e for e in app if e["dj_joined"]],
        field="energy_status",
        outcome_field="outcome",
        win_value="CLOSED_WIN",
        loss_value="CLOSED_LOSS",
    )
    n_dj = approved_result["n_dj_joined"]
    lines.append(_render_categorical_block(
        "BLOCK 1: ENERGY STATUS vs OUTCOME (approved stream, DJ-joined only)",
        energy_breakdown,
        n_total=n_dj,
        col_header="Win Rate (N)",
    ))

    # ── Block 2: Kinematic Grade vs Outcome (approved stream) ────────────────
    grade_breakdown = _categorical_breakdown(
        [e for e in app if e["dj_joined"]],
        field="kinematic_grade",
        outcome_field="outcome",
        win_value="CLOSED_WIN",
        loss_value="CLOSED_LOSS",
    )
    lines.append(_render_categorical_block(
        "BLOCK 2: KINEMATIC GRADE vs OUTCOME (approved stream, DJ-joined only)",
        grade_breakdown,
        n_total=n_dj,
        col_header="Win Rate (N)",
    ))

    # ── Block 3: Box Size vs Outcome (approved stream) ───────────────────────
    box_breakdown = _categorical_breakdown(
        [e for e in app if e["box_bucket"] is not None],
        field="box_bucket",
        outcome_field="outcome",
        win_value="CLOSED_WIN",
        loss_value="CLOSED_LOSS",
    )
    n_box = sum(1 for e in app if e["box_bucket"] is not None)
    lines.append(_render_categorical_block(
        "BLOCK 3: BOX SIZE vs OUTCOME (approved stream, bo/bd-populated only)",
        box_breakdown,
        n_total=n_box,
        col_header="Win Rate (N)",
    ))

    # ── Block 4: JEWEL Gate vs Outcome (approved stream) ─────────────────────
    jewel_matched = [e for e in app if e["jewel_joined"] and e["jewel_gate_open"] is not None]
    jewel_unmatched = [e for e in app if not e["jewel_joined"]]

    def _jewel_key(e):
        return "OPEN" if e["jewel_gate_open"] else "CLOSED"

    jewel_breakdown = _categorical_breakdown(
        jewel_matched,
        field="jewel_gate_open",
        outcome_field="outcome",
        win_value="CLOSED_WIN",
        loss_value="CLOSED_LOSS",
    )
    # remap True/False keys to readable labels
    readable_jewel: Dict[str, Dict] = {}
    for k, v in jewel_breakdown.items():
        label = "OPEN" if k is True or k == "True" else ("CLOSED" if k is False or k == "False" else str(k))
        readable_jewel[label] = v
    if jewel_unmatched:
        readable_jewel["NO SNAPSHOT"] = {
            "n": len(jewel_unmatched), "wins": 0, "losses": 0,
            "rate_str": "— (no snapshot)", "label": "—",
        }

    lines.append(_render_categorical_block(
        "BLOCK 4: JEWEL GATE vs OUTCOME (approved stream, snapshot-matched only)",
        readable_jewel,
        n_total=approved_result["n_jewel_joined"],
        col_header="Win Rate (N)",
    ))

    # ── Block 5: Stand-Down Validation ───────────────────────────────────────
    n_sd      = standdown_result["n_scoreable"]
    n_saved   = sum(1 for e in sds if e["outcome_direction_correct"] is False)
    n_overcau = sum(1 for e in sds if e["outcome_direction_correct"] is True)

    lines += [
        f"\n{'─' * 72}",
        f"BLOCK 5: STAND-DOWN VALIDATION  (scoreable stand-downs N={n_sd})",
        f"{'─' * 72}",
        f"  Correct veto — price moved against indicated direction (saved):  "
        f"N={n_saved}  {_pct_with_n(n_saved, n_sd)}",
        f"  Overcautious  — price moved in indicated direction (possible miss): "
        f"N={n_overcau}  {_pct_with_n(n_overcau, n_sd)}",
        f"  Unscoreable   — outcome_direction_correct=NULL: "
        f"N={standdown_result['n_unscoreable']}",
        f"  All results: {_label(n_sd)} (N={n_sd})",
    ]

    # ── Binomial checkpoints (fire only at milestone N) ───────────────────────
    date_range_str = f"{min(all_dates)} → {max(all_dates)}" if all_dates else "unknown"

    approved_checkpoint = run_checkpoint(
        events=app,
        stream_name="approved",
        win_field="outcome",
        win_value="CLOSED_WIN",
        date_range=date_range_str,
    ) if len(app) in MILESTONES else None

    standdown_checkpoint = run_checkpoint(
        events=sds,
        stream_name="standdown",
        win_field="outcome_direction_correct",
        win_value=False,   # correct veto = price moved AGAINST indicated direction
        date_range=date_range_str,
    ) if len(sds) in MILESTONES else None

    if approved_checkpoint:
        lines.append(format_checkpoint_block(approved_checkpoint))
    if standdown_checkpoint:
        lines.append(format_checkpoint_block(standdown_checkpoint))

    # ── Footer ────────────────────────────────────────────────────────────────
    from math import ceil
    from database import SessionLocal, TrialsLog
    db_trials = SessionLocal()
    try:
        trials_count = db_trials.query(TrialsLog).filter(
            TrialsLog.against_n <= n_evaluable
        ).count()
    except Exception:
        trials_count = 0
    finally:
        db_trials.close()

    weeks_to_threshold = ceil(max(0, FINDING_THRESHOLD - n_evaluable) / 6.5)
    lines += [
        "",
        "=" * 72,
        "ACCUMULATION STATUS",
        f"  Finding threshold:    {FINDING_THRESHOLD} resolved evaluable events (PRELIMINARY_SIGNAL tier)",
        f"  Current:              {n_evaluable}  [{_label(n_evaluable)}]",
        f"  Remaining:            {max(0, FINDING_THRESHOLD - n_evaluable)}",
        f"  Accumulation rate:    ~6–7 events/week (observed)",
        f"  Estimated weeks to threshold: ~{weeks_to_threshold}",
        f"  Trials logged against this dataset: {trials_count}",
        "  Re-run this script weekly. The N will climb; tier labels upgrade",
        "  automatically as milestones are crossed.",
        "=" * 72,
    ]

    # ── Flag block ────────────────────────────────────────────────────────────
    subgroup_cells: Dict[str, Dict] = {}
    for e in [e for e in app if e["dj_joined"]]:
        for field in ("energy_status", "kinematic_grade"):
            key = e.get(field) or "UNKNOWN"
            cell_key = f"{field}:{key}"
            if cell_key not in subgroup_cells:
                subgroup_cells[cell_key] = {"n": 0}
            subgroup_cells[cell_key]["n"] += 1

    lines.append(build_flag_block(
        n_current=n_evaluable,
        n_prior=None,
        subgroup_cells=subgroup_cells,
    ))

    return "\n".join(lines)


if __name__ == "__main__":
    print(run_baseline())
