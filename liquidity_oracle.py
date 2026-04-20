# liquidity_oracle.py
# ==============================================================================
# KABRODA CUSTOM LIQUIDITY ORACLE v3.0 (MACRO DEPTH ENGINE)
# ==============================================================================
# Purpose: Pulls the Public Binance USDT-M Futures Order Book (1000 levels).
# Bypasses US Geofence (no keys required for public REST data).
# Solves the "Microscope Squeeze" by providing massive price depth.
# ==============================================================================

import ccxt.async_support as ccxt
import traceback
from typing import Dict, Any

def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s in ("SOL", "SOLUSDT"): return "SOL/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

async def fetch_liquidation_magnets(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    s = _normalize_symbol(symbol)
    
    # We use binanceusdm (Binance USDT-M Futures) public endpoint
    exchange = ccxt.binanceusdm({'enableRateLimit': True})
    
    try:
        # Binance public API allows up to 1000 limit depth without keys
        # This provides a massive macro field of vision
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
        print(f"[CUSTOM ORACLE ERROR] Failed to fetch order book for {s}: {e}")
        traceback.print_exc()
        return {"status": "ERROR", "message": str(e), "raw_data": {}}
    finally:
        await exchange.close()