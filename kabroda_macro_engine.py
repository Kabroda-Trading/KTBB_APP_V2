# kabroda_macro_engine.py
# ==============================================================================
# KABRODA MACRO ENGINE — v2.0
# Purpose: Autonomous Macro Elliott Wave Scanner & State Latch
# AUDIT FIX: Engineered Time-Stitched Pagination (Spot 1D).
# AUDIT FIX: Complete internal sub-routines for BOTH Bull and Bear Runs.
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
    limit_per_call = 1000  
    all_candles = []
    
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ts = now_ts - (target_days * 86400 * 1000)
    
    while len(all_candles) < target_days:
        try:
            rows = await _exchange.fetch_ohlcv(symbol, "1d", since=since_ts, limit=limit_per_call)
            if not rows:
                break 
                
            formatted = [{"time": int(r[0] / 1000), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]
            all_candles.extend(formatted)
            
            since_ts = int(rows[-1][0]) + 1
            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"Macro Pagination Error for {symbol}: {e}")
            break
            
    return all_candles[-target_days:]

def _find_macro_anchors(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candles: return []
    
    # --- 1. CYCLE EXTREMES ---
    highest_candle = max(candles, key=lambda c: c["high"])
    cycle_top_price = highest_candle["high"]
    top_idx = candles.index(highest_candle)
    
    pre_top_candles = candles[:top_idx+1]
    lowest_candle = min(pre_top_candles, key=lambda c: c["low"])
    cycle_origin_price = lowest_candle["low"]
    origin_idx = candles.index(lowest_candle)
    
    anchors = [
        {"type": "CYCLE_ORIGIN", "price": cycle_origin_price},
        {"type": "CYCLE_TOP", "price": cycle_top_price}
    ]

    # --- 2. BULL RUN SUB-ROUTINE (W0 to W5) ---
    bull_run = candles[origin_idx:top_idx+1]
    if len(bull_run) > 30: 
        midpoint_idx = len(bull_run) // 2
        
        # WAVE 4: Deepest valley in the second half of the run
        w4_candle = min(bull_run[midpoint_idx:], key=lambda c: c["low"])
        w4_idx = bull_run.index(w4_candle)
        
        if w4_idx > 0:
            # WAVE 3: Highest peak between Origin and Wave 4
            w3_candle = max(bull_run[:w4_idx], key=lambda c: c["high"])
            w3_idx = bull_run.index(w3_candle)
            
            if w3_idx > 0:
                # WAVE 2: Deepest valley between Origin and Wave 3
                w2_candle = min(bull_run[:w3_idx], key=lambda c: c["low"])
                w2_idx = bull_run.index(w2_candle)
                
                if w2_idx > 0:
                    # WAVE 1: Highest peak between Origin and Wave 2
                    w1_candle = max(bull_run[:w2_idx], key=lambda c: c["high"])
                    
                    anchors.extend([
                        {"type": "BULL_WAVE_1", "price": w1_candle["high"]},
                        {"type": "BULL_WAVE_2", "price": w2_candle["low"]},
                        {"type": "BULL_WAVE_3", "price": w3_candle["high"]},
                        {"type": "BULL_WAVE_4", "price": w4_candle["low"]}
                    ])

    # --- 3. BEAR RUN SUB-ROUTINE (Post-Top to Present) ---
    post_top = candles[top_idx+1:]
    if post_top:
        # The absolute lowest point so far in the bear market
        bear_lowest_candle = min(post_top, key=lambda c: c["low"])
        bear_lowest_idx = candles.index(bear_lowest_candle)
        
        # In an ongoing bear trend, the absolute lowest is Wave 3 Floor
        anchors.append({"type": "BEAR_WAVE_3", "price": bear_lowest_candle["low"]})
        
        # BEAR WAVE 4: The bounce AFTER the lowest point
        post_lowest = candles[bear_lowest_idx+1:]
        if post_lowest:
            bear_w4_candle = max(post_lowest, key=lambda c: c["high"])
            anchors.append({"type": "BEAR_WAVE_4", "price": bear_w4_candle["high"]})
            
        # BEAR WAVES 1 & 2: The structure BEFORE the lowest point
        pre_lowest = candles[top_idx+1:bear_lowest_idx]
        if len(pre_lowest) > 10: 
            # Wave 2 is the highest bounce before the Wave 3 floor
            bear_w2_candle = max(pre_lowest, key=lambda c: c["high"])
            bear_w2_idx = candles.index(bear_w2_candle)
            
            pre_w2 = candles[top_idx+1:bear_w2_idx]
            if pre_w2:
                # Wave 1 is the initial structural breakdown
                bear_w1_candle = min(pre_w2, key=lambda c: c["low"])
                anchors.extend([
                    {"type": "BEAR_WAVE_1_MSB", "price": bear_w1_candle["low"]},
                    {"type": "BEAR_WAVE_2", "price": bear_w2_candle["high"]}
                ])
                
    return anchors

async def run_macro_scan():
    print(">>> MACRO ENGINE: Scanning multi-year SPOT structural anchors...")
    db = SessionLocal()
    try:
        for symbol in TARGETS:
            db_sym = symbol.replace("/", "")
            daily_data = await fetch_historical_daily_macro(symbol, target_days=1500)
            anchors = _find_macro_anchors(daily_data)
            
            if len(anchors) < 2:
                print(f"|| MACRO ENGINE WARNING || {db_sym} | Insufficient data. Skipping.")
                continue

            now_utc = datetime.now(timezone.utc)
            
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
                    permanence_class=0, 
                    heat_multiplier=15.0 
                )
                db.add(mem)
            
            db.commit()
            print(f"|| MACRO ANCHORS LOCKED (SPOT) || {db_sym} | Anchors Mapped: {len(anchors)}")
            
    except Exception as e:
        print(f"Macro Engine Error: {e}")
    finally:
        db.close()
        await _exchange.close()

if __name__ == "__main__":
    asyncio.run(run_macro_scan())