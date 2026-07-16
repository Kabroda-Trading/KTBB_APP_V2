"""
IMP-003: Position Sizing Module

Provides fixed-fractional, volatility-adjusted, and Kelly-based position sizing
for trade candidates. All values are reference/illustrative — no real capital
is tracked (see REFERENCE_ACCOUNT_BALANCE env var).

Integration: gravity_engine.py calls calc_position_size() after BOS candidate
creation, before writing CampaignLog.
"""

import os
import math
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REFERENCE_ACCOUNT_BALANCE = float(
    os.environ.get("REFERENCE_ACCOUNT_BALANCE", "10000")
)
"""Illustrative account balance. Not a real balance — purely for what-if sizing."""

RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "0.02"))
"""Fraction of account risked per trade (default 2%)."""

POSITION_SIZING_METHOD = os.environ.get("POSITION_SIZING_METHOD", "volatility")
"""One of: 'fixed_fractional', 'volatility', 'kelly'."""


# ---------------------------------------------------------------------------
# Core sizing functions
# ---------------------------------------------------------------------------


def calc_fixed_fractional(
    account_balance: float,
    risk_percent: float,
    entry_price: float,
    stop_price: float,
) -> float:
    """
    Fixed-fractional position sizing.

    Risk per trade = account_balance * risk_percent.
    Position size = risk_amount / |entry - stop|.

    Returns number of contracts/units (always >= 0).
    """
    if entry_price <= 0 or stop_price <= 0:
        return 0.0
    risk_amount = account_balance * risk_percent
    price_risk = abs(entry_price - stop_price)
    if price_risk < 1e-10:
        return 0.0
    return round(risk_amount / price_risk, 6)


def calc_volatility_adjusted(
    account_balance: float,
    risk_percent: float,
    entry_price: float,
    atr_value: float,
    atr_multiplier: float = 1.5,
) -> float:
    """
    Volatility-adjusted position sizing using ATR.

    Stop distance = atr_value * atr_multiplier.
    Position size = (account_balance * risk_percent) / stop_distance.

    Produces smaller positions in high-volatility environments.
    """
    if entry_price <= 0 or atr_value <= 0:
        return 0.0
    risk_amount = account_balance * risk_percent
    stop_distance = atr_value * atr_multiplier
    if stop_distance < 1e-10:
        return 0.0
    return round(risk_amount / stop_distance, 6)


def calc_kelly(
    fraction_won: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """
    Kelly Criterion: f* = p - (1-p) / (W/L)

    Where:
        p = fraction of winning trades
        W = average win amount
        L = average loss amount

    Returns optimal fraction of capital to risk (0.0 to 1.0).
    Clamped to [0, 0.25] for practical use (full Kelly is aggressive).
    """
    if avg_loss <= 0 or fraction_won <= 0 or fraction_won >= 1:
        return 0.0
    win_loss_ratio = avg_win / avg_loss
    kelly = fraction_won - ((1.0 - fraction_won) / win_loss_ratio)
    return max(0.0, min(kelly, 0.25))


def calc_position_size(
    entry_price: float,
    stop_price: float,
    atr_value: float,
    account_balance: Optional[float] = None,
    risk_percent: Optional[float] = None,
    method: Optional[str] = None,
) -> float:
    """
    Unified position sizing dispatcher.

    Args:
        entry_price: Entry price of the trade.
        stop_price: Initial stop loss price.
        atr_value: Current ATR value (from gravity_engine._calc_atr).
        account_balance: Override for REFERENCE_ACCOUNT_BALANCE.
        risk_percent: Override for RISK_PERCENT.
        method: One of 'fixed_fractional', 'volatility', 'kelly'.

    Returns:
        Number of contracts/units (0.0 if inputs are invalid).
    """
    bal = account_balance if account_balance is not None else REFERENCE_ACCOUNT_BALANCE
    risk = risk_percent if risk_percent is not None else RISK_PERCENT
    meth = method if method is not None else POSITION_SIZING_METHOD

    if bal <= 0 or risk <= 0 or entry_price <= 0:
        return 0.0

    if meth == "fixed_fractional":
        return calc_fixed_fractional(bal, risk, entry_price, stop_price)
    elif meth == "kelly":
        # Kelly requires historical stats — fall back to volatility if unavailable
        return calc_volatility_adjusted(bal, risk, entry_price, atr_value)
    else:
        # Default: volatility-adjusted
        return calc_volatility_adjusted(bal, risk, entry_price, atr_value)
