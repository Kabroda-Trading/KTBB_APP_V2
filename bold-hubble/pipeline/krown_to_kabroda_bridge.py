#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Krown → Kabroda Integration Bridge
====================================
Transforms ECKrown YouTube signal data into Kabroda-compatible format
that your Claude Code AI agent can consume to audit tools, validate
setups, and enhance charts.

This bridge:
1. Reads YouTube signal JSON files from extract/output/signals/
2. Maps Krown indicator states → Kabroda chart configurations
3. Generates trade setup recommendations based on Krown's 5 strategies
4. Outputs structured data for the Kabroda AI agent

Usage:
  python pipeline/krown_to_kabroda_bridge.py                    # Process latest signals
  python pipeline/krown_to_kabroda_bridge.py --watch             # Watch for new signals
  python pipeline/krown_to_kabroda_bridge.py --signal VIDEO_ID   # Process specific signal
"""

import os
import sys
import json
import glob
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Add parent to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIGNALS_DIR = os.path.join(BASE_DIR, "extract", "output", "signals")
BRIDGE_OUTPUT_DIR = os.path.join(BASE_DIR, "pipeline", "output")
BRIDGE_STATE_FILE = os.path.join(BASE_DIR, "pipeline", "bridge_state.json")

os.makedirs(BRIDGE_OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Krown → Kabroda Indicator Mapping
# ---------------------------------------------------------------------------

# Maps Krown indicator states to Kabroda chart configurations
INDICATOR_TO_KABRODA_CONFIG = {
    "bbwp": {
        "extreme_squeeze": {
            "chart_action": "highlight_volatility_squeeze",
            "kabroda_tool": "volatility_scanner",
            "description": "BBWP <= 5% — Extreme squeeze. Prepare for violent breakout.",
            "alert_priority": "high",
        },
        "moderate_squeeze": {
            "chart_action": "watch_for_expansion",
            "kabroda_tool": "volatility_scanner",
            "description": "BBWP <= 15% — Compression building. Monitor for expansion.",
            "alert_priority": "medium",
        },
        "high_expansion": {
            "chart_action": "trend_following_mode",
            "kabroda_tool": "trend_scanner",
            "description": "BBWP >= 85% — Active trend surge. Follow momentum.",
            "alert_priority": "medium",
        },
        "extreme_exhaustion": {
            "chart_action": "warning_reversal_risk",
            "kabroda_tool": "divergence_scanner",
            "description": "BBWP >= 95% — Blow-off top. Reversal or pullback likely.",
            "alert_priority": "high",
        },
        "normal": {
            "chart_action": "normal_monitoring",
            "kabroda_tool": "market_overview",
            "description": "BBWP in normal range.",
            "alert_priority": "low",
        },
    },
    "pmarp": {
        "overextended_top": {
            "chart_action": "warning_overextension",
            "kabroda_tool": "mean_reversion_scanner",
            "description": "PMARP >= 95% — Parabolic overextension. Take profits or trail stops.",
            "alert_priority": "high",
        },
        "capitulation_discount": {
            "chart_action": "watch_for_bounce",
            "kabroda_tool": "mean_reversion_scanner",
            "description": "PMARP <= 5% — Deep capitulation. Relief bounce likely.",
            "alert_priority": "medium",
        },
        "normal": {
            "chart_action": "normal_monitoring",
            "kabroda_tool": "market_overview",
            "description": "PMARP in normal range.",
            "alert_priority": "low",
        },
    },
    "rsi": {
        "overbought": {
            "chart_action": "warning_overbought",
            "kabroda_tool": "rsi_divergence_scanner",
            "description": "RSI >= 70 — Overbought. Watch for bearish divergence.",
            "alert_priority": "medium",
        },
        "oversold": {
            "chart_action": "warning_oversold",
            "kabroda_tool": "rsi_divergence_scanner",
            "description": "RSI <= 30 — Oversold. Watch for bullish divergence.",
            "alert_priority": "medium",
        },
        "neutral": {
            "chart_action": "normal_monitoring",
            "kabroda_tool": "market_overview",
            "description": "RSI in neutral range.",
            "alert_priority": "low",
        },
    },
    "revin_ribbons": {
        "below_midband": {
            "chart_action": "bearish_bias_active",
            "kabroda_tool": "trend_scanner",
            "description": "Price below Revin Ribbons midband — Bearish bias. Treat rallies as shorts.",
            "alert_priority": "high",
        },
        "above_midband": {
            "chart_action": "bullish_bias_active",
            "kabroda_tool": "trend_scanner",
            "description": "Price above Revin Ribbons midband — Bullish bias. Treat dips as buys.",
            "alert_priority": "high",
        },
        "gray_dot_test": {
            "chart_action": "watch_for_bounce",
            "kabroda_tool": "support_resistance_scanner",
            "description": "Revin gray dot tested — Key support/resistance bounce zone.",
            "alert_priority": "high",
        },
        "outer_band_test": {
            "chart_action": "warning_extreme_price",
            "kabroda_tool": "volatility_scanner",
            "description": "Revin outer band touched — Extreme price level. Reversal or continuation.",
            "alert_priority": "medium",
        },
    },
    "rmo": {
        "strong_bullish": {
            "chart_action": "momentum_bullish",
            "kabroda_tool": "momentum_scanner",
            "description": "RMO > 60 — Strong bullish momentum. Trend is accelerating upward.",
            "alert_priority": "high",
        },
        "strong_bearish": {
            "chart_action": "momentum_bearish",
            "kabroda_tool": "momentum_scanner",
            "description": "RMO < -60 — Strong bearish momentum. Trend is accelerating downward.",
            "alert_priority": "high",
        },
        "overextended_bullish": {
            "chart_action": "warning_momentum_exhaustion",
            "kabroda_tool": "divergence_scanner",
            "description": "RMO > 80 — Bullish overextension. Momentum exhaustion risk. Watch for bearish divergence.",
            "alert_priority": "high",
        },
        "overextended_bearish": {
            "chart_action": "warning_momentum_exhaustion",
            "kabroda_tool": "divergence_scanner",
            "description": "RMO < -80 — Bearish overextension. Momentum exhaustion risk. Watch for bullish divergence.",
            "alert_priority": "high",
        },
        "neutral": {
            "chart_action": "normal_monitoring",
            "kabroda_tool": "market_overview",
            "description": "RMO in neutral range. No strong momentum signal.",
            "alert_priority": "low",
        },
    },
    "rwp": {
        "extreme_squeeze": {
            "chart_action": "highlight_volatility_squeeze",
            "kabroda_tool": "volatility_scanner",
            "description": "RWP <= 10% — Extreme volatility squeeze. Breakout imminent.",
            "alert_priority": "high",
        },
        "active_expansion": {
            "chart_action": "trend_following_mode",
            "kabroda_tool": "trend_scanner",
            "description": "RWP >= 80% — Active volatility expansion. Trend in progress.",
            "alert_priority": "medium",
        },
        "normal": {
            "chart_action": "normal_monitoring",
            "kabroda_tool": "market_overview",
            "description": "RWP in normal range. No volatility signal.",
            "alert_priority": "low",
        },
    },
    "volatility_state": {
        "compressing": {
            "chart_action": "prepare_for_breakout",
            "kabroda_tool": "volatility_scanner",
            "description": "Volatility compressing — Breakout imminent.",
            "alert_priority": "high",
        },
        "expanding": {
            "chart_action": "trend_following_mode",
            "kabroda_tool": "trend_scanner",
            "description": "Volatility expanding — Trend in progress.",
            "alert_priority": "medium",
        },
    },
}

# ---------------------------------------------------------------------------
# Strategy → Kabroda Action Mapping
# ---------------------------------------------------------------------------

STRATEGY_TO_KABRODA_ACTION = {
    "strategy_1": {
        "name": "Macro Trend Breakout",
        "kabroda_action": "enable_trend_breakout_scanner",
        "description": "Catching big swing moves on 4H or Daily charts",
        "entry_condition": "Price closes above 20 SMA + BBWP rising from <= 15%",
        "stop_loss": "Below previous swing low",
        "take_profit": "Exit when price closes below 20 SMA or PMARP >= 95%",
    },
    "strategy_2": {
        "name": "Uptrend Pullback Dip-Buy",
        "kabroda_action": "enable_dip_buy_scanner",
        "description": "Buying dips inside an established strong uptrend",
        "entry_condition": "20 SMA > 50 SMA + Price in Value Zone + RSI 40-50 or Hidden Bullish Divergence",
        "stop_loss": "Below 50 SMA or swing low",
        "take_profit": "Previous swing high or 1.272 Fibonacci extension",
    },
    "strategy_3": {
        "name": "Downtrend Continuation Rally-Sell",
        "kabroda_action": "enable_rally_sell_scanner",
        "description": "Shorting relief bounces inside a strong downtrend",
        "entry_condition": "20 SMA < 50 SMA + Price rallies to Value Zone + RSI 50-60 or Hidden Bearish Divergence",
        "stop_loss": "Above 50 SMA resistance",
        "take_profit": "Previous swing low",
    },
    "strategy_4": {
        "name": "Counter-Trend Parabolic Exhaustion Short",
        "kabroda_action": "enable_exhaustion_scanner",
        "description": "Fading a blow-off top",
        "entry_condition": "PMARP >= 95% + BBWP >= 85% + Regular Bearish Divergence",
        "stop_loss": "1.5% above blow-off high",
        "take_profit": "5-8% mean reversion to 20 SMA",
    },
    "strategy_5": {
        "name": "Momentum Breakdown Short",
        "kabroda_action": "enable_breakdown_scanner",
        "description": "Riding a sudden support collapse",
        "entry_condition": "Downtrend + BBWP shooting from < 30% on support breakdown",
        "stop_loss": "Above breakdown candle high",
        "take_profit": "1.618 Fibonacci downside extension",
    },
}

# ---------------------------------------------------------------------------
# Bridge Engine
# ---------------------------------------------------------------------------

def load_signals() -> List[Dict[str, Any]]:
    """Load all YouTube signal JSON files."""
    signals = []
    signal_files = sorted(glob.glob(os.path.join(SIGNALS_DIR, "*.json")))
    for fpath in signal_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                signals.append(json.load(f))
        except Exception as e:
            print(f"  [WARN] Failed to load {fpath}: {e}")
    return signals


def load_bridge_state() -> Dict[str, Any]:
    """Load bridge processing state."""
    if os.path.exists(BRIDGE_STATE_FILE):
        with open(BRIDGE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_signal_ids": [], "last_bridge_run": None}


def save_bridge_state(state: Dict[str, Any]):
    """Save bridge processing state."""
    state["last_bridge_run"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(BRIDGE_STATE_FILE), exist_ok=True)
    with open(BRIDGE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def map_indicator_to_kabroda(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Krown indicator states to Kabroda chart configurations."""
    configs = []
    indicators = signals.get("indicators", {})
    vol_state = indicators.get("volatility_state", "")

    for ind_name, ind_data in indicators.items():
        if ind_name == "divergences":
            continue  # Handled separately
        if ind_name == "volatility_state":
            continue  # Handled below

        if isinstance(ind_data, dict):
            state = ind_data.get("state", "")

            # Check indicator mapping
            if ind_name in INDICATOR_TO_KABRODA_CONFIG:
                state_map = INDICATOR_TO_KABRODA_CONFIG[ind_name]
                if state in state_map:
                    config = state_map[state].copy()
                    config["indicator"] = ind_name
                    config["source_value"] = ind_data.get("value", "N/A")
                    configs.append(config)

    # Add volatility state if present
    if vol_state and vol_state in INDICATOR_TO_KABRODA_CONFIG.get("volatility_state", {}):
        config = INDICATOR_TO_KABRODA_CONFIG["volatility_state"][vol_state].copy()
        config["indicator"] = "volatility"
        configs.append(config)

    # Add divergences
    divergences = indicators.get("divergences", [])
    for div in divergences:
        configs.append({
            "indicator": "divergence",
            "kabroda_tool": "divergence_scanner",
            "chart_action": f"alert_{div['type'].replace(' ', '_')}",
            "description": f"{div['type'].title()} divergence detected (x{div['count']})",
            "alert_priority": "high" if "regular" in div["type"] else "medium",
        })

    return configs


def map_strategies_to_kabroda(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map active Krown strategies to Kabroda actions."""
    actions = []
    active_strategies = signals.get("active_strategies", [])

    for s in active_strategies:
        strat_id = s.get("strategy", "")
        if strat_id in STRATEGY_TO_KABRODA_ACTION:
            mapping = STRATEGY_TO_KABRODA_ACTION[strat_id].copy()
            mapping["strategy_id"] = strat_id
            actions.append(mapping)

    return actions


def generate_trade_setups(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate actionable trade setups from signal data."""
    setups = []
    bias = signals.get("market_bias", {})
    levels = signals.get("key_levels", {})
    indicators = signals.get("indicators", {})

    # Determine overall bias direction
    st_bias = bias.get("short_term", "neutral")
    mt_bias = bias.get("medium_term", "neutral")

    # Check for specific conditions
    bbwp = indicators.get("bbwp", {})
    pmarp = indicators.get("pmarp", {})
    rsi = indicators.get("rsi", {})
    rr = indicators.get("revin_ribbons", {})

    # Setup 1: If bearish bias + below midband → short setups
    if mt_bias == "bearish" and rr.get("position") == "below_midband":
        resistance_levels = levels.get("resistance", [])
        targets = levels.get("targets", [])

        setup = {
            "type": "SHORT",
            "bias": "bearish",
            "confidence": "high" if st_bias == "bearish" else "medium",
            "rationale": "Medium-term bearish bias confirmed by Revin Ribbons midband rejection",
            "suggested_entry_zone": [l["price"] for l in resistance_levels[:2]] if resistance_levels else None,
            "suggested_targets": [l["price"] for l in targets[:3]] if targets else None,
            "kabroda_strategy": "Strategy_3_Downtrend_Continuation",
            "validation_needed": [
                "Confirm 20 SMA < 50 SMA",
                "Check RSI for hidden bearish divergence",
                "Verify BBWP not in extreme exhaustion (> 95%)",
            ],
        }
        setups.append(setup)

    # Setup 2: If bullish bias + above midband → long setups
    elif mt_bias == "bullish" and rr.get("position") == "above_midband":
        support_levels = levels.get("support", [])
        targets = levels.get("targets", [])

        setup = {
            "type": "LONG",
            "bias": "bullish",
            "confidence": "high" if st_bias == "bullish" else "medium",
            "rationale": "Medium-term bullish bias confirmed by Revin Ribbons midband reclaim",
            "suggested_entry_zone": [l["price"] for l in support_levels[:2]] if support_levels else None,
            "suggested_targets": [l["price"] for l in targets[:3]] if targets else None,
            "kabroda_strategy": "Strategy_2_Uptrend_Pullback",
            "validation_needed": [
                "Confirm 20 SMA > 50 SMA",
                "Check RSI for hidden bullish divergence",
                "Verify BBWP not in extreme exhaustion (> 95%)",
            ],
        }
        setups.append(setup)

    # Setup 3: Volatility squeeze → prepare for breakout
    if bbwp.get("state") in ("extreme_squeeze", "moderate_squeeze"):
        setup = {
            "type": "VOLATILITY_BREAKOUT_WATCH",
            "bias": "neutral",
            "confidence": "medium",
            "rationale": f"BBWP in {bbwp.get('state', 'squeeze')} — explosive move brewing",
            "suggested_entry_zone": None,
            "suggested_targets": None,
            "kabroda_strategy": "Strategy_1_Macro_Trend",
            "validation_needed": [
                "Wait for BBWP to start expanding",
                "Watch for price to break above 20 SMA (long) or below support (short)",
                "Confirm with volume expansion",
            ],
        }
        setups.append(setup)

    # Setup 4: Overextension → mean reversion
    if pmarp.get("state") == "overextended_top":
        setup = {
            "type": "MEAN_REVERSION_SHORT",
            "bias": "bearish",
            "confidence": "medium",
            "rationale": f"PMARP at {pmarp.get('value', 'N/A')}% — severely overextended",
            "suggested_entry_zone": None,
            "suggested_targets": None,
            "kabroda_strategy": "Strategy_4_Exhaustion_Short",
            "validation_needed": [
                "Confirm regular bearish divergence on RSI",
                "Check BBWP for blow-off (> 85%)",
                "Set tight stop 1.5% above high",
            ],
        }
        setups.append(setup)

    return setups


def generate_kabroda_audit_report(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate a comprehensive audit report for the Kabroda AI agent."""
    if not signals:
        return {"status": "no_data", "message": "No YouTube signals available for audit."}

    # Aggregate all signals
    all_bias = {"short_term": {}, "medium_term": {}, "long_term": {}}
    all_indicators = {}
    all_setups = []
    all_assets = set()
    all_strategies = set()

    for sig in signals:
        bias = sig.get("market_bias", {})
        for tf in ["short_term", "medium_term", "long_term"]:
            b = bias.get(tf, "neutral")
            all_bias[tf][b] = all_bias[tf].get(b, 0) + 1

        for asset in sig.get("assets_mentioned", []):
            all_assets.add(asset)

        for s in sig.get("active_strategies", []):
            all_strategies.add(s.get("strategy", ""))

        # Generate setups
        setups = generate_trade_setups(sig)
        all_setups.extend(setups)

    # Determine consensus bias
    consensus = {}
    for tf in ["short_term", "medium_term", "long_term"]:
        counts = all_bias[tf]
        if counts:
            consensus[tf] = max(counts, key=counts.get)
        else:
            consensus[tf] = "neutral"

    # Generate chart config recommendations
    chart_configs = []
    for sig in signals:
        configs = map_indicator_to_kabroda(sig)
        chart_configs.extend(configs)

    # Deduplicate chart configs
    seen_configs = set()
    unique_configs = []
    for c in chart_configs:
        key = f"{c.get('indicator')}_{c.get('chart_action')}"
        if key not in seen_configs:
            seen_configs.add(key)
            unique_configs.append(c)

    return {
        "report_generated": datetime.now(timezone.utc).isoformat(),
        "videos_analyzed": len(signals),
        "consensus_bias": consensus,
        "assets_monitored": sorted(list(all_assets)),
        "active_strategies": sorted(list(all_strategies)),
        "chart_config_recommendations": unique_configs[:20],  # Top 20
        "trade_setups": all_setups,
        "summary": generate_audit_summary(consensus, all_setups, all_assets),
    }


def generate_audit_summary(consensus: Dict[str, str], setups: List[Dict], assets: set) -> str:
    """Generate a natural language summary for the AI agent."""
    parts = []

    # Bias summary
    bias_parts = []
    for tf in ["short_term", "medium_term", "long_term"]:
        b = consensus.get(tf, "neutral")
        emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
        bias_parts.append(f"{tf.replace('_', ' ').title()}: {emoji.get(b, '⚪')} {b.upper()}")
    parts.append(" | ".join(bias_parts))

    # Setup summary
    high_conf = [s for s in setups if s.get("confidence") == "high"]
    if high_conf:
        parts.append(f"\n🎯 **{len(high_conf)} high-confidence setups detected**")
        for s in high_conf[:3]:
            parts.append(f"   - {s['type']}: {s['rationale']}")

    # Asset summary
    if assets:
        parts.append(f"\n📊 **Assets to monitor**: {', '.join(sorted(assets))}")

    # Action items
    parts.append("\n⚡ **Recommended Kabroda Actions**:")
    for s in setups[:5]:
        strat = s.get("kabroda_strategy", "").replace("_", " ").title()
        parts.append(f"   - Enable `{strat}` scanner")
        for v in s.get("validation_needed", [])[:2]:
            parts.append(f"     • {v}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Output Writers
# ---------------------------------------------------------------------------

def write_bridge_output(data: Dict[str, Any], filename: str):
    """Write bridge output to JSON file."""
    path = os.path.join(BRIDGE_OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  [OUTPUT] Bridge data: {path}")
    return path


def write_ai_agent_prompt(data: Dict[str, Any]):
    """Write a ready-to-use prompt for the Kabroda AI agent (Claude Code)."""
    path = os.path.join(BRIDGE_OUTPUT_DIR, "kabroda_agent_prompt.md")

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Kabroda AI Agent — Krown Integration Brief\n\n")
        f.write(f"*Generated: {data.get('report_generated', 'N/A')}*\n\n")

        f.write("## Current Market Consensus\n\n")
        consensus = data.get("consensus_bias", {})
        for tf in ["short_term", "medium_term", "long_term"]:
            b = consensus.get(tf, "neutral")
            f.write(f"- **{tf.replace('_', ' ').title()}**: {b.upper()}\n")
        f.write("\n")

        f.write("## Assets to Monitor\n\n")
        for asset in data.get("assets_monitored", []):
            f.write(f"- `{asset}`\n")
        f.write("\n")

        f.write("## Active Strategies\n\n")
        for strat in data.get("active_strategies", []):
            name = strat.replace("_", " ").title()
            f.write(f"- {name}\n")
        f.write("\n")

        f.write("## Chart Configuration Recommendations\n\n")
        for cfg in data.get("chart_config_recommendations", []):
            f.write(f"### {cfg.get('indicator', 'N/A').replace('_', ' ').title()}\n")
            f.write(f"- **Action**: `{cfg.get('chart_action', 'N/A')}`\n")
            f.write(f"- **Tool**: `{cfg.get('kabroda_tool', 'N/A')}`\n")
            f.write(f"- **Priority**: {cfg.get('alert_priority', 'low').upper()}\n")
            f.write(f"- **Description**: {cfg.get('description', 'N/A')}\n\n")

        f.write("## Trade Setups\n\n")
        for setup in data.get("trade_setups", []):
            f.write(f"### {setup.get('type', 'N/A')} — Confidence: {setup.get('confidence', 'N/A').upper()}\n")
            f.write(f"- **Rationale**: {setup.get('rationale', 'N/A')}\n")
            f.write(f"- **Strategy**: {setup.get('kabroda_strategy', 'N/A').replace('_', ' ').title()}\n")
            f.write(f"- **Validation Needed**:\n")
            for v in setup.get("validation_needed", []):
                f.write(f"  - [ ] {v}\n")
            f.write("\n")

        f.write("## Summary\n\n")
        f.write(data.get("summary", "No summary available."))
        f.write("\n\n---\n")
        f.write("*Generated by Krown → Kabroda Bridge*\n")

    print(f"  [OUTPUT] AI Agent Prompt: {path}")
    return path


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run_bridge(process_all: bool = False):
    """Run the Krown → Kabroda bridge pipeline."""
    print(f"\n{'#'*60}")
    print(f"# Krown → Kabroda Integration Bridge")
    print(f"{'#'*60}\n")

    # Load signals
    all_signals = load_signals()
    print(f"[SIGNALS] Loaded {len(all_signals)} YouTube signal files")

    if not all_signals:
        print("[INFO] No signals to process. Run youtube_channel_watcher.py first.")
        return

    # Load bridge state
    state = load_bridge_state()
    processed_ids = set(state.get("processed_signal_ids", []))

    if not process_all:
        # Only process new signals
        new_signals = [s for s in all_signals if s.get("video_id") not in processed_ids]
    else:
        new_signals = all_signals

    if not new_signals:
        print("[INFO] No new signals to bridge.")
        return

    print(f"[BRIDGE] Processing {len(new_signals)} signals...")

    # Generate audit report
    print(f"\n[ANALYSIS] Generating Kabroda audit report...")
    audit = generate_kabroda_audit_report(new_signals)

    # Write outputs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_bridge_output(audit, f"kabroda_audit_{timestamp}.json")
    write_bridge_output(audit, "kabroda_latest_audit.json")
    write_ai_agent_prompt(audit)

    # Update state
    for sig in new_signals:
        vid = sig.get("video_id")
        if vid:
            state["processed_signal_ids"].append(vid)
    save_bridge_state(state)

    print(f"\n[DONE] Bridge completed. {len(new_signals)} signals processed.")
    print(f"[STATE] Total bridged: {len(state.get('processed_signal_ids', []))} signals")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Krown → Kabroda Integration Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="Reprocess all signals")
    parser.add_argument("--watch", action="store_true", help="Watch mode (not yet implemented)")

    args = parser.parse_args()
    run_bridge(process_all=args.all)
