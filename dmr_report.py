import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from datetime import datetime, timedelta
import pytz

# ---------------------------------------------------------
# CONFIGURE EXCHANGE: GLOBAL LIQUIDITY (KUCOIN)
# ---------------------------------------------------------
# We use KuCoin because it offers Global USDT pricing that matches
# TradingView charts better than the isolated Binance.US feed.
exchange = ccxt.kucoin({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot', 
    }
})

async def fetch_candles(symbol: str, timeframe: str, limit: int = 1000):
    try:
        # --- SYMBOL NORMALIZER ---
        s = symbol.upper().strip()
        # KuCoin requires strict "BTC/USDT" format
        if s == "BTC" or s == "BTCUSDT":
            symbol = "BTC/USDT"
        elif s == "ETH" or s == "ETHUSDT":
            symbol = "ETH/USDT"
        
        # Async fetch directly
        candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        data = []
        for c in candles:
            data.append({
                "time": int(c[0] / 1000), 
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })
        return data
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe} from KuCoin: {e}")
        return []

# ---------------------------------------------------------
# 1. WEALTH OS DATA FEED (Daily Switch)
# ---------------------------------------------------------
async def get_investing_inputs(symbol: str):
    """
    Fetches Monthly (Macro) and Daily (Execution).
    """
    try:
        monthly, daily = await asyncio.gather(
            fetch_candles(symbol, "1M", 200),
            fetch_candles(symbol, "1d", 1000) 
        )
        return {"monthly_candles": monthly, "weekly_candles": daily}
    except Exception as e:
        print(f"Wealth Feed Error: {e}")
        return {"monthly_candles": [], "weekly_candles": []}

# ---------------------------------------------------------
# 2. BATTLEBOX SUITE DATA FEED (Async)
# ---------------------------------------------------------
async def get_inputs(symbol: str, date=None, session_tz="UTC"):
    """
    Async fetch for Day Trading Suite.
    Fetches 1000 candles of 15m data to support Weekly VRVP calculation.
    """
    try:
        daily, intraday = await asyncio.gather(
            fetch_candles(symbol, "1d", 100),
            fetch_candles(symbol, "15m", 1000) 
        )
        
        current_price = intraday[-1]['close'] if intraday else 0.0
        
        return {
            "daily_candles": daily,
            "intraday_candles": intraday,
            "current_price": current_price
        }
    except Exception as e:
        print(f"Suite Feed Error: {e}")
        return {"daily_candles": [], "intraday_candles": [], "current_price": 0.0}