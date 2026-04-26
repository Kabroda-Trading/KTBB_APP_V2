# gravity_math.py
# ==============================================================================
# KABRODA GRAVITY MATH ENGINE (TRUE KDE DENSITY MODEL)
# UPDATE: Macro Swing Auditor injected for Deep-Space Extension Targeting.
# ==============================================================================

import math
from database import SessionLocal, GravityMemory
from typing import List, Dict, Any

def _gaussian_kernel(x: float, mu: float, sigma: float) -> float:
    """Calculates the gravitational pull (Gaussian curve) at price x for a pivot at mu."""
    if sigma == 0:
        return 0.0
    return math.exp(-0.5 * (((x - mu) / sigma) ** 2))

def calculate_gravity_kde(symbol: str, bandwidth_bps: int = 15, resolution: int = 400) -> Dict[str, Any]:
    """
    Phase 2: True Kernel Density Estimation (KDE).
    Transforms discrete pivot levels into a continuous, Bookmap-style gravity wave.
    """
    db = SessionLocal()
    try:
        db_sym = symbol.replace("/", "")
        levels = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.active == True
        ).all()

        if not levels:
            return {"curve": [], "peaks": [], "max_density": 0.0}

        # 1. Determine the scan range (Min/Max price + 2% padding for wave tails)
        prices = [l.price for l in levels]
        min_p = min(prices) * 0.98 
        max_p = max(prices) * 1.02 
        
        # 2. Setup KDE Parameters
        # Sigma represents the "width" of the gravitational pull. 
        # (e.g., 15 bps of $75,000 is a $112 radius of influence)
        sigma = ((min_p + max_p) / 2.0) * (bandwidth_bps / 10000.0)
        step_size = (max_p - min_p) / resolution
        
        kde_curve = []
        max_density = 0.0

        # 3. Compute the Continuous Density Wave
        for i in range(resolution + 1):
            current_price = min_p + (i * step_size)
            total_density = 0.0
            
            for lvl in levels:
                # Compound weight based on Kabroda Bedrock strength
                weight = lvl.heat_multiplier
                if lvl.permanence_class == 1:
                    weight += 3.0  # Massive pull for 4H Guardrails
                elif lvl.source == "7_DAY_KABRODA":
                    weight += 1.5  # Heavy pull for Session Ranges / Daily Triggers
                    
                pull = _gaussian_kernel(current_price, lvl.price, sigma)
                total_density += (pull * weight)
            
            kde_curve.append({
                "price": round(current_price, 2),
                "density": round(total_density, 4)
            })
            
            if total_density > max_density:
                max_density = total_density

        # 4. Extract "Peaks" (The exact Center of Gravity for the UI)
        peaks = []
        for i in range(1, len(kde_curve) - 1):
            prev_d = kde_curve[i-1]["density"]
            curr_d = kde_curve[i]["density"]
            next_d = kde_curve[i+1]["density"]
            
            # Local Maxima check: Is it a peak? Is it mathematically significant?
            if curr_d > prev_d and curr_d > next_d and curr_d > (max_density * 0.15):
                intensity = "LIGHT"
                if curr_d >= max_density * 0.80:
                    intensity = "MAXIMUM"
                elif curr_d >= max_density * 0.40:
                    intensity = "HEAVY"
                    
                peaks.append({
                    "price": kde_curve[i]["price"],
                    "heat_score": round(curr_d, 2),
                    "intensity": intensity
                })
        
        # Sort actionable targets by Highest Heat first
        peaks = sorted(peaks, key=lambda x: x["heat_score"], reverse=True)
        
        return {
            "curve": kde_curve,
            "peaks": peaks,
            "max_density": round(max_density, 4)
        }
        
    finally:
        db.close()


def calculate_macro_fibs(candles_1d: List[Dict[str, Any]], candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pure math function. Single Source of Truth enforced.
    Calculates both internal retracements AND deep-space extensions for blue-sky breakouts.
    """
    try:
        fib_data = {}
        if candles_1d:
            # Isolate the 30-Day Macro Swing
            macro_slice = candles_1d[-30:] if len(candles_1d) > 30 else candles_1d
            
            highs = [float(c["high"]) for c in macro_slice]
            lows = [float(c["low"]) for c in macro_slice]
            
            swing_high = max(highs)
            swing_low = min(lows)
            diff = swing_high - swing_low
            
            fib_data = {
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                
                # Upside Extensions (Blue Sky Breakouts)
                "ext_up_1272": round(swing_high + (diff * 0.272), 2),
                "ext_up_1618": round(swing_high + (diff * 0.618), 2),
                "ext_up_2000": round(swing_high + (diff * 1.000), 2),
                
                # Downside Extensions (Price Discovery Shorts)
                "ext_dn_1272": round(swing_low - (diff * 0.272), 2),
                "ext_dn_1618": round(swing_low - (diff * 0.618), 2),
                "ext_dn_2000": round(swing_low - (diff * 1.000), 2),
                
                # Internal Retracements
                "fib_0500": round(swing_high - (diff * 0.5), 2),
                "fib_0618": round(swing_high - (diff * 0.618), 2),
                "fib_0786": round(swing_high - (diff * 0.786), 2)
            }
            
        return {**fib_data, "chart_data": candles_15m}
        
    except Exception as e:
        print(f"Gravity Macro Fib Error: {e}")
        return {"chart_data": []}