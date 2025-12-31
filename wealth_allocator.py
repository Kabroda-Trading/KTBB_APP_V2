# wealth_allocator.py
from typing import Dict, Any

def generate_dynamic_plan(capital: float, strategy: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates orders based on User Strategy Profile.
    Strategies: 'ACCUMULATOR', 'CYCLE', 'HYBRID'
    """
    phase = analysis['phase']
    price = analysis['price']
    zones = analysis['zones']
    trend = analysis['indicators']
    
    orders = []
    mode = f"{strategy} PROTOCOL ({phase})"
    rationale = "Analyzing battlefield structure..."
    
    # --- STRATEGY 1: THE ASSET ACCUMULATOR (Fortress) ---
    if strategy == 'ACCUMULATOR':
        rationale = "Mission: Maximum BTC exposure. Ignoring Extraction Signals. Bidding deep value."
        
        # Action: Deploy into Value
        if phase in ["BULL_PULLBACK", "MACRO_WINTER"]:
            rationale += " Price in Deployment Zone. Establishing positions."
            for i, level in enumerate(zones['deploy']['levels']):
                orders.append({
                    "note": f"DEPLOYMENT GRID {i+1}",
                    "type": "BUY_BTC",
                    "price": level,
                    "pct": 0.25
                })
        
        # Action: Rotation (Add on strength)
        elif phase == "ROTATION_IGNITION":
            rationale += " 21/200 Cross Confirmed. Momentum shift. Deploying reserves."
            orders.append({"note": "MARKET ENTRY", "type": "BUY_BTC", "price": price, "pct": 0.50})
            orders.append({"note": "TRAIL SUPPORT (21 EMA)", "type": "BUY_BTC", "price": trend['ema_21'], "pct": 0.50})

    # --- STRATEGY 2: THE CYCLE INVESTOR (Merchant) ---
    elif strategy == 'CYCLE':
        rationale = "Mission: Compound wealth by Extracting at Tops and Deploying at Bottoms."
        
        # Action: Extract Cash (Premium)
        if price >= zones['extract']['bottom']:
            rationale += " Price inside Extraction Zone. Selling BTC for CASH reserves."
            for i, level in enumerate(zones['extract']['levels']):
                if level >= price * 0.98: 
                    orders.append({
                        "note": f"EXTRACTION GRID {i+1}",
                        "type": "SELL_BTC_TO_CASH",
                        "price": level,
                        "pct": 0.0 # User discretionary
                    })
        
        # Action: Deploy Cash (Discount)
        elif phase in ["BULL_PULLBACK", "MACRO_WINTER"]:
            rationale += " Price in Deployment Zone. Utilizing Cash Reserves."
            for i, level in enumerate(zones['deploy']['levels']):
                orders.append({
                    "note": f"DEPLOYMENT GRID {i+1}",
                    "type": "BUY_BTC",
                    "price": level,
                    "pct": 0.20
                })

    # --- STRATEGY 3: THE HYBRID (Hunter) ---
    elif strategy == 'HYBRID':
        rationale = "Mission: Use BTC strength to hunt High-Beta assets. Cash out Alts at the top."
        
        # Action: Risk ON (Rotation/Expansion)
        if phase in ["MOMENTUM_RUN", "ROTATION_IGNITION"]:
            rationale += " BTC Strength Confirmed (21>200). SIGNAL: Accumulate High-Beta Alts."
            orders.append({
                "note": "BETA ENTRY (MKT)",
                "type": "BUY_ALTS",
                "price": price, 
                "pct": 0.40
            })
            orders.append({
                "note": "BETA LIMIT (21 EMA)",
                "type": "BUY_ALTS",
                "price": trend['ema_21'], 
                "pct": 0.60
            })
            
        # Action: Risk OFF (Extraction Zone)
        elif price >= zones['extract']['bottom']:
            rationale += " BTC hitting Extraction Perimeter. SIGNAL: Liquidate Alts to CASH immediately. Do not buy BTC."
            orders.append({
                "note": "FULL LIQUIDATION",
                "type": "SELL_ALTS_TO_CASH",
                "price": price,
                "pct": 0.0
            })
            
        # Action: Macro Bottom (Winter)
        elif phase == "MACRO_WINTER":
             rationale += " Macro Bottom. Only buying BTC here. Alts are too risky."
             for i, level in enumerate(zones['deploy']['levels']):
                orders.append({
                    "note": f"BTC ACCUMULATION {i+1}",
                    "type": "BUY_BTC",
                    "price": level,
                    "pct": 0.25
                })

    # Calc Amounts
    final_orders = []
    for o in orders:
        if o['pct'] > 0 and 'BUY' in o['type']:
            amt = capital * o['pct']
            final_orders.append({
                "note": o['note'],
                "action": o['type'],
                "price": round(o['price'], 2),
                "amount": round(amt, 2)
            })
        else:
            final_orders.append({
                "note": o['note'],
                "action": o['type'],
                "price": round(o['price'], 2),
                "amount": "---"
            })

    return {
        "status": "READY",
        "mode": mode,
        "rationale": rationale,
        "orders": final_orders
    }