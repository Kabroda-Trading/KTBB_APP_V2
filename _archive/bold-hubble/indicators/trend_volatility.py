from typing import List, Dict, Union, Optional
from .rsi_divergence import find_local_extrema
from .bbwp import calculate_sma

def calculate_ema(data: List[float], period: int) -> List[Optional[float]]:
    """Calculates Exponential Moving Average (EMA)."""
    if len(data) < period:
        return [None] * len(data)
        
    ema = [None] * len(data)
    # Start EMA with simple average
    multiplier = 2.0 / (period + 1.0)
    first_sma = sum(data[:period]) / period
    ema[period - 1] = first_sma
    
    for i in range(period, len(data)):
        ema[i] = ((data[i] - ema[i - 1]) * multiplier) + ema[i - 1]
    return ema

def evaluate_dominant_trend(high_prices: List[float], low_prices: List[float], close_prices: List[float], pivot_order: int = 5) -> Dict[str, Union[str, float, int]]:
    """
    Evaluates the Dominant Trend according to Classical Technical Analysis (Krown Methodology).
    
    Evaluates market structure by examining recent swing highs and swing lows:
    - Strong Uptrend: Series of Higher Highs (HH) and Higher Lows (HL)
    - Strong Downtrend: Series of Lower Highs (LH) and Lower Lows (LL)
    - Range / Neutral: Mixed or contracting pivot highs/lows
    
    Returns structured trend classification and trend score (-100 to +100).
    """
    high_idx, low_idx = find_local_extrema(high_prices, order=pivot_order)
    _, low_pivots = find_local_extrema(low_prices, order=pivot_order)
    
    if len(high_idx) < 2 or len(low_pivots) < 2:
        return {
            "regime": "UNDEFINED",
            "score": 0.0,
            "structure": "Insufficient swing points",
            "is_uptrend": False,
            "is_downtrend": False
        }
        
    # Get last two swing highs and lows
    hh_last = high_prices[high_idx[-1]] > high_prices[high_idx[-2]]
    hl_last = low_prices[low_pivots[-1]] > low_prices[low_pivots[-2]]
    
    # Also check short term MA vs long term MA alignment (e.g., 20 SMA vs 50 SMA)
    sma_20 = calculate_sma(close_prices, 20)
    sma_50 = calculate_sma(close_prices, 50)
    
    ma_aligned_up = False
    ma_aligned_down = False
    if sma_20[-1] is not None and sma_50[-1] is not None:
        ma_aligned_up = sma_20[-1] > sma_50[-1] and close_prices[-1] > sma_20[-1]
        ma_aligned_down = sma_20[-1] < sma_50[-1] and close_prices[-1] < sma_20[-1]
        
    score = 0.0
    if hh_last: score += 35.0
    else: score -= 35.0
    
    if hl_last: score += 35.0
    else: score -= 35.0
    
    if ma_aligned_up: score += 30.0
    elif ma_aligned_down: score -= 30.0
    
    regime = "RANGE_CHOP"
    is_up = False
    is_down = False
    
    if score >= 65.0:
        regime = "STRONG_UPTREND"
        is_up = True
    elif score >= 30.0:
        regime = "WEAK_UPTREND"
        is_up = True
    elif score <= -65.0:
        regime = "STRONG_DOWNTREND"
        is_down = True
    elif score <= -30.0:
        regime = "WEAK_DOWNTREND"
        is_down = True
        
    return {
        "regime": regime,
        "score": score,
        "structure": f"HH:{hh_last}, HL:{hl_last}, MA_Up:{ma_aligned_up}",
        "is_uptrend": is_up,
        "is_downtrend": is_down
    }
