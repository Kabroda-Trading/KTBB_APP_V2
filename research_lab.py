# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np
import sse_engine
import ccxt.async_support as ccxt
import pytz

# ---------------------------------------------------------
# 1. MATH ENGINE
# ---------------------------------------------------------
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    if len(prices) < period: return []
    series = pd.Series(prices)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return (100 - (100 / (1 + rs))).fillna(50).tolist()

def calculate_stoch(highs, lows, closes, period=14):
    s_high = pd.Series(highs).rolling(period).max()
    s_low = pd.Series(lows).rolling(period).min()
    k_line = 100 * ((pd.Series(closes) - s_low) / (s_high - s_low))
    return k_line.fillna(50).tolist()

# ---------------------------------------------------------
# 2. STRATEGY ENGINE (Breakout + Pullback)
# ---------------------------------------------------------
def run_breakout_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    bo_level = levels["breakout_trigger"]
    bd_level = levels["breakdown_trigger"]
    if not bo_level or not bd_level: return {"pnl": 0, "status": "NO_LEVELS"}
    
    direction = "NONE"
    confirm_idx = -1
    
    # Scan for 15m Break/Confirm
    for i in range(len(candles_15m) - 3):
        c = candles_15m[i]
        if c['close'] > bo_level and c['open'] < bo_level:
            if candles_15m[i+1]['close'] > bo_level and candles_15m[i+2]['close'] > bo_level:
                direction = "LONG"; confirm_idx = i + 2; break
        elif c['close'] < bd_level and c['open'] > bd_level:
            if candles_15m[i+1]['close'] < bd_level and candles_15m[i+2]['close'] < bd_level:
                direction = "SHORT"; confirm_idx = i + 2; break

    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}

    # 5m Entry/Exit
    confirm_time = candles_15m[confirm_idx]['time']
    c5_closes = [c['close'] for c in candles_5m]
    c5_highs = [c['high'] for c in candles_5m]
    c5_lows = [c['low'] for c in candles_5m]
    
    ema_21 = calculate_ema(c5_closes, 21)
    rsi_14 = calculate_rsi(c5_closes, 14)
    stoch_k = calculate_stoch(c5_highs, c5_lows, c5_closes)
    
    entry_price = 0.0; exit_price = 0.0; entry_idx = -1
    
    for j in range(len(candles_5m)):
        if candles_5m[j]['time'] < confirm_time: continue
        if entry_price == 0.0:
            if direction == "LONG" and rsi_14[j] < 45 and stoch_k[j] < 25:
                entry_price = candles_5m[j]['close']; entry_idx = j; break
            elif direction == "SHORT" and rsi_14[j] > 55 and stoch_k[j] > 75:
                entry_price = candles_5m[j]['close']; entry_idx = j; break
    
    if entry_price == 0.0: return {"pnl": 0, "status": "MISSED_ENTRY", "entry": 0, "exit": 0}
        
    for k in range(entry_idx + 1, len(candles_5m)):
        if direction == "LONG" and candles_5m[k]['close'] < ema_21[k]:
            exit_price = candles_5m[k]['close']; break
        elif direction == "SHORT" and candles_5m[k]['close'] > ema_21[k]:
            exit_price = candles_5m[k]['close']; break
            
    if exit_price == 0.0: exit_price = candles_5m[-1]['close']
    
    pct_move = (exit_price - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_price) / entry_price
    pnl = capital * (pct_move * leverage)
    return {"pnl": round(pnl, 2), "pct": round(pct_move * leverage * 100, 2), "status": f"{direction}_WIN" if pnl > 0 else f"{direction}_LOSS", "entry": entry_price, "exit": exit_price}

# ---------------------------------------------------------
# 3. STATS ENGINE (System Health) - NEW!
# ---------------------------------------------------------
def run_system_stats(history: List[Dict]) -> Dict:
    """Calculates win rates and trigger probabilities."""
    total = len(history)
    if total == 0: return {}
    
    triggered_bo = 0
    triggered_bd = 0
    hit_resistance = 0
    hit_support = 0
    
    for row in history:
        # Check if price action HIT levels
        # We need to look at future candles stored in the strategy result or re-scan
        # For efficiency, we'll assume the strategy engine passed the 'future_15m' max/min
        # But since we don't have that easily, let's look at the 'status' from the breakout checker
        pass 
        # Actually, let's do a quick scan of the High/Low vs Levels
        # (This requires passing the candles back, but for now we will infer from strategy status 
        # OR we can update the main loop to store 'session_high' and 'session_low')
        
    return {
        "breakout_pct": 0, # Placeholder until we wire up the max/min
        "breakdown_pct": 0
    }

# ---------------------------------------------------------
# 4. MAIN RUNNER (Orchestrator)
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    try:
        return [
            {"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])}
            for c in await exchange_kucoin.fetch_ohlcv(symbol, '5m', limit=1400)
        ]
    except: return []

async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000) -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
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
            if len(indices) >= 10: break # Depth
        
        for idx in indices:
            anchor = raw_15m[idx]
            sse_input = {
                "raw_15m_candles": raw_15m[:idx], 
                "slice_24h": raw_15m[max(0, idx-96):idx],
                "slice_4h": raw_15m[max(0, idx-16):idx],
                "session_open_price": anchor.get("open", 0.0),
                "r30_high": anchor.get("high", 0.0),
                "r30_low": anchor.get("low", 0.0),
                "last_price": anchor.get("close", 0.0)
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            
            # Future Data for Verification
            future_15m = raw_15m[idx:]
            future_5m = [c for c in raw_5m if c['time'] >= anchor['time']]
            
            # Run Strategy
            strat_res = run_breakout_strategy(levels, future_15m, future_5m, leverage, capital)
            
            # Verification Stats (Did it hit?)
            session_max = max([c['high'] for c in future_15m[:32]], default=0) # Next 8 hours
            session_min = min([c['low'] for c in future_15m[:32]], default=0)
            
            did_breakout = session_max > levels['breakout_trigger']
            did_breakdown = session_min < levels['breakdown_trigger']
            did_hit_res = session_max >= levels['daily_resistance']
            did_hit_sup = session_min <= levels['daily_support']

            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "levels": levels,
                "strategy": strat_res,
                "stats": {
                    "bo": did_breakout, "bd": did_breakdown,
                    "hit_res": did_hit_res, "hit_sup": did_hit_sup
                }
            })
            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    # CALCULATE AGGREGATE STATS
    if not history: return {"history": [], "stats": {}}
    
    count = len(history)
    stats_out = {
        "bo_rate": int((sum(1 for h in history if h['stats']['bo']) / count) * 100),
        "bd_rate": int((sum(1 for h in history if h['stats']['bd']) / count) * 100),
        "res_hit_rate": int((sum(1 for h in history if h['stats']['hit_res']) / count) * 100),
        "sup_hit_rate": int((sum(1 for h in history if h['stats']['hit_sup']) / count) * 100)
    }
    
    return {"history": history, "stats": stats_out}