# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v10.0 (BATTLEBOX & SESSION ENERGY)
# ==============================================================================
# Updates:
# - ADDED: Session Energy Logic (Prime, Late, Dead windows).
# - ADDED: Timeline Memory (Tracks timestamps for Acceptance, Pullback, etc).
# - ADDED: 'battlebox_packet' generator for GPT/Human handoff.
# - PRESERVED: Locked Levels Doctrine (Step A2).
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
import json

# --- GLOBAL PERSISTENCE (In-Memory Cache) ---
LOCKED_SESSIONS = {}

# --- MATH HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def _resample_15m(raw_5m: List[Dict]) -> List[Dict]:
    if not raw_5m: return []
    df = pd.DataFrame(raw_5m)
    df['dt'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('dt', inplace=True)
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'time': 'first'}
    try:
        df_15m = df.resample('15min').agg(ohlc).dropna()
        return df_15m.to_dict('records')
    except: return []

# --- SHARED LEVEL COMPUTATION CORE ---
def _compute_session_packet(raw_5m: List[Dict], raw_1h: List[Dict], anchor_idx: int) -> Dict[str, Any]:
    if anchor_idx == -1 or anchor_idx + 6 > len(raw_5m):
        return {"error": "Insufficient data for calibration."}

    calibration_slice = raw_5m[anchor_idx : anchor_idx + 6]
    lock_idx = anchor_idx + 6
    context_start = max(0, lock_idx - 288)
    context_slice = raw_5m[context_start : lock_idx]
    
    lock_ts = raw_5m[lock_idx-1]['time']
    valid_1h = [c for c in raw_1h if c['time'] < lock_ts]
    
    # Simple daily deriv from 1h
    df_1h = pd.DataFrame(valid_1h)
    daily_structure = []
    if not df_1h.empty:
        df_1h['dt'] = pd.to_datetime(df_1h['time'], unit='s')
        df_1h.set_index('dt', inplace=True)
        ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'time': 'first'}
        daily_structure = df_1h.resample('1D').agg(ohlc).dropna().to_dict('records')

    r30_high = max(c['high'] for c in calibration_slice)
    r30_low = min(c['low'] for c in calibration_slice)
    open_price = calibration_slice[0]['open']
    
    sse_input = {
        "raw_15m_candles": context_slice, 
        "raw_daily_candles": daily_structure, 
        "slice_24h": context_slice,
        "slice_4h": context_slice[-48:],
        "session_open_price": open_price, 
        "r30_high": r30_high, 
        "r30_low": r30_low,
        "last_price": context_slice[-1]['close']
    }
    
    computed = sse_engine.compute_sse_levels(sse_input)
    return {
        "levels": computed["levels"],
        "bias_score": computed["bias_model"]["daily_lean"]["score"],
        "r30_high": r30_high,
        "r30_low": r30_low
    }

# --- PHASE & TIMELINE LOGIC ---
def detect_phase_advanced(candles_15m: List[Dict], candles_5m: List[Dict], levels: Dict, state_memory: Dict) -> Dict[str, Any]:
    if not candles_15m or not candles_5m: return {"phase": "UNKNOWN", "quality": ""}
    
    price = candles_5m[-1]['close']
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    
    c1 = candles_15m[-1]['close']
    c2 = candles_15m[-2]['close'] if len(candles_15m) > 1 else c1
    
    bull_accept = c1 > bo and c2 > bo
    bear_accept = c1 < bd and c2 < bd
    
    # 1. UPDATE MEMORY (Timeline Milestones)
    # Track Acceptance
    if bull_accept and not state_memory.get("bull_accept_time"):
        state_memory["bull_accept_time"] = candles_15m[-1]['time']
        state_memory["bull_high_water"] = price
    if bear_accept and not state_memory.get("bear_accept_time"):
        state_memory["bear_accept_time"] = candles_15m[-1]['time']
        state_memory["bear_low_water"] = price
        
    # 2. CHECK FAILURE (Trap)
    if state_memory.get("bull_accept_time"):
        if c1 < vah: 
            state_memory["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BULL", "msg": "Trap Detected"}
        
    if state_memory.get("bear_accept_time"):
        if c1 > val: 
            state_memory["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BEAR", "msg": "Trap Detected"}

    # 3. EVALUATE CONFIRMATION
    if bull_accept:
        accept_ts = state_memory["bull_accept_time"]
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts] or candles_5m[-6:]
        
        # Confirmed A: Retest Shelf
        retested = any(c['low'] <= bo * 1.001 for c in post_accept)
        
        if retested and price > bo:
            if not state_memory.get("bull_confirm_time"):
                state_memory["bull_confirm_time"] = candles_5m[-1]['time']
            return {"phase": "DIRECTIONAL_CONFIRMED_BULL", "quality": "A", "msg": "Shelf Converted"}
            
        return {"phase": "DIRECTIONAL_CANDIDATE_BULL", "quality": "", "msg": "Acceptance. Awaiting Pullback."}

    elif bear_accept:
        accept_ts = state_memory["bear_accept_time"]
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts] or candles_5m[-6:]
        
        retested = any(c['high'] >= bd * 0.999 for c in post_accept)
        
        if retested and price < bd:
            if not state_memory.get("bear_confirm_time"):
                state_memory["bear_confirm_time"] = candles_5m[-1]['time']
            return {"phase": "DIRECTIONAL_CONFIRMED_BEAR", "quality": "A", "msg": "Shelf Converted"}
            
        return {"phase": "DIRECTIONAL_CANDIDATE_BEAR", "quality": "", "msg": "Acceptance. Awaiting Pullback."}

    if val <= price <= vah: return {"phase": "BALANCE", "msg": "Rotational"}
    return {"phase": "TESTING_EDGE", "msg": "Testing Edge"}

def detect_regime(candles_15m, bias_score, levels):
    # (Legacy Support)
    return "ROTATIONAL" # Overridden by phase in UI

# --- MAIN RUNNER ---
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=576)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

async def fetch_1h_context(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '1h', limit=720)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

# --- SESSION CONFIG (ENERGY) ---
SESSION_CONFIGS = [
    {"name": "New York", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

def _get_dominant_session(now_utc: datetime) -> Dict[str, Any]:
    candidates = []
    for s in SESSION_CONFIGS:
        tz = pytz.timezone(s["tz"])
        now_local = now_utc.astimezone(tz)
        open_time = now_local.replace(hour=s["open_h"], minute=s["open_m"], second=0, microsecond=0)
        if now_local < open_time: open_time = open_time - timedelta(days=1)
        
        # Calculate Energy State
        elapsed_min = (now_local - open_time).total_seconds() / 60
        
        if elapsed_min < 30: energy = "CALIBRATING"
        elif elapsed_min < 240: energy = "PRIME" # First 4 hours
        elif elapsed_min < 420: energy = "LATE"  # 4h - 7h
        else: energy = "DEAD" # > 7h
        
        candidates.append({
            "name": s["name"], "tz": s["tz"],
            "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()),
            "anchor_fmt": open_time.strftime("%H:%M") + " " + s["name"].upper(),
            "date_key": open_time.strftime("%Y-%m-%d"),
            "diff": elapsed_min,
            "energy": energy
        })
    candidates.sort(key=lambda x: x['diff'])
    return candidates[0]

# --- LIVE PULSE ---
async def run_live_pulse(symbol: str, risk_mode: str = "fixed_margin", capital: float = 1000, leverage: float = 1) -> Dict[str, Any]:
    raw_5m = await fetch_5m_granular(symbol)
    raw_1h = await fetch_1h_context(symbol)
    if not raw_5m: return {"error": "No data"}
    
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    session_info = _get_dominant_session(now_utc)
    anchor_ts = session_info["anchor_time"]
    lock_end_ts = anchor_ts + 1800
    
    session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
    packet = {}; levels_source = "UNKNOWN"
    
    # CHECK LOCK STATUS
    if session_key in LOCKED_SESSIONS:
        packet = LOCKED_SESSIONS[session_key]
        levels_source = "STORED"
    elif now_ts < lock_end_ts:
        return {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "session_lock": session_info["anchor_fmt"],
            "status": "CALIBRATING", "levels_source": "CALIBRATING",
            "message": f"Levels lock in {int((lock_end_ts - now_ts)/60)} min",
            "price": raw_5m[-1]['close'], "regime": "PENDING", "phase": "CALIBRATING", "levels": {}, "strategies": []
        }
    else:
        # COMPUTE FRESH LOCK
        anchor_idx = -1
        for i, c in enumerate(raw_5m):
            if c['time'] >= anchor_ts: anchor_idx = i; break
        
        packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
        if "error" in packet: return packet
        
        packet["memory"] = {}
        LOCKED_SESSIONS[session_key] = packet
        levels_source = "COMPUTED (NEW LOCK)"
    
    levels = packet["levels"]
    state_memory = packet.get("memory", {})
    
    # LIVE ANALYSIS
    anchor_idx_live = -1
    for i, c in enumerate(raw_5m):
        if c['time'] >= anchor_ts: anchor_idx_live = i; break
    if anchor_idx_live == -1: anchor_idx_live = 0
    live_slice = raw_5m[anchor_idx_live:]
    candles_15m = _resample_15m(raw_5m)
    
    phase_data = detect_phase_advanced(candles_15m[-30:], live_slice, levels, state_memory)
    phase = phase_data["phase"]
    
    # STRATEGY GATING (Energy Aware)
    energy = session_info["energy"]
    risk_settings = { "mode": risk_mode, "value": float(capital), "leverage": float(leverage) }
    strategies = {
        "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic,
        "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic,
        "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic,
        "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic
    }
    
    results = []
    regime_legacy = "ROTATIONAL" if "BALANCE" in phase else "DIRECTIONAL"
    
    for code, func in strategies.items():
        try:
            res = func(levels, candles_15m[-30:], live_slice, risk_settings, regime_legacy)
            status = "STANDBY"; color = "#444"; msg = res['audit'].get('reason', 'Waiting...')
            
            # ENERGY OVERRIDES
            if energy == "DEAD":
                status = "OFF-HOURS"; color = "#444"; msg = "Session exhausted. Stand down."
            elif code == "S9" and res['status'] == "S9_ACTIVE":
                status = "CRITICAL ALERT"; color = "#ef4444"; msg = "MARKET HALTED"
            elif phase == "BALANCE":
                if code in ["S1", "S2", "S7"]: status = "BLOCKED"; color = "#222"; msg = "Balanced."
                elif code in ["S4", "S6"]:
                    if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                    else: status = "MONITORING"; color = "#ffcc00"
            elif "CANDIDATE" in phase:
                if code in ["S4", "S6"]: status = "BLOCKED"; color = "#222"; msg = "Breakout Attempt."
                elif code in ["S1", "S2"]: status = "ARMED"; color = "#ffcc00"; msg = "Acceptance. Wait."
            elif "CONFIRMED" in phase:
                if code in ["S4", "S6"]: status = "BLOCKED"; color = "#ef4444"; msg = "Trend Active."
                elif code in ["S7", "S1"]: 
                    if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                    else: status = "HUNTING"; color = "#00ff9d"; msg = "Trend Confirmed."
            elif "FAILED" in phase:
                if code == "S8": status = "ARMED"; color = "#00ff9d"; msg = "Trap Detected."
                else: status = "CAUTION"; color = "#ffcc00"; msg = "Trap Risk."
            
            results.append({
                "strategy": code, "status": status, "color": color, "message": msg,
                "levels": { "stop": res['audit'].get('stop_loss',0), "target": res['audit'].get('target',0) }
            })
        except Exception as e: results.append({"strategy": code, "status": "ERROR", "color": "red"})

    # --- BATTLEBOX PACKET (For GPT) ---
    battlebox = {
        "symbol": symbol,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session": {
            "active": session_info["name"],
            "anchor_time": session_info["anchor_fmt"],
            "energy_state": energy,
            "lock_window": "LOCKED"
        },
        "levels": levels,
        "phase_timeline": {
            "current_phase": phase,
            "milestones": {
                "acceptance_time": state_memory.get("bull_accept_time") or state_memory.get("bear_accept_time"),
                "confirmation_time": state_memory.get("bull_confirm_time") or state_memory.get("bear_confirm_time"),
                "failure_time": state_memory.get("failure_time")
            }
        },
        "strategies_summary": [
            {"code": r["strategy"], "status": r["status"], "msg": r["message"]} 
            for r in results if r["status"] in ["ACTIVE SIGNAL", "ARMED", "HUNTING"]
        ]
    }

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session_lock": session_info["anchor_fmt"],
        "energy": energy, # UI needs this
        "levels_source": levels_source,
        "price": raw_5m[-1]['close'], 
        "regime": regime_legacy, 
        "phase": phase + (" (" + phase_data.get("msg","") + ")"),
        "levels": levels, 
        "strategies": results,
        "battlebox": battlebox # New Payload
    }

# --- HISTORICAL ANALYSIS (SAME AS BEFORE) ---
async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000, strategy_mode: str = "S0", risk_mode: str = "fixed_margin") -> Dict[str, Any]:
    raw_15m = inputs.get("intraday_candles", [])
    symbol = inputs.get("symbol", "BTCUSDT")
    raw_5m = await fetch_5m_granular(symbol)
    raw_1h = await fetch_1h_context(symbol)
    risk_settings = { "mode": risk_mode, "value": float(capital), "leverage": float(leverage) }
    history = []
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
            anchor_ts = raw_15m[idx]['time']
            anchor_5m_idx = -1
            for i, c in enumerate(raw_5m):
                if c['time'] == anchor_ts: anchor_5m_idx = i; break
            if anchor_5m_idx == -1: continue
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_5m_idx)
            if "error" in packet: continue
            levels = packet["levels"]
            future_15m = raw_15m[idx : idx+64] 
            regime = detect_regime(future_15m[:16], packet["bias_score"], levels)
            buffer_time = anchor_ts - (300 * 50)
            future_5m = [c for c in raw_5m if c['time'] >= buffer_time]
            final_result = None
            try:
                if strategy_mode == "ALL":
                    candidates = []
                    strats = [strategy_auditor.run_s1_logic, strategy_auditor.run_s2_logic, strategy_auditor.run_s4_logic, strategy_auditor.run_s5_logic, strategy_auditor.run_s6_logic, strategy_auditor.run_s7_logic, strategy_auditor.run_s8_logic]
                    for f in strats:
                        try: candidates.append(f(levels, future_15m, future_5m, risk_settings, regime))
                        except: continue
                    best = None; best_pnl = -999.0
                    for c in candidates:
                        if c["audit"].get("valid", False) and c["pnl"] > best_pnl: best = c; best_pnl = c["pnl"]
                    final_result = best if best else strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
                else:
                    mapper = {"S0":strategy_auditor.run_s0_logic, "S1": strategy_auditor.run_s1_logic, "S2": strategy_auditor.run_s2_logic, "S3": strategy_auditor.run_s3_logic, "S4": strategy_auditor.run_s4_logic, "S5": strategy_auditor.run_s5_logic, "S6": strategy_auditor.run_s6_logic, "S7": strategy_auditor.run_s7_logic, "S8": strategy_auditor.run_s8_logic, "S9": strategy_auditor.run_s9_logic}
                    func = mapper.get(strategy_mode, strategy_auditor.run_s0_logic)
                    final_result = func(levels, future_15m, future_5m, risk_settings, regime)
            except: final_result = strategy_auditor.run_s0_logic(levels, future_15m, future_5m, risk_settings, regime)
            history.append({"session": s_key, "date": datetime.fromtimestamp(anchor_ts).strftime("%Y-%m-%d"), "regime": regime, "levels": levels, "strategy": final_result})
    history.sort(key=lambda x: x['date'], reverse=True)
    valid_wins = 0; valid_attempts = 0; valid_pnl = 0; exemplar = None; best_score = -999
    for h in history:
        res = h['strategy']
        if res['audit'].get('valid', False):
            valid_attempts += 1; valid_pnl += res['pnl']
            if res['pnl'] > 0: valid_wins += 1
            if res['pnl'] > best_score: best_score = res['pnl']; exemplar = h
    stats = {"win_rate": int(valid_wins/valid_attempts*100) if valid_attempts else 0, "total_pnl": valid_pnl, "valid_trades": valid_attempts, "total_sessions": len(history), "exemplar": exemplar}
    return {"history": history, "stats": stats}