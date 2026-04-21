# liquidity_oracle.py
# ==============================================================================
# KABRODA CUSTOM LIQUIDITY ORACLE v5.0 (PROPRIETARY L2 TELESCOPE)
# ==============================================================================
# Purpose: Pulls the Binance USDT-M Futures Order Book (1000 levels).
# Utilizes a Datacenter Proxy tunnel to completely bypass the US Geofence.
# ==============================================================================

import os
import ccxt.async_support as ccxt
import traceback
from typing import Dict, Any

# Pull the proxy tunnel from Render Environment Variables
BINANCE_PROXY_URL = os.getenv("BINANCE_PROXY_URL")

def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s in ("SOL", "SOLUSDT"): return "SOL/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

async def fetch_liquidation_magnets(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    s = _normalize_symbol(symbol)
    
    config = {'enableRateLimit': True}
    
    # Inject the proxy tunnel if it exists in the environment
    if BINANCE_PROXY_URL:
        config['aiohttp_proxy'] = BINANCE_PROXY_URL
        
    exchange = ccxt.binanceusdm(config)
    
    try:
        # Binance public API allows up to 1000 limit depth without account keys
        # This provides the massive macro field of vision to locate real whales
        orderbook = await exchange.fetch_order_book(s, limit=1000)
        
        return {
            "status": "SUCCESS",
            "symbol": symbol,
            "raw_data": {
                "asks": orderbook.get('asks', []), 
                "bids": orderbook.get('bids', [])  
            }
        }
    except Exception as e:
        print(f"[BINANCE ORACLE ERROR] Failed to fetch order book for {s}: {e}")
        return {"status": "ERROR", "message": str(e), "raw_data": {}}
    finally:
        await exchange.close()