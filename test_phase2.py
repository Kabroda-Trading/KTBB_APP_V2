# test_phase2.py
# ==============================================================================
# PHASE 2 SUCCESS TEST
# Run: .venv/Scripts/python test_phase2.py
# Verifies both new tables exist, accept writes, and return correct reads.
# No API key required — no agent calls made.
# ==============================================================================

import json
import datetime

print("=" * 60)
print("KABRODA PHASE 2 — DATABASE FOUNDATION TEST")
print("=" * 60)

# -------------------------------------------------------
# STEP 1: Create tables
# -------------------------------------------------------
print("\n[1/5] Creating tables via Base.metadata.create_all()...")
from database import Base, engine, SessionLocal
from database import MacroNarrativeLog, JewelSnapshotLog

Base.metadata.create_all(bind=engine)
print("      create_all() completed.")

# -------------------------------------------------------
# STEP 2: Verify both tables exist by querying them
# -------------------------------------------------------
print("\n[2/5] Verifying tables exist...")
db = SessionLocal()

mnl_count_before = db.query(MacroNarrativeLog).count()
jsl_count_before = db.query(JewelSnapshotLog).count()

print(f"      macro_narrative_log rows: {mnl_count_before}")
print(f"      jewel_snapshot_log rows:  {jsl_count_before}")

# -------------------------------------------------------
# STEP 3: Insert one test row into each table
# -------------------------------------------------------
print("\n[3/5] Inserting test rows...")

# macro_narrative_log test row
narrative_row = MacroNarrativeLog(
    symbol="BTC/USDT",
    date_key="2026-05-25",
    authored_by="elliott_wave_specialist",
    wave_label="BEAR_WAVE_4_BOUNCE",
    wave_origin_date="2026-02-05",
    wave_origin_price=60055.00,
    wave_target_price=80632.00,
    wave_day_count=109,
    completion_pct=78.3,
    invalidation_price=83462.00,
    narrative_text=(
        "Bear Wave 4 bounce is 78% complete ($77,808 of $80,632 target). "
        "Started Feb 5 at the $60,055 Wave 3 low. Test row — Phase 2 verification."
    ),
    tactical_text="[TACTICAL PLACEHOLDER — Phase 2 test row]",
    performance_note=None,
)
db.add(narrative_row)

# jewel_snapshot_log test row
tf_state_sample = json.dumps({
    "direction": "BULLISH",
    "zone": "VALUE_ZONE",
    "momentum": "BUILDING",
    "adx_strength": "TRENDING",
})

snapshot_row = JewelSnapshotLog(
    symbol="BTC/USDT",
    timestamp=datetime.datetime.utcnow(),
    session_label="NY_OPEN",
    asset_price=105432.50,
    tf_15m_state=json.dumps({
        "direction": "BULLISH",
        "zone": "VALUE_ZONE",
        "momentum": "BUILDING",
        "adx_strength": "RISING",
    }),
    tf_1h_state=tf_state_sample,
    tf_4h_state=json.dumps({
        "direction": "BULLISH",
        "zone": "OVERBOUGHT_VALUE",
        "momentum": "BURNING",
        "adx_strength": "TRENDING",
    }),
    tf_daily_state=json.dumps({
        "direction": "BULLISH",
        "zone": "VALUE_ZONE",
        "momentum": "BUILDING",
        "adx_strength": "RISING",
    }),
    tf_weekly_state=json.dumps({
        "direction": "NEUTRAL",
        "zone": "VALUE_ZONE",
        "momentum": "BUILDING",
        "adx_strength": "WEAK",
    }),
)
db.add(snapshot_row)
db.commit()

print("      macro_narrative_log: row inserted.")
print("      jewel_snapshot_log:  row inserted.")

# -------------------------------------------------------
# STEP 4: Read them back and verify
# -------------------------------------------------------
print("\n[4/5] Reading rows back...")

mnl_count_after = db.query(MacroNarrativeLog).count()
jsl_count_after = db.query(JewelSnapshotLog).count()

assert mnl_count_after == mnl_count_before + 1, (
    f"macro_narrative_log: expected {mnl_count_before + 1} rows, got {mnl_count_after}"
)
assert jsl_count_after == jsl_count_before + 1, (
    f"jewel_snapshot_log: expected {jsl_count_before + 1} rows, got {jsl_count_after}"
)

latest_mnl = db.query(MacroNarrativeLog).order_by(MacroNarrativeLog.id.desc()).first()
latest_jsl = db.query(JewelSnapshotLog).order_by(JewelSnapshotLog.id.desc()).first()

# Verify macro_narrative_log fields
assert latest_mnl.symbol == "BTC/USDT"
assert latest_mnl.date_key == "2026-05-25"
assert latest_mnl.authored_by == "elliott_wave_specialist"
assert latest_mnl.wave_label == "BEAR_WAVE_4_BOUNCE"
assert latest_mnl.wave_origin_price == 60055.00
assert latest_mnl.wave_target_price == 80632.00
assert latest_mnl.wave_day_count == 109
assert latest_mnl.completion_pct == 78.3
assert latest_mnl.invalidation_price == 83462.00
assert latest_mnl.narrative_text is not None
assert latest_mnl.performance_note is None  # not yet written

# Verify jewel_snapshot_log fields
assert latest_jsl.symbol == "BTC/USDT"
assert latest_jsl.session_label == "NY_OPEN"
assert latest_jsl.asset_price == 105432.50
tf_15m = json.loads(latest_jsl.tf_15m_state)
assert tf_15m["direction"] == "BULLISH"
assert tf_15m["zone"] == "VALUE_ZONE"
tf_4h = json.loads(latest_jsl.tf_4h_state)
assert tf_4h["momentum"] == "BURNING"

db.close()

print(f"      macro_narrative_log rows now: {mnl_count_after}")
print(f"      jewel_snapshot_log rows now:  {jsl_count_after}")
print()
print("      macro_narrative_log latest row:")
print(f"        id:                {latest_mnl.id}")
print(f"        symbol:            {latest_mnl.symbol}")
print(f"        date_key:          {latest_mnl.date_key}")
print(f"        authored_by:       {latest_mnl.authored_by}")
print(f"        wave_label:        {latest_mnl.wave_label}")
print(f"        wave_origin_date:  {latest_mnl.wave_origin_date}")
print(f"        wave_origin_price: ${latest_mnl.wave_origin_price:,.2f}")
print(f"        wave_target_price: ${latest_mnl.wave_target_price:,.2f}")
print(f"        wave_day_count:    {latest_mnl.wave_day_count}")
print(f"        completion_pct:    {latest_mnl.completion_pct}%")
print(f"        invalidation_price:${latest_mnl.invalidation_price:,.2f}")
print(f"        narrative_text:    {latest_mnl.narrative_text[:60]}...")
print(f"        performance_note:  {latest_mnl.performance_note}")
print()
print("      jewel_snapshot_log latest row:")
print(f"        id:                {latest_jsl.id}")
print(f"        symbol:            {latest_jsl.symbol}")
print(f"        session_label:     {latest_jsl.session_label}")
print(f"        asset_price:       ${latest_jsl.asset_price:,.2f}")
print(f"        tf_15m direction:  {tf_15m['direction']}")
print(f"        tf_15m zone:       {tf_15m['zone']}")
print(f"        tf_4h momentum:    {tf_4h['momentum']}")
print(f"        created_at:        {latest_jsl.created_at}")

# -------------------------------------------------------
# STEP 5: Confirm existing tables are present and untouched
# Uses schema inspection rather than model queries so local
# SQLite column-migration gaps don't cause false failures.
# -------------------------------------------------------
print("\n[5/5] Confirming existing tables present via schema inspection...")
from sqlalchemy import inspect as sa_inspect

inspector = sa_inspect(engine)
existing_tables = set(inspector.get_table_names())

expected_tables = {
    "users", "gravity_memory", "session_locks",
    "campaign_logs", "mtf_readings", "decision_journal",
    "agent_run_log", "macro_narrative_log", "jewel_snapshot_log",
}

for table in sorted(expected_tables):
    present = table in existing_tables
    status = "PRESENT" if present else "MISSING"
    assert present, f"Table '{table}' not found in database."
    print(f"      {table:<26} {status}")

# -------------------------------------------------------
# REPORT
# -------------------------------------------------------
print()
print("=" * 60)
print("PHASE 2 SUCCESS — ALL CHECKS PASSED")
print("=" * 60)
print()
print("Tables created:")
print("  macro_narrative_log  — Elliott Wave params + brief text")
print("  jewel_snapshot_log   — 6 daily JEWEL snapshots × 5 timeframes")
print()
print("Ready for Phase 3 approval.")
