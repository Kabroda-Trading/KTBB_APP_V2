import math
from typing import List, Dict, Union, Optional

def calculate_sma(data: List[float], period: int) -> List[Optional[float]]:
    """Calculates Simple Moving Average (SMA)."""
    sma = [None] * len(data)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1 : i + 1]
        sma[i] = sum(window) / period
    return sma

def calculate_stdev(data: List[float], period: int, sma: List[Optional[float]]) -> List[Optional[float]]:
    """Calculates Rolling Standard Deviation."""
    stdev = [None] * len(data)
    for i in range(period - 1, len(data)):
        avg = sma[i]
        if avg is None:
            continue
        window = data[i - period + 1 : i + 1]
        variance = sum((x - avg) ** 2 for x in window) / period
        stdev[i] = math.sqrt(variance)
    return stdev

def calculate_bbw(close_prices: List[float], period: int = 20, num_std: float = 2.0) -> List[Optional[float]]:
    """
    Calculates Bollinger Band Width (BBW).
    BBW = (Upper Band - Lower Band) / Middle Band
    """
    sma = calculate_sma(close_prices, period)
    stdev = calculate_stdev(close_prices, period, sma)
    bbw = [None] * len(close_prices)
    
    for i in range(len(close_prices)):
        if sma[i] is not None and stdev[i] is not None and sma[i] != 0:
            upper = sma[i] + (num_std * stdev[i])
            lower = sma[i] - (num_std * stdev[i])
            bbw[i] = (upper - lower) / sma[i]
    return bbw

def calculate_bbwp(close_prices: List[float], bb_period: int = 20, bb_std: float = 2.0, lookback_percentile: int = 252) -> List[Optional[float]]:
    """
    Calculates Krown's Bollinger Band Width Percentile (BBWP).
    
    BBWP measures where the current Bollinger Band Width falls relative to its historical values over `lookback_percentile` bars.
    Returns values between 0.0 and 100.0 representing the percentile.
    
    Key Thresholds in Krown Trading:
    - BBWP <= 5.0 (Blue/Blue-White): Extreme Volatility Compression (Squeeze). Major breakout impending.
    - BBWP >= 95.0 (Red): Extreme Volatility Expansion. Watch for trend exhaustion or climactic moves.
    """
    bbw = calculate_bbw(close_prices, bb_period, bb_std)
    bbwp = [None] * len(close_prices)
    
    for i in range(len(close_prices)):
        if bbw[i] is None:
            continue
        
        # Determine lookback slice
        start_idx = max(0, i - lookback_percentile + 1)
        # Filter out None values in lookback window
        historical_bbw = [val for val in bbw[start_idx : i + 1] if val is not None]
        
        if not historical_bbw:
            continue
            
        current_val = bbw[i]
        # Calculate percentile rank: count how many historical values are smaller than current_val
        count_smaller = sum(1 for val in historical_bbw if val < current_val)
        percentile = (count_smaller / len(historical_bbw)) * 100.0
        bbwp[i] = round(percentile, 2)
        
    return bbwp

def analyze_bbwp_state(bbwp_val: Optional[float]) -> Dict[str, Union[str, bool, float]]:
    """
    Interprets BBWP value into Krown quantitative volatility states.
    """
    if bbwp_val is None:
        return {"state": "UNKNOWN", "is_compression": False, "is_expansion": False, "signal": "Insufficient data"}
        
    if bbwp_val <= 5.0:
        return {
            "state": "EXTREME_COMPRESSION",
            "is_compression": True,
            "is_expansion": False,
            "signal": "High probability of violent volatility expansion impending (Squeeze alert)"
        }
    elif bbwp_val <= 15.0:
        return {
            "state": "MODERATE_COMPRESSION",
            "is_compression": True,
            "is_expansion": False,
            "signal": "Volatility building up; prepare for trend breakout"
        }
    elif bbwp_val >= 95.0:
        return {
            "state": "EXTREME_EXPANSION",
            "is_compression": False,
            "is_expansion": True,
            "signal": "Extreme volatility expansion; watch for potential climactic blow-off or exhaustion"
        }
    elif bbwp_val >= 85.0:
        return {
            "state": "HIGH_EXPANSION",
            "is_compression": False,
            "is_expansion": True,
            "signal": "Strong trend momentum in progress"
        }
    else:
        return {
            "state": "NEUTRAL",
            "is_compression": False,
            "is_expansion": False,
            "signal": "Normal volatility regime"
        }
