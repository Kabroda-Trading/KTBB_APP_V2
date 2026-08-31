# trade_plan.py
# ==============================================================================
# TRADE PLAN — GENERATION + PRE-COMMIT BRIEF + INTRADAY STATE MACHINE
# KABRODA_COM_TRADE_PLAN_SPEC.md SS3 (the plan object), SS4 (the brief),
# SS5 (the state machine), SS8 (re-entry). Generated ONCE at the session
# lock, never re-generated intraday (SS1's anti-flip-flop rule).
#
# Composed entirely from things that already exist and are already
# validated: decision_engine.py's gate decision (levels, tier, entry/stop/
# targets -- the SSOT, untouched), stop_planner.py's NEW, additive
# execution stop (confirmed with Andy, 2026-08-31, docs/STOP_BASIS_ANSWER.md
# in the Kabroda AI Brain repo -- does not replace or feed decision_
# engine.py's r30-based stop_loss anywhere), and fuel_gate.py (already
# ported from Brain, SS7).
#
# management text: the VALIDATED rule (30% off at T1, 70% runner, stop to
# the fixed runner-stop level, T3 -- same for BOTH tiers), matching what
# ledger_closing_engine.py actually runs. The spec's first draft described
# something different (tier-dependent, stop-to-breakeven) -- a real spec/
# code mismatch, caught and corrected in the Brain repo (commit d8a33ce)
# rather than silently picked one way. Do not let this drift from
# ledger_closing_engine.py's real behavior again without the same check.
#
# Intraday state machine design (2026-08-31): TradePlan's own monitoring
# only covers the PRE-FILL, fuel-gated entry logic (WAITING/VETOED/FILLED)
# -- that's genuinely new, CampaignLog's own fill detection (ledger_
# closing_engine.py Phase 1) is price-only, no fuel gating. POST-fill
# management (T1 partial/runner-stop/T3) is NOT re-implemented a second
# time here -- mirror_campaign_outcome() below watches CampaignLog's own
# already-verified terminal status for the same session instead. Don't
# rebuild what yesterday's 6 regression tests already cover.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import fuel_gate

import stop_planner as sp

FUEL_REQUIREMENT_TEXT = (
    "push must read FUELED at the cross (median-based push-volume ratio "
    ">= 0.8x prior-24h baseline)"
)

# Same for both tiers -- KABRODA_COM_TRADE_PLAN_SPEC.md SS3 (corrected,
# commit d8a33ce), matching ledger_closing_engine.py's real Phase 2 logic.
MANAGEMENT_TEXT = (
    "30% off at T1, stop moves to a fixed runner-stop level "
    "(trigger -+ 0.15x box, ~0.25R loss if hit), 70% rides to T3. "
    "Same rule for PREMIUM and STANDARD -- validated best-or-tied in every "
    "regime (n=165); not tier-dependent, not stop-to-breakeven."
)

COMMIT_OFFSET_MINUTES = 45  # anchor_time + 45min = 08:45 CT / 09:45 ET (the open-window rule)

_TAKE_STATES = ("TAKE_PREMIUM", "TAKE_STANDARD")


def build_trade_plan(
    symbol: str,
    date_key: str,
    session_id: str,
    decision_dict: Dict[str, Any],
    anchor_time: datetime.datetime,
    candles_24h: list,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
    daily_atr14: float,
) -> Dict[str, Any]:
    """Builds the TradePlan fields (a dict, ready for the TradePlan model)
    from an already-computed gate decision (decision_engine.evaluate_15m_
    decision()'s decision_dict) plus the inputs stop_planner.py needs.

    Returns a dict matching database.TradePlan's columns (caller writes it
    to the DB and is responsible for the (symbol, date_key, session_id)
    upsert -- this function is a pure builder, no DB access, matching the
    rest of this codebase's small-single-purpose-module pattern).
    """
    state = decision_dict.get("verdict_state")
    commit_after = anchor_time + datetime.timedelta(minutes=COMMIT_OFFSET_MINUTES)

    base = {
        "symbol": symbol, "date_key": date_key, "session_id": session_id,
        "commit_after": commit_after,
        "fuel_requirement": FUEL_REQUIREMENT_TEXT,
        "management": MANAGEMENT_TEXT,
    }

    if state not in _TAKE_STATES:
        # NO_PLAN is a valid, common outcome (SS3) -- not an error case.
        # decision_dict["tactical_brief"] is already the gate's own specific
        # reason (decision_engine.py's misses list), reused verbatim rather
        # than re-derived.
        return {
            **base,
            "status": "NO_PLAN",
            "no_plan_reason": decision_dict.get("tactical_brief") or f"gate state: {state}",
        }

    side = decision_dict.get("side")
    is_long = side == "LONG"
    entry_price = float(decision_dict["entry_price"])
    t1 = float(decision_dict["t1"])
    t2 = float(decision_dict["t2"])
    t3 = float(decision_dict["t3"])
    tier = decision_dict.get("tier")

    stop_plan = sp.plan_stop(
        candles_24h=candles_24h,
        entry_price=entry_price, is_long=is_long,
        r30_high=r30_high, r30_low=r30_low,
        f24_vah=f24_vah, f24_val=f24_val,
        daily_atr14=daily_atr14,
    )

    if stop_plan["stop_price"] is None:
        # ATR unavailable -- stop_planner.py's own guard. Can't place a real
        # order without a real stop; NO_PLAN rather than guess.
        return {
            **base,
            "status": "NO_PLAN",
            "no_plan_reason": f"stop planner unavailable ({stop_plan['stop_basis']})",
        }

    rr = sp.rr_floor_ok(entry_price, stop_plan["stop_price"], t1, is_long=is_long)

    if not rr["ok"]:
        # SS6 point 5: a wide-enough core zone can push R:R below 1:1 even
        # on a gate-approved setup. Andy's own framing (ORDER_MECHANICS.md
        # SS6 conversation, 2026-08-31): "the stop planner's job is to say
        # the safe stop is too far for this target, so the hand isn't worth
        # playing, not to pretend the R:R is fine." The spec's own text
        # ("degrades to STANDARD tier (T1 only) or NO_PLAN") doesn't specify
        # which of the two applies when -- rather than invent an arbitrary
        # split the spec doesn't define, this always goes to NO_PLAN, matching
        # the doc's own stated philosophy. Flagged for review, not silently
        # picked to avoid the harder case.
        return {
            **base,
            "status": "NO_PLAN",
            "direction": side, "tier": tier,
            "no_plan_reason": (
                f"core-zone stop too wide for T1 -- R:R {rr['ratio']:.2f} "
                f"< 1:1 floor ({stop_plan['stop_basis']})"
            ),
        }

    return {
        **base,
        "status": "WAITING",
        "direction": side,
        "tier": tier,
        "entry_mode": None,  # decided at commit_after, when live price is known (SS2)
        "trigger_price": entry_price,
        "stop_price": stop_plan["stop_price"],
        "stop_basis": stop_plan["stop_basis"],
        "stop_dist_atr": stop_plan["stop_dist_atr"],
        "t1": t1, "t2": t2, "t3": t3,
        "rr_floor_ok": True,
        "rr_ratio": rr["ratio"],
        "last_transition_reason": "plan generated at lock",
    }


def render_brief(plan: Dict[str, Any]) -> str:
    """Renders the pre-commit brief (SS4) from a built plan dict (as
    returned by build_trade_plan(), or a TradePlan row's __dict__). Every
    number in the brief is copied from the plan object, never recomputed
    here -- SS4's own rule."""
    date_key = plan.get("date_key", "")
    symbol = plan.get("symbol", "")

    if plan.get("status") == "NO_PLAN":
        reason = plan.get("no_plan_reason") or "gate did not approve a setup"
        return (
            f"TRADE PLAN — {date_key} — {symbol} — STATUS: NO_PLAN\n\n"
            f"  NO PLAN TODAY — {reason}\n\n"
            f"  NO_PLAN is a valid, common outcome. This does not become a "
            f"plan later in the day."
        )

    direction = plan.get("direction")
    tier = plan.get("tier")
    trigger = plan.get("trigger_price")
    stop = plan.get("stop_price")
    stop_basis = plan.get("stop_basis")
    t1, t2, t3 = plan.get("t1"), plan.get("t2"), plan.get("t3")
    commit_after = plan.get("commit_after")
    commit_str = commit_after.strftime("%H:%M UTC") if isinstance(commit_after, datetime.datetime) else str(commit_after)
    verb = "BUY" if direction == "LONG" else "SELL"

    lines = [
        f"TRADE PLAN — {date_key} — {symbol} — STATUS: {plan.get('status')}",
        f"Tier: {tier} ({'runner management active' if tier == 'PREMIUM' else 'standard sizing'})",
        "",
        f"  WAIT UNTIL {commit_str} to commit (post-open-window rule — the "
        f"first ~1h after equity open degrades every trigger; commit_after "
        f"is already past it)",
        "",
        f"  ORDER 1 (trigger/stop-entry): {verb} {trigger:,.2f}",
        f"  STOP: {stop:,.2f} — {stop_basis}",
        f"  T1: {t1:,.2f} (take 30%, runner stop to the fixed level below)   "
        f"T2: {t2:,.2f}   T3: {t3:,.2f} (open runner)",
        "",
        f"  IF ALREADY BROKEN OUT AT COMMIT TIME: do not chase. Switch to "
        f"ORDER 2 — a limit at the line ({trigger:,.2f}); most breaks retest "
        f"the line before continuing.",
        "",
        f"  FUEL RULE: {plan.get('fuel_requirement')}. If unfueled at the "
        f"cross, the plan VETOES — stand down, wait for the retest.",
        "",
        f"  MANAGEMENT: {plan.get('management')}",
        "",
        "  ONE TRADE TODAY. Re-entry exists only under the fuel-gated "
        "wick-fake rule.",
    ]
    return "\n".join(lines)


# ==============================================================================
# INTRADAY STATE MACHINE (SS5) — pre-fill, fuel-gated entry logic
# ==============================================================================

def advance_waiting_plan(
    plan: Dict[str, Any],
    now_utc: datetime.datetime,
    session_expires_at: Optional[datetime.datetime],
    candles_5m: List[Dict[str, Any]],
    live_price: float,
) -> Optional[Dict[str, Any]]:
    """Pre-fill transitions ONLY: WAITING/VETOED -> FILLED/VETOED/DONE.

    Held until commit_after (the open-window rule) — SS1's "no plan
    generated intraday" rule doesn't mean no MONITORING before commit_after,
    it means the plan's fixed fields (trigger/stop/targets/tier) never
    change; whether/when it fires is exactly what this function decides.

    ARMED and FILLED collapse into one transition here: a stop/limit order
    sitting exactly at trigger_price fills the instant price touches it --
    this is advisory tracking (SS1 point 2, never real order placement),
    so there's no meaningful gap between "fuel confirmed + touched" and
    "filled" at candle-poll granularity.

    Returns a dict of field updates to apply to the TradePlan row, or None
    if nothing changed this poll. Caller (the monitoring loop) owns the DB
    read/write/commit -- this function is pure given its inputs, matching
    build_trade_plan()'s style and this codebase's small-single-purpose-
    module convention.
    """
    status = plan.get("status")
    if status not in ("WAITING", "VETOED"):
        return None

    commit_after = plan.get("commit_after")
    if commit_after and now_utc < commit_after:
        return None  # still in the open-window hold -- nothing to check yet

    if session_expires_at and now_utc >= session_expires_at and plan.get("cross_time") is None:
        return {"status": "DONE", "last_transition_reason": "session ended, trigger never crossed"}

    is_long = plan.get("direction") == "LONG"
    side = "LONG" if is_long else "SHORT"
    trigger = plan.get("trigger_price")

    fuel = fuel_gate.evaluate_fuel_gate(candles_5m, trigger, side)
    verdict = fuel.get("verdict")

    if verdict == "NO_PUSH":
        return None  # not touched yet

    updates: Dict[str, Any] = {"cross_time": now_utc, "fuel_at_cross": verdict}

    # entry_mode decided the first time price actually reaches the trigger
    # at/after commit_after -- SS2's "already broken out at commit time" rule.
    if plan.get("entry_mode") is None:
        already_broken_out = (live_price > trigger) if is_long else (live_price < trigger)
        updates["entry_mode"] = "RETEST_LIMIT_AT_LINE" if already_broken_out else "TRIGGER_AT_LEVEL"

    if verdict == "FUELED":
        updates["status"] = "FILLED"
        updates["fill_time"] = now_utc
        updates["fill_price"] = trigger
        push_ratio = (fuel.get("checks") or {}).get("push_volume", {}).get("ratio")
        prefix = "second " if status == "VETOED" else ""
        updates["last_transition_reason"] = f"{prefix}cross fueled ({push_ratio}x baseline) -- filled"
        return updates

    # NO_FUEL / CONFLICTED
    if status == "VETOED":
        # This is already the second cross (VETOED can only be reached after
        # exactly one unfueled cross -- no counter column needed).
        updates["status"] = "DONE"
        updates["last_transition_reason"] = f"second cross also unfueled ({verdict}) -- no energy, done for the day"
        return updates

    updates["status"] = "VETOED"
    updates["last_transition_reason"] = f"cross unfueled ({verdict}) -- waiting for the retest"
    return updates


def mirror_campaign_outcome(
    plan: Dict[str, Any],
    campaign_status: Optional[str],
    campaign_target_hit: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Once FILLED, TradePlan does not re-scan candles for T1/runner/T3 --
    that's CampaignLog's job (ledger_closing_engine.py, verified 2026-08-30
    with 6 regression tests). This mirrors that ALREADY-RESOLVED terminal
    outcome into TradePlan's own status instead of duplicating the scan.

    campaign_status/campaign_target_hit: the matching CampaignLog row's own
    .status/.target_hit for the same (symbol, date_key, session_id).
    """
    if plan.get("status") != "FILLED":
        return None
    if campaign_status not in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"):
        return None  # still open, nothing to mirror yet

    if campaign_status == "CLOSED_LOSS" and campaign_target_hit == "STOP":
        # Stopped BEFORE ever reaching T1 -- the wick-fake scenario SS8 is
        # about. Re-entry eligibility (a separate fuel check) is decided by
        # check_reentry_eligibility(), not here.
        return {
            "status": "STOPPED",
            "stopped_time": datetime.datetime.utcnow(),
            "last_transition_reason": "stopped before T1 (wick-fake candidate)",
        }

    # T1 was reached (RUNNER_STOP/T3), or CLOSED_AT_EXPIRY at any point --
    # the management sequence ran its course (or the session ended without
    # ever getting a clean stop-before-T1). Whatever the final blended R
    # was, the plan itself is done; it is not a re-entry candidate.
    return {
        "status": "DONE",
        "last_transition_reason": f"management complete ({campaign_status}/{campaign_target_hit})",
    }


def check_reentry_eligibility(plan: Dict[str, Any], fuel_still_fueled: bool) -> Dict[str, Any]:
    """SS8: after a STOPPED (wick-fake) outcome, one re-entry is allowed IF
    the fuel gate still reads FUELED.

    OPEN TENSION (flagged, not silently resolved -- 2026-08-31): SS8 also
    says "if the wide stop from SS6 is available (R:R still >= 1:1), the
    re-entry path is not used -- the wide stop already dominates it." But
    build_trade_plan() never lets a plan reach WAITING/FILLED without
    R:R >= 1:1 in the first place (a failing floor goes straight to
    NO_PLAN -- Andy's confirmed call, 2026-08-31, see build_trade_plan()'s
    own comment). That means the wide stop is ALWAYS "available" for any
    plan that reaches STOPPED, which by SS8's own literal text would mean
    re-entry should never actually fire in this system. This function
    implements the fuel-check mechanically as specified; whether
    REENTRY_ARMED should ever actually be reachable given the above is a
    real, unresolved design question worth confirming before it shows on
    a live plan, not something to guess past.
    """
    if plan.get("status") != "STOPPED":
        return {"status": "DONE", "last_transition_reason": "not eligible for re-entry check"}
    if plan.get("reentry_used"):
        return {"status": "DONE", "last_transition_reason": "re-entry already used"}
    if fuel_still_fueled:
        return {"status": "REENTRY_ARMED", "last_transition_reason": "fuel still FUELED -- one re-entry armed"}
    return {"status": "DONE", "last_transition_reason": "fuel not FUELED after stop -- no re-entry, done"}
