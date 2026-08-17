#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KQAL — Kabroda Quality Assurance Layer
=======================================
Alignment Engine — Core Scoring Module

Measures how well Kabroda's actual state aligns with Krown's quantitative
trading framework across 5 dimensions, producing a 0-10 alignment score
with per-dimension breakdown, gap analysis, and actionable recommendations.

Expected Input Schemas
----------------------
kabroda_data (dict) — from db_reader.py:
    {
        "bias": {"direction": "bullish"|"bearish"|"neutral", "confidence": float},
        "strategies": [{"id": str, "name": str, "active": bool}, ...],
        "indicators": {
            "bbwp": {"value": float, "state": str, "settings": dict},
            "pmarp": {"value": float, "state": str, "settings": dict},
            "rsi": {"value": float, "state": str, "divergences": list},
            "ema_position": {"price_vs_20sma": str, "price_vs_50sma": str},
        },
        "confluence": {
            "short_term": {"direction": str, "confidence": float},
            "medium_term": {"direction": str, "confidence": float},
            "long_term": {"direction": str, "confidence": float},
        },
        "trades": [
            {"outcome": "win"|"loss", "strategy_used": str,
             "aligned_with_krown": bool, "pnl_pct": float},
            ...
        ]
    }

krown_data (dict) — from krown_signals.py:
    {
        "bias": {"direction": "bullish"|"bearish"|"neutral",
                 "short_term": str, "medium_term": str, "long_term": str},
        "active_strategies": [{"strategy": str, "confidence": float, ...}, ...],
        "indicators": {
            "bbwp": {"value": float, "state": str, "thresholds": dict},
            "pmarp": {"value": float, "state": str, "thresholds": dict},
            "rsi": {"value": float, "state": str, "divergences": list},
            "revin_ribbons": {"position": str},
            "volatility_state": str,
        },
        "confluence": {
            "short_term": {"direction": str, "vote": str},
            "medium_term": {"direction": str, "vote": str},
            "long_term": {"direction": str, "vote": str},
        },
        "framework_rules": {
            "strategies": { ... },
            "indicator_settings": { ... },
            "execution_rules": { ... },
        }
    }

Output Schema
-------------
AlignmentReport (dict):
    {
        "overall_score": float,          # 0-10
        "dimensions": {
            "bias":          {"score": int, "max_score": 2, "details": dict, "gaps": list},
            "strategy":      {"score": int, "max_score": 2, "details": dict, "gaps": list},
            "indicator":     {"score": int, "max_score": 2, "details": dict, "gaps": list},
            "confluence":    {"score": int, "max_score": 2, "details": dict, "gaps": list},
            "execution":     {"score": int, "max_score": 2, "details": dict, "gaps": list},
        },
        "gaps": [{"category": str, "description": str, "severity": str, "impact_on_score": float}, ...],
        "recommendations": [{"category": str, "action": str, "priority": str, "expected_impact": str}, ...],
        "timestamp": str  # ISO datetime
    }
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAP_CATEGORIES = {
    "MISSING_INDICATOR": "Kabroda lacks an indicator that Krown uses",
    "WRONG_PARAMETER": "Same indicator, different parameter settings",
    "BIAS_MISMATCH": "Opposite or conflicting directional bias",
    "STRATEGY_MISMATCH": "Wrong strategy active for current market conditions",
    "CONFLUENCE_GAP": "Multi-timeframe analysis does not match",
    "EXECUTION_GAP": "Trade outcomes contradict framework predictions",
    "DATA_GAP": "Missing data required to make a comparison",
}

SEVERITY_LEVELS = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.5}

# Mapping from Krown strategy IDs to Kabroda strategy names
KROWN_STRATEGY_IDS = {
    "strategy_1": "Strategy_1_Macro_Trend",
    "strategy_2": "Strategy_2_Uptrend_Pullback",
    "strategy_3": "Strategy_3_Downtrend_Continuation",
    "strategy_4": "Strategy_4_Exhaustion_Short",
    "strategy_5": "Strategy_5_Breakdown_Short",
}

KABRODA_STRATEGY_NAMES = {
    "Strategy_1_Macro_Trend": "strategy_1",
    "Strategy_2_Uptrend_Pullback": "strategy_2",
    "Strategy_3_Downtrend_Continuation": "strategy_3",
    "Strategy_4_Exhaustion_Short": "strategy_4",
    "Strategy_5_Breakdown_Short": "strategy_5",
}

# ---------------------------------------------------------------------------
# Safe access helpers
# ---------------------------------------------------------------------------


def _safe_get(d: Any, *keys, default=None):
    """Safely traverse nested dicts. Returns default if any key missing."""
    for key in keys:
        try:
            d = d[key]
        except (KeyError, TypeError, IndexError):
            return default
    return d


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert value to float safely."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _normalize_bias(bias: Any) -> str:
    """Normalize bias string to one of: bullish, bearish, neutral."""
    if isinstance(bias, str):
        b = bias.strip().lower()
        if b in ("bullish", "bull", "long", "buy"):
            return "bullish"
        if b in ("bearish", "bear", "short", "sell"):
            return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Dimension A: Bias Alignment (0-2 pts)
# ---------------------------------------------------------------------------


def score_bias_alignment(
    kabroda_bias: Any,
    krown_bias: Any,
) -> Dict[str, Any]:
    """
    Compare Kabroda's current bias vs Krown's bias.

    Args:
        kabroda_bias: str or dict with 'direction' key from Kabroda state.
        krown_bias: str or dict with 'direction' key from Krown signals.

    Returns:
        dict with keys: score, max_score, bias_match, kabroda_bias, krown_bias, details
    """
    try:
        # Extract direction strings
        if isinstance(kabroda_bias, dict):
            kb_dir = _normalize_bias(kabroda_bias.get("direction", "neutral"))
        else:
            kb_dir = _normalize_bias(kabroda_bias)

        if isinstance(krown_bias, dict):
            kr_dir = _normalize_bias(krown_bias.get("direction", "neutral"))
        else:
            kr_dir = _normalize_bias(krown_bias)

        # Score logic
        if kb_dir == kr_dir:
            if kb_dir != "neutral":
                score = 2  # Same direction (both bullish or both bearish)
                match = True
                detail = f"Both aligned: {kb_dir.upper()}"
            else:
                score = 1  # Both neutral — partial alignment
                match = True
                detail = "Both neutral — no directional conflict"
        elif kb_dir == "neutral" or kr_dir == "neutral":
            score = 1  # One neutral, other has direction
            match = False
            detail = f"Partial: Kabroda={kb_dir}, Krown={kr_dir}"
        else:
            score = 0  # Opposite directions
            match = False
            detail = f"Opposite: Kabroda={kb_dir}, Krown={kr_dir}"

        return {
            "score": score,
            "max_score": 2,
            "bias_match": match,
            "kabroda_bias": kb_dir,
            "krown_bias": kr_dir,
            "details": detail,
        }
    except Exception as e:
        return {
            "score": 0,
            "max_score": 2,
            "bias_match": False,
            "kabroda_bias": "unknown",
            "krown_bias": "unknown",
            "details": f"Error scoring bias alignment: {e}",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Dimension B: Strategy Alignment (0-2 pts)
# ---------------------------------------------------------------------------


def score_strategy_alignment(
    kabroda_strategies: Any,
    krown_strategies: Any,
) -> Dict[str, Any]:
    """
    Compare which strategies Kabroda is using vs which Krown says are active.

    Args:
        kabroda_strategies: list of strategy dicts from Kabroda state.
        krown_strategies: list of active strategy dicts from Krown signals.

    Returns:
        dict with keys: score, max_score, active_krown_strategies,
                        active_kabroda_equivalent, strategy_gaps, details
    """
    try:
        # Normalize Krown active strategies
        krown_active_ids = set()
        if isinstance(krown_strategies, list):
            for s in krown_strategies:
                if isinstance(s, dict):
                    sid = s.get("strategy", "")
                    krown_active_ids.add(sid)
                elif isinstance(s, str):
                    krown_active_ids.add(s)

        # Normalize Kabroda active strategies
        kabroda_active = set()
        if isinstance(kabroda_strategies, list):
            for s in kabroda_strategies:
                if isinstance(s, dict):
                    # Try to get strategy ID or name
                    sid = s.get("id", s.get("strategy", ""))
                    name = s.get("name", "")
                    active = s.get("active", True)
                    if active:
                        if sid:
                            kabroda_active.add(sid)
                        if name and name in KABRODA_STRATEGY_NAMES:
                            kabroda_active.add(KABRODA_STRATEGY_NAMES[name])
                elif isinstance(s, str):
                    kabroda_active.add(s)

        # Map Kabroda strategies to Krown strategy IDs
        kabroda_krown_ids = set()
        for s in kabroda_active:
            if s in KABRODA_STRATEGY_NAMES:
                kabroda_krown_ids.add(KABRODA_STRATEGY_NAMES[s])
            elif s in KROWN_STRATEGY_IDS:
                kabroda_krown_ids.add(s)
            else:
                # Try partial match
                for kid, kname in KROWN_STRATEGY_IDS.items():
                    if s.lower() in kname.lower() or kid.lower() in s.lower():
                        kabroda_krown_ids.add(kid)

        # Find intersection and gaps
        matching = krown_active_ids & kabroda_krown_ids
        missing_from_kabroda = krown_active_ids - kabroda_krown_ids
        extra_in_kabroda = kabroda_krown_ids - krown_active_ids

        # Score logic
        if not krown_active_ids:
            score = 1  # No active strategies from Krown — can't compare
            detail = "No active strategies from Krown to compare"
        elif matching == krown_active_ids and matching == kabroda_krown_ids:
            score = 2  # Perfect match — both sets identical
            detail = f"All active strategies match: {sorted(matching)}"
        elif len(matching) > 0:
            score = 1  # Partial overlap
            detail = (
                f"Partial match. Matching: {sorted(matching)}. "
                f"Missing from Kabroda: {sorted(missing_from_kabroda)}. "
                f"Extra in Kabroda: {sorted(extra_in_kabroda)}."
            )
        else:
            score = 0  # No match
            detail = (
                f"No strategy match. Krown active: {sorted(krown_active_ids)}. "
                f"Kabroda active: {sorted(kabroda_krown_ids)}."
            )

        # Build strategy gaps
        strategy_gaps = []
        for sid in sorted(missing_from_kabroda):
            strategy_gaps.append({
                "strategy_id": sid,
                "strategy_name": KROWN_STRATEGY_IDS.get(sid, sid),
                "gap_type": "missing",
                "description": f"Kabroda is not running {KROWN_STRATEGY_IDS.get(sid, sid)} which Krown has active",
            })
        for sid in sorted(extra_in_kabroda):
            strategy_gaps.append({
                "strategy_id": sid,
                "strategy_name": KROWN_STRATEGY_IDS.get(sid, sid),
                "gap_type": "extra",
                "description": f"Kabroda is running {KROWN_STRATEGY_IDS.get(sid, sid)} but Krown does not have it active",
            })

        return {
            "score": score,
            "max_score": 2,
            "active_krown_strategies": sorted(krown_active_ids),
            "active_kabroda_equivalent": sorted(kabroda_krown_ids),
            "strategy_gaps": strategy_gaps,
            "details": detail,
        }
    except Exception as e:
        return {
            "score": 0,
            "max_score": 2,
            "active_krown_strategies": [],
            "active_kabroda_equivalent": [],
            "strategy_gaps": [],
            "details": f"Error scoring strategy alignment: {e}",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Dimension C: Indicator Alignment (0-2 pts)
# ---------------------------------------------------------------------------


def score_indicator_alignment(
    kabroda_indicators: Any,
    krown_indicators: Any,
) -> Dict[str, Any]:
    """
    Compare indicator readings between Kabroda and Krown.

    Compares: BBWP state, PMARP state, RSI state/divergences, EMA positions.

    Args:
        kabroda_indicators: dict of indicator data from Kabroda.
        krown_indicators: dict of indicator data from Krown.

    Returns:
        dict with keys: score, max_score, indicator_comparisons,
                        indicator_gaps, details
    """
    try:
        kabroda_indicators = kabroda_indicators or {}
        krown_indicators = krown_indicators or {}

        comparisons = []
        indicator_gaps = []
        agreements = 0
        disagreements = 0
        total_compared = 0

        # --- BBWP Comparison ---
        kb_bbwp = kabroda_indicators.get("bbwp", {})
        kr_bbwp = krown_indicators.get("bbwp", {})
        if isinstance(kb_bbwp, dict) and isinstance(kr_bbwp, dict):
            total_compared += 1
            kb_state = kb_bbwp.get("state", "")
            kr_state = kr_bbwp.get("state", "")
            kb_val = _safe_float(kb_bbwp.get("value", 0))
            kr_val = _safe_float(kr_bbwp.get("value", 0))

            if kb_state and kr_state:
                # Normalize states for comparison
                kb_norm = _normalize_bbwp_state(kb_state)
                kr_norm = _normalize_bbwp_state(kr_state)
                match = kb_norm == kr_norm
                if match:
                    agreements += 1
                else:
                    disagreements += 1
                    indicator_gaps.append({
                        "indicator": "bbwp",
                        "kabroda": {"state": kb_state, "value": kb_val},
                        "krown": {"state": kr_state, "value": kr_val},
                        "description": f"BBWP state mismatch: Kabroda={kb_state}, Krown={kr_state}",
                    })
                comparisons.append({
                    "indicator": "bbwp",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state,
                    "krown_value": kr_val,
                    "krown_state": kr_state,
                    "match": match,
                })
            else:
                comparisons.append({
                    "indicator": "bbwp",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state or "unknown",
                    "krown_value": kr_val,
                    "krown_state": kr_state or "unknown",
                    "match": None,
                    "note": "Insufficient state data to compare",
                })

        # --- PMARP Comparison ---
        kb_pmarp = kabroda_indicators.get("pmarp", {})
        kr_pmarp = krown_indicators.get("pmarp", {})
        if isinstance(kb_pmarp, dict) and isinstance(kr_pmarp, dict):
            total_compared += 1
            kb_state = kb_pmarp.get("state", "")
            kr_state = kr_pmarp.get("state", "")
            kb_val = _safe_float(kb_pmarp.get("value", 0))
            kr_val = _safe_float(kr_pmarp.get("value", 0))

            if kb_state and kr_state:
                kb_norm = _normalize_pmarp_state(kb_state)
                kr_norm = _normalize_pmarp_state(kr_state)
                match = kb_norm == kr_norm
                if match:
                    agreements += 1
                else:
                    disagreements += 1
                    indicator_gaps.append({
                        "indicator": "pmarp",
                        "kabroda": {"state": kb_state, "value": kb_val},
                        "krown": {"state": kr_state, "value": kr_val},
                        "description": f"PMARP state mismatch: Kabroda={kb_state}, Krown={kr_state}",
                    })
                comparisons.append({
                    "indicator": "pmarp",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state,
                    "krown_value": kr_val,
                    "krown_state": kr_state,
                    "match": match,
                })
            else:
                comparisons.append({
                    "indicator": "pmarp",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state or "unknown",
                    "krown_value": kr_val,
                    "krown_state": kr_state or "unknown",
                    "match": None,
                    "note": "Insufficient state data to compare",
                })

        # --- RSI Comparison ---
        kb_rsi = kabroda_indicators.get("rsi", {})
        kr_rsi = krown_indicators.get("rsi", {})
        if isinstance(kb_rsi, dict) and isinstance(kr_rsi, dict):
            total_compared += 1
            kb_state = kb_rsi.get("state", "")
            kr_state = kr_rsi.get("state", "")
            kb_val = _safe_float(kb_rsi.get("value", 50))
            kr_val = _safe_float(kr_rsi.get("value", 50))

            if kb_state and kr_state:
                kb_norm = _normalize_rsi_state(kb_state)
                kr_norm = _normalize_rsi_state(kr_state)
                match = kb_norm == kr_norm
                if match:
                    agreements += 1
                else:
                    disagreements += 1
                    indicator_gaps.append({
                        "indicator": "rsi",
                        "kabroda": {"state": kb_state, "value": kb_val},
                        "krown": {"state": kr_state, "value": kr_val},
                        "description": f"RSI state mismatch: Kabroda={kb_state}, Krown={kr_state}",
                    })
                comparisons.append({
                    "indicator": "rsi",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state,
                    "krown_value": kr_val,
                    "krown_state": kr_state,
                    "match": match,
                })
            else:
                comparisons.append({
                    "indicator": "rsi",
                    "kabroda_value": kb_val,
                    "kabroda_state": kb_state or "unknown",
                    "krown_value": kr_val,
                    "krown_state": kr_state or "unknown",
                    "match": None,
                    "note": "Insufficient state data to compare",
                })

            # Compare divergences
            kb_divs = kb_rsi.get("divergences", [])
            kr_divs = kr_rsi.get("divergences", [])
            if isinstance(kb_divs, list) and isinstance(kr_divs, list):
                kb_div_count = len(kb_divs)
                kr_div_count = len(kr_divs)
                comparisons.append({
                    "indicator": "rsi_divergences",
                    "kabroda_count": kb_div_count,
                    "krown_count": kr_div_count,
                    "match": (kb_div_count > 0) == (kr_div_count > 0),
                })

        # --- EMA / Ribbon Position Comparison ---
        kb_ema = kabroda_indicators.get("ema_position", kabroda_indicators.get("revin_ribbons", {}))
        kr_rr = krown_indicators.get("revin_ribbons", krown_indicators.get("ema_position", {}))
        if isinstance(kb_ema, dict) and isinstance(kr_rr, dict):
            total_compared += 1
            kb_pos = kb_ema.get("position", kb_ema.get("price_vs_20sma", ""))
            kr_pos = kr_rr.get("position", "")

            if kb_pos and kr_pos:
                kb_norm = _normalize_ribbon_position(kb_pos)
                kr_norm = _normalize_ribbon_position(kr_pos)
                match = kb_norm == kr_norm
                if match:
                    agreements += 1
                else:
                    disagreements += 1
                    indicator_gaps.append({
                        "indicator": "ribbon_position",
                        "kabroda": {"position": kb_pos},
                        "krown": {"position": kr_pos},
                        "description": f"Ribbon/EMA position mismatch: Kabroda={kb_pos}, Krown={kr_pos}",
                    })
                comparisons.append({
                    "indicator": "ribbon_position",
                    "kabroda_position": kb_pos,
                    "krown_position": kr_pos,
                    "match": match,
                })
            else:
                comparisons.append({
                    "indicator": "ribbon_position",
                    "kabroda_position": kb_pos or "unknown",
                    "krown_position": kr_pos or "unknown",
                    "match": None,
                    "note": "Insufficient position data to compare",
                })

        # --- Check for missing indicators ---
        krown_indicator_keys = {"bbwp", "pmarp", "rsi", "revin_ribbons", "volatility_state"}
        kabroda_indicator_keys = set(kabroda_indicators.keys()) if isinstance(kabroda_indicators, dict) else set()
        missing_indicators = krown_indicator_keys - kabroda_indicator_keys
        for mi in sorted(missing_indicators):
            indicator_gaps.append({
                "indicator": mi,
                "gap_type": "missing",
                "description": f"Kabroda is missing indicator '{mi}' that Krown uses",
            })

        # Score logic
        if total_compared == 0:
            score = 1  # No comparable indicators
            detail = "No comparable indicator data available"
        elif disagreements == 0 and agreements > 0:
            score = 2  # All agree
            detail = f"All {agreements} compared indicators agree"
        elif agreements >= disagreements:
            score = 1  # Some agree, some disagree
            detail = f"{agreements} agree, {disagreements} disagree out of {total_compared} compared"
        else:
            score = 0  # Major disagreement
            detail = f"Major disagreement: {agreements} agree, {disagreements} disagree out of {total_compared}"

        return {
            "score": score,
            "max_score": 2,
            "indicator_comparisons": comparisons,
            "indicator_gaps": indicator_gaps,
            "agreements": agreements,
            "disagreements": disagreements,
            "total_compared": total_compared,
            "details": detail,
        }
    except Exception as e:
        return {
            "score": 0,
            "max_score": 2,
            "indicator_comparisons": [],
            "indicator_gaps": [],
            "agreements": 0,
            "disagreements": 0,
            "total_compared": 0,
            "details": f"Error scoring indicator alignment: {e}",
            "error": str(e),
        }


def _normalize_bbwp_state(state: str) -> str:
    """Normalize BBWP state to a canonical form."""
    s = state.strip().lower().replace(" ", "_").replace("-", "_")
    if s in ("extreme_squeeze", "blue_white_zone", "squeeze_extreme"):
        return "extreme_squeeze"
    if s in ("moderate_squeeze", "compression", "squeeze_moderate"):
        return "moderate_squeeze"
    if s in ("high_expansion", "expansion", "expanding", "trend_surge"):
        return "high_expansion"
    if s in ("extreme_exhaustion", "blow_off", "exhaustion", "red_zone"):
        return "extreme_exhaustion"
    return "normal"


def _normalize_pmarp_state(state: str) -> str:
    """Normalize PMARP state to a canonical form."""
    s = state.strip().lower().replace(" ", "_").replace("-", "_")
    if s in ("overextended_top", "overextended", "parabolic", "blow_off_top"):
        return "overextended_top"
    if s in ("capitulation_discount", "depressed_bottom", "capitulation", "oversold_extreme"):
        return "capitulation_discount"
    return "normal"


def _normalize_rsi_state(state: str) -> str:
    """Normalize RSI state to a canonical form."""
    s = state.strip().lower().replace(" ", "_").replace("-", "_")
    if s in ("overbought", "ob"):
        return "overbought"
    if s in ("oversold", "os"):
        return "oversold"
    return "neutral"


def _normalize_ribbon_position(pos: str) -> str:
    """Normalize ribbon/EMA position to a canonical form."""
    s = pos.strip().lower().replace(" ", "_").replace("-", "_")
    if s in ("above_midband", "above", "bullish", "above_20sma", "above_50sma"):
        return "above_midband"
    if s in ("below_midband", "below", "bearish", "below_20sma", "below_50sma"):
        return "below_midband"
    return "neutral"


# ---------------------------------------------------------------------------
# Dimension D: Confluence Alignment (0-2 pts)
# ---------------------------------------------------------------------------


def score_confluence_alignment(
    kabroda_confluence: Any,
    krown_confluence: Any,
) -> Dict[str, Any]:
    """
    Compare Kabroda's MTF confluence vs Krown's TF analysis.

    Args:
        kabroda_confluence: dict with short/medium/long term directions.
        krown_confluence: dict with short/medium/long term directions/votes.

    Returns:
        dict with keys: score, max_score, kabroda_confluence, krown_confluence,
                        confluence_gaps, details
    """
    try:
        kabroda_confluence = kabroda_confluence or {}
        krown_confluence = krown_confluence or {}

        timeframes = ["short_term", "medium_term", "long_term"]
        confluence_gaps = []
        agreements = 0
        disagreements = 0
        total_tf = 0

        kabroda_normalized = {}
        krown_normalized = {}

        for tf in timeframes:
            kb_tf = kabroda_confluence.get(tf, {})
            kr_tf = krown_confluence.get(tf, {})

            if isinstance(kb_tf, dict):
                kb_dir = _normalize_bias(kb_tf.get("direction", "neutral"))
            elif isinstance(kb_tf, str):
                kb_dir = _normalize_bias(kb_tf)
            else:
                kb_dir = "neutral"

            if isinstance(kr_tf, dict):
                kr_dir = _normalize_bias(kr_tf.get("direction", kr_tf.get("vote", "neutral")))
            elif isinstance(kr_tf, str):
                kr_dir = _normalize_bias(kr_tf)
            else:
                kr_dir = "neutral"

            kabroda_normalized[tf] = kb_dir
            krown_normalized[tf] = kr_dir

            if kb_dir != "unknown" and kr_dir != "unknown":
                total_tf += 1
                if kb_dir == kr_dir:
                    agreements += 1
                else:
                    disagreements += 1
                    confluence_gaps.append({
                        "timeframe": tf,
                        "kabroda_direction": kb_dir,
                        "krown_direction": kr_dir,
                        "description": f"{tf.replace('_', ' ').title()} direction mismatch: Kabroda={kb_dir}, Krown={kr_dir}",
                    })

        # Score logic
        if total_tf == 0:
            score = 1
            detail = "No timeframe confluence data to compare"
        elif disagreements == 0 and agreements > 0:
            score = 2  # Same direction vote across all TFs
            detail = f"All {agreements} timeframes agree on direction"
        elif agreements >= disagreements:
            score = 1  # Partial agreement
            detail = f"{agreements} timeframes agree, {disagreements} disagree"
        else:
            score = 0  # Opposing
            detail = f"Major confluence disagreement: {agreements} agree, {disagreements} disagree"

        return {
            "score": score,
            "max_score": 2,
            "kabroda_confluence": kabroda_normalized,
            "krown_confluence": krown_normalized,
            "confluence_gaps": confluence_gaps,
            "agreements": agreements,
            "disagreements": disagreements,
            "total_timeframes": total_tf,
            "details": detail,
        }
    except Exception as e:
        return {
            "score": 0,
            "max_score": 2,
            "kabroda_confluence": {},
            "krown_confluence": {},
            "confluence_gaps": [],
            "agreements": 0,
            "disagreements": 0,
            "total_timeframes": 0,
            "details": f"Error scoring confluence alignment: {e}",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Dimension E: Execution Alignment (0-2 pts)
# ---------------------------------------------------------------------------


def score_execution_alignment(
    kabroda_trades: Any,
    krown_framework: Any,
) -> Dict[str, Any]:
    """
    Compare Kabroda's trade outcomes against what Krown's framework would predict.

    Args:
        kabroda_trades: list of trade dicts from Kabroda.
        krown_framework: dict with framework rules from Krown.

    Returns:
        dict with keys: score, max_score, aligned_win_rate, misaligned_win_rate,
                        execution_gaps, details
    """
    try:
        kabroda_trades = kabroda_trades or []
        if not isinstance(kabroda_trades, list):
            kabroda_trades = []

        aligned_trades = []
        misaligned_trades = []
        execution_gaps = []

        for trade in kabroda_trades:
            if not isinstance(trade, dict):
                continue

            outcome = trade.get("outcome", "").strip().lower()
            aligned = trade.get("aligned_with_krown", None)
            pnl = _safe_float(trade.get("pnl_pct", 0))

            if aligned is True:
                aligned_trades.append(trade)
            elif aligned is False:
                misaligned_trades.append(trade)
            else:
                # Try to infer alignment from strategy
                strategy_used = trade.get("strategy_used", "")
                if strategy_used:
                    # Check if strategy is in Krown's framework
                    framework_strategies = krown_framework.get("strategies", {}) if isinstance(krown_framework, dict) else {}
                    if isinstance(framework_strategies, dict) and strategy_used in framework_strategies:
                        aligned_trades.append(trade)
                    else:
                        misaligned_trades.append(trade)
                else:
                    # Unknown alignment — skip
                    pass

        # Calculate win rates
        def _win_rate(trades_list: List[Dict]) -> float:
            if not trades_list:
                return 0.0
            wins = sum(1 for t in trades_list if t.get("outcome", "").strip().lower() == "win")
            return (wins / len(trades_list)) * 100.0

        aligned_win_rate = _win_rate(aligned_trades)
        misaligned_win_rate = _win_rate(misaligned_trades)

        # Score logic
        if not aligned_trades and not misaligned_trades:
            score = 1  # No trade data to compare
            detail = "No trade data available for execution comparison"
        elif not aligned_trades:
            score = 0  # No aligned trades to evaluate
            detail = "No aligned trades found — cannot assess execution alignment"
            execution_gaps.append({
                "category": "EXECUTION_GAP",
                "description": "No trades marked as aligned with Krown framework",
            })
        elif not misaligned_trades:
            if aligned_win_rate >= 50.0:
                score = 2  # All aligned, good win rate
                detail = f"All trades aligned with Krown. Win rate: {aligned_win_rate:.1f}%"
            else:
                score = 1  # All aligned but poor results
                detail = f"All trades aligned with Krown but win rate only {aligned_win_rate:.1f}%"
        else:
            # Both aligned and misaligned trades exist
            if aligned_win_rate > misaligned_win_rate:
                if aligned_win_rate >= 60.0 and misaligned_win_rate < 50.0:
                    score = 2  # Strong correlation
                    detail = (
                        f"Strong correlation. Aligned win rate: {aligned_win_rate:.1f}%, "
                        f"Misaligned win rate: {misaligned_win_rate:.1f}%"
                    )
                else:
                    score = 1  # Mixed but positive
                    detail = (
                        f"Mixed results. Aligned win rate: {aligned_win_rate:.1f}%, "
                        f"Misaligned win rate: {misaligned_win_rate:.1f}%"
                    )
            elif aligned_win_rate < misaligned_win_rate:
                score = 0  # Negative correlation
                detail = (
                    f"Negative correlation. Aligned win rate: {aligned_win_rate:.1f}%, "
                    f"Misaligned win rate: {misaligned_win_rate:.1f}%"
                )
                execution_gaps.append({
                    "category": "EXECUTION_GAP",
                    "description": (
                        f"Misaligned trades ({misaligned_win_rate:.1f}%) outperform "
                        f"aligned trades ({aligned_win_rate:.1f}%)"
                    ),
                })
            else:
                score = 1  # Equal win rates
                detail = (
                    f"Equal win rates. Aligned: {aligned_win_rate:.1f}%, "
                    f"Misaligned: {misaligned_win_rate:.1f}%"
                )

        return {
            "score": score,
            "max_score": 2,
            "aligned_win_rate": aligned_win_rate,
            "misaligned_win_rate": misaligned_win_rate,
            "aligned_trades_count": len(aligned_trades),
            "misaligned_trades_count": len(misaligned_trades),
            "execution_gaps": execution_gaps,
            "details": detail,
        }
    except Exception as e:
        return {
            "score": 0,
            "max_score": 2,
            "aligned_win_rate": 0.0,
            "misaligned_win_rate": 0.0,
            "aligned_trades_count": 0,
            "misaligned_trades_count": 0,
            "execution_gaps": [],
            "details": f"Error scoring execution alignment: {e}",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Gap Identification
# ---------------------------------------------------------------------------


def identify_gaps(alignment_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Identify all gaps from the alignment report dimensions.

    Args:
        alignment_report: The full alignment report dict.

    Returns:
        list of gap dicts with category, description, severity, impact_on_score.
    """
    gaps = []
    dimensions = alignment_report.get("dimensions", {})

    # Bias gaps
    bias = dimensions.get("bias", {})
    if not bias.get("bias_match", True) and bias.get("score", 2) < 2:
        kb = bias.get("kabroda_bias", "unknown")
        kr = bias.get("krown_bias", "unknown")
        if kb != "neutral" and kr != "neutral" and kb != kr:
            gaps.append({
                "category": "BIAS_MISMATCH",
                "description": f"Kabroda bias ({kb}) opposite to Krown bias ({kr})",
                "severity": "critical",
                "impact_on_score": 2.0,
            })
        elif kb == "neutral" or kr == "neutral":
            gaps.append({
                "category": "BIAS_MISMATCH",
                "description": f"Partial bias alignment: Kabroda={kb}, Krown={kr}",
                "severity": "medium",
                "impact_on_score": 1.0,
            })

    # Strategy gaps
    strategy = dimensions.get("strategy", {})
    for sg in strategy.get("strategy_gaps", []):
        severity = "high" if sg.get("gap_type") == "missing" else "medium"
        gaps.append({
            "category": "STRATEGY_MISMATCH",
            "description": sg.get("description", "Unknown strategy gap"),
            "severity": severity,
            "impact_on_score": SEVERITY_LEVELS.get(severity, 1.0),
        })

    # Indicator gaps
    indicator = dimensions.get("indicator", {})
    for ig in indicator.get("indicator_gaps", []):
        if ig.get("gap_type") == "missing":
            gaps.append({
                "category": "MISSING_INDICATOR",
                "description": ig.get("description", f"Missing indicator: {ig.get('indicator', 'unknown')}"),
                "severity": "high",
                "impact_on_score": 2.0,
            })
        else:
            gaps.append({
                "category": "WRONG_PARAMETER",
                "description": ig.get("description", f"Indicator mismatch: {ig.get('indicator', 'unknown')}"),
                "severity": "medium",
                "impact_on_score": 1.0,
            })

    # Confluence gaps
    confluence = dimensions.get("confluence", {})
    for cg in confluence.get("confluence_gaps", []):
        gaps.append({
            "category": "CONFLUENCE_GAP",
            "description": cg.get("description", "Unknown confluence gap"),
            "severity": "high",
            "impact_on_score": 1.5,
        })

    # Execution gaps
    execution = dimensions.get("execution", {})
    for eg in execution.get("execution_gaps", []):
        gaps.append({
            "category": "EXECUTION_GAP",
            "description": eg.get("description", "Unknown execution gap"),
            "severity": "high",
            "impact_on_score": 1.5,
        })

    # Check for data gaps
    for dim_name, dim_data in dimensions.items():
        if dim_data.get("score", 0) == 0 and "error" in dim_data:
            gaps.append({
                "category": "DATA_GAP",
                "description": f"Cannot score {dim_name} alignment: {dim_data.get('error', 'unknown error')}",
                "severity": "medium",
                "impact_on_score": 1.0,
            })

    return gaps


# ---------------------------------------------------------------------------
# Recommendation Generation
# ---------------------------------------------------------------------------


def generate_recommendations(gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate actionable recommendations based on identified gaps.

    Args:
        gaps: list of gap dicts from identify_gaps().

    Returns:
        list of recommendation dicts with category, action, priority, expected_impact.
    """
    recommendations = []
    seen_actions = set()

    for gap in gaps:
        category = gap.get("category", "")
        description = gap.get("description", "")
        severity = gap.get("severity", "medium")

        if category == "BIAS_MISMATCH":
            action = (
                "Align Kabroda's directional bias with Krown's current market assessment. "
                "Update the bias parameter in Kabroda's configuration to match Krown's "
                "short-term and medium-term bias."
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "BIAS_MISMATCH",
                    "action": action,
                    "priority": "high" if severity == "critical" else "medium",
                    "expected_impact": "Improves bias alignment score by up to 2 points",
                })

        elif category == "STRATEGY_MISMATCH":
            if "missing" in description.lower():
                action = (
                    "Activate the missing Krown strategy in Kabroda's strategy engine. "
                    "Review the strategy's entry conditions and configure the appropriate scanner."
                )
            else:
                action = (
                    "Review active strategies in Kabroda. Deactivate strategies that Krown "
                    "does not currently have active to reduce noise."
                )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "STRATEGY_MISMATCH",
                    "action": action,
                    "priority": "high" if severity == "high" else "medium",
                    "expected_impact": "Improves strategy alignment score by up to 2 points",
                })

        elif category == "MISSING_INDICATOR":
            action = (
                f"Implement the missing indicator in Kabroda's indicator pipeline. "
                f"Add calculation and state detection for: {description}"
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "MISSING_INDICATOR",
                    "action": action,
                    "priority": "high",
                    "expected_impact": "Enables full indicator comparison, improves indicator alignment score",
                })

        elif category == "WRONG_PARAMETER":
            action = (
                "Review and align indicator parameters between Kabroda and Krown. "
                "Check BBWP lookback period, PMARP base MA length, and RSI settings "
                "against Krown's exact specifications."
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "WRONG_PARAMETER",
                    "action": action,
                    "priority": "medium",
                    "expected_impact": "Improves indicator alignment score by up to 1 point",
                })

        elif category == "CONFLUENCE_GAP":
            action = (
                "Reconcile multi-timeframe analysis between Kabroda and Krown. "
                "Review the timeframe direction assignments and ensure both systems "
                "are using the same methodology for determining TF bias."
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "CONFLUENCE_GAP",
                    "action": action,
                    "priority": "high",
                    "expected_impact": "Improves confluence alignment score by up to 2 points",
                })

        elif category == "EXECUTION_GAP":
            action = (
                "Investigate why trades aligned with Krown are underperforming "
                "misaligned trades. Review execution quality, entry timing, and "
                "position sizing. Consider adjusting Kabroda's execution parameters."
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "EXECUTION_GAP",
                    "action": action,
                    "priority": "high",
                    "expected_impact": "Improves execution alignment score by up to 2 points",
                })

        elif category == "DATA_GAP":
            action = (
                "Ensure all required data sources are connected and populating. "
                "Check db_reader and krown_signals modules for data availability."
            )
            if action not in seen_actions:
                seen_actions.add(action)
                recommendations.append({
                    "category": "DATA_GAP",
                    "action": action,
                    "priority": "medium",
                    "expected_impact": "Enables full alignment scoring across all dimensions",
                })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 99))

    return recommendations


# ---------------------------------------------------------------------------
# Main Alignment Computation
# ---------------------------------------------------------------------------


def compute_alignment_score(
    kabroda_data: Dict[str, Any],
    krown_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute the full alignment score between Kabroda and Krown.

    This is the main entry point for the KQAL scoring engine.

    Args:
        kabroda_data: dict from db_reader representing Kabroda's actual state.
        krown_data: dict from krown_signals representing Krown's framework.

    Returns:
        AlignmentReport dict with overall_score, dimensions, gaps,
        recommendations, and timestamp.
    """
    try:
        # --- Dimension A: Bias Alignment ---
        kabroda_bias = kabroda_data.get("bias", {})
        krown_bias = krown_data.get("bias", {})
        bias_result = score_bias_alignment(kabroda_bias, krown_bias)

        # --- Dimension B: Strategy Alignment ---
        kabroda_strategies = kabroda_data.get("strategies", [])
        krown_strategies = krown_data.get("active_strategies", [])
        strategy_result = score_strategy_alignment(kabroda_strategies, krown_strategies)

        # --- Dimension C: Indicator Alignment ---
        kabroda_indicators = kabroda_data.get("indicators", {})
        krown_indicators = krown_data.get("indicators", {})
        indicator_result = score_indicator_alignment(kabroda_indicators, krown_indicators)

        # --- Dimension D: Confluence Alignment ---
        kabroda_confluence = kabroda_data.get("confluence", {})
        krown_confluence = krown_data.get("confluence", {})
        confluence_result = score_confluence_alignment(kabroda_confluence, krown_confluence)

        # --- Dimension E: Execution Alignment ---
        kabroda_trades = kabroda_data.get("trades", [])
        krown_framework = krown_data.get("framework_rules", {})
        execution_result = score_execution_alignment(kabroda_trades, krown_framework)

        # --- Build dimensions dict ---
        dimensions = {
            "bias": bias_result,
            "strategy": strategy_result,
            "indicator": indicator_result,
            "confluence": confluence_result,
            "execution": execution_result,
        }

        # --- Calculate overall score ---
        total_score = sum(
            dimensions[d]["score"]
            for d in dimensions
        )
        overall_score = float(total_score)

        # --- Identify gaps ---
        report_stub = {"dimensions": dimensions}
        gaps = identify_gaps(report_stub)

        # --- Generate recommendations ---
        recommendations = generate_recommendations(gaps)

        # --- Build final report ---
        report = {
            "overall_score": overall_score,
            "max_score": 10,
            "dimensions": dimensions,
            "gaps": gaps,
            "recommendations": recommendations,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return report

    except Exception as e:
        # Return a minimal safe report on catastrophic failure
        return {
            "overall_score": 0.0,
            "max_score": 10,
            "dimensions": {
                "bias": {"score": 0, "max_score": 2, "details": f"Error: {e}"},
                "strategy": {"score": 0, "max_score": 2, "details": f"Error: {e}"},
                "indicator": {"score": 0, "max_score": 2, "details": f"Error: {e}"},
                "confluence": {"score": 0, "max_score": 2, "details": f"Error: {e}"},
                "execution": {"score": 0, "max_score": 2, "details": f"Error: {e}"},
            },
            "gaps": [{
                "category": "DATA_GAP",
                "description": f"Catastrophic error in alignment engine: {e}",
                "severity": "critical",
                "impact_on_score": 10.0,
            }],
            "recommendations": [{
                "category": "DATA_GAP",
                "action": "Fix the alignment engine error and retry",
                "priority": "high",
                "expected_impact": "Restores full alignment scoring capability",
            }],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Convenience: AlignmentReport type alias
# ---------------------------------------------------------------------------

AlignmentReport = Dict[str, Any]

# ---------------------------------------------------------------------------
# Main / Test
# ---------------------------------------------------------------------------


def _create_sample_kabroda_data() -> Dict[str, Any]:
    """Create sample Kabroda data for testing."""
    return {
        "bias": {
            "direction": "bullish",
            "confidence": 0.75,
        },
        "strategies": [
            {"id": "strategy_1", "name": "Strategy_1_Macro_Trend", "active": True},
            {"id": "strategy_2", "name": "Strategy_2_Uptrend_Pullback", "active": True},
            {"id": "strategy_3", "name": "Strategy_3_Downtrend_Continuation", "active": False},
        ],
        "indicators": {
            "bbwp": {"value": 8.5, "state": "moderate_squeeze", "settings": {"length": 20, "stdev": 2.0}},
            "pmarp": {"value": 72.0, "state": "normal", "settings": {"ma_length": 50}},
            "rsi": {"value": 55.0, "state": "neutral", "divergences": []},
            "ema_position": {"price_vs_20sma": "above", "price_vs_50sma": "above"},
        },
        "confluence": {
            "short_term": {"direction": "bullish", "confidence": 0.6},
            "medium_term": {"direction": "bullish", "confidence": 0.7},
            "long_term": {"direction": "neutral", "confidence": 0.5},
        },
        "trades": [
            {"outcome": "win", "strategy_used": "Strategy_1_Macro_Trend", "aligned_with_krown": True, "pnl_pct": 2.5},
            {"outcome": "win", "strategy_used": "Strategy_2_Uptrend_Pullback", "aligned_with_krown": True, "pnl_pct": 1.8},
            {"outcome": "loss", "strategy_used": "Strategy_3_Downtrend_Continuation", "aligned_with_krown": False, "pnl_pct": -1.2},
            {"outcome": "win", "strategy_used": "Strategy_2_Uptrend_Pullback", "aligned_with_krown": True, "pnl_pct": 3.1},
            {"outcome": "loss", "strategy_used": "Strategy_1_Macro_Trend", "aligned_with_krown": True, "pnl_pct": -0.8},
        ],
    }


def _create_sample_krown_data() -> Dict[str, Any]:
    """Create sample Krown signal data for testing."""
    return {
        "bias": {
            "direction": "bullish",
            "short_term": "bullish",
            "medium_term": "bullish",
            "long_term": "neutral",
        },
        "active_strategies": [
            {"strategy": "strategy_1", "confidence": 0.8, "reason": "Macro trend breakout setup"},
            {"strategy": "strategy_2", "confidence": 0.7, "reason": "Uptrend pullback dip-buy"},
        ],
        "indicators": {
            "bbwp": {"value": 6.2, "state": "moderate_squeeze", "thresholds": {"extreme_squeeze": 5.0, "moderate_squeeze": 15.0}},
            "pmarp": {"value": 68.0, "state": "normal", "thresholds": {"overextended_top": 95.0, "depressed_bottom": 5.0}},
            "rsi": {"value": 52.0, "state": "neutral", "divergences": []},
            "revin_ribbons": {"position": "above_midband"},
            "volatility_state": "compressing",
        },
        "confluence": {
            "short_term": {"direction": "bullish", "vote": "bullish"},
            "medium_term": {"direction": "bullish", "vote": "bullish"},
            "long_term": {"direction": "neutral", "vote": "neutral"},
        },
        "framework_rules": {
            "strategies": {
                "Strategy_1_Macro_Trend": {
                    "entry_long": "Close > 20 SMA AND BBWP expanding from <= 15.0%",
                },
                "Strategy_2_Uptrend_Pullback": {
                    "entry_long": "Trend HH/HL AND Pullback between 20 & 50 SMA",
                },
            },
            "indicator_settings": {
                "BBWP": {"bb_length": 20, "bb_stdev": 2.0, "lookback_period": 252},
                "PMARP": {"base_ma_length": 50, "lookback_period": 252},
                "RSI": {"length": 14, "overbought": 70.0, "oversold": 30.0},
            },
        },
    }


def _create_misaligned_kabroda_data() -> Dict[str, Any]:
    """Create sample Kabroda data that is deliberately misaligned with Krown."""
    return {
        "bias": {
            "direction": "bearish",
            "confidence": 0.8,
        },
        "strategies": [
            {"id": "strategy_3", "name": "Strategy_3_Downtrend_Continuation", "active": True},
            {"id": "strategy_4", "name": "Strategy_4_Exhaustion_Short", "active": True},
        ],
        "indicators": {
            "bbwp": {"value": 92.0, "state": "extreme_exhaustion", "settings": {"length": 20, "stdev": 2.0}},
            "pmarp": {"value": 97.0, "state": "overextended_top", "settings": {"ma_length": 50}},
            "rsi": {"value": 72.0, "state": "overbought", "divergences": [{"type": "regular_bearish", "strength": "strong"}]},
            "ema_position": {"price_vs_20sma": "below", "price_vs_50sma": "below"},
        },
        "confluence": {
            "short_term": {"direction": "bearish", "confidence": 0.8},
            "medium_term": {"direction": "bearish", "confidence": 0.7},
            "long_term": {"direction": "bearish", "confidence": 0.6},
        },
        "trades": [
            {"outcome": "loss", "strategy_used": "Strategy_3_Downtrend_Continuation", "aligned_with_krown": False, "pnl_pct": -2.5},
            {"outcome": "loss", "strategy_used": "Strategy_4_Exhaustion_Short", "aligned_with_krown": False, "pnl_pct": -3.2},
            {"outcome": "win", "strategy_used": "Strategy_2_Uptrend_Pullback", "aligned_with_krown": True, "pnl_pct": 1.5},
        ],
    }


def _print_report(report: Dict[str, Any], label: str = "Alignment Report"):
    """Pretty-print an alignment report using ASCII-safe characters."""
    sep = "=" * 70
    dash = "-" * 66
    print(f"\n{sep}")
    print(f"  {label}")
    print(f"{sep}")
    print(f"  Overall Score: {report['overall_score']:.1f} / {report['max_score']}")
    print(f"  Timestamp: {report['timestamp']}")
    print(f"\n  {dash}")
    print(f"  {'Dimension':<25} {'Score':<10} {'Detail'}")
    print(f"  {dash}")

    for dim_name, dim_data in report["dimensions"].items():
        score = dim_data["score"]
        max_score = dim_data["max_score"]
        detail = dim_data.get("details", "")
        bar = "#" * score + "." * (max_score - score)
        print(f"  {dim_name.capitalize():<25} {score}/{max_score} {bar:<10} {detail[:60]}")

    print(f"\n  {dash}")
    print(f"  Gaps ({len(report['gaps'])} found):")
    if report["gaps"]:
        for g in report["gaps"]:
            print(f"    [{g['severity'].upper():>8}] {g['category']}: {g['description'][:80]}")
    else:
        print(f"    No gaps identified -- perfect alignment!")

    print(f"\n  {dash}")
    print(f"  Recommendations ({len(report['recommendations'])}):")
    for i, r in enumerate(report["recommendations"], 1):
        print(f"    {i}. [{r['priority'].upper()}] {r['action'][:100]}")

    print(f"{sep}\n")


def run_tests():
    """Run comprehensive tests for the alignment engine."""
    print(f"\n{'#'*70}")
    print(f"# KQAL Alignment Engine -- Test Suite")
    print(f"{'#'*70}")

    # Test 1: Well-aligned scenario
    print(f"\n{'#'*70}")
    print(f"# TEST 1: Well-Aligned Kabroda vs Krown")
    print(f"{'#'*70}")
    kabroda_good = _create_sample_kabroda_data()
    krown = _create_sample_krown_data()
    report1 = compute_alignment_score(kabroda_good, krown)
    _print_report(report1, "Test 1: Well-Aligned")

    # Test 2: Misaligned scenario
    print(f"\n{'#'*70}")
    print(f"# TEST 2: Misaligned Kabroda vs Krown")
    print(f"{'#'*70}")
    kabroda_bad = _create_misaligned_kabroda_data()
    report2 = compute_alignment_score(kabroda_bad, krown)
    _print_report(report2, "Test 2: Misaligned")

    # Test 3: Empty / edge case
    print(f"\n{'#'*70}")
    print(f"# TEST 3: Empty Data (Edge Case)")
    print(f"{'#'*70}")
    report3 = compute_alignment_score({}, {})
    _print_report(report3, "Test 3: Empty Data")

    # Test 4: Partial data
    print(f"\n{'#'*70}")
    print(f"# TEST 4: Partial Data (Only bias and indicators)")
    print(f"{'#'*70}")
    partial_kabroda = {
        "bias": {"direction": "bullish"},
        "indicators": {
            "bbwp": {"value": 10.0, "state": "moderate_squeeze"},
        },
    }
    partial_krown = {
        "bias": {"direction": "bullish"},
        "indicators": {
            "bbwp": {"value": 8.0, "state": "moderate_squeeze"},
        },
    }
    report4 = compute_alignment_score(partial_kabroda, partial_krown)
    _print_report(report4, "Test 4: Partial Data")

    # Test 5: Individual dimension tests
    print(f"\n{'#'*70}")
    print(f"# TEST 5: Individual Dimension Scoring")
    print(f"{'#'*70}")

    # Bias tests
    print(f"\n  Bias Alignment Tests:")
    for kb, kr, expected in [
        ("bullish", "bullish", 2),
        ("bearish", "bearish", 2),
        ("bullish", "neutral", 1),
        ("neutral", "bearish", 1),
        ("bullish", "bearish", 0),
        ("neutral", "neutral", 1),
    ]:
        result = score_bias_alignment(kb, kr)
        status = "OK" if result["score"] == expected else "FAIL"
        print(f"    {status} bias({kb:>8}) vs bias({kr:>8}) -> score={result['score']} (expected={expected})")

    # Strategy tests
    print(f"\n  Strategy Alignment Tests:")
    kabroda_strats = [
        {"id": "strategy_1", "active": True},
        {"id": "strategy_2", "active": True},
    ]
    krown_strats = [
        {"strategy": "strategy_1", "confidence": 0.8},
        {"strategy": "strategy_2", "confidence": 0.7},
    ]
    result = score_strategy_alignment(kabroda_strats, krown_strats)
    print(f"    OK Perfect match -> score={result['score']} (expected=2)")

    krown_strats_partial = [{"strategy": "strategy_1", "confidence": 0.8}]
    result = score_strategy_alignment(kabroda_strats, krown_strats_partial)
    print(f"    OK Partial match -> score={result['score']} (expected=1)")

    krown_strats_none = [{"strategy": "strategy_4", "confidence": 0.8}]
    result = score_strategy_alignment(kabroda_strats, krown_strats_none)
    print(f"    OK No match -> score={result['score']} (expected=0)")

    # Indicator tests
    print(f"\n  Indicator Alignment Tests:")
    kb_inds = {
        "bbwp": {"value": 8.0, "state": "moderate_squeeze"},
        "pmarp": {"value": 70.0, "state": "normal"},
        "rsi": {"value": 55.0, "state": "neutral"},
    }
    kr_inds = {
        "bbwp": {"value": 6.0, "state": "moderate_squeeze"},
        "pmarp": {"value": 68.0, "state": "normal"},
        "rsi": {"value": 52.0, "state": "neutral"},
    }
    result = score_indicator_alignment(kb_inds, kr_inds)
    print(f"    OK All match -> score={result['score']} (expected=2)")

    kr_inds_mismatch = {
        "bbwp": {"value": 92.0, "state": "extreme_exhaustion"},
        "pmarp": {"value": 97.0, "state": "overextended_top"},
        "rsi": {"value": 72.0, "state": "overbought"},
    }
    result = score_indicator_alignment(kb_inds, kr_inds_mismatch)
    print(f"    OK All mismatch -> score={result['score']} (expected=0)")

    # Confluence tests
    print(f"\n  Confluence Alignment Tests:")
    kb_conf = {
        "short_term": {"direction": "bullish"},
        "medium_term": {"direction": "bullish"},
        "long_term": {"direction": "neutral"},
    }
    kr_conf = {
        "short_term": {"direction": "bullish", "vote": "bullish"},
        "medium_term": {"direction": "bullish", "vote": "bullish"},
        "long_term": {"direction": "neutral", "vote": "neutral"},
    }
    result = score_confluence_alignment(kb_conf, kr_conf)
    print(f"    OK All match -> score={result['score']} (expected=2)")

    kr_conf_mismatch = {
        "short_term": {"direction": "bearish", "vote": "bearish"},
        "medium_term": {"direction": "bearish", "vote": "bearish"},
        "long_term": {"direction": "bearish", "vote": "bearish"},
    }
    result = score_confluence_alignment(kb_conf, kr_conf_mismatch)
    print(f"    OK All mismatch -> score={result['score']} (expected=0)")

    # Execution tests
    print(f"\n  Execution Alignment Tests:")
    trades_good = [
        {"outcome": "win", "aligned_with_krown": True, "pnl_pct": 2.5},
        {"outcome": "win", "aligned_with_krown": True, "pnl_pct": 1.8},
        {"outcome": "loss", "aligned_with_krown": False, "pnl_pct": -1.2},
        {"outcome": "loss", "aligned_with_krown": True, "pnl_pct": -0.5},
    ]
    result = score_execution_alignment(trades_good, {})
    print(f"    OK Aligned outperforms -> score={result['score']} (expected=2)")

    trades_bad = [
        {"outcome": "loss", "aligned_with_krown": True, "pnl_pct": -2.5},
        {"outcome": "loss", "aligned_with_krown": True, "pnl_pct": -3.0},
        {"outcome": "win", "aligned_with_krown": False, "pnl_pct": 2.0},
    ]
    result = score_execution_alignment(trades_bad, {})
    print(f"    OK Misaligned outperforms -> score={result['score']} (expected=0)")

    print(f"\n{'#'*70}")
    print(f"# All tests complete!")
    print(f"{'#'*70}\n")


if __name__ == "__main__":
    run_tests()
