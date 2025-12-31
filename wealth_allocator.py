# wealth_allocator.py
from typing import Dict, Any

def generate_dynamic_plan(capital: float, strategy: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
    # 1. UNPACK V6.0 ANALYSIS
    phase = analysis['phase']   # e.g., "STAIR_STEP_CLIMB"
    price = analysis['price']
    zones = analysis['zones']
    fibs = analysis['fibs']
    
    orders = []
    mode = f"{strategy} // {phase}"
    rationale = "Analyzing market structure..."
    
    # 2. DEFINE LOGIC FLAGS
    is_bullish_stair = phase == "STAIR_STEP_CLIMB"
    is_macro_bear = phase == "MACRO_CORRECTION"
    is_cycle_danger = phase == "CYCLE_BREAKDOWN_WARNING"

    # --- STRATEGY 1: ACCUMULATOR ---
    if strategy == 'ACCUMULATOR':
        if is_cycle_danger:
            rationale = "⚠️ CYCLE BREAKDOWN. Macro 0.5 Broken. Bidding Deep Winter Lows only."
            orders.append({"note": "WINTER BID (0.786)", "type": "BUY_BTC", "price": fibs['macro_bot'], "pct": 0.50})
            orders.append({"note": "DEEP VALUE (0.618)", "type": "BUY_BTC", "price": zones['deploy']['bottom'], "pct": 0.25})
        
        elif is_macro_bear:
            rationale = "Macro Correction active. Accumulating the 0.618 Value Zone."
            # Buy the deployment cloud
            for i, level in enumerate(zones['deploy']['levels']):
                 orders.append({"note": f"DCA LEVEL {i+1}", "type": "BUY_BTC", "price": level, "pct": 0.20})
                 
        elif is_bullish_stair:
            rationale = "Bullish Stair-Step. Buying the 21 EMA pullbacks."
            orders.append({"note": "AGGRESSIVE BUY", "type": "BUY_BTC", "price": zones['deploy']['top'], "pct": 0.40})
            orders.append({"note": "STANDARD BUY", "type": "BUY_BTC", "price": zones['deploy']['bottom'], "pct": 0.40})

    # --- STRATEGY 2: CYCLE INVESTOR ---
    elif strategy == 'CYCLE':
        # Check for Extraction (Selling)
        if price >= zones['extract']['bottom']:
             rationale = "Price entering Extraction Cloud. Rotating to Cash."
             orders.append({"note": "TAKE PROFIT", "type": "SELL_BTC", "price": price, "pct": 0.0})
        
        elif is_cycle_danger:
             rationale = "Cycle structure broken. Preserving cash."
             orders.append({"note": "HOLD CASH", "type": "HOLD", "price": price, "amount": "---"})
             
        elif is_bullish_stair:
             rationale = "Buying the Stair-Step Trend."
             orders.append({"note": "TREND BUY", "type": "BUY_BTC", "price": zones['deploy']['top'], "pct": 0.25})
             
        elif is_macro_bear:
             rationale = "Waiting for Deep Value (0.618)."
             orders.append({"note": "LIMIT BUY", "type": "BUY_BTC", "price": zones['deploy']['top'], "pct": 0.30})

    # --- STRATEGY 3: HYBRID ---
    elif strategy == 'HYBRID':
        if is_cycle_danger:
            rationale = "🚨 RISK OFF. Cycle broken. Exit Alts immediately."
            orders.append({"note": "EMERGENCY EXIT", "type": "SELL_ALTS_TO_CASH", "price": price, "pct": 0.0})
            
        elif is_bullish_stair:
            rationale = "Green Light. Bitcoin trending. Deploying into High-Beta Alts."
            orders.append({"note": "BETA ENTRY", "type": "BUY_ALTS", "price": price, "pct": 0.50})
            
        elif price >= zones['extract']['bottom']:
            rationale = "Top of range. Rotating Beta into Stablecoins."
            orders.append({"note": "ROTATE TO STABLES", "type": "SELL_ALTS", "price": price, "pct": 0.0})
            
        else:
            rationale = "Chop/Correction. Holding position."
            orders.append({"note": "HOLD", "type": "INFO", "price": price, "amount": "---"})

    # 3. FORMAT ORDERS
    final_orders = []
    for o in orders:
        amt = "---"
        if o.get('pct', 0) > 0 and 'BUY' in o.get('type', ''):
            amt = round(capital * o['pct'], 2)
            
        final_orders.append({
            "note": o['note'], 
            "action": o['type'], 
            "price": round(o['price'], 2), 
            "amount": amt
        })

    return {"status": "READY", "mode": mode, "rationale": rationale, "orders": final_orders}