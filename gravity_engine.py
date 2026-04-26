# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION & BEDROCK LOGGING)
# ==============================================================================
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from database import SessionLocal, GravityMemory
import battlebox_pipeline  # <-- SINGLE SOURCE OF TRUTH ENFORCED

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

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
                    symbol=db_sym, timestamp=dt, source="7_DAY_KABRODA",
                    level_type=l_type, price=float(price),
                    permanence_class=2, heat_multiplier=1.0
                )
                db.add(mem)
                
        db.commit()
        print(f"|| GRAVITY BEDROCK LOGGED || {db_sym} | 6 Daily Levels Locked")
        
    except Exception as e:
        traceback.print_exc()
    finally:
        db.close()

def log_radar_anchors(symbol: str, raw_daily: List[Dict[str, Any]], raw_1h: List[Dict[str, Any]]):
    """Logs Macro (Weekly) and Micro (168H) trend anchor prices."""
    db = SessionLocal()
    try:
        db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
        now_utc = datetime.now(timezone.utc)
        
        # 1. MACRO ANCHOR (Last Sunday's Close)
        if raw_daily:
            days_since_sunday = (now_utc.weekday() + 1) % 7
            if days_since_sunday == 0: days_since_sunday = 7
            
            last_sunday_dt = now_utc - timedelta(days=days_since_sunday)
            last_sunday_dt = last_sunday_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            target_ts = int(last_sunday_dt.timestamp())
            
            macro_candle = next((c for c in raw_daily if int(c["time"]) <= target_ts), None)
            if macro_candle:
                macro_price = float(macro_candle["close"])
                
                exists = db.query(GravityMemory).filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.timestamp == last_sunday_dt,
                    GravityMemory.source == "1W_MACRO_ANCHOR"
                ).first()
                
                if not exists:
                    db.add(GravityMemory(
                        symbol=db_sym, timestamp=last_sunday_dt, source="1W_MACRO_ANCHOR",
                        level_type="MACRO_LINE", price=macro_price, permanence_class=1, heat_multiplier=5.0
                    ))
                    print(f"|| GRAVITY MACRO || {db_sym} | Weekly Anchor @ ${macro_price} LOCKED.")

        # 2. MICRO ANCHOR (168H Rolling)
        if raw_1h and len(raw_1h) >= 168:
            micro_candle = raw_1h[-168]
            micro_price = float(micro_candle["close"])
            micro_dt = datetime.fromtimestamp(int(micro_candle["time"]), tz=timezone.utc)
            
            exists = db.query(GravityMemory).filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.timestamp == micro_dt,
                GravityMemory.source == "168H_MICRO_ANCHOR"
            ).first()
            
            if not exists:
                db.add(GravityMemory(
                    symbol=db_sym, timestamp=micro_dt, source="168H_MICRO_ANCHOR",
                    level_type="MICRO_LINE", price=micro_price, permanence_class=2, heat_multiplier=3.0
                ))
                print(f"|| GRAVITY MICRO || {db_sym} | 168H Rolling Anchor @ ${micro_price} LOCKED.")

        db.commit()
    except Exception as e:
        print(f"Radar Anchor Logging Error: {e}")
    finally:
        db.close()


def _calculate_average_volume(candles: List[Dict[str, Any]], current_idx: int, period=20) -> float:
    if current_idx < period:
        return 1.0
    vols = [float(c["volume"]) for c in candles[current_idx-period : current_idx]]
    return sum(vols) / len(vols) if vols else 1.0


def _scan_for_pivots(symbol: str, candles: List[Dict[str, Any]], timeframe: str, left=3, right=3):
    """PineScript Parity: Requires fully closed candles to confirm structural pivots."""
    pivots_found = []
    
    # Strip the last candle because it is actively forming.
    closed_candles = candles[:-1]
    
    if len(closed_candles) < left + right + 1:
        return pivots_found

    for i in range(left, len(closed_candles) - right):
        ch = float(closed_candles[i]["high"]) 
        cl = float(closed_candles[i]["low"]) 
        ts = int(closed_candles[i]["time"])
        vol = float(closed_candles[i]["volume"])
        
        is_supply = all(float(closed_candles[i - j]["high"]) <= ch for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j]["high"]) < ch for j in range(1, right + 1))
                    
        is_demand = all(float(closed_candles[i - j]["low"]) >= cl for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j]["low"]) > cl for j in range(1, right + 1))

        if is_supply or is_demand:
            avg_vol = _calculate_average_volume(closed_candles, i)
            multiplier = 2.0 if (vol > avg_vol * 2.0) else 1.0
            p_class = 1 if timeframe == "4h" else 2
            
            if is_supply:
                pivots_found.append({"ts": ts, "price": ch, "type": "SUPPLY", "class": p_class, "heat": multiplier})
            if is_demand:
                pivots_found.append({"ts": ts, "price": cl, "type": "DEMAND", "class": p_class, "heat": multiplier})
                
    return pivots_found


async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background loop (STRICT SSOT MODE)...")
    while True:
        db = SessionLocal()
        try:
            for symbol in TARGETS:
                db_sym = symbol.replace("/", "")
                
                # ROUTED EXCLUSIVELY THROUGH PIPELINE (SSOT ENFORCED)
                candles_4h = await battlebox_pipeline.fetch_live_4h(symbol, limit=50)
                candles_1h = await battlebox_pipeline.fetch_live_1h(symbol, limit=200) 
                candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)  
                
                # Avoid crashing if pipeline returns empty (e.g., MEXC rate limit)
                if not candles_4h or not candles_1h or not candles_1d:
                    continue
                
                log_radar_anchors(db_sym, candles_1d, candles_1h)
                
                new_pivots = []
                new_pivots.extend(_scan_for_pivots(db_sym, candles_4h, "4h"))
                new_pivots.extend(_scan_for_pivots(db_sym, candles_1h, "1h"))
                
                for p in new_pivots:
                    dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
                    src = "4H_PIVOT" if p["class"] == 1 else "1H_PIVOT"
                    
                    exists = db.query(GravityMemory).filter(
                        GravityMemory.symbol == db_sym,
                        GravityMemory.timestamp == dt,
                        GravityMemory.source == src
                    ).first()
                    
                    if not exists:
                        mem = GravityMemory(
                            symbol=db_sym, timestamp=dt, source=src,
                            level_type=p["type"], price=p["price"],
                            permanence_class=p["class"], heat_multiplier=p["heat"]
                        )
                        db.add(mem)
                        db.commit()
                        print(f"|| GRAVITY BEDROCK || {db_sym} | {src} {p['type']} @ ${p['price']} LOCKED. Heat: {p['heat']}")
                        
        except Exception as e:
            print(f"Gravity Engine Iteration Error: {e}")
        finally:
            db.close()
            
        await asyncio.sleep(900)