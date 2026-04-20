# live_telemetry.py
# ==============================================================================
# KABRODA TELEMETRY ENGINE v3.0 (COINALYZE FUEL GAUGE)
# ==============================================================================
import os
import aiohttp
import asyncio
from typing import Dict, Any

COINALYZE_API_KEY = os.getenv("COINALYZE_API_KEY")
BASE_URL = "https://api.coinalyze.net/v1"

async def fetch_live_telemetry(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    if not COINALYZE_API_KEY:
        return {"status": "OFFLINE", "oi_delta_pct": 0.0, "fuel_multiplier": 1.0}

    # Coinalyze expects a specific format, e.g., BTCUSDT_PERP.A for Binance Futures
    cx_symbol = f"{symbol.replace('USDT', '')}USDT_PERP.A"
    headers = {"api_key": COINALYZE_API_KEY}

    telemetry = {"status": "SUCCESS", "symbol": symbol, "oi_delta_pct": 0.0, "fuel_multiplier": 1.0}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # Fetch Open Interest History (Daily interval)
            params = {"symbols": cx_symbol, "interval": "daily"}
            async with session.get(f"{BASE_URL}/open-interest-history", params=params, timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    if data and isinstance(data, list) and "history" in data[0]:
                        history = data[0]["history"]
                        if len(history) >= 2:
                            prev_oi = float(history[-2]["c"]) # Previous daily close
                            curr_oi = float(history[-1]["c"]) # Current daily close
                            if prev_oi > 0:
                                telemetry["oi_delta_pct"] = ((curr_oi - prev_oi) / prev_oi) * 100.0

        # CALCULATE FUEL: If daily OI is rising, momentum is strong
        if telemetry["oi_delta_pct"] > 0.5: telemetry["fuel_multiplier"] = 1.2
        elif telemetry["oi_delta_pct"] < -0.5: telemetry["fuel_multiplier"] = 0.8

        return telemetry
    except Exception as e:
        print(f"[TELEMETRY ERROR] Coinalyze Fetch Failed: {e}")
        return {"status": "ERROR", "oi_delta_pct": 0.0, "fuel_multiplier": 1.0}