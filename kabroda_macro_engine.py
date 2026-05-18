# kabroda_macro_engine.py
# ==============================================================================
# KABRODA MACRO ENGINE — v5.0 (THE ZIGZAG MATRIX)
# Purpose: Autonomous Macro Elliott Wave Scanner & State Latch
# AUDIT FIX: Replaced Temporal Slicing with pure 20% ZigZag Pivot isolation.
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

# --- KABRODA ARCHITECTURE UPGRADE: 20% ZigZag Engine ---
def _calculate_zigzag_pivots(candles: List[Dict[str, Any]], deviation_pct: float = 0.20) -> List[Dict[str, Any]]:
    """
    Strips noise. Returns an array of pure structural pivots based on a minimum % move.
    """
    if not candles: return []

    pivots = []
    # Initial state
    current_trend = 1 # 1 for Up, -1 for Down
    extreme_price = candles[0]["high"]
    extreme_idx = 0
    extreme_candle = candles[0]

    for i, c in enumerate(candles):
        high = c["high"]
        low = c["low"]

        if current_trend == 1: # Looking for a Top
            if high > extreme_price:
                extreme_price = high
                extreme_idx = i
                extreme_candle = c
            elif low < extreme_price * (1 - deviation_pct):
                # 20% Drop confirmed -> Lock the Top Pivot
                pivots.append({"type": "PEAK", "price": extreme_price, "abs_idx": extreme_idx, "time": extreme_candle["time"]})
                current_trend = -1
                extreme_price = low
                extreme_idx = i
                extreme_candle = c

        elif current_trend == -1: # Looking for a Bottom
            if low < extreme_price:
                extreme_price = low
                extreme_idx = i
                extreme_candle = c
            elif high > extreme_price * (1 + deviation_pct):
                # 20% Bounce confirmed -> Lock the Bottom Pivot
                pivots.append({"type": "TROUGH", "price": extreme_price, "abs_idx": extreme_idx, "time": extreme_candle["time"]})
                current_trend = 1
                extreme_price = high
                extreme_idx = i
                extreme_candle = c
    
    # Push the final unconfirmed extreme
    pivots.append({"type": "PEAK" if current_trend == 1 else "TROUGH", "price": extreme_price, "abs_idx": extreme_idx, "time": extreme_candle["time"]})
    return pivots

def _find_macro_anchors(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candles: return []
    
    for i, c in enumerate(candles):
        c["abs_idx"] = i

    # Phase 1: Build the 20% ZigZag Matrix
    raw_pivots = _calculate_zigzag_pivots(candles, deviation_pct=0.20)
    
    # We will temporarily output the raw ZigZag pivots to verify the engine is isolating noise.
    anchors = []
    for i, p in enumerate(raw_pivots):
        if p["type"] == "PEAK":
            anchors.append({"type": f"ZIGZAG_PEAK_{i}", "price": p["price"]})
        else:
            anchors.append({"type": f"ZIGZAG_TROUGH_{i}", "price": p["price"]})
            
    return anchors

async def run_macro_scan():
    print(">>> MACRO ENGINE: Scanning multi-year SPOT structural anchors (ZIGZAG MATRIX)...")
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
            print(f"|| MACRO ANCHORS LOCKED (SPOT) || {db_sym} | Pivots Mapped: {len(anchors)}")
            
    except Exception as e:
        print(f"Macro Engine Error: {e}")
    finally:
        db.close()
        await _exchange.close()

if __name__ == "__main__":
    asyncio.run(run_macro_scan())