# audit_ai.py
# ==============================================================================
# KABRODA COMPONENT 6 EXTENSION — DAILY 4H/1H CHECKS + PER-TRADE "WHY" DIGEST
#
# Companion to the ALREADY-EXISTING harness/ audit ecosystem (query_layer.py,
# join_logic.py, tier_labels.py, binomial_checkpoint.py, baseline.py,
# audit_runner.py) -- that ecosystem is built exclusively around the 15M
# pipeline (CampaignLog.mas_approval_status in APPROVED/STAND_DOWN,
# SessionAuditLog). Confirmed via repo-wide grep: nothing in harness/
# touches session_timeframe or 4H_CANDIDATE/1H_CANDIDATE. This module covers
# that genuinely untouched territory, reusing harness/'s own statistical
# primitives (tier_labels, binomial_checkpoint) rather than reinventing them,
# and writes to the SAME AuditSuggestionLog table harness/audit_runner.py
# already uses (imported, not redefined) under new hypothesis ids continuing
# its H1-H6 numbering.
#
# Two entry points, both called daily from main.py's scheduler:
#
#   build_daily_digest(date_key) -- per-trade WHY, across all three
#     timeframes (15M/1H/4H), reusing fields that already exist on
#     CampaignLog. Nothing invented, only surfaced and formatted. No
#     equivalent exists anywhere in harness/ (baseline.py does categorical
#     breakdowns, not per-trade records).
#
#   run_daily_4h1h_audit() -- three hypotheses in the 4H/1H space (kinematic/
#     energy grade correlation, 4H macro-bias alignment, runner-mechanic
#     shadow-vs-real). Each computes a live win-rate + tier_label() every
#     day (always-on reading), plus fires binomial_checkpoint.run_checkpoint()
#     for real significance testing at N=30/50/100 milestones (writes to the
#     existing TrialsLog -- same audit trail the 15M side already uses).
#
# AUTHORITY CAP: imports only `database`, `harness.tier_labels`,
# `harness.binomial_checkpoint`, and stdlib. No import of gravity_engine /
# trade_structure_analyst / kabroda_mas_flow / anything that mutates trade
# construction. Cannot touch a live parameter even by accident.
# ==============================================================================

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_

from database import SessionLocal, CampaignLog, AuditSuggestionLog
from harness.tier_labels import tier_label
from harness.binomial_checkpoint import run_checkpoint


def _safe_json(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ==============================================================================
# SECTION 1 — DAILY DIGEST (per-trade "why", all three timeframes)
# ==============================================================================

def build_daily_digest(date_key: str) -> Dict[str, Any]:
    """
    Pure fact-surfacing. For every canonical CampaignLog row created OR
    closed on date_key, across 15M/1H/4H, surfaces why it fired using
    already-populated fields -- no computation of anything new.
    """
    db = SessionLocal()
    try:
        day_start = datetime.strptime(date_key, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)

        rows = db.query(CampaignLog).filter(
            CampaignLog.is_canonical == True,
            or_(
                and_(CampaignLog.created_at >= day_start, CampaignLog.created_at < day_end),
                and_(CampaignLog.closed_at >= day_start, CampaignLog.closed_at < day_end),
            ),
        ).all()

        digest: Dict[str, List[Dict[str, Any]]] = {"15m": [], "1h": [], "4h": []}

        for c in rows:
            tf = (c.session_timeframe or "15M").upper()
            bucket = "15m" if tf == "15M" else ("1h" if tf == "1H" else "4h")

            entry: Dict[str, Any] = {
                "id": c.id,
                "symbol": c.symbol,
                "bias": c.bias,
                "entry_price": c.entry_price,
                "stop_loss": c.stop_loss,
                "t1": c.t1, "t2": c.t2, "t3": c.t3,
                "mas_approval_status": c.mas_approval_status,
                "status": c.status,
                "target_hit": c.target_hit,
                "realized_pnl": c.realized_pnl,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
            }

            if bucket == "15m":
                entry["why"] = {
                    "cro_reasoning": c.mas_executive_brief,
                    "structure_reasoning": _safe_json(c.structure_reasoning),
                }
            else:
                entry["why"] = {
                    "macro_bias": c.macro_bias,
                    "kinematic_grade": c.kinematic_grade,
                    "energy_grade": c.energy_grade,
                    "htf_anchor_type": c.htf_anchor_type,
                    "htf_anchor_price": c.htf_anchor_price,
                    "target_logic_version": c.target_logic_version,
                }

            if c.shadow_runner_active:
                entry["shadow_runner"] = {
                    "stop": c.shadow_runner_stop,
                    "leg2_r": c.shadow_runner_leg2_r,
                    "blended_r": c.shadow_runner_blended_r,
                    "exit_reason": c.shadow_runner_exit_reason,
                    "closed_at": c.shadow_runner_closed_at.isoformat() if c.shadow_runner_closed_at else None,
                }

            digest[bucket].append(entry)

        return {
            "date_key": date_key,
            "trades_covered_15m": len(digest["15m"]),
            "trades_covered_1h": len(digest["1h"]),
            "trades_covered_4h": len(digest["4h"]),
            "digest": digest,
        }
    finally:
        db.close()


# ==============================================================================
# SECTION 2 — DAILY 4H/1H HYPOTHESIS CHECKS
# Reuses harness.tier_labels.tier_label() for a live daily reading and
# harness.binomial_checkpoint.run_checkpoint() for real significance testing
# at milestones -- does not reimplement either.
# ==============================================================================

def _win_rate(rows: List[CampaignLog]) -> Optional[float]:
    resolved = [
        r for r in rows
        if r.status in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY") and r.realized_pnl is not None
    ]
    if not resolved:
        return None
    wins = sum(1 for r in resolved if r.realized_pnl > 0)
    return round(wins / len(resolved) * 100, 1)


def _to_events(rows: List[CampaignLog]) -> List[Dict[str, Any]]:
    return [{"win": (r.realized_pnl or 0) > 0} for r in rows if r.status in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY") and r.realized_pnl is not None]


def _upsert_suggestion(db, *, hypothesis_id: str, hypothesis_text: str, n_total: int,
                        n_outcomes: int, n_supporting: int, metric: Optional[float],
                        suggestion_text: str) -> None:
    """Same update-existing-OPEN-row-or-insert pattern harness/audit_runner.py
    already establishes for H1-H6 -- reused here for consistency, not
    reinvented, so both sets of hypotheses behave identically in the ledger."""
    tier = tier_label(n_supporting)
    existing = (
        db.query(AuditSuggestionLog)
        .filter(AuditSuggestionLog.hypothesis_id == hypothesis_id, AuditSuggestionLog.status == "OPEN")
        .order_by(AuditSuggestionLog.id.desc())
        .first()
    )
    if existing:
        existing.consecutive_runs_surfaced = (existing.consecutive_runs_surfaced or 1) + 1
        existing.n_supporting = n_supporting
        existing.actual_win_rate = metric
        existing.tier_label = tier
        existing.suggestion_text = suggestion_text
        existing.sessions_analyzed_n = n_total
        existing.sessions_with_outcomes_n = n_outcomes
        db.commit()
    else:
        db.add(AuditSuggestionLog(
            logged_at=datetime.utcnow(),
            sessions_analyzed_n=n_total,
            sessions_with_outcomes_n=n_outcomes,
            hypothesis_id=hypothesis_id,
            hypothesis_text=hypothesis_text,
            actual_win_rate=metric,
            tier_label=tier,
            n_supporting=n_supporting,
            suggestion_text=suggestion_text,
            consecutive_runs_surfaced=1,
            status="OPEN",
        ))
        db.commit()


def _check_kinematic_energy(db) -> None:
    rows = db.query(CampaignLog).filter(
        CampaignLog.session_timeframe.in_(["4H", "1H"]),
        CampaignLog.is_canonical == True,
    ).all()

    tangled_weak = [r for r in rows if r.kinematic_grade == "TANGLED" and r.energy_grade in ("WEAK", None)]
    other = [r for r in rows if r not in tangled_weak]

    tw_wr, ot_wr = _win_rate(tangled_weak), _win_rate(other)
    tw_events, ot_events = _to_events(tangled_weak), _to_events(other)
    n_supporting = len(tw_events) + len(ot_events)

    suggestion = (
        f"TANGLED+weak-energy 4H/1H setups: {tw_wr}% win (N={len(tw_events)}) vs. all other "
        f"kinematic/energy combos: {ot_wr}% win (N={len(ot_events)})."
        if tw_wr is not None and ot_wr is not None else
        f"Accumulating -- TANGLED+weak-energy N={len(tw_events)}, other-combo N={len(ot_events)}."
    )
    _upsert_suggestion(
        db, hypothesis_id="H7_KINEMATIC_4H1H",
        hypothesis_text="4H/1H candidates with kinematic_grade=TANGLED and energy_grade in (WEAK,NULL) underperform every other combination.",
        n_total=len(rows), n_outcomes=n_supporting, n_supporting=len(tw_events),
        metric=tw_wr, suggestion_text=suggestion,
    )
    # Real significance test on the TANGLED+weak subgroup, fires only at N milestones
    if tw_events:
        run_checkpoint(tw_events, stream_name="4h1h_tangled_weak", win_field="win", win_value=True,
                        date_range=f"through {datetime.utcnow().date()}")


def _check_macro_bias_4h(db) -> None:
    rows = db.query(CampaignLog).filter(
        CampaignLog.session_timeframe == "4H",
        CampaignLog.is_canonical == True,
        CampaignLog.macro_bias.isnot(None),
    ).all()

    aligned = [r for r in rows if (r.bias == "LONG" and r.macro_bias == "BULLISH") or (r.bias == "SHORT" and r.macro_bias == "BEARISH")]
    counter = [r for r in rows if (r.bias == "LONG" and r.macro_bias == "BEARISH") or (r.bias == "SHORT" and r.macro_bias == "BULLISH")]

    a_wr, c_wr = _win_rate(aligned), _win_rate(counter)
    a_events, c_events = _to_events(aligned), _to_events(counter)
    n_supporting = len(a_events) + len(c_events)

    suggestion = (
        f"4H aligned-with-macro-bias: {a_wr}% win (N={len(a_events)}) vs. counter-trend: {c_wr}% win "
        f"(N={len(c_events)}). The 2026-07-06 backtest found counter-trend outperforming on 4H -- "
        f"checking whether that inversion holds live."
        if a_wr is not None and c_wr is not None else
        f"Accumulating -- aligned N={len(a_events)}, counter-trend N={len(c_events)}."
    )
    _upsert_suggestion(
        db, hypothesis_id="H8_MACROBIAS_4H",
        hypothesis_text="4H candidates: does macro_bias alignment correlate with outcome, and does the backtested inversion hold live?",
        n_total=len(rows), n_outcomes=n_supporting, n_supporting=len(a_events),
        metric=a_wr, suggestion_text=suggestion,
    )
    if c_events:
        run_checkpoint(c_events, stream_name="4h_macro_counter_trend", win_field="win", win_value=True,
                        date_range=f"through {datetime.utcnow().date()}")


def _check_runner_mechanic(db) -> None:
    rows = db.query(CampaignLog).filter(
        CampaignLog.shadow_runner_closed_at.isnot(None),
        CampaignLog.is_canonical == True,
    ).all()

    n = len(rows)
    real_avg = blended_avg = None
    if n > 0:
        real_vals = [r.realized_pnl for r in rows if r.realized_pnl is not None]
        blended_vals = [r.shadow_runner_blended_r for r in rows if r.shadow_runner_blended_r is not None]
        if real_vals:
            real_avg = round(sum(real_vals) / len(real_vals), 4)
        if blended_vals:
            blended_avg = round(sum(blended_vals) / len(blended_vals), 4)

    suggestion = (
        f"Real T1-only close: {real_avg:+.4f}R avg vs. shadow runner blended: {blended_avg:+.4f}R avg (N={n})."
        if n > 0 else
        "No shadow runner legs have resolved yet -- accumulating (bounded by the 5d/2d time caps on each leg)."
    )
    _upsert_suggestion(
        db, hypothesis_id="H9_RUNNER",
        hypothesis_text="Does shadow_runner_blended_r beat the real realized_pnl once shadow legs resolve (15M EMA-trail + 4H/1H zone-trail)?",
        n_total=n, n_outcomes=n, n_supporting=n,
        metric=None, suggestion_text=suggestion,
    )


def run_daily_4h1h_audit() -> Dict[str, Any]:
    """Public entry point. Runs all three 4H/1H hypothesis checks and today's
    per-trade digest. Called daily from main.py's scheduler."""
    db = SessionLocal()
    try:
        _check_kinematic_energy(db)
        _check_macro_bias_4h(db)
        _check_runner_mechanic(db)
    finally:
        db.close()

    date_key = datetime.utcnow().strftime("%Y-%m-%d")
    digest = build_daily_digest(date_key)

    db2 = SessionLocal()
    try:
        from database import DailyAuditLog
        db2.add(DailyAuditLog(
            date_key=date_key,
            digest_json=json.dumps(digest["digest"]),
            trades_covered_15m=digest["trades_covered_15m"],
            trades_covered_1h=digest["trades_covered_1h"],
            trades_covered_4h=digest["trades_covered_4h"],
        ))
        db2.commit()
    finally:
        db2.close()

    return digest
