# liquidity_oracle.py
# ==============================================================================
# KABRODA LIQUIDITY ORACLE v1.0 (COINGLASS API v4)
# ==============================================================================
# Purpose: Single Source of Truth for live institutional liquidation magnets.
# Integrates with CoinGlass Aggregated Map endpoint.
# Strictly Phase 1: Fetches data only. Zero trade evaluation logic.
# ==============================================================================

import os
import aiohttp
import asyncio
from typing import Dict, Any

# Securely pulls the API key from Render (or local environment)
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
BASE_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-map"

async def fetch_liquidation_magnets(symbol: str = "BTC", timeframe_range: str = "1d") -> Dict[str, Any]:
    """
    Fetches the aggregate liquidation map and returns the raw data payload for Phase 2 evaluation.
    """
    if not COINGLASS_API_KEY:
        print(f"[ORACLE WARNING] COINGLASS_API_KEY missing. Handshake bypassed for {symbol}.")
        return {"status": "BYPASSED", "raw_data": {}}

    # Format symbol for CoinGlass (e.g., 'BTCUSDT' -> 'BTC')
    cg_symbol = symbol.replace("USDT", "")

    headers = {
        "accept": "application/json",
        "CG-API-KEY": COINGLASS_API_KEY
    }
    
    params = {
        "symbol": cg_symbol,
        "range": timeframe_range
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BASE_URL, headers=headers, params=params, timeout=10) as response:
                if response.status != 200:
                    print(f"[ORACLE ERROR] CoinGlass API returned {response.status} for {symbol}")
                    return {"status": "ERROR", "message": f"HTTP {response.status}", "raw_data": {}}
                
                payload = await response.json()
                data = payload.get("data", {})
                
                print(f">>> COINGLASS RAW PAYLOAD for {cg_symbol}: {data}")
                print(f">>> COINGLASS PHASE 1 DATA SECURED for {cg_symbol}")

                # We return the raw data block. 
                # Phase 2 (Market Radar) will do the math to evaluate the walls.
                return {
                    "status": "SUCCESS",
                    "symbol": symbol,
                    "raw_data": data
                }

    except asyncio.TimeoutError:
        print(f"[ORACLE TIMEOUT] CoinGlass API took too long to respond for {symbol}.")
        return {"status": "ERROR", "message": "TIMEOUT", "raw_data": {}}
    except Exception as e:
        print(f"[ORACLE EXCEPTION] {str(e)}")
        return {"status": "ERROR", "message": str(e), "raw_data": {}}