from typing import List, Dict, Any
from indicators.trend_volatility import evaluate_dominant_trend, calculate_sma
from indicators.bbwp import calculate_bbwp, analyze_bbwp_state
from indicators.pmarp import calculate_pmarp

def evaluate_strategy_1(high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> Dict[str, Any]:
    """
    Krown Strategy #1: Basic Long/Short Trend System
    
    Target: Macro trend capture in crypto markets (Bitcoin/Altcoins).
    Timeframe: 4H, Daily, 3D.
    """
    if len(close_prices) < 60:
        return {"action": "HOLD", "confidence": 0.0, "reason": "Insufficient data"}
        
    trend = evaluate_dominant_trend(high_prices, low_prices, close_prices)
    bbwp = calculate_bbwp(close_prices)
    pmarp = calculate_pmarp(close_prices)
    sma_20 = calculate_sma(close_prices, 20)
    
    curr_close = close_prices[-1]
    curr_sma = sma_20[-1]
    curr_bbwp = bbwp[-1]
    prev_bbwp = bbwp[-2] if len(bbwp) > 1 else None
    curr_pmarp = pmarp[-1]
    
    bbwp_state = analyze_bbwp_state(curr_bbwp)
    
    # Check for exit warnings first
    if curr_pmarp is not None and curr_pmarp >= 95.0:
        return {
            "action": "TAKE_PROFIT_WARNING",
            "direction": "LONG_EXIT",
            "confidence": 85.0,
            "reason": f"PMARP extreme overextension ({curr_pmarp}%). Lock in profits / tighten trailing stop."
        }
        
    # Long Entry Conditions
    if trend["is_uptrend"] and curr_sma is not None and curr_close > curr_sma:
        # Check if volatility is expanding out of squeeze or active trend
        vol_confirm = (prev_bbwp is not None and curr_bbwp is not None and curr_bbwp > prev_bbwp and prev_bbwp <= 30.0) or (curr_bbwp is not None and curr_bbwp >= 70.0)
        
        if vol_confirm:
            return {
                "action": "BUY",
                "direction": "LONG",
                "confidence": 80.0 if trend["regime"] == "STRONG_UPTREND" else 65.0,
                "entry_price": curr_close,
                "stop_loss": low_prices[-2],
                "take_profit_target": curr_close * 1.15,
                "reason": f"Trend confirmed ({trend['regime']}) + Volatility expansion (BBWP: {curr_bbwp}%)"
            }
            
    # Short Entry Conditions
    if trend["is_downtrend"] and curr_sma is not None and curr_close < curr_sma:
        vol_confirm = (prev_bbwp is not None and curr_bbwp is not None and curr_bbwp > prev_bbwp and prev_bbwp <= 30.0) or (curr_bbwp is not None and curr_bbwp >= 70.0)
        
        if vol_confirm:
            return {
                "action": "SELL",
                "direction": "SHORT",
                "confidence": 80.0 if trend["regime"] == "STRONG_DOWNTREND" else 65.0,
                "entry_price": curr_close,
                "stop_loss": high_prices[-2],
                "take_profit_target": curr_close * 0.85,
                "reason": f"Downtrend confirmed ({trend['regime']}) + Volatility expansion (BBWP: {curr_bbwp}%)"
            }
            
    return {
        "action": "HOLD",
        "direction": "NEUTRAL",
        "confidence": 50.0,
        "reason": f"No actionable setup. Trend: {trend['regime']}, BBWP: {curr_bbwp}%"
    }
