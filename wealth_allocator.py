# wealth_allocator.py
from typing import Dict, Any

def generate_dynamic_plan(capital: float, analysis: Dict[str, Any]) -> Dict[str, Any]:
    phase = analysis['phase']
    price = analysis['price']
    fibs = analysis['fibs']
    trend = analysis['indicators']
    
    orders = []
    
    # STRATEGY: ROTATION (The "Get In" Signal)
    if "ROTATION" in phase:
        orders.append({
            "type": "MARKET ENTRY",
            "price": price,
            "pct": 0.60,
            "note": "Rotation Confirmed (21 EMA Reclaim). Deploying 60%."
        })
        orders.append({
            "type": "TRAIL SUPPORT",
            "price": trend['ema_21'],
            "pct": 0.30,
            "note": "21 EMA Retest Bid."
        })

    # STRATEGY: MOMENTUM (Running)
    elif "IMPULSE" in phase:
        orders.append({
            "type": "DYNAMIC SUPPORT",
            "price": trend['ema_21'],
            "pct": 0.40,
            "note": "Trend Floor (21 EMA)"
        })
        orders.append({
            "type": "SHALLOW FIB",
            "price": fibs['shallow'],
            "pct": 0.40,
            "note": "0.382 Fib (Momentum)"
        })

    # STRATEGY: PULLBACK (Resting)
    elif "PULLBACK" in phase:
        orders.append({
            "type": "GOLDEN POCKET",
            "price": fibs['golden'],
            "pct": 0.50,
            "note": "0.618 Fib (High Probability)"
        })
        if analysis['zones']:
            orders.append({
                "type": "INSTITUTIONAL ZONE",
                "price": analysis['zones'][0]['level'],
                "pct": 0.30,
                "note": "Grade A Velocity Zone"
            })

    # STRATEGY: TAKE PROFIT (Premium)
    if price > fibs['premium_zone']:
        orders = [] # Clear buys
        orders.append({
            "type": "TAKE PROFIT",
            "price": price,
            "pct": 0.0,
            "note": "Price in Premium Zone. Consider grading out 10-20%."
        })

    final_orders = []
    for o in orders:
        if o['pct'] > 0:
            amt = capital * o['pct']
            final_orders.append({
                "note": o['note'],
                "price": round(o['price'], 2),
                "amount": round(amt, 2)
            })
        else:
            final_orders.append(o)

    return {
        "status": "READY",
        "mode": phase,
        "rationale": analysis['action'],
        "orders": final_orders
    }