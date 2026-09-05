# executor_sizing.py
# ==============================================================================
# EXECUTOR SIZING -- pure math only, no DB, no network. Stage 1 of the
# Bitunix executor bot (Kabroda AI Brain repo AGENT_LOG.md, 2026-09-04
# design conversation with Andy + DeepSeek).
#
# Dollar-risk-based sizing (qty = risk / stop_distance -- NOT a fixed
# contract count), Andy's compounding rule (additive, floor/cap-clamped),
# and the liquidation-vs-stop hard safety check: leverage so high that the
# exchange's liquidation price sits between entry and the stop means the
# position gets force-closed BEFORE the stop can ever fire -- the whole
# point of a stop is defeated. This module refuses to guess past that.
# ==============================================================================

from __future__ import annotations

from typing import Optional, Tuple

# Ignores Bitunix's real maintenance-margin-rate table (not yet obtained --
# see AGENT_LOG.md's open-risks list). This is the standard "100%-of-margin-
# lost" bound: the FARTHEST liquidation could possibly be from entry, since
# any real maintenance margin requirement only moves liquidation CLOSER to
# entry (an exchange never lets you lose literally 100% of margin before
# acting). So a pass on check_liquidation_safety() using this estimate is a
# NECESSARY, not sufficient, condition -- real liquidation is at least this
# close to entry, quite possibly closer. Treat this as an honest
# approximation, not a guarantee, until Bitunix's real maintenance-margin
# schedule is confirmed and this formula is corrected to include it.
def estimate_liquidation_price(entry_price: float, leverage: int, direction: str) -> float:
    if not entry_price or entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if not leverage or leverage <= 0:
        raise ValueError("leverage must be positive")
    adverse_move_pct = 1.0 / leverage
    if direction == "LONG":
        return entry_price * (1.0 - adverse_move_pct)
    if direction == "SHORT":
        return entry_price * (1.0 + adverse_move_pct)
    raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")


def compute_qty(risk_dollars: float, entry_price: float, stop_price: float) -> float:
    """qty = risk_dollars / stop_distance -- risk is dollar-defined, R
    multiples are feed-invariant; the exchange only matters for order
    placement, not for what qty represents."""
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be positive -- entry and stop cannot be equal")
    if risk_dollars <= 0:
        raise ValueError("risk_dollars must be positive")
    return risk_dollars / stop_distance


def compute_next_risk(
    risk_last: float, last_trade_pnl: float,
    floor: float = 100.0, cap: float = 1000.0, factor: float = 0.10,
) -> float:
    """Andy's compounding rule: risk_next = min(max(risk_last +
    factor*last_trade_pnl, floor), cap). Additive, anchored to the last
    trade's own PnL -- simulated against the real 2026 book (Kabroda AI
    Brain repo AGENT_LOG.md) before being locked in; the floor/cap are
    what tame the tail-risk drawdown the uncapped version showed."""
    return min(max(risk_last + factor * last_trade_pnl, floor), cap)


def check_liquidation_safety(
    entry_price: float, stop_price: float, liquidation_price: float, direction: str,
) -> Tuple[bool, str]:
    """The hard rule: liquidation must sit FARTHER from entry than the
    stop does, so the stop always has a chance to fire first. Returns
    (ok, detail) -- never raises on a failing check, that's the expected,
    common case this function exists to catch."""
    if direction == "LONG":
        stop_distance = entry_price - stop_price
        liq_distance = entry_price - liquidation_price
    elif direction == "SHORT":
        stop_distance = stop_price - entry_price
        liq_distance = liquidation_price - entry_price
    else:
        return False, f"unknown direction {direction!r}"

    if stop_distance <= 0:
        return False, f"stop ({stop_price}) is on the wrong side of entry ({entry_price}) for {direction}"

    ok = liq_distance > stop_distance
    if ok:
        return True, (
            f"liquidation ({liquidation_price:.2f}) is {liq_distance:.2f} from entry, "
            f"beyond the stop's {stop_distance:.2f} -- stop fires first"
        )
    return False, (
        f"liquidation ({liquidation_price:.2f}) is only {liq_distance:.2f} from entry, "
        f"INSIDE the stop's {stop_distance:.2f} -- leverage too high, refuse this trade"
    )


# 2026-09-05 CORRECTION, replacing an earlier design: this codebase used to
# have a suggest_leverage() that computed a "suggested" leverage to relieve
# margin pressure by raising it. That model doesn't match how Bitunix
# actually works -- verified directly against their place_order API
# parameters (symbol/qty/price/side/tradeSide/orderType/effect/tpPrice/
# slPrice/etc.): there is NO leverage parameter on an order. Leverage is a
# pre-set account/symbol-level configuration (changed only via a separate
# change_leverage call), not something chosen per-trade. So "suggesting" a
# leverage the bot never actually applies was dead computation -- the real
# order always executes at whatever leverage is ALREADY set on the
# exchange, known or not.
#
# This was caught for real, not hypothetically: the first live verify-auth
# check against Andy's real account (2026-09-05) returned leverage=40,
# while the whole design (and this account's own configured
# `leverage_baseline`) assumed 10x -- a real, silent drift between assumed
# and actual exchange state. Andy's resolution, now the standing
# principle: the bot queries the REAL leverage before every trade and
# sizes against reality, never a stored baseline; if that real leverage
# makes the liquidation-vs-stop check unsafe, the bot REFUSES the trade
# and says so loudly -- it does NOT call change_leverage() to silently fix
# it (that mutates real account state as a side effect the bot was never
# asked to take -- default OFF, matching this project's own "never guess,
# never fabricate a fix" discipline).
def check_leverage_is_safe(
    entry_price: float, stop_price: float, direction: str, leverage: int,
) -> Tuple[bool, str, float]:
    """Given the REAL, already-queried leverage (executor_bitunix_client.
    BitunixClient.get_leverage_and_margin_mode()), returns (is_safe,
    detail, liquidation_price_estimate). Callers must query the real
    value themselves -- this function never assumes or defaults one."""
    liq_price = estimate_liquidation_price(entry_price, leverage, direction)
    safe, detail = check_liquidation_safety(entry_price, stop_price, liq_price, direction)
    return safe, detail, liq_price
