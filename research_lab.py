# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor

# ... [KEEP MATH & CONTEXT FUNCTIONS SAME AS BEFORE] ...
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

# ... [LEGACY STRATEGY FUNCTIONS KEPT FOR S1-S9 COMPATIBILITY] ...
def _execute_5m_trade_legacy(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA", target_price=0.0):
    c5_closes = [c['close'] for c in candles_5m]
    ema_21 = calculate_ema(c5_closes, 21)
    entry_price = 0.0; exit_price = 0.0; entry_idx = -1; status = "WAITING"
    
    for i in range(len(candles_5m)):
        c = candles_5m[i]
        if c['time'] < setup_time: continue
        if i >= len(ema_21): break
        if direction == "LONG" and c['close'] > ema_21[i]:
            entry_price = c['close']; entry_idx = i; status = "IN_TRADE"; break
        elif direction == "SHORT" and c['close'] < ema_21[i]:
            entry_price = c['close']; entry_idx = i; status = "IN_TRADE"; break
    if status != "IN_TRADE": return {"pnl": 0, "pct": 0, "status": "NO_5M_ENTRY", "entry": 0, "exit": 0, "audit": {}}

    exit_price = candles_5m[-1]['close'] 
    for i in range(entry_idx + 1, len(candles_5m)):
        c = candles_5m[i]
        if exit_mode == "TARGET":
            if direction == "LONG" and c['high'] >= target_price: exit_price = target_price; status = "HIT_TARGET"; break
            elif direction == "SHORT" and c['low'] <= target_price: exit_price = target_price; status = "HIT_TARGET"; break
        if exit_mode == "EMA":
            if direction == "LONG" and c['close'] < ema_21[i]: exit_price = c['close']; status = "EXIT_EMA"; break
            elif direction == "SHORT" and c['close'] > ema_21[i]: exit_price = c['close']; status = "EXIT_EMA"; break

    raw_pct = (exit_price - entry_price) / entry_price if direction == "LONG" else (entry_price - exit_price) / entry_price
    pnl = capital * (raw_pct * leverage)
    return {"pnl": round(pnl, 2), "status": f"{direction}_{status}", "entry": entry_price, "exit": exit_price, "audit": {}}

def run_s0_strategy(*args): return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": {}}
def run_s1_strategy(levels, c15, c5, lev, cap):
    bo = levels.get("breakout_trigger")
    if not bo: return {"pnl": 0, "status": "NO_LEVELS"}
    direction = "NONE"; setup_time = 0
    for i in range(len(c15) - 2):
        if c15[i]['close'] > bo and c15[i+1]['close'] > bo: direction = "LONG"; setup_time = c15[i+1]['time']; break
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0, "audit": {}}
    return _execute_5m_trade_legacy(direction, setup_time, c5, lev, cap, exit_mode="EMA")
def run_s2_strategy(levels, c15, c5, lev, cap):
    bd = levels.get("breakdown_trigger")
    if not bd: return {"pnl": 0, "status": "NO_LEVELS"}
    direction = "NONE"; setup_time = 0
    for i in range(len(c15) - 2):
        if c15[i]['close'] < bd and c15[i+1]['close'] < bd: direction = "SHORT"; setup_time = c15[i+1]['time']; break
    if direction == "NONE": return {"pnl": 0, "status": "NO_SETUP", "entry": 0, "exit": 0, "audit": {}}
    return _execute_5m_trade_legacy(direction, setup_time, c5, lev, cap, exit_mode="EMA")

# ---------------------------------------------------------
# 4. MAIN RUNNER (THE HYBRID DISPATCHER)
# ---------------------------------------------------------
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=1440)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])} for c in candles]
    except: return []

async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000, strategy_mode: str = "S0", risk_mode: str = "fixed_margin") -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    raw_daily = inputs.get("daily_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_5m = await fetch_5m_granular(symbol)
    
    # Pack Risk Settings
    risk_settings = {
        "mode": risk_mode, # 'fixed_risk' or 'fixed_margin'
        "value": capital,  # Either Risk $ or Margin $
        "leverage": leverage
    }
    
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
            
            # --- STRATEGY DISPATCHER ---
            if strategy_mode == "S4":
                # v2.1: Pass Risk Settings
                result = strategy_auditor.run_s4_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S1":
                result = run_s1_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S2":
                result = run_s2_strategy(levels, future_15m, future_5m, leverage, capital)
            else:
                result = run_s0_strategy()
            
            entry_record = {
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "regime": regime,
                "levels": levels, 
                "strategy": result 
            }
            history.append(entry_record)
            regime_stats[regime].append(result["pnl"] > 0)

            
    history.sort(key=lambda x: x['date'], reverse=True)
    
    exemplar = None
    best_score = -999
    for h in history:
        res = h['strategy']
        has_audit = res.get('audit', {}).get('valid', False)
        if res['pnl'] > 0:
            score = res['pnl']
            if has_audit: score += 1000 
            if score > best_score:
                best_score = score
                exemplar = h
    
    count = len(history)
    wins = sum(1 for h in history if h['strategy']['pnl'] > 0)
    valid_attempts = 0
    for h in history:
        s = h['strategy']
        if "NO_" in s['status'] or "S0" in s['status']: continue
        if s.get('audit') and not s['audit']['valid']: continue 
        valid_attempts += 1

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