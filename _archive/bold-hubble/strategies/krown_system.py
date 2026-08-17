from typing import List, Dict, Any
from indicators.trend_volatility import evaluate_dominant_trend
from indicators.bbwp import calculate_bbwp, analyze_bbwp_state
from indicators.pmarp import calculate_pmarp, analyze_pmarp_state
from indicators.rsi_divergence import calculate_rsi, detect_rsi_divergences

# Revin Suite (R-Squared) imports
from indicators.revin_ribbons import calculate_revin_ribbons, analyze_ribbon_state
from indicators.rmo import calculate_rmo, analyze_rmo_state
from indicators.rwp import calculate_rwp, analyze_rwp_state
from indicators.revin_suite_engine import compute_revin_suite

# Three Drives divergence detection (IMP-005)
from indicators.three_drives import detect_three_drives

from strategies.strategy_1_basic_trend import evaluate_strategy_1
from strategies.strategy_2_uptrend_pullback_long import evaluate_strategy_2
from strategies.strategy_3_downtrend_short import evaluate_strategy_3
from strategies.strategy_4_5_vol_scalps import evaluate_strategy_4_uptrend_vol_short, evaluate_strategy_5_downtrend_vol_short


def evaluate_market_confluence(high_prices: List[float], low_prices: List[float], close_prices: List[float], asset_name: str = "BTC/USDT") -> Dict[str, Any]:
    """
    Unified Krown System Confluence Evaluator
    
    Executes the Krown Trading Bible core quantitative framework on any market data stream:
    1. Evaluates macro structural trend regime.
    2. Evaluates BBWP volatility regime (compression vs expansion).
    3. Evaluates PMARP mean-deviation state.
    4. Evaluates RSI & divergence momentum alignments.
    5. Evaluates Revin Suite (R-Squared): ribbon zone, RMO momentum, RWP volatility.
    6. Runs all 5 Krown Quantitative Strategies and selects the highest confidence actionable setup.
    """
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    bbwp = calculate_bbwp(close_prices)
    pmarp = calculate_pmarp(close_prices)
    rsi = calculate_rsi(close_prices)
    divergences = detect_rsi_divergences(high_prices, low_prices, close_prices)
    
    curr_bbwp = bbwp[-1]
    curr_pmarp = pmarp[-1]
    curr_rsi = rsi[-1]
    
    bbwp_state = analyze_bbwp_state(curr_bbwp)
    pmarp_state = analyze_pmarp_state(curr_pmarp)
    
    # ── Revin Suite (R-Squared) ─────────────────────────────────────────
    revin_suite = compute_revin_suite(close_prices, high_prices, low_prices)
    current = revin_suite["current"]
    ribbon_state = current["ribbon_state"]
    rmo_state = current["rmo_state"]
    rwp_state = current["rwp_state"]
    
    revin_zone = ribbon_state.get("zone", "UNKNOWN")
    revin_gray_dot = ribbon_state.get("gray_dot_tested", False)
    revin_outer_band = ribbon_state.get("outer_band_tested", False)
    revin_midline_direction = ribbon_state.get("midline_direction", "UNKNOWN")
    
    rmo_score = rmo_state.get("score", 0.0)
    rmo_direction = rmo_state.get("state", "NEUTRAL")
    rmo_overextended = rmo_state.get("is_overextended", False)
    
    rwp_score = rwp_state.get("score", 50.0)
    rwp_squeeze = rwp_state.get("is_squeeze", False)
    rwp_expansion = rwp_state.get("is_expansion", False)
    
    # Run all 5 strategy evaluators
    s1 = evaluate_strategy_1(high_prices, low_prices, close_prices)
    s2 = evaluate_strategy_2(high_prices, low_prices, close_prices)
    s3 = evaluate_strategy_3(high_prices, low_prices, close_prices)
    s4 = evaluate_strategy_4_uptrend_vol_short(high_prices, low_prices, close_prices)
    s5 = evaluate_strategy_5_downtrend_vol_short(high_prices, low_prices, close_prices)
    
    # ── Adjust strategy confidence with Revin Suite data ────────────────
    # RMO alignment: boost confidence if RMO direction matches strategy bias.
    # RMO returns 5 states: STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH.
    # Use substring match so STRONG_BULLISH matches "BULLISH" strategy bias.
    # If aligned but overextended (exhaustion risk), give a smaller boost instead of a penalty.
    for name, res in [("Strategy_1_Macro_Trend", s1), ("Strategy_2_Uptrend_Pullback", s2),
                      ("Strategy_3_Downtrend_Continuation", s3),
                      ("Strategy_4_Uptrend_Exhaustion_Short", s4),
                      ("Strategy_5_Downtrend_Vol_Short", s5)]:
        action = res.get("action", "HOLD")
        if action in ("BUY", "SELL"):
            strategy_bias = "BULLISH" if action == "BUY" else "BEARISH"
            rmo_aligned = strategy_bias in rmo_direction  # catches STRONG_BULLISH/BULLISH etc.
            if rmo_aligned and rmo_overextended:
                # Aligned but exhausted — smaller boost instead of penalty
                res["confidence"] = min(res.get("confidence", 0) + 5, 100)
                res["revin_boost"] = "rmo_aligned_exhausted"
            elif rmo_aligned:
                res["confidence"] = min(res.get("confidence", 0) + 10, 100)
                res["revin_boost"] = "rmo_aligned"
            elif rmo_overextended:
                res["confidence"] = max(res.get("confidence", 0) - 15, 0)
                res["revin_penalty"] = "rmo_overextended"
            else:
                res["revin_boost"] = "neutral"

    # ── Three Drives divergence (IMP-005) ────────────────────────────────
    # Detect 3-drive patterns and adjust strategy confidence accordingly.
    # A confirmed Three Drives pattern in the same direction as the strategy
    # bias is a strong alignment signal.
    three_drives_results = detect_three_drives(high_prices, low_prices, rsi)
    for name, res in [("Strategy_1_Macro_Trend", s1), ("Strategy_2_Uptrend_Pullback", s2),
                      ("Strategy_3_Downtrend_Continuation", s3),
                      ("Strategy_4_Uptrend_Exhaustion_Short", s4),
                      ("Strategy_5_Downtrend_Vol_Short", s5)]:
        action = res.get("action", "HOLD")
        if action in ("BUY", "SELL"):
            strategy_bias = "BULLISH" if action == "BUY" else "BEARISH"
            for td in three_drives_results:
                if td["pattern"] == strategy_bias and td["signal"] == "CONFIRMED":
                    res["confidence"] = min(res.get("confidence", 0) + 15, 100)
                    res["three_drives_boost"] = f"td_{td['pattern']}_c{td['confidence']:.0f}"
                elif td["pattern"] == strategy_bias and td["signal"] == "PENDING":
                    res["confidence"] = min(res.get("confidence", 0) + 5, 100)
                    res["three_drives_boost"] = f"td_{td['pattern']}_pending"
    
    strategies_eval = {
        "Strategy_1_Macro_Trend": s1,
        "Strategy_2_Uptrend_Pullback": s2,
        "Strategy_3_Downtrend_Continuation": s3,
        "Strategy_4_Uptrend_Exhaustion_Short": s4,
        "Strategy_5_Downtrend_Vol_Short": s5
    }
    
    # Select best actionable signal (BUY or SELL with highest confidence)
    actionable = [
        (name, res) for name, res in strategies_eval.items()
        if res.get("action") in ("BUY", "SELL", "TAKE_PROFIT_WARNING")
    ]
    
    best_signal = None
    if actionable:
        # Sort by confidence descending
        actionable.sort(key=lambda x: x[1].get("confidence", 0), reverse=True)
        best_signal = {
            "strategy_name": actionable[0][0],
            "details": actionable[0][1]
        }
    else:
        best_signal = {
            "strategy_name": "NONE",
            "details": {"action": "HOLD", "confidence": 0.0, "reason": "No high-confidence Krown setup active"}
        }
        
    return {
        "asset": asset_name,
        "current_price": close_prices[-1],
        "regime_summary": {
            "dominant_trend": trend["regime"],
            "trend_score": trend["score"],
            "volatility_bbwp": f"{curr_bbwp}% ({bbwp_state['state']})",
            "mean_deviation_pmarp": f"{curr_pmarp}% ({pmarp_state['state']})",
            "momentum_rsi": curr_rsi,
            # Revin Suite fields
            "revin_ribbon_zone": revin_zone,
            "revin_midline_direction": revin_midline_direction,
            "revin_gray_dot_tested": revin_gray_dot,
            "revin_outer_band_tested": revin_outer_band,
            "rmo_score": rmo_score,
            "rmo_direction": rmo_direction,
            "rmo_overextended": rmo_overextended,
            "rwp_score": rwp_score,
            "rwp_squeeze": rwp_squeeze,
            "rwp_expansion": rwp_expansion,
        },
        "detected_divergences": {
            k: len(v) for k, v in divergences.items() if len(v) > 0
        },
        "best_actionable_signal": best_signal,
        "all_strategy_evaluations": strategies_eval
    }
