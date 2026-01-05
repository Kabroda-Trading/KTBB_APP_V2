# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz

# NEW: Import the Master Strategy Auditor
import strategy_auditor

# ---------------------------------------------------------
# 1. MATH & INDICATORS
# ---------------------------------------------------------
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def calculate_sma(prices: List[float], period: int = 50) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).rolling(window=period).mean().tolist()

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1: return 0.0
    tr_sum = 0.0
    for i in range(1, period + 1):
        hl = highs[-i] - lows[-i]
        hc = abs(highs[-i] - closes[-i-1])
        lc = abs(lows[-i] - closes[-i-1])
        tr_sum += max(hl, hc, lc)
    return tr_sum / period

# ---------------------------------------------------------
# 2. CONTEXT ENGINE
# ---------------------------------------------------------
def detect_regime(candles_15m: List[Dict], bias_score: float, levels: Dict) -> str:
    if not candles_15m: return "UNKNOWN"
    highs = [c['high'] for c in candles_15m]
    lows = [c['low'] for c in candles_15m]
    closes = [c['close'] for c in candles_15m]
    atr = calculate_atr(highs, lows, closes, 14)
    price = closes[-1]
    
    atr_pct = (atr / price) * 100
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    range_pct = ((bo - bd) / price) * 100 if price > 0 else 0
    
    if range_pct < 0.5 and atr_pct < 0.2: return "COMPRESSED"
    if abs(bias_score) > 0.25: return "DIRECTIONAL"
    if abs(bias_score) <= 0.25 and range_pct > 0.5: return "ROTATIONAL"
    if atr_pct > 0.5 and abs(bias_score) <= 0.25: return "VOLATILE"
    return "ROTATIONAL"

# ---------------------------------------------------------
# 3. STRATEGY EXECUTION (NOW WITH AUDIT)
# ---------------------------------------------------------

def execute_strategy_simulation(strategy_id: str, levels: Dict, 
                              candles_15m: List[Dict], candles_5m: List[Dict], 
                              leverage: float, capital: float):
    
    # 1. FIND POTENTIAL SETUP (Scanning Phase)
    # We look for the first valid trigger in the session
    
    direction = "NONE"
    setup_time = 0
    entry_price = 0.0
    
    # S4 LOGIC (Specific Scan)
    if strategy_id == "S4":
        vah = levels.get("f24_vah", 0)
        val = levels.get("f24_val", 0)
        # Scan for Rejection at edges
        for i in range(1, len(candles_15m)):
            prev, curr = candles_15m[i-1], candles_15m[i]
            # Short Setup: Poke above VAH then Close inside
            if prev['high'] > vah and curr['close'] < vah:
                direction = "SHORT"; setup_time = curr['time']; entry_price = curr['close']; break
            # Long Setup: Poke below VAL then Close inside
            if prev['low'] < val and curr['close'] > val:
                direction = "LONG"; setup_time = curr['time']; entry_price = curr['close']; break
    
    # DEFAULT / OTHER STRATEGIES (Placeholder for S0-S9 expansion)
    elif strategy_id == "S0":
        return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": {}}
    else:
        # Fallback to simple logic for others for now
        return {"pnl": 0, "status": "NOT_IMPLEMENTED_YET", "entry": 0, "exit": 0, "audit": {}}

    if direction == "NONE":
        return {"pnl": 0, "status": "NO_SETUP_FOUND", "entry": 0, "exit": 0, "audit": {}}

    # 2. AUDIT THE SETUP (The Forensic Check)
    # We pass the candidate trade to the Auditor BEFORE executing
    audit_result = strategy_auditor.audit_s4(
        levels, entry_price, setup_time, direction.lower(), candles_15m, candles_5m
    )
    
    # If the audit says INVALID, we record it but DO NOT trade (or trade with warning)
    # For simulation, we will trade it to show PnL, but tag it with the Audit Code.
    
    # 3. EXECUTE TRADE (The PnL Check)
    # Use the Auditor's calculated Stop and Target
    stop_loss = audit_result["stop_loss"]
    target = audit_result["target"]
    
    exit_price = entry_price # Default if flat
    status = "OPEN"
    
    # Simulate forward from setup time
    for c in candles_5m:
        if c['time'] <= setup_time: continue
        
        if direction == "LONG":
            if c['low'] <= stop_loss: exit_price = stop_loss; status = "STOPPED_OUT"; break
            if c['high'] >= target:   exit_price = target;    status = "TAKE_PROFIT"; break
        else: # SHORT
            if c['high'] >= stop_loss: exit_price = stop_loss; status = "STOPPED_OUT"; break
            if c['low'] <= target:     exit_price = target;    status = "TAKE_PROFIT"; break
            
    # 4. CALCULATE PNL
    raw_pct = (exit_price - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_price) / entry_price
    pnl = capital * (raw_pct * leverage)
    
    return {
        "pnl": round(pnl, 2),
        "status": status,
        "entry": entry_price,
        "exit": exit_price,
        "audit": audit_result  # Embed the forensic report
    }

# ---------------------------------------------------------
# 4. MAIN RUNNER
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
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
    regime_stats = { "COMPRESSED": [], "DIRECTIONAL": [], "ROTATIONAL": [], "VOLATILE": [] }
    
    tz_map = {"London": ("Europe/London", 8, 0), "New_York": ("America/New_York", 8, 30)}
    
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
            bias_score = computed["bias_model"]["daily_lean"]["score"]
            
            future_15m = raw_15m[idx : idx+64] 
            regime = detect_regime(future_15m[:16], bias_score, levels)
            
            buffer_time = anchor['time'] - (300 * 50)
            future_5m = [c for c in raw_5m if c['time'] >= buffer_time]
            
            # --- EXECUTE WITH NEW LOGIC ---
            result = execute_strategy_simulation(strategy_mode, levels, future_15m, future_5m, leverage, capital)
            
            entry = {
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "regime": regime,
                "levels": levels, 
                "strategy": result # Now contains 'audit' block
            }
            history.append(entry)
            regime_stats[regime].append(result["pnl"] > 0)

            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    # 4. FIND EXEMPLAR (Best Valid Trade)
    exemplar = None
    best_score = -999
    for h in history:
        # Prioritize VALID Audits over just raw PnL
        audit = h['strategy'].get('audit', {})
        if h['strategy']['pnl'] > 0 and audit.get('valid', False):
            score = h['strategy']['pnl'] + audit.get('quality', 0)
            if score > best_score:
                best_score = score
                exemplar = h
    
    # 5. STATS
    count = len(history)
    wins = sum(1 for h in history if h['strategy']['pnl'] > 0)
    valid_attempts = sum(1 for h in history if "NO_" not in h['strategy']['status'] and "S0" not in h['strategy']['status'])
    win_rate = int((wins/valid_attempts)*100) if valid_attempts > 0 else 0
    total_pnl = sum(h['strategy']['pnl'] for h in history)
    
    regime_breakdown = {}
    for r, results in regime_stats.items():
        if results:
            w = sum(1 for x in results if x)
            regime_breakdown[r] = int((w / len(results)) * 100)
        else:
            regime_breakdown[r] = 0

    stats_out = {
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "valid_trades": valid_attempts,
        "total_sessions": count,
        "regime_breakdown": regime_breakdown,
        "exemplar": exemplar
    }
    
    return {"history": history, "stats": stats_out}