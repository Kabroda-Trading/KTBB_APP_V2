# liquidity_oracle.py
# ==============================================================================
# KABRODA LIQUIDITY ORACLE v1.0 (COINGLASS API v4)
# ==============================================================================
# Purpose: Single Source of Truth for live institutional liquidation magnets.
# Integrates with CoinGlass Aggregated Map endpoint.
# ==============================================================================

import os
import aiohttp
import asyncio
from typing import Dict, Any

COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
BASE_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-map"

async def fetch_liquidation_magnets(symbol: str = "BTC", timeframe_range: str = "1d") -> Dict[str, Any]:
    if not COINGLASS_API_KEY:
        return {"status": "BYPASSED", "raw_data": {}}

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
                    return {"status": "ERROR", "message": f"HTTP {response.status}", "raw_data": {}}
                
                payload = await response.json()
                data = payload.get("data", {})
                
                # DIAGNOSTIC PROBE: Print the entire payload to catch internal CoinGlass errors
                print(f">>> FULL COINGLASS API RESPONSE for {cg_symbol}: {payload}")
                
                return {
                    "status": "SUCCESS",
                    "symbol": symbol,
                    "raw_data": data
                }

    except Exception as e:
        return {"status": "ERROR", "message": str(e), "raw_data": {}}