# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v8.1 (SESSION LOCK DOCTRINE)
# ==============================================================================
# Updates:
# - IMPLEMENTED: Step A2 - Session Locking.
# - FIXED: Levels are calculated ONLY from the 30m Lock Window (Open -> Open+30m).
# - ADDED: In-Memory Persistence (LOCKED_SESSIONS) to prevent drift.
# - ADDED: 'CALIBRATING' state for the first 30 mins of a session.
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor
import traceback

# --- GLOBAL PERSISTENCE (In-Memory Cache) ---
# Stores locked levels to prevent re-computation/drift during a session.
# Key: "{symbol}_{session_name}_{anchor_date_str}"
# Value: Dict with levels, r30, timestamp, etc.
LOCKED_SESSIONS = {}

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

def _resample_15m(raw_5m: List[Dict]) -> List[Dict]:
    """Helper to convert 5m candles to 15m for structure checks."""
    if not raw_5m: return []
    df = pd.DataFrame(raw_5m)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    
    ohlc = {
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'time': 'first'
    }
    try:
        df_15m = df.resample('15min').agg(ohlc).dropna()
        return df_15m.to_dict('records')
    except:
        return []

def detect_regime(candles_15m: List[Dict], bias_score: float, levels: Dict) -> str:
    if not candles_15m: return "UNKNOWN"
    closes = [c['close'] for c in candles_15m]
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    price = closes[-1]
    
    # 1. Live Directional Override (Explicit Acceptance)
    if price > bo * 1.001: return "DIRECTIONAL"
    if price < bd * 0.999: return "DIRECTIONAL"
    
    # 2. Standard Logic
    range_pct = ((bo - bd) / price) * 100 if price > 0 else 0
    if abs(bias_score) > 0.25: return "DIRECTIONAL"
    if range_pct > 0.5: return "ROTATIONAL"
    return "COMPRESSED"

def detect_phase(candles_15m: List[Dict], candles_5m: List[Dict], levels: Dict) -> str:
    """
    Determines the current Market Phase based on sequence:
    BALANCE -> CANDIDATE (Acceptance) -> CONFIRMED (Pullback Hold) -> FAILED (Trap)
    """
    if not candles_15m or not candles_5m: return "UNKNOWN"
    
    price = candles_5m[-1]['close']
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    
    # 1. CHECK ACCEPTANCE (Last 2 15m closes outside)
    c1 = candles_15m[-1]['close']
    c2 = candles_15m[-2]['close'] if len(candles_15m) > 1 else c1
    
    bull_accept = c1 > bo and c2 > bo
    bear_accept = c1 < bd and c2 < bd
    
    # 2. CHECK FAILED BREAK (TRAP)
    if (c2 > bo and c1 < vah): return "FAILED_BREAK_BULL"
    if (c2 < bd and c1 > val): return "FAILED_BREAK_BEAR"
    
    # 3. CHECK CONFIRMATION (Pullback Hold)
    if bull_accept:
        recent_lows = [c['low'] for c in candles_5m[-6:]]
        dipped = any(l <= bo * 1.001 for l in recent_lows)
        held = price > bo
        if dipped and held: return "DIRECTIONAL_CONFIRMED_BULL"
        return "DIRECTIONAL_CANDIDATE_BULL"

    if bear_accept:
        recent_highs = [c['high'] for c in candles_5m[-6:]]
        dipped = any(h >= bd * 0.999 for h in recent_highs)
        held = price < bd
        if dipped and held: return "DIRECTIONAL_CONFIRMED_BEAR"
        return "DIRECTIONAL_CANDIDATE_BEAR"

    # 4. DEFAULT
    if val <= price <= vah: return "BALANCE"
    return "TESTING_EDGE"

# --- MAIN RUNNER ---
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        # Fetch 2 days of history to ensure we cover the anchor point
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=576)
        return [{
            "time": int(c[0]/1000), 
            "open": float(c[1]), 
            "high": float(c[2]), 
            "low": float(c[3]), 
            "close": float(c[4]),
            "volume": float(c[5])
        } for c in candles]
    except: return []

# --- SESSION CONFIGURATION ---
SESSION_CONFIGS = [
    {"name": "New York", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

def _get_dominant_session(now_utc: datetime) -> Dict[str, Any]:
    """
    Finds the session that opened most recently.
    """
    candidates = []
    for s in SESSION_CONFIGS:
        tz = pytz.timezone(s["tz"])
        now_local = now_utc.astimezone(tz)
        
        # Today's open
        open_time = now_local.replace(hour=s["open_h"], minute=s["open_m"], second=0, microsecond=0)
        
        # If open_time is in future, consider yesterday's open
        if now_local < open_time:
            open_time = open_time - timedelta(days=1)
            
        # Time since open
        diff = (now_local - open_time).total_seconds()
        
        candidates.append({
            "name": s["name"],
            "tz": s["tz"],
            "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()),
            "anchor_fmt": open_time.strftime("%H:%M") + " " + s["name"].upper(),
            "date_key": open_time.strftime("%Y-%m-%d"),
            "diff": diff
        })
    
    candidates.sort(key=lambda x: x['diff'])
    return candidates[0]

# --- LIVE PULSE FUNCTION (SESSION LOCKED) ---
async def run_live_pulse(symbol: str, risk_mode: str = "fixed_margin", capital: float = 1000, leverage: float = 1) -> Dict[str, Any]:
    raw_5m = await fetch_5m_granular(symbol)
    if not raw_5m: return {"error": "No data"}
    
    # 1. Determine Session Anchor
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    session_info = _get_dominant_session(now_utc)
    
    anchor_ts = session_info["anchor_time"]
    lock_end_ts = anchor_ts + 1800 # 30 minutes after open
    
    # Generate Unique Session Key for Cache
    session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
    
    levels = {}
    bias_score = 0
    levels_source = "UNKNOWN"
    
    # 2. CHECK CACHE (Persistence Rule)
    if session_key in LOCKED_SESSIONS:
        cached = LOCKED_SESSIONS[session_key]
        levels = cached["levels"]
        bias_score = cached["bias_score"]
        levels_source = "STORED"
    
    # 3. CHECK CALIBRATION STATUS
    elif now_ts < lock_end_ts:
        # Lock window is still open. We are CALIBRATING.
        # We do NOT compute final levels yet.
        return {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "session_lock": session_info["anchor_fmt"],
            "status": "CALIBRATING",
            "levels_source": "CALIBRATING",
            "message": f"Levels lock in {int((lock_end_ts - now_ts)/60)} min",
            "price": raw_5m[-1]['close'],
            "regime": "PENDING",
            "phase": "CALIBRATING",
            "levels": {}, # Empty levels
            "strategies": []
        }
        
    # 4. COMPUTE & LOCK (If not in cache and window closed)
    else:
        # Find indices for the CORRECT windows
        # Window A: Context (24h ending at Lock Time) -> for VAH/VAL
        # Window B: Calibration (Anchor -> Lock Time) -> for R30 High/Low
        
        lock_idx = -1
        anchor_idx = -1
        
        for i, c in enumerate(raw_5m):
            if c['time'] == anchor_ts: anchor_idx = i
            if c['time'] == lock_end_ts: lock_idx = i # Exact match preferred
            
        # Fallback if exact timestamp missing (e.g. gap)
        if lock_idx == -1: 
            # Find first candle AFTER lock time
            for i, c in enumerate(raw_5m):
                if c['time'] > lock_end_ts: 
                    lock_idx = i
                    break
        
        if anchor_idx == -1 or lock_idx == -1:
             return {"error": "Insufficient data to lock session."}

        # Context Slice: 24h ending at LOCK TIME (not rolling live)
        context_start = max(0, lock_idx - 288)
        context_slice = raw_5m[context_start : lock_idx]
        
        # Calibration Slice: The 30m window (Anchor -> Lock Time)
        calibration_slice = raw_5m[anchor_idx : lock_idx]
        
        if not calibration_slice:
             return {"error": "Empty calibration window."}

        # SSE Computation
        # Note: We override 'r30' logic to strictly use calibration_slice
        r30_high = max(c['high'] for c in calibration_slice)
        r30_low = min(c['low'] for c in calibration_slice)
        open_price = calibration_slice[0]['open']
        
        sse_input = {
            "raw_15m_candles": context_slice, 
            "raw_daily_candles": [], 
            "slice_24h": context_slice,
            "slice_4h": context_slice[-48:],
            "session_open_price": open_price, 
            "r30_high": r30_high, 
            "r30_low": r30_low,
            "last_price": context_slice[-1]['close']
        }
        
        try:
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed["levels"]
            bias_score = computed["bias_model"]["daily_lean"]["score"]
            
            # STORE IN CACHE
            LOCKED_SESSIONS[session_key] = {
                "levels": levels,
                "bias_score": bias_score,
                "locked_at": datetime.now(timezone.utc).isoformat()
            }
            levels_source = "COMPUTED (NEW LOCK)"
            
        except Exception as e:
            print(f"SSE ENGINE ERROR: {e}")
            return {"error": f"Level Calc Failed: {str(e)}"}

    # 5. Live Analysis (Phase/Regime)
    # Using LIVE price against LOCKED levels
    
    # Re-find anchor index for live slicing
    anchor_idx_live = -1
    for i, c in enumerate(raw_5m):
        if c['time'] >= anchor_ts:
            anchor_idx_live = i
            break
            
    # Build 15m candles from live data for Structure Check
    candles_15m = _resample_15m(raw_5m)
    
    regime = detect_regime(candles_15m[-30:], bias_score, levels)
    phase = detect_phase(candles_15m[-30:], raw_5m[-48:], levels)
    
    risk_settings = { "mode": risk_mode, "value": float(capital), "leverage": float(leverage) }
    strategies = {
        "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic,
        "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic,
        "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic,
        "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic
    }
    
    results = []
    # Only run strategies if we have valid levels
    if levels:
        for code, func in strategies.items():
            try:
                # Use live data slice for logic
                live_slice = raw_5m[anchor_idx_live:] if anchor_idx_live != -1 else raw_5m[-100:]
                res = func(levels, candles_15m[-30:], live_slice, risk_settings, regime)
                
                status = "STANDBY"; color = "#444"; msg = res['audit'].get('reason', 'Waiting...')
                
                if code == "S9" and res['status'] == "S9_ACTIVE":
                    status = "CRITICAL ALERT"; color = "#ef4444"; msg = "MARKET HALTED (Extreme)"
                
                elif phase == "BALANCE":
                    if code in ["S1", "S2", "S7"]: status = "BLOCKED"; color = "#222"; msg = "Market is Balanced."
                    elif code in ["S4", "S6"]:
                        if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"; msg = f"Entry: {res['entry']}"
                        else: status = "MONITORING"; color = "#ffcc00"; msg = "Valid Rotation Phase."

                elif "CANDIDATE" in phase:
                    if code in ["S4", "S6", "S5"]: status = "BLOCKED"; color = "#222"; msg = "Breakout Attempted."
                    elif code in ["S1", "S2"]: status = "ARMED"; color = "#ffcc00"; msg = "Acceptance. Awaiting Pullback."
                    elif code == "S7": status = "STANDBY"; color = "#444"

                elif "CONFIRMED" in phase:
                    if code in ["S4", "S6", "S5"]: status = "BLOCKED"; color = "#ef4444"; msg = "TREND ACTIVE."
                    elif code in ["S1", "S2", "S7"]:
                        if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"; msg = f"Entry: {res['entry']}"
                        else: status = "HUNTING"; color = "#00ff9d"; msg = "Trend Confirmed."

                elif "FAILED" in phase:
                    if code == "S8": status = "ARMED"; color = "#00ff9d"; msg = "Trap Detected."
                    else: status = "CAUTION"; color = "#ffcc00"; msg = "Trap Risk."

                elif res['audit'].get('valid', False):
                    if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                
                results.append({
                    "strategy": code, "status": status, "color": color, "message": msg,
                    "levels": { "stop": res['audit'].get('stop_loss',0), "target": res['audit'].get('target',0) }
                })
            except Exception as e:
                results.append({"strategy": code, "status": "ERROR", "color": "red", "message": str(e)})

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session_lock": session_info["anchor_fmt"],
        "levels_source": levels_source, # DEBUG FIELD
        "lock_end_ts": lock_end_ts,     # DEBUG FIELD
        "session_key": session_key,     # DEBUG FIELD
        "price": raw_5m[-1]['close'], 
        "regime": regime, 
        "phase": phase,
        "levels": levels, 
        "strategies": results
    }

# --- HISTORICAL ANALYSIS (PRESERVED) ---
async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000, strategy_mode: str = "S0", risk_mode: str = "fixed_margin") -> Dict[str, Any]:
    # (Existing historical code remains here - keeping it brief for the file overwrite)
    # ... [PASTE THE PREVIOUS HISTORICAL FUNCTION HERE IF NEEDED, OR IT WILL BE PRESERVED IF YOU ONLY PASTE THE TOP HALF] ...
    # To avoid truncation errors, I will include the standard historical logic below.
    
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
                "regime": regime, "levels": levels, "strategy": final_result
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