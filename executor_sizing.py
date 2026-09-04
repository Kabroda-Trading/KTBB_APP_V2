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


# Deliberately not a strict >80% "reduce leverage" rule as an earlier design
# note phrased it -- margin_required = notional / leverage, so for a FIXED
# qty (already set by compute_qty()'s risk-based sizing), LOWERING leverage
# INCREASES margin required, it does not reduce it. Raising leverage is what
# relieves margin pressure for a fixed notional. (This corrects a numeric
# example in the design conversation -- Kabroda AI Brain repo AGENT_LOG.md,
# 2026-09-04 15:25 CT -- that described a lower leverage producing a lower
# margin figure; the arithmetic there doesn't hold for a fixed qty. Flagged
# back to AGENT_LOG.md separately rather than silently propagated here.)
# Raising leverage to relieve margin pressure moves liquidation CLOSER to
# entry, so this function never returns a leverage that would fail
# check_liquidation_safety() against the given stop -- if no leverage up to
# max_leverage satisfies both constraints, it says so rather than picking
# an unsafe one.
def suggest_leverage(
    entry_price: float, stop_price: float, direction: str, qty: float,
    leverage_baseline: int, free_balance_usd: Optional[float],
    max_margin_pct: float = 0.80, max_leverage: int = 20,
) -> Tuple[int, str]:
    notional = entry_price * qty

    def margin_at(lev: int) -> float:
        return notional / lev

    if not free_balance_usd or free_balance_usd <= 0:
        return leverage_baseline, "no balance figure available -- using baseline leverage unchecked"

    margin = margin_at(leverage_baseline)
    if margin <= max_margin_pct * free_balance_usd:
        return leverage_baseline, (
            f"margin ${margin:.2f} at {leverage_baseline}x is within "
            f"{max_margin_pct:.0%} of ${free_balance_usd:.2f} balance -- baseline leverage OK"
        )

    lev = leverage_baseline
    while lev < max_leverage:
        lev += 1
        margin = margin_at(lev)
        liq = estimate_liquidation_price(entry_price, lev, direction)
        safe, _ = check_liquidation_safety(entry_price, stop_price, liq, direction)
        if not safe:
            return leverage_baseline, (
                f"margin pressure at {leverage_baseline}x (${margin_at(leverage_baseline):.2f}), "
                f"but raising leverage further would violate the liquidation-vs-stop safety "
                f"check at {lev}x -- reduce risk_dollars instead of raising leverage"
            )
        if margin <= max_margin_pct * free_balance_usd:
            return lev, (
                f"leverage raised {leverage_baseline}x -> {lev}x to bring margin "
                f"(${margin:.2f}) within {max_margin_pct:.0%} of ${free_balance_usd:.2f} balance"
            )

    return leverage_baseline, (
        f"even at {max_leverage}x, margin (${margin_at(max_leverage):.2f}) exceeds "
        f"{max_margin_pct:.0%} of ${free_balance_usd:.2f} balance -- reduce risk_dollars instead"
    )
