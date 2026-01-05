# research_lab.py
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor

# --- MATH HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

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
    closes = [c['close'] for c in candles_15m]
    
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    price = closes[-1]
    
    range_pct = ((bo - bd) / price) * 100 if price > 0 else 0
    
    if abs(bias_score) > 0.25: return "DIRECTIONAL"
    if range_pct > 0.5: return "ROTATIONAL"
    return "COMPRESSED"

# --- LEGACY STRATEGIES ---
def _execute_5m_trade_legacy(direction, setup_time, candles_5m, leverage, capital, exit_mode="EMA"):
    # (Simplified legacy logic for S1/S2/etc)
    return {"pnl": 0, "status": "LEGACY_SIM", "entry": 0, "exit": 0, "audit": {}}

def run_s0_strategy(): return {"pnl": 0, "status": "S0_OBSERVED", "entry": 0, "exit": 0, "audit": {}}
def run_s1_strategy(l, c15, c5, lev, cap): return _execute_5m_trade_legacy("LONG", 0, c5, lev, cap)
def run_s2_strategy(l, c15, c5, lev, cap): return _execute_5m_trade_legacy("SHORT", 0, c5, lev, cap)

# --- MAIN RUNNER ---
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
    
    # 1. Pack Risk Settings
    risk_settings = {
        "mode": risk_mode, 
        "value": float(capital),
        "leverage": float(leverage)
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
            
            # --- DISPATCHER ---
            if strategy_mode == "S4":
                result = strategy_auditor.run_s4_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S1":
                result = run_s1_strategy(levels, future_15m, future_5m, leverage, capital)
            elif strategy_mode == "S2":
                result = run_s2_strategy(levels, future_15m, future_5m, leverage, capital)
            else:
                result = run_s0_strategy()
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "regime": regime,
                "levels": levels, 
                "strategy": result 
            })
            regime_stats[regime].append(result["pnl"] > 0)

    history.sort(key=lambda x: x['date'], reverse=True)
    
    # Simple Stats
    wins = sum(1 for h in history if h['strategy']['pnl'] > 0)
    count = len(history)
    win_rate = int((wins/count)*100) if count > 0 else 0
    total_pnl = sum(h['strategy']['pnl'] for h in history)
    
    return {"history": history, "stats": {"win_rate": win_rate, "total_pnl": total_pnl, "total_sessions": count, "valid_trades": count, "regime_breakdown": {}, "exemplar": None}}