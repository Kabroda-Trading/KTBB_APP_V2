# test_phase3c.py
# Phase 3C integration test — JEWEL Specialist
# Calls run_jewel_snapshot(), verifies DB write, prints full output.
# Reads live MEXC data. No LLM call, no API key needed.

import asyncio
import json
import sys

from database import SessionLocal, JewelSnapshotLog, AgentRunLog, init_db
from jewel_specialist import run_jewel_snapshot

REQUIRED_TF_FIELDS = {
    "direction", "zone", "momentum", "adx_strength",
    "bbwp_value", "bbwp_compressed",
    "pmarp_value", "pmarp_overextended", "pmarp_direction",
    "divergence", "divergence_strength",
}

REQUIRED_JEWEL_FIELDS = {
    "gate_open", "direction", "conviction",
    "exit_warning", "divergence_warning", "signal_summary",
}


def _check(condition: bool, msg: str) -> None:
    if not condition:
        print(f"  FAIL: {msg}")
        sys.exit(1)
    print(f"  OK:   {msg}")


async def _run():
    print("=" * 60)
    print("PHASE 3C — JEWEL SPECIALIST TEST")
    print("=" * 60)

    print("\n[1] Initialising database (runs migrations)...")
    init_db()
    print("  Done.")

    print("\n[2] Calling run_jewel_snapshot() — fetching live MEXC data...")
    result = await run_jewel_snapshot(
        symbol="BTC/USDT",
        session_label="NY_OPEN",
        current_price=76744.74,
        date_key="2026-05-26",
    )

    print(f"\n  Status: {result['status']}")
    if result["status"] != "SUCCESS":
        print(f"  ERROR: {result.get('error')}")
        sys.exit(1)

    snap = result["snapshot"]

    # ----------------------------------------------------------------
    print("\n[3] Top-level snapshot fields:")
    print(f"  id:                {snap['id']}")
    print(f"  symbol:            {snap['symbol']}")
    print(f"  timestamp:         {snap['timestamp']}")
    print(f"  session_label:     {snap['session_label']}")
    print(f"  asset_price:       {snap['asset_price']}")
    print(f"  confluence_score:  {snap['confluence_score']}")
    print(f"  dominant_dir:      {snap['dominant_direction']}")
    print(f"  conviction:        {snap['conviction']}")
    print(f"  any_tf_compressed: {snap['any_tf_compressed']}")
    print(f"  any_tf_overextended:{snap['any_tf_overextended']}")
    print(f"  any_tf_divergence: {snap['any_tf_divergence']}")

    # ----------------------------------------------------------------
    print("\n[4] jewel_signal (sequential synthesis):")
    js = snap["jewel_signal"]
    for k, v in js.items():
        print(f"  {k:<22} {v}")

    # ----------------------------------------------------------------
    print("\n[5] Per-timeframe states:")
    tf_cols = ["tf_15m_state", "tf_1h_state", "tf_4h_state", "tf_daily_state", "tf_weekly_state"]
    for col in tf_cols:
        state = snap[col]
        print(f"\n  {col}:")
        for k, v in state.items():
            print(f"    {k:<22} {v}")

    # ----------------------------------------------------------------
    print("\n[6] Verifying DB row...")
    db = SessionLocal()
    try:
        row = db.query(JewelSnapshotLog).filter(JewelSnapshotLog.id == snap["id"]).first()
        _check(row is not None, "Row found in jewel_snapshot_log")
        _check(row.symbol == "BTC/USDT", f"symbol = BTC/USDT (got {row.symbol})")
        _check(row.session_label == "NY_OPEN", f"session_label = NY_OPEN")
        _check(row.asset_price == 76744.74, f"asset_price = 76744.74")
        _check(row.confluence_score is not None, "confluence_score written")
        _check(row.dominant_direction is not None, "dominant_direction written")
        _check(row.jewel_gate_open is not None, "jewel_gate_open written")
        _check(row.jewel_signal_summary is not None, "jewel_signal_summary written")

        for col in tf_cols:
            raw = getattr(row, col)
            _check(raw is not None, f"{col} is not null")
            parsed = json.loads(raw)
            missing = REQUIRED_TF_FIELDS - parsed.keys()
            _check(len(missing) == 0, f"{col} has all 11 required fields (missing: {missing})")

        # ----------------------------------------------------------------
        print("\n[7] Verifying agent_run_log entry...")
        log = (
            db.query(AgentRunLog)
            .filter(AgentRunLog.agent_name == "jewel_specialist")
            .order_by(AgentRunLog.id.desc())
            .first()
        )
        _check(log is not None, "agent_run_log row found")
        _check(log.status == "SUCCESS", f"status = SUCCESS (got {log.status})")
        _check(log.estimated_cost_usd == 0.0, "estimated_cost_usd = 0.0 (no LLM call)")
        _check(log.triggered_by == "NY_OPEN", f"triggered_by = NY_OPEN")

        print(f"\n  agent_name:         {log.agent_name}")
        print(f"  triggered_by:       {log.triggered_by}")
        print(f"  status:             {log.status}")
        print(f"  estimated_cost_usd: {log.estimated_cost_usd}")

    finally:
        db.close()

    # ----------------------------------------------------------------
    print("\n[8] jewel_signal full text:")
    print(f'  "{js["signal_summary"]}"')

    print("\n" + "=" * 60)
    print("PHASE 3C TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_run())
