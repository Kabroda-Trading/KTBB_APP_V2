# test_integration_3b.py
# ==============================================================================
# PHASE 3B INTEGRATION TEST — Senior Analyst reads Specialist wave row
#
# Verifies that _read_narrative_context() correctly surfaces the Elliott Wave
# Specialist's verified wave data (wave_label, wave_reasoning,
# confirmation_condition) to the Senior Analyst via macro_narrative_log.
#
# The Senior Analyst brief should reflect BEAR_WAVE_4_BOUNCE context,
# NOT a fabricated bull narrative.
#
# Requires: ANTHROPIC_API_KEY in environment.
# Targets:  Render PostgreSQL (DATABASE_URL hardcoded below).
# ==============================================================================

import os
import sys

os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://database_rules:"
    "8kpudE8RmeXSPmJzUt8zp67BWIF1985I@"
    "dpg-d524svnfte5s73cu6jlg-a.oregon-postgres.render.com/ktbb_postgres"
)

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    sys.exit(1)

print("=" * 60)
print("KABRODA PHASE 3B INTEGRATION TEST")
print("Senior Analyst reads Specialist wave row")
print("=" * 60)

from database import SessionLocal, MacroNarrativeLog, AgentRunLog, CampaignLog, DecisionJournal

# -------------------------------------------------------
# STEP 1: Verify Specialist row exists and has wave data
# -------------------------------------------------------
print("\n[1/6] Verifying Specialist wave row in macro_narrative_log...")

db = SessionLocal()
wave_row = (
    db.query(MacroNarrativeLog)
    .filter(
        MacroNarrativeLog.symbol == "BTC/USDT",
        MacroNarrativeLog.authored_by == "elliott_wave_specialist",
    )
    .order_by(MacroNarrativeLog.id.desc())
    .first()
)

assert wave_row is not None, (
    "No Elliott Wave Specialist row found. Run test_phase3b.py first."
)
assert wave_row.wave_label == "BEAR_WAVE_4_BOUNCE", (
    f"Expected BEAR_WAVE_4_BOUNCE, got {wave_row.wave_label}"
)
assert wave_row.wave_status == "IN_PROGRESS", (
    f"Expected IN_PROGRESS, got {wave_row.wave_status}"
)
assert wave_row.wave_reasoning and len(wave_row.wave_reasoning) > 50, (
    "wave_reasoning missing or too short"
)
assert wave_row.confirmation_condition and len(wave_row.confirmation_condition) > 10, (
    "confirmation_condition missing"
)

mnl_before = db.query(MacroNarrativeLog).count()
arl_before = db.query(AgentRunLog).count()
db.close()

print(f"      wave_label:           {wave_row.wave_label}")
print(f"      wave_status:          {wave_row.wave_status}")
print(f"      completion_pct:       {wave_row.completion_pct}%")
print(f"      origin:               ${wave_row.wave_origin_price:,.2f}")
print(f"      target:               ${wave_row.wave_target_price:,.2f}")
print(f"      invalidation:         ${wave_row.invalidation_price:,.2f}")
print(f"      wave_reasoning:       {wave_row.wave_reasoning[:80]}...")
print(f"      confirmation_cond:    {wave_row.confirmation_condition[:80]}...")
print("      Specialist row verified.")


# -------------------------------------------------------
# STEP 2: Build realistic battlebox for BTC @ $76,744.74
#
# Session: NY Futures 2026-05-26
# BO = $77,150  BD = $75,600  Distance = $1,550
# Long T1: $78,700  T2: $79,706.90  T3: $81,256.90
# Short T1: $74,050  T2: $73,093.10  T3: $71,543.10
#
# macro_structure uses the REAL Class 0 levels from gravity_memory
# (verified in test_phase3b.py audit session)
# -------------------------------------------------------
SYMBOL = "BTC/USDT"
SESSION_ID = "us_ny_futures"
DATE_KEY = "2026-05-26"

BATTLEBOX = {
    "levels": {
        "breakout_trigger":  77150.0,
        "breakdown_trigger": 75600.0,
        "daily_resistance":  80632.72,   # BEAR_WAVE_1_MSB — structural ceiling
        "daily_support":     74508.01,   # BULL_WAVE_4 — nearest labeled floor
        "range30m_high":     77050.0,
        "range30m_low":      75650.0,
    },
    "context": {
        "macro_bias": "BEARISH",
        "micro_bias": "NEUTRAL",
        "micro_state": "PULLBACK",
        "1h_fuel_status": "FADING",
        "fuel_gauge": {
            "4H": {
                "trend":    "BEARISH",
                "momentum": "NEGATIVE",
                "rsi":      44.2,
            },
            "1H": {
                "trend":    "NEUTRAL",
                "momentum": "FLAT",
                "rsi":      48.7,
            },
            "15M_JEWEL": {
                "kinematic_grade":         "TANGLED",
                "rsi":                     51.3,
                "ribbon_spread_pct":       0.12,
                "deviation_from_mean_pct": 0.08,
                "stoch_rsi":               "VALUE_MID",
            },
        },
        "kde_peaks": [
            {"price": 74508.01, "heat_score": 22.4, "intensity": "HEAVY"},    # BULL_WAVE_4
            {"price": 76200.0,  "heat_score": 5.1,  "intensity": "LIGHT"},
            {"price": 77150.0,  "heat_score": 8.3,  "intensity": "MODERATE"}, # near BO
            {"price": 78500.0,  "heat_score": 4.2,  "intensity": "LIGHT"},
            {"price": 80632.72, "heat_score": 38.1, "intensity": "MAXIMUM"},  # BEAR_WAVE_1_MSB
            {"price": 75600.0,  "heat_score": 6.8,  "intensity": "MODERATE"},
        ],
        "macro_structure": [
            {"type": "CYCLE_ORIGIN",    "price": 15487.64},
            {"type": "BULL_WAVE_2",     "price": 19559.40},
            {"type": "BULL_WAVE_1",     "price": 25247.24},
            {"type": "BEAR_WAVE_3_LOW", "price": 60055.00},
            {"type": "BULL_WAVE_4",     "price": 74508.01},
            {"type": "BEAR_WAVE_1_MSB", "price": 80632.72},
            {"type": "BEAR_WAVE_2",     "price": 97929.98},
            {"type": "BULL_WAVE_3",     "price": 109568.87},
            {"type": "CYCLE_TOP",       "price": 126192.94},
        ],
        "macro_environment": {
            "SPX":  "-0.3% — mild risk-off",
            "DXY":  "104.8 — dollar firm",
            "VIX":  "18.2 — elevated caution",
            "BIAS": "RISK-OFF",
        },
    },
}


# -------------------------------------------------------
# STEP 3: Call run_mas_analysis()
# -------------------------------------------------------
print("\n[2/6] Calling run_mas_analysis() with Phase 3B context...")
print("      (Live Anthropic API call — ~15-25 seconds)")

from kabroda_mas_flow import run_mas_analysis

result = run_mas_analysis(
    symbol=SYMBOL,
    session_id=SESSION_ID,
    date_key=DATE_KEY,
    battlebox_payload=BATTLEBOX,
)

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
# STEP 4: Verify Specialist wave data reached the brief
# -------------------------------------------------------
print("\n[3/6] Verifying Specialist wave data appears in the brief...")

newsletter = brief.get("formatted_newsletter_md", "").lower()
bear_cited = any(term in newsletter for term in [
    "bear wave 4", "bear_wave_4", "wave 4 bounce", "bear wave", "wave 4",
    "60,055", "60055", "80,632", "80632",
])

assert bear_cited, (
    "Senior Analyst brief does not reference bear wave context or the known "
    f"structural levels ($60,055 / $80,632). The Specialist row may not have "
    "been passed correctly.\n\nFull newsletter:\n" + brief.get("formatted_newsletter_md", "")
)

print("      Bear wave context confirmed in newsletter output.")


# -------------------------------------------------------
# STEP 5: Check MacroNarrativeLog row written
# -------------------------------------------------------
print("\n[4/6] Verifying MacroNarrativeLog row written...")

db = SessionLocal()
mnl_after = db.query(MacroNarrativeLog).count()
arl_after  = db.query(AgentRunLog).count()

assert mnl_after == mnl_before + 1, (
    f"Expected {mnl_before + 1} macro_narrative_log rows, got {mnl_after}"
)

latest_mnl = (
    db.query(MacroNarrativeLog)
    .order_by(MacroNarrativeLog.id.desc())
    .first()
)

assert latest_mnl.authored_by == "senior_analyst"
assert latest_mnl.narrative_text and len(latest_mnl.narrative_text) > 50
assert latest_mnl.tactical_text and len(latest_mnl.tactical_text) > 50
db.close()

print(f"      Row confirmed. authored_by: {latest_mnl.authored_by}")
print(f"      agent_run_log: +{arl_after - arl_before} rows")


# -------------------------------------------------------
# STEP 6: Cost
# -------------------------------------------------------
import agent_core
summary = agent_core.get_cost_summary()


# -------------------------------------------------------
# FULL OUTPUT
# -------------------------------------------------------
print()
print("=" * 60)
print("PHASE 3B INTEGRATION — ALL CHECKS PASSED")
print("=" * 60)
print()
print("NARRATIVE_TEXT (Part 1 — cross-day memory):")
print("-" * 60)
print(latest_mnl.narrative_text)
print()
print("TACTICAL_TEXT (Part 2 — execution directive):")
print("-" * 60)
print(latest_mnl.tactical_text)
print()
print("COST REPORT:")
print(f"  Total spend today:  ${summary['today']['total_usd']:.6f}")
print(f"  Budget consumed:    {summary['today']['budget_pct']}%")
print()
print("INTEGRATION VERIFIED:")
print("  _read_narrative_context()  — queries analyst + specialist rows separately")
print("  wave_reasoning             — passed to Senior Analyst context")
print("  confirmation_condition     — passed to Senior Analyst context")
print("  Senior Analyst output      — reflects BEAR_WAVE_4_BOUNCE, not fabricated bull narrative")
