# test_phase3b.py
# ==============================================================================
# PHASE 3B SUCCESS TEST — Elliott Wave Specialist
# Run: .venv/Scripts/python test_phase3b.py
# Requires: ANTHROPIC_API_KEY set in environment.
# Reads live gravity_memory from Render DB. Writes to Render macro_narrative_log.
# ==============================================================================

import os
import sys

# --- Set DATABASE_URL to Render BEFORE any database imports ---
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://database_rules:"
    "8kpudE8RmeXSPmJzUt8zp67BWIF1985I@"
    "dpg-d524svnfte5s73cu6jlg-a.oregon-postgres.render.com/ktbb_postgres"
)

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    sys.exit(1)

print("=" * 60)
print("KABRODA PHASE 3B — ELLIOTT WAVE SPECIALIST TEST")
print("=" * 60)

# Database imports AFTER DATABASE_URL is set
from database import SessionLocal, GravityMemory, MacroNarrativeLog, AgentRunLog

# Verified live BTC state (confirmed against War Room + chart, 2026-05-25)
SYMBOL        = "BTC/USDT"
DATE_KEY      = "2026-05-25"
CURRENT_PRICE = 76744.74

# Known correct answers from live gravity_memory (verified in audit session)
EXPECTED_WAVE_LABEL         = "BEAR_WAVE_4_BOUNCE"
EXPECTED_WAVE_ORIGIN_PRICE  = 60055.00   # BEAR_WAVE_3_LOW
EXPECTED_INVALIDATION_PRICE = 80632.72   # BEAR_WAVE_1_MSB


# -------------------------------------------------------
# STEP 1: Verify live gravity_memory has Class 0 data
# -------------------------------------------------------
print("\n[1/7] Verifying Class 0 levels in Render gravity_memory...")
db = SessionLocal()

class0_rows = (
    db.query(GravityMemory)
    .filter(
        GravityMemory.symbol == "BTCUSDT",
        GravityMemory.source == "MACRO_ENGINE_CLASS_0",
        GravityMemory.active == True,
    )
    .order_by(GravityMemory.price.asc())
    .all()
)

assert len(class0_rows) >= 6, (
    f"Expected at least 6 Class 0 rows for BTCUSDT, got {len(class0_rows)}. "
    "Run macro engine on Render first."
)

print(f"      Found {len(class0_rows)} Class 0 rows for BTCUSDT:")
for r in class0_rows:
    print(f"        {r.level_type:<25} ${r.price:>12,.2f}")

# Verify the specific levels we know are there
level_map = {r.level_type: r.price for r in class0_rows}
assert "BEAR_WAVE_3_LOW"  in level_map, "BEAR_WAVE_3_LOW missing from gravity_memory"
assert "BEAR_WAVE_1_MSB"  in level_map, "BEAR_WAVE_1_MSB missing from gravity_memory"
assert "CYCLE_TOP"        in level_map, "CYCLE_TOP missing from gravity_memory"
assert "CYCLE_ORIGIN"     in level_map, "CYCLE_ORIGIN missing from gravity_memory"

assert abs(level_map["BEAR_WAVE_3_LOW"] - 60055.00) < 1.0, (
    f"BEAR_WAVE_3_LOW expected ~$60,055, got ${level_map['BEAR_WAVE_3_LOW']:,.2f}"
)
assert abs(level_map["BEAR_WAVE_1_MSB"] - 80632.72) < 1.0, (
    f"BEAR_WAVE_1_MSB expected ~$80,632.72, got ${level_map['BEAR_WAVE_1_MSB']:,.2f}"
)

print("      Key levels verified: BEAR_WAVE_3_LOW and BEAR_WAVE_1_MSB present and correct.")

# Record baselines
mnl_before = db.query(MacroNarrativeLog).count()
arl_before = db.query(AgentRunLog).count()
db.close()


# -------------------------------------------------------
# STEP 2: Call run_elliott_wave_analysis()
# -------------------------------------------------------
print(f"\n[2/7] Calling run_elliott_wave_analysis() @ ${CURRENT_PRICE:,.2f}...")
print("      (Live Anthropic API call — ~10-15 seconds)")

from elliott_wave_specialist import run_elliott_wave_analysis

result = run_elliott_wave_analysis(
    symbol=SYMBOL,
    current_price=CURRENT_PRICE,
    date_key=DATE_KEY,
)

print(f"      Status returned: {result.get('status')}")
if result.get("status") != "SUCCESS":
    print(f"      ERROR: {result.get('message')}")
    if result.get("raw_response"):
        print(f"      RAW: {result['raw_response'][:500]}")
    sys.exit(1)

wave = result["wave"]
print(f"      wave_label:           {wave.get('wave_label')}")
print(f"      wave_status:          {wave.get('wave_status')}")
print(f"      wave_origin_price:    ${wave.get('wave_origin_price'):,.2f}")
print(f"      wave_target_price:    ${wave.get('wave_target_price'):,.2f}")
print(f"      invalidation_price:   ${wave.get('invalidation_price'):,.2f}")


# -------------------------------------------------------
# STEP 3: Verify wave_label is BEAR_WAVE_4_BOUNCE
# -------------------------------------------------------
print("\n[3/7] Verifying wave identification...")

assert wave.get("wave_label") == EXPECTED_WAVE_LABEL, (
    f"Expected wave_label '{EXPECTED_WAVE_LABEL}', got '{wave.get('wave_label')}'"
)
assert wave.get("wave_status") in ("IN_PROGRESS", "CONFIRMED", "PENDING", "QUESTIONABLE"), (
    f"Invalid wave_status: {wave.get('wave_status')}"
)

# Should be IN_PROGRESS — ZigZag has not yet locked a W4 peak for BTC
assert wave.get("wave_status") == "IN_PROGRESS", (
    f"Expected IN_PROGRESS (W4 peak not yet locked by ZigZag), got {wave.get('wave_status')}"
)

print(f"      wave_label correct:  {wave['wave_label']}")
print(f"      wave_status correct: {wave['wave_status']}")


# -------------------------------------------------------
# STEP 4: Verify structural levels match War Room display
# -------------------------------------------------------
print("\n[4/7] Verifying structural levels match live gravity_memory...")

assert abs(wave.get("wave_origin_price") - EXPECTED_WAVE_ORIGIN_PRICE) < 1.0, (
    f"wave_origin_price should be ~${EXPECTED_WAVE_ORIGIN_PRICE:,.2f} (BEAR_WAVE_3_LOW), "
    f"got ${wave.get('wave_origin_price'):,.2f}"
)
assert abs(wave.get("invalidation_price") - EXPECTED_INVALIDATION_PRICE) < 1.0, (
    f"invalidation_price should be ~${EXPECTED_INVALIDATION_PRICE:,.2f} (BEAR_WAVE_1_MSB), "
    f"got ${wave.get('invalidation_price'):,.2f}"
)
assert abs(wave.get("wave_target_price") - EXPECTED_INVALIDATION_PRICE) < 1.0, (
    f"wave_target_price for BEAR_WAVE_4 should be ~${EXPECTED_INVALIDATION_PRICE:,.2f} "
    f"(BEAR_WAVE_1_MSB ceiling), got ${wave.get('wave_target_price'):,.2f}"
)

print(f"      origin:        ${wave['wave_origin_price']:,.2f}  (matches BEAR_WAVE_3_LOW)")
print(f"      target:        ${wave['wave_target_price']:,.2f}  (matches BEAR_WAVE_1_MSB)")
print(f"      invalidation:  ${wave['invalidation_price']:,.2f}  (matches BEAR_WAVE_1_MSB)")


# -------------------------------------------------------
# STEP 5: Verify EWT rule citation in wave_reasoning
# -------------------------------------------------------
print("\n[5/7] Verifying EWT rule citation in wave_reasoning...")

reasoning = wave.get("wave_reasoning", "")
assert len(reasoning) > 50, "wave_reasoning is too short"

# Must cite the Wave 4 cannot exceed Wave 1 rule
rule_cited = (
    "Wave 4 cannot exceed" in reasoning
    or "wave 4 cannot exceed" in reasoning.lower()
    or "cannot exceed the end of Wave 1" in reasoning
    or "cannot exceed end of Wave 1" in reasoning
)
assert rule_cited, (
    "wave_reasoning must cite 'Wave 4 cannot exceed the end of Wave 1'. "
    f"Got: {reasoning[:300]}"
)

# Must reference the invalidation price
assert "80,632" in reasoning or "80632" in reasoning, (
    f"wave_reasoning must reference the invalidation price $80,632.72. Got: {reasoning[:300]}"
)

print("      EWT rule citation confirmed: 'Wave 4 cannot exceed' present.")
print(f"      wave_reasoning snippet: {reasoning[:150]}...")


# -------------------------------------------------------
# STEP 6: Verify no time projection language
# -------------------------------------------------------
print("\n[6/7] Checking for banned time projection language...")

TIME_BANNED = [
    "in the next", "within the next", "over the next",
    "typically takes", "average duration",
    "within weeks", "within months", "within days",
    "in a few", "in 1-", "in 2-", "in 3-",
    "should complete by", "expect in", "by end of",
    "will take", "takes approximately",
]

full_text = (
    (wave.get("wave_reasoning") or "")
    + (wave.get("confirmation_condition") or "")
).lower()

violations = [phrase for phrase in TIME_BANNED if phrase in full_text]
assert not violations, (
    f"Time projection language found in output: {violations}\n"
    f"Full text: {full_text[:500]}"
)

print("      No time projection language detected.")


# -------------------------------------------------------
# STEP 7: Verify MacroNarrativeLog and AgentRunLog rows written
# -------------------------------------------------------
print("\n[7/7] Verifying database writes...")

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

assert latest_mnl.authored_by == "elliott_wave_specialist"
assert latest_mnl.wave_label  == EXPECTED_WAVE_LABEL
assert latest_mnl.wave_origin_price is not None
assert latest_mnl.wave_target_price is not None
assert latest_mnl.invalidation_price is not None
assert latest_mnl.completion_pct is not None

# New Phase 3B columns — Specialist audit trail
assert latest_mnl.wave_status in ("IN_PROGRESS", "CONFIRMED", "PENDING", "QUESTIONABLE"), (
    f"wave_status not persisted or invalid: {latest_mnl.wave_status}"
)
assert latest_mnl.wave_reasoning and len(latest_mnl.wave_reasoning) > 50, (
    f"wave_reasoning not persisted or too short: {latest_mnl.wave_reasoning!r}"
)
assert latest_mnl.confirmation_condition and len(latest_mnl.confirmation_condition) > 10, (
    f"confirmation_condition not persisted or too short: {latest_mnl.confirmation_condition!r}"
)

assert latest_mnl.narrative_text is None, (
    "narrative_text should be NULL — that's the Senior Analyst's column"
)
assert latest_mnl.tactical_text is None, (
    "tactical_text should be NULL — that's the Senior Analyst's column"
)

# completion_pct is Python-computed, not LLM
expected_completion = round(
    (CURRENT_PRICE - EXPECTED_WAVE_ORIGIN_PRICE)
    / (EXPECTED_INVALIDATION_PRICE - EXPECTED_WAVE_ORIGIN_PRICE)
    * 100, 1
)
assert abs(latest_mnl.completion_pct - expected_completion) < 0.5, (
    f"completion_pct should be ~{expected_completion}%, got {latest_mnl.completion_pct}%"
)

assert arl_after >= arl_before + 1, (
    f"Expected at least {arl_before + 1} agent_run_log rows, got {arl_after}"
)

latest_arl = (
    db.query(AgentRunLog)
    .order_by(AgentRunLog.id.desc())
    .first()
)
assert latest_arl.agent_name == "elliott_wave_specialist"
assert latest_arl.status == "SUCCESS"
assert latest_arl.estimated_cost_usd > 0

db.close()

print(f"      macro_narrative_log: +1 row (now {mnl_after})")
print(f"      agent_run_log:       +{arl_after - arl_before} rows")
print(f"      authored_by:         {latest_mnl.authored_by}")
print(f"      wave_label:          {latest_mnl.wave_label}")
print(f"      wave_status:         {latest_mnl.wave_status}")
print(f"      completion_pct:      {latest_mnl.completion_pct}% (Python-computed)")
print(f"      wave_reasoning:      {(latest_mnl.wave_reasoning or '')[:80]}...")
print(f"      confirmation_cond:   {(latest_mnl.confirmation_condition or '')[:80]}...")
print(f"      narrative_text:      {latest_mnl.narrative_text} (NULL — correct)")
print(f"      cost:                ${latest_arl.estimated_cost_usd:.6f}")


# -------------------------------------------------------
# REPORT
# -------------------------------------------------------
import agent_core
summary = agent_core.get_cost_summary()

print()
print("=" * 60)
print("PHASE 3B SUCCESS — ALL CHECKS PASSED")
print("=" * 60)
print()
print("WAVE ANALYSIS:")
print(f"  wave_label:           {wave['wave_label']}")
print(f"  wave_status:          {wave['wave_status']}")
print(f"  wave_origin_price:    ${wave['wave_origin_price']:,.2f}")
print(f"  wave_target_price:    ${wave['wave_target_price']:,.2f}")
print(f"  invalidation_price:   ${wave['invalidation_price']:,.2f}")
print(f"  completion_pct:       {latest_mnl.completion_pct}%")
print()
print("WAVE REASONING (full):")
print(wave["wave_reasoning"])
print()
print("CONFIRMATION CONDITION:")
print(wave["confirmation_condition"])
print()
print("COST REPORT (today):")
print(f"  Total spend today:    ${summary['today']['total_usd']:.6f}")
print(f"  Budget consumed:      {summary['today']['budget_pct']}%")
print()
print("PHASE 3B FUNCTIONS VERIFIED:")
print("  run_elliott_wave_analysis()  — wave identified + DB written")
print("  _read_class0_levels()        — live Render gravity_memory read")
print("  _write_wave_log()            — MacroNarrativeLog row written")
print("  completion_pct               — Python-computed, not LLM")
print("  wave_status                  — persisted to DB")
print("  wave_reasoning               — persisted to DB (audit trail)")
print("  confirmation_condition       — persisted to DB")
print("  EWT rule citation            — Wave 4 rule present in reasoning")
print("  Time projection ban          — No violations detected")
print()
print("Ready for Phase 3B approval.")
print()
print("REMINDER: Rotate the Render database password after this session.")
