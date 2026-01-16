# battlebox_pipeline.py
# ==============================================================================
# KABRODA BATTLEBOX PIPELINE — v7.0 (Full Rewrite)
# ==============================================================================
# Purpose:
# - The single "Moment of Truth" for each session/day
# - Locks session open + 30m anchor range (calibration)
# - Computes levels via sse_engine.compute_sse_levels
# - Feeds post-lock candles into structure_state_engine (law layer)
#
# Session Authority:
# - session_manager.py is the ONLY time anchor source
#
# Notes:
# - This module should be consumed by:
#   - Session Control page (review packet)
#   - Research Lab (batch/backtest)
#   - Battle Control / Omega (live packet + structure state)
# ==============================================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import traceback
import asyncio

import ccxt.async_support as ccxt

import session_manager
import sse_engine
import structure_state_engine

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
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        traceback.print_exc()
        return []


async def fetch_live_1h(symbol: str, limit: int = 720) -> List[Dict[str, Any]]:
    s = _normalize_symbol(symbol)
    try:
        rows = await _exchange_live.fetch_ohlcv(s, "1h", limit=limit)
        return [
            {
                "time": int(r[0] / 1000),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []

async def fetch_historical_pagination(symbol: str, start_ts: int, end_ts: int, limit: int = 1500) -> List[Dict[str, Any]]:
    """
    Historical 5m candles between [start_ts, end_ts).
    Uses ccxt pagination via 'since' in milliseconds.
    HARD STOP protections to prevent infinite loops.
    """
    s = _normalize_symbol(symbol)

    since_ms = int(start_ts) * 1000
    end_ms = int(end_ts) * 1000

    out: List[Dict[str, Any]] = []
    last_first_ts: Optional[int] = None
    safety_iters = 0

    while since_ms < end_ms:
        safety_iters += 1
        if safety_iters > 2000:
            # absolute hard stop
            break

        rows = await _exchange_live.fetch_ohlcv(s, "5m", since=since_ms, limit=limit)
        if not rows:
            break

        # Detect stuck pagination
        first_ts = int(rows[0][0])
        if last_first_ts is not None and first_ts == last_first_ts:
            break
        last_first_ts = first_ts

        for r in rows:
            t_ms = int(r[0])
            if t_ms >= end_ms:
                break
            out.append(
                {
                    "time": int(t_ms / 1000),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )

        # Move forward by one candle to avoid duplication
        last_ts = int(rows[-1][0])
        if last_ts <= since_ms:
            break
        since_ms = last_ts + (5 * 60 * 1000)

        # tiny yield to play nice with event loop
        await asyncio.sleep(0)

    # Deduplicate + sort (ccxt can overlap)
    out.sort(key=lambda x: int(x["time"]))
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for c in out:
        t = int(c["time"])
        if t in seen:
            continue
        seen.add(t)
        dedup.append(c)

    return dedup

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_placeholder_state(reason: str = "Waiting...") -> Dict[str, Any]:
    return {
        "action": "HOLD FIRE",
        "reason": reason,
        "permission": {"status": "NOT_EARNED", "side": "NONE"},
        "acceptance_progress": {"count": 0, "required": 2, "side_hint": "NONE"},
        "location": {"relative_to_triggers": "INSIDE"},
        "execution": {
            "pause_state": "NONE",
            "resumption_state": "NONE",
            "gates_mode": "PREVIEW",
            "locked_at": None,
            "levels": {"failure": 0.0, "continuation": 0.0},
        },
        "diagnostics": {"fail_reason": "WAITING"},
    }


def _war_map_from_1h(raw_1h: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Lightweight 1H context for UI.
    Not permission logic. Just pressure context.
    """
    if not raw_1h:
        return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "UNCLEAR", "note": "No 1h data."}

    closes = [float(c["close"]) for c in raw_1h]
    if len(closes) < 22:
        return {"status": "PLACEHOLDER", "lean": "NEUTRAL", "phase": "TRANSITION", "note": "Insufficient history."}

    alpha = 2.0 / (21.0 + 1.0)
    ema = closes[0]
    for px in closes[1:]:
        ema = (px * alpha) + (ema * (1 - alpha))

    lean = "BULLISH" if closes[-1] > ema else "BEARISH"
    return {"status": "LIVE", "lean": lean, "phase": "TRANSITION", "note": f"Pressure is {lean}."}


def _compute_sse_packet(raw_5m: List[Dict[str, Any]], anchor_ts: int, tuning: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Builds a locked packet from:
    - calibration window: [anchor_ts, anchor_ts + 1800)
    - 24h history ending at lock_end
    """
    lock_end_ts = int(anchor_ts) + 1800

    calibration = [c for c in raw_5m if anchor_ts <= int(c["time"]) < lock_end_ts]
    if len(calibration) < 6:
        return {"error": "Insufficient calibration data.", "lock_end_ts": lock_end_ts}

    # 24h history ending at lock end
    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= int(c["time"]) < lock_end_ts]
    if len(context_24h) < 50:
        # still try, but warn
        pass

    session_open = float(calibration[0]["open"])
    r30_high = max(float(c["high"]) for c in calibration)
    r30_low = min(float(c["low"]) for c in calibration)

    last_price = float(context_24h[-1]["close"]) if context_24h else session_open

    sse_input = {
        "locked_history_5m": context_24h,     # 5m-native contract
        "slice_24h_5m": context_24h,
        "slice_4h_5m": context_24h[-48:],     # last 4h in 5m bars
        "session_open_price": session_open,
        "r30_high": r30_high,
        "r30_low": r30_low,
        "last_price": last_price,
        "tuning": tuning or {},
    }

    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed:
        return computed

    return {
        "levels": computed["levels"],
        "context": computed.get("context", {}),
        "bias_model": computed.get("bias_model", {}),
        "htf_shelves": computed.get("htf_shelves", {}),
        "lock_time": lock_end_ts,
        "meta": computed.get("meta", {}),
    }


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
async def get_live_battlebox(
    symbol: str,
    session_mode: str = "AUTO",
    manual_id: Optional[str] = None,
    operator_flex: bool = False,
    tuning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Live view:
    - fetch candles
    - resolve current session via session_manager
    - if within 30m lock window => CALIBRATING
    - else compute/return locked packet + structure state
    """
    raw_5m = await fetch_live_5m(symbol)
    raw_1h = await fetch_live_1h(symbol)

    if not raw_5m:
        return {"status": "ERROR", "message": "No Data"}

    now_utc = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now_utc, session_mode, manual_id)
    anchor_ts = int(session["anchor_time"])
    lock_end_ts = anchor_ts + 1800

    # If we are in the first 30 minutes, we do not lock levels yet
    if int(now_utc.timestamp()) < lock_end_ts:
        wm = _war_map_from_1h(raw_1h)
        return {
            "status": "CALIBRATING",
            "timestamp": now_utc.strftime("%H:%M UTC"),
            "price": float(raw_5m[-1]["close"]),
            "energy": "CALIBRATING",
            "battlebox": {
                "war_map_context": wm,
                "session_battle": _safe_placeholder_state("Calibrating..."),
                "session": session,
                "levels": {},
                "bias_model": {},
                "context": {},
            },
        }

    # Cache key: one locked packet per session/day per symbol
    date_key = session["date_key"]
    session_key = f"{_normalize_symbol(symbol)}::{session['id']}::{date_key}"

    async with _CACHE_LOCK:
        if session_key not in _LOCKED_PACKETS:
            pkt = _compute_sse_packet(raw_5m, anchor_ts, tuning=tuning)
            if "error" in pkt:
                wm = _war_map_from_1h(raw_1h)
                return {
                    "status": "ERROR",
                    "message": pkt["error"],
                    "battlebox": {
                        "war_map_context": wm,
                        "session_battle": _safe_placeholder_state(pkt["error"]),
                        "session": session,
                        "levels": {},
                        "bias_model": {},
                        "context": {},
                    },
                }
            _LOCKED_PACKETS[session_key] = pkt

        pkt = _LOCKED_PACKETS[session_key]

    levels = pkt["levels"]
    lock_time = int(pkt["lock_time"])

    # Only feed post-lock candles into law layer (prevents drift)
    post_lock = [c for c in raw_5m if int(c["time"]) >= lock_time]

    state = structure_state_engine.compute_structure_state(
        levels=levels,
        candles_5m_post_lock=post_lock,
        tuning=tuning or {},
    )

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
        },
    }


async def get_session_review(
    symbol: str,
    session_id: str = "us_ny_futures",
    tuning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Session Review (aka Session Control "moment of truth packet"):
    - locks the most recent session open for that session_id
    - if still calibrating => CALIBRATING
    - else returns LOCKED packet levels + bias + context
    """
    raw_5m = await fetch_live_5m(symbol)
    if not raw_5m:
        return {"ok": False, "error": "No Data"}

    cfg = session_manager.get_session_config(session_id)

    now_utc = datetime.now(timezone.utc)
    anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, now_utc)
    lock_end_ts = anchor_ts + 1800

    # If anchor is in the future for today (rare due to timezone edges), shift to yesterday
    if anchor_ts > int(now_utc.timestamp()):
        anchor_ts -= 86400
        lock_end_ts -= 86400

    # If within lock window, return calibrating response
    if int(now_utc.timestamp()) < lock_end_ts:
        return {
            "ok": True,
            "mode": "CALIBRATING",
            "symbol": symbol,
            "session": {"id": cfg["id"], "name": cfg["name"], "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()},
            "message": "Calibrating... (first 30 minutes after open)",
        }

    pkt = _compute_sse_packet(raw_5m, anchor_ts, tuning=tuning)
    if "error" in pkt:
        return {"ok": False, "error": pkt["error"]}

    return {
        "ok": True,
        "mode": "LOCKED",
        "symbol": symbol,
        "price": float(raw_5m[-1]["close"]),
        "session": {
            "id": cfg["id"],
            "name": cfg["name"],
            "anchor_time": datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat(),
        },
        "levels": pkt["levels"],
        "bias_model": pkt.get("bias_model", {}),
        "context": pkt.get("context", {}),
        "htf_shelves": pkt.get("htf_shelves", {}),
        "meta": pkt.get("meta", {}),
        "range_30m": {
            "high": float(pkt["levels"].get("range30m_high", 0.0)),
            "low": float(pkt["levels"].get("range30m_low", 0.0)),
        },
    }
