# harness/audit_runner.py
# =============================================================================
# KABRODA AUDIT-AI — Weekly Learning Ledger
#
# Runs 6 pre-defined hypotheses against session_audit_log data.
# Outputs a Markdown brief to system_audit_log.
# Logs to audit_suggestion_log only when N_supporting >= 30 per hypothesis.
#
# AUTHORITY CAP: This script WRITES TO audit_suggestion_log AND system_audit_log
# ONLY. It never modifies live system parameters, prompts, thresholds, or rules.
# Owner reviews weekly output and decides whether to act on any suggestion.
# Suggestion with consecutive_runs_surfaced >= 3 AND PROVISIONAL_FINDING tier
# (N=100+) is the minimum bar for even discussing a live change.
#
# Run standalone:    python harness/audit_runner.py
# Run via API:       POST /api/admin/run-audit  (admin auth required)
# =============================================================================

import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, SessionAuditLog, AuditSuggestionLog, SystemAuditLog

MIN_N_FOR_SUGGESTION = 30


def _tier_label(n: int) -> str:
    if n < 30:
        return "DIRECTIONAL_OBSERVATION"
    if n < 50:
        return "PRELIMINARY_SIGNAL"
    if n < 100:
        return "PROVISIONAL_FINDING"
    return "VALIDATED_EDGE"


def _win_rate(rows: list) -> Optional[float]:
    wins   = sum(1 for r in rows if r.outcome_type == "CLOSED_WIN")
    losses = sum(1 for r in rows if r.outcome_type == "CLOSED_LOSS")
    total  = wins + losses
    return wins / total if total > 0 else None


def _stand_down_correct_rate(rows: list) -> Optional[float]:
    """
    STAND_DOWN 'correct' = the system was right to sit out.
    Defined as: outcome_type == STAND_DOWN_SAVED
                OR (outcome_direction_correct IS False — price didn't move profitably).
    """
    resolved = [r for r in rows if r.outcome_type is not None]
    if not resolved:
        return None
    correct = sum(
        1 for r in resolved
        if r.outcome_type == "STAND_DOWN_SAVED"
        or r.outcome_direction_correct is False
    )
    return correct / len(resolved)


def _generate_suggestion(hyp_id: str, metric: float, n: int, param_label: str) -> str:
    tier = _tier_label(n)
    pct  = f"{metric * 100:.1f}%"
    if hyp_id == "H1":
        verdict = "CAUTION" if metric < 0.40 else "MAINTAIN"
        note    = ("Below 40% — consider tighter sizing in DAILY_BEAR. Requires PROVISIONAL_FINDING (N=100) to act."
                   if metric < 0.40 else "Acceptable hit rate in DAILY_BEAR. No change indicated.")
        return f"{verdict} [{tier}, N={n}]: APPROVED trades in DAILY_BEAR regime hit T1 at {pct}. {note}"
    if hyp_id == "H2":
        quality = "strong" if metric > 0.70 else "weak"
        note    = ("Veto is working — price confirmed the stand-down." if metric > 0.70
                   else "Veto may be overcautious. Monitor for false negatives.")
        return f"OBSERVATION [{tier}, N={n}]: STAND_DOWN when weekly 200 SMA untested shows {pct} correctness ({quality} signal). {note}"
    if hyp_id == "H3":
        quality = "strong" if metric > 0.70 else "below expectation"
        return (f"OBSERVATION [{tier}, N={n}]: STAND_DOWN (4H below 200 SMA + TANGLED) shows {pct} correctness ({quality}). "
                f"{'Current rule is justified.' if metric > 0.70 else 'Rule may be overcautious here. Monitor.'}")
    if hyp_id == "H4":
        verdict = "WARNING" if metric < 0.40 else "NOTE"
        note    = ("High failure rate — WEAK 1H ADX is a meaningful risk signal. Consider soft veto at N=100+."
                   if metric < 0.40 else "Hit rate acceptable despite WEAK 1H ADX.")
        return f"{verdict} [{tier}, N={n}]: APPROVED trades with WEAK 1H ADX hit T1 at {pct}. {note}"
    if hyp_id == "H5":
        note = ("Negative 4H momentum underperforms. Pattern worth watching."
                if metric < 0.45 else "Acceptable win rate despite negative 4H MACD hist.")
        return f"OBSERVATION [{tier}, N={n}]: APPROVED trades with negative 4H MACD hist hit T1 at {pct}. {note}"
    if hyp_id == "H6":
        note = ("TF misalignment is a meaningful risk factor." if metric < 0.45
                else "TF disagreement not strongly predictive here yet.")
        return f"OBSERVATION [{tier}, N={n}]: APPROVED trades where 1H/4H trend disagrees hit T1 at {pct}. {note}"
    return f"OBSERVATION [{tier}, N={n}]: {param_label} — metric {pct}."


def _upsert_suggestion(
    db,
    hyp_id: str,
    hyp_text: str,
    n_total: int,
    n_outcomes: int,
    n_supporting: int,
    metric: float,
    tier: str,
    param_label: str,
    suggestion_text: str,
) -> None:
    """Increment consecutive_runs_surfaced on existing OPEN row, or insert new."""
    existing = (
        db.query(AuditSuggestionLog)
        .filter(AuditSuggestionLog.hypothesis_id == hyp_id,
                AuditSuggestionLog.status == "OPEN")
        .order_by(AuditSuggestionLog.id.desc())
        .first()
    )
    if existing:
        existing.consecutive_runs_surfaced = (existing.consecutive_runs_surfaced or 1) + 1
        existing.n_supporting             = n_supporting
        existing.actual_win_rate          = metric
        existing.tier_label               = tier
        existing.suggestion_text          = suggestion_text
        existing.sessions_analyzed_n      = n_total
        existing.sessions_with_outcomes_n = n_outcomes
        db.commit()
        print(f"[AUDIT-AI] {hyp_id} updated (run #{existing.consecutive_runs_surfaced})")
    else:
        row = AuditSuggestionLog(
            logged_at=datetime.now(timezone.utc),
            sessions_analyzed_n=n_total,
            sessions_with_outcomes_n=n_outcomes,
            hypothesis_id=hyp_id,
            hypothesis_text=hyp_text,
            current_param_label=param_label,
            actual_win_rate=metric,
            tier_label=tier,
            n_supporting=n_supporting,
            suggestion_text=suggestion_text,
            consecutive_runs_surfaced=1,
            status="OPEN",
        )
        db.add(row)
        db.commit()
        print(f"[AUDIT-AI] {hyp_id} first logged (N={n_supporting})")


# ---------------------------------------------------------------------------
# Six pre-defined hypotheses — mechanistically grounded, not combinatorial.
# Each is a tuple: (id, slug, filter_fn, metric_fn, text, param_label)
# ---------------------------------------------------------------------------
def _build_hypotheses():
    return [
        (
            "H1",
            "daily_bear_approved",
            lambda r: (r.daily_21ema_direction == "SLOPING_DOWN"
                       and getattr(r, "daily_200sma_position", None) == "BELOW"
                       and r.approval_status == "APPROVED"),
            _win_rate,
            ("When daily_21ema_direction=SLOPING_DOWN AND daily_200sma_position=BELOW, "
             "what fraction of APPROVED sessions hit T1?"),
            "APPROVED trades in DAILY_BEAR regime",
        ),
        (
            "H2",
            "weekly_below_no_test_standdown",
            lambda r: (r.weekly_200sma_position == "BELOW"
                       and (r.weekly_200sma_test_count or 0) == 0
                       and r.approval_status == "STAND_DOWN"),
            _stand_down_correct_rate,
            ("When weekly_200sma_position=BELOW AND weekly_200sma_test_count=0, "
             "what is the STAND_DOWN correctness rate?"),
            "STAND_DOWN when weekly 200 SMA untested",
        ),
        (
            "H3",
            "4h_below_tangled_standdown",
            lambda r: (r.tf4h_200sma_position == "BELOW"
                       and r.kinematic_grade == "TANGLED"
                       and r.approval_status == "STAND_DOWN"),
            _stand_down_correct_rate,
            ("When tf4h_200sma_position=BELOW AND kinematic_grade=TANGLED, "
             "was STAND_DOWN the correct call?"),
            "STAND_DOWN when 4H below 200 SMA and TANGLED",
        ),
        (
            "H4",
            "1h_adx_weak_approved",
            lambda r: (getattr(r, "tf1h_adx_strength", None) == "WEAK"
                       and r.approval_status == "APPROVED"),
            _win_rate,
            ("When tf1h_adx_strength=WEAK at lock time, "
             "what fraction of APPROVED trades hit T1?"),
            "APPROVED when 1H ADX is WEAK",
        ),
        (
            "H5",
            "4h_macd_neg_approved",
            lambda r: (getattr(r, "tf4h_macd_hist", None) is not None
                       and (getattr(r, "tf4h_macd_hist", 0) or 0) < 0
                       and r.approval_status == "APPROVED"),
            _win_rate,
            ("When tf4h_macd_hist < 0 (negative 4H momentum) and 15M was APPROVED, "
             "what was the outcome distribution?"),
            "APPROVED when 4H MACD histogram is negative",
        ),
        (
            "H6",
            "1h_4h_trend_disagree_approved",
            lambda r: (getattr(r, "tf1h_trend", None) is not None
                       and getattr(r, "tf4h_trend", None) is not None
                       and getattr(r, "tf1h_trend", "") != getattr(r, "tf4h_trend", "")
                       and r.approval_status == "APPROVED"),
            _win_rate,
            ("When 1H trend disagrees with 4H trend and 15M was APPROVED, "
             "what is the win rate?"),
            "APPROVED when 1H and 4H trend alignment breaks",
        ),
    ]


def run_audit() -> str:
    """
    Run all 6 hypotheses. Returns the Markdown brief string.
    Also writes suggestions to audit_suggestion_log when N >= 30.
    """
    db = SessionLocal()
    try:
        all_rows          = db.query(SessionAuditLog).all()
        rows_with_outcomes = [r for r in all_rows if r.outcome_type is not None]

        n_total    = len(all_rows)
        n_outcomes = len(rows_with_outcomes)
        today_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tier       = _tier_label(n_outcomes)

        lines = []
        lines.append(f"# AUDIT-AI WEEKLY LEDGER — {today_str}")
        lines.append(f"Sessions analyzed: **{n_total}** | Sessions with outcomes: **{n_outcomes}**")
        lines.append(
            f"Tier: **{tier}**"
            + (" — N<30, all readings below suggestion threshold" if n_outcomes < 30 else "")
        )
        lines.append("")

        any_logged = False

        for hyp_id, slug, filter_fn, metric_fn, hyp_text, param_label in _build_hypotheses():
            subset = [r for r in rows_with_outcomes if filter_fn(r)]
            n_sub  = len(subset)
            metric = metric_fn(subset) if subset else None

            metric_str  = f"{metric * 100:.1f}%" if metric is not None else "no data"
            status_str  = (f"INSUFFICIENT (N={n_sub}, no suggestion logged)" if n_sub < MIN_N_FOR_SUGGESTION
                           else f"N={n_sub}, {_tier_label(n_sub)}")
            lines.append(f"**{hyp_id}** ({slug}): {metric_str} — {status_str}")

            if n_sub >= MIN_N_FOR_SUGGESTION and metric is not None:
                any_logged = True
                suggestion_text = _generate_suggestion(hyp_id, metric, n_sub, param_label)
                _upsert_suggestion(
                    db=db,
                    hyp_id=hyp_id,
                    hyp_text=hyp_text,
                    n_total=n_total,
                    n_outcomes=n_outcomes,
                    n_supporting=n_sub,
                    metric=metric,
                    tier=_tier_label(n_sub),
                    param_label=param_label,
                    suggestion_text=suggestion_text,
                )

        lines.append("")
        if not any_logged:
            lines.append(
                f"No suggestions logged this run. Accumulating data. "
                f"First threshold: N={MIN_N_FOR_SUGGESTION} resolved outcomes per hypothesis. "
                f"Current total: {n_outcomes} outcomes."
            )
            lines.append(
                "Re-run after 10 additional sessions with outcomes, or weekly — whichever comes first."
            )
        else:
            lines.append("One or more suggestions logged. Review audit_suggestion_log before acting.")
            lines.append(
                "Minimum bar for a live change: consecutive_runs_surfaced ≥ 3 "
                "AND tier = PROVISIONAL_FINDING (N≥100)."
            )

        return "\n".join(lines)
    finally:
        db.close()


def write_brief_to_system_log(brief_md: str) -> None:
    """Append the weekly brief to system_audit_log as a new row."""
    db = SessionLocal()
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = SystemAuditLog(
            symbol="BTC/USDT",
            date_key=today_str,
            audit_md=brief_md,
        )
        db.add(row)
        db.commit()
        print(f"[AUDIT-AI] Brief written to system_audit_log ({today_str})")
    except Exception as e:
        print(f"[AUDIT-AI] ERROR writing brief to system_audit_log: {e}")
    finally:
        db.close()


def main() -> str:
    """Entry point — returns the brief string for API callers."""
    print("[AUDIT-AI] Starting weekly ledger run...")
    brief = run_audit()
    print(brief)
    write_brief_to_system_log(brief)
    print("[AUDIT-AI] Done.")
    return brief


if __name__ == "__main__":
    main()
