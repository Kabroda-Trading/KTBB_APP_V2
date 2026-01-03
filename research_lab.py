# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd  # Required for EMA math
import sse_engine
import ccxt.async_support as ccxt
import pytz

# ---------------------------------------------------------
# 1. MATH ENGINE (EMA & Indicators)
# ---------------------------------------------------------
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    """Calculates Exponential Moving Average"""
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

# ---------------------------------------------------------
# 2. MECHANICAL STRATEGY ENGINE
# ---------------------------------------------------------
def run_mechanical_strategy(
    bo_level: float, 
    bd_level: float, 
    candles: List[Dict], # Using 15m for reliable history
    leverage: float, 
    capital: float
):
    """
    KABRODA MECHANIC V1:
    1. TRIGGER: 15m Close outside level.
    2. CONFIRM: Next 2 candles MUST close outside level.
    3. ENTRY: On Open of the 4th candle (if aligned with 21 EMA).
    4. EXIT: When 15m Close crosses back over 21 EMA.
    """
    if not bo_level or not bd_level: 
        return {"pnl": 0, "pct": 0, "status": "NO_LEVELS", "entry": 0, "exit": 0}
    
    # Pre-calculate EMA for the whole series (Speed)
    closes = [c['close'] for c in candles]
    ema_21 = calculate_ema(closes, 21)
    
    direction = "NONE"
    entry_idx = -1
    
    # 1. SCAN FOR CONFIRMED BREAKOUT
    # We need at least 3 candles to confirm (Trigger + Conf1 + Conf2)
    scan_limit = min(24, len(candles) - 3) 
    
    for i in range(scan_limit):
        c1 = candles[i]   # Trigger Candle
        c2 = candles[i+1] # Confirmation 1
        c3 = candles[i+2] # Confirmation 2
        
        # LONG SETUP
        if c1['close'] > bo_level:
            # Must hold for 2 more closes
            if c2['close'] > bo_level and c3['close'] > bo_level:
                # Check EMA Alignment (Price > 21 EMA) at entry point
                if i+3 < len(ema_21) and candles[i+3]['open'] > ema_21[i+3]:
                    direction = "LONG"
                    entry_idx = i + 3
                    break
        
        # SHORT SETUP
        elif c1['close'] < bd_level:
            # Must hold for 2 more closes
            if c2['close'] < bd_level and c3['close'] < bd_level:
                # Check EMA Alignment (Price < 21 EMA)
                if i+3 < len(ema_21) and candles[i+3]['open'] < ema_21[i+3]:
                    direction = "SHORT"
                    entry_idx = i + 3
                    break
            
    if direction == "NONE":
        return {"pnl": 0, "pct": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}

    # 2. EXECUTE & MANAGE (EMA TRAILING EXIT)
    entry_price = candles[entry_idx]['open']
    exit_price = entry_price # Default flat
    status = "HELD"
    
    for i in range(entry_idx + 1, len(candles)):
        current_close = candles[i]['close']
        current_ema = ema_21[i]
        
        if direction == "LONG":
            # EXIT CONDITION: Close BELOW 21 EMA
            if current_close < current_ema:
                exit_price = current_close
                status = "EXIT_EMA"
                break
                
        elif direction == "SHORT":
            # EXIT CONDITION: Close ABOVE 21 EMA
            if current_close > current_ema:
                exit_price = current_close
                status = "EXIT_EMA"
                break
                
    # If we run out of data, mark to market
    if status == "HELD": exit_price = candles[-1]['close']

    # 3. CALCULATE PNL
    if direction == "LONG":
        raw_pct = (exit_price - entry_price) / entry_price
    else:
        raw_pct = (entry_price - exit_price) / entry_price
        
    pnl = capital * (raw_pct * leverage)
    
    trade_status = f"{direction}_WIN" if pnl > 0 else f"{direction}_LOSS"

    return {
        "pnl": round(pnl, 2), 
        "pct": round(raw_pct * 100 * leverage, 2), 
        "status": trade_status, 
        "entry": entry_price, 
        "exit": exit_price
    }

# ---------------------------------------------------------
# 3. MAIN RUNNER (Orchestrator)
# ---------------------------------------------------------
async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000) -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    
    history = []
    
    tz_map = {
        "London": ("Europe/London", 8, 0),
        "New_York": ("America/New_York", 8, 30),
        "Tokyo": ("Asia/Tokyo", 9, 0)
    }
    
    # 1. Identify Historical Sessions
    for s_key in session_keys:
        target = (0,0,0)
        for k,v in tz_map.items():
            if k in s_key or s_key in k: target = v
        if target == (0,0,0): continue 
        
        market_tz = pytz.timezone(target[0])
        indices = []
        for i in range(len(raw_15m)-1, -1, -1):
            dt = datetime.fromtimestamp(raw_15m[i]['time'], tz=pytz.UTC).astimezone(market_tz)
            if dt.hour == target[1] and dt.minute == target[2]:
                indices.append(i)
            if len(indices) >= 20: break 
        
        # 2. Run Simulation for Each Session
        for idx in indices:
            anchor = raw_15m[idx]
            # ISOLATE HISTORY: Ensure engine only sees data available AT THAT TIME
            valid_dailies = [d for d in raw_daily if d['time'] < anchor['time']]
            
            sse_input = {
                "raw_15m_candles": raw_15m[:idx], 
                "raw_daily_candles": valid_dailies,
                "slice_24h": raw_15m[max(0, idx-96):idx],
                "slice_4h": raw_15m[max(0, idx-16):idx],
                "session_open_price": anchor.get("open", 0.0),
                "r30_high": anchor.get("high", 0.0),
                "r30_low": anchor.get("low", 0.0),
                "last_price": anchor.get("close", 0.0)
            }
            
            # GENERATE LOCKED LEVELS
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            
            # GET FUTURE DATA (For strategy execution)
            future_data = raw_15m[idx : idx+64] # Next 16 hours
            
            # --- RUN A/B TEST ---
            
            # A) Safe Strategy (Uses Calculated Triggers)
            res_safe = run_mechanical_strategy(
                levels["breakout_trigger"], levels["breakdown_trigger"], 
                future_data, leverage, capital
            )
            
            # B) Aggressive Strategy (Uses Raw 30m Range)
            # 30m High/Low + 0.05% tiny buffer
            agg_bo = levels["range30m_high"] * 1.0005
            agg_bd = levels["range30m_low"] * 0.9995
            
            res_agg = run_mechanical_strategy(
                agg_bo, agg_bd, 
                future_data, leverage, capital
            )
            
            # Stats for display
            session_max = max([c['high'] for c in future_data[:32]], default=0) 
            session_min = min([c['low'] for c in future_data[:32]], default=0)
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "levels": levels, 
                "strategy": res_safe,       # Main Display
                "strategy_agg": res_agg,    # Comparison
                "stats": {
                    "hit_res": session_max >= levels['daily_resistance'], 
                    "hit_sup": session_min <= levels['daily_support']
                }
            })
            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    # 3. AGGREGATE STATS
    if not history: return {"history": [], "stats": {}}
    
    count = len(history)
    def sum_pnl(key): return sum(h[key]['pnl'] for h in history)
    def win_rate(key): 
        wins = sum(1 for h in history if h[key]['pnl'] > 0)
        total = sum(1 for h in history if h[key]['status'] != "NO_SETUP")
        return int((wins/total)*100) if total > 0 else 0

    stats_out = {
        "safe_pnl": sum_pnl('strategy'),
        "agg_pnl": sum_pnl('strategy_agg'),
        "safe_win_rate": win_rate('strategy'),
        "agg_win_rate": win_rate('strategy_agg'),
        "res_hit_rate": int((sum(1 for h in history if h['stats']['hit_res']) / count) * 100),
        "sup_hit_rate": int((sum(1 for h in history if h['stats']['hit_sup']) / count) * 100),
        
        # Keep frontend compatible
        "bo_rate": win_rate('strategy'), 
        "bd_rate": win_rate('strategy_agg') 
    }
    
    return {"history": history, "stats": stats_out}