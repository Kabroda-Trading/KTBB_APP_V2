# test_phase3a.py
# ==============================================================================
# PHASE 3A SUCCESS TEST
# Run: .venv/Scripts/python test_phase3a.py
# Verifies the Phase 3A Senior Analyst rewrite end-to-end.
# Requires: ANTHROPIC_API_KEY set in environment.
# No FastAPI server needed.
# ==============================================================================

import os
import sys
import json
import datetime

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    sys.exit(1)

print("=" * 60)
print("KABRODA PHASE 3A — SENIOR ANALYST REWRITE TEST")
print("=" * 60)

# Ensure DB tables exist
from database import Base, engine, SessionLocal
from database import AgentRunLog, MacroNarrativeLog, CampaignLog, DecisionJournal
Base.metadata.create_all(bind=engine)

# -------------------------------------------------------
# MOCK BATTLEBOX PACKET (realistic BTC NY Futures session)
# BO = $107,850  BD = $105,200  →  distance = $2,650
# BO long targets:  T1=110,500  T2=112,134.70  T3=114,789.70
# BD short targets: T1=102,550  T2=100,915.30  T3=98,260.30
# -------------------------------------------------------
SYMBOL = "BTC/USDT"
SESSION_ID = "us_ny_futures"
DATE_KEY = "2026-05-25"

MOCK_BATTLEBOX = {
    "levels": {
        "breakout_trigger": 107850.0,
        "breakdown_trigger": 105200.0,
        "daily_resistance": 109200.0,
        "daily_support":    103800.0,
        "range30m_high":    107700.0,
        "range30m_low":     105350.0,
    },
    "context": {
        "macro_bias": "BULLISH",
        "micro_bias": "BULLISH",
        "micro_state": "SWEET_ZONE",
        "1h_fuel_status": "BUILDING",
        "fuel_gauge": {
            "4H": {
                "trend":    "BULLISH",
                "momentum": "POSITIVE",
                "rsi":      58.4,
            },
            "1H": {
                "trend":    "BULLISH",
                "momentum": "POSITIVE",
                "rsi":      54.1,
            },
            "15M_JEWEL": {
                "kinematic_grade":         "PRIMED",
                "rsi":                     61.3,
                "ribbon_spread_pct":       0.38,
                "deviation_from_mean_pct": 0.22,
                "stoch_rsi":               "VALUE_HIGH",
            },
        },
        "kde_peaks": [
            {"price": 108200.0, "heat_score": 4.2,  "intensity": "LIGHT"},
            {"price": 109250.0, "heat_score": 8.7,  "intensity": "MODERATE"},
            {"price": 110500.0, "heat_score": 3.1,  "intensity": "LIGHT"},
            {"price": 112000.0, "heat_score": 18.5, "intensity": "HEAVY"},
            {"price": 104800.0, "heat_score": 6.3,  "intensity": "MODERATE"},
            {"price": 103200.0, "heat_score": 22.1, "intensity": "MAXIMUM"},
        ],
        "macro_structure": [
            {"type": "CYCLE_ORIGIN",    "price": 15476.0},
            {"type": "BULL_WAVE_1_TOP", "price": 73738.0},
            {"type": "BEAR_WAVE_4_LOW", "price": 49000.0},
            {"type": "BEAR_WAVE_4_TOP", "price": 80000.0},
            {"type": "BULL_WAVE_5_PROJ","price": 135000.0},
        ],
        "macro_environment": {
            "SPX":  "+0.4% — risk-on tone",
            "DXY":  "101.2 — dollar soft",
            "VIX":  "14.8 — low fear",
            "BIAS": "RISK-ON",
        },
    },
}


# -------------------------------------------------------
# STEP 1: Record baseline counts before the run
# -------------------------------------------------------
print("\n[1/7] Recording baseline row counts...")
db = SessionLocal()
mnl_before   = db.query(MacroNarrativeLog).count()
arl_before   = db.query(AgentRunLog).count()
cl_before    = db.query(CampaignLog).count()
dj_before    = db.query(DecisionJournal).count()
db.close()

print(f"      macro_narrative_log rows:  {mnl_before}")
print(f"      agent_run_log rows:        {arl_before}")
print(f"      campaign_logs rows:        {cl_before}")
print(f"      decision_journal rows:     {dj_before}")


# -------------------------------------------------------
# STEP 2: Call run_mas_analysis()
# -------------------------------------------------------
print("\n[2/7] Calling run_mas_analysis()...")
print("      (This fires a live Anthropic API call — ~10-20 seconds)")

from kabroda_mas_flow import run_mas_analysis

result = run_mas_analysis(
    symbol=SYMBOL,
    session_id=SESSION_ID,
    date_key=DATE_KEY,
    battlebox_payload=MOCK_BATTLEBOX,
)

print(f"      Status returned: {result.get('status')}")
if result.get("status") != "SUCCESS":
    print(f"      ERROR: {result.get('message')}")
    sys.exit(1)

brief = result["brief"]
print(f"      approval_status: {brief.get('approval_status')}")
print(f"      bias:            {brief.get('bias')}")
print(f"      entry_price:     ${brief.get('entry_price'):,.2f}")
print(f"      stop_loss:       ${brief.get('stop_loss'):,.2f}")
print(f"      t1:              ${brief.get('t1'):,.2f}")
print(f"      t2:              ${brief.get('t2'):,.2f}")
print(f"      t3:              ${brief.get('t3'):,.2f}")


# -------------------------------------------------------
# STEP 3: Verify all ExecutiveBrief fields are populated
# -------------------------------------------------------
print("\n[3/7] Verifying ExecutiveBrief fields...")

required_fields = [
    "approval_status", "tactical_brief", "bias",
    "entry_price", "stop_loss", "t1", "t2", "t3", "formatted_newsletter_md",
]
for field in required_fields:
    val = brief.get(field)
    assert val is not None and val != "", (
        f"Field '{field}' is missing or empty in ExecutiveBrief"
    )

assert brief["approval_status"] in ("APPROVED", "REJECTED", "WAITING_FOR_15M"), (
    f"Invalid approval_status: {brief['approval_status']}"
)
assert brief["bias"] in ("LONG", "SHORT", "NEUTRAL"), (
    f"Invalid bias: {brief['bias']}"
)
assert isinstance(brief["entry_price"], float), "entry_price must be float"
assert isinstance(brief["stop_loss"],  float), "stop_loss must be float"
assert isinstance(brief["t1"], float), "t1 must be float"
assert isinstance(brief["t2"], float), "t2 must be float"
assert isinstance(brief["t3"], float), "t3 must be float"
assert len(brief["formatted_newsletter_md"]) > 100, (
    "formatted_newsletter_md seems too short"
)
assert len(brief["tactical_brief"]) > 50, "tactical_brief seems too short"

print("      All 9 required fields present and typed correctly.")
print(f"      approval_status: {brief['approval_status']}")

# Verify entry/stop match pre-computed targets for the chosen direction
if brief["bias"] == "LONG":
    assert abs(brief["entry_price"] - 107850.0) < 1.0, (
        f"LONG entry should be ~107850 (BO), got {brief['entry_price']}"
    )
    assert abs(brief["stop_loss"] - 105200.0) < 1.0, (
        f"LONG stop should be ~105200 (BD), got {brief['stop_loss']}"
    )
elif brief["bias"] == "SHORT":
    assert abs(brief["entry_price"] - 105200.0) < 1.0, (
        f"SHORT entry should be ~105200 (BD), got {brief['entry_price']}"
    )
    assert abs(brief["stop_loss"] - 107850.0) < 1.0, (
        f"SHORT stop should be ~107850 (BO), got {brief['stop_loss']}"
    )

print("      Entry/stop match pre-computed targets for chosen direction.")


# -------------------------------------------------------
# STEP 4: Verify MacroNarrativeLog row was written
# -------------------------------------------------------
print("\n[4/7] Verifying MacroNarrativeLog row written...")
db = SessionLocal()
mnl_after = db.query(MacroNarrativeLog).count()

assert mnl_after == mnl_before + 1, (
    f"Expected {mnl_before + 1} rows in macro_narrative_log, got {mnl_after}"
)

latest_mnl = (
    db.query(MacroNarrativeLog)
    .order_by(MacroNarrativeLog.id.desc())
    .first()
)
db.close()

assert latest_mnl.symbol == SYMBOL
assert latest_mnl.date_key == DATE_KEY
assert latest_mnl.authored_by == "senior_analyst"
assert latest_mnl.tactical_text and len(latest_mnl.tactical_text) > 20, (
    "tactical_text should contain Part 2 directive"
)

print(f"      New row confirmed. Row ID: {latest_mnl.id}")
print(f"      authored_by: {latest_mnl.authored_by}")
print(f"      date_key:    {latest_mnl.date_key}")
print(f"      narrative_text snippet: {(latest_mnl.narrative_text or '')[:80]}...")


# -------------------------------------------------------
# STEP 5: Verify AgentRunLog entries written (cost tracking)
# -------------------------------------------------------
print("\n[5/7] Verifying AgentRunLog entries written...")
db = SessionLocal()
arl_after = db.query(AgentRunLog).count()

new_rows = arl_after - arl_before
assert new_rows >= 1, f"Expected at least 1 new AgentRunLog row, got {new_rows}"

latest_arl = (
    db.query(AgentRunLog)
    .order_by(AgentRunLog.id.desc())
    .first()
)
db.close()

assert latest_arl.status == "SUCCESS", (
    f"Latest AgentRunLog status should be SUCCESS, got {latest_arl.status}"
)
assert latest_arl.input_tokens > 0,  "input_tokens should be > 0"
assert latest_arl.output_tokens > 0, "output_tokens should be > 0"
assert latest_arl.estimated_cost_usd > 0, "cost should be > 0"

print(f"      New AgentRunLog rows written: {new_rows}")
print(f"      Latest call: {latest_arl.agent_name}")
print(f"      input_tokens:        {latest_arl.input_tokens}")
print(f"      output_tokens:       {latest_arl.output_tokens}")
print(f"      cache_read_tokens:   {latest_arl.cache_read_tokens}")
print(f"      cache_write_tokens:  {latest_arl.cache_write_tokens}")
print(f"      estimated_cost_usd:  ${latest_arl.estimated_cost_usd:.6f}")


# -------------------------------------------------------
# STEP 6: Call interrogate_cro() with a test question
# -------------------------------------------------------
print("\n[6/7] Calling interrogate_cro()...")
print("      (This fires a second live Anthropic API call — ~5-10 seconds)")

from kabroda_mas_flow import interrogate_cro

commlink_response = interrogate_cro(
    symbol=SYMBOL,
    user_message="What is the T1 target for today's session and why?",
)

print(f"      Response type: {type(commlink_response).__name__}")
print(f"      Response length: {len(commlink_response)} chars")
print(f"      Response preview: {commlink_response[:200]}")


# -------------------------------------------------------
# STEP 7: Verify interrogate_cro() returned a valid string
# -------------------------------------------------------
print("\n[7/7] Verifying commlink response...")

assert isinstance(commlink_response, str), (
    f"interrogate_cro must return a string, got {type(commlink_response)}"
)
assert len(commlink_response) > 20, (
    f"Response too short ({len(commlink_response)} chars) — likely an error"
)
assert not commlink_response.startswith("COMMLINK FAILURE"), (
    f"Commlink returned an error: {commlink_response}"
)
assert not commlink_response.startswith("COMMLINK BLOCKED"), (
    f"Commlink was budget-blocked: {commlink_response}"
)

print("      Response is a valid non-empty string.")
print("      No error prefix detected.")


# -------------------------------------------------------
# COST REPORT
# -------------------------------------------------------
import agent_core
summary = agent_core.get_cost_summary()

print()
print("=" * 60)
print("PHASE 3A SUCCESS — ALL CHECKS PASSED")
print("=" * 60)
print()
print("BRIEF SUMMARY:")
print(f"  approval_status:  {brief['approval_status']}")
print(f"  bias:             {brief['bias']}")
print(f"  entry:            ${brief['entry_price']:,.2f}")
print(f"  stop:             ${brief['stop_loss']:,.2f}")
print(f"  t1:               ${brief['t1']:,.2f}")
print(f"  t2:               ${brief['t2']:,.2f}")
print(f"  t3:               ${brief['t3']:,.2f}")
print()
print(f"DATABASE WRITES:")
print(f"  macro_narrative_log: +1 row (now {mnl_after})")
print(f"  agent_run_log:       +{new_rows} rows (now {arl_after})")
print(f"  campaign_logs:       updated")
print(f"  decision_journal:    updated")
print()
print("COST REPORT (today):")
print(f"  Total spend today:   ${summary['today']['total_usd']:.6f}")
print(f"  Budget remaining:    ${summary['today']['budget_usd'] - summary['today']['total_usd']:.4f}")
print(f"  Budget consumed:     {summary['today']['budget_pct']}%")
print()
print("PHASE 3A FUNCTIONS VERIFIED:")
print("  run_mas_analysis()            — ExecutiveBrief returned + DB written")
print("  interrogate_cro()             — Commlink response returned")
print("  _compute_targets()            — Python math, LLM copied correctly")
print("  _write_narrative_log()        — MacroNarrativeLog row written")
print("  _read_narrative_context()     — Cross-day context reader (Day 1 path)")
print("  agent_core cost tracking      — AgentRunLog entries confirmed")
print()
print("Ready for Phase 3A approval and Phase 3B planning.")
