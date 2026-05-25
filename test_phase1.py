# test_phase1.py
# ==============================================================================
# PHASE 1 SUCCESS TEST
# Run this directly: .venv/Scripts/python test_phase1.py
# Does NOT require the full FastAPI server to be running.
# Verifies: _call_agent() fires, agent_run_log is written, cost summary returns.
# ==============================================================================

import os
import sys
import json

# Ensure ANTHROPIC_API_KEY is set
if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    print("Set it with: set ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

print("=" * 60)
print("KABRODA PHASE 1 — COST INFRASTRUCTURE TEST")
print("=" * 60)

# -------------------------------------------------------
# STEP 1: Verify table exists
# -------------------------------------------------------
print("\n[1/4] Checking agent_run_log table...")
from database import SessionLocal, AgentRunLog, Base, engine

Base.metadata.create_all(bind=engine)
db = SessionLocal()
row_count_before = db.query(AgentRunLog).count()
db.close()
print(f"      Table exists. Current row count: {row_count_before}")

# -------------------------------------------------------
# STEP 2: Fire test call through _call_agent()
# -------------------------------------------------------
print("\n[2/4] Firing test call through _call_agent()...")
import agent_core

SYSTEM_PROMPT = (
    "You are a cost-tracking verification agent for the Kabroda trading "
    "intelligence system. Your only function is to confirm that the Phase 1 "
    "cost infrastructure is operational."
)
CONTEXT = (
    "Confirm system status. "
    "Respond with exactly one line: PHASE_1_COST_INFRASTRUCTURE_ONLINE"
)

try:
    response_text = agent_core._call_agent(
        agent_name="infrastructure_test",
        system_prompt=SYSTEM_PROMPT,
        context_text=CONTEXT,
        triggered_by="test_phase1_script",
    )
    print(f"      Agent response: {response_text.strip()}")
except RuntimeError as e:
    print(f"      BUDGET BLOCKED: {e}")
    sys.exit(1)
except Exception as e:
    print(f"      ERROR: {e}")
    sys.exit(1)

# -------------------------------------------------------
# STEP 3: Verify row written to agent_run_log
# -------------------------------------------------------
print("\n[3/4] Verifying agent_run_log entry...")
db = SessionLocal()
row_count_after = db.query(AgentRunLog).count()
latest = db.query(AgentRunLog).order_by(AgentRunLog.id.desc()).first()
db.close()

assert row_count_after == row_count_before + 1, (
    f"Expected {row_count_before + 1} rows, got {row_count_after}"
)

print(f"      New row written. Total rows: {row_count_after}")
print(f"      agent_name:         {latest.agent_name}")
print(f"      model:              {latest.model}")
print(f"      triggered_by:       {latest.triggered_by}")
print(f"      status:             {latest.status}")
print(f"      input_tokens:       {latest.input_tokens}")
print(f"      output_tokens:      {latest.output_tokens}")
print(f"      cache_read_tokens:  {latest.cache_read_tokens}")
print(f"      cache_write_tokens: {latest.cache_write_tokens}")
print(f"      estimated_cost_usd: ${latest.estimated_cost_usd:.6f}")
print(f"      created_at:         {latest.created_at}")

assert latest.status == "SUCCESS", f"Expected SUCCESS, got {latest.status}"
assert latest.input_tokens > 0, "input_tokens should be > 0"
assert latest.output_tokens > 0, "output_tokens should be > 0"
assert latest.estimated_cost_usd > 0, "cost should be > 0"

# -------------------------------------------------------
# STEP 4: Verify get_cost_summary() returns the call
# -------------------------------------------------------
print("\n[4/4] Verifying get_cost_summary() output...")
summary = agent_core.get_cost_summary()

assert summary["ok"] is True, f"get_cost_summary failed: {summary}"
assert summary["today"]["total_usd"] > 0, "Today spend should be > 0"
assert len(summary["last_10_calls"]) > 0, "Should have at least 1 call in history"
assert summary["today"]["budget_usd"] == float(os.getenv("AGENT_DAILY_BUDGET_USD", "5.00"))

last_call = summary["last_10_calls"][0]
print(f"      Today spend:        ${summary['today']['total_usd']:.6f}")
print(f"      Budget:             ${summary['today']['budget_usd']:.2f}")
print(f"      Budget consumed:    {summary['today']['budget_pct']}%")
print(f"      7-day spend:        ${summary['seven_day']['total_usd']:.6f}")
print(f"      Last call in log:   {last_call['agent_name']} / {last_call['status']} / ${last_call['estimated_cost_usd']:.6f}")

# -------------------------------------------------------
# REPORT
# -------------------------------------------------------
print("\n" + "=" * 60)
print("PHASE 1 SUCCESS — ALL CHECKS PASSED")
print("=" * 60)
print()
print("COST SUMMARY (JSON):")
print(json.dumps({
    "today": {
        "total_usd": summary["today"]["total_usd"],
        "budget_usd": summary["today"]["budget_usd"],
        "budget_pct": summary["today"]["budget_pct"],
        "by_agent": summary["today"]["by_agent"],
    },
    "seven_day": {
        "total_usd": summary["seven_day"]["total_usd"],
    },
    "last_call": last_call,
}, indent=2))
print()
print("The /api/agents/cost and /api/agents/test-call endpoints")
print("are wired in main.py and will be live once the server starts.")
print("The admin page at /admin shows the Agent Cost Monitor card.")
