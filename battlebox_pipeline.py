# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE (SINGLE SOURCE OF TRUTH)
# ==============================================================================
# 1) ONLY this module fetches market candles for Suite.
# 2) SSE computes levels (no permission).
# 3) Structure State Engine computes permission gates (Law Layer).
# ==============================================================================

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import traceback
import pytz
import ccxt.async_support as ccxt
import sse_engine
import structure_state_engine

# ----------------------------
# CONFIG
# ----------------------------
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity",  "name": "NY Equity",  "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london",     "name": "London",     "tz": "Europe/London",    "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo",    "name": "Tokyo",      "tz": "Asia/Tokyo",       "open_h": 9, "open_m": 0},
    {"id": "au_sydney",     "name": "Sydney",     "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

LOCKED_SESSIONS: Dict[str, Dict[str, Any]] = {}
_exchange_kucoin = ccxt.kucoin({"enableRateLimit": True})

# ----------------------------
# HELPERS
# ----------------------------
def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"): return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"): return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s: return s.replace("USDT", "/USDT")
    return s

def _safe_placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {
        "action": "HOLD FIRE",
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {"relative_to_triggers": "INSIDE_BAND"},
        "execution": {
            "pause_state": "NONE", "resumption_state": "NONE", "gates_mode": "PREVIEW",
            "locked_at": None, "levels": {"failure": 0.0, "continuation": 0.0},
        },
    }

def _infer_energy(elapsed_min: float) -> str:
    if elapsed_min < 30: return "CALIBRATING"
    if elapsed_min < 240: return "PRIME"
    if elapsed_min < 420: return "LATE"
    return "DEAD"

def anchor_ts_for_utc_date(cfg: Dict, utc_date: datetime) -> int:
    """
    STRICT ANCHORING: Prevents 'previous day' bug.
    Constructs the anchor based on the explicit Y-M-D of the requested date
    in the target timezone.
    """
    tz = pytz.timezone(cfg["tz"])
    y, m, d = utc_date.year, utc_date.month, utc_date.day
    # Create naive time at the correct hour/minute on that specific day
    local_open_naive = datetime(y, m, d, cfg["open_h"], cfg["open_m"], 0)
    # Localize it (handle DST correctly)
    local_open = tz.localize(local_open_naive)
    return int(local_open.astimezone(timezone.utc).timestamp())

def resolve_session(now_utc: datetime, mode: str = "AUTO", manual_id: Optional[str] = None) -> Dict[str, Any]:
    mode = (mode or "AUTO").upper()
    
    # MANUAL OVERRIDE
    if mode == "MANUAL" and manual_id:
        cfg = next((s for s in SESSION_CONFIGS if s["id"] == manual_id), SESSION_CONFIGS[0])
        tz = pytz.timezone(cfg["tz"])
        now_local = now_utc.astimezone(tz)
        open_time = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
        if now_local < open_time: open_time -= timedelta(days=1)
        elapsed = (now_local - open_time).total_seconds() / 60.0
        return {
            "id": cfg["id"], "name": cfg["name"], "tz": cfg["tz"],
            "anchor_time": int(open_time.astimezone(timezone.utc).timestamp()),
            "date_key": open_time.strftime("%Y-%m-%d"),
            "energy": _infer_energy(elapsed),
        }

    # AUTO SELECTION
    candidates = []
    for cfg in SESSION_CONFIGS:
        tz = pytz.timezone(cfg["tz"])
        now_local = now_utc.astimezone(tz)
        open_time = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
        if now_local < open_time: open_time -= timedelta(days=1)
        elapsed = (now_local - open_time).total_seconds() / 60.0
        candidates.append((elapsed, cfg, open_time))
    
    candidates.sort(key=lambda x: x[0])
    elapsed, cfg, open_time = candidates[0]
    
    return {
        "id": cfg["id"], "name": cfg["name"], "tz": cfg["tz"],
        "anchor_time": int(open_time.astimezone(timezone.utc).timestamp()),
        "date_key": open_time.strftime("%Y-%m-%d"),
        "energy": _infer_energy(elapsed),
    }

# ----------------------------
# DATA FETCHING
# ----------------------------
async def fetch_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_kucoin.fetch_ohlcv(s, "5m", limit=limit)
        return [{"time": int(r[0]/1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except:
        traceback.print_exc()
        return []

async def fetch_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_kucoin.fetch_ohlcv(s, "1h", limit=limit)
        return [{"time": int(r[0]/1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except: return []

def _war_map_from_1h(raw_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw_1h: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No 1h data."}
    closes = [c["close"] for c in raw_1h]
    if len(closes) < 22: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "note": "Insufficient history."}
    alpha = 2.0 / (21.0 + 1.0)
    ema = closes[0]
    for px in closes[1:]: ema = (px * alpha) + (ema * (1 - alpha))
    lean = "BULLISH" if closes[-1] > ema else "BEARISH"
    return {"status": "LIVE", "lean": lean, "phase": "TRANSITION", "note": f"Pressure is {lean}."}

# ----------------------------
# CORE COMPUTATION
# ----------------------------
def _compute_sse_packet(raw_5m: List[Dict], anchor_ts: int) -> Dict[str, Any]:
    lock_end_ts = anchor_ts + 1800
    calibration = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
    
    if len(calibration) < 6:
        return {"error": "Insufficient calibration data.", "lock_end_ts": lock_end_ts}

    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
    
    session_open = calibration[0]["open"]
    r30_high = max(c["high"] for c in calibration)
    r30_low = min(c["low"] for c in calibration)
    last_price = context_24h[-1]["close"] if context_24h else session_open

    sse_input = {
        "locked_history_5m": context_24h,
        "slice_24h_5m": context_24h,
        "slice_4h_5m": context_24h[-48:], 
        "session_open_price": session_open,
        "r30_high": r30_high, 
        "r30_low": r30_low,
        "last_price": last_price,
    }

    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed: return computed
    
    return {
        "levels": computed["levels"],
        "context": computed.get("context", {}),
        "bias_model": computed.get("bias_model", {}),
        "htf_shelves": computed.get("htf_shelves", {}),
        "lock_time": lock_end_ts
    }

# ----------------------------
# HISTORICAL / RESEARCH DELEGATE
# ----------------------------
def compute_session_from_candles(cfg: Dict, utc_date: datetime, raw_5m: List[Dict], exec_hours: int = 6) -> Dict[str, Any]:
    """
    THE TRUTH FUNCTION for Research Lab.
    Calculates exactly what happened in a historical session using Pipeline logic.
    """
    anchor_ts = anchor_ts_for_utc_date(cfg, utc_date)
    lock_end_ts = anchor_ts + 1800
    exec_end_ts = lock_end_ts + (exec_hours * 3600)
    
    # 1. Compute SSE (Levels)
    pkt = _compute_sse_packet(raw_5m, anchor_ts)
    
    if "error" in pkt:
        return {"ok": False, "error": pkt["error"], "final_state": "INVALID"}
    
    levels = pkt["levels"]
    
    # 2. Compute Law Layer (State)
    # We feed the execution window to see if acceptance/alignment happened
    post_lock_candles = [c for c in raw_5m if lock_end_ts <= c["time"] < exec_end_ts]
    
    state = structure_state_engine.compute_structure_state(levels, post_lock_candles)
    
    had_acceptance = (state["permission"]["status"] == "EARNED")
    had_alignment = (state["execution"]["gates_mode"] == "LOCKED")
    side = state["permission"]["side"]
    
    return {
        "ok": True,
        "counts": {
            "had_acceptance": had_acceptance,
            "had_alignment": had_alignment
        },
        "events": {
            "acceptance_side": side,
            "final_state": state["action"]
        }
    }

# ----------------------------
# PUBLIC API
# ----------------------------
async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: str = None, operator_flex: bool = False) -> Dict[str, Any]:
    raw_5m = await fetch_5m(symbol)
    raw_1h = await fetch_1h(symbol)
    
    if not raw_5m: return {"status": "ERROR", "message": "No Data"}
    
    now_utc = datetime.now(timezone.utc)
    session = resolve_session(now_utc, session_mode, manual_id)
    anchor_ts = session["anchor_time"]
    lock_end_ts = anchor_ts + 1800

    # 1. CALIBRATION PHASE
    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {
            "status": "CALIBRATING",
            "timestamp": now_utc.strftime("%H:%M UTC"),
            "price": raw_5m[-1]["close"],
            "energy": "CALIBRATING",
            "battlebox": {
                "war_map_context": wm,
                "session_battle": _safe_placeholder_state("Calibrating..."),
                "session": session,
                "levels": {}
            }
        }

    # 2. LOCKED SESSION CACHE
    session_key = f"{symbol}_{session['id']}_{session['date_key']}"
    if session_key not in LOCKED_SESSIONS:
        pkt = _compute_sse_packet(raw_5m, anchor_ts)
        if "error" in pkt:
             wm = _war_map_from_1h(raw_1h)
             return {"status": "ERROR", "message": pkt["error"], "battlebox": {"war_map_context": wm, "session_battle": _safe_placeholder_state(pkt["error"]), "levels": {}}}
        LOCKED_SESSIONS[session_key] = pkt

    pkt = LOCKED_SESSIONS[session_key]
    levels = pkt["levels"]
    lock_time = pkt["lock_time"]

    # 3. LAW LAYER (State Engine)
    post_lock = [c for c in raw_5m if c["time"] >= lock_time]
    state = structure_state_engine.compute_structure_state(levels, post_lock)
    wm = _war_map_from_1h(raw_1h)

    return {
        "status": "OK",
        "timestamp": now_utc.strftime("%H:%M UTC"),
        "price": raw_5m[-1]["close"],
        "energy": session["energy"],
        "battlebox": {
            "war_map_context": wm,
            "session_battle": state,
            "levels": levels,
            "session": session,
            "bias_model": pkt.get("bias_model", {}),
            "context": pkt.get("context", {}),
        }
    }

async def get_session_review(symbol: str, session_tz: str) -> Dict[str, Any]:
    raw_5m = await fetch_5m(symbol)
    if not raw_5m: return {"ok": False, "error": "No Data"}

    # Resolve Session Config from TZ
    cfg = next((s for s in SESSION_CONFIGS if s["tz"] == session_tz), SESSION_CONFIGS[0])
    # USE SMART ANCHORING
    anchor_ts = anchor_ts_for_utc_date(cfg, datetime.now(timezone.utc))
    lock_end_ts = anchor_ts + 1800
    
    # If the computed anchor is in the future, we probably wanted yesterday
    # (Though typically review runs after close, so current day is correct)
    if anchor_ts > datetime.now(timezone.utc).timestamp():
         anchor_ts -= 86400
         lock_end_ts -= 86400
         
    # Build packet
    pkt = _compute_sse_packet(raw_5m, anchor_ts)
    
    if "error" in pkt:
        # Check if it's because we are currently calibrating (session just started)
        if int(datetime.now(timezone.utc).timestamp()) < lock_end_ts:
             return {"ok": True, "mode": "CALIBRATING", "symbol": symbol, "session": {"name": cfg["name"]}, "message": "Calibrating..."}
        return {"ok": False, "error": pkt["error"]}

    open_time = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()

    return {
        "ok": True,
        "mode": "LOCKED",
        "symbol": symbol,
        "price": raw_5m[-1]["close"],
        "session": {"name": cfg["name"], "anchor_time": open_time},
        "levels": pkt["levels"],
        "bias_model": pkt.get("bias_model", {}),
        "context": pkt.get("context", {}),
        "htf_shelves": pkt.get("htf_shelves", {}),
        "range_30m": {
            "high": pkt["levels"].get("range30m_high", 0),
            "low": pkt["levels"].get("range30m_low", 0)
        }
    }