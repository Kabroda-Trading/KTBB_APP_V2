#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KQAL — Kabroda Quality Assurance Layer
=======================================
Trade Validator Module
Analyzes trade outcomes against Krown framework alignment.

This module:
  1. Receives trade data from db_reader.py and alignment data from alignment_engine.py
  2. Produces statistical analysis of how alignment correlates with outcomes
  3. Identifies actionable patterns for strategy improvement

Usage:
  from kqal.trade_validator import validate_trades

  report = validate_trades(trades_list, alignment_data)
  for pattern in report["patterns"]:
      print(f"{pattern['pattern']}: {pattern['recommendation']}")
"""

import os
import sys
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Fix Windows console encoding for emoji support
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KQAL_OUTPUT_DIR = os.path.join(BASE_DIR, "kqal", "output")
os.makedirs(KQAL_OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INSUFFICIENT_N = 5  # Minimum sample size for reliable statistics

TIMEFRAMES = ["15M", "1H", "4H"]

INDICATORS = ["bbwp", "pmarp", "kinematic_grade", "energy_grade"]

STRATEGY_NAMES = {
    "strategy_1": "Macro Trend Breakout",
    "strategy_2": "Uptrend Pullback Dip-Buy",
    "strategy_3": "Downtrend Continuation Rally-Sell",
    "strategy_4": "Counter-Trend Parabolic Exhaustion Short",
    "strategy_5": "Momentum Breakdown Short",
}

# ---------------------------------------------------------------------------
# Data Structures (type aliases for documentation)
# ---------------------------------------------------------------------------

# Trade dict fields:
#   id: str
#   symbol: str
#   direction: str  ("LONG" | "SHORT")
#   entry_price: float
#   exit_price: float
#   quantity: float
#   realized_pnl: float  (in R-multiples)
#   entry_time: str  (ISO format)
#   exit_time: str  (ISO format)
#   timeframe: str  ("15M" | "1H" | "4H")
#   strategy: str  ("strategy_1" ... "strategy_5")
#   kabroda_bias: str  ("bullish" | "bearish" | "neutral")
#   krown_bias: str  ("bullish" | "bearish" | "neutral")
#   indicators: dict  {indicator_name: state_string}
#   outcome: str  ("win" | "loss" | "expired")

# AlignmentData dict fields:
#   trades: dict  {trade_id: alignment_info}
#   alignment_info fields:
#     kabroda_bias: str
#     krown_bias: str
#     alignment: str  ("aligned" | "misaligned" | "neutral")

# ValidationReport dict structure (see validate_trades docstring)


# ===================================================================
# Helper: Safe Division
# ===================================================================

def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Divide a by b, returning default if b is zero or NaN."""
    try:
        if b is None or b == 0 or math.isnan(b):
            return default
        result = a / b
        return result if not math.isnan(result) and not math.isinf(result) else default
    except Exception:
        return default


# ===================================================================
# Helper: Compute Win Rate
# ===================================================================

def compute_win_rate(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute win/loss/expired counts and win rate for a list of trades.

    Returns:
        dict with keys:
            total_trades: int
            win_count: int
            loss_count: int
            expired_count: int
            win_rate: float  (0.0 to 1.0, 0 if no trades)
            insufficient_data: bool  (True if total < INSUFFICIENT_N)
    """
    try:
        total = len(trades)
        wins = sum(1 for t in trades if t.get("outcome") == "win")
        losses = sum(1 for t in trades if t.get("outcome") == "loss")
        expired = sum(1 for t in trades if t.get("outcome") == "expired")

        return {
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "expired_count": expired,
            "win_rate": safe_div(wins, total),
            "insufficient_data": total < INSUFFICIENT_N,
        }
    except Exception as e:
        return {
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "expired_count": 0,
            "win_rate": 0.0,
            "insufficient_data": True,
            "error": str(e),
        }


# ===================================================================
# Helper: Compute R-Multiple Statistics
# ===================================================================

def compute_r_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute R-multiple statistics for a list of trades.

    Returns:
        dict with keys:
            net_r: float  (sum of realized_pnl)
            avg_r: float  (average realized_pnl)
            avg_win_r: float  (average win R)
            avg_loss_r: float  (average loss R)
            profit_factor: float  (sum wins / abs(sum losses))
            max_win_r: float
            max_loss_r: float
            std_r: float  (standard deviation of R values)
            insufficient_data: bool
    """
    try:
        if not trades:
            return {
                "net_r": 0.0, "avg_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
                "profit_factor": 0.0, "max_win_r": 0.0, "max_loss_r": 0.0,
                "std_r": 0.0, "insufficient_data": True,
            }

        r_values = [t.get("realized_pnl", 0.0) or 0.0 for t in trades]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r < 0]

        net_r = sum(r_values)
        avg_r = safe_div(net_r, len(r_values))
        avg_win_r = safe_div(sum(wins), len(wins)) if wins else 0.0
        avg_loss_r = safe_div(sum(losses), len(losses)) if losses else 0.0

        sum_wins = sum(wins)
        sum_losses_abs = abs(sum(losses))
        profit_factor = safe_div(sum_wins, sum_losses_abs) if sum_losses_abs > 0 else (
            float("inf") if sum_wins > 0 else 0.0
        )

        max_win_r = max(wins) if wins else 0.0
        max_loss_r = min(losses) if losses else 0.0

        # Standard deviation
        if len(r_values) > 1:
            variance = sum((r - avg_r) ** 2 for r in r_values) / len(r_values)
            std_r = math.sqrt(variance)
        else:
            std_r = 0.0

        return {
            "net_r": round(net_r, 4),
            "avg_r": round(avg_r, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
            "profit_factor": round(profit_factor, 4),
            "max_win_r": round(max_win_r, 4),
            "max_loss_r": round(max_loss_r, 4),
            "std_r": round(std_r, 4),
            "insufficient_data": len(trades) < INSUFFICIENT_N,
        }
    except Exception as e:
        return {
            "net_r": 0.0, "avg_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
            "profit_factor": 0.0, "max_win_r": 0.0, "max_loss_r": 0.0,
            "std_r": 0.0, "insufficient_data": True, "error": str(e),
        }


# ===================================================================
# Helper: Split by Alignment
# ===================================================================

def split_by_alignment(
    trades: List[Dict[str, Any]], alignment_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Split trades into aligned, misaligned, and neutral groups based on
    Kabroda bias vs Krown bias alignment.

    Alignment logic:
      - aligned:   kabroda_bias == krown_bias (both bullish or both bearish)
      - misaligned: kabroda_bias != krown_bias and neither is neutral
      - neutral:   at least one bias is "neutral"

    Returns:
        dict with keys:
            aligned: dict with count, win_rate, net_r, avg_r, trades
            misaligned: dict with count, win_rate, net_r, avg_r, trades
            neutral: dict with count, win_rate, net_r, avg_r, trades
            alignment_delta: float (aligned win_rate - misaligned win_rate)
    """
    try:
        aligned = []
        misaligned = []
        neutral = []

        for t in trades:
            kab_bias = t.get("kabroda_bias", "neutral") or "neutral"
            kr_bias = t.get("krown_bias", "neutral") or "neutral"

            if kab_bias == "neutral" or kr_bias == "neutral":
                neutral.append(t)
            elif kab_bias == kr_bias:
                aligned.append(t)
            else:
                misaligned.append(t)

        def _build_group(group_trades: List[Dict]) -> Dict[str, Any]:
            wr = compute_win_rate(group_trades)
            rs = compute_r_stats(group_trades)
            return {
                "count": len(group_trades),
                "win_rate": wr["win_rate"],
                "win_count": wr["win_count"],
                "loss_count": wr["loss_count"],
                "expired_count": wr["expired_count"],
                "net_r": rs["net_r"],
                "avg_r": rs["avg_r"],
                "avg_win_r": rs["avg_win_r"],
                "avg_loss_r": rs["avg_loss_r"],
                "profit_factor": rs["profit_factor"],
                "insufficient_data": wr["insufficient_data"],
            }

        aligned_stats = _build_group(aligned)
        misaligned_stats = _build_group(misaligned)
        neutral_stats = _build_group(neutral)

        aligned_wr = aligned_stats["win_rate"]
        misaligned_wr = misaligned_stats["win_rate"]
        alignment_delta = aligned_wr - misaligned_wr

        return {
            "aligned": aligned_stats,
            "misaligned": misaligned_stats,
            "neutral": neutral_stats,
            "alignment_delta": round(alignment_delta, 4),
        }
    except Exception as e:
        return {
            "aligned": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
            "misaligned": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
            "neutral": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
            "alignment_delta": 0.0,
            "error": str(e),
        }


# ===================================================================
# Helper: Split by Timeframe
# ===================================================================

def split_by_timeframe(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Split trades by timeframe (15M, 1H, 4H).

    Returns:
        dict with:
            per_timeframe: dict mapping timeframe -> stats dict
            best_timeframe: str (timeframe with highest win_rate, or "N/A")
            worst_timeframe: str (timeframe with lowest win_rate, or "N/A")
    """
    try:
        grouped: Dict[str, List[Dict]] = {}
        for t in trades:
            tf = t.get("timeframe", "UNKNOWN")
            if tf not in grouped:
                grouped[tf] = []
            grouped[tf].append(t)

        per_timeframe = {}
        for tf in TIMEFRAMES:
            tf_trades = grouped.get(tf, [])
            wr = compute_win_rate(tf_trades)
            rs = compute_r_stats(tf_trades)
            per_timeframe[tf] = {
                "count": len(tf_trades),
                "win_rate": wr["win_rate"],
                "win_count": wr["win_count"],
                "loss_count": wr["loss_count"],
                "net_r": rs["net_r"],
                "avg_r": rs["avg_r"],
                "avg_win_r": rs["avg_win_r"],
                "avg_loss_r": rs["avg_loss_r"],
                "profit_factor": rs["profit_factor"],
                "insufficient_data": wr["insufficient_data"],
            }

        # Also include any unknown timeframes
        for tf in grouped:
            if tf not in TIMEFRAMES:
                tf_trades = grouped[tf]
                wr = compute_win_rate(tf_trades)
                rs = compute_r_stats(tf_trades)
                per_timeframe[tf] = {
                    "count": len(tf_trades),
                    "win_rate": wr["win_rate"],
                    "win_count": wr["win_count"],
                    "loss_count": wr["loss_count"],
                    "net_r": rs["net_r"],
                    "avg_r": rs["avg_r"],
                    "avg_win_r": rs["avg_win_r"],
                    "avg_loss_r": rs["avg_loss_r"],
                    "profit_factor": rs["profit_factor"],
                    "insufficient_data": wr["insufficient_data"],
                }

        # Determine best/worst timeframes (only among those with sufficient data)
        valid_tfs = {
            tf: stats for tf, stats in per_timeframe.items()
            if stats["count"] >= INSUFFICIENT_N and stats["count"] > 0
        }

        best_tf = "N/A"
        worst_tf = "N/A"
        if valid_tfs:
            best_tf = max(valid_tfs, key=lambda tf: valid_tfs[tf]["win_rate"])
            worst_tf = min(valid_tfs, key=lambda tf: valid_tfs[tf]["win_rate"])

        return {
            "per_timeframe": per_timeframe,
            "best_timeframe": best_tf,
            "worst_timeframe": worst_tf,
        }
    except Exception as e:
        return {
            "per_timeframe": {},
            "best_timeframe": "N/A",
            "worst_timeframe": "N/A",
            "error": str(e),
        }


# ===================================================================
# Helper: Strategy Performance
# ===================================================================

def compute_strategy_performance(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute performance breakdown by strategy type.

    Returns:
        dict mapping strategy_id -> stats dict
        Also includes a 'best_strategies' list and 'worst_strategies' list.
    """
    try:
        grouped: Dict[str, List[Dict]] = {}
        for t in trades:
            strat = t.get("strategy", "unknown")
            if strat not in grouped:
                grouped[strat] = []
            grouped[strat].append(t)

        per_strategy = {}
        for strat_id, strat_trades in grouped.items():
            wr = compute_win_rate(strat_trades)
            rs = compute_r_stats(strat_trades)
            display_name = STRATEGY_NAMES.get(strat_id, strat_id.replace("_", " ").title())
            per_strategy[strat_id] = {
                "name": display_name,
                "count": len(strat_trades),
                "win_rate": wr["win_rate"],
                "win_count": wr["win_count"],
                "loss_count": wr["loss_count"],
                "net_r": rs["net_r"],
                "avg_r": rs["avg_r"],
                "avg_win_r": rs["avg_win_r"],
                "avg_loss_r": rs["avg_loss_r"],
                "profit_factor": rs["profit_factor"],
                "insufficient_data": wr["insufficient_data"],
            }

        # Rank strategies
        valid_strats = {
            sid: s for sid, s in per_strategy.items()
            if s["count"] >= INSUFFICIENT_N
        }

        sorted_by_wr = sorted(valid_strats.items(), key=lambda x: x[1]["win_rate"], reverse=True)
        sorted_by_net_r = sorted(valid_strats.items(), key=lambda x: x[1]["net_r"], reverse=True)

        return {
            "per_strategy": per_strategy,
            "best_by_win_rate": [sid for sid, _ in sorted_by_wr[:3]],
            "worst_by_win_rate": [sid for sid, _ in sorted_by_wr[-3:]] if len(sorted_by_wr) >= 3 else [sid for sid, _ in sorted_by_wr],
            "best_by_net_r": [sid for sid, _ in sorted_by_net_r[:3]],
        }
    except Exception as e:
        return {
            "per_strategy": {},
            "best_by_win_rate": [],
            "worst_by_win_rate": [],
            "best_by_net_r": [],
            "error": str(e),
        }


# ===================================================================
# Helper: Indicator Correlation
# ===================================================================

def compute_indicator_correlation(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    For each indicator (BBWP, PMARP, kinematic_grade, energy_grade),
    compute per-state win rates and R-multiple stats.

    Returns:
        dict mapping indicator_name -> dict with:
            per_state: dict mapping state -> stats
            best_state: str (state with highest win_rate)
            worst_state: str (state with lowest win_rate)
    """
    try:
        result = {}
        for ind_name in INDICATORS:
            # Group trades by indicator state
            state_groups: Dict[str, List[Dict]] = {}
            for t in trades:
                indicators = t.get("indicators", {}) or {}
                state = indicators.get(ind_name, "unknown")
                if state not in state_groups:
                    state_groups[state] = []
                state_groups[state].append(t)

            per_state = {}
            for state, state_trades in state_groups.items():
                wr = compute_win_rate(state_trades)
                rs = compute_r_stats(state_trades)
                per_state[state] = {
                    "count": len(state_trades),
                    "win_rate": wr["win_rate"],
                    "win_count": wr["win_count"],
                    "loss_count": wr["loss_count"],
                    "net_r": rs["net_r"],
                    "avg_r": rs["avg_r"],
                    "avg_win_r": rs["avg_win_r"],
                    "avg_loss_r": rs["avg_loss_r"],
                    "profit_factor": rs["profit_factor"],
                    "insufficient_data": wr["insufficient_data"],
                }

            # Best/worst states (only those with sufficient data)
            valid_states = {
                s: st for s, st in per_state.items()
                if st["count"] >= INSUFFICIENT_N
            }
            best_state = max(valid_states, key=lambda s: valid_states[s]["win_rate"]) if valid_states else "N/A"
            worst_state = min(valid_states, key=lambda s: valid_states[s]["win_rate"]) if valid_states else "N/A"

            result[ind_name] = {
                "per_state": per_state,
                "best_state": best_state,
                "worst_state": worst_state,
            }

        return result
    except Exception as e:
        return {ind: {"per_state": {}, "best_state": "N/A", "worst_state": "N/A", "error": str(e)} for ind in INDICATORS}


# ===================================================================
# Helper: Recent Trend
# ===================================================================

def compute_recent_trend(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute recent performance trends.

    Returns:
        dict with:
            last_10: dict (win_rate, net_r, avg_r for most recent 10 trades)
            rolling_7d: dict (win_rate, net_r, avg_r for last 7 days)
            trend_direction: str ("improving" | "declining" | "stable" | "insufficient_data")
            trend_confidence: str ("high" | "medium" | "low")
    """
    try:
        if not trades:
            return {
                "last_10": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
                "rolling_7d": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
                "trend_direction": "insufficient_data",
                "trend_confidence": "low",
            }

        # Sort trades by exit_time descending (most recent first)
        sorted_trades = sorted(
            trades,
            key=lambda t: t.get("exit_time", t.get("entry_time", "")),
            reverse=True,
        )

        # Last 10 trades
        last_10_trades = sorted_trades[:10]
        last_10_wr = compute_win_rate(last_10_trades)
        last_10_rs = compute_r_stats(last_10_trades)
        last_10 = {
            "count": len(last_10_trades),
            "win_rate": last_10_wr["win_rate"],
            "win_count": last_10_wr["win_count"],
            "loss_count": last_10_wr["loss_count"],
            "net_r": last_10_rs["net_r"],
            "avg_r": last_10_rs["avg_r"],
            "avg_win_r": last_10_rs["avg_win_r"],
            "avg_loss_r": last_10_rs["avg_loss_r"],
            "profit_factor": last_10_rs["profit_factor"],
            "insufficient_data": len(last_10_trades) < INSUFFICIENT_N,
        }

        # Rolling 7 days
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)
        recent_7d_trades = [
            t for t in trades
            if _parse_time(t.get("exit_time", t.get("entry_time", ""))) >= seven_days_ago
        ]
        r7d_wr = compute_win_rate(recent_7d_trades)
        r7d_rs = compute_r_stats(recent_7d_trades)
        rolling_7d = {
            "count": len(recent_7d_trades),
            "win_rate": r7d_wr["win_rate"],
            "win_count": r7d_wr["win_count"],
            "loss_count": r7d_wr["loss_count"],
            "net_r": r7d_rs["net_r"],
            "avg_r": r7d_rs["avg_r"],
            "avg_win_r": r7d_rs["avg_win_r"],
            "avg_loss_r": r7d_rs["avg_loss_r"],
            "profit_factor": r7d_rs["profit_factor"],
            "insufficient_data": len(recent_7d_trades) < INSUFFICIENT_N,
        }

        # Determine trend direction by comparing recent vs older performance
        trend_direction = "insufficient_data"
        trend_confidence = "low"

        if len(sorted_trades) >= 10:
            # Compare last 5 vs previous 5
            recent_5 = sorted_trades[:5]
            older_5 = sorted_trades[5:10]

            recent_wr = compute_win_rate(recent_5)["win_rate"]
            older_wr = compute_win_rate(older_5)["win_rate"]

            recent_net = compute_r_stats(recent_5)["net_r"]
            older_net = compute_r_stats(older_5)["net_r"]

            # Use both win_rate and net_r to determine trend
            wr_improving = recent_wr > older_wr + 0.05
            wr_declining = recent_wr < older_wr - 0.05
            net_improving = recent_net > older_net + 0.5
            net_declining = recent_net < older_net - 0.5

            if (wr_improving and net_improving) or (wr_improving and abs(recent_net - older_net) < 1.0):
                trend_direction = "improving"
                trend_confidence = "high" if (wr_improving and net_improving) else "medium"
            elif (wr_declining and net_declining) or (wr_declining and abs(recent_net - older_net) < 1.0):
                trend_direction = "declining"
                trend_confidence = "high" if (wr_declining and net_declining) else "medium"
            else:
                trend_direction = "stable"
                trend_confidence = "medium"

        return {
            "last_10": last_10,
            "rolling_7d": rolling_7d,
            "trend_direction": trend_direction,
            "trend_confidence": trend_confidence,
        }
    except Exception as e:
        return {
            "last_10": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
            "rolling_7d": {"count": 0, "win_rate": 0.0, "net_r": 0.0, "insufficient_data": True},
            "trend_direction": "insufficient_data",
            "trend_confidence": "low",
            "error": str(e),
        }


def _parse_time(time_str: str) -> datetime:
    """Parse an ISO format time string, returning epoch on failure."""
    try:
        if not time_str:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        # Handle various ISO formats
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        return datetime.fromisoformat(time_str)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


# ===================================================================
# Pattern Identification
# ===================================================================

def identify_patterns(validation_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Analyze the validation report and identify actionable patterns.

    Pattern types:
      - ALIGNMENT_MATTERS: Clear win rate difference between aligned and misaligned
      - TIMEFRAME_SPECIALIZATION: One timeframe significantly outperforms
      - INDICATOR_SIGNAL: Specific indicator state predicts outcomes
      - STRATEGY_STRENGTH: Specific strategy type works well
      - RECENT_DECLINE: Performance is trending down
      - RECENT_IMPROVEMENT: Performance is trending up
      - LOW_CONFIDENCE: Not enough data for reliable patterns

    Returns:
        list of pattern dicts with keys:
            pattern: str (pattern type name)
            evidence: str (description of the evidence)
            confidence: str ("high" | "medium" | "low")
            recommendation: str (actionable recommendation)
    """
    patterns = []

    try:
        # --- ALIGNMENT_MATTERS ---
        alignment = validation_report.get("alignment_based", {})
        aligned = alignment.get("aligned", {})
        misaligned = alignment.get("misaligned", {})
        alignment_delta = alignment.get("alignment_delta", 0.0)

        if aligned.get("count", 0) >= INSUFFICIENT_N and misaligned.get("count", 0) >= INSUFFICIENT_N:
            if abs(alignment_delta) >= 0.10:
                direction = "higher" if alignment_delta > 0 else "lower"
                patterns.append({
                    "pattern": "ALIGNMENT_MATTERS",
                    "evidence": (
                        f"Aligned trades win rate ({aligned['win_rate']:.1%}) is {direction} "
                        f"than misaligned ({misaligned['win_rate']:.1%}) by {abs(alignment_delta):.1%}. "
                        f"Aligned N={aligned['count']}, Misaligned N={misaligned['count']}."
                    ),
                    "confidence": "high" if abs(alignment_delta) >= 0.20 else "medium",
                    "recommendation": (
                        "Prioritize trades where Kabroda and Krown biases align. "
                        "Avoid or reduce size on misaligned setups."
                        if alignment_delta > 0 else
                        "Re-evaluate alignment logic — misaligned trades are outperforming aligned ones."
                    ),
                })

        # --- TIMEFRAME_SPECIALIZATION ---
        tf_data = validation_report.get("timeframe_breakdown", {})
        per_tf = tf_data.get("per_timeframe", {})
        best_tf = tf_data.get("best_timeframe", "N/A")
        worst_tf = tf_data.get("worst_timeframe", "N/A")

        if best_tf != "N/A" and worst_tf != "N/A" and best_tf != worst_tf:
            best_stats = per_tf.get(best_tf, {})
            worst_stats = per_tf.get(worst_tf, {})
            tf_delta = best_stats.get("win_rate", 0) - worst_stats.get("win_rate", 0)
            if abs(tf_delta) >= 0.10:
                patterns.append({
                    "pattern": "TIMEFRAME_SPECIALIZATION",
                    "evidence": (
                        f"Best timeframe: {best_tf} ({best_stats.get('win_rate', 0):.1%} win rate, "
                        f"N={best_stats.get('count', 0)}). "
                        f"Worst: {worst_tf} ({worst_stats.get('win_rate', 0):.1%} win rate, "
                        f"N={worst_stats.get('count', 0)}). "
                        f"Delta: {abs(tf_delta):.1%}."
                    ),
                    "confidence": "high" if abs(tf_delta) >= 0.20 else "medium",
                    "recommendation": (
                        f"Focus on {best_tf} timeframe trades where performance is strongest. "
                        f"Consider filtering out {worst_tf} setups or reducing position size."
                    ),
                })

        # --- INDICATOR_SIGNAL ---
        ind_corr = validation_report.get("indicator_correlation", {})
        for ind_name, ind_data in ind_corr.items():
            best_state = ind_data.get("best_state", "N/A")
            worst_state = ind_data.get("worst_state", "N/A")
            per_state = ind_data.get("per_state", {})

            if best_state != "N/A" and worst_state != "N/A" and best_state != worst_state:
                best_s = per_state.get(best_state, {})
                worst_s = per_state.get(worst_state, {})
                if best_s.get("count", 0) >= INSUFFICIENT_N and worst_s.get("count", 0) >= INSUFFICIENT_N:
                    ind_delta = best_s.get("win_rate", 0) - worst_s.get("win_rate", 0)
                    if abs(ind_delta) >= 0.10:
                        patterns.append({
                            "pattern": "INDICATOR_SIGNAL",
                            "evidence": (
                                f"Indicator '{ind_name}': state '{best_state}' predicts wins "
                                f"({best_s.get('win_rate', 0):.1%}, N={best_s.get('count', 0)}) "
                                f"vs state '{worst_state}' ({worst_s.get('win_rate', 0):.1%}, "
                                f"N={worst_s.get('count', 0)}). Delta: {abs(ind_delta):.1%}."
                            ),
                            "confidence": "high" if abs(ind_delta) >= 0.20 else "medium",
                            "recommendation": (
                                f"Favor trades where {ind_name} is in '{best_state}' state. "
                                f"Avoid or reduce size when in '{worst_state}' state."
                            ),
                        })

        # --- STRATEGY_STRENGTH ---
        strat_perf = validation_report.get("strategy_performance", {})
        per_strat = strat_perf.get("per_strategy", {})
        best_strats = strat_perf.get("best_by_win_rate", [])
        worst_strats = strat_perf.get("worst_by_win_rate", [])

        if best_strats and worst_strats:
            best_sid = best_strats[0]
            worst_sid = worst_strats[0]
            best_s = per_strat.get(best_sid, {})
            worst_s = per_strat.get(worst_sid, {})

            if best_s.get("count", 0) >= INSUFFICIENT_N and worst_s.get("count", 0) >= INSUFFICIENT_N:
                strat_delta = best_s.get("win_rate", 0) - worst_s.get("win_rate", 0)
                if abs(strat_delta) >= 0.10:
                    patterns.append({
                        "pattern": "STRATEGY_STRENGTH",
                        "evidence": (
                            f"Best strategy: {best_s.get('name', best_sid)} "
                            f"({best_s.get('win_rate', 0):.1%} win rate, "
                            f"R={best_s.get('net_r', 0):.2f}, N={best_s.get('count', 0)}). "
                            f"Worst: {worst_s.get('name', worst_sid)} "
                            f"({worst_s.get('win_rate', 0):.1%} win rate, "
                            f"R={worst_s.get('net_r', 0):.2f}, N={worst_s.get('count', 0)})."
                        ),
                        "confidence": "high" if abs(strat_delta) >= 0.20 else "medium",
                        "recommendation": (
                            f"Allocate more capital to {best_s.get('name', best_sid)}. "
                            f"Review and refine {worst_s.get('name', worst_sid)} entry criteria."
                        ),
                    })

        # --- RECENT_DECLINE / RECENT_IMPROVEMENT ---
        recent = validation_report.get("recent_trend", {})
        trend_dir = recent.get("trend_direction", "insufficient_data")
        trend_conf = recent.get("trend_confidence", "low")
        last_10 = recent.get("last_10", {})
        rolling_7d = recent.get("rolling_7d", {})

        if trend_dir == "declining" and trend_conf in ("high", "medium"):
            patterns.append({
                "pattern": "RECENT_DECLINE",
                "evidence": (
                    f"Last 10 trades: {last_10.get('win_rate', 0):.1%} win rate, "
                    f"{last_10.get('net_r', 0):.2f} net R. "
                    f"7-day rolling: {rolling_7d.get('win_rate', 0):.1%} win rate, "
                    f"{rolling_7d.get('net_r', 0):.2f} net R. "
                    f"Confidence: {trend_conf}."
                ),
                "confidence": trend_conf,
                "recommendation": (
                    "Reduce position sizes and tighten risk parameters. "
                    "Review recent trades for common failure patterns. "
                    "Consider sitting out until conditions improve."
                ),
            })

        if trend_dir == "improving" and trend_conf in ("high", "medium"):
            patterns.append({
                "pattern": "RECENT_IMPROVEMENT",
                "evidence": (
                    f"Last 10 trades: {last_10.get('win_rate', 0):.1%} win rate, "
                    f"{last_10.get('net_r', 0):.2f} net R. "
                    f"7-day rolling: {rolling_7d.get('win_rate', 0):.1%} win rate, "
                    f"{rolling_7d.get('net_r', 0):.2f} net R. "
                    f"Confidence: {trend_conf}."
                ),
                "confidence": trend_conf,
                "recommendation": (
                    "Gradually increase position sizes. "
                    "Analyze what changed in recent winning trades to reinforce the approach."
                ),
            })

        # --- LOW_CONFIDENCE ---
        total = validation_report.get("overall", {}).get("total_trades", 0)
        if total < INSUFFICIENT_N * 2:
            patterns.append({
                "pattern": "LOW_CONFIDENCE",
                "evidence": (
                    f"Only {total} total trades available for analysis. "
                    f"Minimum recommended sample is {INSUFFICIENT_N * 2} for reliable pattern detection."
                ),
                "confidence": "low",
                "recommendation": (
                    "Continue collecting trade data. Avoid making strategy changes "
                    "based on limited sample sizes."
                ),
            })

    except Exception as e:
        patterns.append({
            "pattern": "LOW_CONFIDENCE",
            "evidence": f"Error during pattern identification: {e}",
            "confidence": "low",
            "recommendation": "Review trade data integrity and re-run validation.",
        })

    return patterns


# ===================================================================
# Main Validation Function
# ===================================================================

def validate_trades(
    trades: List[Dict[str, Any]],
    alignment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Main entry point: produce a comprehensive ValidationReport.

    Args:
        trades: List of trade dicts from db_reader.
            Each trade should have at minimum:
                id, realized_pnl, outcome, timeframe, strategy,
                kabroda_bias, krown_bias, indicators, entry_time, exit_time
        alignment_data: Optional alignment data from alignment_engine.
            If not provided, alignment is computed from trade-level biases.

    Returns:
        ValidationReport dict with sections:
            overall: Overall performance stats
            alignment_based: Alignment-based performance breakdown
            timeframe_breakdown: Per-timeframe performance
            strategy_performance: Per-strategy performance
            indicator_correlation: Per-indicator state analysis
            recent_trend: Recent performance trends
            patterns: List of identified patterns
            metadata: Report generation metadata
    """
    try:
        # Ensure we have valid trade data
        if not trades:
            trades = []

        # Use alignment_data if provided, otherwise compute from trade biases
        if alignment_data is None:
            alignment_data = {}

        # ------------------------------------------------------------------
        # A. Overall Performance
        # ------------------------------------------------------------------
        overall_wr = compute_win_rate(trades)
        overall_rs = compute_r_stats(trades)
        overall = {
            "total_trades": overall_wr["total_trades"],
            "win_count": overall_wr["win_count"],
            "loss_count": overall_wr["loss_count"],
            "expired_count": overall_wr["expired_count"],
            "win_rate": overall_wr["win_rate"],
            "net_r": overall_rs["net_r"],
            "avg_r": overall_rs["avg_r"],
            "avg_win_r": overall_rs["avg_win_r"],
            "avg_loss_r": overall_rs["avg_loss_r"],
            "profit_factor": overall_rs["profit_factor"],
            "max_win_r": overall_rs["max_win_r"],
            "max_loss_r": overall_rs["max_loss_r"],
            "std_r": overall_rs["std_r"],
            "insufficient_data": overall_wr["insufficient_data"],
        }

        # ------------------------------------------------------------------
        # B. Alignment-Based Performance
        # ------------------------------------------------------------------
        alignment_based = split_by_alignment(trades, alignment_data)

        # ------------------------------------------------------------------
        # C. Timeframe Breakdown
        # ------------------------------------------------------------------
        timeframe_breakdown = split_by_timeframe(trades)

        # ------------------------------------------------------------------
        # D. Strategy Performance
        # ------------------------------------------------------------------
        strategy_performance = compute_strategy_performance(trades)

        # ------------------------------------------------------------------
        # E. Indicator Correlation
        # ------------------------------------------------------------------
        indicator_correlation = compute_indicator_correlation(trades)

        # ------------------------------------------------------------------
        # F. Recent Trend
        # ------------------------------------------------------------------
        recent_trend = compute_recent_trend(trades)

        # ------------------------------------------------------------------
        # Assemble Report
        # ------------------------------------------------------------------
        report = {
            "overall": overall,
            "alignment_based": alignment_based,
            "timeframe_breakdown": timeframe_breakdown,
            "strategy_performance": strategy_performance,
            "indicator_correlation": indicator_correlation,
            "recent_trend": recent_trend,
            "patterns": [],
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "module": "kqal.trade_validator",
                "version": "1.0.0",
                "trades_analyzed": len(trades),
                "alignment_data_provided": bool(alignment_data),
            },
        }

        # ------------------------------------------------------------------
        # Identify Patterns
        # ------------------------------------------------------------------
        report["patterns"] = identify_patterns(report)

        return report

    except Exception as e:
        return {
            "overall": {
                "total_trades": 0, "win_count": 0, "loss_count": 0, "expired_count": 0,
                "win_rate": 0.0, "net_r": 0.0, "avg_r": 0.0, "avg_win_r": 0.0,
                "avg_loss_r": 0.0, "profit_factor": 0.0, "insufficient_data": True,
            },
            "alignment_based": {},
            "timeframe_breakdown": {},
            "strategy_performance": {},
            "indicator_correlation": {},
            "recent_trend": {},
            "patterns": [{
                "pattern": "LOW_CONFIDENCE",
                "evidence": f"Validation failed with error: {e}",
                "confidence": "low",
                "recommendation": "Check trade data integrity and re-run.",
            }],
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "module": "kqal.trade_validator",
                "version": "1.0.0",
                "trades_analyzed": 0,
                "error": str(e),
            },
        }


# ===================================================================
# Utility: Export Report to JSON
# ===================================================================

def export_report(
    report: Dict[str, Any],
    filepath: Optional[str] = None,
) -> str:
    """
    Export a validation report to a JSON file.

    Args:
        report: The ValidationReport dict from validate_trades()
        filepath: Optional output path. Defaults to kqal/output/validation_report_<timestamp>.json

    Returns:
        The filepath where the report was saved.
    """
    try:
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(KQAL_OUTPUT_DIR, f"validation_report_{timestamp}.json")

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        return filepath
    except Exception as e:
        fallback = os.path.join(KQAL_OUTPUT_DIR, "validation_report_fallback.json")
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump({"error": str(e), "report": report}, f, indent=2, default=str)
        return fallback


# ===================================================================
# Utility: Generate Summary Text
# ===================================================================

def generate_summary_text(report: Dict[str, Any]) -> str:
    """
    Generate a human-readable summary of the validation report.

    Args:
        report: The ValidationReport dict from validate_trades()

    Returns:
        A formatted string with key findings.
    """
    try:
        lines = []
        lines.append("=" * 60)
        lines.append("KQAL — Trade Validation Report")
        lines.append("=" * 60)
        lines.append("")

        # Overall
        overall = report.get("overall", {})
        lines.append("📊 OVERALL PERFORMANCE")
        lines.append("-" * 40)
        lines.append(f"  Total Trades:  {overall.get('total_trades', 0)}")
        lines.append(f"  Win Rate:      {overall.get('win_rate', 0):.1%} "
                      f"({overall.get('win_count', 0)}W / {overall.get('loss_count', 0)}L / "
                      f"{overall.get('expired_count', 0)}E)")
        lines.append(f"  Net R:         {overall.get('net_r', 0):.2f}")
        lines.append(f"  Avg R:         {overall.get('avg_r', 0):.3f}")
        lines.append(f"  Avg Win R:     {overall.get('avg_win_r', 0):.3f}")
        lines.append(f"  Avg Loss R:    {overall.get('avg_loss_r', 0):.3f}")
        lines.append(f"  Profit Factor: {overall.get('profit_factor', 0):.2f}")
        lines.append("")

        # Alignment
        alignment = report.get("alignment_based", {})
        aligned = alignment.get("aligned", {})
        misaligned = alignment.get("misaligned", {})
        neutral = alignment.get("neutral", {})
        lines.append("🎯 ALIGNMENT ANALYSIS")
        lines.append("-" * 40)
        lines.append(f"  Aligned:    {aligned.get('count', 0)} trades, "
                      f"{aligned.get('win_rate', 0):.1%} WR, "
                      f"{aligned.get('net_r', 0):.2f} net R")
        lines.append(f"  Misaligned: {misaligned.get('count', 0)} trades, "
                      f"{misaligned.get('win_rate', 0):.1%} WR, "
                      f"{misaligned.get('net_r', 0):.2f} net R")
        lines.append(f"  Neutral:    {neutral.get('count', 0)} trades, "
                      f"{neutral.get('win_rate', 0):.1%} WR, "
                      f"{neutral.get('net_r', 0):.2f} net R")
        lines.append(f"  Alignment Delta: {alignment.get('alignment_delta', 0):.1%}")
        lines.append("")

        # Timeframe
        tf = report.get("timeframe_breakdown", {})
        per_tf = tf.get("per_timeframe", {})
        lines.append("⏱️ TIMEFRAME BREAKDOWN")
        lines.append("-" * 40)
        for tf_name in TIMEFRAMES:
            stats = per_tf.get(tf_name, {})
            if stats.get("count", 0) > 0:
                lines.append(f"  {tf_name}: {stats.get('count', 0)} trades, "
                              f"{stats.get('win_rate', 0):.1%} WR, "
                              f"{stats.get('net_r', 0):.2f} net R")
        lines.append(f"  Best:  {tf.get('best_timeframe', 'N/A')}")
        lines.append(f"  Worst: {tf.get('worst_timeframe', 'N/A')}")
        lines.append("")

        # Patterns
        patterns = report.get("patterns", [])
        if patterns:
            lines.append("🔍 DETECTED PATTERNS")
            lines.append("-" * 40)
            for p in patterns:
                conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
                lines.append(f"  {conf_emoji.get(p.get('confidence', 'low'), '⚪')} "
                              f"{p.get('pattern', 'UNKNOWN')}")
                lines.append(f"     Evidence: {p.get('evidence', 'N/A')}")
                lines.append(f"     → {p.get('recommendation', 'N/A')}")
                lines.append("")

        # Recent
        recent = report.get("recent_trend", {})
        last_10 = recent.get("last_10", {})
        rolling = recent.get("rolling_7d", {})
        lines.append("📈 RECENT TREND")
        lines.append("-" * 40)
        lines.append(f"  Last 10:     {last_10.get('win_rate', 0):.1%} WR, "
                      f"{last_10.get('net_r', 0):.2f} net R")
        lines.append(f"  7-Day Roll:  {rolling.get('win_rate', 0):.1%} WR, "
                      f"{rolling.get('net_r', 0):.2f} net R")
        lines.append(f"  Direction:   {recent.get('trend_direction', 'N/A').upper()} "
                      f"(confidence: {recent.get('trend_confidence', 'N/A')})")
        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)
    except Exception as e:
        return f"Error generating summary: {e}"


# ===================================================================
# Sample Data Generator (for testing)
# ===================================================================

def _generate_sample_trades(count: int = 50) -> List[Dict[str, Any]]:
    """
    Generate sample trade data for testing and demonstration.

    Args:
        count: Number of sample trades to generate

    Returns:
        List of trade dicts
    """
    import random

    timeframes = ["15M", "1H", "4H"]
    strategies = ["strategy_1", "strategy_2", "strategy_3", "strategy_4", "strategy_5"]
    biases = ["bullish", "bearish", "neutral"]
    outcomes = ["win", "loss", "expired"]

    # Indicator states
    bbwp_states = ["extreme_squeeze", "moderate_squeeze", "high_expansion", "extreme_exhaustion", "normal"]
    pmarp_states = ["overextended_top", "capitulation_discount", "normal"]
    kinematic_states = ["A", "B", "C", "D", "F"]
    energy_states = ["rising", "falling", "neutral"]

    trades = []
    base_time = datetime.now(timezone.utc) - timedelta(days=30)

    for i in range(count):
        # Create realistic alignment patterns
        # Aligned trades win more often
        kab_bias = random.choice(biases)
        kr_bias = random.choice(biases)

        # Determine if aligned
        is_aligned = (kab_bias == kr_bias and kab_bias != "neutral")
        is_misaligned = (kab_bias != kr_bias and kab_bias != "neutral" and kr_bias != "neutral")

        # Weight outcomes: aligned more likely to win
        if is_aligned:
            outcome_weights = [0.60, 0.30, 0.10]  # win, loss, expired
        elif is_misaligned:
            outcome_weights = [0.35, 0.55, 0.10]
        else:
            outcome_weights = [0.45, 0.40, 0.15]

        outcome = random.choices(outcomes, weights=outcome_weights, k=1)[0]

        # Generate realistic R values
        if outcome == "win":
            realized_pnl = round(random.uniform(0.5, 3.0), 2)
        elif outcome == "loss":
            realized_pnl = round(random.uniform(-2.0, -0.3), 2)
        else:
            realized_pnl = round(random.uniform(-0.2, 0.2), 2)

        # Time distribution: more recent trades
        days_ago = random.expovariate(1.0 / 10)
        days_ago = min(days_ago, 30)
        trade_time = base_time + timedelta(days=30 - days_ago)

        trade = {
            "id": f"trade_{i+1:04d}",
            "symbol": random.choice(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
            "direction": random.choice(["LONG", "SHORT"]),
            "entry_price": round(random.uniform(20000, 70000), 2),
            "exit_price": round(random.uniform(20000, 70000), 2),
            "quantity": round(random.uniform(0.01, 1.0), 4),
            "realized_pnl": realized_pnl,
            "entry_time": trade_time.isoformat(),
            "exit_time": (trade_time + timedelta(hours=random.randint(1, 48))).isoformat(),
            "timeframe": random.choice(timeframes),
            "strategy": random.choice(strategies),
            "kabroda_bias": kab_bias,
            "krown_bias": kr_bias,
            "outcome": outcome,
            "indicators": {
                "bbwp": random.choice(bbwp_states),
                "pmarp": random.choice(pmarp_states),
                "kinematic_grade": random.choice(kinematic_states),
                "energy_grade": random.choice(energy_states),
            },
        }
        trades.append(trade)

    return trades


# ===================================================================
# Main / CLI
# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  KQAL — Trade Validator")
    print("  Kabroda Quality Assurance Layer")
    print("=" * 60)
    print()

    # Generate sample data
    print("[SAMPLE] Generating 50 sample trades with realistic patterns...")
    sample_trades = _generate_sample_trades(50)
    print(f"[SAMPLE] Generated {len(sample_trades)} trades")
    print()

    # Run validation
    print("[VALIDATE] Running full validation...")
    report = validate_trades(sample_trades)
    print()

    # Print summary
    summary = generate_summary_text(report)
    print(summary)

    # Export report
    export_path = export_report(report)
    print(f"\n[EXPORT] Report saved to: {export_path}")

    # Print pattern details
    print()
    print("=" * 60)
    print("  PATTERN DETAILS")
    print("=" * 60)
    for p in report.get("patterns", []):
        print(f"\n  [{p.get('pattern', '?')}] Confidence: {p.get('confidence', '?')}")
        print(f"  Evidence: {p.get('evidence', 'N/A')}")
        print(f"  → {p.get('recommendation', 'N/A')}")

    print()
    print("=" * 60)
    print("  Validation complete.")
    print("=" * 60)
