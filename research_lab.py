# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v12.1 (CRASH FIX + HISTORY BOOST)
# ==============================================================================
# Updates:
# - FIXED: Restored 'detect_regime' to fix Historical Lab crash (500 Error).
# - UPGRADE: Increased 5m data fetch from 2 days -> 5 days (1500 candles).
# - FEATURE: Full Manual Session support for Live Tactical.
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

# --- GLOBAL PERSISTENCE ---
LOCKED_SESSIONS = {}

# --- CONSTANTS ---
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity", "name": "NY Equity", "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london", "name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo", "name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"id": "au_sydney", "name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

STRATEGY_NAMES = {
    "S1": "Breakout Continuation (Long)",
    "S2": "Breakdown Continuation (Short)",
    "S4": "Value Edge Rejection",
    "S5": "Range Extremes Fade",
    "S6": "Value Rotation",
    "S7": "Trend Pullback Continuation",
    "S8": "Failed Break / Trap",
    "S9": "Circuit Breaker"
}

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

# --- MISSING FUNCTION RESTORED (Fixes Historical Crash) ---
def detect_regime(candles_15m: List[Dict], bias_score: float, levels: Dict) -> str:
    """
    Legacy helper for Historical Lab to determine Rotational/Directional
    based on simple heuristics (Range % or Bias Score).
    """
    if not candles_15m: return "UNKNOWN"
    closes = [c['close'] for c in candles_15m]
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    price = closes[-1]
    
    # 1. Explicit Acceptance Override
    if price > bo * 1.001: return "DIRECTIONAL"
    if price < bd * 0.999: return "DIRECTIONAL"
    
    # 2. Standard Logic
    range_pct = ((bo - bd) / price) * 100 if price > 0 else 0
    if abs(bias_score) > 0.25: return "DIRECTIONAL"
    if range_pct > 0.5: return "ROTATIONAL"
    return "COMPRESSED"

# --- CORE LOGIC ---
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

def detect_phase_ledger(candles_15m: List[Dict], candles_5m: List[Dict], levels: Dict, ledger: Dict) -> Dict[str, Any]:
    if not candles_15m or not candles_5m: return {"phase": "UNKNOWN", "msg": "No Data"}
    
    price = candles_5m[-1]['close']
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    vah = levels.get("f24_vah", 0)
    val = levels.get("f24_val", 0)
    dr = levels.get("daily_resistance", 0)
    ds = levels.get("daily_support", 0)
    
    c1 = candles_15m[-1]['close']
    c2 = candles_15m[-2]['close'] if len(candles_15m) > 1 else c1
    
    if c1 > bo and c2 > bo:
        if not ledger.get("acceptance_time"):
            ledger["acceptance_time"] = candles_15m[-1]['time']
            ledger["acceptance_side"] = "BULL"
    elif c1 < bd and c2 < bd:
        if not ledger.get("acceptance_time"):
            ledger["acceptance_time"] = candles_15m[-1]['time']
            ledger["acceptance_side"] = "BEAR"
            
    if ledger.get("acceptance_side") == "BULL":
        if c1 < vah: 
            ledger["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BULL", "msg": "Trap Detected"}
    elif ledger.get("acceptance_side") == "BEAR":
        if c1 > val:
            ledger["failure_time"] = candles_15m[-1]['time']
            return {"phase": "FAILED_BREAK_BEAR", "msg": "Trap Detected"}

    if ledger.get("acceptance_side") == "BULL" and not ledger.get("confirmation_time"):
        accept_ts = ledger["acceptance_time"]
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts]
        shelves = [s for s in [bo, dr, vah] if s > 0 and s < price * 1.05]
        confirmed_shelf = None
        for shelf in shelves:
            if any(c['low'] <= shelf * 1.0015 for c in post_accept):
                confirmed_shelf = shelf
                break
        if confirmed_shelf and price > bo:
            ledger["confirmation_time"] = candles_5m[-1]['time']
            ledger["confirmation_shelf"] = confirmed_shelf
            
    elif ledger.get("acceptance_side") == "BEAR" and not ledger.get("confirmation_time"):
        accept_ts = ledger["acceptance_time"]
        post_accept = [c for c in candles_5m if c['time'] >= accept_ts]
        shelves = [s for s in [bd, ds, val] if s > 0 and s > price * 0.95]
        confirmed_shelf = None
        for shelf in shelves:
            if any(c['high'] >= shelf * 0.9985 for c in post_accept):
                confirmed_shelf = shelf
                break
        if confirmed_shelf and price < bd:
            ledger["confirmation_time"] = candles_5m[-1]['time']
            ledger["confirmation_shelf"] = confirmed_shelf

    side = ledger.get("acceptance_side", "")
    if ledger.get("failure_time"): return {"phase": f"FAILED_BREAK_{side}", "msg": "Trap Logic Active"}
    elif ledger.get("confirmation_time"): return {"phase": f"DIRECTIONAL_CONFIRMED_{side}", "msg": f"Shelf Retest Confirmed"}
    elif ledger.get("acceptance_time"): return {"phase": f"DIRECTIONAL_CANDIDATE_{side}", "msg": "Accepted. Awaiting Pullback."}
    
    if val <= price <= vah: return {"phase": "BALANCE", "msg": "Rotational"}
    return {"phase": "TESTING_EDGE", "msg": "Testing Edge"}

# --- MAIN RUNNER ---
exchange_kucoin = ccxt.kucoin({'enableRateLimit': True})

async def fetch_5m_granular(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        # INCREASED LIMIT TO 1500 (approx 5 days)
        candles = await exchange_kucoin.fetch_ohlcv(s, '5m', limit=1500)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

async def fetch_1h_context(symbol: str):
    s = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    try:
        candles = await exchange_kucoin.fetch_ohlcv(s, '1h', limit=720)
        return [{"time": int(c[0]/1000), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in candles]
    except: return []

def _get_active_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> Dict[str, Any]:
    # 1. MANUAL OVERRIDE
    if mode == "MANUAL" and manual_id:
        cfg = next((s for s in SESSION_CONFIGS if s["id"] == manual_id), None)
        if cfg:
            tz = pytz.timezone(cfg["tz"])
            now_local = now_utc.astimezone(tz)
            open_time = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
            if now_local < open_time: open_time = open_time - timedelta(days=1)
            
            elapsed_min = (now_local - open_time).total_seconds() / 60
            if elapsed_min < 30: energy = "CALIBRATING"
            elif elapsed_min < 240: energy = "PRIME"
            elif elapsed_min < 420: energy = "LATE"
            else: energy = "DEAD"
            
            return {
                "name": cfg["name"], "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()),
                "anchor_fmt": open_time.strftime("%H:%M") + " " + cfg["name"].upper(),
                "date_key": open_time.strftime("%Y-%m-%d"), "energy": energy, "manual": True
            }

    # 2. AUTO DETECT
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
            "name": s["name"], "anchor_time": int(open_time.astimezone(pytz.UTC).timestamp()),
            "anchor_fmt": open_time.strftime("%H:%M") + " " + s["name"].upper(),
            "date_key": open_time.strftime("%Y-%m-%d"), "energy": energy, "diff": elapsed_min, "manual": False
        })
    
    candidates.sort(key=lambda x: x['diff'])
    return candidates[0]

def _build_safe_response(status="OK", msg="", session_info={}, levels={}, strategies=[], phase="INIT", ledger={}):
    return {
        "status": status, "message": msg,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "session_lock": session_info.get("anchor_fmt", "--"),
        "energy": session_info.get("energy", "UNKNOWN"),
        "price": 0, "regime": "UNKNOWN", "phase": phase,
        "levels": levels, "strategies": strategies,
        "battlebox": {
            "session": {"active": session_info.get("name", "--"), "energy_state": session_info.get("energy", "--")},
            "phase_timeline": {
                "current_phase": phase,
                "milestones": {
                    "acceptance_time": ledger.get("acceptance_time"),
                    "confirmation_time": ledger.get("confirmation_time"),
                    "failure_time": ledger.get("failure_time")
                }
            },
            "strategies_summary": []
        }
    }

# --- LIVE PULSE ---
async def run_live_pulse(symbol: str, session_mode: str = "AUTO", manual_id: str = None, risk_mode="fixed_margin", capital=1000, leverage=1) -> Dict[str, Any]:
    try:
        raw_5m = await fetch_5m_granular(symbol)
        raw_1h = await fetch_1h_context(symbol)
        if not raw_5m: return _build_safe_response(status="ERROR", msg="No Data")
        
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        session_info = _get_active_session(now_utc, session_mode, manual_id)
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800
        session_key = f"{symbol}_{session_info['name']}_{session_info['date_key']}"
        
        packet = {}
        if session_key in LOCKED_SESSIONS:
            packet = LOCKED_SESSIONS[session_key]
        elif now_ts < lock_end_ts:
            resp = _build_safe_response(status="CALIBRATING", msg=f"Levels lock in {int((lock_end_ts - now_ts)/60)} min", session_info=session_info, phase="CALIBRATING")
            resp["price"] = raw_5m[-1]['close']
            return resp
        else:
            anchor_idx = -1
            for i, c in enumerate(raw_5m):
                if c['time'] >= anchor_ts: anchor_idx = i; break
            
            packet = _compute_session_packet(raw_5m, raw_1h, anchor_idx)
            if "error" in packet: return _build_safe_response(status="ERROR", msg=packet["error"], session_info=session_info)
            
            packet["ledger"] = {}
            LOCKED_SESSIONS[session_key] = packet
        
        levels = packet["levels"]
        ledger = packet.setdefault("ledger", {})
        
        anchor_idx_live = -1
        for i, c in enumerate(raw_5m):
            if c['time'] >= anchor_ts: anchor_idx_live = i; break
        if anchor_idx_live == -1: anchor_idx_live = 0
        
        live_slice = raw_5m[anchor_idx_live:]
        candles_15m = _resample_15m(raw_5m)
        phase_data = detect_phase_ledger(candles_15m[-30:], live_slice, levels, ledger)
        phase = phase_data["phase"]
        
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
                full_name = STRATEGY_NAMES.get(code, code)
                
                if energy == "DEAD":
                    status = "OFF-HOURS"; color = "#444"; msg = "Session exhausted."
                elif code == "S9" and res['status'] == "S9_ACTIVE":
                    status = "CRITICAL ALERT"; color = "#ef4444"; msg = "MARKET HALTED"
                elif phase == "BALANCE":
                    if code in ["S1", "S2", "S7"]: status = "BLOCKED"; color = "#222"; msg = "Balanced."
                    elif code in ["S4", "S6"]:
                        if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                        else: status = "MONITORING"; color = "#ffcc00"
                elif "CANDIDATE" in phase:
                    if "BULL" in phase:
                        if code == "S1": status = "ARMED"; color = "#ffcc00"; msg = "Accepted. Wait Pullback."
                        elif code == "S2": status = "BLOCKED"; color = "#222"; msg = "Bull Bias."
                    elif "BEAR" in phase:
                        if code == "S2": status = "ARMED"; color = "#ffcc00"; msg = "Accepted. Wait Pullback."
                        elif code == "S1": status = "BLOCKED"; color = "#222"; msg = "Bear Bias."
                    if code in ["S4", "S6"]: status = "BLOCKED"; color = "#222"
                elif "CONFIRMED" in phase:
                    if "BULL" in phase:
                        if code in ["S1", "S7"]: 
                            if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                            else: status = "HUNTING"; color = "#00ff9d"
                        elif code == "S2": status = "BLOCKED"; color = "#ef4444"
                    elif "BEAR" in phase:
                        if code in ["S2", "S7"]: 
                            if res['entry'] > 0: status = "ACTIVE SIGNAL"; color = "#00ff9d"
                            else: status = "HUNTING"; color = "#00ff9d"
                        elif code == "S1": status = "BLOCKED"; color = "#ef4444"
                elif "FAILED" in phase:
                    if code == "S8": status = "ARMED"; color = "#00ff9d"; msg = "Trap Detected."
                    else: status = "CAUTION"; color = "#ffcc00"
                
                results.append({
                    "strategy": code, "name": full_name, "status": status, "color": color, "message": msg,
                    "levels": { "stop": res['audit'].get('stop_loss',0), "target": res['audit'].get('target',0) }
                })
            except: results.append({"strategy": code, "name": code, "status": "ERROR", "color": "red"})

        resp = _build_safe_response(session_info=session_info, levels=levels, strategies=results, phase=phase, ledger=ledger)
        resp["price"] = raw_5m[-1]['close']
        resp["regime"] = regime_legacy
        resp["phase"] = phase + (" (" + phase_data.get("msg","") + ")")
        
        resp["battlebox"]["strategies_summary"] = [
            {"code": r["strategy"], "status": r["status"], "msg": r["message"]} 
            for r in results if r["status"] in ["ACTIVE SIGNAL", "ARMED", "HUNTING"]
        ]
        return resp

    except Exception as e:
        print(f"CRITICAL API ERROR: {e}")
        traceback.print_exc()
        return _build_safe_response(status="ERROR", msg=f"Internal Error: {str(e)}")

async def run_historical_analysis(inputs, session_keys, leverage, capital, strategy_mode, risk_mode):
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