# live_telemetry.py
# ==============================================================================
# KABRODA TELEMETRY ENGINE v2.0 (HYBRID FUEL GAUGE)
# ==============================================================================

import os
import aiohttp
import asyncio
from typing import Dict, Any

COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
BASE_URL = "https://open-api-v4.coinglass.com/api"

def _safe_extract(candle_data: Any) -> float:
    """Safely extracts the closing value whether CoinGlass sends a list or a dict."""
    try:
        if isinstance(candle_data, list) and len(candle_data) > 4:
            return float(candle_data[4])
        elif isinstance(candle_data, dict):
            # Check common CoinGlass V4 dictionary keys for close or value
            return float(candle_data.get("c", candle_data.get("v", candle_data.get("openInterest", 0))))
    except Exception:
        pass
    return 0.0

async def fetch_live_telemetry(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    if not COINGLASS_API_KEY:
        return {"status": "OFFLINE", "oi_delta_pct": 0.0, "funding_rate": 0.0, "fuel_multiplier": 1.0}

    full_symbol = symbol if "USDT" in symbol else f"{symbol}USDT"
    headers = {"accept": "application/json", "CG-API-KEY": COINGLASS_API_KEY}

    oi_params = {"exchange": "Binance", "symbol": full_symbol, "interval": "4h", "limit": 2}
    fr_params = {"exchange": "Binance", "symbol": full_symbol, "interval": "4h", "limit": 1}

    telemetry = {
        "status": "SUCCESS", "symbol": symbol, "oi_delta_pct": 0.0, 
        "funding_rate": 0.0, "fuel_multiplier": 1.0
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Fetch 4H Open Interest
            async with session.get(f"{BASE_URL}/futures/open-interest/history", params=oi_params, timeout=5) as oi_res:
                if oi_res.status == 200:
                    oi_data = (await oi_res.json()).get("data", [])
                    if oi_data and len(oi_data) >= 2:
                        prev_oi = _safe_extract(oi_data[-2])
                        curr_oi = _safe_extract(oi_data[-1])
                        if prev_oi > 0: telemetry["oi_delta_pct"] = ((curr_oi - prev_oi) / prev_oi) * 100.0

            # 2. Fetch 4H Funding Rate
            async with session.get(f"{BASE_URL}/futures/funding-rate/history", params=fr_params, timeout=5) as fr_res:
                if fr_res.status == 200:
                    fr_data = (await fr_res.json()).get("data", [])
                    if fr_data: telemetry["funding_rate"] = _safe_extract(fr_data[-1])

        # CALCULATE FUEL MULTIPLIER
        if telemetry["oi_delta_pct"] > 1.0: telemetry["fuel_multiplier"] = 1.2
        elif telemetry["oi_delta_pct"] < -1.0: telemetry["fuel_multiplier"] = 0.8

        return telemetry

    except Exception as e:
        print(f"[TELEMETRY ERROR] {e}")
        return {"status": "ERROR", "oi_delta_pct": 0.0, "funding_rate": 0.0, "fuel_multiplier": 1.0}