# kabroda_macro_engine.py
# ==============================================================================
# KABRODA MACRO ENGINE — v3.0
# Purpose: Autonomous Macro Elliott Wave Scanner & State Latch
# AUDIT FIX: Replaced list.index() with Absolute Global Indices to prevent 
# duplicate-price timestamp collisions over the 4-year dataset.
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
    
    # INJECT ABSOLUTE INDEX to prevent .index() collision
    for i, c in enumerate(candles):
        c["abs_idx"] = i
    
    # --- 1. CYCLE EXTREMES ---
    highest_candle = max(candles, key=lambda c: c["high"])
    cycle_top_price = highest_candle["high"]
    top_idx = highest_candle["abs_idx"]
    
    pre_top_candles = candles[:top_idx+1]
    lowest_candle = min(pre_top_candles, key=lambda c: c["low"])
    cycle_origin_price = lowest_candle["low"]
    origin_idx = lowest_candle["abs_idx"]
    
    anchors = [
        {"type": "CYCLE_ORIGIN", "price": cycle_origin_price},
        {"type": "CYCLE_TOP", "price": cycle_top_price}
    ]

    # --- 2. BULL RUN SUB-ROUTINE ---
    bull_length = top_idx - origin_idx
    if bull_length > 30: 
        mid_idx = origin_idx + (bull_length // 2)
        
        w4_candle = min(candles[mid_idx:top_idx], key=lambda c: c["low"])
        w4_idx = w4_candle["abs_idx"]
        
        if w4_idx > origin_idx:
            w3_candle = max(candles[origin_idx:w4_idx], key=lambda c: c["high"])
            w3_idx = w3_candle["abs_idx"]
            
            if w3_idx > origin_idx:
                w2_candle = min(candles[origin_idx:w3_idx], key=lambda c: c["low"])
                w2_idx = w2_candle["abs_idx"]
                
                if w2_idx > origin_idx:
                    w1_candle = max(candles[origin_idx:w2_idx], key=lambda c: c["high"])
                    
                    anchors.extend([
                        {"type": "BULL_WAVE_1", "price": w1_candle["high"]},
                        {"type": "BULL_WAVE_2", "price": w2_candle["low"]},
                        {"type": "BULL_WAVE_3", "price": w3_candle["high"]},
                        {"type": "BULL_WAVE_4", "price": w4_candle["low"]}
                    ])

    # --- 3. BEAR RUN SUB-ROUTINE ---
    post_top = candles[top_idx+1:]
    if post_top:
        bear_lowest_candle = min(post_top, key=lambda c: c["low"])
        bear_lowest_idx = bear_lowest_candle["abs_idx"]
        
        anchors.append({"type": "BEAR_WAVE_3", "price": bear_lowest_candle["low"]})
        
        post_lowest = candles[bear_lowest_idx+1:]
        if post_lowest:
            bear_w4_candle = max(post_lowest, key=lambda c: c["high"])
            anchors.append({"type": "BEAR_WAVE_4", "price": bear_w4_candle["high"]})
            
        pre_lowest = candles[top_idx+1:bear_lowest_idx]
        if len(pre_lowest) > 2: 
            bear_w2_candle = max(pre_lowest, key=lambda c: c["high"])
            bear_w2_idx = bear_w2_candle["abs_idx"]
            
            pre_w2 = candles[top_idx+1:bear_w2_idx]
            if pre_w2:
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