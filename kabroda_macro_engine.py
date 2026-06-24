# kabroda_macro_engine.py
# ==============================================================================
# KABRODA MACRO ENGINE — v6.0 (THE QUANT AXIOM ENGINE)
# Purpose: Autonomous Macro Elliott Wave Scanner & State Latch
# AUDIT FIX: Phase 1 (ZigZag Matrix) integrated with Phase 2 (Axiom Validator).
# Enforces strict Elliott Wave rules on structural pivots to map pure sequences.
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
            if not rows: break 
                
            formatted = [{"time": int(r[0] / 1000), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]
            all_candles.extend(formatted)
            since_ts = int(rows[-1][0]) + 1
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Macro Pagination Error for {symbol}: {e}")
            break
            
    return all_candles[-target_days:]

def _calculate_zigzag_pivots(candles: List[Dict[str, Any]], deviation_pct: float = 0.20) -> List[Dict[str, Any]]:
    """Phase 1: Strips daily noise. Returns pure 20% structural pivots."""
    if not candles: return []

    pivots = []
    current_trend = 1 
    extreme_price = candles[0]["high"]
    extreme_idx = 0

    for i, c in enumerate(candles):
        high = c["high"]
        low = c["low"]

        if current_trend == 1: 
            if high > extreme_price:
                extreme_price = high
                extreme_idx = i
            elif low < extreme_price * (1 - deviation_pct):
                pivots.append({"type": "PEAK", "price": extreme_price, "abs_idx": extreme_idx})
                current_trend = -1
                extreme_price = low
                extreme_idx = i

        elif current_trend == -1: 
            if low < extreme_price:
                extreme_price = low
                extreme_idx = i
            elif high > extreme_price * (1 + deviation_pct):
                pivots.append({"type": "TROUGH", "price": extreme_price, "abs_idx": extreme_idx})
                current_trend = 1
                extreme_price = high
                extreme_idx = i
    
    return pivots

def _find_macro_anchors(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candles: return []
    
    for i, c in enumerate(candles):
        c["abs_idx"] = i

    # Phase 1: Raw Pivots
    raw_pivots = _calculate_zigzag_pivots(candles, deviation_pct=0.20)
    
    if len(raw_pivots) < 4: return [] # Need structure to map waves

    # Absolute Extremes (Wave 0 and Wave 5)
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

    # --- Phase 2: Elliott Wave Axiom Validator (Bull Run) ---
    bull_pivots = [p for p in raw_pivots if origin_idx < p["abs_idx"] < top_idx]
    peaks = [p for p in bull_pivots if p["type"] == "PEAK"]
    troughs = [p for p in bull_pivots if p["type"] == "TROUGH"]

    if len(peaks) >= 2 and len(troughs) >= 2:
        # Axiom 1: Find W4 (Lowest structural trough in upper half of run)
        mid_price = cycle_origin_price + ((cycle_top_price - cycle_origin_price) * 0.5)
        upper_troughs = [t for t in troughs if t["price"] > mid_price]
        
        if upper_troughs:
            w4 = min(upper_troughs, key=lambda t: t["price"])
            
            # Axiom 2: Find W3 (Highest peak before W4)
            valid_w3 = [p for p in peaks if p["abs_idx"] < w4["abs_idx"]]
            if valid_w3:
                w3 = max(valid_w3, key=lambda p: p["price"])
                
                # Axiom 3: Find W2 (Lowest trough before W3)
                valid_w2 = [t for t in troughs if t["abs_idx"] < w3["abs_idx"]]
                if valid_w2:
                    w2 = min(valid_w2, key=lambda t: t["price"])
                    
                    # Axiom 4: Find W1 (Highest peak before W2)
                    valid_w1 = [p for p in peaks if p["abs_idx"] < w2["abs_idx"]]
                    if valid_w1:
                        w1 = max(valid_w1, key=lambda p: p["price"])
                        
                        # INVIOLABLE ELLIOTT WAVE RULES CHECK:
                        # 1. W4 cannot overlap W1 territory
                        # 2. W2 cannot break Origin
                        if w4["price"] > w1["price"] and w2["price"] > cycle_origin_price:
                            anchors.extend([
                                {"type": "BULL_WAVE_1", "price": w1["price"]},
                                {"type": "BULL_WAVE_2", "price": w2["price"]},
                                {"type": "BULL_WAVE_3", "price": w3["price"]},
                                {"type": "BULL_WAVE_4", "price": w4["price"]}
                            ])

    # --- Phase 2: Elliott Wave Axiom Validator (Bear Run) ---
    bear_pivots = [p for p in raw_pivots if p["abs_idx"] > top_idx]
    bear_peaks = [p for p in bear_pivots if p["type"] == "PEAK"]
    bear_troughs = [p for p in bear_pivots if p["type"] == "TROUGH"]

    if bear_troughs:
        # Bear W3 is absolute lowest point of bear market so far
        bear_w3 = min(bear_troughs, key=lambda t: t["price"])
        anchors.append({"type": "BEAR_WAVE_3_LOW", "price": bear_w3["price"]})

        # Bear W4 is the highest bounce AFTER W3
        valid_w4 = [p for p in bear_peaks if p["abs_idx"] > bear_w3["abs_idx"]]
        if valid_w4:
            bear_w4 = max(valid_w4, key=lambda p: p["price"])
            anchors.append({"type": "BEAR_WAVE_4_BOUNCE", "price": bear_w4["price"]})

        # Bear W1 & W2 (Before W3)
        valid_w2 = [p for p in bear_peaks if p["abs_idx"] < bear_w3["abs_idx"]]
        if valid_w2:
            bear_w2 = max(valid_w2, key=lambda p: p["price"])
            
            valid_w1 = [t for t in bear_troughs if t["abs_idx"] < bear_w2["abs_idx"]]
            if valid_w1:
                bear_w1 = min(valid_w1, key=lambda t: t["price"])
                
                # Axiom Check: W2 must be lower than Top
                if bear_w2["price"] < cycle_top_price:
                    anchors.extend([
                        {"type": "BEAR_WAVE_1_MSB", "price": bear_w1["price"]},
                        {"type": "BEAR_WAVE_2", "price": bear_w2["price"]}
                    ])

    return anchors


def _compute_weekly_200sma(daily_candles: List[Dict[str, Any]]) -> float:
    """
    Resample daily candles to weekly (anchor: UTC Monday open), then return
    the simple moving average of the last 200 completed weekly closes.
    Requires at least 1400 daily candles (≈200 weeks of 5 trading days each).
    Returns 0.0 if insufficient data.
    """
    if len(daily_candles) < 1400:
        return 0.0

    # Group by ISO week (year, week_number) — last candle in each group is the weekly close
    weeks: dict = {}
    for c in daily_candles:
        dt = datetime.fromtimestamp(c["time"], tz=timezone.utc)
        year, week, _ = dt.isocalendar()
        key = (year, week)
        if key not in weeks:
            weeks[key] = c["close"]
        else:
            weeks[key] = c["close"]  # overwrite — last daily candle in week wins

    sorted_closes = [weeks[k] for k in sorted(weeks.keys())]
    if len(sorted_closes) < 200:
        return 0.0

    return sum(sorted_closes[-200:]) / 200.0


async def run_macro_scan():
    print(">>> MACRO ENGINE: Scanning multi-year SPOT structural anchors (AXIOM VALIDATOR)...")
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
            print(f"|| MACRO ANCHORS LOCKED (SPOT) || {db_sym} | Exact Waves Mapped: {len(anchors)}")

            # ── WEEKLY 200 SMA (stored as active=False so KDE ignores it) ──
            # Queried by _fetch_weekly_200sma() in battlebox_pipeline at lock time.
            try:
                sma_200w = _compute_weekly_200sma(daily_data)
                if sma_200w > 0:
                    db.query(GravityMemory).filter(
                        GravityMemory.symbol == db_sym,
                        GravityMemory.source == "WEEKLY_200_SMA",
                    ).delete()
                    db.add(GravityMemory(
                        symbol=db_sym,
                        timestamp=now_utc,
                        source="WEEKLY_200_SMA",
                        level_type="WEEKLY_200_SMA_REFERENCE",
                        price=sma_200w,
                        permanence_class=2,
                        heat_multiplier=0.0,
                        active=False,  # invisible to KDE — reference value only
                    ))
                    db.commit()
                    print(f"|| WEEKLY 200 SMA || {db_sym} | {sma_200w:.2f}")
            except Exception as sma_err:
                print(f"|| WEEKLY 200 SMA ERROR || {db_sym}: {sma_err}")

    except Exception as e:
        print(f"Macro Engine Error: {e}")
    finally:
        db.close()
        await _exchange.close()

if __name__ == "__main__":
    asyncio.run(run_macro_scan())