# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz

# ---------------------------------------------------------
# 1. MATH ENGINE
# ---------------------------------------------------------
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

# ---------------------------------------------------------
# 2. STRATEGY ENGINE (15m Setup + 5m Execution)
# ---------------------------------------------------------
def run_mtf_strategy(
    bo_level: float, 
    bd_level: float, 
    candles_15m: List[Dict], 
    candles_5m: List[Dict], # Expects pre-filtered 5m data for this session
    leverage: float, 
    capital: float
):
    if not bo_level or not bd_level: 
        return {"pnl": 0, "pct": 0, "status": "NO_LEVELS", "entry": 0, "exit": 0}
    
    # 1. ANALYZE 15M SETUP (The "Green Light")
    direction = "NONE"
    setup_time = 0
    
    # We need 3 candles: Trigger + Conf1 + Conf2
    scan_limit = min(32, len(candles_15m)-3) # Scan first 8 hours
    
    for i in range(scan_limit):
        c1 = candles_15m[i]
        c2 = candles_15m[i+1]
        c3 = candles_15m[i+2]
        
        # LONG SETUP: 3 Closes above Trigger
        if c1['close'] > bo_level and c2['close'] > bo_level and c3['close'] > bo_level:
            direction = "LONG"
            setup_time = c3['time'] # Time of 3rd candle open
            break
            
        # SHORT SETUP: 3 Closes below Trigger
        if c1['close'] < bd_level and c2['close'] < bd_level and c3['close'] < bd_level:
            direction = "SHORT"
            setup_time = c3['time']
            break
            
    if direction == "NONE":
        return {"pnl": 0, "pct": 0, "status": "NO_15M_SETUP", "entry": 0, "exit": 0}

    # 2. EXECUTE ON 5M (The "Entry Gate")
    if not candles_5m:
         return {"pnl": 0, "pct": 0, "status": "NO_5M_DATA", "entry": 0, "exit": 0}

    # Calculate 5m EMA 
    c5_closes = [c['close'] for c in candles_5m]
    ema_21 = calculate_ema(c5_closes, 21)
    
    entry_price = 0.0
    exit_price = 0.0
    entry_idx = -1
    status = "WAITING_FOR_ALIGNMENT"
    
    # FIND ENTRY
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        # Skip until AFTER setup time
        if c['time'] < setup_time: continue 
        
        # Ensure EMA exists
        if i >= len(ema_21): break
        ema = ema_21[i]
        
        if direction == "LONG":
            # ENTRY RULE: Price > 21 EMA
            if c['close'] > ema:
                entry_price = c['close'] 
                entry_idx = i
                status = "IN_TRADE"
                break
                
        elif direction == "SHORT":
            # ENTRY RULE: Price < 21 EMA
            if c['close'] < ema:
                entry_price = c['close']
                entry_idx = i
                status = "IN_TRADE"
                break
                
    if status != "IN_TRADE":
        return {"pnl": 0, "pct": 0, "status": "NO_5M_ENTRY", "entry": 0, "exit": 0}

    # 3. MANAGE EXIT (5m EMA Cross)
    exit_price = candles_5m[-1]['close'] # Default mark to market
    
    for i in range(entry_idx + 1, len(candles_5m)):
        c = candles_5m[i]
        ema = ema_21[i]
        
        if direction == "LONG":
            if c['close'] < ema:
                exit_price = c['close']
                break
        elif direction == "SHORT":
            if c['close'] > ema:
                exit_price = c['close']
                break

    # 4. CALC RESULTS
    if direction == "LONG":
        raw_pct = (exit_price - entry_price) / entry_price
    else:
        raw_pct = (entry_price - exit_price) / entry_price
        
    pnl = capital * (raw_pct * leverage)
    final_status = f"{direction}_WIN" if pnl > 0 else f"{direction}_LOSS"

    return {
        "pnl": round(pnl, 2), 
        "pct": round(raw_pct * 100 * leverage, 2), 
        "status": final_status, 
        "entry": entry_price, 
        "exit": exit_price
    }

# ---------------------------------------------------------
# 3. MAIN RUNNER
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    try:
        # Fetch max 5m candles (approx 5 days)
        return [
            {"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])}
            for c in await exchange_kucoin.fetch_ohlcv(symbol, '5m', limit=1440) 
        ]
    except: return []

async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000) -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
    
    # FETCH 5M DATA (Global)
    raw_5m = await fetch_5m_granular(symbol)
    
    history = []
    tz_map = {
        "London": ("Europe/London", 8, 0),
        "New_York": ("America/New_York", 8, 30),
        "Tokyo": ("Asia/Tokyo", 9, 0)
    }
    
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
        
        for idx in indices:
            anchor = raw_15m[idx]
            valid_dailies = [d for d in raw_daily if d['time'] < anchor['time']]
            
            # SSE Calculation (Locked to Anchor)
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
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            
            # Future Data
            future_15m = raw_15m[idx : idx+64] 
            
            # Filter 5m data relative to session start
            # We fetch a bit of buffer BEFORE the anchor to let EMA warm up
            buffer_time = anchor['time'] - (300 * 50) # 50 candles back
            future_5m = [c for c in raw_5m if c['time'] >= buffer_time]
            
            # --- RUN STRATEGY ---
            res_safe = run_mtf_strategy(
                levels["breakout_trigger"], levels["breakdown_trigger"], 
                future_15m, future_5m, leverage, capital
            )
            
            # --- CALCULATE RAW STATS (Did price break levels?) ---
            session_max = max([c['high'] for c in future_15m[:32]], default=0) 
            session_min = min([c['low'] for c in future_15m[:32]], default=0)
            
            did_bo = session_max > levels['breakout_trigger']
            did_bd = session_min < levels['breakdown_trigger']
            did_res = session_max >= levels['daily_resistance']
            did_sup = session_min <= levels['daily_support']
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "levels": levels, 
                "strategy": res_safe,
                "stats": {
                    "bo": did_bo, "bd": did_bd,
                    "hit_res": did_res, "hit_sup": did_sup
                }
            })
            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    # 3. AGGREGATE STATS (FIXED MAPPING)
    if not history: return {"history": [], "stats": {}}
    
    count = len(history)
    
    # FIX: Map 'bo_rate' to the raw 'stats.bo' flag, NOT the strategy PnL
    stats_out = {
        "bo_rate": int((sum(1 for h in history if h['stats']['bo']) / count) * 100),
        "bd_rate": int((sum(1 for h in history if h['stats']['bd']) / count) * 100),
        "res_hit_rate": int((sum(1 for h in history if h['stats']['hit_res']) / count) * 100),
        "sup_hit_rate": int((sum(1 for h in history if h['stats']['hit_sup']) / count) * 100),
        
        "safe_win_rate": int((sum(1 for h in history if h['strategy']['pnl'] > 0) / count) * 100)
    }
    
    return {"history": history, "stats": stats_out}