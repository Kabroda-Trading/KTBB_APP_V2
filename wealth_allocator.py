# wealth_allocator.py
# ---------------------------------------------------------
# WEALTH OPERATOR: DYNAMIC DEPLOYMENT
# ---------------------------------------------------------
from typing import Dict, Any

def generate_dynamic_plan(capital: float, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    "Buy on Red, Sell on Green."
    Allocates capital based on the QUALITY of the price.
    Prevents sitting in cash during a Bull Run.
    """
    micro = analysis['micro']
    rotation = analysis['rotation']
    # exhaustion = analysis['exhaustion'] # Available if we want deeper logic later
    trend = analysis['indicators']
    fibs = analysis['fibs']
    
    orders = []
    mode = "UNKNOWN"
    rationale = ""
    reserve_pct = 0.0
    
    # --- GEAR 1: ROTATION (The "Get In" Signal) ---
    if rotation:
        mode = "ROTATION ENTRY"
        rationale = "Price is rotating (Reclaiming 21 EMA / 200 SMA). The Rest phase is ending. Deploying heavy capital."
        reserve_pct = 0.05 # 95% Invested. Don't miss the move.
        
        # Order: Market/Limit right here to catch the move
        orders.append({
            "type": "ROTATION BUY",
            "price": analysis['price'],
            "weight": 1.0,
            "note": "Confirming Strength. Entering position."
        })

    # --- GEAR 2: MOMENTUM (Catch the Run) ---
    elif micro == "RUN (IMPULSE)":
        mode = "AGGRESSIVE ACCUMULATION"
        rationale = "Market is running (Micro Bull > 21 EMA). Deploying into strength to capture upside."
        reserve_pct = 0.10 # 90% In. Don't sit in cash.
        
        # Buy the "Shallow" supports because deep ones won't hit
        orders.append({
            "type": "DYNAMIC SUPPORT",
            "price": trend['ema_21'],
            "weight": 0.40,
            "note": "21 EMA (Trend Floor)"
        })
        orders.append({
            "type": "SHALLOW FIB",
            "price": fibs['shallow'], # 0.382 area
            "weight": 0.60,
            "note": "Shallow Retracement (High Demand)"
        })

    # --- GEAR 3: HEALTHY PULLBACK (Standard Buy) ---
    elif micro == "REST (PULLBACK)":
        mode = "STANDARD ACCUMULATION"
        rationale = "Healthy correction in a Bull Market. Targeting high-probability structural zones."
        reserve_pct = 0.20 # Keep 20% for a surprise wick
        
        # We want the Golden Pocket
        orders.append({
            "type": "GOLDEN POCKET",
            "price": fibs['golden'], # 0.618
            "weight": 0.60,
            "note": "0.618 Fib (High Probability)"
        })
        # Insurance bid
        orders.append({
            "type": "DEEP VALUE",
            "price": fibs['deep'], # 0.786
            "weight": 0.40,
            "note": "0.786 Fib (Wick Catcher)"
        })

    # --- CALCULATE ORDERS ---
    active_capital = capital * (1 - reserve_pct)
    final_orders = []
    
    for o in orders:
        amt = active_capital * o['weight']
        final_orders.append({
            "type": o['type'],
            "price": round(o['price'], 2),
            "amount": round(amt, 2),
            "note": o['note']
        })

    return {
        "status": "READY",
        "mode": mode,
        "rationale": rationale,
        "summary": {
            "deploy_now": sum(x['amount'] for x in final_orders),
            "reserve": capital * reserve_pct
        },
        "orders": final_orders
    }