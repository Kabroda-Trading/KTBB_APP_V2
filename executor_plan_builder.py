# executor_plan_builder.py
# ==============================================================================
# EXECUTOR PLAN BUILDER -- reads an already-FILLED TravelerPlan row + an
# account's own risk state, and computes the hypothetical order that
# account would place. Writes nothing itself -- the caller (executor_
# engine.py) owns persistence. Not a pure function anymore as of
# 2026-09-05 (it makes one real, read-only exchange call to verify
# leverage/margin mode when credentials are set -- see below), but still
# never mutates the DB or the exchange. This is the layer
# that never re-decides the trade: direction/entry/stop/T1 all come
# straight off the TravelerPlan row, verbatim (E1 has no T2/T3 -- single
# full-exit design). Originally Stage 1 of the Bitunix executor bot for
# v1/v2's own TradePlan lineage too (build_hypothetical_order(), removed
# 2026-09-24, V2 Crown retirement) -- see build_hypothetical_traveler_
# order()'s own docstring for what carried over vs. what didn't.
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
from database import ExecutorAccount, ExecutorOrder, ExecutorRiskState, TravelerPlan


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


async def _query_real_balance(account: ExecutorAccount) -> Dict[str, Any]:
    """Returns {"balance_usd": float, "source": str}. Same never-crash-on-
    network-hiccup fallback pattern as the leverage/mmr queries above --
    falls back to account.assumed_balance_usd (clearly labeled unverified)
    if no credentials are set yet or the query fails.

    equity = available + margin + isolationUnrealizedPNL -- confirmed
    against Andy's real Verify Auth response (2026-09-05) for the
    no-open-position case (available="726.0451...", margin="0",
    isolationUnrealizedPNL="0", no open position): available + margin
    correctly reproduces total account equity there. The
    isolationUnrealizedPNL term is the correct generalization for when a
    position IS open (his account runs ISOLATION margin mode, so margin
    alone would not include that position's own live unrealized P&L) --
    but this has NOT yet been tested against a response carrying a
    nonzero unrealized P&L. Flagged as the one remaining unverified edge,
    not the whole formula."""
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return {
            "balance_usd": account.assumed_balance_usd,
            "source": "no credentials set yet -- using assumed_balance_usd, NOT verified against the exchange",
        }

    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_balance()
        data = resp["data"]
        equity = float(data["available"]) + float(data["margin"]) + float(data.get("isolationUnrealizedPNL", 0))
        return {
            "balance_usd": equity,
            "source": "verified against the real exchange account (available + margin + isolationUnrealizedPNL)",
        }
    except Exception as e:
        return {
            "balance_usd": account.assumed_balance_usd,
            "source": f"balance query failed ({e}) -- using assumed_balance_usd, NOT verified against the exchange",
        }


async def _size_and_check_order(
    db: Session, base: Dict[str, Any], symbol: str, direction: str,
    entry_price: Optional[float], stop_price: Optional[float],
    account: ExecutorAccount, risk_state: ExecutorRiskState,
    sizing_multiplier: Optional[float] = None,
) -> Dict[str, Any]:
    """The shared sizing/leverage/liquidation core for
    build_hypothetical_traveler_order() (TravelerPlan/GATE_TRAVELER)
    below -- previously also shared with the now-deleted V2 wrapper,
    build_hypothetical_order() (TradePlan/v1/v2, removed 2026-09-24, V2
    Crown retirement). `base` already carries
    trade_plan_id/traveler_plan_id/account_id/mode/symbol/direction --
    this function only ADDS to it, never removes keys, so a caller's early-
    return dict shape stays consistent whether this runs or not.

    sizing_multiplier: Phase 2's F_A gate -- passed straight through to
    compute_stake(). Always a real float from build_hypothetical_
    traveler_order() (executor_sizing.f_a_multiplier() never returns
    None); the None default here just means "no multiplier" for any
    other/future caller.
    """
    if not entry_price or not stop_price or not direction:
        return {**base, "decision": "ERROR", "decision_reason": "plan is missing entry/stop/direction -- cannot size"}

    policy = executor_accounts.get_or_init_sizing_policy(db, account)

    # Only pay for the extra exchange call when the policy actually needs
    # the live balance -- FIXED/ROLLING-only accounts (no percent-of-
    # balance base, no percent cap, no balance-tiered switch) never query
    # it. compute_stake() itself still runs unconditionally; it degrades
    # to the existing risk_last_usd-based math when none of those
    # optional params are set.
    balance_usd: Optional[float] = None
    balance_source = "not queried -- policy does not use account balance"
    if (policy.base_risk_pct is not None or policy.cap_pct is not None
            or policy.tier_threshold_usd is not None or policy.band_step_usd is not None):
        balance_state = await _query_real_balance(account)
        balance_usd = balance_state["balance_usd"]
        balance_source = balance_state["source"]

    try:
        stake_usd, stake_detail = executor_sizing.compute_stake(
            risk_last_usd=risk_state.risk_last_usd,
            base_risk_pct=policy.base_risk_pct,
            account_balance_usd=balance_usd,
            tier_threshold_usd=policy.tier_threshold_usd,
            tier_flat_usd=policy.tier_flat_usd,
            band_step_usd=policy.band_step_usd,
            band_risk_per_step_usd=policy.band_risk_per_step_usd,
            band_below_pct=policy.band_below_pct,
            band_max_risk_usd=policy.band_max_risk_usd,
            cap_abs_usd=policy.cap_abs_usd,
            cap_pct=policy.cap_pct,
            consecutive_losses=risk_state.consecutive_losses,
            derisk_n=policy.derisk_n,
            derisk_factor=policy.derisk_factor,
            sizing_multiplier=sizing_multiplier,
        )
        stake_detail = {**stake_detail, "balance_source": balance_source}
        qty = executor_sizing.compute_qty(stake_usd, entry_price, stop_price)
    except ValueError as e:
        return {**base, "decision": "ERROR", "decision_reason": f"sizing failed: {e}"}

    exchange_state = await _query_real_leverage_and_margin_mode(account, symbol)
    leverage = exchange_state["leverage"]
    margin_mode = exchange_state["margin_mode"]

    notional = entry_price * qty
    mmr_state = await _query_real_maintenance_margin_rate(account, symbol, notional)

    liq_ok, liq_detail, liq_price = executor_sizing.check_leverage_is_safe(
        entry_price, stop_price, direction, leverage, maintenance_margin_rate=mmr_state["mmr"])
    margin_required = notional / leverage

    result = {
        **base,
        "entry_price": entry_price, "stop_price": stop_price,
        "risk_dollars_used": stake_usd,
        "stake_calculation_detail": stake_detail,
        "stop_distance": abs(entry_price - stop_price),
        "qty": qty, "leverage_used": leverage,
        "margin_required_usd": margin_required,
        "maintenance_margin_rate_used": mmr_state["mmr"],
        "liquidation_price_estimate": liq_price,
        "liquidation_check_passed": liq_ok,
        "liquidation_check_detail": liq_detail,
    }
    if sizing_multiplier is not None:
        result["sizing_multiplier_used"] = sizing_multiplier
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


# build_hypothetical_order() (TradePlan/v1/v2) removed 2026-09-24 (V2
# Crown retirement, Step 3f-ii) -- its only real caller,
# executor_engine.py's _process_account(), was deleted in the same pass.
# See build_hypothetical_traveler_order() below for GATE_TRAVELER's own
# counterpart, unaffected by this retirement.


async def build_hypothetical_traveler_order(
    db: Session, traveler_plan_row: TravelerPlan, account: ExecutorAccount, risk_state: ExecutorRiskState,
) -> Dict[str, Any]:
    """GATE_TRAVELER's plan builder -- sizing/leverage/liquidation core
    (_size_and_check_order()), fed from TravelerPlan. Idempotency is keyed on
    traveler_plan_id, a SEPARATE column/constraint from trade_plan_id (see
    database.py's ExecutorOrder docstring) -- deliberately NOT reusing
    trade_plan_id for this, since TravelerPlan and TradePlan have
    independent id sequences and could collide on the same integer.

    F_A (CC_WORK_ORDER_PHASE2.md step 5) is computed here, from THIS row's
    own rsi_4h_at_cross/direction (2026-09-21: was rsi_4h_at_lock -- see
    CC_WORK_ORDER_D1_RSI_AT_CROSS.md; that field is the measured DP0 basis,
    rsi_4h_at_lock is v2-only now), and passed through as compute_stake()'s
    sizing_multiplier -- a dollar-ledger-only scale, per that function's
    own docstring.
    """
    base = {
        "trade_plan_id": traveler_plan_row.id,  # audit/join convenience only -- see database.py's own comment on this
        "traveler_plan_id": traveler_plan_row.id,
        "account_id": account.id,
        "mode": account.mode,
        "symbol": traveler_plan_row.symbol,
        "direction": traveler_plan_row.direction,
        "t1_price": traveler_plan_row.t1_price, "t2_price": None, "t3_price": None,  # E1 has no T2/T3 -- full exit at T1
    }

    tradeable, reason = executor_accounts.is_account_tradeable(db, account)
    if not tradeable:
        decision = "SKIPPED_KILL_SWITCH" if "kill switch" in reason else "SKIPPED_ACCOUNT_INACTIVE"
        return {**base, "decision": decision, "decision_reason": reason}

    dup = db.query(ExecutorOrder).filter_by(account_id=account.id, traveler_plan_id=traveler_plan_row.id).first()
    if dup is not None:
        return {**base, "decision": "SKIPPED_ALREADY_IN_TRADE", "decision_reason": "an order already exists for this exact traveler plan + account"}

    # One-trade-at-a-time per account, same reasoning as build_hypothetical_
    # order() above -- checked against this bot's own WOULD_PLACE record
    # for any OTHER traveler plan that isn't DONE/STOPPED/FILLED-and-closed
    # yet. TravelerPlan has no post-fill status of its own (D3 management
    # lives on the ExecutorOrder row itself, same as v1/v2) -- FILLED is
    # its own terminal D1/D2 state, so "still open" here means the ORDER's
    # own management_state hasn't reached a CLOSED_* terminal value yet.
    other_would_places = db.query(ExecutorOrder).filter(
        ExecutorOrder.account_id == account.id,
        ExecutorOrder.decision == "WOULD_PLACE",
        ExecutorOrder.traveler_plan_id.isnot(None),
        ExecutorOrder.traveler_plan_id != traveler_plan_row.id,
    ).all()
    _open_states = ("PENDING_ENTRY", "ENTRY_FILLED_ORDERS_PLACED", "ENTRY_FILLED_UNPROTECTED", "T1_FILLED")
    for other in other_would_places:
        if (other.management_state or "PENDING_ENTRY") in _open_states:
            return {
                **base, "decision": "SKIPPED_ALREADY_IN_TRADE",
                "decision_reason": f"account already has an active order from traveler_plan_id={other.traveler_plan_id}",
            }

    f_a = executor_sizing.f_a_multiplier(traveler_plan_row.rsi_4h_at_cross, traveler_plan_row.direction)
    return await _size_and_check_order(
        db, base, traveler_plan_row.symbol, traveler_plan_row.direction,
        traveler_plan_row.fill_price, traveler_plan_row.stop_price, account, risk_state,
        sizing_multiplier=f_a,
    )
