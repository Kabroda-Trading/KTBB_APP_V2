# liquidity_oracle.py
# ==============================================================================
# KABRODA CUSTOM LIQUIDITY ORACLE v5.3 (BULLETPROOF L2 TELESCOPE)
# ==============================================================================
# Purpose: Bypasses CCXT and aiohttp. Utilizes the synchronous 'requests' 
# library wrapped in an async thread to guarantee Proxy Authentication 
# headers are never dropped during the HTTPS CONNECT tunnel.
# ==============================================================================

import os
import requests
import asyncio
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

    def _fetch_sync():
        proxies = {}
        if BINANCE_PROXY_URL:
            # The requests library perfectly parses the URL string natively
            proxies = {
                "http": BINANCE_PROXY_URL,
                "https": BINANCE_PROXY_URL
            }
        
        # We use requests because it properly passes Auth through HTTPS tunnels
        response = requests.get(url, proxies=proxies, timeout=15)
        response.raise_for_status()
        return response.json()

    try:
        # Run the fetch in a background thread so we don't freeze the async radar
        data = await asyncio.to_thread(_fetch_sync)

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