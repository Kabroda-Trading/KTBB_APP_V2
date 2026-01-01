# wealth_os.py
from __future__ import annotations
from typing import Any, Dict
import pandas as pd

# ---------------------------------------------------------
# WEALTH OS BRAIN (Macro Investing Logic)
# ---------------------------------------------------------

def calculate_sma(prices, period):
    if len(prices) < period: return []
    return pd.Series(prices).rolling(window=period).mean().tolist()

def run_wealth_brain(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyzes Monthly/Weekly data for long-term investing signals.
    """
    monthly_data = inputs.get("monthly_candles", [])
    weekly_data = inputs.get("weekly_candles", []) # Note: Feed actually pulls Daily for precision
    
    symbol = "BTCUSDT" # Default
    
    if not monthly_data or len(monthly_data) < 20:
        return {
            "status": "INSUFFICIENT_DATA",
            "cycle": "UNKNOWN",
            "action": "HOLD",
            "zone": "NEUTRAL"
        }
        
    # 1. EXTRACT PRICES
    closes = [c["close"] for c in monthly_data]
    current_price = closes[-1]
    
    # 2. RUN MACRO INDICATORS (Simple Moving Averages)
    sma_20 = calculate_sma(closes, 20) # 20-Month SMA (Bull/Bear Divider)
    
    # 3. DETERMINE CYCLE
    cycle_stage = "ACCUMULATION"
    action = "DCA"
    zone_color = "#387ef5" # Blue
    
    # Basic Logic: Price vs 20 SMA
    if len(sma_20) > 0:
        m20 = sma_20[-1]
        
        if current_price > m20 * 1.4:
            cycle_stage = "PARABOLIC / MANIA"
            action = "TAKE PROFIT"
            zone_color = "#ff4d4d" # Red
        elif current_price > m20:
            cycle_stage = "BULL MARKET"
            action = "HOLD / ADD DIP"
            zone_color = "#00ff9d" # Green
        elif current_price < m20 * 0.6:
            cycle_stage = "DEEP VALUE / DEPRESSION"
            action = "HEAVY BUY"
            zone_color = "#b39ddb" # Purple
        else:
            cycle_stage = "BEAR / ACCUMULATION"
            action = "DCA"
            zone_color = "#387ef5" # Blue

    return {
        "symbol": symbol,
        "price": current_price,
        "cycle": cycle_stage,
        "action": action,
        "zone_color": zone_color,
        "stats": {
            "sma_20_monthly": sma_20[-1] if len(sma_20) > 0 else 0
        }
    }