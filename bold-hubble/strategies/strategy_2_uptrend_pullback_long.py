from typing import List, Dict, Any
from indicators.trend_volatility import evaluate_dominant_trend, calculate_sma
from indicators.rsi_divergence import calculate_rsi, detect_rsi_divergences

def evaluate_strategy_2(high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> Dict[str, Any]:
    """
    Krown Strategy #2: Uptrend Pullback Long Scalp System
    
    Target: High-probability dip buying inside an established crypto uptrend.
    Timeframe: 15m, 1H, 4H.
    """
    if len(close_prices) < 60:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Insufficient data"}
        
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    if not trend["is_uptrend"]:
        return {
            "action": "HOLD",
            "direction": "NEUTRAL",
            "confidence": 0.0,
            "reason": f"Strategy #2 requires Uptrend. Current regime: {trend['regime']}"
        }
        
    sma_20 = calculate_sma(close_prices, 20)
    sma_50 = calculate_sma(close_prices, 50)
    rsi = calculate_rsi(close_prices, 14)
    divergences = detect_rsi_divergences(high_prices, low_prices, close_prices)
    
    curr_close = close_prices[-1]
    curr_low = low_prices[-1]
    curr_rsi = rsi[-1]
    
    if sma_20[-1] is None or sma_50[-1] is None or curr_rsi is None:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Indicator calculation incomplete"}
        
    # Check if price is pulling back into the value zone (between 20 SMA and 50 SMA)
    in_value_zone = curr_low <= sma_20[-1] * 1.01 and curr_close >= sma_50[-1]
    rsi_pulled_back = 40.0 <= curr_rsi <= 53.0
    
    # Check for recent hidden bullish divergence within last 3 bars
    recent_hidden_bull = any(
        div["bar_index"] >= len(close_prices) - 4
        for div in divergences.get("hidden_bullish", [])
    )
    
    if in_value_zone and (rsi_pulled_back or recent_hidden_bull):
        confidence = 85.0 if recent_hidden_bull else 75.0
        return {
            "action": "BUY",
            "direction": "LONG",
            "confidence": confidence,
            "entry_price": curr_close,
            "stop_loss": min(low_prices[-3:]) * 0.99,
            "take_profit_target": max(high_prices[-15:]),
            "reason": f"Pullback into MA Value Zone + RSI reset ({curr_rsi}) + Hidden Bullish Div: {recent_hidden_bull}"
        }
        
    return {
        "action": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 40.0,
        "reason": f"Waiting for optimal pullback zone. RSI: {curr_rsi}"
    }
