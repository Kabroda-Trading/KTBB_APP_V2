# liquidity_oracle.py
# ==============================================================================
# KABRODA CUSTOM LIQUIDITY ORACLE v5.2 (SURGICAL L2 TELESCOPE)
# ==============================================================================
# Purpose: Bypasses CCXT to avoid the massive 'exchangeInfo' payload block.
# Hits the raw Binance /depth endpoint directly through the Residential Proxy.
# Explicitly handles proxy authentication to prevent 407 drops.
# ==============================================================================

import os
import aiohttp
import asyncio
from urllib.parse import urlparse
from typing import Dict, Any

# Pull the proxy tunnel from Render Environment Variables
BINANCE_PROXY_URL = os.getenv("BINANCE_PROXY_URL")

def _normalize_binance_symbol(symbol: str) -> str:
    # Binance's raw API wants "BTCUSDT" without any slashes
    s = (symbol or "").upper().strip()
    s = s.replace("/", "")
    if s == "BTC": return "BTCUSDT"
    if s == "ETH": return "ETHUSDT"
    if s == "SOL": return "SOLUSDT"
    return s

async def fetch_liquidation_magnets(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    raw_sym = _normalize_binance_symbol(symbol)
    
    # The exact surgical endpoint for 1000 levels of depth
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={raw_sym}&limit=1000"

    try:
        # We use a raw aiohttp session to completely bypass CCXT's overhead
        async with aiohttp.ClientSession() as session:
            kwargs = {}
            if BINANCE_PROXY_URL:
                parsed = urlparse(BINANCE_PROXY_URL)
                if parsed.username and parsed.password:
                    # Explicitly format auth for aiohttp to prevent 407 Auth errors
                    kwargs['proxy'] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                    kwargs['proxy_auth'] = aiohttp.BasicAuth(parsed.username, parsed.password)
                else:
                    kwargs['proxy'] = BINANCE_PROXY_URL

            # 15-second timeout to allow the Residential proxy time to route
            async with session.get(url, timeout=15, **kwargs) as response:
                
                if response.status != 200:
                    err_text = await response.text()
                    print(f"[BINANCE RAW ERROR] Status {response.status}: {err_text}")
                    return {"status": "ERROR", "message": f"HTTP {response.status}", "raw_data": {}}

                data = await response.json()

                # Convert raw string arrays from Binance into floats for Middle Brain math
                asks = [[float(price), float(vol)] for price, vol in data.get('asks', [])]
                bids = [[float(price), float(vol)] for price, vol in data.get('bids', [])]

                return {
                    "status": "SUCCESS",
                    "symbol": symbol,
                    "raw_data": {
                        "asks": asks,
                        "bids": bids
                    }
                }
                
    except Exception as e:
        print(f"[BINANCE ORACLE ERROR] Failed to fetch raw depth for {raw_sym}: {e}")
        return {"status": "ERROR", "message": str(e), "raw_data": {}}