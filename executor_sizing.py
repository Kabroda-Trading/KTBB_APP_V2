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
from typing import Any, Dict, Optional, Tuple

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


# ------------------------------------------------------------------
# 2026-09-05, Sizing Policy Wizard: the ONE stake-sizing primitive.
# Every preset (Conservative/Steady Grow/Scale With Account/Custom) is
# just which of these optional parameters is non-None -- deliberately
# never a separate code path per preset, per the design partner's own
# explicit requirement. "Stake" here means the same thing risk_last_usd
# already means throughout this module: a risk-DOLLAR amount (what's
# lost if the stop is hit), not notional or margin -- confirmed directly
# against Andy's own worked example for the percent-of-balance case
# ($1,000 account -> $100 stake -> $2,000 account -> $200 stake, i.e.
# 10% of balance IS the risk amount, not a position-value allocation).
# ------------------------------------------------------------------

def compute_stake(
    *,
    risk_last_usd: float,
    base_risk_pct: Optional[float] = None,
    account_balance_usd: Optional[float] = None,
    tier_threshold_usd: Optional[float] = None,
    tier_flat_usd: Optional[float] = None,
    cap_abs_usd: Optional[float] = None,
    cap_pct: Optional[float] = None,
    consecutive_losses: int = 0,
    derisk_n: Optional[int] = None,
    derisk_factor: Optional[float] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Returns (stake_usd, detail) -- detail is both the audit payload
    and exactly what the wizard's preview panel renders, so it records
    every stage rather than just the final number.

    Order of operations:
      1. base: base_risk_pct*account_balance_usd (percent-of-balance
         modes) or risk_last_usd (FIXED/ROLLING modes -- the rolled
         current baseline, advanced elsewhere by
         executor_accounts.record_trade_result() via compute_next_risk()).
      2. tier switch: REPLACES the base with tier_flat_usd once
         account_balance_usd >= tier_threshold_usd, evaluated fresh
         against the live balance every call -- reverting below the
         threshold falls out for free, no stored "am I tiered" flag.
      3. derisk (optional): once consecutive_losses >= derisk_n,
         multiplies the stake by derisk_factor -- a single-step shrink,
         not compounding per additional loss beyond N (the simplest
         defensible reading of an explicitly under-specified rule).
      4. dual caps: takes the MINIMUM of whichever of cap_abs_usd /
         cap_pct*account_balance_usd are set.
    """
    if base_risk_pct is not None:
        if account_balance_usd is None:
            raise ValueError("base_risk_pct requires account_balance_usd")
        base = account_balance_usd * base_risk_pct
    else:
        base = risk_last_usd

    detail: Dict[str, Any] = {"base": base, "tier_applied": False, "derisk_applied": False, "cap_binding": "none"}
    stake = base

    if tier_threshold_usd is not None and tier_flat_usd is not None:
        if account_balance_usd is None:
            raise ValueError("tier_threshold_usd/tier_flat_usd require account_balance_usd")
        if account_balance_usd >= tier_threshold_usd:
            stake = tier_flat_usd
            detail["tier_applied"] = True

    if derisk_n is not None and derisk_factor is not None and consecutive_losses >= derisk_n:
        stake = stake * derisk_factor
        detail["derisk_applied"] = True

    candidates = [stake]
    if cap_abs_usd is not None:
        candidates.append(cap_abs_usd)
    if cap_pct is not None and account_balance_usd is not None:
        candidates.append(cap_pct * account_balance_usd)

    final = min(candidates)
    if final < stake:
        if cap_abs_usd is not None and final == cap_abs_usd and (cap_pct is None or cap_abs_usd <= cap_pct * account_balance_usd):
            detail["cap_binding"] = "abs"
        elif cap_pct is not None:
            detail["cap_binding"] = "pct"

    detail["stake_before_cap"] = stake
    detail["final_stake"] = final
    return final, detail
