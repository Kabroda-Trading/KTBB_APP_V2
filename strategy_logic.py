# strategy_logic.py

from typing import Dict, Any


def evaluate_trade(
    context: Dict[str, Any],
    levels: Dict[str, float],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Computes whether a trade should be executed for a session, based on:
    - session context (candles, price, time)
    - levels (BO, BD, DR, DS)
    - config (alignment rules, stoch, confirmations, etc.)
    """

    result = {
        "active_side": "NONE",
        "directive": "HOLD FIRE",
        "acceptance": False,
        "had_alignment": False,
        "had_go": False,
        "go_reason": None,
        "fail_reason": None,
        "entry": None,
        "stop_loss": None,
        "targets": [],
        "trade_type": None,
    }

    # Ensure price and levels are valid
    price = context.get("price")
    if not price or not levels:
        result["fail_reason"] = "MISSING_DATA"
        return result

    bo = levels.get("breakout_trigger")
    bd = levels.get("breakdown_trigger")

    # === Confirmation Mode Logic ===
    mode = config.get("confirmation_mode", "1-Candle Close (Standard)")
    required_closes = config.get("acceptance_closes", 2)

    # Pull candle data for confirmation
    locked_candles = context.get("calibration_candles", [])
    if not locked_candles or len(locked_candles) < required_closes:
        result["fail_reason"] = "INSUFFICIENT_DATA"
        return result

    closes = [candle["close"] for candle in locked_candles[-required_closes:]]

    if all(c > bo for c in closes):
        result["acceptance"] = True
        result["active_side"] = "LONG"
    elif all(c < bd for c in closes):
        result["acceptance"] = True
        result["active_side"] = "SHORT"
    else:
        result["fail_reason"] = "NO_ACCEPTANCE"
        return result

    # === Alignment Filters ===
    ignore_alignment = config.get("ignore_alignment", False)
    ignore_stoch = config.get("ignore_stoch", False)

    if not ignore_alignment:
        result["fail_reason"] = "NO_ALIGNMENT"
        return result
    else:
        result["had_alignment"] = False
        result["had_go"] = True
        result["go_reason"] = "FORCED_15M_5M|STRUCT"
        result["directive"] = "EXECUTE"
        result["trade_type"] = "STRUCTURE"

        # --- Build Entry Structure ---
        result["entry"] = price
        result["stop_loss"] = compute_stop(price, result["active_side"], config)
        result["targets"] = compute_targets(price, result["active_side"])

        return result


def compute_stop(price: float, side: str, config: Dict[str, Any]) -> float:
    # Very basic logic for now
    risk_bps = config.get("stop_risk_bps", 120)
    if side == "LONG":
        return price - (price * risk_bps / 10000)
    else:
        return price + (price * risk_bps / 10000)


def compute_targets(price: float, side: str) -> list:
    target_bps = [40, 80, 140]
    targets = []

    for bps in target_bps:
        if side == "LONG":
            targets.append(price + (price * bps / 10000))
        else:
            targets.append(price - (price * bps / 10000))

    return targets
