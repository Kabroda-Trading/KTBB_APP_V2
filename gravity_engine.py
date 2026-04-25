# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION)
# ==============================================================================
import asyncio
import traceback
from datetime import datetime, timezone
import ccxt.async_support as ccxt
from database import SessionLocal, GravityMemory

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
_exchange = ccxt.binance({"enableRateLimit": True})

def _calculate_average_volume(candles, current_idx, period=20):
    if current_idx < period:
        return 1.0
    vols = [float(c[5]) for c in candles[current_idx-period : current_idx]]
    return sum(vols) / len(vols) if vols else 1.0

def _scan_for_pivots(symbol, candles, timeframe, left=3, right=3):
    """
    STRICT 7-CANDLE GEOMETRY.
    Will NOT confirm until `right` number of candles have fully closed.
    """
    pivots_found = []
    
    # Ensure we have enough data to check the right-side closure
    if len(candles) < left + right + 1:
        return pivots_found

    for i in range(left, len(candles) - right):
        ch = float(candles[i][2]) # High
        cl = float(candles[i][3]) # Low
        ts = int(candles[i][0] / 1000)
        vol = float(candles[i][5])
        
        # Check Supply (Top)
        is_supply = all(float(candles[i - j][2]) <= ch for j in range(1, left + 1)) and \
                    all(float(candles[i + j][2]) < ch for j in range(1, right + 1))
                    
        # Check Demand (Bottom)
        is_demand = all(float(candles[i - j][3]) >= cl for j in range(1, left + 1)) and \
                    all(float(candles[i + j][3]) > cl for j in range(1, right + 1))

        if is_supply or is_demand:
            avg_vol = _calculate_average_volume(candles, i)
            multiplier = 2.0 if (vol > avg_vol * 2.0) else 1.0
            
            p_class = 1 if timeframe == "4h" else 2
            
            if is_supply:
                pivots_found.append({"ts": ts, "price": ch, "type": "SUPPLY", "class": p_class, "heat": multiplier})
            if is_demand:
                pivots_found.append({"ts": ts, "price": cl, "type": "DEMAND", "class": p_class, "heat": multiplier})
                
    return pivots_found

async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background ingestion loop...")
    while True:
        db = SessionLocal()
        try:
            for symbol in TARGETS:
                db_sym = symbol.replace("/", "")
                
                # Fetch recent candles to find newly locked pivots
                candles_4h = await _exchange.fetch_ohlcv(symbol, "4h", limit=50)
                candles_1h = await _exchange.fetch_ohlcv(symbol, "1h", limit=50)
                
                new_pivots = []
                new_pivots.extend(_scan_for_pivots(db_sym, candles_4h, "4h"))
                new_pivots.extend(_scan_for_pivots(db_sym, candles_1h, "1h"))
                
                for p in new_pivots:
                    dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
                    src = "4H_PIVOT" if p["class"] == 1 else "1H_PIVOT"
                    
                    # Prevent duplicate logging of the exact same structural scar
                    exists = db.query(GravityMemory).filter(
                        GravityMemory.symbol == db_sym,
                        GravityMemory.timestamp == dt,
                        GravityMemory.source == src
                    ).first()
                    
                    if not exists:
                        mem = GravityMemory(
                            symbol=db_sym,
                            timestamp=dt,
                            source=src,
                            level_type=p["type"],
                            price=p["price"],
                            permanence_class=p["class"],
                            heat_multiplier=p["heat"]
                        )
                        db.add(mem)
                        db.commit()
                        print(f"|| GRAVITY LOGGED || {db_sym} | {src} {p['type']} @ ${p['price']} | Heat: {p['heat']}")
                        
        except Exception as e:
            traceback.print_exc()
        finally:
            db.close()
            
        # Wait 15 minutes before sweeping the exchange again
        await asyncio.sleep(900)