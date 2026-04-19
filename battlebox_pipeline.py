# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE — v9.0 (THE MIDDLE BRAIN)
# ==============================================================================
# Purpose:
# - The single "Moment of Truth" for each session/day
# - Locks session open + 30m anchor range (calibration)
# - Computes levels via sse_engine.compute_sse_levels
# - Synthesizes Monday-Sunday Macro Bias from 1D candles.
# - INJECTS Postgres Memory (Campaign State) and CoinGlass Oracle Math.
# ==============================================================================

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import traceback
import asyncio

import ccxt.async_support as ccxt

import session_manager
import sse_engine
import structure_state_engine
import liquidity_oracle 
import database_manager # <--- INJECTING POSTGRES MEMORY

# Public re-export for compatibility
SESSION_CONFIGS = session_manager.SESSION_CONFIGS

# ----------------------------------------------------------------------
# Cache / Locks
# ----------------------------------------------------------------------
_LOCKED_PACKETS: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

# ----------------------------------------------------------------------
# Exchange (KuCoin)
# ----------------------------------------------------------------------
_exchange_live = ccxt.kucoin({"enableRateLimit": True})

def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in ("BTC", "BTCUSDT"):
        return "BTC/USDT"
    if s in ("ETH", "ETHUSDT"):
        return "ETH/USDT"
    if s.endswith("USDT") and "/" not in s:
        return s.replace("USDT", "/USDT")
    return s

async def close_exchange() -> None:
    """Call this on app shutdown to avoid Render/network weirdness."""
    global _exchange_live
    try:
        if _exchange_live is not None:
            await _exchange_live.close()
    except Exception:
        pass

# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------
async def fetch_live_5m(symbol: str, limit: int = 1500) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "5m", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception:
        traceback.print_exc()
        return []

async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception:
        return []

async def fetch_live_daily(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1d", limit=limit)
        return [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]
    except Exception:
        return []

async def fetch_historical_pagination(symbol: str, start_ts: int, end_ts: int, limit: int = 1500) -> List[Dict[str, Any]]:
    """
    Historical 5m candles between [start_ts, end_ts).
    Uses ccxt pagination via 'since' in milliseconds.
    """
    s = _normalize_symbol(symbol)
    since_ms = int(start_ts) * 1000
    end_ms = int(end_ts) * 1000
    out: List[Dict[str, Any]] = []
    last_first_ts: Optional[int] = None
    safety_iters = 0

    while since_ms < end_ms:
        safety_iters += 1
        if safety_iters > 2000: break

        rows = await _exchange_live.fetch_ohlcv(s, "5m", since=since_ms, limit=limit)
        if not rows: break

        first_ts = int(rows[0][0])
        if last_first_ts is not None and first_ts == last_first_ts: break
        last_first_ts = first_ts

        for r in rows:
            t_ms = int(r[0])
            if t_ms >= end_ms: break
            out.append({"time": int(t_ms / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])})

        last_ts = int(rows[-1][0])
        if last_ts <= since_ms: break
        since_ms = last_ts + (5 * 60 * 1000)
        await asyncio.sleep(0)

    out.sort(key=lambda x: int(x["time"]))
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for c in out:
        t = int(c["time"])
        if t in seen: continue
        seen.add(t)
        dedup.append(c)

    return dedup

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _calculate_weekly_force(raw_daily: List[Dict[str, Any]], anchor_ts: int) -> str:
    if not raw_daily: return "NEUTRAL"
    anchor_dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc)
    days_since_sunday = (anchor_dt.weekday() + 1) % 7
    if days_since_sunday == 0: days_since_sunday = 7
    last_sunday_dt = anchor_dt - timedelta(days=days_since_sunday)
    last_sunday_dt = last_sunday_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    last_monday_dt = last_sunday_dt - timedelta(days=6)
    last_monday_dt = last_monday_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = int(last_monday_dt.timestamp())
    end_ts = int(last_sunday_dt.timestamp())
    week_candles = [c for c in raw_daily if start_ts <= int(c["time"]) <= end_ts]
    
    if not week_candles: return "NEUTRAL"
    op = float(week_candles[0]["open"])
    cl = float(week_candles[-1]["close"])
    if cl > op: return "BULLISH"
    if cl < op: return "BEARISH"
    return "NEUTRAL"

def _calculate_168h_micro_bias(raw_1h: List[Dict[str, Any]]) -> str:
    if not raw_1h or len(raw_1h) < 168: return "NEUTRAL"
    current_price = float(raw_1h[-1]["close"])
    past_price = float(raw_1h[-168]["close"])
    pct_change = ((current_price - past_price) / past_price) * 100.0
    if pct_change > 1.00: return "BULLISH"
    elif pct_change < -1.00: return "BEARISH"
    return "NEUTRAL"

def _safe_placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {"action": "HOLD FIRE", "reason": reason, "permission": {"status": "NOT_EARNED", "side": "NONE"}, "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"}, "location": {"relative_to_triggers": "INSIDE"}, "execution": {"pause_state": "NONE", "resumption_state": "NONE", "gates_mode": "PREVIEW", "locked_at": None, "levels": {"failure": 0.0, "continuation": 0.0}}, "diagnostics": {"fail_reason": "WAITING"}}

def _war_map_from_1h(raw_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw_1h: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No 1h data."}
    closes = [float(c["close"]) for c in raw_1h]
    if len(closes) < 22: return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "note": "Insufficient history."}
    alpha = 2.0 / (21.0 + 1.0)
    ema = closes[0]
    for px in closes[1:]: ema = (px * alpha) + (ema * (1 - alpha))
    lean = "BULLISH" if closes[-1] > ema else "BEARISH"
    return {"status": "LIVE", "lean": lean, "phase": "TRANSITION", "note": f"Pressure is {lean}."}

def _compute_sse_packet(raw_5m: List[Dict[str, Any]], anchor_ts: int, macro_bias: str, micro_bias: str, tuning: Optional[Dict[str, Any]] = None, raw_daily: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    lock_end_ts = int(anchor_ts) + 1800
    calibration = [c for c in raw_5m if anchor_ts <= int(c["time"]) < lock_end_ts]
    if len(calibration) < 6: return {"error": "Insufficient calibration data.", "lock_end_ts": lock_end_ts}

    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= int(c["time"]) < lock_end_ts]
    session_open = float(calibration[0]["open"])
    r30_high = max(float(c["high"]) for c in calibration)
    r30_low = min(float(c["low"]) for c in calibration)
    last_price = float(context_24h[-1]["close"]) if context_24h else session_open

    d_ema20, d_ema30, d_ema50 = 0.0, 0.0, 0.0
    if raw_daily and len(raw_daily) > 50:
        closes = [float(c["close"]) for c in raw_daily]
        def calc_ema(period, values):
            k = 2 / (period + 1)
            ema = values[0]
            for v in values[1:]: ema = (v * k) + (ema * (1 - k))
            return ema
        d_ema20 = calc_ema(20, closes)
        d_ema30 = calc_ema(30, closes)
        d_ema50 = calc_ema(50, closes)

    sse_input = {
        "locked_history_5m": context_24h,
        "slice_24h_5m": context_24h,
        "slice_4h_5m": context_24h[-48:],
        "raw_daily_candles": raw_daily or [], 
        "session_open_price": session_open,
        "r30_high": r30_high,
        "r30_low": r30_low,
        "last_price": last_price,
        "tuning": tuning or {},
    }

    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed: return computed

    if "levels" in computed:
        computed["levels"]["daily_ema20"] = d_ema20 
        computed["levels"]["daily_ema30"] = d_ema30
        computed["levels"]["daily_ema50"] = d_ema50

    if "context" not in computed: computed["context"] = {}
    computed["context"]["macro_bias"] = macro_bias
    computed["context"]["micro_bias"] = micro_bias

    return {
        "levels": computed["levels"],
        "context": computed.get("context", {}),
        "bias_model": computed.get("bias_model", {}),
        "htf_shelves": computed.get("htf_shelves", {}),
        "lock_time": lock_end_ts,
        "meta": computed.get("meta", {}),
    }

# ----------------------------------------------------------------------
# THE MIDDLE BRAIN MATH (Crater vs Speedbump)
# ----------------------------------------------------------------------
def _analyze_true_gap(symbol, trigger, static_level, order_book, direction):
    min_gap, primal_max, exhaust_max = 0.50, 1.50, 2.25
    if "ETH" in symbol: min_gap, primal_max, exhaust_max = 0.80, 2.50, 3.50
    if "SOL" in symbol: min_gap, primal_max, exhaust_max = 1.50, 4.00, 6.00
    
    if trigger == 0: return 0.0, "WAITING", 0.0

    if not order_book:
        fallback_gap = (abs(static_level - trigger) / trigger) * 100 if static_level > 0 else 0
        if fallback_gap < min_gap: return fallback_gap, "DEATH ZONE (STATIC)", static_level
        return fallback_gap, "MAGNET (STATIC)", static_level

    if direction == "LONG":
        valid_walls = [x for x in order_book if x[0] > trigger]
        valid_walls.sort(key=lambda x: x[0]) 
    else:
        valid_walls = [x for x in order_book if x[0] < trigger]
        valid_walls.sort(key=lambda x: x[0], reverse=True) 
        
    if not valid_walls:
        fallback_gap = (abs(static_level - trigger) / trigger) * 100
        return fallback_gap, "JAILBREAK (NO WALLS)", static_level

    search_range = trigger * 1.01 if direction == "LONG" else trigger * 0.99
    immediate_zone = [x for x in valid_walls if (x[0] <= search_range if direction == "LONG" else x[0] >= search_range)]
    immediate_wall = max(immediate_zone, key=lambda x: x[1]) if immediate_zone else valid_walls[0]

    macro_wall = max(valid_walls, key=lambda x: x[1])

    current_day = datetime.now(timezone.utc).weekday()
    vol_multiplier = 2.0 if current_day in [0, 4, 5, 6] else 1.5

    is_speedbump = False
    if macro_wall[0] != immediate_wall[0]: 
        if macro_wall[1] >= (immediate_wall[1] * vol_multiplier):
            is_speedbump = True

    true_target = macro_wall[0] if is_speedbump else immediate_wall[0]
    true_gap = (abs(true_target - trigger) / trigger) * 100

    if true_gap < min_gap: return true_gap, "DEATH ZONE (CRATER)", true_target
    elif true_gap > exhaust_max: return true_gap, "DEATH ZONE (EXHAUSTION)", true_target
    elif true_gap > primal_max: return true_gap, "EXTENDED MAGNET", true_target
    else:
        if is_speedbump: return true_gap, "PRIMAL ZONE (SPEEDBUMP CLEARED)", true_target
        return true_gap, "PRIMAL ZONE (DIRECT MAGNET)", true_target

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: Optional[str] = None, operator_flex: bool = False, tuning: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    raw_1h = await fetch_live_1h(symbol)
    raw_daily = await fetch_live_daily(symbol)

    if not raw_5m: return {"status": "ERROR", "message": "No Data"}

    now_utc = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now_utc, session_mode, manual_id)
    anchor_ts = int(session["anchor_time"])
    lock_end_ts = anchor_ts + 1800

    macro_bias = _calculate_weekly_force(raw_daily, anchor_ts)
    micro_bias = _calculate_168h_micro_bias(raw_1h)

    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {"status": "CALIBRATING", "timestamp": now_utc.strftime("%H:%M UTC"), "price": float(raw_5m[-1]["close"]), "energy": "CALIBRATING", "battlebox": {"war_map_context": wm, "session_battle": _safe_placeholder_state("Calibrating..."), "session": session, "levels": {}, "bias_model": {}, "context": {"macro_bias": macro_bias, "micro_bias": micro_bias}}}

    date_key = session["date_key"]
    session_key = f"{_normalize_symbol(symbol)}::{session['id']}::{date_key}"

    async with _CACHE_LOCK:
        if session_key not in _LOCKED_PACKETS:
            pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, tuning=tuning, raw_daily=raw_daily)
            if "error" in pkt:
                wm = _war_map_from_1h(raw_1h)
                return {"status": "ERROR", "message": pkt["error"], "battlebox": {"war_map_context": wm, "session_battle": _safe_placeholder_state(pkt["error"]), "session": session, "levels": {}, "bias_model": {}, "context": {}}}
            
            # --- PHASE 1/2 INJECTION: ORACLE & MEMORY ---
            liquidity_data = await liquidity_oracle.fetch_liquidation_magnets(symbol)
            db_state = await database_manager.get_campaign_state(symbol)
            
            pkt["liquidity_walls"] = liquidity_data
            pkt["campaign_state"] = db_state
            
            # --- MIDDLE BRAIN MATH ---
            bo = float(pkt["levels"].get("breakout_trigger", 0))
            bd = float(pkt["levels"].get("breakdown_trigger", 0))
            dr = float(pkt["levels"].get("daily_resistance", 0))
            ds = float(pkt["levels"].get("daily_support", 0))

            raw_walls = liquidity_data.get("raw_data", {})
            asks = raw_walls.get("asks", [])
            bids = raw_walls.get("bids", [])

            l_gap, l_tier, l_target = _analyze_true_gap(symbol, bo, dr, asks, "LONG")
            s_gap, s_tier, s_target = _analyze_true_gap(symbol, bd, ds, bids, "SHORT")
            
            pkt["middle_brain"] = {
                "long_tier": l_tier, "long_target": l_target, "long_gap": l_gap,
                "short_tier": s_tier, "short_target": s_target, "short_gap": s_gap
            }
            
            _LOCKED_PACKETS[session_key] = pkt

        pkt = _LOCKED_PACKETS[session_key]

    levels = pkt["levels"]
    lock_time = int(pkt["lock_time"])
    post_lock = [c for c in raw_5m if int(c["time"]) >= lock_time]

    state = structure_state_engine.compute_structure_state(levels=levels, candles_5m_post_lock=post_lock, tuning=tuning or {})
    wm = _war_map_from_1h(raw_1h)

    return {
        "status": "OK",
        "timestamp": now_utc.strftime("%H:%M UTC"),
        "price": float(raw_5m[-1]["close"]),
        "energy": session.get("energy", "ACTIVE"),
        "battlebox": {
            "war_map_context": wm,
            "session_battle": state,
            "levels": levels,
            "session": session,
            "bias_model": pkt.get("bias_model", {}),
            "context": pkt.get("context", {}),
            "htf_shelves": pkt.get("htf_shelves", {}),
            "meta": pkt.get("meta", {}),
            "liquidity_walls": pkt.get("liquidity_walls", {}),
            "campaign_state": pkt.get("campaign_state", {}),
            "middle_brain": pkt.get("middle_brain", {})
        },
        "candles": post_lock
    }

async def get_session_review(symbol: str, session_id: str = "us_ny_futures", tuning: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_5m = await fetch_live_5m(symbol)
    if not raw_5m: return {"ok": False, "error": "No Data"}
    raw_1h = await fetch_live_1h(symbol) 
    raw_daily = await fetch_live_daily(symbol)
    cfg = session_manager.get_session_config(session_id)
    now_utc = datetime.now(timezone.utc)
    anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, now_utc)
    lock_end_ts = anchor_ts + 1800

    if anchor_ts > int(now_utc.timestamp()):
        anchor_ts -= 86400
        lock_end_ts -= 86400

    macro_bias = _calculate_weekly_force(raw_daily, anchor_ts)
    micro_bias = _calculate_168h_micro_bias(raw_1h)

    if int(now_utc.timestamp()) < lock_end_ts:
        return {"ok": True, "mode": "CALIBRATING", "symbol": symbol, "session": {"id": cfg["id"], "name": cfg["name"], "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()}, "message": "Calibrating... (first 30 minutes after open)"}

    pkt = _compute_sse_packet(raw_5m, anchor_ts, macro_bias, micro_bias, tuning=tuning, raw_daily=raw_daily)
    if "error" in pkt: return {"ok": False, "error": pkt["error"]}

    liquidity_data = await liquidity_oracle.fetch_liquidation_magnets(symbol)
    db_state = await database_manager.get_campaign_state(symbol)

    return {
        "ok": True, "mode": "LOCKED", "symbol": symbol, "price": float(raw_5m[-1]["close"]),
        "session": {"id": cfg["id"], "name": cfg["name"], "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()},
        "levels": pkt["levels"], "bias_model": pkt.get("bias_model", {}), "context": pkt.get("context", {}),
        "htf_shelves": pkt.get("htf_shelves", {}), "meta": pkt.get("meta", {}),
        "liquidity_walls": liquidity_data, "campaign_state": db_state,
        "range_30m": {"high": float(pkt["levels"].get("range30m_high", 0.0)), "low": float(pkt["levels"].get("range30m_low", 0.0))}
    }