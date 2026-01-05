# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v11.0 (PHASE LEDGER & SMART PULLBACKS)
# ==============================================================================
# Updates:
# - PERSISTENCE: 'phase_timeline' is now stored in LOCKED_SESSIONS memory.
# - LOGIC: Pullbacks now check MULTIPLE shelves (Trigger, DR, VAH).
# - GATING: 'DEAD' energy disables active hunting.
# - UI: Always returns valid timeline structure to prevent frontend crash.
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

# --- ADVANCED PHASE LOGIC WITH LEDGER ---
def detect_phase_ledger(candles_15m: List[Dict], candles_5m: List[Dict], levels: Dict, ledger: Dict) -> Dict[str, Any]:
    """
    State Machine that Writes to Ledger (Persistent Memory).
    """
    if not candles_15m or not candles_5m: return {"phase": "UNKNOWN", "msg": "No Data"}
    
    price = candles_5m[-1]['close']
    # Levels
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    dr = levels.get("daily_resistance", 0)
    ds = levels.get("daily_support", 0)
    
    c1 = candles_15m[-1]['close']
    c2 = candles_15m[-2]['close'] if len(candles_15m) > 1 else c1
    
    # 1. ACCEPTANCE CHECK
    if c1 > bo and c2 > bo:
        if not ledger.get("acceptance_time"):
            ledger["acceptance_time"] = candles_15m[-1]['time']
            ledger["acceptance_side"] = "BULL"
    elif c1 < bd and c2 < bd:
        if not ledger.get("acceptance_time"):
            ledger["acceptance_time"] = candles_15m[-1]['time']
            ledger["acceptance_side"] = "BEAR"
            
    # 2. FAILURE CHECK
    if ledger.get("acceptance_side") == "BULL":
        if c1 < vah: 
            ledger["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BULL", "msg": "Trap Detected"}
    elif ledger.get("acceptance_side") == "BEAR":
        if c1 > val:
            ledger["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BEAR", "msg": "Trap Detected"}

    # 3. CONFIRMATION CHECK (Smart Pullback)
    if ledger.get("acceptance_side") == "BULL" and not ledger.get("confirmation_time"):
        accept_ts = ledger["acceptance_time"]
        # Look at 5m candles SINCE acceptance
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts]
        
        # Define Shelves to Test
        shelves = [bo, dr, vah]
        # Filter out 0 or irrelevant shelves
        valid_shelves = [s for s in shelves if s > 0 and s < price * 1.05]
        
        # Did we dip into any shelf zone? (Within 0.15%)
        confirmed_shelf = None
        for shelf in valid_shelves:
            if any(c['low'] <= shelf * 1.0015 for c in post_accept):
                confirmed_shelf = shelf
                break
        
        # If dipped AND held above BO
        if confirmed_shelf and price > bo:
            ledger["confirmation_time"] = candles_5m[-1]['time']
            ledger["confirmation_shelf"] = confirmed_shelf
            
    elif ledger.get("acceptance_side") == "BEAR" and not ledger.get("confirmation_time"):
        accept_ts = ledger["acceptance_time"]
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts]
        
        shelves = [bd, ds, val]
        valid_shelves = [s for s in shelves if s > 0 and s > price * 0.95]
        
        confirmed_shelf = None
        for shelf in valid_shelves:
            if any(c['high'] >= shelf * 0.9985 for c in post_accept):
                confirmed_shelf = shelf
                break
                
        if confirmed_shelf and price < bd:
            ledger["confirmation_time"] = candles_5m[-1]['time']
            ledger["confirmation_shelf"] = confirmed_shelf

    # 4. DETERMINE PHASE FROM LEDGER
    side = ledger.get("acceptance_side", "")
    if ledger.get("failure_time"):
        return {"phase": f"FAILED_BREAK_{side}", "msg": "Trap Logic Active"}
    elif ledger.get("confirmation_time"):
        return {"phase": f"DIRECTIONAL_CONFIRMED_{side}", "msg": f"Shelf Retest Confirmed"}
    elif ledger.get("acceptance_time"):
        return {"phase": f"DIRECTIONAL_CANDIDATE_{side}", "msg": "Accepted. Awaiting Pullback."}
    
    # 5. DEFAULT BALANCE
    if val <= price <= vah: return {"phase": "BALANCE", "msg": "Rotational"}
    return {"phase": "TESTING_EDGE", "msg": "Testing Edge"}

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
        
        elapsed_min = (now_local - open_time).total_seconds() / 60
        
        if elapsed_min < 30: energy = "CALIBRATING"
        elif elapsed_min < 240: energy = "PRIME"
        elif elapsed_min < 420: energy = "LATE"
        else: energy = "DEAD"
        
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
    
    # --- PHASE LEDGER INITIALIZATION ---
    # Ensure a ledger exists for this session key
    # Structure: { acceptance_time: int, acceptance_side: str, confirmation_time: int, failure_time: int, ... }
    
    # 1. CHECK LEVEL LOCK
    if session_key in LOCKED_SESSIONS:
        packet = LOCKED_SESSIONS[session_key]
        levels_source = "STORED"
    elif now_ts < lock_end_ts:
        return {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "session_lock": session_info["anchor_fmt"],
            "status": "CALIBRATING", "levels_source": "CALIBRATING",
            "message": f"Levels lock in {int((lock_end_ts - now_ts)/60)} min",
            "price": raw_5m[-1]['close'], "regime": "PENDING", "phase": "CALIBRATING", "levels": {}, "strategies": [],
            "battlebox": {} # Safety
        }
    else:
        # COMPUTE FRESH LOCK
        anchor_idx = -1
        for i, c in enumerate(raw_5m):
            if c['time'] >= anchor_ts: anchor_idx = i; break
        
        packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
        if "error" in packet: return packet
        
        packet["ledger"] = {} # Initialize clean ledger
        LOCKED_SESSIONS[session_key] = packet
        levels_source = "COMPUTED (NEW LOCK)"
    
    levels = packet["levels"]
    ledger = packet.setdefault("ledger", {}) # Retrieve persistent ledger
    
    # 2. LIVE ANALYSIS
    anchor_idx_live = -1
    for i, c in enumerate(raw_5m):
        if c['time'] >= anchor_ts: anchor_idx_live = i; break
    if anchor_idx_live == -1: anchor_idx_live = 0
    live_slice = raw_5m[anchor_idx_live:]
    candles_15m = _resample_15m(raw_5m)
    
    # RUN DETECT PHASE (Updates Ledger In-Place)
    phase_data = detect_phase_ledger(candles_15m[-30:], live_slice, levels, ledger)
    phase = phase_data["phase"]
    
    # 3. STRATEGY GATING
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
            
            if energy == "DEAD":
                status = "OFF-HOURS"; color = "#444"; msg = "Session exhausted."
            elif code == "S9" and res['status'] == "S9_ACTIVE":
                status = "CRITICAL ALERT"; color = "#ef4444"; msg = "MARKET HALTED"
            elif phase == "BALANCE":
                if code in ["S1", "S2", "S7"]: status = "BLOCKED"; color = "#222"; msg = "Balanced."
                elif code in ["S4", "S6"]:
                    if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                    else: status = "MONITORING"; color = "#ffcc00"
            
            # DIRECTIONAL GATING (Direction Aware)
            elif "CANDIDATE" in phase:
                if "BULL" in phase:
                    if code == "S1": status = "ARMED"; color = "#ffcc00"; msg = "Accepted. Awaiting Pullback."
                    elif code == "S2": status = "BLOCKED"; color = "#222"; msg = "Bull Posture."
                elif "BEAR" in phase:
                    if code == "S2": status = "ARMED"; color = "#ffcc00"; msg = "Accepted. Awaiting Pullback."
                    elif code == "S1": status = "BLOCKED"; color = "#222"; msg = "Bear Posture."
                if code in ["S4", "S6"]: status = "BLOCKED"; color = "#222"

            elif "CONFIRMED" in phase:
                if "BULL" in phase:
                    if code in ["S1", "S7"]: 
                        if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                        else: status = "HUNTING"; color = "#00ff9d"; msg = "Trend Confirmed."
                    elif code == "S2": status = "BLOCKED"; color = "#ef4444"
                elif "BEAR" in phase:
                    if code in ["S2", "S7"]: 
                        if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                        else: status = "HUNTING"; color = "#00ff9d"; msg = "Trend Confirmed."
                    elif code == "S1": status = "BLOCKED"; color = "#ef4444"
            
            elif "FAILED" in phase:
                if code == "S8": status = "ARMED"; color = "#00ff9d"; msg = "Trap Detected."
                else: status = "CAUTION"; color = "#ffcc00"; msg = "Trap Risk."
            
            results.append({
                "strategy": code, "status": status, "color": color, "message": msg,
                "levels": { "stop": res['audit'].get('stop_loss',0), "target": res['audit'].get('target',0) }
            })
        except Exception as e: results.append({"strategy": code, "status": "ERROR", "color": "red"})

    # --- BATTLEBOX PACKET ---
    battlebox = {
        "symbol": symbol,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session": {
            "active": session_info["name"], "anchor_time": session_info["anchor_fmt"], "energy_state": energy
        },
        "levels": levels,
        "phase_timeline": {
            "current_phase": phase,
            "milestones": {
                "acceptance_time": ledger.get("acceptance_time"),
                "confirmation_time": ledger.get("confirmation_time"),
                "failure_time": ledger.get("failure_time"),
                "confirmed_shelf": ledger.get("confirmation_shelf")
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
        "energy": energy,
        "levels_source": levels_source,
        "price": raw_5m[-1]['close'], 
        "regime": regime_legacy, 
        "phase": phase + (" (" + phase_data.get("msg","") + ")"),
        "levels": levels, 
        "strategies": results,
        "battlebox": battlebox
    }

# --- HISTORICAL ANALYSIS (PRESERVED) ---
async def run_historical_analysis(inputs: Dict[str, Any], session_keys: List[str], leverage: float = 1, capital: float = 1000, strategy_mode: str = "S0", risk_mode: str = "fixed_margin") -> Dict[str, Any]:
    # (Same as v9.1 - omitted for brevity but preserved in full overwrite)
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