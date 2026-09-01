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
#
# CORRECTION (2026-08-31, same day): the STOPPED (wick-fake) determination
# CANNOT come from CampaignLog's terminal status -- an earlier draft of
# this file did exactly that (CampaignLog CLOSED_LOSS/STOP -> TradePlan
# STOPPED) and it was a real bug, caught before ever reaching the caller
# wiring. CampaignLog.stop_loss is the r30-based, UNCHANGED risk-basis
# stop (tighter, usually). TradePlan.stop_price is stop_planner.py's
# separate, wider, additive execution stop (see the module comment
# above) -- CampaignLog can stop out on its own tighter level while
# TradePlan's wider stop was never even touched, and that is NOT a
# wick-fake of TradePlan's own plan. So whether TradePlan's wide stop was
# hit before T1 is answered by check_wide_stop_or_t1() below, which scans
# TradePlan's own stop_price/t1 directly; mirror_campaign_outcome() now
# only ever produces DONE (it can no longer produce STOPPED).
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


def anticipate_setup(
    breakout_trigger: float,
    breakdown_trigger: float,
    daily_atr14: float,
    candles_15m: List[Dict[str, Any]],
    candles_1d: List[Dict[str, Any]],
    candles_1h: List[Dict[str, Any]],
    candles_4h: List[Dict[str, Any]],
    session_hour_utc: Optional[int],
) -> Dict[str, Any]:
    """The real fix for the 2026-08-31 "WAITING-state plan visibility gap"
    Andy found on the live site (Kabroda AI Brain AGENT_LOG.md): the plan
    was only ever generated once decision_engine.evaluate_15m_decision()
    already returns a TAKE state -- which requires `side` to already be
    known, which requires the trigger to have ALREADY crossed. That's
    backwards from SS4's own example brief (full levels shown at WAITING,
    before any cross) and the real order mechanics DeepSeek documented:
    Andy rests a trigger order AT THE LEVEL before the cross -- only the
    TIER stamp waits for the fuel check at the cross ("cross mechanics
    clarified for Andy", same log).

    This answers the ONE question decision_engine.py's real gate genuinely
    can't answer pre-cross: which direction to anticipate. Everything else
    the gate checks (reachability, daily regime/counter-trend, 15m DEAD
    tape, HTF trend, session hour) is already knowable at lock -- none of
    it depends on price having crossed a trigger, only FUEL does (push
    volume needs the actual crossing candle, unavailable pre-cross). So
    this reuses decision_engine.py's own collaborator modules directly
    (reachability.py, market_regime.py, micro_regime.py, htf_fuel.py --
    never reimplements their logic) to run everything except the fuel
    check, picking the trend-aligned side using the SAME logic the gate's
    own counter-trend veto already encodes (a break against a GOOD-quality
    daily bias gets vetoed anyway, so the aligned side is the only one
    that could pass).

    Returns {"viable": False, "reason": str} (-> NO_PLAN) or
    {"viable": True, "side": "LONG"|"SHORT", "reason": str} (-> WAITING;
    tier is stamped later, at the real cross, by advance_waiting_plan()).

    A genuinely undetermined direction (no daily bias, no HTF lean) is NOT
    guessed at -- it returns viable=False, deferring to the ORIGINAL
    cross-based path (unchanged, still correct): the plan stays NO_PLAN
    until an actual cross gives decision_engine.py's real gate a side to
    evaluate, same as today's behavior for every case, not just this one.
    """
    import decision_engine as _decision_engine
    import htf_fuel as _htf_fuel
    import market_regime as _market_regime
    import micro_regime as _micro_regime
    import reachability as _reachability

    bo, bd = float(breakout_trigger or 0), float(breakdown_trigger or 0)
    atr = float(daily_atr14 or 0)
    box = (bo - bd) if (bo and bd and bo > bd) else 0.0

    reach = _reachability.reachability(box, atr)
    if not reach["ok"]:
        return {"viable": False, "reason": reach["note"]}

    if session_hour_utc is not None and session_hour_utc in _decision_engine.DEAD_HOURS:
        return {"viable": False, "reason": f"{session_hour_utc:02d}:00 UTC is a dead-tape hour"}

    micro = _micro_regime.classify_regime(candles_15m)
    if micro.get("regime") == "DEAD":
        return {"viable": False, "reason": "15M regime is DEAD -- no participation"}

    daily = _market_regime.classify_market_regime(candles_1d)
    daily_bias = (daily.get("policy") or {}).get("bias")
    daily_quality = daily.get("quality")

    # trend_1h/trend_4h are side-independent raw reads -- the `side` arg
    # htf_fuel() takes only affects the aligned/opposed COUNT it also
    # returns, which is recomputed here for both hypothetical sides
    # instead (no need to call it twice).
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, "LONG")
    trend_1h, trend_4h = htf.get("trend_1h"), htf.get("trend_4h")
    long_aligned = sum(1 for t in (trend_1h, trend_4h) if t == "BULLISH")
    short_aligned = sum(1 for t in (trend_1h, trend_4h) if t == "BEARISH")

    side: Optional[str] = None
    reason: Optional[str] = None
    if daily_quality == "GOOD" and daily_bias in ("UP", "DOWN"):
        side = "LONG" if daily_bias == "UP" else "SHORT"
        reason = f"anticipating {side} -- aligned with a {daily_bias} daily trend on a GOOD table"
    elif long_aligned > short_aligned:
        side, reason = "LONG", f"anticipating LONG -- {long_aligned}/2 HTF timeframes bullish"
    elif short_aligned > long_aligned:
        side, reason = "SHORT", f"anticipating SHORT -- {short_aligned}/2 HTF timeframes bearish"

    if side is None:
        return {"viable": False, "reason": "no clear directional bias yet -- awaiting a cross to determine side"}
    return {"viable": True, "side": side, "reason": reason}


def _build_waiting_plan(
    base: Dict[str, Any],
    side: str,
    entry_price: float,
    t1: float,
    t2: float,
    t3: float,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
    daily_atr14: float,
    candles_24h: list,
    tier: Optional[str],
    generation_reason: str,
) -> Dict[str, Any]:
    """Shared by both build_trade_plan() paths (an already-crossed TAKE
    decision, and the pre-cross anticipate_setup() path) -- the stop-
    planning/R:R-floor logic is identical either way; only the source of
    side/entry/t1/t2/t3/tier differs."""
    is_long = side == "LONG"
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
        "last_transition_reason": generation_reason,
    }


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
    breakout_trigger: Optional[float] = None,
    breakdown_trigger: Optional[float] = None,
    candles_15m: Optional[list] = None,
    candles_1d: Optional[list] = None,
    candles_1h: Optional[list] = None,
    candles_4h: Optional[list] = None,
    session_hour_utc: Optional[int] = None,
) -> Dict[str, Any]:
    """Builds the TradePlan fields (a dict, ready for the TradePlan model)
    from an already-computed gate decision (decision_engine.evaluate_15m_
    decision()'s decision_dict) plus the inputs stop_planner.py needs.

    The new optional params (breakout_trigger/breakdown_trigger/candles_15m/
    candles_1d/candles_1h/candles_4h/session_hour_utc) feed
    anticipate_setup() for the PRE-cross case (decision_dict["side"] is
    None, verdict_state PASS -- see that function's docstring). Omitting
    them falls back to the original NO_PLAN-until-a-real-cross behavior
    rather than crashing, for any caller that hasn't been updated.

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

    if decision_dict.get("side") is None and state == "PASS":
        have_precross_inputs = (
            breakout_trigger and breakdown_trigger
            and candles_15m is not None and candles_1d is not None
        )
        if have_precross_inputs:
            import decision_engine as _decision_engine
            anticipated = anticipate_setup(
                breakout_trigger, breakdown_trigger, daily_atr14,
                candles_15m, candles_1d, candles_1h or [], candles_4h or [],
                session_hour_utc,
            )
            if not anticipated["viable"]:
                return {**base, "status": "NO_PLAN", "no_plan_reason": anticipated["reason"]}
            plan = _decision_engine._plan_for_side(
                anticipated["side"], breakout_trigger, breakdown_trigger, r30_high, r30_low,
            )
            return _build_waiting_plan(
                base, anticipated["side"], plan["entry"], plan["t1"], plan["t2"], plan["t3"],
                r30_high, r30_low, f24_vah, f24_val, daily_atr14, candles_24h,
                tier=None,  # stamped at the real cross -- advance_waiting_plan()
                generation_reason=anticipated["reason"],
            )
        # No pre-cross inputs supplied -- original behavior, unchanged.
        return {
            **base,
            "status": "NO_PLAN",
            "no_plan_reason": decision_dict.get("tactical_brief") or f"gate state: {state}",
        }

    if state not in _TAKE_STATES:
        # A cross ALREADY happened and the real gate said no -- its reason
        # is authoritative (fuel/vetoes already evaluated for real), never
        # overridden by the pre-cross heuristic above.
        return {
            **base,
            "status": "NO_PLAN",
            "no_plan_reason": decision_dict.get("tactical_brief") or f"gate state: {state}",
        }

    side = decision_dict.get("side")
    entry_price = float(decision_dict["entry_price"])
    t1 = float(decision_dict["t1"])
    t2 = float(decision_dict["t2"])
    t3 = float(decision_dict["t3"])
    tier = decision_dict.get("tier")

    return _build_waiting_plan(
        base, side, entry_price, t1, t2, t3, r30_high, r30_low, f24_vah, f24_val,
        daily_atr14, candles_24h, tier, generation_reason="plan generated at lock",
    )


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

    if tier is None:
        tier_line = "Tier: TBD — stamped at the cross once the fuel check confirms (size, not the entry itself)"
    else:
        tier_line = f"Tier: {tier} ({'runner management active' if tier == 'PREMIUM' else 'standard sizing'})"

    lines = [
        f"TRADE PLAN — {date_key} — {symbol} — STATUS: {plan.get('status')}",
        tier_line,
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

def _stamp_tier_at_cross(
    plan: Dict[str, Any],
    candles_1h: List[Dict[str, Any]],
    candles_4h: List[Dict[str, Any]],
    daily_atr14: float,
) -> str:
    """PREMIUM if both HTF timeframes back the side AND box/ATR <= 0.40 at
    the cross, else STANDARD -- decision_engine.py's own _core_gate() tier
    formula, recomputed here (not reimplemented differently), for a plan
    whose tier was left None at generation (the anticipate_setup() pre-
    cross path -- see build_trade_plan()'s docstring) because it genuinely
    couldn't be known until now.
    """
    import htf_fuel as _htf_fuel
    import reachability as _reachability

    side = plan.get("direction")
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
    aligned = htf.get("aligned") or 0

    trigger, t2 = plan.get("trigger_price"), plan.get("t2")
    box = abs(t2 - trigger) if (trigger is not None and t2 is not None) else 0.0
    reach = _reachability.reachability(box, daily_atr14)
    ratio = reach.get("ratio")

    premium = aligned == 2 and ratio is not None and ratio <= _reachability.PREMIUM_BOX_ATR
    return "PREMIUM" if premium else "STANDARD"


def advance_waiting_plan(
    plan: Dict[str, Any],
    now_utc: datetime.datetime,
    session_expires_at: Optional[datetime.datetime],
    candles_5m: List[Dict[str, Any]],
    live_price: float,
    candles_1h: Optional[List[Dict[str, Any]]] = None,
    candles_4h: Optional[List[Dict[str, Any]]] = None,
    daily_atr14: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Pre-fill transitions ONLY: WAITING/VETOED -> FILLED/VETOED/DONE.

    Held until commit_after (the open-window rule) — SS1's "no plan
    generated intraday" rule doesn't mean no MONITORING before commit_after,
    it means the plan's fixed fields (trigger/stop/targets/tier) never
    change; whether/when it fires is exactly what this function decides.

    candles_1h/candles_4h/daily_atr14 (all optional) feed _stamp_tier_at_
    cross() -- ONLY used, and only needed, when plan["tier"] is still None
    at the FUELED fill (the anticipate_setup() pre-cross generation path
    defers tier to the cross on purpose). A plan generated with a tier
    already known (the original, already-crossed TAKE path) is left
    untouched -- this never re-decides an existing tier.

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
        # P0 FIX (2026-09-01, confirmed live -- Kabroda AI Brain repo
        # AGENT_LOG.md "CONFIRMED P0: state machine missed a live cross"):
        # NO_PUSH on the anticipated side does NOT mean nothing happened --
        # anticipate_setup() picks ONE direction at lock (e.g. trend-
        # aligned with a GOOD daily table), but price can break the
        # OPPOSITE trigger instead (a genuine counter-trend move --
        # decision_engine.py's own counter-trend veto treats this as a
        # real, expected scenario, not noise). Without this check the
        # plan sat WAITING forever while price moved 200+ points through
        # the other trigger with zero detection, no email, nothing.
        # box is derivable from already-known fields (t2 = trigger +/-
        # 1.0*box) -- no new field needed to reconstruct the untaken side.
        trigger_price, t2 = plan.get("trigger_price"), plan.get("t2")
        if trigger_price is not None and t2 is not None:
            box = abs(t2 - trigger_price)
            opposite_trigger = trigger_price - box if is_long else trigger_price + box
            opposite_side = "SHORT" if is_long else "LONG"
            opposite_beyond = (live_price < opposite_trigger) if is_long else (live_price > opposite_trigger)
            if opposite_beyond:
                return {
                    "status": "DONE",
                    "last_transition_reason": (
                        f"price broke the OPPOSITE trigger ({opposite_trigger:,.2f}) -- "
                        f"counter to the anticipated {side}; this plan only covers "
                        f"{side}, no plan exists for the {opposite_side} side today"
                    ),
                }
        return None  # not touched on either side yet

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
        # faked_first (SS9a): did the FIRST cross wick back before
        # acceptance? True only when this fill is the retest after an
        # earlier unfueled cross (status was already VETOED coming in) --
        # a direct first-cross fill is a clean acceptance, not a fake.
        updates["faked_first"] = (status == "VETOED")
        push_ratio = (fuel.get("checks") or {}).get("push_volume", {}).get("ratio")
        prefix = "second " if status == "VETOED" else ""
        updates["last_transition_reason"] = f"{prefix}cross fueled ({push_ratio}x baseline) -- filled"
        if plan.get("tier") is None and candles_1h is not None and candles_4h is not None and daily_atr14:
            updates["tier"] = _stamp_tier_at_cross(plan, candles_1h, candles_4h, daily_atr14)
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


def check_wide_stop_or_t1(
    plan: Dict[str, Any],
    candles_since_fill: List[Dict[str, Any]],
) -> Optional[str]:
    """Post-fill: did TradePlan's OWN wide stop (stop_price, stop_planner.py's
    core-zone execution stop) get touched before T1?

    Deliberately separate from mirror_campaign_outcome() below -- see the
    module-header CORRECTION note. CampaignLog tracks a DIFFERENT, tighter
    stop (r30-based stop_loss, unchanged, the system's R-multiple risk
    basis), so its terminal status cannot answer whether TradePlan's own
    wider stop got wicked; only a direct scan of stop_price/t1 can.

    candles_since_fill: 1m candles from fill_time forward, chronological,
    using the same {"l","h","ts"} shape ledger_closing_engine.py's own
    scan already uses (so a caller can share one _fetch_1m_since() result
    instead of fetching twice).

    Returns "WIDE_STOP_FIRST", "T1_FIRST", "NEITHER_YET", or None if the
    plan isn't FILLED or is missing stop_price/t1.
    """
    if plan.get("status") != "FILLED":
        return None
    stop = plan.get("stop_price")
    t1 = plan.get("t1")
    if stop is None or t1 is None:
        return None
    is_long = plan.get("direction") == "LONG"

    for candle in candles_since_fill:
        hit_stop = candle["l"] <= stop if is_long else candle["h"] >= stop
        hit_t1 = candle["h"] >= t1 if is_long else candle["l"] <= t1
        if hit_stop:
            # Stop-first on same-candle ambiguity (conservative) -- matches
            # ledger_closing_engine.py's own documented convention exactly.
            return "WIDE_STOP_FIRST"
        if hit_t1:
            return "T1_FIRST"
    return "NEITHER_YET"


def mirror_campaign_outcome(
    plan: Dict[str, Any],
    campaign_status: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Once FILLED, and provided check_wide_stop_or_t1() has NOT already
    fired WIDE_STOP_FIRST (that -- and only that -- is what can move a
    plan to STOPPED), TradePlan does not re-scan candles for the rest of
    T1/runner/T3 -- that's CampaignLog's job (ledger_closing_engine.py,
    verified 2026-08-30 with 6 regression tests). This just mirrors that
    ALREADY-RESOLVED terminal outcome into a plain DONE once the
    underlying trade actually closes, whatever the real result was (a
    win, CampaignLog's own tighter-stop loss, or a session expiry) --
    TradePlan's own re-entry question was already answered, or ruled out,
    by the wide-stop check, not by this.

    campaign_status: the matching CampaignLog row's own .status for the
    same (symbol, date_key, session_id).

    Never applies to a re-entry fill (plan["reentry_used"] is True) --
    CampaignLog tracks only the ORIGINAL fill and has no re-entry concept
    at all, so by the time a re-entry even becomes possible (the original
    fill already had to stop out on its OWN, tighter r30 stop first),
    CampaignLog is almost always already terminal from that unrelated
    event. Mirroring it here would silently close the re-entry out using
    a stale verdict -- a real bug, caught before any live re-entry ever
    exercised this path. resolve_reentry_fill() is the re-entry
    counterpart.
    """
    if plan.get("status") != "FILLED":
        return None
    if plan.get("reentry_used"):
        return None
    if campaign_status not in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"):
        return None  # still open, nothing to mirror yet

    return {
        "status": "DONE",
        "last_transition_reason": f"management complete ({campaign_status})",
    }


def resolve_reentry_fill(
    plan: Dict[str, Any],
    wide_stop_verdict: Optional[str],
    now_utc: datetime.datetime,
    session_expires_at: Optional[datetime.datetime],
) -> Optional[Dict[str, Any]]:
    """T1_FIRST / NEITHER_YET resolution for a re-entry-sourced FILLED plan
    (plan["reentry_used"] is True) -- mirror_campaign_outcome() refuses
    these (see its own docstring), so they need a distinct resolution.

    WIDE_STOP_FIRST is deliberately NOT handled here -- the caller routes
    that to the same STOPPED transition every FILLED plan gets;
    check_reentry_eligibility()'s own reentry_used guard already finalizes
    a second stop-out to DONE on the very next poll ("one attempt max"),
    so no separate path is needed for it in this function.
    """
    if plan.get("status") != "FILLED" or not plan.get("reentry_used"):
        return None
    if wide_stop_verdict == "T1_FIRST":
        return {
            "status": "DONE",
            "last_transition_reason": (
                "re-entry reached T1 -- full runner/T3 outcome isn't "
                "tracked for re-entry fills (documented gap, not guessed)"
            ),
        }
    if session_expires_at and now_utc >= session_expires_at:
        return {"status": "DONE", "last_transition_reason": "session ended, re-entry outcome unresolved"}
    return None


def check_reentry_eligibility(plan: Dict[str, Any], fuel_still_fueled: bool) -> Dict[str, Any]:
    """SS8: after a STOPPED (wick-fake) outcome, one re-entry is allowed IF
    the fuel gate still reads FUELED.

    RESOLVED (2026-08-31, DeepSeek/Andy, AGENT_LOG.md): SS8's "if the wide
    stop is available, re-entry is not used" means SURVIVED THE DAY, not
    existed at plan time -- every FILLED plan had an R:R-valid wide stop,
    but that stop can still be wicked through (measured: 1/39 gate-
    approved fake sessions hit the 24h core-zone stop within 2h). STOPPED
    is exactly that event now that check_wide_stop_or_t1() derives it from
    TradePlan's own stop_price (not CampaignLog's tighter r30 stop -- see
    that function's docstring), so REENTRY_ARMED is genuinely reachable
    here, not dead code.
    """
    if plan.get("status") != "STOPPED":
        return {"status": "DONE", "last_transition_reason": "not eligible for re-entry check"}
    if plan.get("reentry_used"):
        return {"status": "DONE", "last_transition_reason": "re-entry already used"}
    if fuel_still_fueled:
        return {"status": "REENTRY_ARMED", "last_transition_reason": "fuel still FUELED -- one re-entry armed"}
    return {"status": "DONE", "last_transition_reason": "fuel not FUELED after stop -- no re-entry, done"}


def advance_reentry_plan(
    plan: Dict[str, Any],
    now_utc: datetime.datetime,
    session_expires_at: Optional[datetime.datetime],
    candles_5m: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """SS8: the one re-entry attempt itself, once check_reentry_eligibility()
    has set REENTRY_ARMED. Deliberately NOT advance_waiting_plan() reused --
    re-entry has no commit_after gate (the open-window rule already applied
    hours earlier, to the original plan) and no VETOED-retry loop ("one
    attempt max," SS8's own words): an unfueled re-entry cross goes
    straight to DONE, it does not arm a second retest watch.
    """
    if plan.get("status") != "REENTRY_ARMED":
        return None
    if session_expires_at and now_utc >= session_expires_at:
        return {"status": "DONE", "reentry_used": True,
                "last_transition_reason": "session ended, re-entry window closed"}

    side = "LONG" if plan.get("direction") == "LONG" else "SHORT"
    trigger = plan.get("trigger_price")
    fuel = fuel_gate.evaluate_fuel_gate(candles_5m, trigger, side)
    verdict = fuel.get("verdict")

    if verdict == "NO_PUSH":
        return None  # not touched yet

    if verdict == "FUELED":
        return {
            "status": "FILLED",
            "reentry_used": True,
            "reentry_cross_time": now_utc,
            "reentry_fill_price": trigger,
            "fill_time": now_utc,
            "fill_price": trigger,
            "cross_time": now_utc,
            "fuel_at_cross": verdict,
            "last_transition_reason": "re-entry cross fueled -- filled (one attempt used)",
        }

    return {
        "status": "DONE",
        "reentry_used": True,
        "last_transition_reason": f"re-entry cross unfueled ({verdict}) -- one attempt used, done",
    }
