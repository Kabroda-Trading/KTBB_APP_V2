# black_ops_engine.py
# PROJECT OMEGA — BLACK OPS v13.0 UNIFIED ENGINE

from typing import Dict, Any, List
from fastapi import APIRouter
from session_context import get_session_context
from sse_engine import generate_levels
from strategy_logic import evaluate_trade

router = APIRouter()


def _compute_stoch(candles: List[Dict], k_period: int = 14) -> Dict[str, float]:
    if len(candles) < k_period:
        return {"k": 50.0, "d": 50.0}
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    hh = max(highs[-k_period:])
    ll = min(lows[-k_period:])
    curr = closes[-1]
    k_val = 50.0 if hh == ll else ((curr - ll) / (hh - ll)) * 100.0
    return {"k": k_val, "d": k_val}


def _compute_rsi(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 50.0
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


@router.get("/blackops/{session_id}")
def blackops_directive(session_id: str = "us_ny_futures") -> Dict[str, Any]:
    try:
        # 1. Get locked session context
        context = get_session_context(session_id)

        # 2. Compute level triggers using locked candles
        levels = generate_levels(context["calibration_candles"], context)

        # 3. Set strategy config (can pull from DB later)
        config = {
            "confirmation_mode": "1-Candle Close (Standard)",
            "acceptance_closes": 2,
            "ignore_alignment": True,
            "ignore_stoch": True,
            "stop_risk_bps": 120
        }

        # 4. Evaluate go/no-go directive
        directive = evaluate_trade(context, levels, config)

        # 5. Compute live indicators
        stoch = _compute_stoch(context["calibration_candles"][-20:])
        rsi = _compute_rsi(context["calibration_candles"][-20:])

        # 6. Target logic
        price = context["price"]
        dr = context["r30_high"]
        ds = context["r30_low"]
        energy = abs(dr - ds) or price * 0.01

        side = directive["active_side"]
        entry = directive["entry"] or price
        is_blue_sky = entry > dr if side == "LONG" else entry < ds if side == "SHORT" else False

        if side == "LONG":
            t1 = dr if not is_blue_sky else entry + (energy * 0.5)
            targets = [
                {"id": "T1", "price": round(t1, 2)},
                {"id": "T2", "price": round(entry + energy, 2)},
                {"id": "T3", "price": round(entry + (energy * 3.0), 2)}
            ]
        elif side == "SHORT":
            t1 = ds if not is_blue_sky else entry - (energy * 0.5)
            targets = [
                {"id": "T1", "price": round(t1, 2)},
                {"id": "T2", "price": round(entry - energy, 2)},
                {"id": "T3", "price": round(entry - (energy * 3.0), 2)}
            ]
        else:
            targets = []

        telemetry = {
            "session_state": context["status"],
            "next_event_ts": context["lock_end_ts"],
            "verification": {
                "r30_high": dr,
                "r30_low": ds,
                "daily_res": levels.get("daily_resistance"),
                "daily_sup": levels.get("daily_support")
            }
        }

        return {
            "ok": True,
            "status": directive["directive"],
            "symbol": session_id,
            "price": price,
            "side": side,
            "context": "BLUE SKY" if is_blue_sky else "STRUCTURE",
            "strength": {
                "score": 0,
                "rating": directive["directive"],
                "tags": [],
                "is_blue_sky": is_blue_sky
            },
            "triggers": {
                "BO": levels.get("breakout_trigger"),
                "BD": levels.get("breakdown_trigger")
            },
            "telemetry": telemetry,
            "execution": {
                "entry": entry,
                "stop_loss": directive.get("stop_loss"),
                "targets": targets,
                "fusion_metrics": {
                    "k": stoch["k"],
                    "rsi": rsi
                }
            }
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "ERROR",
            "msg": str(e)
        }
