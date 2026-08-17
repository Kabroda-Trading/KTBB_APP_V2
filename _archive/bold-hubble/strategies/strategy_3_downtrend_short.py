from typing import List, Dict, Any
from indicators.trend_volatility import evaluate_dominant_trend, calculate_sma
from indicators.rsi_divergence import calculate_rsi, detect_rsi_divergences

def evaluate_strategy_3(high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> Dict[str, Any]:
    """
    Krown Strategy #3: Downtrend Continuation Short Scalp System
    
    Target: Shorting weak relief rallies inside an established crypto downtrend.
    Timeframe: 15m, 1H, 4H.
    """
    if len(close_prices) < 60:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Insufficient data"}
        
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    if not trend["is_downtrend"]:
        return {
            "action": "HOLD",
            "direction": "NEUTRAL",
            "confidence": 0.0,
            "reason": f"Strategy #3 requires Downtrend. Current regime: {trend['regime']}"
        }
        
    sma_20 = calculate_sma(close_prices, 20)
    sma_50 = calculate_sma(close_prices, 50)
    rsi = calculate_rsi(close_prices, 14)
    divergences = detect_rsi_divergences(high_prices, low_prices, close_prices)
    
    curr_close = close_prices[-1]
    curr_high = high_prices[-1]
    curr_rsi = rsi[-1]
    
    if sma_20[-1] is None or sma_50[-1] is None or curr_rsi is None:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Indicator calculation incomplete"}
        
    # Check if price rallied up into resistance zone (around/between 20 SMA and 50 SMA)
    in_resistance_zone = curr_high >= sma_20[-1] * 0.99 and curr_close <= sma_50[-1]
    rsi_rallied = 47.0 <= curr_rsi <= 60.0
    
    recent_hidden_bear = any(
        div["bar_index"] >= len(close_prices) - 4
        for div in divergences.get("hidden_bearish", [])
    )
    
    if in_resistance_zone and (rsi_rallied or recent_hidden_bear):
        confidence = 85.0 if recent_hidden_bear else 75.0
        return {
            "action": "SELL",
            "direction": "SHORT",
            "confidence": confidence,
            "entry_price": curr_close,
            "stop_loss": max(high_prices[-3:]) * 1.01,
            "take_profit_target": min(low_prices[-15:]),
            "reason": f"Rally into MA Resistance Zone + RSI resistance ({curr_rsi}) + Hidden Bearish Div: {recent_hidden_bear}"
        }
        
    return {
        "action": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 40.0,
        "reason": f"Waiting for optimal rally resistance zone. RSI: {curr_rsi}"
    }
