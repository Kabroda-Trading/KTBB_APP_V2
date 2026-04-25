# gravity_math.py
# ==============================================================================
# KABRODA GRAVITY MATH ENGINE (KDE CLUSTERING & MACRO FIBS)
# ==============================================================================

from database import SessionLocal, GravityMemory
from typing import List, Dict, Any
import ccxt.async_support as ccxt
import os

def calculate_gravity_heatmap(symbol: str, sensitivity_pct: float = 0.20) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        db_sym = symbol.replace("/", "")
        levels = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.active == True
        ).all()

        if not levels:
            return []

        sorted_levels = sorted(levels, key=lambda x: x.price)

        clusters = []
        current_cluster = []
        current_base = sorted_levels[0].price

        threshold = sensitivity_pct / 100.0

        for lvl in sorted_levels:
            upper_limit = current_base * (1 + threshold)
            
            if lvl.price <= upper_limit:
                current_cluster.append(lvl)
            else:
                clusters.append(current_cluster)
                current_cluster = [lvl]
                current_base = lvl.price
                
        if current_cluster:
            clusters.append(current_cluster)

        heatmap = []
        for cluster in clusters:
            top_price = max(l.price for l in cluster)
            bot_price = min(l.price for l in cluster)
            
            if top_price == bot_price:
                top_price *= 1.0005
                bot_price *= 0.9995

            total_heat = sum(l.heat_multiplier for l in cluster)
            guardrail_count = sum(1 for l in cluster if l.permanence_class == 1)
            total_heat += (guardrail_count * 3.0) 

            intensity = "LIGHT"
            if total_heat >= 10:
                intensity = "MAXIMUM"
            elif total_heat >= 5:
                intensity = "HEAVY"

            heatmap.append({
                "top": round(top_price, 2),
                "bottom": round(bot_price, 2),
                "heat_score": round(total_heat, 2),
                "intensity": intensity,
                "level_count": len(cluster),
                "contains_guardrail": guardrail_count > 0,
                "contributors": [f"{l.source} ({l.level_type})" for l in cluster]
            })

        return sorted(heatmap, key=lambda x: x["heat_score"], reverse=True)
        
    finally:
        db.close()


async def calculate_macro_fibs(symbol: str) -> Dict[str, float]:
    exchange = ccxt.mexc({"enableRateLimit": True})
    try:
        candles = await exchange.fetch_ohlcv(symbol, "1d", limit=30)
        if not candles:
            return {}

        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        
        swing_high = max(highs)
        swing_low = min(lows)
        
        diff = swing_high - swing_low
        
        return {
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
            "fib_0500": round(swing_high - (diff * 0.5), 2),
            "fib_0618": round(swing_high - (diff * 0.618), 2),
            "fib_0786": round(swing_high - (diff * 0.786), 2)
        }
    except Exception as e:
        print(f"Gravity Macro Fib Error: {e}")
        return {}
    finally:
        await exchange.close()