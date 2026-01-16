# project_omega.py
# ==============================================================================
# PROJECT OMEGA ENGINE (CLEAN + SESSION PICKER + FERRARI MODE)
# - Truth source: session_manager.resolve_anchor_time(session_id)
# - Data source: battlebox_pipeline.fetch_live_5m
# - Levels: sse_engine.compute_sse_levels (frozen 24h context to lock_end_ts)
# - Ferrari mode: tighter triggers / acceptance option (no research lab dependency)
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List, Optional

import session_manager
import battlebox_pipeline
import sse_engine

# ----------------------------
# Internal Indicators (simple)
# ----------------------------
def _compute_stoch(candles: List[Dict[str, Any]], k_period: int = 14) -> Dict[str, float]:
    if len(candles) < k_period:
        return {"k": 50.0, "d": 50.0}
    try:
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        hh = max(highs[-k_period:])
        ll = min(lows[-k_period:])
        curr = closes[-1]
        k_val = 50.0 if hh == ll else ((curr - ll) / (hh - ll)) * 100.0
        return {"k": k_val, "d": k_val}
    except Exception:
        return {"k": 50.0, "d": 50.0}

def _compute_rsi(candles: List[Dict[str, Any]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 50.0
    try:
        closes = [float(c["close"]) for c in candles]
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    except Exception:
        return 50.0

def _calc_strength(entry: float, dr: float, ds: float, side: str) -> Dict[str, Any]:
    is_blue_sky = False
    if entry <= 0:
        return {"score": 0, "rating": "WAITING", "tags": [], "is_blue_sky": False}
    if side == "LONG" and entry > dr:
        is_blue_sky = True
    if side == "SHORT" and entry < ds:
        is_blue_sky = True
    return {"score": 0, "rating": "GO", "tags": [], "is_blue_sky": is_blue_sky}

# ----------------------------
# Core Omega
# ----------------------------
async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
) -> Dict[str, Any]:
    """
    ferrari_mode (aggressive):
      - uses tighter "near trigger" lock window
      - can be extended later to different entry logic
    """
    current_price = 0.0
    try:
        # 1) Central truth (anchor + lock window)
        resolve_fn = getattr(session_manager, "resolve_anchor_time", None)
        if not callable(resolve_fn):
            return {"ok": False, "status": "ERROR", "msg": "session_manager.resolve_anchor_time missing"}

        sess = resolve_fn(session_id)
        anchor_ts = int(sess["anchor_ts"])
        lock_end_ts = int(sess["lock_end_ts"])
        session_status = str(sess.get("status") or "ACTIVE")

        # 2) Live 5m feed
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)
        if not raw_5m or len(raw_5m) < 20:
            return {"ok": False, "status": "OFFLINE", "msg": "Waiting for data..."}

        current_price = float(raw_5m[-1]["close"])

        # 3) Strict lock context
        calibration = [c for c in raw_5m if anchor_ts <= int(c["time"]) < lock_end_ts]
        context_start = lock_end_ts - 86400
        context_24h = [c for c in raw_5m if context_start <= int(c["time"]) < lock_end_ts]

        r30_high = r30_low = 0.0
        bo = bd = dr = ds = 0.0

        if len(calibration) >= 4 and len(context_24h) > 100:
            r30_high = max(float(c["high"]) for c in calibration)
            r30_low = min(float(c["low"]) for c in calibration)

            sse_input = {
                "locked_history_5m": context_24h,
                "slice_24h_5m": context_24h,
                "slice_4h_5m": context_24h[-48:],
                "session_open_price": float(calibration[0]["open"]),
                "r30_high": r30_high,
                "r30_low": r30_low,
                "last_price": current_price,
                "tuning": {"ferrari_mode": bool(ferrari_mode)},
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed.get("levels", {}) if isinstance(computed, dict) else {}

            bo = float(levels.get("breakout_trigger", 0.0))
            bd = float(levels.get("breakdown_trigger", 0.0))
            dr = float(levels.get("daily_resistance", 0.0))
            ds = float(levels.get("daily_support", 0.0))

        # 4) Execution state (live against locked levels)
        status = "STANDBY"
        side = "NONE"
        stop_loss = 0.0

        # Ferrari mode: tighter “near trigger” radius
        near_radius = 0.0007 if ferrari_mode else 0.0010

        if session_status in ("ACTIVE", "CLOSED"):
            if bo > 0 and bd > 0:
                last_candle = raw_5m[-2] if len(raw_5m) > 1 else raw_5m[-1]
                last_close = float(last_candle["close"])

                if last_close > bo:
                    status = "EXECUTING"
                    side = "LONG"
                    stop_loss = r30_low
                elif last_close < bd:
                    status = "EXECUTING"
                    side = "SHORT"
                    stop_loss = r30_high
                else:
                    # "LOCKED" (near trigger)
                    if abs(current_price - bo) / bo < near_radius:
                        status = "LOCKED"
                        side = "LONG"
                    elif abs(current_price - bd) / bd < near_radius:
                        status = "LOCKED"
                        side = "SHORT"

        if session_status == "CALIBRATING":
            status = "CALIBRATING"
        if session_status == "CLOSED":
            status = "CLOSED"

        # 5) Targets + telemetry
        trigger_px = bo if side == "LONG" else bd
        if side == "NONE" and bo > 0 and bd > 0:
            mid = (bo + bd) / 2.0
            trigger_px = bo if current_price >= mid else bd

        strength = _calc_strength(trigger_px, dr, ds, side)

        energy = abs(dr - ds)
        if energy == 0:
            energy = current_price * 0.01

        targets = []
        if side == "LONG" or (side == "NONE" and current_price >= (bo + bd) / 2.0):
            t1 = dr if not strength["is_blue_sky"] else trigger_px + (energy * 0.5)
            targets = [
                {"id": "T1", "price": round(t1, 2)},
                {"id": "T2", "price": round(trigger_px + energy, 2)},
                {"id": "T3", "price": round(trigger_px + (energy * 3.0), 2)},
            ]
        elif bd > 0:
            t1 = ds if not strength["is_blue_sky"] else trigger_px - (energy * 0.5)
            targets = [
                {"id": "T1", "price": round(t1, 2)},
                {"id": "T2", "price": round(trigger_px - energy, 2)},
                {"id": "T3", "price": round(trigger_px - (energy * 3.0), 2)},
            ]

        stoch = _compute_stoch(raw_5m[-20:])
        rsi = _compute_rsi(raw_5m[-20:])

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "session_id": session_id,
            "ferrari_mode": bool(ferrari_mode),
            "price": current_price,
            "side": side,
            "context": "BLUE SKY" if strength["is_blue_sky"] else "STRUCTURE",
            "strength": strength,
            "triggers": {"BO": bo, "BD": bd},
            "telemetry": {
                "session_state": session_status,
                "anchor_ts": anchor_ts,
                "lock_end_ts": lock_end_ts,
                "verification": {"r30_high": r30_high, "r30_low": r30_low, "daily_res": dr, "daily_sup": ds},
            },
            "execution": {
                "entry": trigger_px,
                "stop_loss": stop_loss,
                "targets": targets,
                "fusion_metrics": {"k": stoch["k"], "rsi": rsi},
            },
        }

    except Exception as e:
        return {"ok": False, "status": "ERROR", "price": current_price, "msg": str(e)}
