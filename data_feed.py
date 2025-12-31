import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from datetime import datetime, timedelta
import pytz

# Configure exchange
exchange = ccxt.binanceus({'enableRateLimit': True})

async def fetch_candles(symbol: str, timeframe: str, limit: int = 1000):
    try:
        # Fallback mapping
        if symbol == "BTC/USDT":
            symbol = "BTC/USDT"
        
        # CCXT fetch
        candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        data = []
        for c in candles:
            data.append({
                "time": int(c[0] / 1000), # Unix timestamp
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })
        return data
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe}: {e}")
        return []

# ---------------------------------------------------------
# 1. WEALTH OS DATA FEED (Macro/Weekly)
# ---------------------------------------------------------
def get_investing_inputs(symbol: str):
    """
    Fetches Monthly and Weekly data for Wealth OS analysis.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Monthly: ~16 years (Macro Cycle)
        monthly = loop.run_until_complete(fetch_candles(symbol, "1M", 200))
        
        # Weekly: ~7 years (Micro/Stair-Step)
        weekly = loop.run_until_complete(fetch_candles(symbol, "1w", 350))
        
    finally:
        loop.close()
        
    return {"monthly_candles": monthly, "weekly_candles": weekly}

# ---------------------------------------------------------
# 2. BATTLEBOX SUITE DATA FEED (Daily/Intraday)
# ---------------------------------------------------------
def get_inputs(symbol: str, date=None, session_tz="UTC"):
    """
    Fetches Daily and 30m data for Day Trading Session analysis.
    Restored to fix AttributeError.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Daily: Used for Support/Resistance levels
        daily = loop.run_until_complete(fetch_candles(symbol, "1d", 100))
        
        # 30m: Used for Session High/Low calculations
        # We fetch enough to cover the last few days of sessions
        intraday = loop.run_until_complete(fetch_candles(symbol, "30m", 200))
        
    finally:
        loop.close()
    
    # Structure matching dmr_report expectations
    return {
        "daily_candles": daily,
        "intraday_candles": intraday,
        "current_price": intraday[-1]['close'] if intraday else 0.0
    }