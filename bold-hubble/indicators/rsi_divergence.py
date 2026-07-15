from typing import List, Dict, Union, Optional, Tuple

def calculate_rsi(close_prices: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Calculates Wilder's Relative Strength Index (RSI).
    """
    if len(close_prices) <= period:
        return [None] * len(close_prices)
        
    rsi = [None] * len(close_prices)
    gains = []
    losses = []
    
    for i in range(1, len(close_prices)):
        change = close_prices[i] - close_prices[i - 1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
        
    # First average gain and loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))
        
    # Wilder's smoothed moving average for subsequent bars
    for i in range(period + 1, len(close_prices)):
        gain = gains[i - 1]
        loss = losses[i - 1]
        
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = round(100.0 - (100.0 / (1.0 + rs)), 2)
            
    return rsi

def find_local_extrema(data: List[float], order: int = 3) -> Tuple[List[int], List[int]]:
    """
    Finds indices of local highs (peaks) and local lows (troughs) within `order` bars on either side.
    """
    highs = []
    lows = []
    for i in range(order, len(data) - order):
        if data[i] is None:
            continue
        window = data[i - order : i + order + 1]
        if all(val is not None for val in window):
            if data[i] == max(window) and data[i] > data[i - 1]:
                highs.append(i)
            if data[i] == min(window) and data[i] < data[i - 1]:
                lows.append(i)
    return highs, lows

def detect_rsi_divergences(high_prices: List[float], low_prices: List[float], close_prices: List[float], rsi_period: int = 14, pivot_order: int = 3) -> Dict[str, List[Dict]]:
    """
    Detects Regular and Hidden RSI Divergences (Krown Quantitative Methodology).
    
    Returns structured list of detected divergences at recent pivots:
    - Regular Bullish: Price Lower Low + RSI Higher Low (Reversal Buy)
    - Regular Bearish: Price Higher High + RSI Lower High (Reversal Sell)
    - Hidden Bullish: Price Higher Low + RSI Lower Low (Uptrend Continuation Buy)
    - Hidden Bearish: Price Lower High + RSI Higher High (Downtrend Continuation Sell)
    """
    rsi = calculate_rsi(close_prices, rsi_period)
    
    # Use close or high/low for extrema detection
    price_highs, price_lows = find_local_extrema(high_prices, order=pivot_order)
    _, rsi_lows = find_local_extrema(low_prices, order=pivot_order)
    
    divergences = {
        "regular_bullish": [],
        "regular_bearish": [],
        "hidden_bullish": [],
        "hidden_bearish": []
    }
    
    # Check Lows (Bullish Divergence evaluation)
    for idx in range(1, len(price_lows)):
        curr_idx = price_lows[idx]
        prev_idx = price_lows[idx - 1]
        
        if rsi[curr_idx] is None or rsi[prev_idx] is None:
            continue
            
        p_curr = low_prices[curr_idx]
        p_prev = low_prices[prev_idx]
        r_curr = rsi[curr_idx]
        r_prev = rsi[prev_idx]
        
        # Regular Bullish: Price Lower Low (LL), RSI Higher Low (HL)
        if p_curr < p_prev and r_curr > r_prev:
            divergences["regular_bullish"].append({
                "bar_index": curr_idx,
                "prev_pivot_index": prev_idx,
                "price_curr": p_curr,
                "price_prev": p_prev,
                "rsi_curr": r_curr,
                "rsi_prev": r_prev,
                "signal": "Reversal Buy (Regular Bullish Divergence)"
            })
            
        # Hidden Bullish: Price Higher Low (HL), RSI Lower Low (LL)
        elif p_curr > p_prev and r_curr < r_prev:
            divergences["hidden_bullish"].append({
                "bar_index": curr_idx,
                "prev_pivot_index": prev_idx,
                "price_curr": p_curr,
                "price_prev": p_prev,
                "rsi_curr": r_curr,
                "rsi_prev": r_prev,
                "signal": "Uptrend Continuation Buy (Hidden Bullish Divergence)"
            })
            
    # Check Highs (Bearish Divergence evaluation)
    for idx in range(1, len(price_highs)):
        curr_idx = price_highs[idx]
        prev_idx = price_highs[idx - 1]
        
        if rsi[curr_idx] is None or rsi[prev_idx] is None:
            continue
            
        p_curr = high_prices[curr_idx]
        p_prev = high_prices[prev_idx]
        r_curr = rsi[curr_idx]
        r_prev = rsi[prev_idx]
        
        # Regular Bearish: Price Higher High (HH), RSI Lower High (LH)
        if p_curr > p_prev and r_curr < r_prev:
            divergences["regular_bearish"].append({
                "bar_index": curr_idx,
                "prev_pivot_index": prev_idx,
                "price_curr": p_curr,
                "price_prev": p_prev,
                "rsi_curr": r_curr,
                "rsi_prev": r_prev,
                "signal": "Reversal Sell (Regular Bearish Divergence)"
            })
            
        # Hidden Bearish: Price Lower High (LH), RSI Higher High (HH)
        elif p_curr < p_prev and r_curr > r_prev:
            divergences["hidden_bearish"].append({
                "bar_index": curr_idx,
                "prev_pivot_index": prev_idx,
                "price_curr": p_curr,
                "price_prev": p_prev,
                "rsi_curr": r_curr,
                "rsi_prev": r_prev,
                "signal": "Downtrend Continuation Sell (Hidden Bearish Divergence)"
            })
            
    return divergences
