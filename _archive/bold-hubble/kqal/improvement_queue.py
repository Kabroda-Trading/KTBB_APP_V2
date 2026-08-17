#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KQAL — Kabroda Quality Assurance Layer
=======================================
Improvement Queue Generator

Generates a prioritized build queue for Claude Code by:
1. Receiving gaps from alignment_engine.py
2. Receiving patterns from trade_validator.py
3. Cross-referencing against WORK_LOG.md SUGGESTION BOX
4. Producing kabroda_improvement_queue.json and kabroda_agent_prompt.md

Usage:
    python kqal/improvement_queue.py                          # Run with sample data
    python kqal/improvement_queue.py --gaps <file> --patterns <file> --krown <file>
"""

import os
import sys
import json
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KQAL_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(KQAL_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Known Improvement Items (from WORK_LOG.md SUGGESTION BOX & codebase analysis)
# ---------------------------------------------------------------------------

KNOWN_IMPROVEMENTS = [
    {
        "id": "IMP-001",
        "title": "Add Revin Ribbons Indicator",
        "category": "MISSING_INDICATOR",
        "description": (
            "Krown references Revin Ribbons midband in 8/15 videos as his primary "
            "trend-bias indicator. Kabroda has no equivalent. The Revin Ribbons "
            "suite includes a 21-period EMA midline, ±1.0/±2.5/±3.5 StDev bands, "
            "RWP (Revin Width Percentile), and RMO (Revin Momentum Oscillator)."
        ),
        "impact_score": 1.5,
        "effort": "MEDIUM",
        "krown_frequency": 8,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build a Revin Ribbons indicator module:\n"
            "1. Create indicators/revin_ribbons.py with:\n"
            "   - 21-period EMA midline calculation\n"
            "   - ±1.0, ±2.5, ±3.5 Standard Deviation bands\n"
            "   - RWP (Revin Width Percentile) — same calc as BBWP on Revin bands\n"
            "   - RMO (Revin Momentum Oscillator) — composite -100/+100 score\n"
            "2. Wire into mtf_confluence_scanner.py\n"
            "3. Add to gravity_engine.py candidate context\n"
            "4. Add to pipeline/krown_to_kabroda_bridge.py indicator mapping"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.5,
    },
    {
        "id": "IMP-002",
        "title": "Fibonacci EMA Ribbon (5/21/55/377)",
        "category": "MISSING_INDICATOR",
        "description": (
            "Krown uses a Fibonacci-based EMA ribbon (5, 21, 55, 377) for multi-timeframe "
            "trend alignment. Kabroda currently uses 9/21/35/55 SMA. The Fib sequence "
            "provides better harmonic alignment with Krown's methodology."
        ),
        "impact_score": 1.0,
        "effort": "SMALL",
        "krown_frequency": 6,
        "kabroda_status": "PARTIAL",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Update the moving average system to support Fibonacci EMA ribbon:\n"
            "1. Add 5 EMA and 377 EMA to the MA configuration\n"
            "2. Replace 9 SMA with 5 EMA for ultra-fast trend\n"
            "3. Replace 35 SMA with 55 EMA for intermediate trend\n"
            "4. Add 377 EMA for macro trend context\n"
            "5. Update trend_volatility.py to use Fib EMAs in trend scoring\n"
            "6. Update krown_settings_and_rules.json with new MA settings"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.0,
    },
    {
        "id": "IMP-003",
        "title": "Three Drives Divergence Detection",
        "category": "MISSING_PATTERN",
        "description": (
            "Krown requires 3 drives (swing points) to confirm divergence patterns. "
            "Kabroda's rsi_divergence.py only detects single divergences between 2 points. "
            "Adding 3-drive detection would align with Krown's methodology and reduce false signals."
        ),
        "impact_score": 1.0,
        "effort": "MEDIUM",
        "krown_frequency": 5,
        "kabroda_status": "PARTIAL",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Enhance rsi_divergence.py with Three Drives divergence detection:\n"
            "1. Add function detect_three_drives(highs, lows, rsi_values) -> List[Dict]\n"
            "2. Implement swing point detection with configurable pivot lookback (default 3)\n"
            "3. Detect 3-drive bullish pattern: price LL, HL, LL with RSI making higher lows\n"
            "4. Detect 3-drive bearish pattern: price HH, LH, HH with RSI making lower highs\n"
            "5. Add confidence scoring based on harmonic ratios between drives\n"
            "6. Wire into pipeline/krown_to_kabroda_bridge.py divergence mapping"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.0,
    },
    {
        "id": "IMP-004",
        "title": "SSE-into-TSA Target Wiring",
        "category": "ARCHITECTURE_GAP",
        "description": (
            "SSE (Support/Resistance) levels from Krown's S/R detection are not wired "
            "into TSA (Target) computation. This means Kabroda generates targets without "
            "considering key S/R levels, producing unrealistic profit targets."
        ),
        "impact_score": 1.5,
        "effort": "MEDIUM",
        "krown_frequency": 7,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Wire SSE S/R levels into target computation:\n"
            "1. Create a target_computation module that reads SSE levels\n"
            "2. Implement nearest-S/R target logic: take profit at next major S/R level\n"
            "3. Implement S/R cluster target: zone where multiple S/R levels converge\n"
            "4. Add Fibonacci extension targets filtered by S/R proximity\n"
            "5. Wire into pipeline/krown_to_kabroda_bridge.py trade setup generation\n"
            "6. Update strategy evaluation to use S/R-aware targets"
        ),
        "dependencies": ["IMP-001"],
        "estimated_alignment_gain": 1.5,
    },
    {
        "id": "IMP-005",
        "title": "Position Sizing Module",
        "category": "MISSING_FEATURE",
        "description": (
            "Kabroda has no position sizing mechanism. Krown emphasizes risk-based "
            "sizing (1-2% risk per trade, volatility-adjusted). Without sizing, "
            "Kabroda cannot generate actionable trade plans with proper risk management."
        ),
        "impact_score": 1.5,
        "effort": "MEDIUM",
        "krown_frequency": 9,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build a position sizing module:\n"
            "1. Create strategies/position_sizing.py with:\n"
            "   - Fixed fractional sizing (1-2% risk per trade)\n"
            "   - Volatility-adjusted sizing (ATR-based position scaling)\n"
            "   - Kelly Criterion option for optimal growth\n"
            "2. Add account balance input parameter\n"
            "3. Wire into trade setup generation in pipeline\n"
            "4. Add to strategy evaluation output"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.5,
    },
    {
        "id": "IMP-006",
        "title": "Live Exhaustion Monitor",
        "category": "MISSING_FEATURE",
        "description": (
            "In-trade runner exhaustion signals are not monitored. Krown teaches that "
            "runners should be monitored for momentum exhaustion (PMARP > 95%, BBWP > 85%, "
            "RSI divergence). Kabroda has no in-trade monitoring capability."
        ),
        "impact_score": 1.0,
        "effort": "MEDIUM",
        "krown_frequency": 4,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build a live exhaustion monitor:\n"
            "1. Create strategies/exhaustion_monitor.py with:\n"
            "   - PMARP overextension detection (> 95%)\n"
            "   - BBWP blow-off detection (> 85%)\n"
            "   - RSI divergence confirmation on each new bar\n"
            "2. Implement alert levels: WATCH, WARNING, EXIT\n"
            "3. Add trailing stop adjustment logic on exhaustion signals\n"
            "4. Wire into pipeline output as in-trade advisory"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.0,
    },
    {
        "id": "IMP-007",
        "title": "News/Event Calendar Integration",
        "category": "MISSING_FEATURE",
        "description": (
            "No FOMC or economic event awareness. Krown frequently references FOMC "
            "weeks, NFP releases, and CPI data as volatility catalysts. Kabroda should "
            "be aware of upcoming events to adjust position sizing and avoid trading "
            "into high-impact news."
        ),
        "impact_score": 0.8,
        "effort": "SMALL",
        "krown_frequency": 3,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Add news/event calendar awareness:\n"
            "1. Create a simple event calendar module that reads from a JSON config\n"
            "2. Add FOMC, NFP, CPI, and other high-impact event dates\n"
            "3. Implement event proximity check: days until next major event\n"
            "4. Add position sizing adjustment: reduce size near events\n"
            "5. Wire into strategy evaluation as a risk modifier"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 0.8,
    },
    {
        "id": "IMP-008",
        "title": "Stand-Down Re-Arm Alerter",
        "category": "MISSING_FEATURE",
        "description": (
            "After a trade is taken, Kabroda has no mechanism to alert when conditions "
            "improve for re-entry. Krown teaches waiting for re-arm (RSI reset, price "
            "returning to value zone). This would alert when re-arm conditions are met."
        ),
        "impact_score": 0.5,
        "effort": "SMALL",
        "krown_frequency": 2,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build a stand-down re-arm alerter:\n"
            "1. Create strategies/rearm_alerter.py\n"
            "2. Monitor for RSI reset to 40-50 range after overbought/oversold\n"
            "3. Monitor for price return to value zone (between 20 & 50 MA)\n"
            "4. Monitor for BBWP re-compression after expansion\n"
            "5. Generate alert when re-arm conditions are met\n"
            "6. Wire into pipeline output"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 0.5,
    },
    {
        "id": "IMP-009",
        "title": "Entry Mechanics Model",
        "category": "MISSING_FEATURE",
        "description": (
            "Kabroda lacks a formal entry mechanics model. Krown distinguishes between "
            "trigger entries (aggressive), confirm entries (conservative), and retest "
            "entries (optimal). Modeling these would improve entry precision."
        ),
        "impact_score": 1.0,
        "effort": "MEDIUM",
        "krown_frequency": 5,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build an entry mechanics model:\n"
            "1. Create strategies/entry_mechanics.py with:\n"
            "   - Trigger entry: enter on first signal bar close\n"
            "   - Confirm entry: enter after confirmation candle\n"
            "   - Retest entry: enter on retest of broken level\n"
            "2. Add configurable entry style per strategy\n"
            "3. Wire into trade setup generation\n"
            "4. Add to strategy evaluation output"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 1.0,
    },
    {
        "id": "IMP-010",
        "title": "Runner Mechanic (Partial Profit Trailing)",
        "category": "MISSING_FEATURE",
        "description": (
            "Kabroda has no runner/partial profit management. Krown teaches taking "
            "partial profits at key levels and trailing the remainder. This would "
            "add partial take-profit and trailing stop logic."
        ),
        "impact_score": 0.8,
        "effort": "MEDIUM",
        "krown_frequency": 4,
        "kabroda_status": "NOT_BUILT",
        "suggestion_box_ref": None,
        "claude_code_prompt": (
            "Build a runner mechanic module:\n"
            "1. Create strategies/runner_mechanic.py with:\n"
            "   - Partial take-profit levels (e.g., 33% at 1:1, 33% at 1.618, 34% runner)\n"
            "   - Trailing stop logic (ATR-based or MA-based trail)\n"
            "   - Breakeven stop after first target hit\n"
            "2. Add configurable profit split per strategy\n"
            "3. Wire into trade setup generation\n"
            "4. Add to strategy evaluation output"
        ),
        "dependencies": [],
        "estimated_alignment_gain": 0.8,
    },
]

# ---------------------------------------------------------------------------
# Priority Scoring
# ---------------------------------------------------------------------------

EFFORT_WEIGHTS = {
    "SMALL": 1,
    "MEDIUM": 2,
    "LARGE": 3,
}


def compute_priority(impact_score: float, effort: str) -> str:
    """Compute priority based on impact score and effort.

    Rules:
        - HIGH if impact >= 1.5 and effort != LARGE
        - MEDIUM if impact >= 1.0 or effort == SMALL
        - LOW otherwise
    """
    effort_weight = EFFORT_WEIGHTS.get(effort, 2)

    if impact_score >= 1.5 and effort_weight < 3:
        return "HIGH"
    if impact_score >= 1.0 or effort_weight == 1:
        return "MEDIUM"
    return "LOW"


def compute_impact_score(item: Dict[str, Any]) -> float:
    """Compute or return the impact score for an improvement item."""
    return item.get("impact_score", 0.0)


# ---------------------------------------------------------------------------
# Core Queue Generation
# ---------------------------------------------------------------------------


def generate_improvement_queue(
    gaps: Optional[List[Dict[str, Any]]] = None,
    patterns: Optional[List[Dict[str, Any]]] = None,
    krown_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a prioritized improvement queue.

    Args:
        gaps: List of gap dicts from alignment_engine.py
        patterns: List of pattern dicts from trade_validator.py
        krown_data: Dict of Krown reference data

    Returns:
        ImprovementQueue dict with items and summary
    """
    try:
        gaps = gaps or []
        patterns = patterns or []
        krown_data = krown_data or {}

        # Start with known improvements as the base queue
        items = []
        for known in KNOWN_IMPROVEMENTS:
            item = dict(known)  # shallow copy
            item["priority"] = compute_priority(
                item.get("impact_score", 0.0), item.get("effort", "MEDIUM")
            )
            items.append(item)

        # Cross-reference with alignment gaps
        items = _apply_gap_overrides(items, gaps)

        # Cross-reference with trade validator patterns
        items = _apply_pattern_overrides(items, patterns)

        # Cross-reference with Krown data
        items = _apply_krown_overrides(items, krown_data)

        # Sort by priority (HIGH first), then by impact_score descending
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        items.sort(key=lambda x: (priority_order.get(x.get("priority", "LOW"), 99), -x.get("impact_score", 0.0)))

        # Re-assign sequential IDs after sorting
        for idx, item in enumerate(items, start=1):
            item["id"] = f"IMP-{idx:03d}"

        # Build summary
        high_count = sum(1 for i in items if i.get("priority") == "HIGH")
        med_count = sum(1 for i in items if i.get("priority") == "MEDIUM")
        low_count = sum(1 for i in items if i.get("priority") == "LOW")
        total_gain = sum(i.get("estimated_alignment_gain", 0.0) for i in items)

        queue = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
            "summary": {
                "total_items": len(items),
                "high_priority": high_count,
                "medium_priority": med_count,
                "low_priority": low_count,
                "total_estimated_alignment_gain": round(total_gain, 2),
            },
        }

        return queue

    except Exception as e:
        print(f"[ERROR] generate_improvement_queue: {e}", file=sys.stderr)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "summary": {
                "total_items": 0,
                "high_priority": 0,
                "medium_priority": 0,
                "low_priority": 0,
                "total_estimated_alignment_gain": 0.0,
            },
        }


def _apply_gap_overrides(
    items: List[Dict[str, Any]], gaps: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Cross-reference known items with alignment gaps to adjust scores/status."""
    try:
        if not gaps:
            return items

        for item in items:
            title_lower = item.get("title", "").lower()
            for gap in gaps:
                gap_title = gap.get("title", "").lower()
                gap_desc = gap.get("description", "").lower()

                # Check if gap matches this item
                if title_lower in gap_title or title_lower in gap_desc:
                    # Boost impact if gap is severe
                    gap_severity = gap.get("severity", 0.0)
                    if gap_severity > 0:
                        item["impact_score"] = max(
                            item.get("impact_score", 0.0), gap_severity
                        )
                        item["estimated_alignment_gain"] = item["impact_score"]
                        item["priority"] = compute_priority(
                            item["impact_score"], item.get("effort", "MEDIUM")
                        )

                    # Update status if gap indicates partial build
                    gap_status = gap.get("kabroda_status", "")
                    if gap_status:
                        item["kabroda_status"] = gap_status

        return items

    except Exception as e:
        print(f"[WARN] _apply_gap_overrides: {e}", file=sys.stderr)
        return items


def _apply_pattern_overrides(
    items: List[Dict[str, Any]], patterns: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Cross-reference known items with trade validator patterns."""
    try:
        if not patterns:
            return items

        for item in items:
            title_lower = item.get("title", "").lower()
            for pattern in patterns:
                pattern_name = pattern.get("name", "").lower()
                pattern_desc = pattern.get("description", "").lower()

                if title_lower in pattern_name or title_lower in pattern_desc:
                    # Patterns can confirm the need for an improvement
                    pattern_frequency = pattern.get("frequency", 0)
                    if pattern_frequency > 0:
                        item["krown_frequency"] = max(
                            item.get("krown_frequency", 0), pattern_frequency
                        )

        return items

    except Exception as e:
        print(f"[WARN] _apply_pattern_overrides: {e}", file=sys.stderr)
        return items


def _apply_krown_overrides(
    items: List[Dict[str, Any]], krown_data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Cross-reference known items with Krown reference data."""
    try:
        if not krown_data:
            return items

        # Extract indicator settings from Krown data
        indicator_settings = krown_data.get("indicator_settings", {})
        strategies = krown_data.get("execution_rules", {})

        for item in items:
            title_lower = item.get("title", "").lower()

            # Check if Krown data mentions this item's indicator
            for ind_name, ind_settings in indicator_settings.items():
                if isinstance(ind_settings, dict):
                    if title_lower in ind_name.lower():
                        # Krown has settings for this — increase relevance
                        item["krown_frequency"] = max(
                            item.get("krown_frequency", 0), 5
                        )

            # Check strategies
            for strat_key, strat_data in strategies.items():
                if isinstance(strat_data, dict):
                    strat_str = json.dumps(strat_data).lower()
                    if title_lower in strat_str:
                        item["krown_frequency"] = max(
                            item.get("krown_frequency", 0), 3
                        )

        return items

    except Exception as e:
        print(f"[WARN] _apply_krown_overrides: {e}", file=sys.stderr)
        return items


# ---------------------------------------------------------------------------
# Output Writers
# ---------------------------------------------------------------------------


def generate_queue_json(
    queue: Dict[str, Any],
    output_dir: Optional[str] = None,
) -> str:
    """Write the improvement queue to a JSON file.

    Args:
        queue: The improvement queue dict
        output_dir: Directory to write to (defaults to kqal/output/)

    Returns:
        Path to the written file
    """
    try:
        output_dir = output_dir or OUTPUT_DIR
        os.makedirs(output_dir, exist_ok=True)

        path = os.path.join(output_dir, "kabroda_improvement_queue.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, default=str)

        print(f"  [OUTPUT] Improvement queue: {path}")
        return path

    except Exception as e:
        print(f"[ERROR] generate_queue_json: {e}", file=sys.stderr)
        return ""


def generate_agent_prompt(
    queue: Dict[str, Any],
    alignment_report: Optional[Dict[str, Any]] = None,
    validation_report: Optional[Dict[str, Any]] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Write a Claude Code-ready agent prompt as a markdown file.

    Args:
        queue: The improvement queue dict
        alignment_report: Optional alignment report from alignment_engine.py
        validation_report: Optional validation report from trade_validator.py
        output_dir: Directory to write to (defaults to kqal/output/)

    Returns:
        Path to the written file
    """
    try:
        output_dir = output_dir or OUTPUT_DIR
        os.makedirs(output_dir, exist_ok=True)

        alignment_report = alignment_report or {}
        validation_report = validation_report or {}

        # Extract alignment score
        alignment_score = alignment_report.get("alignment_score", "N/A")
        if isinstance(alignment_score, (int, float)):
            alignment_score = f"{alignment_score}/10"

        items = queue.get("items", [])
        summary = queue.get("summary", {})
        generated_at = queue.get("generated_at", "N/A")

        # Format date for display
        try:
            dt = datetime.fromisoformat(generated_at)
            date_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            date_str = generated_at

        lines = []
        lines.append(f"# KQAL Improvement Queue — {date_str}")
        lines.append("")
        lines.append(f"## Current Alignment Score: {alignment_score}")
        lines.append("")

        # Summary stats
        lines.append("### Queue Summary")
        lines.append(f"- **Total Items:** {summary.get('total_items', 0)}")
        lines.append(f"- **High Priority:** {summary.get('high_priority', 0)}")
        lines.append(f"- **Medium Priority:** {summary.get('medium_priority', 0)}")
        lines.append(f"- **Low Priority:** {summary.get('low_priority', 0)}")
        lines.append(f"- **Total Estimated Alignment Gain:** +{summary.get('total_estimated_alignment_gain', 0.0)}")
        lines.append("")

        # Group items by priority
        priority_groups = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for item in items:
            p = item.get("priority", "LOW")
            priority_groups.setdefault(p, []).append(item)

        for priority_label in ["HIGH", "MEDIUM", "LOW"]:
            group = priority_groups.get(priority_label, [])
            if not group:
                continue

            lines.append(f"## {priority_label.title()} Priority Items")
            lines.append("")

            for item in group:
                item_id = item.get("id", "IMP-???")
                title = item.get("title", "Untitled")
                impact = item.get("impact_score", 0.0)
                effort = item.get("effort", "MEDIUM")
                category = item.get("category", "MISSING_FEATURE")
                description = item.get("description", "")
                status = item.get("kabroda_status", "NOT_BUILT")
                frequency = item.get("krown_frequency", 0)
                prompt = item.get("claude_code_prompt", "")
                deps = item.get("dependencies", [])
                gain = item.get("estimated_alignment_gain", 0.0)

                lines.append(f"### {item_id}: {title}")
                lines.append(f"**Impact:** +{impact} alignment score | **Effort:** {effort} | **Category:** {category}")
                lines.append(f"**Status:** {status} | **Krown Frequency:** {frequency}/15 videos")
                if deps:
                    lines.append(f"**Dependencies:** {', '.join(deps)}")
                lines.append(f"**Estimated Alignment Gain:** +{gain}")
                lines.append("")
                lines.append(f"**Why:** {description}")
                lines.append("")
                lines.append("**Build instructions:**")
                lines.append("")
                for line in prompt.strip().split("\n"):
                    lines.append(line)
                lines.append("")

            lines.append("---")
            lines.append("")

        # Add alignment report context if available
        if alignment_report:
            lines.append("## Alignment Report Context")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(alignment_report, indent=2, default=str))
            lines.append("```")
            lines.append("")

        # Add validation report context if available
        if validation_report:
            lines.append("## Validation Report Context")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(validation_report, indent=2, default=str))
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append(f"*Generated by KQAL Improvement Queue at {generated_at}*")
        lines.append("")

        content = "\n".join(lines)

        path = os.path.join(output_dir, "kabroda_agent_prompt.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"  [OUTPUT] Agent prompt: {path}")
        return path

    except Exception as e:
        print(f"[ERROR] generate_agent_prompt: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Sample Data Generators (for testing / __main__)
# ---------------------------------------------------------------------------


def _sample_gaps() -> List[Dict[str, Any]]:
    """Generate sample alignment gaps for testing."""
    return [
        {
            "title": "Revin Ribbons Indicator Missing",
            "description": "Krown references Revin Ribbons midband in 8/15 videos. Kabroda has no equivalent.",
            "severity": 1.5,
            "kabroda_status": "NOT_BUILT",
            "category": "MISSING_INDICATOR",
        },
        {
            "title": "SSE-into-TSA Target Wiring",
            "description": "SSE S/R levels not wired into target computation.",
            "severity": 1.5,
            "kabroda_status": "NOT_BUILT",
            "category": "ARCHITECTURE_GAP",
        },
        {
            "title": "Position Sizing Missing",
            "description": "No position sizing mechanism exists in Kabroda.",
            "severity": 1.5,
            "kabroda_status": "NOT_BUILT",
            "category": "MISSING_FEATURE",
        },
    ]


def _sample_patterns() -> List[Dict[str, Any]]:
    """Generate sample trade validator patterns for testing."""
    return [
        {
            "name": "Three Drives Divergence",
            "description": "Krown requires 3 drives to confirm divergence. Kabroda detects single divergences.",
            "frequency": 5,
            "category": "MISSING_PATTERN",
        },
        {
            "name": "Fibonacci EMA Ribbon",
            "description": "Krown uses 5/21/55/377 EMA ribbon. Kabroda uses 9/21/35/55 SMA.",
            "frequency": 6,
            "category": "MISSING_INDICATOR",
        },
    ]


def _sample_krown_data() -> Dict[str, Any]:
    """Generate sample Krown reference data for testing."""
    return {
        "indicator_settings": {
            "BBWP": {
                "bb_length": 20,
                "bb_stdev": 2.0,
                "lookback_period": 252,
                "ma_type": "SMA",
            },
            "PMARP": {
                "base_ma_length": 50,
                "lookback_period": 252,
            },
            "RSI": {
                "length": 14,
                "source": "close",
                "overbought": 70.0,
                "oversold": 30.0,
            },
            "Moving_Averages": {
                "fast_trend_sma": 20,
                "macro_trend_sma": 50,
            },
        },
        "execution_rules": {
            "Strategy_1_Macro_Trend": {
                "entry_long": "Close > 20 SMA AND BBWP expanding from <= 15.0%",
                "stop_loss_long": "Previous swing low",
                "take_profit_long": "Close < 20 SMA OR PMARP >= 95.0%",
            },
            "Strategy_2_Uptrend_Pullback": {
                "entry_long": "Trend Higher Highs/Lows AND Pullback between 20 & 50 SMA AND (RSI 40-50 OR Hidden Bullish Divergence)",
                "stop_loss_long": "Below 50 SMA",
                "take_profit_long": "1.272 Fibonacci Extension",
            },
        },
    }


def _sample_alignment_report() -> Dict[str, Any]:
    """Generate a sample alignment report for testing."""
    return {
        "alignment_score": 7.2,
        "total_checks": 15,
        "passed_checks": 11,
        "failed_checks": 4,
        "gaps_found": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _sample_validation_report() -> Dict[str, Any]:
    """Generate a sample validation report for testing."""
    return {
        "total_patterns_checked": 8,
        "patterns_matched": 5,
        "patterns_missing": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Run the improvement queue generator with sample data."""
    print(f"\n{'#'*60}")
    print(f"# KQAL — Improvement Queue Generator")
    print(f"{'#'*60}\n")

    # Generate sample data
    print("[SAMPLE] Generating sample gaps, patterns, and Krown data...")
    gaps = _sample_gaps()
    patterns = _sample_patterns()
    krown_data = _sample_krown_data()
    alignment_report = _sample_alignment_report()
    validation_report = _sample_validation_report()

    print(f"[GAPS] {len(gaps)} alignment gaps loaded")
    print(f"[PATTERNS] {len(patterns)} trade patterns loaded")
    print(f"[KROWN] Krown reference data loaded")
    print()

    # Generate the queue
    print("[QUEUE] Generating improvement queue...")
    queue = generate_improvement_queue(
        gaps=gaps, patterns=patterns, krown_data=krown_data
    )

    # Print summary
    summary = queue.get("summary", {})
    print(f"\n{'='*60}")
    print(f"Queue Summary:")
    print(f"  Total Items: {summary.get('total_items', 0)}")
    print(f"  High Priority: {summary.get('high_priority', 0)}")
    print(f"  Medium Priority: {summary.get('medium_priority', 0)}")
    print(f"  Low Priority: {summary.get('low_priority', 0)}")
    print(f"  Total Alignment Gain: +{summary.get('total_estimated_alignment_gain', 0.0)}")
    print(f"{'='*60}\n")

    # Print items
    for item in queue.get("items", []):
        print(f"  [{item.get('priority', '?')}] {item.get('id', '?')}: {item.get('title', '?')}")
        print(f"       Impact: +{item.get('impact_score', 0.0)} | Effort: {item.get('effort', '?')} | Status: {item.get('kabroda_status', '?')}")
        print()

    # Write outputs
    print("[OUTPUT] Writing queue JSON...")
    json_path = generate_queue_json(queue)

    print("[OUTPUT] Writing agent prompt...")
    prompt_path = generate_agent_prompt(queue, alignment_report, validation_report)

    print(f"\n[DONE] Output files:")
    print(f"  JSON:   {json_path}")
    print(f"  Prompt: {prompt_path}")
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="KQAL Improvement Queue Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--gaps",
        type=str,
        default=None,
        help="Path to alignment gaps JSON file",
    )
    parser.add_argument(
        "--patterns",
        type=str,
        default=None,
        help="Path to trade validator patterns JSON file",
    )
    parser.add_argument(
        "--krown",
        type=str,
        default=None,
        help="Path to Krown reference data JSON file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for generated files",
    )

    args = parser.parse_args()

    if args.gaps or args.patterns or args.krown:
        # Production mode: load from files
        gaps = []
        patterns = []
        krown_data = {}

        if args.gaps:
            with open(args.gaps, "r", encoding="utf-8") as f:
                gaps = json.load(f)
        if args.patterns:
            with open(args.patterns, "r", encoding="utf-8") as f:
                patterns = json.load(f)
        if args.krown:
            with open(args.krown, "r", encoding="utf-8") as f:
                krown_data = json.load(f)

        queue = generate_improvement_queue(
            gaps=gaps, patterns=patterns, krown_data=krown_data
        )

        output_dir = args.output_dir or OUTPUT_DIR
        generate_queue_json(queue, output_dir)
        generate_agent_prompt(queue, output_dir=output_dir)
    else:
        # Demo mode: run with sample data
        main()
