# wealth_allocator.py
from typing import Dict, Any

def generate_dynamic_plan(capital: float, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    AUDITED LOGIC: "Gear Shifting"
    1. Rotation/Impulse -> Deploy Heavy (Shallow).
    2. Pullback -> Deploy Standard (Deep).
    3. Macro Fail -> Preserve Cash.
    """
    micro = analysis['micro']
    rotation = analysis['rotation']
    trend = analysis['indicators']
    fibs = analysis['fibs']
    zones = analysis['zones']
    
    orders = []
    mode = "UNKNOWN"
    rationale = ""
    reserve_pct = 0.0
    
    # --- GEAR 1: ROTATION (The Trigger) ---
    if rotation:
        mode = "ROTATION ENTRY"
        rationale = "Price is reclaiming structure (21 EMA / 200 SMA). The Rest phase is ending. DEPLOY NOW."
        reserve_pct = 0.05 # 95% In.
        
        orders.append({
            "price": analysis['price'],
            "weight": 1.0,
            "note": "Market Buy / Limit (Trend Confirm)"
        })

    # --- GEAR 2: MOMENTUM (The Run) ---
    elif micro == "RUN (IMPULSE)":
        mode = "AGGRESSIVE ACCUMULATION"
        rationale = "Micro Bull Run active (>21 EMA). Buying shallow support to catch the move."
        reserve_pct = 0.10 # 90% In.
        
        # Buy Shallow
        orders.append({
            "price": trend['ema_21'],
            "weight": 0.40,
            "note": "21 EMA (Dynamic Floor)"
        })
        orders.append({
            "price": fibs['shallow'],
            "weight": 0.60,
            "note": "0.382 Fib (Momentum Zone)"
        })

    # --- GEAR 3: PULLBACK (The Reload) ---
    elif micro == "REST (PULLBACK)":
        mode = "STANDARD ACCUMULATION"
        rationale = "Healthy Correction. Targeting Golden Pocket & Structural Demand."
        reserve_pct = 0.20 # Keep 20% for wicks
        
        # Buy Deep
        orders.append({
            "price": fibs['golden'],
            "weight": 0.50,
            "note": "0.618 Fib (High Probability)"
        })
        # Check if there is a Grade A Zone nearby
        zone_target = zones['demand'][0]['level'] if zones['demand'] else fibs['deep']
        orders.append({
            "price": zone_target,
            "weight": 0.50,
            "note": "Structural Demand / Deep Fib"
        })

    # --- CALCULATE ---
    active_capital = capital * (1 - reserve_pct)
    final_orders = []
    
    for o in orders:
        amt = active_capital * o['weight']
        final_orders.append({
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