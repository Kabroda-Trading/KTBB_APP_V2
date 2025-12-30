# wealth_allocator.py
from typing import Dict, Any

def generate_dynamic_plan(capital: float, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Allocates capital based on market Gear Shifting (Impulse, Rest, Rotation)."""
    micro = analysis['micro']
    rotation = analysis['rotation']
    trend = analysis['indicators']
    fibs = analysis['fibs']
    
    orders = []
    mode = "UNKNOWN"; rationale = ""; reserve_pct = 0.0
    
    if rotation:
        mode = "ROTATION ENTRY"
        rationale = "Reclaiming 21 EMA / 200 SMA. Pullback complete. Deploying heavy capital."
        reserve_pct = 0.05
        orders.append({"price": analysis['price'], "weight": 1.0, "note": "Market/Limit Entry"})

    elif micro == "RUN (IMPULSE)":
        mode = "AGGRESSIVE ACCUMULATION"
        rationale = "Impulse run active (>21 EMA). Buying momentum floors to capture run."
        reserve_pct = 0.10
        orders.append({"price": trend['ema_21'], "weight": 0.4, "note": "21 EMA (Dynamic Support)"})
        orders.append({"price": fibs['shallow'], "weight": 0.6, "note": "0.382 Fib Momentum Zone"})

    else: # REST (PULLBACK)
        mode = "STANDARD ACCUMULATION"
        rationale = "Healthy rest period. Bidding high-probability Fibonacci targets."
        reserve_pct = 0.20
        orders.append({"price": fibs['golden'], "weight": 0.6, "note": "0.618 Fib Golden Pocket"})
        orders.append({"price": fibs['deep'], "weight": 0.4, "note": "0.786 Fib Deep Cycle Support"})

    active_cap = capital * (1 - reserve_pct)
    final_orders = [{"price": round(o['price'], 2), "amount": round(active_cap * o['weight'], 2), "note": o['note']} for o in orders]

    return {
        "status": "READY", "mode": mode, "rationale": rationale,
        "summary": {"deployed": sum(x['amount'] for x in final_orders), "reserve": capital * reserve_pct},
        "orders": final_orders
    }