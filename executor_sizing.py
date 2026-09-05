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

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional, Tuple

# 2026-09-05: previously ignored Bitunix's real maintenance-margin-rate
# table entirely (the naive "100%-of-margin-lost" bound). Now takes an
# OPTIONAL real maintenance_margin_rate (queried live from Bitunix's
# get_position_tiers -- see executor_plan_builder.py's own
# _query_real_maintenance_margin_rate(), never a hardcoded/cached table,
# same philosophy as never trusting a stored leverage baseline). Default
# 0.0 reproduces the old naive formula exactly -- every existing caller/
# test that doesn't pass this param is unaffected.
#
# LONG:  liq = entry * (1 - 1/leverage + mmr)
# SHORT: liq = entry * (1 + 1/leverage - mmr)
# A higher real mmr moves liquidation CLOSER to entry (the exchange
# force-closes once maintenance margin, not 100% of margin, is breached).
def estimate_liquidation_price(
    entry_price: float, leverage: int, direction: str, maintenance_margin_rate: float = 0.0,
) -> float:
    if not entry_price or entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if not leverage or leverage <= 0:
        raise ValueError("leverage must be positive")
    # Clamped at 0 for the (should-never-happen-in-practice) case where
    # mmr >= 1/leverage: liq pins to entry_price exactly, which then
    # always fails check_liquidation_safety() (a zero liq distance can
    # never exceed a positive stop distance) -- fails safe automatically,
    # no special-case exception needed.
    adverse_move_pct = max(0.0, 1.0 / leverage - maintenance_margin_rate)
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
    maintenance_margin_rate: float = 0.0,
) -> Tuple[bool, str, float]:
    """Given the REAL, already-queried leverage (executor_bitunix_client.
    BitunixClient.get_leverage_and_margin_mode()) and, ideally, the REAL
    already-queried maintenance margin rate (get_position_tiers -- see
    executor_plan_builder.py), returns (is_safe, detail,
    liquidation_price_estimate). Callers must query real values
    themselves -- this function never assumes or defaults leverage, and
    defaults maintenance_margin_rate to 0.0 (the old naive bound) only
    for backward compatibility with callers that haven't been updated."""
    liq_price = estimate_liquidation_price(entry_price, leverage, direction, maintenance_margin_rate)
    safe, detail = check_liquidation_safety(entry_price, stop_price, liq_price, direction)
    return safe, detail, liq_price


# ------------------------------------------------------------------
# 2026-09-05, Stage 2: precision formatting for real order params.
# Bitunix's place_order/tpsl endpoints take qty/price as STRING types on
# the wire, each bounded by the exchange's own basePrecision/
# quotePrecision for a symbol (get_trading_pairs). Decimal is used here,
# and ONLY here in this module, specifically to avoid binary-float
# artifacts (Decimal(0.1) != Decimal('0.1')) -- it never propagates past
# these two functions' return boundary, a deliberate, scoped exception
# to this codebase's otherwise all-float convention.
# ------------------------------------------------------------------

def round_qty_to_precision(qty: float, precision: int) -> str:
    """Floors -- NEVER rounds up -- to `precision` decimal places. A
    qty must never exceed what basePrecision/minTradeVolume represents;
    rounding up here could send an order the exchange rejects or, worse,
    an unintended larger size. Returns a plain decimal string (no
    scientific notation)."""
    if precision < 0:
        raise ValueError("precision must be >= 0")
    quant = Decimal(1).scaleb(-precision)
    d = Decimal(str(qty)).quantize(quant, rounding=ROUND_DOWN)
    return format(d, "f")


def round_price_to_precision(price: float, precision: int) -> str:
    """Same Decimal-string approach as round_qty_to_precision(), but
    ROUND_HALF_UP -- a price has no qty's 'never exceed a floor'
    constraint, nearest-representable is the correct behavior."""
    if precision < 0:
        raise ValueError("precision must be >= 0")
    quant = Decimal(1).scaleb(-precision)
    d = Decimal(str(price)).quantize(quant, rounding=ROUND_HALF_UP)
    return format(d, "f")
