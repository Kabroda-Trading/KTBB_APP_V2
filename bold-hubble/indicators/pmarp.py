from typing import List, Dict, Union, Optional
from .bbwp import calculate_sma

def calculate_pmar(close_prices: List[float], ma_period: int = 50) -> List[Optional[float]]:
    """
    Calculates Price Moving Average Ratio (PMAR).
    PMAR = Close / SMA(ma_period)
    """
    sma = calculate_sma(close_prices, ma_period)
    pmar = [None] * len(close_prices)
    for i in range(len(close_prices)):
        if sma[i] is not None and sma[i] > 0:
            pmar[i] = close_prices[i] / sma[i]
    return pmar

def calculate_pmarp(close_prices: List[float], ma_period: int = 50, lookback_percentile: int = 252) -> List[Optional[float]]:
    """
    Calculates Krown's Price Moving Average Ratio Percentile (PMARP).
    
    PMARP ranks the current PMAR against historical PMAR values over `lookback_percentile` bars.
    Returns percentile values from 0.0 to 100.0.
    
    Key Quantitative Thresholds:
    - PMARP >= 95.0: Extreme upside deviation from trend average (Parabolic / Overextended). High mean-reversion risk.
    - PMARP <= 5.0: Extreme downside deviation from trend average (Depressed / Capitulation). High mean-reversion bounce probability.
    """
    pmar = calculate_pmar(close_prices, ma_period)
    pmarp = [None] * len(close_prices)
    
    for i in range(len(close_prices)):
        if pmar[i] is None:
            continue
            
        start_idx = max(0, i - lookback_percentile + 1)
        historical_pmar = [val for val in pmar[start_idx : i + 1] if val is not None]
        
        if not historical_pmar:
            continue
            
        current_val = pmar[i]
        count_smaller = sum(1 for val in historical_pmar if val < current_val)
        percentile = (count_smaller / len(historical_pmar)) * 100.0
        pmarp[i] = round(percentile, 2)
        
    return pmarp

def analyze_pmarp_state(pmarp_val: Optional[float]) -> Dict[str, Union[str, bool, float]]:
    """
    Interprets PMARP value into quantitative mean-deviation states.
    """
    if pmarp_val is None:
        return {"state": "UNKNOWN", "is_overextended": False, "is_depressed": False, "signal": "Insufficient data"}
        
    if pmarp_val >= 95.0:
        return {
            "state": "EXTREME_OVEREXTENDED",
            "is_overextended": True,
            "is_depressed": False,
            "signal": "Extreme upside extension from MA. Elevated risk of pullback/mean reversion."
        }
    elif pmarp_val >= 85.0:
        return {
            "state": "MODERATE_OVEREXTENDED",
            "is_overextended": True,
            "is_depressed": False,
            "signal": "Price stretched above MA; trailing stop protection advised."
        }
    elif pmarp_val <= 5.0:
        return {
            "state": "EXTREME_DEPRESSED",
            "is_overextended": False,
            "is_depressed": True,
            "signal": "Extreme downside extension below MA. High probability of relief bounce/mean reversion."
        }
    elif pmarp_val <= 15.0:
        return {
            "state": "MODERATE_DEPRESSED",
            "is_overextended": False,
            "is_depressed": True,
            "signal": "Price deeply discounted relative to MA."
        }
    else:
        return {
            "state": "NORMAL_DEVIATION",
            "is_overextended": False,
            "is_depressed": False,
            "signal": "Price trading within normal historical variance around MA."
        }
