# wealth_engine.py
from __future__ import annotations
import pandas as pd
import ccxt
import asyncio
from datetime import datetime, timedelta

# Re-use the Universal Feed logic concept, but simplified for Macro Analysis
def get_exchange():
    return ccxt.kraken({'enableRateLimit': True})

async def fetch_daily_history(symbol: str = "BTC/USDT", days: int = 365):
    """
    Fetches daily candles for Macro Analysis.
    """
    try:
        exch = get_exchange()
        # Normalize symbol for Kraken if needed
        if "BTC" in symbol and "USDT" in symbol: symbol = "BTC/USDT"
        
        since = exch.milliseconds() - (days * 24 * 60 * 60 * 1000)
        ohlcv = await asyncio.to_thread(exch.fetch_ohlcv, symbol, '1d', since)
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Wealth Engine Error: {e}")
        return pd.DataFrame()

def analyze_macro_cycle(df: pd.DataFrame):
    """
    Determines if we are in Accumulation, Bull, or Bear based on 200 SMA.
    """
    if df.empty:
        return {"phase": "NEUTRAL", "trend": "FLAT", "sma_200": 0, "current": 0}

    df['sma_50'] = df['close'].rolling(window=50).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    last = df.iloc[-1]
    price = last['close']
    sma200 = last['sma_200'] if not pd.isna(last['sma_200']) else price
    sma50 = last['sma_50'] if not pd.isna(last['sma_50']) else price

    # Logic: Where are we relative to the 200 Day MA?
    phase = "ACCUMULATION"
    if price > sma200:
        phase = "BULL_MARKET" if price > sma50 else "RECOVERY"
    else:
        phase = "BEAR_MARKET" if price < sma50 else "DISTRIBUTION"

    return {
        "phase": phase,
        "current_price": price,
        "sma_200": sma200,
        "distance_to_200": round(((price - sma200) / sma200) * 100, 2)
    }

def run_wealth_scenario(capital: float, profile_key: str, macro_data: dict):
    """
    Projects growth based on Kabroda Conservative Multipliers.
    """
    # Conservative annual estimates based on Bitcoin historic cycle lows vs highs
    multipliers = {
        "vault": 1.5,   # Conservative (Hold)
        "growth": 2.5,  # Moderate (DCA)
        "alpha": 4.0    # Aggressive (Cycle Trading)
    }
    
    mult = multipliers.get(profile_key, 1.5)
    projected = capital * mult
    profit = projected - capital
    
    return {
        "scenario": profile_key.upper(),
        "input_capital": capital,
        "multiplier": mult,
        "projected_value": projected,
        "profit": profit,
        "macro_context": macro_data
    }