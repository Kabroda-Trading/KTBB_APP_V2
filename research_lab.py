# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz

# ---------------------------------------------------------
# 1. MATH & INDICATORS
# ---------------------------------------------------------
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def calculate_sma(prices: List[float], period: int = 50) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).rolling(window=period).mean().tolist()

# ---------------------------------------------------------
# 2. STRATEGY LIBRARY (S0 - S9)
# ---------------------------------------------------------

# --- S0: HOLD FIRE (No Trade) ---
def run_s0_strategy(*args):
    """Default state. Protect capital."""
    return {"pnl": 0, "pct": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0}

# --- S1: BREAKOUT ACCEPTANCE (Long Directional) ---
def run_s1_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    bo = levels.get("breakout_trigger")
    if not bo: return {"pnl": 0, "status": "NO_LEVELS"}
    
    # Setup: 15m Close > Breakout Trigger + Confirmation (2 candles)
    direction = "NONE"; setup_time = 0
    for i in range(len(candles_15m) - 2):
        c1, c2 = candles_15m[i], candles_15m[i+1]
        if c1['close'] > bo and c2['close'] > bo: 
            direction = "LONG"; setup_time = c2['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Directional = EMA Trail
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA")

# --- S2: BREAKDOWN ACCEPTANCE (Short Directional) ---
def run_s2_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    bd = levels.get("breakdown_trigger")
    if not bd: return {"pnl": 0, "status": "NO_LEVELS"}
    
    # Setup: 15m Close < Breakdown Trigger + Confirmation (2 candles)
    direction = "NONE"; setup_time = 0
    for i in range(len(candles_15m) - 2):
        c1, c2 = candles_15m[i], candles_15m[i+1]
        if c1['close'] < bd and c2['close'] < bd: 
            direction = "SHORT"; setup_time = c2['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Directional = EMA Trail
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA")

# --- S3: FAILED BREAKOUT (Trap Reversal) ---
def run_s3_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    bo, bd = levels.get("breakout_trigger"), levels.get("breakdown_trigger")
    poc = levels.get("f24_poc", 0)
    if not bo or not bd: return {"pnl": 0, "status": "NO_LEVELS"}
    
    direction = "NONE"; setup_time = 0
    for i in range(1, len(candles_15m)):
        prev, curr = candles_15m[i-1], candles_15m[i]
        
        # Bull Trap: Price broke BO, then closed back below BO
        if prev['high'] > bo and curr['close'] < bo and curr['open'] > bo:
            direction = "SHORT"; setup_time = curr['time']; break
            
        # Bear Trap: Price broke BD, then closed back above BD
        if prev['low'] < bd and curr['close'] > bd and curr['open'] < bd:
            direction = "LONG"; setup_time = curr['time']; break

    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Rotational = Target POC
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="TARGET", target_price=poc)

# --- S4: MID-BAND FADE (Rotational) ---
def run_s4_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    vah, val = levels.get("f24_vah", 0), levels.get("f24_val", 0)
    poc = levels.get("f24_poc", 0)
    if not vah: return {"pnl": 0, "status": "NO_VALUE_DATA"}
    
    direction = "NONE"; setup_time = 0
    for i in range(1, len(candles_15m)):
        prev, curr = candles_15m[i-1], candles_15m[i]
        # Fade High: Was > VAH, Closed < VAH
        if prev['high'] > vah and curr['close'] < vah:
            direction = "SHORT"; setup_time = curr['time']; break
        # Fade Low: Was < VAL, Closed > VAL
        if prev['low'] < val and curr['close'] > val:
            direction = "LONG"; setup_time = curr['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Rotational = Target POC
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="TARGET", target_price=poc)

# --- S5: RANGE EXTREMES (Rotational) ---
def run_s5_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    dr, ds = levels.get("daily_resistance", 0), levels.get("daily_support", 0)
    mid = (dr + ds) / 2
    if not dr: return {"pnl": 0, "status": "NO_LEVELS"}
    
    direction = "NONE"; setup_time = 0
    for i in range(len(candles_15m)):
        c = candles_15m[i]
        # Reject Resistance
        if c['high'] >= dr and c['close'] < dr:
            direction = "SHORT"; setup_time = c['time']; break
        # Reject Support
        if c['low'] <= ds and c['close'] > ds:
            direction = "LONG"; setup_time = c['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Rotational = Target Mid-Range
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="TARGET", target_price=mid)

# --- S6: VALUE ROTATION (Rotational) ---
def run_s6_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    vah, val = levels.get("f24_vah", 0), levels.get("f24_val", 0)
    poc = levels.get("f24_poc", 0)
    
    direction = "NONE"; setup_time = 0
    for i in range(len(candles_15m)):
        c = candles_15m[i]
        # VAH Rejection (Short)
        if c['high'] >= vah and c['close'] < vah and c['close'] > val:
            direction = "SHORT"; setup_time = c['time']; break
        # VAL Rejection (Long)
        if c['low'] <= val and c['close'] > val and c['close'] < vah:
            direction = "LONG"; setup_time = c['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Rotational = Target POC
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="TARGET", target_price=poc)

# --- S7: TREND PULLBACK (Directional) ---
def run_s7_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    closes_15 = [c['close'] for c in candles_15m]
    sma_50 = calculate_sma(closes_15, 50)
    
    direction = "NONE"; setup_time = 0
    for i in range(50, len(candles_15m)-1):
        prev, curr = candles_15m[i-1], candles_15m[i]
        trend_up = curr['close'] > sma_50[i]
        trend_dn = curr['close'] < sma_50[i]
        
        # Bull Flag logic (simplified)
        if trend_up and curr['low'] < prev['low'] and curr['close'] > prev['close']:
            direction = "LONG"; setup_time = curr['time']; break
        
        if trend_dn and curr['high'] > prev['high'] and curr['close'] < prev['close']:
            direction = "SHORT"; setup_time = curr['time']; break

    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Directional = EMA Trail
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA")

# --- S8: MOMENTUM EXPANSION (Directional) ---
def run_s8_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    direction = "NONE"; setup_time = 0
    # Volatility Expansion: Current range > 2x average range of previous 2 candles
    for i in range(3, len(candles_15m)):
        curr = candles_15m[i]
        range_curr = curr['high'] - curr['low']
        avg_range = (candles_15m[i-1]['high'] - candles_15m[i-1]['low'] + 
                     candles_15m[i-2]['high'] - candles_15m[i-2]['low']) / 2
        
        if range_curr > (avg_range * 2.0): 
            if curr['close'] > curr['open']: direction = "LONG"
            else: direction = "SHORT"
            setup_time = curr['time']
            break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Directional = EMA Trail
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA")

# --- S9: EXHAUSTION REVERSAL (Rotational) ---
def run_s9_strategy(levels: Dict, candles_15m: List[Dict], candles_5m: List[Dict], leverage: float, capital: float):
    poc = levels.get("f24_poc", 0)
    direction = "NONE"; setup_time = 0
    bo, bd = levels.get("breakout_trigger"), levels.get("breakdown_trigger")
    
    # Reversal Logic: Price pushes far beyond trigger, then fails/wicks back
    for i in range(1, len(candles_15m)):
        curr = candles_15m[i]
        if curr['high'] > bo and curr['close'] < curr['open']: # Exhaustion High
            direction = "SHORT"; setup_time = curr['time']; break
        if curr['low'] < bd and curr['close'] > curr['open']: # Exhaustion Low
            direction = "LONG"; setup_time = curr['time']; break
            
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0}
    # Exit: Rotational = Target POC
    return _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="TARGET", target_price=poc)


# --- SHARED EXECUTION ENGINE (5m Structure + Exit Logic) ---
def _execute_5m_trade(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA", target_price=0.0):
    c5_closes = [c['close'] for c in candles_5m]
    ema_21 = calculate_ema(c5_closes, 21)
    
    entry_price = 0.0; exit_price = 0.0; entry_idx = -1; status = "WAITING"
    
    # 1. ENTRY (Gate: Price must be on correct side of 21 EMA)
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        if c['time'] < setup_time: continue
        if i >= len(ema_21): break
        
        if direction == "LONG" and c['close'] > ema_21[i]:
            entry_price = c['close']; entry_idx = i; status = "IN_TRADE"; break
        elif direction == "SHORT" and c['close'] < ema_21[i]:
            entry_price = c['close']; entry_idx = i; status = "IN_TRADE"; break
            
    if status != "IN_TRADE": return {"pnl": 0, "pct": 0, "status": "NO_5M_ENTRY", "entry": 0, "exit": 0}

    # 2. EXIT MANAGEMENT
    exit_price = candles_5m[-1]['close'] # Default End of Session
    
    for i in range(entry_idx + 1, len(candles_5m)):
        c = candles_5m[i]
        
        # A) TARGET EXIT (For Rotational Strategies)
        if exit_mode == "TARGET":
            if direction == "LONG" and c['high'] >= target_price:
                exit_price = target_price; status = "HIT_TARGET"; break
            elif direction == "SHORT" and c['low'] <= target_price:
                exit_price = target_price; status = "HIT_TARGET"; break
                
        # B) EMA TRAILING EXIT (For Directional Strategies)
        if exit_mode == "EMA":
            if direction == "LONG" and c['close'] < ema_21[i]:
                exit_price = c['close']; status = "EXIT_EMA"; break
            elif direction == "SHORT" and c['close'] > ema_21[i]:
                exit_price = c['close']; status = "EXIT_EMA"; break

    # 3. PNL
    raw_pct = (exit_price - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_price) / entry_price
    pnl = capital * (raw_pct * leverage)
    
    return {"pnl": round(pnl, 2), "pct": round(raw_pct*100*leverage, 2), "status": f"{direction}_{status}", "entry": entry_price, "exit": exit_price}


# ---------------------------------------------------------
# 3. MAIN RUNNER (Router)
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    # Fix for symbol format (BTCUSDT -> BTC/USDT)
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=1440)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])} for c in candles]
    except: return []

async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000, strategy_mode: str = "S0") -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_5m = await fetch_5m_granular(symbol)
    
    history = []
    tz_map = {"London": ("Europe/London", 8, 0), "New_York": ("America/New_York", 8, 30), "Tokyo": ("Asia/Tokyo", 9, 0)}
    
    for s_key in session_keys:
        target = (0,0,0)
        for k,v in tz_map.items(): 
            if k in s_key or s_key in k: target = v
        if target == (0,0,0): continue 
        
        market_tz = pytz.timezone(target[0])
        indices = []
        for i in range(len(raw_15m)-1, -1, -1):
            dt = datetime.fromtimestamp(raw_15m[i]['time'], tz=pytz.UTC).astimezone(market_tz)
            if dt.hour == target[1] and dt.minute == target[2]: indices.append(i)
            if len(indices) >= 20: break 
        
        for idx in indices:
            anchor = raw_15m[idx]
            valid_dailies = [d for d in raw_daily if d['time'] < anchor['time']]
            
            sse_input = {
                "raw_15m_candles": raw_15m[:idx], "raw_daily_candles": valid_dailies,
                "slice_24h": raw_15m[max(0, idx-96):idx], "slice_4h": raw_15m[max(0, idx-16):idx],
                "session_open_price": anchor.get("open", 0.0), "r30_high": anchor.get("high", 0.0), "r30_low": anchor.get("low", 0.0), "last_price": anchor.get("close", 0.0)
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            
            future_15m = raw_15m[idx : idx+64] 
            buffer_time = anchor['time'] - (300 * 50)
            future_5m = [c for c in raw_5m if c['time'] >= buffer_time]
            
            # --- STRATEGY ROUTER ---
            result = {}
            if strategy_mode == "S0": result = run_s0_strategy()
            elif strategy_mode == "S1": result = run_s1_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S2": result = run_s2_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S3": result = run_s3_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S4": result = run_s4_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S5": result = run_s5_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S6": result = run_s6_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S7": result = run_s7_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S8": result = run_s8_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S9": result = run_s9_strategy(levels, future_15m, future_5m, leverage, capital)
            else: result = run_s0_strategy()
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "levels": levels, 
                "strategy": result
            })
            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    # Stats Aggregation
    count = len(history)
    wins = sum(1 for h in history if h['strategy']['pnl'] > 0)
    valid = sum(1 for h in history if "NO_" not in h['strategy']['status'] and "S0" not in h['strategy']['status'])
    win_rate = int((wins/valid)*100) if valid > 0 else 0
    total_pnl = sum(h['strategy']['pnl'] for h in history)

    stats_out = {
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "valid_trades": valid,
        "total_sessions": count
    }
    
    return {"history": history, "stats": stats_out}