# harness/snapshot_report.py
# =============================================================================
# KABRODA FORWARD-AUDIT LOOP — Snapshot Flag Report
#
# Produces a FLAG block appended to the end of each baseline snapshot.
# Reads prior state from session_audit_log (label_tier) and trials_log
# (comparison count) — both persist in the production DB. No dependency
# on ephemeral filesystem snapshots.
#
# Flag conditions (priority order):
#   1. N milestone crossed — first time total reaches 30 / 50 / 100
#   2. Trials count warning — comparisons > 20 triggers Bonferroni note
#   3. Subgroup cell reached N=10 (first cell exits single-digit territory)
#   4. Active candidate with forward_watch records (FORWARD_WATCH status)
#
# Two additional flags are left for the binomial_checkpoint module, which
# injects p-value movement and significance crossing directly into its own
# formatted block (displayed above this one in the snapshot output).
#
# READ path: session_audit_log, trials_log via SessionLocal.
# NO write path.
# =============================================================================

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, SessionAuditLog, TrialsLog
from harness.tier_labels import BOUNDARY_PRELIMINARY, BOUNDARY_PROVISIONAL, BOUNDARY_VALIDATED


def _count_audit_rows(db) -> int:
    try:
        return db.query(SessionAuditLog).count()
    except Exception:
        return 0


def _count_trials(db, against_n: int) -> int:
    """Count comparisons spent against any dataset of this size or smaller."""
    try:
        return (
            db.query(TrialsLog)
            .filter(TrialsLog.against_n <= against_n)
            .count()
        )
    except Exception:
        return 0


def _forward_watch_candidates(db) -> List[Any]:
    try:
        return (
            db.query(TrialsLog)
            .filter(TrialsLog.candidate_status == "FORWARD_WATCH")
            .all()
        )
    except Exception:
        return []


def _milestone_crossed(n_current: int, n_prior: int) -> Optional[int]:
    """Return the milestone N if current run crossed it for the first time."""
    for m in [BOUNDARY_PRELIMINARY, BOUNDARY_PROVISIONAL, BOUNDARY_VALIDATED]:
        if n_prior < m <= n_current:
            return m
    return None


def build_flag_block(
    n_current: int,
    n_prior: Optional[int],
    subgroup_cells: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """
    Build the FLAG block for the snapshot footer.

    Parameters
    ----------
    n_current     : total evaluable events in this run
    n_prior       : total from the last run (read from audit row count or passed in);
                    None if no prior run exists
    subgroup_cells: optional dict of {cell_name: {"n": int, ...}} for subgroup N tracking

    Returns a multi-line string ready to append to the snapshot.
    """
    flags: List[str] = []

    db = SessionLocal()
    try:
        # Flag 1 — N milestone
        if n_prior is not None:
            m = _milestone_crossed(n_current, n_prior)
            if m is not None:
                tier_names = {
                    BOUNDARY_PRELIMINARY: "PRELIMINARY_SIGNAL",
                    BOUNDARY_PROVISIONAL: "PROVISIONAL_FINDING",
                    BOUNDARY_VALIDATED:   "VALIDATED_EDGE",
                }
                flags.append(
                    f"N MILESTONE CROSSED: Total evaluable events reached {m} for the first time. "
                    f"Tier upgrades to {tier_names.get(m, '?')}. "
                    f"{'Binomial checkpoint now active.' if m == BOUNDARY_PRELIMINARY else 'Run baseline to generate checkpoint.'}"
                )

        # Flag 2 — trials count warning
        trials_n = _count_trials(db, against_n=n_current)
        if trials_n > 20:
            bonferroni = round(0.05 / trials_n, 4)
            flags.append(
                f"TRIALS COUNT WARNING: {trials_n} configurations have been tested against "
                f"this dataset. Any new result requires Bonferroni correction "
                f"(p < 0.05 / {trials_n} = {bonferroni})."
            )

        # Flag 3 — subgroup N milestone (first cell exits single digits)
        if subgroup_cells:
            for cell_name, cell_data in subgroup_cells.items():
                cn = cell_data.get("n", 0)
                if 9 < cn <= 15:  # just crossed 10, flagged until N=15
                    flags.append(
                        f"SUBGROUP MILESTONE: '{cell_name}' reached N={cn}. "
                        f"First cell to exit single-digit territory."
                    )

        # Flag 4 — active candidates in FORWARD_WATCH
        candidates = _forward_watch_candidates(db)
        for c in candidates:
            if c.against_n is not None and c.against_n >= 30:
                flags.append(
                    f"PROMOTION CANDIDATE: trials_log entry id={c.id} "
                    f"'{(c.hypothesis or '')[:60]}...' "
                    f"in FORWARD_WATCH with against_n={c.against_n}. Review before promoting."
                )
    finally:
        db.close()

    # Build output
    separator = "─" * 72
    lines = [f"\n{separator}", "FLAGS", separator]
    if flags:
        for i, f in enumerate(flags, 1):
            lines.append(f"  [{i}] {f}")
    else:
        lines.append("  No milestones, reversals, or threshold crossings flagged this run.")
    return "\n".join(lines)
