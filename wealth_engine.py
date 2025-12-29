# wealth_engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import ccxt.async_support as ccxt  # Use async ccxt to not block main thread
import pandas as pd
import asyncio

# 1. THE WEALTH PROFILES (The "Personalities")
PROFILES = {
    "vault": {
        "name": "The Vault",
        "desc": "Maximum Accumulation. Never Sell.",
        "alloc_matrix": {"winter": 2.0, "summer": 1.0, "trim": 0.0}
    },
    "compounder": {
        "name": "The Compounder",
        "desc": "Growth & Harvesting. Trims Tops.",
        "alloc_matrix": {"winter": 1.5, "summer": 0.8, "trim": 0.15}
    },
    "sentinel": {
        "name": "The Sentinel",
        "desc": "Capital Preservation. Confirms Trends.",
        "alloc_matrix": {"winter": 0.0, "summer": 1.2, "trim": 0.50}
    }
}

# 2. DATA FETCHING (Isolated to avoid touching data_feed.py)
async def fetch_daily_history(symbol: str = "BTC/USDT", limit: int = 730) -> pd.DataFrame:
    """
    Fetches 2 years of daily data specifically for Macro Analysis.
    Independent of the BattleBox intraday feed.
    """
    exchange = ccxt.kraken() # Or binance, based on preference
    try:
        # Check if we need to map symbol
        if symbol == "BTCUSDT": symbol = "BTC/USDT"
        
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe='1d', limit=limit)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        return df
    finally:
        await exchange.close()

# 3. MACRO ANALYSIS (The "Navigator")
def analyze_macro_cycle(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"state": "UNKNOWN", "price": 0}

    # Calculate EMAs manually to avoid heavy dependencies
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

    current_price = df['close'].iloc[-1]
    is_bull = df['ema21'].iloc[-1] > df['ema200'].iloc[-1]
    
    # Identify Cycle High/Low (Simple logic: Rolling 1-year max/min)
    # In production, you might want more complex "pivot" logic here.
    cycle_high = df['high'].rolling(365, min_periods=1).max().iloc[-1]
    
    # Find the "Winter Low" (Lowest low since last Death Cross)
    # Simplified for robust execution: use 1-year low
    cycle_low = df['low'].rolling(365, min_periods=1).min().iloc[-1]

    # Calculate Zones
    rng = cycle_high - cycle_low
    kill_switch = cycle_high - (rng * 0.5)
    buy_zone_top = cycle_high - (rng * 0.5)
    buy_zone_bot = cycle_high - (rng * 0.618)
    ext_1618 = cycle_high + (rng * 0.618)
    
    # Determine State
    state = "MOMENTUM"
    regime = "BULL"
    
    if not is_bull: 
        state = "WINTER"
        regime = "BEAR"
    elif current_price < kill_switch:
        state = "DANGER" # Bullish EMAs but price collapsed below 50%
    elif buy_zone_bot <= current_price <= buy_zone_top:
        state = "DISCOUNT"
    elif current_price >= ext_1618:
        state = "OVERHEATED"
        
    return {
        "price": current_price,
        "regime": regime,
        "state": state,
        "levels": {
            "floor": cycle_low,
            "ceiling": cycle_high,
            "kill_switch": kill_switch,
            "buy_zone": f"{buy_zone_bot:.0f} - {buy_zone_top:.0f}",
            "extension": ext_1618
        }
    }

# 4. THE CALCULATOR (The "What-If" Engine)
def run_wealth_scenario(capital: float, profile_key: str, macro_data: Dict) -> Dict:
    profile = PROFILES.get(profile_key, PROFILES["vault"])
    state = macro_data.get("state", "UNKNOWN")
    
    action = "HOLD"
    pct = 0.0
    reasoning = "Standard Hold."
    
    if state == "WINTER":
        pct = 0.5 * profile["alloc_matrix"]["winter"]
        action = "ACCUMULATE" if pct > 0 else "WAIT"
        reasoning = "Market is in Winter. " + ("Deploy reserves." if pct > 0 else "Preserve capital.")
        
    elif state == "DISCOUNT":
        pct = 0.6 * profile["alloc_matrix"]["summer"]
        action = "BUY THE DIP"
        reasoning = "Price is in the Green Zone (Micro Pullback)."
        
    elif state == "MOMENTUM":
        pct = 0.2 * profile["alloc_matrix"]["summer"]
        action = "DCA"
        reasoning = "Trend is healthy. Standard allocation."
        
    elif state == "OVERHEATED":
        pct = -1.0 * profile["alloc_matrix"]["trim"]
        action = "TRIM" if pct < 0 else "HOLD"
        reasoning = "Market hit 1.618 Extension."
        
    elif state == "DANGER":
        pct = 0.0
        action = "WAIT"
        reasoning = "Price lost the 50% level. Wait for reclaim."

    amount = capital * abs(pct)
    
    return {
        "profile": profile["name"],
        "action": action,
        "amount": amount,
        "pct_display": f"{pct*100:.0f}%",
        "reasoning": reasoning,
        "state": state
    }