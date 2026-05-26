# test_phase3d.py
# Phase 3D integration test — Performance Auditor
# Shows computed stats block before LLM, then the generated performance_note.
# Verifies DB write and agent_run_log cost capture.
# Requires: ANTHROPIC_API_KEY in environment.

import sys
sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime, timedelta

from database import SessionLocal, MacroNarrativeLog, AgentRunLog, init_db
from performance_auditor import run_performance_audit, _collect_stats, _format_stats_block


def _check(condition: bool, msg: str) -> None:
    if not condition:
        print(f"  FAIL: {msg}")
        sys.exit(1)
    print(f"  OK:   {msg}")


def main():
    print("=" * 60)
    print("PHASE 3D — PERFORMANCE AUDITOR TEST")
    print("=" * 60)

    print("\n[1] Initialising database (runs migrations)...")
    init_db()
    print("  Done.")

    # ----------------------------------------------------------------
    print("\n[2] Computing stats — no LLM call yet...")
    cutoff = datetime.utcnow() - timedelta(days=7)
    stats = _collect_stats("BTC/USDT", cutoff)

    print("\n  CampaignLog (last 7 days):")
    for k, v in stats["campaign"].items():
        print(f"    {k:<28} {v}")

    print("\n  DecisionJournal (last 7 days):")
    for k, v in stats["decisions"].items():
        print(f"    {k:<28} {v}")

    print("\n  JEWEL gate analysis (last 7 days):")
    for k, v in stats["jewel"].items():
        print(f"    {k:<28} {v}")

    print("\n  Wave structure (last 7 days):")
    for k, v in stats["wave"].items():
        print(f"    {k:<28} {v}")

    # ----------------------------------------------------------------
    print("\n[3] Stats block sent to LLM:")
    print("-" * 60)
    block = _format_stats_block("BTC/USDT", "2026-05-26", stats)
    print(block)
    print("-" * 60)

    # ----------------------------------------------------------------
    print("\n[4] Running full audit (LLM synthesis)...")
    result = run_performance_audit(
        symbol="BTC/USDT",
        date_key="2026-05-26",
    )

    print(f"\n  Status: {result['status']}")

    if result["status"] == "BUDGET_BLOCKED":
        print(f"  Budget blocked: {result.get('error')}")
        sys.exit(1)

    if result["status"] != "SUCCESS":
        print(f"  ERROR: {result.get('error')}")
        if "performance_note" in result:
            print(f"\n  Generated note (not persisted):\n{result['performance_note']}")
        sys.exit(1)

    # ----------------------------------------------------------------
    print("\n[5] Generated performance_note:")
    print("-" * 60)
    print(result["performance_note"])
    print("-" * 60)

    # ----------------------------------------------------------------
    print("\n[6] Verifying DB write...")
    db = SessionLocal()
    try:
        row_id = result.get("target_row_id")

        if row_id:
            row = db.query(MacroNarrativeLog).filter(MacroNarrativeLog.id == row_id).first()
            _check(row is not None, f"MacroNarrativeLog id={row_id} found")
            _check(row.authored_by == "senior_analyst", f"authored_by = senior_analyst")
            _check(row.performance_note is not None, "performance_note written")
            _check(len(row.performance_note) > 50, "performance_note has substantive content")
            print(f"\n  Written to row id={row_id} | analyst date_key={row.date_key}")
        else:
            print(
                "\n  NOTE: No senior_analyst row in DB — performance_note generated but not persisted.\n"
                "  Expected on a fresh system where Senior Analyst hasn't run yet.\n"
                "  Run the Senior Analyst once and re-run this test to confirm full write path."
            )

        # ----------------------------------------------------------------
        print("\n[7] Verifying agent_run_log cost capture...")
        log = (
            db.query(AgentRunLog)
            .filter(AgentRunLog.agent_name == "performance_auditor")
            .order_by(AgentRunLog.id.desc())
            .first()
        )
        _check(log is not None, "agent_run_log row found")
        _check(log.status == "SUCCESS", f"status = SUCCESS (got {log.status})")
        _check(log.estimated_cost_usd > 0.0, "estimated_cost_usd > 0 (confirms LLM call)")
        _check(log.triggered_by == "2026-05-26", "triggered_by = date_key")

        print(f"\n  agent_name:         {log.agent_name}")
        print(f"  triggered_by:       {log.triggered_by}")
        print(f"  status:             {log.status}")
        print(f"  input_tokens:       {log.input_tokens}")
        print(f"  output_tokens:      {log.output_tokens}")
        print(f"  cache_read_tokens:  {log.cache_read_tokens}")
        print(f"  estimated_cost_usd: ${log.estimated_cost_usd:.6f}")

    finally:
        db.close()

    print("\n" + "=" * 60)
    print("PHASE 3D TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
