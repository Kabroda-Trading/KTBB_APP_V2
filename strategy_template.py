# strategy_template.py
# ==============================================================================
# KABRODA UNIVERSAL STRATEGY TEMPLATE
# ==============================================================================
# 1. DUPLICATE this file and rename it (e.g., "project_alpha.py")
# 2. SCROLL down to the "CUSTOM STRATEGY LOGIC" section
# 3. PASTE your new rules (Stops, Entries, Targets) there.
# 4. REGISTER in main.py.
# ==============================================================================

from typing import Dict, Any
import battlebox_pipeline

async def get_strategy_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    settings: Dict[str, Any] = {} # Catch-all for extra settings (Ferrari, etc.)
) -> Dict[str, Any]:
    
    # --- 1. THE "PLUMBING" (DO NOT TOUCH) ---
    # This automatically connects to the Corporate Pipeline.
    try:
        pipeline_data = await battlebox_pipeline.get_live_battlebox(
            symbol=symbol,
            session_mode="MANUAL",
            manual_id=session_id
        )

        # Pipeline Failure Check
        if pipeline_data.get("status") == "ERROR":
            return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Connection Failed"}

        # Extract Core Data (The "Truth")
        current_price = pipeline_data.get("price", 0.0)
        levels = pipeline_data.get("battlebox", {}).get("levels", {})
        
        # Standard Corporate Levels
        bo = float(levels.get("breakout_trigger", 0.0))
        bd = float(levels.get("breakdown_trigger", 0.0))
        dr = float(levels.get("daily_resistance", 0.0))
        ds = float(levels.get("daily_support", 0.0))
        r30_high = float(levels.get("range30m_high", 0.0))
        r30_low = float(levels.get("range30m_low", 0.0))

        # --- 2. YOUR CUSTOM STRATEGY LOGIC (EDIT THIS AREA) ---
        # ======================================================
        # Use the variables above (bo, bd, current_price) to make decisions.
        
        status = "WAITING"
        side = "NONE"
        stop_loss = 0.0
        target = 0.0
        
        # EXAMPLE: Simple Logic (Replace with your GPT Research)
        if current_price > bo:
            status = "EXECUTING"
            side = "LONG"
            stop_loss = r30_low
            target = bo + (bo - r30_low) # 1:1 Target
        elif current_price < bd:
            status = "EXECUTING"
            side = "SHORT"
            stop_loss = r30_high
            target = bd - (r30_high - bd)
        
        # ======================================================

        # --- 3. THE RETURN PACKET (Standardized) ---
        return {
            "ok": True,
            "strategy_name": "TEMPLATE_STRATEGY", # Change this name
            "symbol": symbol,
            "session": session_id,
            "price": current_price,
            "decision": {
                "status": status,
                "side": side,
                "entry": bo if side == "LONG" else bd,
                "stop_loss": stop_loss,
                "target": target
            },
            "corporate_levels": {
                "BO": bo, "BD": bd, "DR": dr, "DS": ds
            }
        }

    except Exception as e:
        return {"ok": False, "status": "CRASH", "msg": str(e)}