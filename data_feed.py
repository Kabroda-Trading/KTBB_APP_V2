import ccxt.async_support as ccxt
import pandas as pd
import asyncio

# Configure exchange
exchange = ccxt.binanceus({'enableRateLimit': True})

async def fetch_candles(symbol: str, timeframe: str, limit: int = 1000):
    try:
        # Map common symbols if needed
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

def get_investing_inputs(symbol: str):
    """
    Synchronous wrapper to fetch data for Wealth OS.
    Fetches Monthly (Macro) and Weekly (Micro) candles.
    """
    # 1. DEFINITION RESTORED: Create the Event Loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # 2. Run the async tasks synchronously
        # Monthly: 200 months (~16 years) covers all macro cycles
        monthly = loop.run_until_complete(fetch_candles(symbol, "1M", 200))
        
        # Weekly: 350 weeks (~7 years) - The specific limit you requested
        weekly = loop.run_until_complete(fetch_candles(symbol, "1w", 350))
        
    finally:
        # 3. Clean up
        loop.close()
        
    return {"monthly_candles": monthly, "weekly_candles": weekly}