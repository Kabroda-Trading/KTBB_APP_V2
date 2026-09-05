# executor_plan_builder.py
# ==============================================================================
# EXECUTOR PLAN BUILDER -- reads an already-FILLED TradePlan row + an
# account's own risk state, and computes the hypothetical order that
# account would place. Writes nothing itself -- the caller (executor_
# engine.py) owns persistence. Not a pure function anymore as of
# 2026-09-05 (it makes one real, read-only exchange call to verify
# leverage/margin mode when credentials are set -- see below), but still
# never mutates the DB or the exchange. This is the layer
# that never re-decides the trade: direction/entry/stop/T1/T2/T3 all come
# straight off the TradePlan row, verbatim. Stage 1 of the Bitunix
# executor bot.
#
# 2026-09-05: now queries the REAL leverage/margin mode from the exchange
# before every computation (async), rather than trusting a stored
# ExecutorAccount.leverage_baseline/margin_mode -- this is the direct
# fix for a real drift caught live: Andy's account's actual leverage
# (40x) didn't match what the whole design assumed (10x). See
# executor_sizing.py's own header for why the bot never "suggests" a
# leverage to use -- Bitunix's real place_order API has no leverage
# parameter at all; it's a pre-set account/symbol config the bot only
# ever reads, never changes. If the real leverage makes the trade unsafe
# (liquidation inside the stop), this REFUSES the trade -- it does not
# call change_leverage() to silently fix it (a real account mutation the
# bot was never asked to make -- default OFF, Andy's own explicit call).
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

import executor_accounts
import executor_sizing
from database import ExecutorAccount, ExecutorOrder, ExecutorRiskState, TradePlan


_FALLBACK_MMR_UNVERIFIED = 0.01
# 2026-09-05: deliberately HIGHER than Bitunix's own docs' top-tier
# BTCUSDT example (0.004-0.005) -- a fallback that's too LOW would make
# the liquidation estimate falsely OPTIMISTIC, the same class of danger
# as the 40x-vs-10x leverage incident this whole file's design already
# learned from. Erring high biases toward REJECTING borderline trades on
# a get_position_tiers query failure, never toward falsely approving
# one. This exact constant is a judgment call, flagged explicitly to
# Andy for sign-off -- not settled just because it's in code.


async def _query_real_maintenance_margin_rate(account: ExecutorAccount, symbol: str, notional_value: float) -> Dict[str, Any]:
    """Returns {"mmr": float, "source": str}. Same never-crash-on-network-
    hiccup fallback pattern as _query_real_leverage_and_margin_mode() --
    falls back to _FALLBACK_MMR_UNVERIFIED (clearly labeled unverified)
    if no credentials are set yet or the query fails. Selects the tier
    whose notional bracket (startValue <= notional_value < endValue)
    contains this trade's own notional -- NOT a leverage lookup; a
    tier's `leverage` field is that bracket's MAXIMUM ALLOWED leverage,
    not the account's actual configured leverage."""
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        # 0.0, not the elevated conservative fallback below -- "never
        # connected yet" is a benign, expected state (matches
        # _query_real_leverage_and_margin_mode()'s own no-credentials
        # branch, which reuses the configured baseline rather than
        # getting artificially more cautious). The elevated fallback is
        # reserved for the more alarming case: credentials exist and a
        # live query was actually attempted and failed.
        return {
            "mmr": 0.0,
            "source": "no credentials set yet -- using unverified 0.0 mmr (no real check attempted)",
        }

    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_position_tiers(symbol.replace("/", ""))
        tiers = resp["data"]
        tier = next(
            (t for t in tiers if float(t["startValue"]) <= notional_value < float(t["endValue"])),
            None,
        )
        if tier is None:
            return {
                "mmr": _FALLBACK_MMR_UNVERIFIED,
                "source": "no matching notional tier returned -- using conservative fallback MMR, NOT verified against the exchange",
            }
        return {
            "mmr": float(tier["maintenanceMarginRate"]),
            "source": "verified against the real exchange position tiers",
        }
    except Exception as e:
        return {
            "mmr": _FALLBACK_MMR_UNVERIFIED,
            "source": f"tiers query failed ({e}) -- using conservative fallback MMR, NOT verified against the exchange",
        }


async def _query_real_leverage_and_margin_mode(account: ExecutorAccount, symbol: str) -> Dict[str, Any]:
    """Returns {"leverage": int, "margin_mode": str, "source": str}.
    Falls back to the account's configured baseline (clearly labeled as
    unverified) if no credentials are set yet or the query fails -- never
    crashes the whole computation over a network hiccup, but never
    silently pretends a fallback is a verified value either."""
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return {
            "leverage": account.leverage_baseline, "margin_mode": account.margin_mode,
            "source": "no credentials set yet -- using configured baseline, NOT verified against the exchange",
        }

    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_leverage_and_margin_mode(symbol.replace("/", ""))
        data = resp["data"]
        return {
            "leverage": int(data["leverage"]), "margin_mode": data["marginMode"],
            "source": "verified against the real exchange account",
        }
    except Exception as e:
        return {
            "leverage": account.leverage_baseline, "margin_mode": account.margin_mode,
            "source": f"exchange query failed ({e}) -- using configured baseline, NOT verified against the exchange",
        }


async def build_hypothetical_order(
    db: Session, trade_plan_row: TradePlan, account: ExecutorAccount, risk_state: ExecutorRiskState,
) -> Dict[str, Any]:
    base = {
        "trade_plan_id": trade_plan_row.id,
        "account_id": account.id,
        "mode": account.mode,
        "symbol": trade_plan_row.symbol,
        "direction": trade_plan_row.direction,
    }

    tradeable, reason = executor_accounts.is_account_tradeable(db, account)
    if not tradeable:
        decision = "SKIPPED_KILL_SWITCH" if "kill switch" in reason else "SKIPPED_ACCOUNT_INACTIVE"
        return {**base, "decision": decision, "decision_reason": reason}

    # Idempotency: an order already exists for this EXACT (trade_plan_id,
    # account_id) pair -- the DB's own unique constraint would refuse a
    # second insert anyway; check here first for a clean decision/reason
    # instead of relying on a caller catching an IntegrityError.
    dup = db.query(ExecutorOrder).filter_by(account_id=account.id, trade_plan_id=trade_plan_row.id).first()
    if dup is not None:
        return {**base, "decision": "SKIPPED_ALREADY_IN_TRADE", "decision_reason": "an order already exists for this exact trade plan + account"}

    # One-trade-at-a-time per account (Andy's methodology: one trade at a
    # time). Stage 1 has no real position/fill tracking to check against
    # (documented non-goal), so this checks against this bot's OWN
    # would-place record for any OTHER trade plan that isn't DONE yet --
    # an approximation, not a guarantee, until Stage 2/3 add real fill
    # detection.
    other_would_places = db.query(ExecutorOrder).filter(
        ExecutorOrder.account_id == account.id,
        ExecutorOrder.decision == "WOULD_PLACE",
        ExecutorOrder.trade_plan_id != trade_plan_row.id,
    ).all()
    for other in other_would_places:
        other_plan = db.query(TradePlan).filter_by(id=other.trade_plan_id).first()
        if other_plan is not None and other_plan.status != "DONE":
            return {
                **base, "decision": "SKIPPED_ALREADY_IN_TRADE",
                "decision_reason": f"account already has an active order from trade_plan_id={other.trade_plan_id}",
            }

    entry_price = trade_plan_row.fill_price or trade_plan_row.trigger_price
    stop_price = trade_plan_row.stop_price
    direction = trade_plan_row.direction
    if not entry_price or not stop_price or not direction:
        return {**base, "decision": "ERROR", "decision_reason": "trade plan is missing entry/stop/direction -- cannot size"}

    try:
        qty = executor_sizing.compute_qty(risk_state.risk_last_usd, entry_price, stop_price)
    except ValueError as e:
        return {**base, "decision": "ERROR", "decision_reason": f"sizing failed: {e}"}

    exchange_state = await _query_real_leverage_and_margin_mode(account, trade_plan_row.symbol)
    leverage = exchange_state["leverage"]
    margin_mode = exchange_state["margin_mode"]

    notional = entry_price * qty
    mmr_state = await _query_real_maintenance_margin_rate(account, trade_plan_row.symbol, notional)

    liq_ok, liq_detail, liq_price = executor_sizing.check_leverage_is_safe(
        entry_price, stop_price, direction, leverage, maintenance_margin_rate=mmr_state["mmr"])
    margin_required = notional / leverage

    result = {
        **base,
        "entry_price": entry_price, "stop_price": stop_price,
        "t1_price": trade_plan_row.t1, "t2_price": trade_plan_row.t2, "t3_price": trade_plan_row.t3,
        "risk_dollars_used": risk_state.risk_last_usd,
        "stop_distance": abs(entry_price - stop_price),
        "qty": qty, "leverage_used": leverage,
        "margin_required_usd": margin_required,
        "maintenance_margin_rate_used": mmr_state["mmr"],
        "liquidation_price_estimate": liq_price,
        "liquidation_check_passed": liq_ok,
        "liquidation_check_detail": liq_detail,
    }
    if margin_mode != account.margin_mode:
        return {
            **result, "decision": "REJECTED",
            "decision_reason": (
                f"real exchange margin mode ({margin_mode}) does not match configured "
                f"({account.margin_mode}) -- {exchange_state['source']}; fix the mismatch before trading"
            ),
        }
    if not liq_ok:
        return {
            **result, "decision": "REJECTED",
            "decision_reason": f"{liq_detail} (leverage {exchange_state['source']}; mmr {mmr_state['source']})",
        }
    return {
        **result, "decision": "WOULD_PLACE",
        "decision_reason": (
            f"leverage {leverage}x, {exchange_state['source']}; "
            f"mmr {mmr_state['mmr']}, {mmr_state['source']}; {liq_detail}"
        ),
    }
