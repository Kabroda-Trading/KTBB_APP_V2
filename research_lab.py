# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np
import sse_engine
import ccxt.async_support as ccxt

# ---------------------------------------------------------
# MATH ENGINE (Indicators)
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
    # Fast Stochastic %K
    s_high = pd.Series(highs).rolling(period).max()
    s_low = pd.Series(lows).rolling(period).min()
    k_line = 100 * ((pd.Series(closes) - s_low) / (s_high - s_low))
    return k_line.fillna(50).tolist()

# ---------------------------------------------------------
# STRATEGY LOGIC: "Breakout + Pullback Entry"
# ---------------------------------------------------------
def run_breakout_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    """
    Executes the User's Logic:
    1. 15m Breakout/Breakdown Confirmed (2 candles hold).
    2. 5m Pullback Entry (Buy Dip / Sell Rip).
    3. 5m EMA Exit.
    """
    bo_level = levels["breakout_trigger"]
    bd_level = levels["breakdown_trigger"]
    if not bo_level or not bd_level: return {"pnl": 0, "status": "NO_LEVELS"}
    
    direction = "NONE" # "LONG" or "SHORT"
    confirm_idx = -1
    
    # 1. SCAN FOR BREAKOUT / BREAKDOWN (15m)
    for i in range(len(candles_15m) - 3):
        c = candles_15m[i]
        
        # CHECK LONG BREAKOUT
        if c['close'] > bo_level and c['open'] < bo_level:
            c1 = candles_15m[i+1]
            c2 = candles_15m[i+2]
            # Confirm: Next 2 candles stay ABOVE the trigger
            if c1['close'] > bo_level and c2['close'] > bo_level:
                direction = "LONG"
                confirm_idx = i + 2
                break
        
        # CHECK SHORT BREAKDOWN
        elif c['close'] < bd_level and c['open'] > bd_level:
            c1 = candles_15m[i+1]
            c2 = candles_15m[i+2]
            # Confirm: Next 2 candles stay BELOW the trigger
            if c1['close'] < bd_level and c2['close'] < bd_level:
                direction = "SHORT"
                confirm_idx = i + 2
                break

    if direction == "NONE":
        return {"pnl": 0, "status": "NO_CONFIRMED_SETUP", "entry": 0, "exit": 0}

    # 2. SWITCH TO 5M FOR PRECISION ENTRY
    confirm_time = candles_15m[confirm_idx]['time']
    
    c5_closes = [c['close'] for c in candles_5m]
    c5_highs = [c['high'] for c in candles_5m]
    c5_lows = [c['low'] for c in candles_5m]
    
    ema_21 = calculate_ema(c5_closes, 21)
    rsi_14 = calculate_rsi(c5_closes, 14)
    stoch_k = calculate_stoch(c5_highs, c5_lows, c5_closes)
    
    entry_price = 0.0
    exit_price = 0.0
    entry_idx = -1
    
    # SCAN FOR 5m ENTRY AFTER CONFIRMATION
    for j in range(len(candles_5m)):
        if candles_5m[j]['time'] < confirm_time: continue
        
        if entry_price == 0.0:
            if direction == "LONG":
                # BUY THE DIP: RSI < 45 (Bottom) AND Stoch < 25 (Oversold)
                if rsi_14[j] < 45 and stoch_k[j] < 25:
                    entry_price = candles_5m[j]['close']
                    entry_idx = j
                    break
            elif direction == "SHORT":
                # SELL THE RIP: RSI > 55 (Top) AND Stoch > 75 (Overbought)
                if rsi_14[j] > 55 and stoch_k[j] > 75:
                    entry_price = candles_5m[j]['close']
                    entry_idx = j
                    break
    
    if entry_price == 0.0:
        return {"pnl": 0, "status": f"{direction}_MISSED_ENTRY", "entry": 0, "exit": 0}
        
    # 3. MANAGE TRADE (EMA TRAIL)
    for k in range(entry_idx + 1, len(candles_5m)):
        current_close = candles_5m[k]['close']
        current_ema = ema_21[k]
        
        if direction == "LONG":
            # EXIT LONG: Close CROSSES BELOW 21 EMA
            if current_close < current_ema:
                exit_price = current_close
                break
        elif direction == "SHORT":
            # EXIT SHORT: Close CROSSES ABOVE 21 EMA
            if current_close > current_ema:
                exit_price = current_close
                break
                
    # Force close if data ends
    if exit_price == 0.0: exit_price = candles_5m[-1]['close']
    
    # 4. CALC PNL
    if direction == "LONG":
        pct_move = (exit_price - entry_price) / entry_price
    else: # SHORT
        pct_move = (entry_price - exit_price) / entry_price # Profit if entry > exit
        
    leveraged_pct = pct_move * leverage
    pnl_dollars = capital * leveraged_pct
    
    return {
        "pnl": round(pnl_dollars, 2),
        "pct": round(leveraged_pct * 100, 2),
        "status": f"{direction}_WIN" if pnl_dollars > 0 else f"{direction}_LOSS",
        "entry": entry_price,
        "exit": exit_price
    }

# ---------------------------------------------------------
# DATA FETCHING HELPERS
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    try:
        candles = await exchange_kucoin.fetch_ohlcv(symbol, '5m', limit=1400)
        data = []
        for c in candles:
            data.append({
                "time": int(c[0] / 1000), "open": float(c[1]), 
                "high": float(c[2]), "low": float(c[3]), 
                "close": float(c[4]), "volume": float(c[5])
            })
        return data
    except: return []

# ---------------------------------------------------------
# MAIN RUNNER
# ---------------------------------------------------------
async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000) -> List[Dict]:
    raw_15m = inputs.get("intraday_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
    
    # Fetch High-Res Data
    raw_5m = await fetch_5m_granular(symbol)
    
    history = []
    
    # Process Sessions
    tz_map = {
        "London": ("Europe/London", 8, 0),
        "New_York": ("America/New_York", 8, 30),
        "Tokyo": ("Asia/Tokyo", 9, 0),
        "Sydney": ("Australia/Sydney", 7, 0)
    }
    
    for s_key in session_keys:
        target = (0,0,0)
        for k,v in tz_map.items():
            if k in s_key or s_key in k: target = v
        if target == (0,0,0): continue 
        
        indices = []
        import pytz
        market_tz = pytz.timezone(target[0])
        for i in range(len(raw_15m)-1, -1, -1):
            dt = datetime.fromtimestamp(raw_15m[i]['time'], tz=pytz.UTC).astimezone(market_tz)
            if dt.hour == target[1] and dt.minute == target[2]:
                indices.append(i)
            if len(indices) >= 5: break
        
        for idx in indices:
            anchor = raw_15m[idx]
            
            # SSE Engine
            sse_input = {
                "raw_15m_candles": raw_15m[:idx], 
                "slice_24h": raw_15m[max(0, idx-96):idx],
                "slice_4h": raw_15m[max(0, idx-16):idx],
                "session_open_price": anchor.get("open", 0.0),
                "r30_high": anchor.get("high", 0.0),
                "r30_low": anchor.get("low", 0.0),
                "last_price": anchor.get("close", 0.0)
            }
            levels = sse_engine.compute_sse_levels(sse_input)["levels"]
            
            # Run Strategy
            future_15m = raw_15m[idx:]
            future_5m = [c for c in raw_5m if c['time'] >= anchor['time']]
            
            result = run_breakout_strategy(levels, future_15m, future_5m, leverage, capital)
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "levels": levels,
                "strategy": result
            })
            
    history.sort(key=lambda x: x['date'], reverse=True)
    return history