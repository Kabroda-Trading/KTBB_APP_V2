from typing import List, Dict, Any
from indicators.trend_volatility import evaluate_dominant_trend
from indicators.bbwp import calculate_bbwp
from indicators.pmarp import calculate_pmarp
from indicators.rsi_divergence import detect_rsi_divergences

def evaluate_strategy_4_uptrend_vol_short(high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> Dict[str, Any]:
    """
    Krown Strategy #4: Uptrend Continuation Volatility Short Scalp
    
    Target: Counter-trend short scalp when a crypto uptrend reaches parabolic exhaustion.
    Trigger: PMARP extreme (> 90-95) + BBWP extreme expansion (> 85-90) + Bearish Divergence.
    """
    if len(close_prices) < 60:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Insufficient data"}
        
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    bbwp = calculate_bbwp(close_prices)
    pmarp = calculate_pmarp(close_prices)
    divergences = detect_rsi_divergences(high_prices, low_prices, close_prices)
    
    curr_close = close_prices[-1]
    curr_bbwp = bbwp[-1]
    curr_pmarp = pmarp[-1]
    
    if curr_bbwp is None or curr_pmarp is None:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Indicator calculation incomplete"}
        
    recent_reg_bear = any(
        div["bar_index"] >= len(close_prices) - 4
        for div in divergences.get("regular_bearish", [])
    )
    
    # Condition: Parabolic extension + Volatility blow-off
    if curr_pmarp >= 90.0 and curr_bbwp >= 85.0:
        confidence = 85.0 if recent_reg_bear else 70.0
        return {
            "action": "SELL",
            "direction": "SHORT",
            "confidence": confidence,
            "entry_price": curr_close,
            "stop_loss": max(high_prices[-3:]) * 1.015,
            "take_profit_target": curr_close * 0.94,  # Quick 6% mean reversion scalp
            "reason": f"Parabolic exhaustion (PMARP: {curr_pmarp}%) + Vol blow-off (BBWP: {curr_bbwp}%) + Bear Div: {recent_reg_bear}"
        }
        
    return {
        "action": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 30.0,
        "reason": f"No exhaustion detected. PMARP: {curr_pmarp}%, BBWP: {curr_bbwp}%"
    }

def evaluate_strategy_5_downtrend_vol_short(high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> Dict[str, Any]:
    """
    Krown Strategy #5: Downtrend Continuation Volatility Short Scalp
    
    Target: Aggressive momentum breakdown short when volatility expands during a downtrend collapse.
    Trigger: Dominant Downtrend + BBWP breaking out of compression (> 20 rising sharply).
    """
    if len(close_prices) < 60:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Insufficient data"}
        
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    bbwp = calculate_bbwp(close_prices)
    
    curr_close = close_prices[-1]
    curr_bbwp = bbwp[-1]
    prev_bbwp = bbwp[-2] if len(bbwp) > 1 else None
    
    if curr_bbwp is None or prev_bbwp is None:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Indicator calculation incomplete"}
        
    if trend["is_downtrend"] and curr_bbwp > prev_bbwp and curr_bbwp >= 25.0 and prev_bbwp <= 35.0:
        return {
            "action": "SELL",
            "direction": "SHORT",
            "confidence": 80.0,
            "entry_price": curr_close,
            "stop_loss": high_prices[-2],
            "take_profit_target": curr_close * 0.90,
            "reason": f"Downtrend momentum surge. Vol expansion BBWP rising ({prev_bbwp}% -> {curr_bbwp}%)"
        }
        
    return {
        "action": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 40.0,
        "reason": f"No volatility surge setup. Regime: {trend['regime']}, BBWP: {curr_bbwp}%"
    }
