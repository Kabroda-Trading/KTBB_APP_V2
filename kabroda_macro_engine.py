# kabroda_macro_engine.py
# ==============================================================================
# KABRODA MACRO ENGINE — v1.3
# Purpose: Autonomous Macro Elliott Wave Scanner & State Latch
# AUDIT FIX: Engineered Time-Stitched Pagination to bypass 1000-candle API limit.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
import ccxt.async_support as ccxt
from database import SessionLocal, GravityMemory

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# Single Source of Truth: SPOT Market
_exchange = ccxt.mexc({
    "enableRateLimit": True
})

async def fetch_historical_daily_macro(symbol: str, target_days: int = 1500) -> List[Dict[str, Any]]:
    """
    Bypasses the MEXC 1000-candle hard limit using Time-Stitched Pagination.
    Recursively fetches chunks until the full 1500-day history is built.
    """
    limit_per_call = 1000  # MEXC Hard Limit
    all_candles = []
    
    # Start the clock exactly 1500 days ago
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ts = now_ts - (target_days * 86400 * 1000)
    
    while len(all_candles) < target_days:
        try:
            # Request up to 1000 candles starting from 'since_ts'
            rows = await _exchange.fetch_ohlcv(symbol, "1d", since=since_ts, limit=limit_per_call)
            
            if not rows:
                break  # Exit if no more data is returned
                
            formatted = [{"time": int(r[0] / 1000), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]
            all_candles.extend(formatted)
            
            # Move the timestamp forward to the last received candle + 1 millisecond
            since_ts = int(rows[-1][0]) + 1
            
            # Brief pause to respect API rate limits
            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"Macro Pagination Error for {symbol}: {e}")
            break
            
    # Ensure we strictly return the target amount
    return all_candles[-target_days:]

def _find_macro_anchors(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candles: return []
    
    # 1. Find Cycle Top (Wave 5)
    highest_candle = max(candles, key=lambda c: c["high"])
    cycle_top_price = highest_candle["high"]
    cycle_top_idx = candles.index(highest_candle)
    
    # 2. Find Cycle Origin (Wave 0 - Lowest point before the top)
    pre_top_candles = candles[:cycle_top_idx+1]
    lowest_candle = min(pre_top_candles, key=lambda c: c["low"])
    cycle_origin_price = lowest_candle["low"]
    
    # 3. Find Post-Top Bear Structure (Wave A/1 Bottom)
    post_top_candles = candles[cycle_top_idx+1:]
    bear_floor_price = 0.0
    if post_top_candles:
        bear_floor_candle = min(post_top_candles, key=lambda c: c["low"])
        bear_floor_price = bear_floor_candle["low"]

    anchors = [
        {"type": "CYCLE_ORIGIN", "price": cycle_origin_price},
        {"type": "CYCLE_TOP", "price": cycle_top_price}
    ]
    
    if bear_floor_price > 0:
        anchors.append({"type": "BEAR_FLOOR_MSB", "price": bear_floor_price})
        
    return anchors

async def run_macro_scan():
    print(">>> MACRO ENGINE: Scanning multi-year SPOT structural anchors...")
    db = SessionLocal()
    try:
        for symbol in TARGETS:
            db_sym = symbol.replace("/", "")
            # Calling our new paginated fetcher
            daily_data = await fetch_historical_daily_macro(symbol, target_days=1500)
            anchors = _find_macro_anchors(daily_data)
            
            # Safety check: Skip database write if we didn't get enough data
            if len(anchors) < 2:
                print(f"|| MACRO ENGINE WARNING || {db_sym} | Insufficient data. Skipping.")
                continue

            now_utc = datetime.now(timezone.utc)
            
            # Clear old macro anchors to prevent duplication
            db.query(GravityMemory).filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "MACRO_ENGINE_CLASS_0"
            ).delete()
            
            for anchor in anchors:
                mem = GravityMemory(
                    symbol=db_sym, 
                    timestamp=now_utc, 
                    source="MACRO_ENGINE_CLASS_0",
                    level_type=anchor["type"], 
                    price=anchor["price"],
                    permanence_class=0, # CLASS 0: Titanium Structure
                    heat_multiplier=15.0 # Massive weight for Gravity Math
                )
                db.add(mem)
            
            db.commit()
            print(f"|| MACRO ANCHORS LOCKED (SPOT) || {db_sym} | History: {len(daily_data)} Days | Top: {anchors[1]['price']}")
            
    except Exception as e:
        print(f"Macro Engine Error: {e}")
    finally:
        db.close()
        await _exchange.close()

if __name__ == "__main__":
    asyncio.run(run_macro_scan())