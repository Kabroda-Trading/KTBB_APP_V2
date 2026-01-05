# research_lab.py
# (No changes needed if you updated it in the previous step, but re-pasting for safety)
from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor

# --- MATH HELPERS (PRESERVED) ---
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
    
    risk_settings = { "mode": risk_mode, "value": float(capital), "leverage": float(leverage) }
    
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
            if strategy_mode == "S0": result = strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S1": result = strategy_auditor.run_s1_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S2": result = strategy_auditor.run_s2_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S3": result = strategy_auditor.run_s3_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S4": result = strategy_auditor.run_s4_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S5": result = strategy_auditor.run_s5_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S6": result = strategy_auditor.run_s6_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S7": result = strategy_auditor.run_s7_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S8": result = strategy_auditor.run_s8_logic(levels, future_15m, future_5m, risk_settings, regime)
            elif strategy_mode == "S9": result = strategy_auditor.run_s9_logic(levels, future_15m, future_5m, risk_settings, regime)
            else: result = strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "regime": regime,
                "levels": levels, 
                "strategy": result 
            })
            regime_stats[regime].append(result["pnl"] > 0)

    history.sort(key=lambda x: x['date'], reverse=True)
    
    # Stats
    valid_wins = 0; valid_attempts = 0; valid_pnl_total = 0.0; exemplar = None; best_score = -999
    
    for h in history:
        res = h['strategy']
        is_valid = res.get('audit', {}).get('valid', False)
        
        # S3 Special Case: Valid Discipline = Valid "Win"
        if strategy_mode == "S3" and is_valid:
            valid_attempts += 1; valid_wins += 1 # 100% win rate for discipline
        elif is_valid:
            valid_attempts += 1; valid_pnl_total += res['pnl']
            if res['pnl'] > 0: valid_wins += 1
            score = res['pnl'] + 1000
            if score > best_score: best_score = score; exemplar = h

    win_rate = int((valid_wins/valid_attempts)*100) if valid_attempts > 0 else 0
    
    regime_breakdown = {}
    for r, results in regime_stats.items():
        if results:
            w = sum(1 for x in results if x)
            regime_breakdown[r] = int((w / len(results)) * 100)
        else:
            regime_breakdown[r] = 0

    stats_out = {
        "win_rate": win_rate,
        "total_pnl": valid_pnl_total,
        "valid_trades": valid_attempts,
        "total_sessions": len(history),
        "regime_breakdown": regime_breakdown,
        "exemplar": exemplar
    }
    
    return {"history": history, "stats": stats_out}