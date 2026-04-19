# live_telemetry.py
# ==============================================================================
# KABRODA LIVE TELEMETRY ENGINE v1.0 (PHASE 3)
# ==============================================================================
# Purpose: Live polling of CoinGlass Open Interest and Funding Rates.
# Calculates the 15-minute OI Delta to measure institutional momentum.
# Designed for real-time polling during active trade execution windows.
# ==============================================================================

import os
import aiohttp
import asyncio
from typing import Dict, Any

COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
BASE_URL = "https://open-api-v4.coinglass.com/api"

async def fetch_live_telemetry(symbol: str = "BTC") -> Dict[str, Any]:
    """
    Fetches the latest 15m Open Interest and Funding Rate data to calculate momentum.
    """
    if not COINGLASS_API_KEY:
        print(f"[TELEMETRY WARNING] API Key missing. Telemetry offline for {symbol}.")
        return {"status": "OFFLINE", "oi_delta_pct": 0.0, "funding_rate": 0.0}

    cg_symbol = symbol.replace("USDT", "")
    
    headers = {
        "accept": "application/json",
        "CG-API-KEY": COINGLASS_API_KEY
    }

    # We fetch the last two 15m candles to calculate the immediate trajectory (Delta)
    oi_params = {"symbol": cg_symbol, "interval": "15m", "limit": 2}
    fr_params = {"symbol": cg_symbol, "interval": "15m", "limit": 1}

    telemetry = {
        "status": "SUCCESS",
        "symbol": symbol,
        "oi_current": 0.0,
        "oi_delta_pct": 0.0,
        "funding_rate": 0.0,
        "sentiment": "NEUTRAL"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Fetch Open Interest History
            async with session.get(f"{BASE_URL}/futures/openInterest/ohlc-history", params=oi_params, timeout=5) as oi_res:
                if oi_res.status == 200:
                    oi_payload = await oi_res.json()
                    oi_data = oi_payload.get("data", [])
                    
                    if len(oi_data) >= 2:
                        # Extract the 'close' OI from the previous and current 15m candle
                        # Schema is typically [time, open, high, low, close, volume]
                        prev_oi = float(oi_data[-2][4])
                        curr_oi = float(oi_data[-1][4])
                        
                        telemetry["oi_current"] = curr_oi
                        if prev_oi > 0:
                            delta = ((curr_oi - prev_oi) / prev_oi) * 100.0
                            telemetry["oi_delta_pct"] = round(delta, 4)
                            
                        # Basic Sentiment Flagging
                        if telemetry["oi_delta_pct"] > 1.0:
                            telemetry["sentiment"] = "INJECTING_CAPITAL"
                        elif telemetry["oi_delta_pct"] < -1.0:
                            telemetry["sentiment"] = "DRAINING_CAPITAL"
                else:
                    print(f"[TELEMETRY ERROR] OI Fetch Failed: HTTP {oi_res.status}")

            # 2. Fetch Live Funding Rate
            async with session.get(f"{BASE_URL}/futures/fundingRate/ohlc-history", params=fr_params, timeout=5) as fr_res:
                if fr_res.status == 200:
                    fr_payload = await fr_res.json()
                    fr_data = fr_payload.get("data", [])
                    if fr_data:
                        # Extract the closing funding rate
                        telemetry["funding_rate"] = float(fr_data[-1][4])
                else:
                    print(f"[TELEMETRY ERROR] Funding Rate Fetch Failed: HTTP {fr_res.status}")

        return telemetry

    except asyncio.TimeoutError:
        print(f"[TELEMETRY TIMEOUT] CoinGlass API unresponsive for {symbol}.")
        return {"status": "ERROR", "message": "TIMEOUT"}
    except Exception as e:
        print(f"[TELEMETRY EXCEPTION] {str(e)}")
        return {"status": "ERROR", "message": str(e)}