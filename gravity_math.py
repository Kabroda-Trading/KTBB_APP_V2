# gravity_math.py
# ==============================================================================
# KABRODA GRAVITY MATH ENGINE (KDE CLUSTERING)
# ==============================================================================
# Purpose: Transforms raw historical price lines into weighted Gravity Zones.
# Uses the strict 0.2% sensitivity clustering logic from Predator Command.
# ==============================================================================

from database import SessionLocal, GravityMemory
from typing import List, Dict, Any

def calculate_gravity_heatmap(symbol: str, sensitivity_pct: float = 0.20) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        # 1. Pull all active structural memory for the symbol
        db_sym = symbol.replace("/", "")
        levels = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.active == True
        ).all()

        if not levels:
            return []

        # 2. Sort by price from lowest to highest
        sorted_levels = sorted(levels, key=lambda x: x.price)

        clusters = []
        current_cluster = []
        current_base = sorted_levels[0].price

        # 3. The Confluence Loop (0.2% Variance Threshold)
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

        # 4. Score and Weight the Clusters
        heatmap = []
        for cluster in clusters:
            top_price = max(l.price for l in cluster)
            bot_price = min(l.price for l in cluster)
            
            # Add micro-padding if the cluster is exactly on one price tick
            if top_price == bot_price:
                top_price *= 1.0005
                bot_price *= 0.9995

            # Calculate Heat Mass
            total_heat = sum(l.heat_multiplier for l in cluster)
            
            # Apply Permanence Weight (4H Guardrails add massive gravity)
            guardrail_count = sum(1 for l in cluster if l.permanence_class == 1)
            total_heat += (guardrail_count * 3.0) # 3x weight for 4H structural walls

            # Determine color intensity based on total heat mass
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

        # Sort heatmap by heaviest gravity zones first
        return sorted(heatmap, key=lambda x: x["heat_score"], reverse=True)
        
    finally:
        db.close()