# wealth_lab.py
# ---------------------------------------------------------
# KABRODA LABS: GRID DCA STRATEGY ENGINE
# Implements "Master System: Diamond Edition" Logic
# ---------------------------------------------------------

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any
import math

@dataclass
class GridConfig:
    """Defines the rules for the Grid Strategy (Matches Pine Inputs)"""
    asset_price: float      # Current Price (Anchor)
    total_capital: float    # User's Available Capital
    deploy_pct: float = 50.0 # % of capital to deploy in this window
    max_buys: int = 10      # Number of tranches
    grid_step_pct: float = 2.0 # % drop between levels
    size_mult: float = 1.6  # Progressive multiplier (1.0 = Flat, 1.6 = Martingale-ish)

def run_grid_simulation(config: GridConfig) -> Dict[str, Any]:
    """
    Calculates the exact Buy Levels and Order Sizes for a Grid Campaign.
    Returns a deployment schedule matching the Diamond Edition logic.
    """
    
    entries = []
    
    # 1. Calculate Total Weight for Progressive Sizing
    if config.size_mult == 1.0:
        total_weight = float(config.max_buys)
    else:
        # Sum = (1 - r^n) / (1 - r)
        total_weight = (1 - math.pow(config.size_mult, config.max_buys)) / (1 - config.size_mult)
    
    # 2. Calculate Base Tranche $ Amount (The smallest first buy)
    effective_capital = config.total_capital * (config.deploy_pct / 100.0)
    base_amount = effective_capital / total_weight
    
    # 3. Generate the Grid
    running_invested = 0.0
    running_coins = 0.0
    
    for i in range(config.max_buys):
        # A. Price Level Calculation
        step_size_usd = config.asset_price * (config.grid_step_pct / 100.0)
        buy_price = config.asset_price - (step_size_usd * (i + 1))
        
        # B. Position Size Calculation
        tranche_weight = math.pow(config.size_mult, i)
        usd_amount = base_amount * tranche_weight
        
        # C. Accumulation Math (Stats)
        coin_amount = usd_amount / buy_price
        running_invested += usd_amount
        running_coins += coin_amount
        avg_price = running_invested / running_coins if running_coins > 0 else 0.0
        
        entry = {
            "level": i + 1,
            "drop_from_anchor": f"-{config.grid_step_pct * (i + 1):.1f}%",
            "buy_price": round(buy_price, 2),
            "cost_usd": round(usd_amount, 2),
            "coins_bought": round(coin_amount, 6),
            "total_invested": round(running_invested, 2),
            "new_avg_price": round(avg_price, 2)
        }
        entries.append(entry)
        
    return {
        "status": "READY",
        "config": {
            "anchor_price": config.asset_price,
            "total_capital": config.total_capital,
            "deployment": f"{config.deploy_pct}% (${effective_capital:,.2f})",
            "sizing_model": f"{config.size_mult}x Progressive"
        },
        "totals": {
            "total_deployed": round(running_invested, 2),
            "final_avg_price": round(entries[-1]['new_avg_price'], 2),
            "max_drawdown_depth": entries[-1]['drop_from_anchor']
        },
        "grid": entries
    }