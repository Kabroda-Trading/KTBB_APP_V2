#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Krown Signals Module — KQAL (Kabroda Quality Assurance Layer)
=============================================================
Loads and structures Krown YouTube content and bridge data for consumption
by the Kabroda AI agent and downstream quality checks.

Data Sources:
  - youtube_streams_analysis.json  → Krown YouTube video analysis with keyword-tagged snippets
  - krown_strategy_evaluation.json  → Strategy simulation results from the Krown Trading Bible
  - bridge_state.json              → Krown→Kabroda bridge processing state
  - krown_to_kabroda_bridge.py     → Indicator/strategy mapping definitions (read as module)

Usage:
  from kqal.krown_signals import (
      get_krown_current_bias,
      get_krown_strategy_map,
      get_krown_indicator_settings,
      get_krown_recent_signals,
      get_krown_key_levels,
      get_krown_ema_ribbon,
      get_krown_revin_ribbons_info,
      get_krown_three_drives_info,
      get_bridge_state,
      get_strategy_evaluations,
      get_bridge_indicator_mapping,
  )
"""

import os
import sys
import json
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from functools import lru_cache

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
YOUTUBE_ANALYSIS_PATH = os.path.join(BASE_DIR, "extract", "youtube_streams_analysis.json")
STRATEGY_EVAL_PATH = os.path.join(BASE_DIR, "pipeline", "krown_strategy_evaluation.json")
BRIDGE_STATE_PATH = os.path.join(BASE_DIR, "pipeline", "bridge_state.json")
BRIDGE_PY_PATH = os.path.join(BASE_DIR, "pipeline", "krown_to_kabroda_bridge.py")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("kqal.krown_signals")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE: Dict[str, Tuple[float, Any]] = {}


def _cached_load(file_path: str, loader: callable) -> Any:
    """Load a file with a 5-minute TTL cache."""
    now = datetime.now().timestamp()
    if file_path in _CACHE:
        expiry, data = _CACHE[file_path]
        if now < expiry:
            return data
    try:
        data = loader(file_path)
        _CACHE[file_path] = (now + _CACHE_TTL_SECONDS, data)
        return data
    except Exception as e:
        logger.error(f"Failed to load {file_path}: {e}")
        return None


def _load_json(file_path: str) -> Optional[Any]:
    """Load a JSON file with caching."""
    def _loader(path: str):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return _cached_load(file_path, _loader)


def _load_bridge_module() -> Optional[Any]:
    """Dynamically import the bridge module to access its mappings."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("krown_bridge", BRIDGE_PY_PATH)
        if spec is None or spec.loader is None:
            logger.error(f"Could not load bridge module spec from {BRIDGE_PY_PATH}")
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        logger.error(f"Failed to import bridge module: {e}")
        return None


# ---------------------------------------------------------------------------
# Number extraction helpers
# ---------------------------------------------------------------------------

_PRICE_PATTERN = re.compile(
    r'(?:\$)?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:k|K|bucks|\$)?'
)


def _extract_numbers(text: str) -> List[float]:
    """Extract meaningful price-like values from a snippet of text.

    Filters out small ordinal-like numbers (1-9) and focuses on
    realistic price levels (>= 10 or with 3+ digits).
    """
    matches = _PRICE_PATTERN.findall(text)
    results = []
    for m in matches:
        cleaned = m.replace(",", "")
        try:
            val = float(cleaned)
            # Filter to reasonable price ranges (10–1,000,000)
            # Skip single-digit numbers that are likely ordinals or counts
            if 10.0 <= val <= 1_000_000.0:
                results.append(val)
        except ValueError:
            continue
    return results


def _parse_bias_from_snippets(snippets: List[str]) -> Dict[str, Any]:
    """Heuristically determine bias from keyword-tagged snippets."""
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    total = len(snippets)

    for snippet in snippets:
        lower = snippet.lower()
        # Bullish indicators
        if any(phrase in lower for phrase in [
            "bullish", "bounce", "reclaim", "rally", "buy", "long",
            "upside", "higher low", "support", "hopium"
        ]):
            bullish_count += 1
        # Bearish indicators
        if any(phrase in lower for phrase in [
            "bearish", "dump", "short", "sell", "downside", "lower high",
            "resistance", "breakdown", "crush", "rejection"
        ]):
            bearish_count += 1

    # Determine overall bias
    if bullish_count > bearish_count and bullish_count > total * 0.2:
        bias = "bullish"
        confidence = min(round(bullish_count / max(total, 1) * 100, 1), 100.0)
    elif bearish_count > bullish_count and bearish_count > total * 0.2:
        bias = "bearish"
        confidence = min(round(bearish_count / max(total, 1) * 100, 1), 100.0)
    else:
        bias = "neutral"
        confidence = 50.0

    return {
        "bias": bias,
        "confidence": confidence,
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
        "total_snippets": total,
    }


# ===========================================================================
# Public API — YouTube Analysis
# ===========================================================================

def get_krown_current_bias() -> Dict[str, Any]:
    """
    Aggregate bias from recent Krown YouTube videos.

    Returns a dict with:
      - overall_bias: "bullish" | "bearish" | "neutral"
      - confidence: float 0–100
      - breakdown: per-video bias analysis
      - video_count: int
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return {"overall_bias": "neutral", "confidence": 0.0, "breakdown": [], "video_count": 0}

        video_biases = []
        for video in data:
            title = video.get("video_title", "Unknown")
            takeaways = video.get("key_takeaways", [])
            snippets = [t.get("snippet", "") for t in takeaways]
            bias_info = _parse_bias_from_snippets(snippets)
            bias_info["video_title"] = title
            video_biases.append(bias_info)

        # Aggregate across all videos
        bullish_total = sum(v["bullish_signals"] for v in video_biases)
        bearish_total = sum(v["bearish_signals"] for v in video_biases)
        total_snippets = sum(v["total_snippets"] for v in video_biases)

        if bullish_total > bearish_total:
            overall = "bullish"
            conf = min(round(bullish_total / max(total_snippets, 1) * 100, 1), 100.0)
        elif bearish_total > bullish_total:
            overall = "bearish"
            conf = min(round(bearish_total / max(total_snippets, 1) * 100, 1), 100.0)
        else:
            overall = "neutral"
            conf = 50.0

        return {
            "overall_bias": overall,
            "confidence": conf,
            "breakdown": video_biases,
            "video_count": len(data),
            "bullish_total": bullish_total,
            "bearish_total": bearish_total,
            "total_snippets": total_snippets,
        }
    except Exception as e:
        logger.error(f"get_krown_current_bias failed: {e}")
        return {"overall_bias": "neutral", "confidence": 0.0, "breakdown": [], "video_count": 0}


def get_krown_strategy_map() -> Dict[str, Any]:
    """
    Determine which of Krown's 5 strategies are active based on video analysis.

    Returns a dict mapping strategy names to their activation status and evidence.
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return _default_strategy_map()

        # Collect all snippets
        all_snippets = []
        for video in data:
            for t in video.get("key_takeaways", []):
                all_snippets.append(t.get("snippet", ""))

        combined = " ".join(all_snippets).lower()

        strategies = {
            "Strategy_1_Macro_Trend": {
                "name": "Basic Long/Short Macro Trend System",
                "active": False,
                "evidence": [],
                "keywords": ["macro", "trend", "breakout", "swing", "big move"],
            },
            "Strategy_2_Uptrend_Pullback": {
                "name": "Uptrend Pullback Long Scalp System",
                "active": False,
                "evidence": [],
                "keywords": ["pullback", "dip", "buy the dip", "support bounce", "value zone"],
            },
            "Strategy_3_Downtrend_Continuation": {
                "name": "Downtrend Continuation Short Scalp System",
                "active": False,
                "evidence": [],
                "keywords": ["downtrend", "continuation", "rally sell", "lower high", "bounce short"],
            },
            "Strategy_4_Uptrend_Exhaustion_Short": {
                "name": "Uptrend Parabolic Exhaustion Counter-Trend Short",
                "active": False,
                "evidence": [],
                "keywords": ["exhaustion", "blow-off", "parabolic", "overextended", "blow off top"],
            },
            "Strategy_5_Downtrend_Vol_Short": {
                "name": "Downtrend Volatility Surge Breakdown Short",
                "active": False,
                "evidence": [],
                "keywords": ["breakdown", "volatility surge", "support collapse", "momentum breakdown"],
            },
        }

        for strat_id, strat_info in strategies.items():
            matches = []
            for kw in strat_info["keywords"]:
                if kw in combined:
                    matches.append(kw)
            if matches:
                strat_info["active"] = True
                strat_info["evidence"] = matches

        # Also check strategy evaluation for active strategies
        eval_data = _load_json(STRATEGY_EVAL_PATH)
        if eval_data and isinstance(eval_data, dict):
            simulations = eval_data.get("simulation_results", {})
            for sim_name, sim_data in simulations.items():
                best_signal = sim_data.get("best_actionable_signal", {})
                strat_name = best_signal.get("strategy_name", "NONE")
                if strat_name != "NONE":
                    for sid, sinfo in strategies.items():
                        if sid == strat_name or sid.replace("_", "") == strat_name.replace("_", ""):
                            sinfo["active"] = True
                            sinfo["evidence"].append(f"Confirmed by {sim_name} simulation")

        return {
            "strategies": strategies,
            "active_count": sum(1 for s in strategies.values() if s["active"]),
            "total_strategies": len(strategies),
        }
    except Exception as e:
        logger.error(f"get_krown_strategy_map failed: {e}")
        return _default_strategy_map()


def _default_strategy_map() -> Dict[str, Any]:
    """Return a safe default strategy map."""
    return {
        "strategies": {
            f"Strategy_{i}": {
                "name": f"Strategy {i}",
                "active": False,
                "evidence": [],
            }
            for i in range(1, 6)
        },
        "active_count": 0,
        "total_strategies": 5,
    }


def get_krown_indicator_settings() -> Dict[str, Any]:
    """
    Extract indicator parameters Krown uses from video snippets.

    Returns a dict with settings for:
      - EMA lengths (5, 21, 55, 377 Fibonacci stack)
      - RSI settings
      - BBWP thresholds
      - Revin Ribbons configuration
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return _default_indicator_settings()

        all_snippets = []
        for video in data:
            for t in video.get("key_takeaways", []):
                all_snippets.append(t.get("snippet", ""))

        combined = " ".join(all_snippets)

        # --- EMA Settings ---
        ema_settings = {
            "ema_5": {"period": 5, "color": "red", "confirmed": "5 EMA" in combined},
            "ema_21": {"period": 21, "color": "yellow", "confirmed": "21 EMA" in combined},
            "ema_55": {"period": 55, "color": "green", "confirmed": "55 EMA" in combined},
            "ema_377": {"period": 377, "color": "blue", "confirmed": "377 EMA" in combined},
        }

        # --- RSI Settings ---
        rsi_settings = {
            "period": 14,
            "overbought": 70,
            "oversold": 30,
            "confirmed": "RSI" in combined or "rsi" in combined,
        }

        # --- BBWP Thresholds ---
        bbwp_settings = {
            "extreme_squeeze_threshold": 5.0,
            "moderate_squeeze_threshold": 15.0,
            "high_expansion_threshold": 85.0,
            "extreme_exhaustion_threshold": 95.0,
            "confirmed": "BBWP" in combined or "bbwp" in combined,
        }

        # --- Revin Ribbons ---
        revin_settings = {
            "indicator_name": "Revin Ribbons",
            "midband_used": True,
            "lower_band_1_used": True,
            "upper_band_1_used": True,
            "confirmed": ("revin" in combined.lower() or "ribbon" in combined.lower()),
        }

        return {
            "ema": ema_settings,
            "rsi": rsi_settings,
            "bbwp": bbwp_settings,
            "revin_ribbons": revin_settings,
            "source": "extracted from YouTube analysis snippets",
        }
    except Exception as e:
        logger.error(f"get_krown_indicator_settings failed: {e}")
        return _default_indicator_settings()


def _default_indicator_settings() -> Dict[str, Any]:
    """Return safe default indicator settings."""
    return {
        "ema": {
            "ema_5": {"period": 5, "color": "red", "confirmed": False},
            "ema_21": {"period": 21, "color": "yellow", "confirmed": False},
            "ema_55": {"period": 55, "color": "green", "confirmed": False},
            "ema_377": {"period": 377, "color": "blue", "confirmed": False},
        },
        "rsi": {"period": 14, "overbought": 70, "oversold": 30, "confirmed": False},
        "bbwp": {
            "extreme_squeeze_threshold": 5.0,
            "moderate_squeeze_threshold": 15.0,
            "high_expansion_threshold": 85.0,
            "extreme_exhaustion_threshold": 95.0,
            "confirmed": False,
        },
        "revin_ribbons": {
            "indicator_name": "Revin Ribbons",
            "midband_used": True,
            "lower_band_1_used": True,
            "upper_band_1_used": True,
            "confirmed": False,
        },
        "source": "defaults (no data available)",
    }


def get_krown_recent_signals(days: int = 30) -> List[Dict[str, Any]]:
    """
    Return recent signal history from Krown YouTube videos.

    Args:
        days: Number of days to look back (default 30).

    Returns a list of signal dicts, each containing:
      - video_title, video_url
      - date (approximate from filename/order)
      - keywords found
      - extracted price levels
      - bias assessment
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return []

        signals = []
        for video in data:
            title = video.get("video_title", "Unknown")
            url = video.get("video_url", "")
            takeaways = video.get("key_takeaways", [])

            # Collect all keywords and snippets
            keywords_found = list(set(t.get("keyword", "") for t in takeaways))
            snippets = [t.get("snippet", "") for t in takeaways]

            # Extract price levels from all snippets
            all_numbers = []
            for snippet in snippets:
                all_numbers.extend(_extract_numbers(snippet))

            # Deduplicate and sort price levels
            price_levels = sorted(set(all_numbers))

            # Bias assessment
            bias_info = _parse_bias_from_snippets(snippets)

            signal = {
                "video_title": title,
                "video_url": url,
                "keywords": keywords_found,
                "snippet_count": len(takeaways),
                "extracted_price_levels": price_levels,
                "bias": bias_info["bias"],
                "bias_confidence": bias_info["confidence"],
            }
            signals.append(signal)

        return signals
    except Exception as e:
        logger.error(f"get_krown_recent_signals failed: {e}")
        return []


def get_krown_key_levels() -> Dict[str, Any]:
    """
    Extract key price levels mentioned across Krown videos.

    Returns a dict with:
      - support_levels: list of support prices with context
      - resistance_levels: list of resistance prices with context
      - target_levels: list of target prices with context
      - all_mentioned_levels: sorted list of all unique price levels
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return {"support_levels": [], "resistance_levels": [], "target_levels": [], "all_mentioned_levels": []}

        support_levels = []
        resistance_levels = []
        target_levels = []
        all_numbers = []

        for video in data:
            title = video.get("video_title", "Unknown")
            for t in video.get("key_takeaways", []):
                keyword = t.get("keyword", "")
                snippet = t.get("snippet", "")
                numbers = _extract_numbers(snippet)
                all_numbers.extend(numbers)

                for num in numbers:
                    entry = {"price": num, "video": title, "snippet_excerpt": snippet[:120] + "..."}
                    if keyword == "support":
                        support_levels.append(entry)
                    elif keyword == "resistance":
                        resistance_levels.append(entry)
                    elif keyword == "target":
                        target_levels.append(entry)

        # Deduplicate and sort
        def _dedupe_and_sort(levels: List[Dict]) -> List[Dict]:
            seen = set()
            unique = []
            for lv in sorted(levels, key=lambda x: x["price"]):
                key = round(lv["price"], 2)
                if key not in seen:
                    seen.add(key)
                    unique.append(lv)
            return unique

        return {
            "support_levels": _dedupe_and_sort(support_levels),
            "resistance_levels": _dedupe_and_sort(resistance_levels),
            "target_levels": _dedupe_and_sort(target_levels),
            "all_mentioned_levels": sorted(set(round(n, 2) for n in all_numbers)),
        }
    except Exception as e:
        logger.error(f"get_krown_key_levels failed: {e}")
        return {"support_levels": [], "resistance_levels": [], "target_levels": [], "all_mentioned_levels": []}


def get_krown_ema_ribbon() -> Dict[str, Any]:
    """
    Extract the Fibonacci EMA stack (5/21/55/377) that Krown actually uses.

    Returns a dict with:
      - emas: list of EMA configs {period, color, confirmed, last_mentioned_value}
      - ribbon_active: bool — whether the ribbon is actively being referenced
      - cross_signals: any cross events mentioned (5/21 cross, etc.)
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return {"emas": [], "ribbon_active": False, "cross_signals": []}

        all_snippets = []
        for video in data:
            for t in video.get("key_takeaways", []):
                all_snippets.append(t.get("snippet", ""))

        combined = " ".join(all_snippets)

        ema_configs = [
            {"period": 5, "color": "red", "label": "5 EMA", "confirmed": "5 EMA" in combined},
            {"period": 21, "color": "yellow", "label": "21 EMA", "confirmed": "21 EMA" in combined},
            {"period": 55, "color": "green", "label": "55 EMA", "confirmed": "55 EMA" in combined},
            {"period": 377, "color": "blue", "label": "377 EMA", "confirmed": "377 EMA" in combined},
        ]

        # Extract last mentioned values for each EMA
        for ema in ema_configs:
            label = ema["label"]
            # Look for patterns like "5 EMA which is effectively 60,000"
            pattern = re.compile(
                rf'{ema["period"]}\s*EMA.*?(?:is|at|around|about|hovering)\s*\$?(\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?)',
                re.IGNORECASE
            )
            match = pattern.search(combined)
            if match:
                try:
                    ema["last_mentioned_value"] = float(match.group(1).replace(",", ""))
                except ValueError:
                    ema["last_mentioned_value"] = None
            else:
                ema["last_mentioned_value"] = None

        # Detect cross signals
        cross_signals = []
        if re.search(r'cross\s+(between|of)\s+(the\s+)?(red\s+)?5\s*EMA', combined, re.IGNORECASE):
            cross_signals.append({
                "type": "5_21_cross",
                "description": "5 EMA / 21 EMA cross mentioned",
                "confirmed": True,
            })

        ribbon_active = any(e["confirmed"] for e in ema_configs)

        return {
            "emas": ema_configs,
            "ribbon_active": ribbon_active,
            "cross_signals": cross_signals,
            "fibonacci_sequence": [5, 21, 55, 377],
        }
    except Exception as e:
        logger.error(f"get_krown_ema_ribbon failed: {e}")
        return {"emas": [], "ribbon_active": False, "cross_signals": []}


def get_krown_revin_ribbons_info() -> Dict[str, Any]:
    """
    Extract Revin Ribbons midband price and band levels mentioned in videos.

    Returns a dict with:
      - midband_values: list of {value, video, snippet_excerpt}
      - lower_band_values: list of lower band prices mentioned
      - upper_band_values: list of upper band prices mentioned
      - current_midband: most recently mentioned midband value
      - bias_implication: "bearish" if below midband, "bullish" if above
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return {
                "midband_values": [], "lower_band_values": [], "upper_band_values": [],
                "current_midband": None, "bias_implication": "neutral",
            }

        midband_values = []
        lower_band_values = []
        upper_band_values = []

        for video in data:
            title = video.get("video_title", "Unknown")
            for t in video.get("key_takeaways", []):
                keyword = t.get("keyword", "")
                snippet = t.get("snippet", "")
                lower = snippet.lower()

                # Check if this snippet is about Revin Ribbons
                if "revin" not in lower and "ribbon" not in lower:
                    continue

                numbers = _extract_numbers(snippet)

                # Midband detection
                if "midband" in lower or "mid band" in lower or "mid-band" in lower:
                    for num in numbers:
                        midband_values.append({
                            "value": num,
                            "video": title,
                            "snippet_excerpt": snippet[:120] + "...",
                        })

                # Lower band detection
                if "lower band" in lower or "lower-band" in lower:
                    for num in numbers:
                        lower_band_values.append({
                            "value": num,
                            "video": title,
                            "snippet_excerpt": snippet[:120] + "...",
                        })

                # Upper band detection
                if "upper band" in lower or "upper-band" in lower:
                    for num in numbers:
                        upper_band_values.append({
                            "value": num,
                            "video": title,
                            "snippet_excerpt": snippet[:120] + "...",
                        })

        # Determine current midband (most recently mentioned)
        current_midband = None
        if midband_values:
            # Take the last one (most recent video)
            current_midband = midband_values[-1]["value"]

        # Determine bias implication
        # If midband is mentioned with "below" context, bias is bearish
        bias_implication = "neutral"
        combined = " ".join(
            t.get("snippet", "") for video in data for t in video.get("key_takeaways", [])
        ).lower()
        if "below the" in combined and ("midband" in combined or "mid band" in combined):
            bias_implication = "bearish"
        elif "above the" in combined and ("midband" in combined or "mid band" in combined):
            bias_implication = "bullish"

        return {
            "midband_values": midband_values,
            "lower_band_values": lower_band_values,
            "upper_band_values": upper_band_values,
            "current_midband": current_midband,
            "bias_implication": bias_implication,
        }
    except Exception as e:
        logger.error(f"get_krown_revin_ribbons_info failed: {e}")
        return {
            "midband_values": [], "lower_band_values": [], "upper_band_values": [],
            "current_midband": None, "bias_implication": "neutral",
        }


def get_krown_three_drives_info() -> Dict[str, Any]:
    """
    Extract Three Drives divergence pattern requirements from video analysis.

    Returns a dict with:
      - detected: bool — whether the pattern is mentioned
      - pattern_type: "bullish" | "bearish" | None
      - target_ema: the EMA typically targeted (usually 21 EMA)
      - divergence_type: "regular" | "hidden"
      - mentions: list of snippet excerpts mentioning three drives
    """
    try:
        data = _load_json(YOUTUBE_ANALYSIS_PATH)
        if not data or not isinstance(data, list):
            return {"detected": False, "pattern_type": None, "target_ema": None, "divergence_type": None, "mentions": []}

        mentions = []
        pattern_type = None
        target_ema = None
        divergence_type = None

        for video in data:
            title = video.get("video_title", "Unknown")
            for t in video.get("key_takeaways", []):
                snippet = t.get("snippet", "")
                lower = snippet.lower()

                if "three drives" not in lower and "3 drives" not in lower and "three-drives" not in lower:
                    continue

                mentions.append({
                    "video": title,
                    "snippet_excerpt": snippet[:150] + "...",
                })

                # Determine pattern type
                if "bearish" in lower:
                    pattern_type = "bearish"
                elif "bullish" in lower:
                    pattern_type = "bullish"

                # Determine divergence type
                if "regular" in lower:
                    divergence_type = "regular"
                elif "hidden" in lower:
                    divergence_type = "hidden"

                # Extract target EMA
                ema_match = re.search(r'(yellow\s+)?(\d+)\s*EMA', snippet, re.IGNORECASE)
                if ema_match:
                    target_ema = int(ema_match.group(2))

        detected = len(mentions) > 0

        return {
            "detected": detected,
            "pattern_type": pattern_type,
            "target_ema": target_ema or 21,
            "divergence_type": divergence_type or "regular",
            "mentions": mentions,
            "description": (
                "Three Drives pattern: Krown looks for 3 drives of divergence "
                f"({'regular' if not divergence_type else divergence_type}) "
                f"with a target back to the {'21' if not target_ema else target_ema} EMA"
            ),
        }
    except Exception as e:
        logger.error(f"get_krown_three_drives_info failed: {e}")
        return {"detected": False, "pattern_type": None, "target_ema": None, "divergence_type": None, "mentions": []}


# ===========================================================================
# Public API — Bridge Data
# ===========================================================================

def get_bridge_state() -> Dict[str, Any]:
    """
    Load the Krown→Kabroda bridge processing state.

    Returns a dict with:
      - processed_signal_ids: list of video IDs already processed
      - processed_count: int
      - last_bridge_run: ISO timestamp or None
    """
    try:
        state = _load_json(BRIDGE_STATE_PATH)
        if not state or not isinstance(state, dict):
            return {"processed_signal_ids": [], "processed_count": 0, "last_bridge_run": None}

        processed = state.get("processed_signal_ids", [])
        return {
            "processed_signal_ids": processed,
            "processed_count": len(processed),
            "last_bridge_run": state.get("last_bridge_run"),
        }
    except Exception as e:
        logger.error(f"get_bridge_state failed: {e}")
        return {"processed_signal_ids": [], "processed_count": 0, "last_bridge_run": None}


def get_strategy_evaluations() -> Dict[str, Any]:
    """
    Load the full Krown strategy evaluation JSON.

    Returns the complete strategy evaluation data including:
      - system_version
      - supported_indicators
      - strategies (5 strategy names)
      - simulation_results with regime summaries, divergences, and actionable signals
    """
    try:
        data = _load_json(STRATEGY_EVAL_PATH)
        if not data or not isinstance(data, dict):
            return {
                "system_version": "unknown",
                "supported_indicators": [],
                "strategies": {},
                "simulation_results": {},
            }
        return data
    except Exception as e:
        logger.error(f"get_strategy_evaluations failed: {e}")
        return {
            "system_version": "unknown",
            "supported_indicators": [],
            "strategies": {},
            "simulation_results": {},
        }


def get_bridge_indicator_mapping() -> Dict[str, Any]:
    """
    Load the Krown→Kabroda indicator configuration mapping from the bridge module.

    Returns a dict with:
      - indicator_configs: the INDICATOR_TO_KABRODA_CONFIG mapping
      - strategy_actions: the STRATEGY_TO_KABRODA_ACTION mapping
      - source: path to the bridge module
    """
    try:
        mod = _load_bridge_module()
        if mod is None:
            return {"indicator_configs": {}, "strategy_actions": {}, "source": BRIDGE_PY_PATH}

        indicator_configs = getattr(mod, "INDICATOR_TO_KABRODA_CONFIG", {})
        strategy_actions = getattr(mod, "STRATEGY_TO_KABRODA_ACTION", {})

        return {
            "indicator_configs": indicator_configs,
            "strategy_actions": strategy_actions,
            "source": BRIDGE_PY_PATH,
        }
    except Exception as e:
        logger.error(f"get_bridge_indicator_mapping failed: {e}")
        return {"indicator_configs": {}, "strategy_actions": {}, "source": BRIDGE_PY_PATH}


# ===========================================================================
# Composite / Convenience Functions
# ===========================================================================

def get_all_krown_signals() -> Dict[str, Any]:
    """
    Aggregate all Krown signals into a single composite report.

    Returns a comprehensive dict with all signal data for the Kabroda AI agent.
    """
    return {
        "current_bias": get_krown_current_bias(),
        "strategy_map": get_krown_strategy_map(),
        "indicator_settings": get_krown_indicator_settings(),
        "recent_signals": get_krown_recent_signals(days=30),
        "key_levels": get_krown_key_levels(),
        "ema_ribbon": get_krown_ema_ribbon(),
        "revin_ribbons": get_krown_revin_ribbons_info(),
        "three_drives": get_krown_three_drives_info(),
        "bridge_state": get_bridge_state(),
        "strategy_evaluations": get_strategy_evaluations(),
        "bridge_indicator_mapping": get_bridge_indicator_mapping(),
    }


# ===========================================================================
# Main — Test / Demo
# ===========================================================================

def _print_section(title: str, data: Any, indent: int = 0):
    """Pretty-print a section of data for the test harness."""
    prefix = "  " * indent
    print(f"\n{prefix}{'=' * 60}")
    print(f"{prefix}{title}")
    print(f"{prefix}{'=' * 60}")
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (list, dict)) and len(str(value)) > 120:
                print(f"{prefix}  {key}:")
                if isinstance(value, list):
                    for i, item in enumerate(value[:5]):
                        if isinstance(item, dict):
                            print(f"{prefix}    [{i}]: {json.dumps(item, default=str)[:200]}")
                        else:
                            print(f"{prefix}    [{i}]: {item}")
                    if len(value) > 5:
                        print(f"{prefix}    ... ({len(value) - 5} more items)")
                elif isinstance(value, dict):
                    for k2, v2 in list(value.items())[:8]:
                        print(f"{prefix}    {k2}: {json.dumps(v2, default=str)[:150]}")
                    if len(value) > 8:
                        print(f"{prefix}    ... ({len(value) - 8} more keys)")
            else:
                print(f"{prefix}  {key}: {value}")
    elif isinstance(data, list):
        for i, item in enumerate(data[:5]):
            print(f"{prefix}  [{i}]: {json.dumps(item, default=str)[:200]}")
        if len(data) > 5:
            print(f"{prefix}  ... ({len(data) - 5} more items)")
    else:
        print(f"{prefix}  {data}")


def main():
    """Test all functions and print structured output."""
    print("=" * 60)
    print("  Krown Signals Module — Test Harness")
    print("=" * 60)
    print(f"  Base dir: {BASE_DIR}")
    print(f"  YouTube analysis: {YOUTUBE_ANALYSIS_PATH}")
    print(f"  Strategy eval: {STRATEGY_EVAL_PATH}")
    print(f"  Bridge state: {BRIDGE_STATE_PATH}")
    print(f"  Bridge module: {BRIDGE_PY_PATH}")

    # 1. Current Bias
    _print_section("1. get_krown_current_bias()", get_krown_current_bias())

    # 2. Strategy Map
    _print_section("2. get_krown_strategy_map()", get_krown_strategy_map())

    # 3. Indicator Settings
    _print_section("3. get_krown_indicator_settings()", get_krown_indicator_settings())

    # 4. Recent Signals
    _print_section("4. get_krown_recent_signals(days=30)", get_krown_recent_signals(days=30))

    # 5. Key Levels
    _print_section("5. get_krown_key_levels()", get_krown_key_levels())

    # 6. EMA Ribbon
    _print_section("6. get_krown_ema_ribbon()", get_krown_ema_ribbon())

    # 7. Revin Ribbons
    _print_section("7. get_krown_revin_ribbons_info()", get_krown_revin_ribbons_info())

    # 8. Three Drives
    _print_section("8. get_krown_three_drives_info()", get_krown_three_drives_info())

    # 9. Bridge State
    _print_section("9. get_bridge_state()", get_bridge_state())

    # 10. Strategy Evaluations
    _print_section("10. get_strategy_evaluations()", get_strategy_evaluations())

    # 11. Bridge Indicator Mapping
    _print_section("11. get_bridge_indicator_mapping()", get_bridge_indicator_mapping())

    # 12. Composite
    _print_section("12. get_all_krown_signals() [summary only]", {
        k: f"<{type(v).__name__}>" for k, v in get_all_krown_signals().items()
    })

    print(f"\n{'=' * 60}")
    print("  All tests complete.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
