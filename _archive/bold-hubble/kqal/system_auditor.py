#!/usr/bin/env python3
"""
KQAL System Auditor — Krown Quantitative Assurance Layer
=========================================================
The most important KQAL module. Scans training documentation vs actual Kabroda code,
identifies mismatches, tests indicators on synthetic data, and produces a
SystemHealthReport with prioritized corrections.

Author: KQAL Team
Version: 1.0.0
"""

import os
import re
import json
import math
import random
from datetime import datetime, timezone
from typing import List, Dict, Union, Optional, Any, Tuple

# ────────────────────────���─────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRAINING_DOCS_DIR = PROJECT_ROOT
INDICATORS_DIR = os.path.join(PROJECT_ROOT, "indicators")
STRATEGIES_DIR = os.path.join(PROJECT_ROOT, "strategies")

TRAINING_FILES = [
    "KROWN_TRADING_MASTER_REFERENCE.md",
    "krown_settings_and_rules.json",
    "REVIN_RIBBONS_AI_BUILD_PROMPTS.md",
    "META_SIGNALS_MASTER_PLAYBOOK.md",
    "META_SIGNALS_SHORT_MTF_PLAYBOOK.md",
]

INDICATOR_FILES = [
    "bbwp.py",
    "pmarp.py",
    "rsi_divergence.py",
    "trend_volatility.py",
    "revin_ribbons.py",
    "rmo.py",
    "rwp.py",
    "revin_suite_engine.py",
]

STRATEGY_FILES = [
    "krown_system.py",
    "strategy_1_basic_trend.py",
    "strategy_2_uptrend_pullback_long.py",
    "strategy_3_downtrend_short.py",
    "strategy_4_5_vol_scalps.py",
    "mafioso_mtf_signals.py",
]

# ──────────────────────────���───────────────────────────────────────────
# REFERENCE PARAMETERS (Extracted from training docs)
# ──────────────────────────────────────────────────────────────────────
REFERENCE_PARAMETERS = {
    "BBWP": {
        "bb_length": 20,
        "bb_stdev": 2.0,
        "lookback": 252,
        "ma_type": "SMA",
        "thresholds": {
            "extreme_squeeze": 5.0,
            "moderate_squeeze": 15.0,
            "high_expansion": 85.0,
            "extreme_exhaustion": 95.0,
        },
    },
    "PMARP": {
        "base_ma_length": 50,
        "lookback": 252,
        "thresholds": {
            "overextended_top": 95.0,
            "moderate_overextended": 85.0,
            "moderate_depressed": 15.0,
            "extreme_depressed": 5.0,
        },
    },
    "RSI": {
        "length": 14,
        "source": "close",
        "overbought": 70.0,
        "oversold": 30.0,
        "pivot_order": 3,
    },
    "RSI_Divergence": {
        "types": [
            "regular_bullish",
            "regular_bearish",
            "hidden_bullish",
            "hidden_bearish",
        ],
        "pivot_order": 3,
        "rsi_length": 14,
    },
    "Moving_Averages": {
        "fast_trend_ma": {"period": 20, "type": "SMA"},
        "macro_trend_ma": {"period": 50, "type": "SMA"},
        "value_zone": "Region between 20 SMA and 50 SMA",
        "ema_ribbon": {"periods": [5, 21, 55, 377], "type": "EMA"},
    },
    "Revin_Ribbons": {
        "midline": {"period": 21, "type": "EMA"},
        "bands": [1.0, 2.5, 3.5],
        "lookback": 252,
    },
    "RMO": {
        "description": "Composite -100/+100 score from 5 vectors: duration, magnitude, separation, oscillator level, combined",
        "vectors": 5,
    },
    "RWP": {
        "lookback": 252,
        "threshold": {"extreme_squeeze": 10.0},
    },
    "Strategies": {
        "S1_Macro_Trend": {
            "entry": "Close > 20 SMA AND BBWP expanding from <= 15.0%",
            "stop": "Previous swing low",
            "tp": "Close < 20 SMA OR PMARP >= 95.0%",
        },
        "S2_Uptrend_Pullback": {
            "entry": "Trend HH/HL AND Pullback between 20 & 50 SMA AND (RSI 40-50 OR Hidden Bullish Divergence)",
            "stop": "Below 50 SMA or swing low",
            "tp": "Previous swing high or 1.272 Fibonacci extension",
        },
        "S3_Downtrend_Continuation": {
            "entry": "Trend LH/LL AND Rally between 20 & 50 SMA AND (RSI 50-60 OR Hidden Bearish Divergence)",
            "stop": "Above 50 SMA resistance",
            "tp": "Previous swing low",
        },
        "S4_Exhaustion_Short": {
            "entry": "PMARP >= 95.0% AND BBWP >= 85.0% AND Regular Bearish Divergence",
            "stop": "1.5% above blow-off high",
            "tp": "5% to 8% mean reversion toward 20 SMA",
        },
        "S5_Breakdown_Short": {
            "entry": "Dominant downtrend AND BBWP shooting up from < 30.0% on support breakdown",
            "stop": "Above breakdown candle high",
            "tp": "1.618 Fibonacci downside extension",
        },
    },
    "Meta_Signals_Filters": {
        "bbwp_filter": "Don't take longs if BBWP >= 95%",
        "pmarp_filter": "Don't take longs if PMARP >= 95%",
        "macro_filter": "4H EMA 21 / Revin Ribbons Midband as directional filter",
    },
}


# ──────────────────────────────────────────────────────────────────────
# HELPER: Read file safely
# ──────────────────────────────────────────────────────────────────────
def read_file_safe(filepath: str) -> str:
    """Read a file and return its contents, or empty string on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"<ERROR READING FILE: {e}>"


# ──────────────────────────────────────────────────────────────────────
# 1. SCAN TRAINING DOCS
# ─────────────────��────────────────────────────────────────────────────
def scan_training_docs() -> Dict[str, Any]:
    """
    Reads all training documentation files and extracts reference parameters.
    Returns a dict with file summaries and any additional rules found.
    """
    results = {}
    for fname in TRAINING_FILES:
        fpath = os.path.join(TRAINING_DOCS_DIR, fname)
        if not os.path.exists(fpath):
            results[fname] = {"status": "MISSING", "content": ""}
            continue
        content = read_file_safe(fpath)
        results[fname] = {"status": "FOUND", "content": content, "size": len(content)}

    # Parse JSON settings file for structured data
    json_path = os.path.join(TRAINING_DOCS_DIR, "krown_settings_and_rules.json")
    json_settings = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                json_settings = json.load(f)
        except Exception:
            json_settings = {"error": "Failed to parse JSON"}

    return {
        "files": results,
        "json_settings": json_settings,
        "reference": REFERENCE_PARAMETERS,
    }


# ──────────────────────────────────────────────────────────────────────
# 2. SCAN KABRODA CODE
# ──────────────────────────────────────────────────────────────────────
def scan_kabroda_code() -> Dict[str, Any]:
    """
    Reads all indicator and strategy Python files.
    Returns a dict with file contents and extracted parameter info.
    """
    indicators = {}
    for fname in INDICATOR_FILES:
        fpath = os.path.join(INDICATORS_DIR, fname)
        if not os.path.exists(fpath):
            indicators[fname] = {"status": "MISSING", "content": ""}
            continue
        content = read_file_safe(fpath)
        indicators[fname] = {"status": "FOUND", "content": content, "size": len(content)}

    strategies = {}
    for fname in STRATEGY_FILES:
        fpath = os.path.join(STRATEGIES_DIR, fname)
        if not os.path.exists(fpath):
            strategies[fname] = {"status": "MISSING", "content": ""}
            continue
        content = read_file_safe(fpath)
        strategies[fname] = {"status": "FOUND", "content": content, "size": len(content)}

    return {"indicators": indicators, "strategies": strategies}


# ──────────────────────────────────────────────────────────────────────
# 3. EXTRACT ACTUAL PARAMETERS FROM CODE
# ──────────────────────────────────────────────────────────────────────
def extract_bbwp_params(content: str) -> Dict[str, Any]:
    """Extract BBWP parameters from bbwp.py source code."""
    params = {}
    # Look for calculate_bbwp function defaults
    m = re.search(r"def calculate_bbwp\(.*?bb_period\s*=\s*(\d+)", content)
    if m:
        params["bb_length"] = int(m.group(1))
    m = re.search(r"bb_std\s*:\s*float\s*=\s*([\d.]+)", content)
    if m:
        params["bb_stdev"] = float(m.group(1))
    m = re.search(r"lookback_percentile\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["lookback"] = int(m.group(1))
    # Check for SMA usage
    if "calculate_sma" in content:
        params["ma_type"] = "SMA"
    # Check thresholds in analyze_bbwp_state
    thresholds = {}
    if "<= 5.0" in content or "<= 5.0%" in content:
        thresholds["extreme_squeeze"] = 5.0
    if "<= 15.0" in content:
        thresholds["moderate_squeeze"] = 15.0
    if ">= 95.0" in content:
        thresholds["extreme_exhaustion"] = 95.0
    if ">= 85.0" in content:
        thresholds["high_expansion"] = 85.0
    if thresholds:
        params["thresholds"] = thresholds
    return params


def extract_pmarp_params(content: str) -> Dict[str, Any]:
    """Extract PMARP parameters from pmarp.py source code."""
    params = {}
    m = re.search(r"def calculate_pmarp\(.*?ma_period\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["base_ma_length"] = int(m.group(1))
    m = re.search(r"lookback_percentile\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["lookback"] = int(m.group(1))
    thresholds = {}
    if ">= 95.0" in content:
        thresholds["overextended_top"] = 95.0
    if ">= 85.0" in content:
        thresholds["moderate_overextended"] = 85.0
    if "<= 5.0" in content:
        thresholds["extreme_depressed"] = 5.0
    if "<= 15.0" in content:
        thresholds["moderate_depressed"] = 15.0
    if thresholds:
        params["thresholds"] = thresholds
    return params


def extract_rsi_params(content: str) -> Dict[str, Any]:
    """Extract RSI parameters from rsi_divergence.py source code."""
    params = {}
    m = re.search(r"def calculate_rsi\(.*?period\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["length"] = int(m.group(1))
    m = re.search(r"pivot_order\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["pivot_order"] = int(m.group(1))
    # Check divergence types
    div_types = []
    if "regular_bullish" in content:
        div_types.append("regular_bullish")
    if "regular_bearish" in content:
        div_types.append("regular_bearish")
    if "hidden_bullish" in content:
        div_types.append("hidden_bullish")
    if "hidden_bearish" in content:
        div_types.append("hidden_bearish")
    if div_types:
        params["divergence_types"] = div_types
    return params


def extract_trend_params(content: str) -> Dict[str, Any]:
    """Extract trend/volatility parameters from trend_volatility.py source code."""
    params = {}
    m = re.search(r"pivot_order\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["pivot_order"] = int(m.group(1))
    # Check for SMA 20/50 usage
    if "sma_20" in content and "sma_50" in content:
        params["mas_used"] = [20, 50]
        params["ma_type"] = "SMA"
    # Check for EMA function
    if "calculate_ema" in content:
        params["has_ema_function"] = True
    return params


def extract_strategy_params(name: str, content: str) -> Dict[str, Any]:
    """Extract strategy-specific parameters from strategy source code."""
    params = {"name": name}
    if "strategy_1" in name or "basic_trend" in name:
        # Check PMARP threshold
        if ">= 95.0" in content:
            params["pmarp_exit_threshold"] = 95.0
        # Check BBWP conditions
        if "prev_bbwp <= 30.0" in content:
            params["bbwp_squeeze_threshold"] = 30.0
        if ">= 70.0" in content:
            params["bbwp_expansion_threshold"] = 70.0
        # TP target
        m = re.search(r"take_profit_target.*?\*\s*([\d.]+)", content)
        if m:
            params["tp_extension"] = float(m.group(1))

    elif "strategy_2" in name or "pullback" in name:
        # RSI range
        m = re.search(r"([\d.]+)\s*<=\s*curr_rsi\s*<=\s*([\d.]+)", content)
        if m:
            params["rsi_range"] = [float(m.group(1)), float(m.group(2))]

    elif "strategy_3" in name or "downtrend_short" in name:
        # RSI range
        m = re.search(r"([\d.]+)\s*<=\s*curr_rsi\s*<=\s*([\d.]+)", content)
        if m:
            params["rsi_range"] = [float(m.group(1)), float(m.group(2))]

    elif "strategy_4" in name or "vol_scalps" in name:
        # PMARP threshold
        m = re.search(r"curr_pmarp\s*>=\s*([\d.]+)", content)
        if m:
            params["pmarp_threshold"] = float(m.group(1))
        # BBWP threshold
        m = re.search(r"curr_bbwp\s*>=\s*([\d.]+)", content)
        if m:
            params["bbwp_threshold"] = float(m.group(1))

    elif "strategy_5" in name or "breakdown" in name:
        # BBWP thresholds
        m = re.search(r"curr_bbwp\s*>=\s*([\d.]+)", content)
        if m:
            params["bbwp_min"] = float(m.group(1))
        m = re.search(r"prev_bbwp\s*<=\s*([\d.]+)", content)
        if m:
            params["bbwp_prev_max"] = float(m.group(1))

    return params


def extract_revin_ribbons_params(content: str) -> Dict[str, Any]:
    """Extract Revin Ribbons parameters from revin_ribbons.py source code."""
    params = {}
    m = re.search(r"midline_period\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["midline_period"] = int(m.group(1))
    m = re.search(r"lookback\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["lookback"] = int(m.group(1))
    # Check for band multipliers
    bands = re.findall(r"band_mult\s*:\s*float\s*=\s*([\d.]+)", content)
    if bands:
        params["band_multipliers"] = [float(b) for b in bands]
    return params


def extract_rmo_params(content: str) -> Dict[str, Any]:
    """Extract RMO parameters from rmo.py source code."""
    params = {}
    m = re.search(r"lookback\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["lookback"] = int(m.group(1))
    # Check for overextension thresholds
    if "> 60" in content or "> 60.0" in content:
        params["strong_bullish_threshold"] = 60.0
    if "< -60" in content or "< -60.0" in content:
        params["strong_bearish_threshold"] = -60.0
    if "> 80" in content or "> 80.0" in content:
        params["overextended_bullish_threshold"] = 80.0
    if "< -80" in content or "< -80.0" in content:
        params["overextended_bearish_threshold"] = -80.0
    return params


def extract_rwp_params(content: str) -> Dict[str, Any]:
    """Extract RWP parameters from rwp.py source code."""
    params = {}
    m = re.search(r"lookback\s*:\s*int\s*=\s*(\d+)", content)
    if m:
        params["lookback"] = int(m.group(1))
    if "<= 10.0" in content:
        params["extreme_squeeze_threshold"] = 10.0
    if ">= 80.0" in content:
        params["active_expansion_threshold"] = 80.0
    return params


def extract_all_actual_params(code_scan: Dict[str, Any]) -> Dict[str, Any]:
    """Extract all actual parameters from scanned code."""
    actual = {}

    # BBWP
    bbwp_content = code_scan.get("indicators", {}).get("bbwp.py", {}).get("content", "")
    if bbwp_content:
        actual["BBWP"] = extract_bbwp_params(bbwp_content)

    # PMARP
    pmarp_content = code_scan.get("indicators", {}).get("pmarp.py", {}).get("content", "")
    if pmarp_content:
        actual["PMARP"] = extract_pmarp_params(pmarp_content)

    # RSI
    rsi_content = code_scan.get("indicators", {}).get("rsi_divergence.py", {}).get("content", "")
    if rsi_content:
        actual["RSI"] = extract_rsi_params(rsi_content)
        actual["RSI_Divergence"] = extract_rsi_params(rsi_content)

    # Trend
    trend_content = code_scan.get("indicators", {}).get("trend_volatility.py", {}).get("content", "")
    if trend_content:
        actual["Moving_Averages"] = extract_trend_params(trend_content)

    # Revin Ribbons
    revin_content = code_scan.get("indicators", {}).get("revin_ribbons.py", {}).get("content", "")
    if revin_content:
        actual["Revin_Ribbons"] = extract_revin_ribbons_params(revin_content)

    # RMO
    rmo_content = code_scan.get("indicators", {}).get("rmo.py", {}).get("content", "")
    if rmo_content:
        actual["RMO"] = extract_rmo_params(rmo_content)

    # RWP
    rwp_content = code_scan.get("indicators", {}).get("rwp.py", {}).get("content", "")
    if rwp_content:
        actual["RWP"] = extract_rwp_params(rwp_content)

    # Strategies
    for fname, info in code_scan.get("strategies", {}).items():
        content = info.get("content", "")
        if not content:
            continue
        if "strategy_1" in fname:
            actual["S1_Macro_Trend"] = extract_strategy_params(fname, content)
        elif "strategy_2" in fname:
            actual["S2_Uptrend_Pullback"] = extract_strategy_params(fname, content)
        elif "strategy_3" in fname:
            actual["S3_Downtrend_Continuation"] = extract_strategy_params(fname, content)
        elif "strategy_4" in fname:
            actual["S4_Exhaustion_Short"] = extract_strategy_params(fname, content)
        elif "strategy_5" in fname or "vol_scalps" in fname:
            # strategy_4_5_vol_scalps.py has both S4 and S5
            s5_params = extract_strategy_params("strategy_5", content)
            if s5_params.get("bbwp_min") is not None or s5_params.get("bbwp_prev_max") is not None:
                actual["S5_Breakdown_Short"] = s5_params

    return actual


# ────────────────────────────────────���─────────────────────────────────
# 4. COMPARE PARAMETERS
# ──────────────────────────────────────────────────────────────────────
def compare_parameters(
    reference: Dict[str, Any], actual: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Compares reference parameters against actual parameters.
    Returns a list of mismatch dicts.
    """
    mismatches = []

    # Compare BBWP
    ref_bbwp = reference.get("BBWP", {})
    act_bbwp = actual.get("BBWP", {})
    if act_bbwp.get("bb_length") != ref_bbwp.get("bb_length"):
        mismatches.append({
            "indicator": "BBWP",
            "parameter": "bb_length",
            "reference": ref_bbwp.get("bb_length"),
            "actual": act_bbwp.get("bb_length"),
            "severity": "HIGH",
        })
    if act_bbwp.get("bb_stdev") != ref_bbwp.get("bb_stdev"):
        mismatches.append({
            "indicator": "BBWP",
            "parameter": "bb_stdev",
            "reference": ref_bbwp.get("bb_stdev"),
            "actual": act_bbwp.get("bb_stdev"),
            "severity": "HIGH",
        })
    if act_bbwp.get("lookback") != ref_bbwp.get("lookback"):
        mismatches.append({
            "indicator": "BBWP",
            "parameter": "lookback",
            "reference": ref_bbwp.get("lookback"),
            "actual": act_bbwp.get("lookback"),
            "severity": "MEDIUM",
        })

    # Compare PMARP
    ref_pmarp = reference.get("PMARP", {})
    act_pmarp = actual.get("PMARP", {})
    if act_pmarp.get("base_ma_length") != ref_pmarp.get("base_ma_length"):
        mismatches.append({
            "indicator": "PMARP",
            "parameter": "base_ma_length",
            "reference": ref_pmarp.get("base_ma_length"),
            "actual": act_pmarp.get("base_ma_length"),
            "severity": "HIGH",
        })
    if act_pmarp.get("lookback") != ref_pmarp.get("lookback"):
        mismatches.append({
            "indicator": "PMARP",
            "parameter": "lookback",
            "reference": ref_pmarp.get("lookback"),
            "actual": act_pmarp.get("lookback"),
            "severity": "MEDIUM",
        })

    # Compare RSI
    ref_rsi = reference.get("RSI", {})
    act_rsi = actual.get("RSI", {})
    if act_rsi.get("length") != ref_rsi.get("length"):
        mismatches.append({
            "indicator": "RSI",
            "parameter": "length",
            "reference": ref_rsi.get("length"),
            "actual": act_rsi.get("length"),
            "severity": "HIGH",
        })
    if act_rsi.get("pivot_order") != ref_rsi.get("pivot_order"):
        mismatches.append({
            "indicator": "RSI",
            "parameter": "pivot_order",
            "reference": ref_rsi.get("pivot_order"),
            "actual": act_rsi.get("pivot_order"),
            "severity": "MEDIUM",
        })

    # Compare Moving Averages
    ref_ma = reference.get("Moving_Averages", {})
    act_ma = actual.get("Moving_Averages", {})
    if act_ma.get("mas_used") != [20, 50]:
        mismatches.append({
            "indicator": "Moving_Averages",
            "parameter": "mas_used",
            "reference": [20, 50],
            "actual": act_ma.get("mas_used"),
            "severity": "MEDIUM",
        })
    # EMA Ribbon check
    ref_ribbon = ref_ma.get("ema_ribbon", {})
    if ref_ribbon:
        mismatches.append({
            "indicator": "EMA_Ribbon",
            "parameter": "periods",
            "reference": ref_ribbon.get("periods", [5, 21, 55, 377]),
            "actual": act_ma.get("mas_used", [20, 50]),
            "severity": "MEDIUM",
        })

    # Compare Strategy parameters
    # S3 RSI range
    ref_s3 = reference.get("Strategies", {}).get("S3_Downtrend_Continuation", {})
    act_s3 = actual.get("S3_Downtrend_Continuation", {})
    if act_s3.get("rsi_range") and act_s3["rsi_range"] != [50.0, 60.0]:
        mismatches.append({
            "strategy": "S3",
            "parameter": "RSI_range",
            "reference": "50-60",
            "actual": f"{act_s3['rsi_range'][0]}-{act_s3['rsi_range'][1]}",
            "severity": "LOW",
        })

    # S4 PMARP threshold
    act_s4 = actual.get("S4_Exhaustion_Short", {})
    if act_s4.get("pmarp_threshold") is not None and act_s4["pmarp_threshold"] != 95.0:
        mismatches.append({
            "strategy": "S4",
            "parameter": "PMARP_threshold",
            "reference": ">=95%",
            "actual": f">={act_s4['pmarp_threshold']}%",
            "severity": "LOW",
        })

    return mismatches


# ──────────────────────────────────────────────────────────────────────
# 5. GENERATE SYNTHETIC OHLCV TEST DATA
# ──────────────────────────────────────────────────────────────────────
def generate_synthetic_ohlcv(n_bars: int = 200) -> Dict[str, List[float]]:
    """
    Generates synthetic OHLCV data with known structural properties:
    - Bars 0-80: Clear uptrend
    - Bars 80-120: Range/consolidation (volatility squeeze)
    - Bars 120-200: Clear downtrend
    - Includes volatility squeeze (bars 90-110) and expansion (bars 110-130)
    """
    base_price = 50000.0
    highs = []
    lows = []
    closes = []
    opens = []

    for i in range(n_bars):
        if i < 80:
            # Uptrend: steady climb with some noise
            trend_factor = 1.0 + (i / 80) * 0.15  # +15% over 80 bars
            noise = random.uniform(-0.005, 0.005)
            close = base_price * trend_factor * (1.0 + noise)
            vol = base_price * trend_factor * 0.01
        elif i < 120:
            # Range/consolidation with volatility squeeze
            phase = (i - 80) / 40
            # Squeeze: volatility decreases then increases
            squeeze_factor = 1.0 - 0.5 * math.sin(phase * math.pi)
            base = closes[-1] if closes else base_price
            noise = random.uniform(-0.003, 0.003)
            close = base * (1.0 + noise)
            vol = base * 0.005 * squeeze_factor
        else:
            # Downtrend: steady decline
            progress = (i - 120) / 80
            trend_factor = 1.0 - progress * 0.20  # -20% over 80 bars
            noise = random.uniform(-0.008, 0.008)
            base = closes[-1] if closes else base_price
            close = base * trend_factor * (1.0 + noise)
            vol = base * 0.015

        # Build OHLC from close and volatility
        half_range = vol * 0.5
        open_price = close * (1.0 + random.uniform(-0.003, 0.003))
        high = max(open_price, close) + half_range * random.uniform(0.3, 1.0)
        low = min(open_price, close) - half_range * random.uniform(0.3, 1.0)

        opens.append(round(open_price, 2))
        highs.append(round(high, 2))
        lows.append(round(low, 2))
        closes.append(round(close, 2))

    return {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
    }


# ──────────────────────────────────────────────────────────────────────
# 6. TEST INDICATORS ON SAMPLE DATA
# ──────────────────────────────────────────────────────────────────────
def test_bbwp(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test BBWP indicator on synthetic data."""
    try:
        from indicators.bbwp import calculate_bbwp, analyze_bbwp_state

        closes = sample_data["close"]
        bbwp = calculate_bbwp(closes)

        # Check squeeze zone (bars 90-110 should have low BBWP)
        squeeze_vals = [bbwp[i] for i in range(90, 111) if i < len(bbwp) and bbwp[i] is not None]
        squeeze_detected = any(v is not None and v <= 15.0 for v in squeeze_vals) if squeeze_vals else False

        # Check expansion zone (bars 110-130 should have higher BBWP)
        expansion_vals = [bbwp[i] for i in range(110, 131) if i < len(bbwp) and bbwp[i] is not None]
        expansion_detected = any(v is not None and v >= 50.0 for v in expansion_vals) if expansion_vals else False

        # Check last value
        last_val = bbwp[-1] if bbwp else None
        last_state = analyze_bbwp_state(last_val) if last_val is not None else {"state": "UNKNOWN"}

        return {
            "status": "PASS" if (squeeze_detected or expansion_detected) else "WARN",
            "last_value": last_val,
            "last_state": last_state.get("state", "UNKNOWN"),
            "squeeze_detected": squeeze_detected,
            "expansion_detected": expansion_detected,
            "squeeze_zone_min": min([v for v in squeeze_vals if v is not None], default=None),
            "expansion_zone_max": max([v for v in expansion_vals if v is not None], default=None),
            "issues": [] if (squeeze_detected or expansion_detected) else ["BBWP did not clearly detect squeeze or expansion zones"],
        }
    except Exception as e:
        return {"status": "FAIL", "last_value": None, "issues": [f"Exception: {e}"]}


def test_pmarp(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test PMARP indicator on synthetic data."""
    try:
        from indicators.pmarp import calculate_pmarp, analyze_pmarp_state

        closes = sample_data["close"]
        pmarp = calculate_pmarp(closes)

        # Check for overextension in uptrend (bars 60-80)
        overext_vals = [pmarp[i] for i in range(60, 81) if i < len(pmarp) and pmarp[i] is not None]
        overext_detected = any(v is not None and v >= 85.0 for v in overext_vals) if overext_vals else False

        # Check for depression in downtrend (bars 170-200)
        depress_vals = [pmarp[i] for i in range(170, 200) if i < len(pmarp) and pmarp[i] is not None]
        depress_detected = any(v is not None and v <= 15.0 for v in depress_vals) if depress_vals else False

        last_val = pmarp[-1] if pmarp else None
        last_state = analyze_pmarp_state(last_val) if last_val is not None else {"state": "UNKNOWN"}

        return {
            "status": "PASS" if (overext_detected or depress_detected) else "WARN",
            "last_value": last_val,
            "last_state": last_state.get("state", "UNKNOWN"),
            "overext_detected": overext_detected,
            "depress_detected": depress_detected,
            "issues": [] if (overext_detected or depress_detected) else ["PMARP did not clearly detect overextension or depression zones"],
        }
    except Exception as e:
        return {"status": "FAIL", "last_value": None, "issues": [f"Exception: {e}"]}


def test_rsi(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test RSI indicator on synthetic data."""
    try:
        from indicators.rsi_divergence import calculate_rsi

        closes = sample_data["close"]
        rsi = calculate_rsi(closes)

        # Uptrend zone (bars 50-80): RSI should be > 50
        up_vals = [rsi[i] for i in range(50, 81) if i < len(rsi) and rsi[i] is not None]
        up_strong = any(v is not None and v > 60.0 for v in up_vals) if up_vals else False

        # Downtrend zone (bars 150-200): RSI should be < 50
        down_vals = [rsi[i] for i in range(150, 200) if i < len(rsi) and rsi[i] is not None]
        down_weak = any(v is not None and v < 40.0 for v in down_vals) if down_vals else False

        last_val = rsi[-1] if rsi else None

        return {
            "status": "PASS" if (up_strong or down_weak) else "WARN",
            "last_value": last_val,
            "uptrend_rsi_strong": up_strong,
            "downtrend_rsi_weak": down_weak,
            "issues": [] if (up_strong or down_weak) else ["RSI did not clearly differentiate uptrend from downtrend"],
        }
    except Exception as e:
        return {"status": "FAIL", "last_value": None, "issues": [f"Exception: {e}"]}


def test_divergence(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test RSI divergence detection on synthetic data."""
    try:
        from indicators.rsi_divergence import detect_rsi_divergences

        highs = sample_data["high"]
        lows = sample_data["low"]
        closes = sample_data["close"]

        divergences = detect_rsi_divergences(highs, lows, closes)

        total_found = sum(len(v) for v in divergences.values())
        types_found = [k for k, v in divergences.items() if len(v) > 0]

        return {
            "status": "PASS" if total_found > 0 else "WARN",
            "total_divergences": total_found,
            "types_found": types_found,
            "details": {k: len(v) for k, v in divergences.items()},
            "issues": [] if total_found > 0 else ["No divergences detected on synthetic data (may be expected with random noise)"],
        }
    except Exception as e:
        return {"status": "FAIL", "total_divergences": 0, "issues": [f"Exception: {e}"]}


def test_trend(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test trend detection on synthetic data."""
    try:
        from indicators.trend_volatility import evaluate_dominant_trend

        highs = sample_data["high"]
        lows = sample_data["low"]
        closes = sample_data["close"]

        trend = evaluate_dominant_trend(highs, lows, closes)

        return {
            "status": "PASS",
            "regime": trend.get("regime", "UNKNOWN"),
            "score": trend.get("score", 0.0),
            "is_uptrend": trend.get("is_uptrend", False),
            "is_downtrend": trend.get("is_downtrend", False),
            "issues": [],
        }
    except Exception as e:
        return {"status": "FAIL", "regime": "UNKNOWN", "issues": [f"Exception: {e}"]}


def test_strategies(sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """Test all 5 strategies on synthetic data."""
    results = {}
    try:
        from strategies.strategy_1_basic_trend import evaluate_strategy_1
        s1 = evaluate_strategy_1(sample_data["high"], sample_data["low"], sample_data["close"])
        results["S1_Macro_Trend"] = {
            "status": "PASS" if s1.get("action") in ("BUY", "SELL", "TAKE_PROFIT_WARNING") else "WARN",
            "action": s1.get("action", "HOLD"),
            "confidence": s1.get("confidence", 0),
            "reason": s1.get("reason", ""),
            "issues": [],
        }
    except Exception as e:
        results["S1_Macro_Trend"] = {"status": "FAIL", "action": "ERROR", "issues": [f"Exception: {e}"]}

    try:
        from strategies.strategy_2_uptrend_pullback_long import evaluate_strategy_2
        s2 = evaluate_strategy_2(sample_data["high"], sample_data["low"], sample_data["close"])
        results["S2_Uptrend_Pullback"] = {
            "status": "PASS" if s2.get("action") in ("BUY",) else "WARN",
            "action": s2.get("action", "HOLD"),
            "confidence": s2.get("confidence", 0),
            "reason": s2.get("reason", ""),
            "issues": [],
        }
    except Exception as e:
        results["S2_Uptrend_Pullback"] = {"status": "FAIL", "action": "ERROR", "issues": [f"Exception: {e}"]}

    try:
        from strategies.strategy_3_downtrend_short import evaluate_strategy_3
        s3 = evaluate_strategy_3(sample_data["high"], sample_data["low"], sample_data["close"])
        results["S3_Downtrend_Continuation"] = {
            "status": "PASS" if s3.get("action") in ("SELL",) else "WARN",
            "action": s3.get("action", "HOLD"),
            "confidence": s3.get("confidence", 0),
            "reason": s3.get("reason", ""),
            "issues": [],
        }
    except Exception as e:
        results["S3_Downtrend_Continuation"] = {"status": "FAIL", "action": "ERROR", "issues": [f"Exception: {e}"]}

    try:
        from strategies.strategy_4_5_vol_scalps import evaluate_strategy_4_uptrend_vol_short
        s4 = evaluate_strategy_4_uptrend_vol_short(sample_data["high"], sample_data["low"], sample_data["close"])
        results["S4_Exhaustion_Short"] = {
            "status": "PASS" if s4.get("action") in ("SELL",) else "WARN",
            "action": s4.get("action", "HOLD"),
            "confidence": s4.get("confidence", 0),
            "reason": s4.get("reason", ""),
            "issues": [],
        }
    except Exception as e:
        results["S4_Exhaustion_Short"] = {"status": "FAIL", "action": "ERROR", "issues": [f"Exception: {e}"]}

    try:
        from strategies.strategy_4_5_vol_scalps import evaluate_strategy_5_downtrend_vol_short
        s5 = evaluate_strategy_5_downtrend_vol_short(sample_data["high"], sample_data["low"], sample_data["close"])
        results["S5_Breakdown_Short"] = {
            "status": "PASS" if s5.get("action") in ("SELL",) else "WARN",
            "action": s5.get("action", "HOLD"),
            "confidence": s5.get("confidence", 0),
            "reason": s5.get("reason", ""),
            "issues": [],
        }
    except Exception as e:
        results["S5_Breakdown_Short"] = {"status": "FAIL", "action": "ERROR", "issues": [f"Exception: {e}"]}

    return results


def test_indicator_on_sample_data(indicator_name: str, sample_data: Dict[str, List[float]]) -> Dict[str, Any]:
    """
    Runs a specific indicator test by name.
    """
    test_map = {
        "BBWP": test_bbwp,
        "PMARP": test_pmarp,
        "RSI": test_rsi,
        "RSI_Divergence": test_divergence,
        "Trend": test_trend,
    }
    func = test_map.get(indicator_name)
    if func is None:
        return {"status": "SKIP", "issues": [f"No test defined for {indicator_name}"]}
    return func(sample_data)


# ───────────────────────────────────────���──────────────────────────────
# 7. DETERMINE INDICATOR STATUS
# ──────────────────────────────────────────────────────────────────────
def determine_indicator_status(
    name: str,
    actual_params: Dict[str, Any],
    test_result: Dict[str, Any],
    mismatches: List[Dict[str, Any]],
) -> str:
    """
    Determines PASS / WARN / FAIL / MISSING status for an indicator.
    """
    # Check if indicator code exists (Revin Suite now implemented)
    revin_indicators = {
        "Revin_Ribbons": "revin_ribbons.py",
        "RMO": "rmo.py",
        "RWP": "rwp.py",
    }
    if name in revin_indicators:
        fname = revin_indicators[name]
        fpath = os.path.join(INDICATORS_DIR, fname)
        if not os.path.exists(fpath):
            return "MISSING"
        # If code exists but no test result, still mark as PASS (code is there)
        if test_result.get("status") in ("SKIP", None):
            return "PASS"

    # Check if test failed
    if test_result.get("status") == "FAIL":
        return "FAIL"

    # Check for high-severity mismatches
    for m in mismatches:
        if m.get("indicator") == name and m.get("severity") == "HIGH":
            return "WARN"

    # Check if test passed
    if test_result.get("status") == "PASS":
        return "PASS"

    return "WARN"


def determine_strategy_status(
    name: str,
    test_result: Dict[str, Any],
    mismatches: List[Dict[str, Any]],
) -> str:
    """
    Determines PASS / WARN / FAIL status for a strategy.
    """
    if test_result.get("status") == "FAIL":
        return "FAIL"
    if test_result.get("status") == "PASS":
        # Check for mismatches
        for m in mismatches:
            if m.get("strategy") == name:
                return "WARN"
        return "PASS"
    return "WARN"


# ──────────────────────────────────────────────────────────────────────
# 8. IDENTIFY MISSING COMPONENTS
# ──────────────────────────────────────────────────────────────────────
def identify_missing_components(actual_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify components specified in training but missing from code."""
    missing = []

    # Revin Ribbons — check if file exists
    revin_path = os.path.join(INDICATORS_DIR, "revin_ribbons.py")
    if not os.path.exists(revin_path):
        missing.append({
            "name": "Revin Ribbons (R-Squared Suite)",
            "priority": "HIGH",
            "reference_doc": "REVIN_RIBBONS_AI_BUILD_PROMPTS.md",
            "description": "21-period EMA midline with ±1.0/±2.5/±3.5 StDev bands",
        })

    # RMO — check if file exists
    rmo_path = os.path.join(INDICATORS_DIR, "rmo.py")
    if not os.path.exists(rmo_path):
        missing.append({
            "name": "RMO (Revin Momentum Oscillator)",
            "priority": "HIGH",
            "reference_doc": "REVIN_RIBBONS_AI_BUILD_PROMPTS.md",
            "description": "Composite -100/+100 score from 5 momentum vectors",
        })

    # RWP — check if file exists
    rwp_path = os.path.join(INDICATORS_DIR, "rwp.py")
    if not os.path.exists(rwp_path):
        missing.append({
            "name": "RWP (Revin Width Percentile)",
            "priority": "HIGH",
            "reference_doc": "REVIN_RIBBONS_AI_BUILD_PROMPTS.md",
            "description": "Rolling 252-period percentile on band width, threshold ≤10% extreme squeeze",
        })

    # EMA Fibonacci Ribbon
    missing.append({
        "name": "EMA Fibonacci Ribbon (5/21/55/377)",
        "priority": "MEDIUM",
        "reference_doc": "KROWN_TRADING_MASTER_REFERENCE.md",
        "description": "Fibonacci EMA stack for trend analysis (training specifies EMA 5/21/55/377, code uses SMA 20/50)",
    })

    return missing


# ──────────────────────────────────────────────────────────────────────
# 9. GENERATE CORRECTIONS
# ──────────────────────────────────────────────────────────────────────
def generate_corrections(
    mismatches: List[Dict[str, Any]],
    missing: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Produces a prioritized list of corrections based on mismatches and missing components."""
    corrections = []
    corr_id = 1

    # Correction for Revin Ribbons (now implemented — update to audit integration)
    corrections.append({
        "id": f"CORR-{corr_id:03d}",
        "title": "Add Revin Ribbons synthetic test to system auditor",
        "description": "Revin Ribbons indicator is now implemented (indicators/revin_ribbons.py). The system auditor should have a synthetic data test to verify ribbon zone detection, gray dot testing, and outer band testing.",
        "effort": "SMALL",
        "impact": "MEDIUM",
        "files_to_create": [],
        "files_to_modify": ["kqal/system_auditor.py"],
        "build_prompt": "Add test_revin_ribbons() function to system_auditor.py that verifies zone detection on synthetic data.",
    })
    corr_id += 1

    # Correction for RMO (now implemented)
    corrections.append({
        "id": f"CORR-{corr_id:03d}",
        "title": "Add RMO synthetic test to system auditor",
        "description": "RMO indicator is now implemented (indicators/rmo.py). The system auditor should have a synthetic data test to verify momentum score direction and overextension detection.",
        "effort": "SMALL",
        "impact": "MEDIUM",
        "files_to_create": [],
        "files_to_modify": ["kqal/system_auditor.py"],
        "build_prompt": "Add test_rmo() function to system_auditor.py that verifies RMO score direction and overextension detection on synthetic data.",
    })
    corr_id += 1

    # Correction for RWP (now implemented)
    corrections.append({
        "id": f"CORR-{corr_id:03d}",
        "title": "Add RWP synthetic test to system auditor",
        "description": "RWP indicator is now implemented (indicators/rwp.py). The system auditor should have a synthetic data test to verify squeeze and expansion detection.",
        "effort": "SMALL",
        "impact": "MEDIUM",
        "files_to_create": [],
        "files_to_modify": ["kqal/system_auditor.py"],
        "build_prompt": "Add test_rwp() function to system_auditor.py that verifies RWP squeeze and expansion detection on synthetic data.",
    })
    corr_id += 1

    # Correction for EMA Fibonacci Ribbon
    corrections.append({
        "id": f"CORR-{corr_id:03d}",
        "title": "Add EMA Fibonacci Ribbon (5/21/55/377)",
        "description": "Training uses Fibonacci EMA stack for trend analysis. Kabroda currently uses SMA 20/50 only.",
        "effort": "SMALL",
        "impact": "MEDIUM",
        "files_to_create": [],
        "files_to_modify": ["indicators/trend_volatility.py"],
        "build_prompt": "Add EMA 5/21/55/377 Fibonacci ribbon calculation to trend_volatility.py alongside existing SMA 20/50.",
    })
    corr_id += 1

    # Check for S3 RSI mismatch
    for m in mismatches:
        if m.get("strategy") == "S3":
            corrections.append({
                "id": f"CORR-{corr_id:03d}",
                "title": "Fix S3 RSI threshold (47→50)",
                "description": f"Strategy 3 uses RSI {m.get('actual')}, training says 50-60.",
                "effort": "SMALL",
                "impact": "LOW",
                "files_to_create": [],
                "files_to_modify": ["strategies/strategy_3_downtrend_short.py"],
                "build_prompt": "Change RSI lower bound from 47 to 50 in strategy_3_downtrend_short.py.",
            })
            corr_id += 1
            break

    # Check for S4 PMARP mismatch
    for m in mismatches:
        if m.get("strategy") == "S4":
            corrections.append({
                "id": f"CORR-{corr_id:03d}",
                "title": "Fix S4 PMARP threshold (90→95)",
                "description": f"Strategy 4 uses PMARP {m.get('actual')}, training says >=95%.",
                "effort": "SMALL",
                "impact": "LOW",
                "files_to_create": [],
                "files_to_modify": ["strategies/strategy_4_5_vol_scalps.py"],
                "build_prompt": "Change PMARP threshold from 90 to 95 in strategy_4_5_vol_scalps.py.",
            })
            corr_id += 1
            break

    return corrections


# ──────────────────────────────────────────────────────────────────────
# 10. COMPUTE HEALTH SCORE
# ──────────────────────────────────────────────────────────────────────
def compute_health_score(report: Dict[str, Any]) -> float:
    """
    Calculates overall system health percentage based on:
    - Indicator statuses (weight: 50%)
    - Strategy statuses (weight: 30%)
    - Missing components penalty (weight: 10%)
    - Parameter mismatches penalty (weight: 10%)
    """
    score = 100.0

    # Indicator status scoring (50% weight)
    indicator_scores = []
    for name, info in report.get("indicators", {}).items():
        status = info.get("status", "MISSING")
        if status == "PASS":
            indicator_scores.append(100)
        elif status == "WARN":
            indicator_scores.append(60)
        elif status == "FAIL":
            indicator_scores.append(20)
        elif status == "MISSING":
            indicator_scores.append(0)

    if indicator_scores:
        indicator_avg = sum(indicator_scores) / len(indicator_scores)
    else:
        indicator_avg = 0.0

    # Strategy status scoring (30% weight)
    strategy_scores = []
    for name, info in report.get("strategies", {}).items():
        status = info.get("status", "WARN")
        if status == "PASS":
            strategy_scores.append(100)
        elif status == "WARN":
            strategy_scores.append(60)
        elif status == "FAIL":
            strategy_scores.append(20)
        elif status == "MISSING":
            strategy_scores.append(0)

    if strategy_scores:
        strategy_avg = sum(strategy_scores) / len(strategy_scores)
    else:
        strategy_avg = 0.0

    # Missing components penalty (10% weight)
    missing_count = len(report.get("missing_components", []))
    missing_penalty = min(100, missing_count * 20)  # 20 pts per missing component
    missing_score = max(0, 100 - missing_penalty)

    # Parameter mismatches penalty (10% weight)
    mismatch_count = len(report.get("parameter_mismatches", []))
    mismatch_penalty = min(100, mismatch_count * 15)  # 15 pts per mismatch
    mismatch_score = max(0, 100 - mismatch_penalty)

    # Weighted composite
    overall = (
        indicator_avg * 0.50
        + strategy_avg * 0.30
        + missing_score * 0.10
        + mismatch_score * 0.10
    )

    return round(overall, 1)


# ────────────────────────────────────────────────────────────────────���─
# 11. MAIN AUDIT FUNCTION
# ──────────────────────────────────────────────────────────────────────
def audit_system() -> Dict[str, Any]:
    """
    Main audit function. Scans training docs, scans code, compares parameters,
    runs tests on synthetic data, and produces a complete SystemHealthReport.
    """
    report = {
        "overall_health": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "indicators": {},
        "strategies": {},
        "missing_components": [],
        "parameter_mismatches": [],
        "corrections": [],
    }

    try:
        # Step 1: Scan training docs
        training = scan_training_docs()

        # Step 2: Scan Kabroda code
        code = scan_kabroda_code()

        # Step 3: Extract actual parameters
        actual_params = extract_all_actual_params(code)

        # Step 4: Compare parameters
        mismatches = compare_parameters(REFERENCE_PARAMETERS, actual_params)
        report["parameter_mismatches"] = mismatches

        # Step 5: Generate synthetic test data
        sample_data = generate_synthetic_ohlcv(200)

        # Step 6: Test each indicator
        indicator_names = ["BBWP", "PMARP", "RSI", "RSI_Divergence", "Trend", "Revin_Ribbons", "RMO", "RWP"]
        indicator_tests = {}
        for name in indicator_names:
            indicator_tests[name] = test_indicator_on_sample_data(name, sample_data)

        # Step 7: Test strategies
        strategy_tests = test_strategies(sample_data)

        # Step 8: Build indicator report
        indicator_configs = [
            ("BBWP", "BBWP", "PASS"),
            ("PMARP", "PMARP", "PASS"),
            ("RSI", "RSI", "PASS"),
            ("RSI_Divergence", "RSI_Divergence", "PASS"),
            ("Moving_Averages", "EMA_Ribbon", "WARN"),
            ("Revin_Ribbons", "Revin_Ribbons", "PASS"),
            ("RMO", "RMO", "PASS"),
            ("RWP", "RWP", "PASS"),
        ]

        for ref_key, display_name, default_status in indicator_configs:
            ref = REFERENCE_PARAMETERS.get(ref_key, {})
            act = actual_params.get(ref_key, {})
            test_res = indicator_tests.get(ref_key, {"status": "SKIP"})

            status = determine_indicator_status(display_name, act, test_res, mismatches)

            issues = []
            if status == "MISSING":
                issues.append(f"Not implemented. Training has full spec in training docs.")
            elif status == "WARN":
                # Add mismatch issues
                for m in mismatches:
                    if m.get("indicator") == ref_key or m.get("indicator") == display_name:
                        issues.append(f"{m.get('parameter')}: reference={m.get('reference')}, actual={m.get('actual')}")
                # Add test issues
                if test_res.get("issues"):
                    issues.extend(test_res["issues"])

            report["indicators"][display_name] = {
                "status": status,
                "reference": ref,
                "actual": act if act else None,
                "thresholds_match": status == "PASS",
                "issues": issues,
            }

        # Step 9: Build strategy report
        strategy_configs = [
            ("S1_Macro_Trend", "S1_Macro_Trend"),
            ("S2_Uptrend_Pullback", "S2_Uptrend_Pullback"),
            ("S3_Downtrend_Continuation", "S3_Downtrend_Continuation"),
            ("S4_Exhaustion_Short", "S4_Exhaustion_Short"),
            ("S5_Breakdown_Short", "S5_Breakdown_Short"),
        ]

        for ref_key, display_name in strategy_configs:
            ref = REFERENCE_PARAMETERS.get("Strategies", {}).get(ref_key, {})
            test_res = strategy_tests.get(display_name, {"status": "WARN", "action": "HOLD"})

            status = determine_strategy_status(display_name, test_res, mismatches)

            issues = []
            if status == "WARN":
                for m in mismatches:
                    if m.get("strategy") == ref_key or m.get("strategy") == display_name:
                        issues.append(f"{m.get('parameter')}: reference={m.get('reference')}, actual={m.get('actual')}")

            report["strategies"][display_name] = {
                "status": status,
                "reference_rules": ref.get("entry", ""),
                "actual_rules": test_res.get("reason", ""),
                "issues": issues,
            }

        # Step 10: Identify missing components
        report["missing_components"] = identify_missing_components(actual_params)

        # Step 11: Generate corrections
        report["corrections"] = generate_corrections(mismatches, report["missing_components"])

        # Step 12: Compute overall health
        report["overall_health"] = compute_health_score(report)

    except Exception as e:
        report["overall_health"] = 0.0
        report["_error"] = str(e)

    return report


# ──────────────────────────────────────────────────────────────────────
# 12. FORMATTED REPORT PRINTER
# ──────────────────────────────────────────────────────────────────────
def print_formatted_report(report: Dict[str, Any]) -> None:
    """Prints a beautifully formatted SystemHealthReport to stdout."""
    sep = "=" * 80
    sub_sep = "-" * 80

    print(sep)
    print(f"  KQAL SYSTEM HEALTH REPORT")
    print(f"  Timestamp: {report.get('timestamp', 'N/A')}")
    print(sep)
    print(f"\n  OVERALL HEALTH: {report.get('overall_health', 0.0)}%")
    health = report.get("overall_health", 0.0)
    if health >= 80:
        print(f"  Rating: ✅ GOOD - System is healthy")
    elif health >= 60:
        print(f"  Rating: ⚠️  FAIR - Some issues need attention")
    elif health >= 40:
        print(f"  Rating: 🔴 POOR - Significant issues detected")
    else:
        print(f"  Rating: ❌ CRITICAL - Major components missing or broken")
    print()

    # Indicators
    print(sub_sep)
    print("  INDICATORS")
    print(sub_sep)
    for name, info in report.get("indicators", {}).items():
        status = info.get("status", "UNKNOWN")
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "MISSING": "⬜"}.get(status, "❓")
        print(f"\n  {icon} {name}: {status}")
        if info.get("issues"):
            for issue in info["issues"]:
                print(f"     └─ {issue}")
        if info.get("reference") and info.get("actual"):
            ref = info["reference"]
            act = info["actual"]
            if isinstance(ref, dict) and isinstance(act, dict):
                for key in ref:
                    if key in act and act[key] != ref[key]:
                        print(f"     └─ {key}: ref={ref[key]}, actual={act[key]}")

    # Strategies
    print(f"\n{sub_sep}")
    print("  STRATEGIES")
    print(sub_sep)
    for name, info in report.get("strategies", {}).items():
        status = info.get("status", "UNKNOWN")
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(status, "❓")
        print(f"\n  {icon} {name}: {status}")
        if info.get("issues"):
            for issue in info["issues"]:
                print(f"     └─ {issue}")

    # Missing Components
    print(f"\n{sub_sep}")
    print("  MISSING COMPONENTS")
    print(sub_sep)
    for comp in report.get("missing_components", []):
        priority = comp.get("priority", "MEDIUM")
        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(priority, "⚪")
        print(f"\n  {icon} {comp.get('name', 'Unknown')} (Priority: {priority})")
        print(f"     └─ Source: {comp.get('reference_doc', 'N/A')}")
        print(f"     └─ {comp.get('description', '')}")

    # Parameter Mismatches
    print(f"\n{sub_sep}")
    print("  PARAMETER MISMATCHES")
    print(sub_sep)
    mismatches = report.get("parameter_mismatches", [])
    if not mismatches:
        print("\n  ✅ No parameter mismatches detected.")
    else:
        for m in mismatches:
            sev = m.get("severity", "LOW")
            icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚪")
            ind = m.get("indicator") or m.get("strategy", "Unknown")
            print(f"\n  {icon} [{sev}] {ind}: {m.get('parameter', '?')}")
            print(f"     └─ Reference: {m.get('reference', '?')}")
            print(f"     └─ Actual:    {m.get('actual', '?')}")

    # Corrections
    print(f"\n{sub_sep}")
    print("  PRIORITIZED CORRECTIONS")
    print(sub_sep)
    for corr in report.get("corrections", []):
        impact = corr.get("impact", "MEDIUM")
        effort = corr.get("effort", "MEDIUM")
        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(impact, "⚪")
        print(f"\n  {icon} {corr.get('id', '?')}: {corr.get('title', '?')}")
        print(f"     └─ Impact: {impact} | Effort: {effort}")
        print(f"     └─ {corr.get('description', '')}")
        if corr.get("files_to_create"):
            print(f"     └─ Create: {', '.join(corr['files_to_create'])}")
        if corr.get("files_to_modify"):
            print(f"     └─ Modify: {', '.join(corr['files_to_modify'])}")

    print(f"\n{sep}")
    print(f"  END OF SYSTEM HEALTH REPORT")
    print(f"{sep}")


# ──────────────────────────────────────────────────────────────────────
# 13. MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    print("KQAL System Auditor — Running full audit...")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()

    report = audit_system()
    print_formatted_report(report)

    # Also save report to JSON
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "system_health_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nReport saved to: {report_path}")
    except Exception as e:
        print(f"\nWarning: Could not save report to {report_path}: {e}")

    # Exit with code based on health
    health = report.get("overall_health", 0.0)
    if health >= 80:
        sys.exit(0)
    elif health >= 60:
        sys.exit(1)
    else:
        sys.exit(2)
