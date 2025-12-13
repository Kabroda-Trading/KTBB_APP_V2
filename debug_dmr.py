# debug_dmr.py
#
# Run the full auto DMR stack for a symbol and print the 30m OR
# and YAML block, without going through FastAPI or the web UI.

from datetime import datetime
from typing import Dict, Any

from data_feed import build_auto_inputs, resolve_symbol
from sse_engine import compute_dm_levels
from dmr_report import generate_dmr_report


def run_debug(symbol: str = "BTC") -> None:
    print(f"=== KABRODA BattleBox DMR DEBUG for {symbol} ===")

    binance_symbol = resolve_symbol(symbol)
    print(f"Using Binance symbol: {binance_symbol}")

    # 1) Build auto inputs (weekly VRVP, FRVPs, HTF shelves, 30m OR)
    inputs: Dict[str, float] = build_auto_inputs(symbol)
    print("\n[Auto inputs]")
    for k in sorted(inputs.keys()):
        print(f"  {k:10s}: {inputs[k]:,.2f}")

    # 2) Run SSE to get levels + HTF shelves
    levels_full = compute_dm_levels(
        h4_supply=inputs["h4_supply"],
        h4_demand=inputs["h4_demand"],
        h1_supply=inputs["h1_supply"],
        h1_demand=inputs["h1_demand"],
        weekly_val=inputs["weekly_val"],
        weekly_poc=inputs["weekly_poc"],
        weekly_vah=inputs["weekly_vah"],
        f24_val=inputs["f24_val"],
        f24_poc=inputs["f24_poc"],
        f24_vah=inputs["f24_vah"],
        morn_val=inputs["morn_val"],
        morn_poc=inputs["morn_poc"],
        morn_vah=inputs["morn_vah"],
        r30_high=inputs["r30_high"],
        r30_low=inputs["r30_low"],
    )

    htf_shelves = {
        "resistance": levels_full.get("htf_resistance", []),
        "support": levels_full.get("htf_support", []),
    }
    levels = {k: v for k, v in levels_full.items() if not k.startswith("htf_")}

    # 3) Build range_30m dict
    range_30m = {
        "high": inputs["r30_high"],
        "low": inputs["r30_low"],
    }

    print("\n[30m Opening Range from backend]")
    print(f"  high: {range_30m['high']:.2f}")
    print(f"  low : {range_30m['low']:.2f}")

    print("\n[Structural levels (SSE output)]")
    for key in ["daily_support", "breakdown_trigger", "breakout_trigger", "daily_resistance"]:
        print(f"  {key:18s}: {levels.get(key, 0.0):,.2f}")

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # 4) Generate DMR narrative
    report: Dict[str, Any] = generate_dmr_report(
        symbol=binance_symbol,
        date_str=today,
        inputs=inputs,
        levels=levels,
        htf_shelves=htf_shelves,
        range_30m=range_30m,
    )

    print("\n[Bias]")
    print(f"  label     : {report.get('bias')}")
    print(f"  yaml_block:\n{report.get('yaml_block','(no yaml_block key, see full_text)')}")

    # Optional: show just the YAML block if you want
    # print(report["yaml_block"])


if __name__ == "__main__":
    # Change "BTC" to "ETH" here if you want to test ETH.
    run_debug("BTC")
