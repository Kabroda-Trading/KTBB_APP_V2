# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v6.3 (LIVE VOLUME FIX)
# ==============================================================================
# Updates:
# - FIXED: fetch_5m_granular now includes 'volume' to prevent SSE Engine crash.
# - FIXED: "KeyError: valid" protection in historical scanner.
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime, timezone
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor
import traceback

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
    
    if price > bo * 1.001: return "DIRECTIONAL"
    if price < bd * 0.999: return "DIRECTIONAL"
    
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
        # FIX: Include 'volume' (index 5)
        return [{
            "time": int(c[0]/1000), 
            "open": float(c[1]), 
            "high": float(c[2]), 
            "low": float(c[3]), 
            "close": float(c[4]),
            "volume": float(c[5]) # <--- ADDED THIS
        } for c in candles]
    except: return []

# --- LIVE PULSE FUNCTION ---
async def run_live_pulse(symbol: str, risk_mode: str = "fixed_margin", capital: float = 1000, leverage: float = 1) -> Dict[str, Any]:
    raw_5m = await fetch_5m_granular(symbol)
    if not raw_5m: return {"error": "No data"}
    
    slice_24h = raw_5m[-288:]
    last_candle = raw_5m[-1]
    
    sse_input = {
        "raw_15m_candles": slice_24h, 
        "raw_daily_candles": [], 
        "slice_24h": slice_24h,
        "slice_4h": slice_24h[-48:],
        "session_open_price": slice_24h[0]['open'], 
        "r30_high": max(c['high'] for c in slice_24h[-6:]), 
        "r30_low": min(c['low'] for c in slice_24h[-6:]),
        "last_price": last_candle['close']
    }
    
    try:
        computed = sse_engine.compute_sse_levels(sse_input)
        levels = computed["levels"]
        bias_score = computed["bias_model"]["daily_lean"]["score"]
        regime = detect_regime(slice_24h, bias_score, levels)
    except Exception as e:
        print(f"SSE ENGINE ERROR: {e}")
        return {"error": f"Level Calc Failed: {str(e)}"}
    
    risk_settings = { "mode": risk_mode, "value": float(capital), "leverage": float(leverage) }
    strategies = {
        "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic,
        "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic,
        "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic,
        "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic
    }
    
    results = []
    for code, func in strategies.items():
        try:
            res = func(levels, slice_24h, raw_5m[-288:], risk_settings, regime)
            
            status = "STANDBY"; color = "#444"; msg = res['audit'].get('reason', 'Waiting...')
            
            if code == "S9" and res['status'] == "S9_ACTIVE":
                status = "CRITICAL ALERT"; color = "#ef4444"; msg = "MARKET HALTED (Extreme)"
            elif res['audit'].get('valid', False):
                if res['entry'] > 0:
                    status = "ACTIVE SIGNAL"; color = "#00ff9d"; msg = f"Entry found at {res['entry']}"
                else:
                    status = "MONITORING"; color = "#ffcc00"; msg = "Valid Regime. Waiting for Trigger."
            elif "INVALID" in res['audit'].get('code', ''):
                status = "BLOCKED"; color = "#222"; msg = res['audit'].get('reason', 'Regime Mismatch')
            
            results.append({
                "strategy": code, "status": status, "color": color, "message": msg,
                "levels": { "stop": res['audit'].get('stop_loss',0), "target": res['audit'].get('target',0) }
            })
        except Exception as e:
            results.append({"strategy": code, "status": "ERROR", "color": "red", "message": str(e)})

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "price": last_candle['close'], "regime": regime, "levels": levels, "strategies": results
    }

# --- HISTORICAL ANALYSIS ---
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
            sse_input = {
                "raw_15m_candles": raw_15m[:idx], 
                "raw_daily_candles": [d for d in raw_daily if d['time'] < anchor['time']],
                "slice_24h": raw_15m[max(0, idx-96):idx],
                "slice_4h": raw_15m[max(0, idx-16):idx],
                "session_open_price": anchor.get("open", 0.0), 
                "r30_high": anchor.get("high", 0.0), 
                "r30_low": anchor.get("low", 0.0), 
                "last_price": anchor.get("close", 0.0)
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            bias_score = computed["bias_model"]["daily_lean"]["score"]
            future_15m = raw_15m[idx : idx+64] 
            regime = detect_regime(future_15m[:16], bias_score, levels)
            buffer_time = anchor['time'] - (300 * 50)
            future_5m = [c for c in raw_5m if c['time'] >= buffer_time]
            
            final_result = None
            
            if strategy_mode == "ALL":
                candidates = []
                strategies = [
                    strategy_auditor.run_s1_logic, strategy_auditor.run_s2_logic,
                    strategy_auditor.run_s4_logic, strategy_auditor.run_s5_logic,
                    strategy_auditor.run_s6_logic, strategy_auditor.run_s7_logic,
                    strategy_auditor.run_s8_logic
                ]
                
                for strat_func in strategies:
                    try:
                        res = strat_func(levels, future_15m, future_5m, risk_settings, regime)
                        candidates.append(res)
                    except: continue

                try: s3_res = strategy_auditor.run_s3_logic(levels, future_15m, future_5m, risk_settings, regime)
                except: s3_res = {"audit": {"valid": False}}

                best_exec = None; best_pnl = -999999.0
                for c in candidates:
                    if c["audit"].get("valid", False) and c["pnl"] > 0:
                        if c["pnl"] > best_pnl: best_pnl = c["pnl"]; best_exec = c
                
                if best_exec: final_result = best_exec
                elif s3_res.get("audit", {}).get("valid", False): final_result = s3_res
                else: final_result = strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
            
            else:
                try:
                    mapper = {
                        "S0": strategy_auditor.run_s0_logic, "S1": strategy_auditor.run_s1_logic,
                        "S2": strategy_auditor.run_s2_logic, "S3": strategy_auditor.run_s3_logic,
                        "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic,
                        "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic,
                        "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic
                    }
                    func = mapper.get(strategy_mode, strategy_auditor.run_s0_logic)
                    final_result = func(levels, future_15m, future_5m, risk_settings, regime)
                except:
                    final_result = strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
            
            history.append({
                "session": s_key.replace("America/", "").replace("Europe/", ""),
                "date": datetime.fromtimestamp(anchor["time"]).strftime("%Y-%m-%d"),
                "regime": regime,
                "levels": levels, 
                "strategy": final_result 
            })
            regime_stats[regime].append(final_result["pnl"] > 0)

    history.sort(key=lambda x: x['date'], reverse=True)
    valid_wins = 0; valid_attempts = 0; valid_pnl_total = 0.0; exemplar = None; best_score = -999
    
    for h in history:
        res = h['strategy']
        is_valid = res.get('audit', {}).get('valid', False)
        if is_valid:
            valid_attempts += 1
            if res['status'] == "S0_OBSERVED": valid_wins += 1 
            else:
                valid_pnl_total += res['pnl']
                if res['pnl'] > 0: valid_wins += 1
            score = res['pnl'] + (1000 if is_valid else 0)
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
        "win_rate": win_rate, "total_pnl": valid_pnl_total, "valid_trades": valid_attempts,
        "total_sessions": len(history), "regime_breakdown": regime_breakdown, "exemplar": exemplar
    }
    
    return {"history": history, "stats": stats_out}