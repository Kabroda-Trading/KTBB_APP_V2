# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB (v1 - ISOLATED BACKTEST LAB)
# ------------------------------------------------------------------------------
# Purpose:
# - Replay sessions to measure "how often the system actually allows trades"
# - Uses SSE v2.0 (5m-native) + Structure State Engine (Law Layer)
#
# Non-goals:
# - No PnL simulation
# - No strategy execution
# - No modifications to live Battle Control
# ==============================================================================

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import traceback
import pytz
import ccxt.async_support as ccxt
import sse_engine
import structure_state_engine

# ----------------------------
# Config (match Battle Control)
# ----------------------------
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity", "name": "NY Equity", "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london", "name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo", "name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"id": "au_sydney", "name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

DEFAULT_SESSION_IDS = [s["id"] for s in SESSION_CONFIGS]

exchange_kucoin = ccxt.kucoin({"enableRateLimit": True})

def _sym(symbol: str) -> str:
    s = (symbol or "BTCUSDT").strip().upper()
    return s.replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")

def _to_candle_list(ohlcv: List[List[float]]) -> List[Dict[str, Any]]:
    # [ms, open, high, low, close, volume]
    out = []
    for c in ohlcv:
        out.append({
            "time": int(c[0] / 1000),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        })
    return out

async def fetch_5m_range(symbol: str, start_ts: int, end_ts: int, limit_per_call: int = 1500) -> List[Dict[str, Any]]:
    """
    Fetch 5m candles between [start_ts, end_ts).
    Uses pagination via `since`.
    """
    market = _sym(symbol)
    since_ms = start_ts * 1000
    end_ms = end_ts * 1000

    all_rows: List[List[float]] = []
    safety = 0

    while since_ms < end_ms and safety < 200:
        safety += 1
        try:
            rows = await exchange_kucoin.fetch_ohlcv(market, "5m", since=since_ms, limit=limit_per_call)
        except Exception:
            break

        if not rows:
            break

        # Keep only rows before end
        for r in rows:
            if r[0] < end_ms:
                all_rows.append(r)

        last_ms = rows[-1][0]
        # Advance by 1 candle to avoid duplicates
        since_ms = last_ms + (5 * 60 * 1000)

        # If exchange stopped advancing, break
        if len(rows) < 2:
            break

    # Dedup by timestamp
    seen = set()
    dedup = []
    for r in all_rows:
        ts = int(r[0])
        if ts in seen:
            continue
        seen.add(ts)
        dedup.append(r)

    dedup.sort(key=lambda x: x[0])
    return _to_candle_list(dedup)

def _session_anchor_utc_for_date(session_cfg: Dict[str, Any], day_utc: datetime) -> int:
    """
    Given a UTC day (date), compute the session open time for that date in the session timezone,
    then convert to UTC timestamp.
    """
    tz = pytz.timezone(session_cfg["tz"])
    # Convert the UTC day start into local, then replace time to open
    day_local = day_utc.astimezone(tz)
    open_local = day_local.replace(hour=session_cfg["open_h"], minute=session_cfg["open_m"], second=0, microsecond=0)
    # Convert back to UTC
    open_utc = open_local.astimezone(pytz.UTC)
    return int(open_utc.timestamp())

def _slice_by_ts(candles: List[Dict[str, Any]], start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    return [c for c in candles if start_ts <= int(c["time"]) < end_ts]

def _compute_locked_levels_from_5m(raw_5m: List[Dict[str, Any]], anchor_ts: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Implements the doctrine:
    - calibration window is exactly [anchor_ts, anchor_ts+1800)
    - context is 24h ending at lock_end_ts
    """
    lock_end_ts = anchor_ts + 1800

    calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
    if len(calibration) < 6:
        return None, "Insufficient calibration window (need 6x 5m candles)."

    context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
    # slice_4h = _slice_by_ts(raw_5m, lock_end_ts - 14400, lock_end_ts)

    # 24h fallback if context is short
    if len(context_24h) < 50:
        pass 

    # For SSE v2.0 input contract
    slice_4h = _slice_by_ts(raw_5m, lock_end_ts - 14400, lock_end_ts)

    session_open_price = float(calibration[0]["open"])
    r30_high = max(float(c["high"]) for c in calibration)
    r30_low = min(float(c["low"]) for c in calibration)
    last_price = float(context_24h[-1]["close"]) if context_24h else session_open_price

    sse_input = {
        "locked_history_5m": context_24h,
        "slice_24h_5m": context_24h,
        "slice_4h_5m": slice_4h,
        "raw_daily_candles": [],  # optional; lab can run without
        "session_open_price": session_open_price,
        "r30_high": r30_high,
        "r30_low": r30_low,
        "last_price": last_price,
    }

    computed = sse_engine.compute_sse_levels(sse_input)
    if "error" in computed:
        return None, computed["error"]

    return {
        "lock_end_ts": lock_end_ts,
        "levels": computed["levels"],
        "sse_meta": computed.get("meta", {}),
        "bias_model": computed.get("bias_model", {}),
        "context": computed.get("context", {}),
    }, None

def _replay_structure_counts(levels: Dict[str, float], post_lock_5m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Step-by-step replay:
    - acceptance: first time permission.status becomes EARNED
    - alignment: first time gates_mode becomes LOCKED
    """
    acceptance_ts = None
    alignment_ts = None
    acceptance_side = "NONE"

    last_state = None

    for i in range(1, len(post_lock_5m) + 1):
        window = post_lock_5m[:i]
        st = structure_state_engine.compute_structure_state(levels, window)

        last_state = st

        # acceptance
        perm = (st.get("permission") or {})
        if acceptance_ts is None and perm.get("status") == "EARNED":
            acceptance_ts = window[-1]["time"]
            acceptance_side = perm.get("side", "NONE")

        # alignment
        exe = (st.get("execution") or {})
        if alignment_ts is None and exe.get("gates_mode") == "LOCKED":
            alignment_ts = window[-1]["time"]

        # If we have both, we can stop replay early
        if acceptance_ts is not None and alignment_ts is not None:
            break

    return {
        "acceptance_ts": acceptance_ts,
        "acceptance_side": acceptance_side,
        "alignment_ts": alignment_ts,
        "final_state": last_state,
    }

async def run_research_lab(
    symbol: str,
    start_date_utc: str,
    end_date_utc: str,
    session_ids: Optional[List[str]] = None,
    session_horizon_minutes: int = 720,  # replay 12 hours after lock by default
) -> Dict[str, Any]:
    """
    Returns counts and per-session outcomes.
    Dates are YYYY-MM-DD in UTC.
    """
    session_ids = session_ids or DEFAULT_SESSION_IDS
    cfgs = [s for s in SESSION_CONFIGS if s["id"] in session_ids]
    if not cfgs:
        return {"ok": False, "error": "No valid session_ids selected."}

    try:
        start_day = datetime.fromisoformat(start_date_utc).replace(tzinfo=timezone.utc)
        end_day = datetime.fromisoformat(end_date_utc).replace(tzinfo=timezone.utc)
    except ValueError:
        return {"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}

    if end_day < start_day:
        return {"ok": False, "error": "end_date_utc must be >= start_date_utc"}

    # Pull enough data to cover all sessions:
    # from start_day 00:00 - 24h context cushion to end_day 23:59 + horizon
    fetch_start = int((start_day - timedelta(days=1)).timestamp())
    fetch_end = int((end_day + timedelta(days=1)).timestamp()) + (session_horizon_minutes * 60)

    raw_5m = await fetch_5m_range(symbol, fetch_start, fetch_end)
    if not raw_5m:
        return {"ok": False, "error": "No 5m data returned from exchange."}

    sessions_out: List[Dict[str, Any]] = []

    day = start_day
    while day <= end_day:
        for cfg in cfgs:
            anchor_ts = _session_anchor_utc_for_date(cfg, day)
            lock_end_ts = anchor_ts + 1800
            session_end_ts = lock_end_ts + (session_horizon_minutes * 60)

            # Only analyze if we have at least through lock_end
            lock_packet, err = _compute_locked_levels_from_5m(raw_5m, anchor_ts)
            if err:
                sessions_out.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "session_id": cfg["id"],
                    "session_name": cfg["name"],
                    "anchor_ts": anchor_ts,
                    "lock_end_ts": lock_end_ts,
                    "ok": False,
                    "error": err,
                })
                continue

            levels = lock_packet["levels"]

            post_lock = _slice_by_ts(raw_5m, lock_end_ts, session_end_ts)
            replay = _replay_structure_counts(levels, post_lock) if post_lock else {
                "acceptance_ts": None, "acceptance_side": "NONE", "alignment_ts": None, "final_state": None
            }

            sessions_out.append({
                "date": day.strftime("%Y-%m-%d"),
                "session_id": cfg["id"],
                "session_name": cfg["name"],
                "anchor_ts": anchor_ts,
                "lock_end_ts": lock_end_ts,
                "ok": True,
                "levels": levels,
                "bias_model": lock_packet.get("bias_model", {}),
                "context": lock_packet.get("context", {}),
                "counts": {
                    "had_acceptance": bool(replay["acceptance_ts"]),
                    "had_alignment": bool(replay["alignment_ts"]),
                },
                "events": replay,
            })

        day = day + timedelta(days=1)

    # Aggregate stats
    ok_sessions = [s for s in sessions_out if s.get("ok")]
    acceptance = sum(1 for s in ok_sessions if s["counts"]["had_acceptance"])
    alignment = sum(1 for s in ok_sessions if s["counts"]["had_alignment"])
    no_trade = sum(1 for s in ok_sessions if not s["counts"]["had_alignment"])

    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "range": {"start_date_utc": start_date_utc, "end_date_utc": end_date_utc},
        "session_ids": session_ids,
        "stats": {
            "sessions_total": len(ok_sessions),
            "acceptance_count": acceptance,
            "alignment_count": alignment,
            "no_trade_sessions": no_trade,
            "alignment_rate_pct": round((alignment / max(len(ok_sessions), 1)) * 100.0, 1),
        },
        "sessions": sessions_out,
    }