# liquidity_oracle.py
# ==============================================================================
# KABRODA CUSTOM LIQUIDITY ORACLE v2.1 (STABLE L2 ENGINE)
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
    exchange = ccxt.kucoin({'enableRateLimit': True})
    
    try:
        # KuCoin strictly requires limit=100
        orderbook = await exchange.fetch_order_book(s, limit=100)
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
        return {"status": "ERROR", "message": str(e), "raw_data": {}}
    finally:
        await exchange.close()