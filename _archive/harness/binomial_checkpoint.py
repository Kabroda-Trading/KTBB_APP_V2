# harness/binomial_checkpoint.py
# =============================================================================
# KABRODA FORWARD-AUDIT LOOP — Binomial Checkpoint
#
# Fires at N milestones (30, 50, 100) for each stream independently.
# Runs a one-tailed binomial test against a 50% null. Reports p-value
# alongside win rate and N — never a bare percentage.
#
# Each checkpoint run is logged to trials_log as a BINOMIAL_CHECKPOINT
# entry — because running a checkpoint against the data is itself a
# comparison spent.
#
# Requires scipy. If scipy is unavailable, falls back to a normal
# approximation with a warning.
#
# READ path: event lists from join_logic.py streams.
# WRITE path: trials_log only (via SessionLocal).
# =============================================================================

import os
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, TrialsLog
from harness.tier_labels import tier_label, pct_with_n, BOUNDARY_PRELIMINARY, BOUNDARY_PROVISIONAL, BOUNDARY_VALIDATED

# N milestones at which the checkpoint fires
MILESTONES = [BOUNDARY_PRELIMINARY, BOUNDARY_PROVISIONAL, BOUNDARY_VALIDATED]


def _binomial_p(k: int, n: int, p_null: float = 0.5) -> float:
    """
    One-tailed binomial p-value: P(X >= k | n, p_null).
    Uses scipy if available; falls back to normal approximation.
    """
    try:
        from scipy.stats import binomtest
        result = binomtest(k, n, p_null, alternative="greater")
        return round(result.pvalue, 4)
    except ImportError:
        # Normal approximation (valid when n >= 30)
        import math
        mean = n * p_null
        std = math.sqrt(n * p_null * (1 - p_null))
        if std == 0:
            return 1.0
        z = (k - 0.5 - mean) / std  # continuity correction
        # P(Z >= z) via error function
        p = 0.5 * (1 - math.erf(z / math.sqrt(2)))
        return round(max(0.0, min(1.0, p)), 4)


def _log_to_trials(
    db,
    *,
    stream_name: str,
    n: int,
    wins: int,
    p_value: float,
    accuracy_pct: float,
    date_range: str,
    result_label: str,
) -> None:
    """Write the checkpoint run to trials_log. Non-fatal on error."""
    try:
        hypothesis = (
            f"Binomial checkpoint: is {stream_name} accuracy > 50%? "
            f"One-tailed test at N={n} milestone."
        )
        config = json.dumps({"stream": stream_name, "n_milestone": n, "null_hypothesis": "p=0.50"})
        summary = (
            f"N={n}: {wins}W / {n - wins}L = {accuracy_pct}% "
            f"(p={p_value}, one-tail vs 50%) — {result_label}"
        )
        row = TrialsLog(
            logged_at_utc=datetime.now(timezone.utc),
            test_type="BINOMIAL_CHECKPOINT",
            hypothesis=hypothesis,
            config_json=config,
            against_n=n,
            against_date_range=date_range,
            result_summary=summary,
            result_accuracy_pct=accuracy_pct,
            result_n=n,
            candidate_status="UNDER_REVIEW",
            notes=f"Auto-generated at N={n} milestone for stream '{stream_name}'.",
        )
        db.add(row)
        db.commit()
    except Exception as e:
        print(f"BINOMIAL CHECKPOINT WARNING: could not log to trials_log: {e}")


def run_checkpoint(
    events: List[Dict[str, Any]],
    stream_name: str,
    win_field: str,
    win_value: Any,
    date_range: str = "unknown",
    force_n: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run a binomial checkpoint for a stream at its current N.

    Only fires when N crosses a milestone. Returns None if no milestone applies.

    Parameters
    ----------
    events      : list of event dicts (from join_logic streams)
    stream_name : "approved" | "standdown" — used in report labels
    win_field   : field name in each event dict that holds the outcome
    win_value   : the value of win_field that counts as a "success"
    date_range  : string for the trials_log record
    force_n     : override to force the checkpoint to run at a specific N
                  (used in testing; None = use len(events))

    Returns
    -------
    dict with keys: stream, n, wins, losses, win_rate_pct, p_value,
                    tier, interpretation, fired_at_milestone, result_label
    """
    n = force_n if force_n is not None else len(events)

    # Only fire at milestone N values
    if n not in MILESTONES and force_n is None:
        # Check if we just crossed a milestone (within 1 of a boundary)
        crossed = [m for m in MILESTONES if n == m]
        if not crossed:
            return None

    wins = sum(1 for e in events if e.get(win_field) == win_value)
    losses = n - wins
    accuracy_pct = round(wins / n * 100.0, 1) if n > 0 else 0.0
    p_value = _binomial_p(wins, n)
    tier = tier_label(n)

    # Interpretation
    if p_value < 0.05:
        result_label = f"SIGNIFICANT (p={p_value} < 0.05) — {tier}"
        interpretation = (
            f"Significance threshold crossed (p<0.05, N={n}). "
            f"Still insufficient for multi-regime validation. "
            f"Regime segmentation warranted at N={BOUNDARY_PROVISIONAL}."
        )
    elif p_value < 0.10:
        result_label = f"APPROACHING SIGNIFICANCE (p={p_value}) — {tier}"
        interpretation = (
            f"Approaching significance (p<0.10, N={n}). "
            f"Directional — continue accumulating. Do not act."
        )
    else:
        result_label = f"NOT SIGNIFICANT (p={p_value}) — {tier}"
        interpretation = (
            f"Not significant (p={p_value}, N={n}). "
            f"Result may reflect noise. Continue accumulating."
        )

    result = {
        "stream":             stream_name,
        "n":                  n,
        "wins":               wins,
        "losses":             losses,
        "win_rate_pct":       accuracy_pct,
        "win_rate_str":       pct_with_n(wins, n, label=tier),
        "p_value":            p_value,
        "tier":               tier,
        "result_label":       result_label,
        "interpretation":     interpretation,
        "fired_at_milestone": n,
    }

    # Log to trials_log (non-fatal)
    db = SessionLocal()
    try:
        _log_to_trials(
            db,
            stream_name=stream_name,
            n=n,
            wins=wins,
            p_value=p_value,
            accuracy_pct=accuracy_pct,
            date_range=date_range,
            result_label=result_label,
        )
    finally:
        db.close()

    return result


def format_checkpoint_block(result: Dict[str, Any]) -> str:
    """Format a checkpoint result dict as a human-readable block for snapshot output."""
    if not result:
        return ""
    lines = [
        f"\n{'─' * 72}",
        f"BINOMIAL CHECKPOINT — {result['stream'].upper()} STREAM  (N={result['n']})",
        f"{'─' * 72}",
        f"  Win rate:     {result['win_rate_str']}",
        f"  p-value:      {result['p_value']} (one-tailed, H₀: win rate = 50%)",
        f"  Result:       {result['result_label']}",
        f"  Note:         {result['interpretation']}",
    ]
    return "\n".join(lines)
