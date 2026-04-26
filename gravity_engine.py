# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION & BEDROCK LOGGING)
# ==============================================================================
import asyncio
import traceback
import os
from datetime import datetime, timezone
import ccxt.async_support as ccxt
from database import SessionLocal, GravityMemory

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# ----------------------------------------------------------------------
# Exchange (MEXC - No Proxy Required)
# ----------------------------------------------------------------------
_exchange = ccxt.mexc({"enableRateLimit": True})

def log_kabroda_bedrock(symbol: str, levels: dict, lock_ts: int):
    """Logs the 7-Day macro anchors (Daily S/R, 30m boundaries, Triggers)."""
    db = SessionLocal()
    try:
        db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
        dt = datetime.fromtimestamp(lock_ts, tz=timezone.utc)
        
        exists = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.timestamp == dt,
            GravityMemory.source == "7_DAY_KABRODA"
        ).first()
        
        if exists: 
            return
        
        mapping = {
            "BREAKOUT": levels.get("breakout_trigger", 0),
            "BREAKDOWN": levels.get("breakdown_trigger", 0),
            "DAILY_RESISTANCE": levels.get("daily_resistance", 0),
            "DAILY_SUPPORT": levels.get("daily_support", 0),
            "30M_HIGH": levels.get("range30m_high", 0),
            "30M_LOW": levels.get("range30m_low", 0),
        }
        
        for l_type, price in mapping.items():
            if float(price) > 0:
                mem = GravityMemory(
                    symbol=db_sym,
                    timestamp=dt,
                    source="7_DAY_KABRODA",
                    level_type=l_type,
                    price=float(price),
                    permanence_class=2,
                    heat_multiplier=1.0
                )
                db.add(mem)
                
        db.commit()
        print(f"|| GRAVITY BEDROCK LOGGED || {db_sym} | 6 Daily Levels Locked")
        
    except Exception as e:
        traceback.print_exc()
    finally:
        db.close()


def _calculate_average_volume(candles, current_idx, period=20):
    if current_idx < period:
        return 1.0
    vols = [float(c[5]) for c in candles[current_idx-period : current_idx]]
    return sum(vols) / len(vols) if vols else 1.0


def _scan_for_pivots(symbol, candles, timeframe, left=3, right=3):
    """
    PineScript Parity: Requires fully closed candles to confirm structural pivots.
    """
    pivots_found = []
    
    # ARCHITECTURAL FIX: Strip the last candle because it is actively forming.
    # We only calculate pivots on completely closed market data.
    closed_candles = candles[:-1]
    
    if len(closed_candles) < left + right + 1:
        return pivots_found

    for i in range(left, len(closed_candles) - right):
        ch = float(closed_candles[i][2]) 
        cl = float(closed_candles[i][3]) 
        ts = int(closed_candles[i][0] / 1000)
        vol = float(closed_candles[i][5])
        
        is_supply = all(float(closed_candles[i - j][2]) <= ch for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j][2]) < ch for j in range(1, right + 1))
                    
        is_demand = all(float(closed_candles[i - j][3]) >= cl for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j][3]) > cl for j in range(1, right + 1))

        if is_supply or is_demand:
            avg_vol = _calculate_average_volume(closed_candles, i)
            # Heat modifier: Volume spikes increase gravitational pull
            multiplier = 2.0 if (vol > avg_vol * 2.0) else 1.0
            p_class = 1 if timeframe == "4h" else 2
            
            if is_supply:
                pivots_found.append({"ts": ts, "price": ch, "type": "SUPPLY", "class": p_class, "heat": multiplier})
            if is_demand:
                pivots_found.append({"ts": ts, "price": cl, "type": "DEMAND", "class": p_class, "heat": multiplier})
                
    return pivots_found


async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background ingestion loop (STRICT MODE)...")
    while True:
        db = SessionLocal()
        try:
            for symbol in TARGETS:
                db_sym = symbol.replace("/", "")
                
                # Fetch recent candles
                candles_4h = await _exchange.fetch_ohlcv(symbol, "4h", limit=50)
                candles_1h = await _exchange.fetch_ohlcv(symbol, "1h", limit=50)
                
                new_pivots = []
                new_pivots.extend(_scan_for_pivots(db_sym, candles_4h, "4h"))
                new_pivots.extend(_scan_for_pivots(db_sym, candles_1h, "1h"))
                
                for p in new_pivots:
                    dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
                    src = "4H_PIVOT" if p["class"] == 1 else "1H_PIVOT"
                    
                    # Single Source of Truth DB check
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
                        print(f"|| GRAVITY BEDROCK || {db_sym} | {src} {p['type']} @ ${p['price']} LOCKED. Heat: {p['heat']}")
                        
        except Exception as e:
            print(f"Gravity Engine Iteration Error: {e}")
        finally:
            db.close()
            
        # Run sweeps every 15 minutes to catch the HTF closes right as they happen
        await asyncio.sleep(900)