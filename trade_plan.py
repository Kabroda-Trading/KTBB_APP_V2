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
# management text: the AUDITED rule (2026-09-07 correction -- see
# MANAGEMENT_TEXT's own comment below): 50% off at T1, stop stays
# original, 50% runner to T3; PREMIUM only moves the stop to breakeven,
# mechanically, at T2. This is what executor_live_engine.py (Domain 2)
# implements for real money -- ledger_closing_engine.py's OLDER 30/70
# rule is deprecated, not the reference anymore. Do not let this text
# drift from executor_live_engine.py's real behavior without the same
# check that caught this drift in the first place.
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
from typing import Any, Dict, List, Optional, Tuple

import fuel_gate

import stop_planner as sp

FUEL_REQUIREMENT_TEXT = (
    "push must read FUELED or CONFLICTED at the cross (median-based push-"
    "volume ratio >= 0.8x prior-24h baseline, or a real-but-conflicted "
    "push) -- FUELED earns PREMIUM sizing if HTF/box also qualify, "
    "CONFLICTED still fills as a real STANDARD trade. NO_FUEL (a ghost "
    "push, no real volume) is the only fuel-based veto."
)

# 2026-09-07 (Domain 2 build, DeepSeek's live-email review, Kabroda AI
# Brain repo AGENT_LOG.md 09:30/09:50 CT): CORRECTED. This constant used
# to describe the pre-audit 30%-at-T1/70%-runner rule ("not tier-
# dependent, not stop-to-breakeven") -- stale the moment GATE_REBUILD_
# SPEC.md/CLEAN_REPORT.md validated the real, tier-differentiated 50/50
# rule that executor_live_engine.py now implements for real money. Do
# not let this drift from that rule again -- it's the one thing every
# component (executor, this text, the old ledger_closing_engine.py which
# is now itself marked deprecated) must agree on.
MANAGEMENT_TEXT = (
    "50% off at T1, stop stays at the original level, 50% rides toward "
    "T3. PREMIUM only: at T2, the stop moves to breakeven (mechanical, "
    "unconditional -- not a judgment call). STANDARD: the stop never "
    "moves before T3 or the original stop."
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
    box_atr_ratio = reach.get("ratio")
    if not reach["ok"]:
        # WIDE_BOX is the one lock-time no-plan reason that is truly final:
        # box and ATR are both frozen at lock, so no later cross can ever
        # pass the gate's reachability check. Every other category below can
        # still resolve to a real trade intraday.
        return {"viable": False, "reason": reach["note"],
                "category": "WIDE_BOX", "box_atr_ratio": box_atr_ratio}

    if session_hour_utc is not None and session_hour_utc in _decision_engine.DEAD_HOURS:
        return {"viable": False, "reason": f"{session_hour_utc:02d}:00 UTC is a dead-tape hour",
                "category": "DEAD_HOUR", "box_atr_ratio": box_atr_ratio}

    micro = _micro_regime.classify_regime(candles_15m)
    if micro.get("regime") == "DEAD":
        return {"viable": False, "reason": "15M regime is DEAD -- no participation",
                "category": "DEAD_TAPE", "box_atr_ratio": box_atr_ratio}

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
        return {"viable": False, "reason": "no clear directional bias yet -- awaiting a cross to determine side",
                "category": "NO_DIRECTION", "box_atr_ratio": box_atr_ratio}

    # htf_backs_side: does at least one of {1H, 4H} agree with the anticipated
    # direction? A side picked purely off a GOOD daily table can have neither
    # -- and the gate's aligned>=1 cut (shipped 2026-09-10) means that plan
    # can only fill if HTF turns by the real cross. The lock email says so
    # honestly ("watching one side, needs HTF") rather than a confident "PLAN".
    want = "BULLISH" if side == "LONG" else "BEARISH"
    htf_backs_side = (trend_1h == want) or (trend_4h == want)
    return {"viable": True, "side": side, "reason": reason,
            "category": "VIABLE", "box_atr_ratio": box_atr_ratio,
            "htf_backs_side": htf_backs_side}


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
    side/entry/t1/t2/t3/tier differs.

    2026-09-08 TIER-SPECIFIC STOP (Andy's explicit, informed decision --
    Kabroda AI Brain repo AGENT_LOG.md, same date -- to implement this
    directly rather than wait for the out-of-sample check and DeepSeek's
    independent review Claude Code recommended first; see that log entry
    for the full context and the honest caveat on what has NOT yet been
    verified). Backtest finding (brain/calibration/tier_specific_stop_
    variants.py, full 2021-2026 corpus, 840 trades): PREMIUM performs
    better on the tight, 24h-core-zone stop (its setups are already the
    cleanest/highest-conviction -- no need for extra room); STANDARD
    performs better on the wider r30-based stop (its setups are lower-
    conviction/fuel-conflicted, and the extra room lets more of them
    survive to actually develop instead of getting shaken out by normal
    noise -- T1-reach rate 56.5% -> 62.0% in the same backtest). Combined:
    +185.2R vs +168.4R for either stop applied uniformly to both tiers,
    positive in 5 of 6 years tested.

    STANDARD now uses decision_engine.py's own r30-based formula (r30 -+
    STOP_BUFFER_BOX*box) as its REAL execution stop -- previously this
    formula was ONLY the risk-bookkeeping stop (never sent to the
    exchange, see TRADE_RULES_AUDIT.md section 4's original "two stops"
    explanation, now tier-dependent for the execution stop specifically).
    PREMIUM is completely unchanged -- still the 24h-core-zone stop,
    identical code path to before this change.

    The r30-based candidate is computed and stored (stop_price_r30) on
    EVERY plan, tier known or not -- when tier isn't known yet (the pre-
    cross anticipate_setup() path), advance_waiting_plan() reads this
    field back and swaps it in if the real cross confirms STANDARD (see
    that function's own comment for the R:R re-check this requires)."""
    import decision_engine as _decision_engine
    is_long = side == "LONG"
    box = abs(t2 - entry_price)
    r30_stop_raw = (r30_low - _decision_engine.STOP_BUFFER_BOX * box) if is_long \
        else (r30_high + _decision_engine.STOP_BUFFER_BOX * box)
    stop_price_r30 = round(float(r30_stop_raw), 2)

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

    if tier == "STANDARD":
        active_stop_price = stop_price_r30
        active_stop_basis = f"r30 edge {'-' if is_long else '+'} {_decision_engine.STOP_BUFFER_BOX:.3f}xbox (STANDARD tier's own execution stop, not just the R-bookkeeping basis)"
        active_stop_dist_atr = round(abs(entry_price - stop_price_r30) / daily_atr14, 4) if daily_atr14 else None
    else:
        # PREMIUM or tier not yet known (pre-cross) -- completely unchanged
        # from before this change: the 24h-core-zone stop.
        active_stop_price = stop_plan["stop_price"]
        active_stop_basis = stop_plan["stop_basis"]
        active_stop_dist_atr = stop_plan["stop_dist_atr"]

    rr = sp.rr_floor_ok(entry_price, active_stop_price, t1, is_long=is_long)

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
            "stop_price_r30": stop_price_r30,
            "no_plan_reason": (
                f"{'r30' if tier == 'STANDARD' else 'core-zone'} stop too wide for T1 -- R:R {rr['ratio']:.2f} "
                f"< 1:1 floor ({active_stop_basis})"
            ),
        }

    waiting = {
        **base,
        "status": "WAITING",
        "direction": side,
        "tier": tier,
        "entry_mode": None,  # decided at commit_after, when live price is known (SS2)
        "trigger_price": entry_price,
        "stop_price": active_stop_price,
        "stop_basis": active_stop_basis,
        "stop_dist_atr": active_stop_dist_atr,
        "stop_price_r30": stop_price_r30,
        "t1": t1, "t2": t2, "t3": t3,
        "rr_floor_ok": True,
        "rr_ratio": rr["ratio"],
        "last_transition_reason": generation_reason,
    }
    # Persist the disposition headline as the transition reason so the radar's
    # Trade Plan panel (which renders last_transition_reason) shows the SAME
    # text the lock email does -- the transient category/ratio fields don't
    # survive to the DB, but this string does.
    _disp = lock_disposition(waiting)
    if _disp:
        waiting["last_transition_reason"] = _disp["headline"]
    return waiting


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
        # Transient (NOT TradePlan columns -- dropped by _inject_trade_plan_
        # to_database()'s valid_cols filter before the DB write). Carried
        # through every return path, including NO_PLAN, so the lock-time
        # email (trade_plan_notify.py) can show "the structure being
        # watched" even on a no-trade morning -- Andy's confirmed UX
        # (Kabroda AI Brain AGENT_LOG.md, 2026-09-02 12:00 CT), the gap
        # behind the day-4 email-delivery incident (same file, 11:00 CT).
        "breakout_trigger": breakout_trigger,
        "breakdown_trigger": breakdown_trigger,
        "r30_high": r30_high,
        "r30_low": r30_low,
        # 2026-09-06 (DeepSeek's queued ask, Kabroda AI Brain repo
        # AGENT_LOG.md, 12:45 CT): also transient, not TradePlan columns
        # -- carries the gate's own already-computed alignment reads
        # through to render_brief() for the email headline (see
        # classify_alignment() below). Same real fields GateLog stores
        # (decision_dict["fuel_verdict"]/["htf_aligned"]), nothing new
        # computed here, purely informational -- does not affect sizing
        # or the gate itself.
        "fuel_verdict": decision_dict.get("fuel_verdict"),
        "htf_aligned": decision_dict.get("htf_aligned"),
        "trend_1h": decision_dict.get("trend_1h"),
        "trend_4h": decision_dict.get("trend_4h"),
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
            # Transient (not TradePlan columns) -- carried so the lock email,
            # the radar Trade Plan panel, and render_brief() all render the
            # SAME disposition text via lock_disposition() below.
            base["no_plan_category"] = anticipated.get("category")
            base["box_atr_ratio"] = anticipated.get("box_atr_ratio")
            base["htf_backs_side_at_lock"] = anticipated.get("htf_backs_side")
            if not anticipated["viable"]:
                np = {**base, "status": "NO_PLAN", "no_plan_reason": anticipated["reason"]}
                _disp = lock_disposition(np)
                if _disp:
                    np["last_transition_reason"] = _disp["headline"]
                return np
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


def advance_no_plan(
    decision_dict: Dict[str, Any],
    candles_24h: list,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
    daily_atr14: float,
    now_utc: datetime.datetime,
) -> Optional[Dict[str, Any]]:
    """Andy's 2026-09-02 decision (Kabroda AI Brain repo AGENT_LOG.md,
    "poll-routing decision", finalized 15:45/15:50 CT after the exact
    contract was worked out with DeepSeek): a NO_PLAN morning is no longer
    permanently final for the rest of the session. trade_plan_engine.py
    now polls NO_PLAN rows too, re-running the REAL, full decision_engine.py
    gate on every poll (not the pre-cross anticipate_setup() heuristic
    build_trade_plan() used at lock -- this is decision_engine.evaluate_
    15m_decision() itself, same as the already-live opposite-break/own-
    cross paths, including the counter-trend veto). The agreed contract,
    verbatim: "a later clean 5m-close cross through either trigger re-runs
    the FULL gate ... pass -> ARMED + email at that moment; fail -> VETOED
    + email with reason; no cross -> silence; first cross defines the
    session side; after a vetoed re-run the session is DONE (no repeated
    attempts)". Three real outcomes, not two -- an earlier draft of this
    function only handled the first and silently did nothing for the
    third, which would have violated the agreed contract by leaving Andy
    with no email on a real, resolved counter-trend veto. Caught and fixed
    before this ever ran live.

    - No cross yet (`side` is None -- price still inside the box): returns
      None, silently. Matches every other "still waiting" poll outcome in
      this module.
    - A real TAKE: promotes straight to FILLED -- the SAME FUELED-
      collapses-ARMED+FILLED convention advance_waiting_plan() already
      uses for the anticipated-side path, since a TAKE verdict already
      implies fuel=FUELED by construction. Returns None instead (stay
      NO_PLAN, keep watching) if a stop can't be safely planned (ATR
      unavailable / R:R floor fails) -- same NO_PLAN-preserving philosophy
      build_trade_plan() itself uses: never guess a stop to force a
      promotion, a bad stop this poll doesn't mean it stays bad next poll.
    - A real fail (`side` is set, state is PASS -- either a hard veto or
      multiple soft misses): resolves to DONE, carrying `vetoed_cross_
      side`/`vetoed_cross_trigger` so trade_plan_notify.py's build_done_
      email() can render this as the VETOED call it actually is (see that
      function), not a bare "nothing happened" DONE. "No repeated
      attempts" per the contract -- unlike the anticipated-side WAITING->
      VETOED path (which held a resting order and gets one retest), a
      NO_PLAN promotion never had a resting order, so there's no retest to
      wait for; one real cross resolves the session outright.
    - A real TAKE whose push is below PROMOTED_PUSH_FLOOR (2026-09-10,
      Andy-approved): resolves to DONE, same VETOED framing. A NO_PLAN
      morning had no anticipated direction at lock -- the 5-year forensic
      (Kabroda AI Brain repo, anticipate_replay.py) showed those promotions
      are only a real edge on a genuinely strong push (>= 1.8x baseline);
      below that they are a coin flip that nets ~0R. This bar is STRICTER
      than, and additional to, the normal STANDARD_FUEL_RATIO_FLOOR (1.1),
      and it applies ONLY here -- a WAITING plan that had a direction at
      lock is not subject to it. See decision_engine.PROMOTED_PUSH_FLOOR's
      own comment for the full evidence.
    """
    state = decision_dict.get("verdict_state")
    side = decision_dict.get("side")

    if side is None:
        return None  # still inside the box -- no cross yet

    if state not in _TAKE_STATES:
        headline = decision_dict.get("tactical_brief") or f"{side}: gate declined"
        return {
            "status": "DONE",
            "cross_time": now_utc,
            "vetoed_cross_side": side,
            "vetoed_cross_trigger": decision_dict.get("entry_price"),
            "last_transition_reason": f"cross confirmed, full gate declined -- {headline}",
        }

    import decision_engine as _decision_engine
    push_ratio = decision_dict.get("fuel_push_ratio")
    if push_ratio is not None and push_ratio < _decision_engine.PROMOTED_PUSH_FLOOR:
        return {
            "status": "DONE",
            "cross_time": now_utc,
            "vetoed_cross_side": side,
            "vetoed_cross_trigger": decision_dict.get("entry_price"),
            "last_transition_reason": (
                f"cross cleared the gate ({push_ratio}x baseline push) but a no-plan "
                f"morning promotes only on a strong push -- below the "
                f"{_decision_engine.PROMOTED_PUSH_FLOOR}x floor, no trade"
            ),
        }

    import decision_engine as _decision_engine
    entry_price = float(decision_dict["entry_price"])
    t1 = float(decision_dict["t1"])
    t2 = float(decision_dict["t2"])
    t3 = float(decision_dict["t3"])
    tier = decision_dict.get("tier")
    is_long = side == "LONG"
    box = abs(t2 - entry_price)  # t2 = trigger +/- 1.0*box, so this recovers box

    stop_plan = sp.plan_stop(
        candles_24h=candles_24h, entry_price=entry_price, is_long=is_long,
        r30_high=r30_high, r30_low=r30_low, f24_vah=f24_vah, f24_val=f24_val,
        daily_atr14=daily_atr14,
    )
    if stop_plan["stop_price"] is None:
        return None
    zone_stop = float(stop_plan["stop_price"])

    # TIER-SPECIFIC STOP (2026-09-08, shipped for the other two fill paths in
    # _build_waiting_plan() / advance_waiting_plan() -- see _build_waiting_plan()'s
    # own comment for the full backtest rationale). This promotion path was
    # missing it (found in the 2026-09-10 Domain 1/2/3 audit): STANDARD's real
    # execution stop is the r30-based formula, not PREMIUM's tighter 24h-zone
    # stop. PREMIUM (and tier-unknown, which shouldn't reach here) keeps the
    # zone stop. stop_price_r30 is stored on every plan for audit either way.
    stop_r30 = (r30_low - _decision_engine.STOP_BUFFER_BOX * box) if is_long \
        else (r30_high + _decision_engine.STOP_BUFFER_BOX * box)
    stop_price_r30 = round(float(stop_r30), 2)
    if tier == "STANDARD":
        active_stop = stop_price_r30
        active_basis = (f"r30 edge {'-' if is_long else '+'} {_decision_engine.STOP_BUFFER_BOX:.3f}xbox "
                        f"(STANDARD tier's own execution stop -- NO_PLAN promotion)")
        active_dist_atr = round(abs(entry_price - stop_price_r30) / daily_atr14, 4) if daily_atr14 else None
    else:
        active_stop = zone_stop
        active_basis = stop_plan["stop_basis"]
        active_dist_atr = stop_plan["stop_dist_atr"]

    rr = sp.rr_floor_ok(entry_price, active_stop, t1, is_long=is_long)
    if not rr["ok"]:
        # A wider r30 stop can fail 1:1 where the tighter zone stop passed --
        # same NO_PLAN-preserving philosophy as build_trade_plan(): never
        # place a real order the floor rule would reject; a bad stop this
        # poll doesn't mean it stays bad next poll.
        return None

    headline = decision_dict.get("tactical_brief") or f"{side} real cross confirmed"
    return {
        "status": "FILLED",
        "direction": side, "tier": tier,
        "trigger_price": entry_price,
        "stop_price": active_stop, "stop_basis": active_basis,
        "stop_dist_atr": active_dist_atr,
        "stop_price_r30": stop_price_r30,
        "t1": t1, "t2": t2, "t3": t3,
        "management": MANAGEMENT_TEXT,
        # Detected via a 60s poll, always after the fact -- price is
        # already beyond the trigger every time this fires, matching
        # advance_waiting_plan()'s own "already_broken_out" branch.
        "entry_mode": "RETEST_LIMIT_AT_LINE",
        "rr_floor_ok": True, "rr_ratio": rr["ratio"],
        "cross_time": now_utc, "fuel_at_cross": "FUELED",
        "fill_time": now_utc, "fill_price": entry_price,
        "faked_first": False,
        "last_transition_reason": f"NO_PLAN morning re-evaluated on a later real cross -- {headline}",
    }


# Backtest reference for the alignment-tier email copy (Kabroda AI Brain
# repo AGENT_LOG.md -- DeepSeek's 2026-09-06 12:45 CT initial study,
# confirmed still holding at-break in the 15:25 CT lock-vs-break
# validation study: lock/break readings agree 77.6% of the time, and the
# T3 gradient is stable either way, though the at-break RECOMPUTE only
# matched the gate's own pipeline 44.8% of the time -- exactly why the
# LOCK-time reading, not a fresh at-break one, is what's shown). 58-trade
# MEXC real-lock corpus. Revisit as the corpus grows -- DeepSeek's own
# caveat, not a permanent constant.
_T3_RATE_FULLY_ALIGNED_PCT = 36
_T3_RATE_PARTIAL_ALIGNED_PCT = 21


def _alignment_words(fuel_verdict: Optional[str], htf_aligned: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """Shared word-mapping core for classify_alignment() and
    build_alignment_email_line() -- one place decides what counts as
    FULLY ALIGNED/PARTIAL/CONFLICTED and FUELED/CONFLICTED/NEUTRAL.
    Returns (tier_word, fuel_word), or (None, None) if either input is
    unavailable (e.g. a NO_PLAN morning before any real cross) rather
    than guessing a tier."""
    if fuel_verdict is None or htf_aligned is None:
        return None, None
    fuel_word = fuel_verdict if fuel_verdict in ("FUELED", "CONFLICTED") else "NEUTRAL"
    if htf_aligned >= 2:
        tier_word = "FULLY ALIGNED"
    elif htf_aligned == 1:
        tier_word = "PARTIAL"
    else:
        tier_word = "CONFLICTED"
    return tier_word, fuel_word


def classify_alignment(fuel_verdict: Optional[str], htf_aligned: Optional[int]) -> Optional[str]:
    """Plain-words alignment tag, e.g. "FULLY ALIGNED / fuel FUELED" --
    built ONLY from real fields the gate already computes and GateLog
    already stores (fuel_verdict, htf_aligned -- the count of {1H, 4H}
    trends agreeing with the trade direction, 0-2), no new decision
    input, no effect on sizing or the gate. See build_alignment_email_
    line() for the full email copy this feeds into."""
    tier_word, fuel_word = _alignment_words(fuel_verdict, htf_aligned)
    if tier_word is None:
        return None
    return f"{tier_word} / fuel {fuel_word}"


def build_alignment_email_line(plan: Dict[str, Any]) -> Optional[str]:
    """Full email copy for the alignment tier -- Andy's exact wording
    spec (DeepSeek relay, Kabroda AI Brain repo AGENT_LOG.md, 2026-09-06
    15:30 CT): (1) label the moment ("as of session lock") so it's never
    misread as a live-at-the-break reading; (2) show the real fuel +
    1H/4H trend reads, not just the collapsed tier word; (3) frame
    alignment as CARRY (how far a winner runs), never as entry win rate
    -- htf_fuel.py's own docstring: HTF alignment changes MFE (1.72R ->
    2.57R at 0 -> 2 aligned), NOT T1 win rate (~60% either way per
    CALIBRATION.md section 12, 1,913 breaks); (4) no sizing/gate language
    anywhere near it.

    Returns None when the inputs can't be classified (same rule as
    classify_alignment())."""
    tier_word, fuel_word = _alignment_words(plan.get("fuel_verdict"), plan.get("htf_aligned"))
    if tier_word is None:
        return None

    trend_bits = [f"1H trend {plan['trend_1h']}"] if plan.get("trend_1h") else []
    if plan.get("trend_4h"):
        trend_bits.append(f"4H trend {plan['trend_4h']}")
    lead = f"Fuel {fuel_word}" + ("".join(f" | {b}" for b in trend_bits)) + f" -> {tier_word}"

    if tier_word == "FULLY ALIGNED":
        stat = (
            f"Fully-aligned setups like this reached T3 about {_T3_RATE_FULLY_ALIGNED_PCT}% of the time "
            f"in our 58-trade live-lock study, vs ~{_T3_RATE_PARTIAL_ALIGNED_PCT}% for partial alignment"
        )
    elif tier_word == "PARTIAL":
        stat = (
            f"Partially-aligned setups reached T3 about {_T3_RATE_PARTIAL_ALIGNED_PCT}% of the time "
            f"in our 58-trade live-lock study, vs ~{_T3_RATE_FULLY_ALIGNED_PCT}% when fully aligned"
        )
    else:
        stat = "Conflicted setups have historically run less far after entry in the same study"

    return (
        f"Setup strength (as of session lock): {lead}. {stat} -- alignment is about how far the "
        f"trade can run, not whether it wins. (Exact numbers may be refreshed as the corpus grows; "
        f"the 30-day live review is the referee.)"
    )


def lock_disposition(plan: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """THE single source of truth for the lock-time disposition text --
    the same words the lock email, the radar's Trade Plan panel, and
    render_brief() all render (Andy's radar/email-parity requirement,
    2026-09-10). Returns None once a real cross has moved the plan past
    its lock-time state (FILLED/DONE/VETOED/STOPPED/etc) -- those have
    their own transition reasons and are not a "disposition."

    Four codes, keyed off fields anticipate_setup() already computed:
      A  no trade today       -- WIDE_BOX: box/ATR frozen too wide, no
                                 later cross can ever pass the gate. The
                                 only genuinely final stand-down.
      B  standing by          -- box is reachable but no side to anticipate
                                 yet (no direction / dead hour / dead 15m
                                 tape). A strong-volume break can still ARM.
      C  plan set             -- a real WAITING plan, >=1 HTF backs the side.
      C_WEAK  watching one side -- WAITING plan whose side came off a GOOD
                                 daily table but neither 1H nor 4H backs it
                                 yet; only fills if HTF turns by the cross.

    `code` is stable; `subject`/`headline`/`body` are the rendered strings.
    """
    status = plan.get("status")
    sym = (plan.get("symbol") or "").replace("/", "")
    bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
    ratio = plan.get("box_atr_ratio")
    ratio_txt = f"{ratio:.2f}x daily ATR" if isinstance(ratio, (int, float)) else "wider than the 0.55 ceiling"
    lvl = (f"{bo:,.0f}" if bo else "the breakout"), (f"{bd:,.0f}" if bd else "the breakdown")

    if status == "WAITING":
        direction = plan.get("direction") or "?"
        trig = plan.get("trigger_price")
        trig_txt = f" @ {trig:.0f}" if trig else ""  # no comma -- matches the ARMED subject convention
        tier = plan.get("tier")
        # htf_backs_side_at_lock: True / False / None(legacy) -- only an
        # explicit False downgrades to C_WEAK.
        if plan.get("htf_backs_side_at_lock") is False:
            return {
                "code": "C_WEAK",
                "subject": f"KABRODA - watching one side - {sym} {direction}{trig_txt} - needs HTF",
                "headline": f"Watching {direction} only - the daily table favors it but neither 1H nor 4H backs it yet.",
                "body": (
                    f"The daily table favors {direction}, but neither 1H nor 4H backs it yet -- this only "
                    f"becomes a real trade if a higher timeframe turns before price crosses. Lower confidence "
                    f"than a normal plan. Full plan below."
                ),
            }
        tier_txt = f" ({tier} likely)" if tier else ""
        return {
            "code": "C",
            "subject": f"KABRODA - plan set - {sym} {direction}{trig_txt}{tier_txt}",
            "headline": f"Plan set: {direction}{trig_txt}{tier_txt}.",
            "body": "",  # render_brief()'s full plan output is the body for C
        }

    if status != "NO_PLAN":
        return None  # FILLED / DONE / VETOED / etc -- not a lock-time disposition

    # A NO_PLAN row only has a disposition if anticipate_setup() classified it
    # at lock. A NO_PLAN written by the cross-declined path (a real cross the
    # gate turned down) has a `no_plan_reason` but no category -- that's a
    # VETOED-style outcome, not "standing by"; leave it to its own reason text.
    category = plan.get("no_plan_category")
    if category == "WIDE_BOX":
        return {
            "code": "A",
            "subject": f"KABRODA - no trade today - {sym} (box too wide)",
            "headline": f"No trade today: box is {ratio_txt}, T1 out of reach. No ARMED email possible.",
            "body": (
                f"Genuine stand-down. The box is {ratio_txt} against the 0.55 ceiling -- T1 is out of "
                f"reach even if price breaks a trigger. No ARMED email is possible today. Ignore the "
                f"charts; next check is tomorrow at the session open."
            ),
        }
    if category in ("DEAD_HOUR", "DEAD_TAPE", "NO_DIRECTION"):
        return {
            "code": "B",
            "subject": f"KABRODA - standing by - {sym} (no clear side yet)",
            "headline": "Standing by: box is reachable but no side to anticipate yet. ARMED only on a strong-volume break.",
            "body": (
                f"The box is reachable ({ratio_txt}), but neither the daily table nor the 1H/4H gives a "
                f"direction to anticipate yet. Not watching.\n\n"
                f"If price breaks {lvl[0]} or {lvl[1]} on a strong-volume push with a higher-timeframe "
                f"backing it, you'll get one ARMED email at that moment. Silence the rest of the session "
                f"means it never set up."
            ),
        }
    return None


def render_brief(plan: Dict[str, Any]) -> str:
    """Renders the pre-commit brief (SS4) from a built plan dict (as
    returned by build_trade_plan(), or a TradePlan row's __dict__). Every
    number in the brief is copied from the plan object, never recomputed
    here -- SS4's own rule."""
    date_key = plan.get("date_key", "")
    symbol = plan.get("symbol", "")

    if plan.get("status") == "NO_PLAN":
        disp = lock_disposition(plan)
        # The one-line headline here, not the full body -- the lock email
        # prepends the body already, and a standalone brief / the radar
        # panel want the terse version.
        reason = (disp["headline"] if disp else None) or plan.get("no_plan_reason") or "gate did not approve a setup"
        bo, bd = plan.get("breakout_trigger"), plan.get("breakdown_trigger")
        levels_line = (
            f"\n  Breakout trigger (BO): {bo:.0f}\n  Breakdown trigger (BD): {bd:.0f}\n"
            if bo and bd else ""
        )
        direction = plan.get("direction")
        watching_line = f"\n  Anticipated side, if either crosses: {direction}\n" if direction else ""
        # WIDE_BOX (code A) is the one genuinely-final stand-down; a B disposition
        # can still ARM on a later strong-volume cross (poll-NO_PLAN +
        # PROMOTED_PUSH_FLOOR). No claim either way when we couldn't classify it.
        code = disp.get("code") if disp else None
        if code == "A":
            closer = ("  NO_PLAN is a valid, common outcome. Box and ATR are frozen at lock, "
                      "so this one cannot become a plan later today.")
        elif code == "B":
            closer = ("  NO_PLAN is a valid, common outcome. It can still ARM later if price breaks a "
                      "trigger on a strong-volume push with a higher-timeframe backing it -- otherwise "
                      "silence means it held for the session.")
        else:
            closer = "  NO_PLAN is a valid, common outcome."
        return (
            f"TRADE PLAN — {date_key} — {symbol} — STATUS: NO_PLAN\n\n"
            f"  NO PLAN TODAY — {reason}\n"
            f"{levels_line}{watching_line}\n"
            f"{closer}"
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
        tier_line = "Tier: TBD — stamped at the cross once the fuel check confirms (size and T2 handling, not the entry itself)"
    else:
        tier_line = f"Tier: {tier} ({'stop moves to breakeven at T2' if tier == 'PREMIUM' else 'stop stays at the original level throughout'})"

    alignment_line = build_alignment_email_line(plan)

    lines = [
        f"TRADE PLAN — {date_key} — {symbol} — STATUS: {plan.get('status')}",
        tier_line,
    ]
    if alignment_line:
        lines.append(alignment_line)
    lines += [
        "",
        f"  WAIT UNTIL {commit_str} to commit (post-open-window rule — the "
        f"first ~1h after equity open degrades every trigger; commit_after "
        f"is already past it)",
        "",
        f"  ORDER 1 (trigger/stop-entry): {verb} {trigger:,.2f}",
        f"  STOP: {stop:,.2f} — {stop_basis}",
        f"  T1: {t1:,.2f} (take 50%, stop stays at the original level)   "
        f"T2: {t2:,.2f}{' (PREMIUM: stop to breakeven here)' if tier == 'PREMIUM' else ''}   "
        f"T3: {t3:,.2f} (runner exits here or at the stop)",
        "",
        f"  IF ALREADY BROKEN OUT AT COMMIT TIME: do not chase. Switch to "
        f"ORDER 2 — a limit at the line ({trigger:,.2f}); most breaks retest "
        f"the line before continuing.",
        "",
        f"  FUEL RULE: {plan.get('fuel_requirement')}. A ghost push (NO_FUEL, "
        f"no real volume) VETOES — stand down, wait for the retest. A "
        f"CONFLICTED push still fills, as a real STANDARD trade.",
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
    fuel_verdict: Optional[str] = None,
    push_ratio: Optional[float] = None,
) -> Optional[str]:
    """PREMIUM requires fuel FUELED specifically (not just fuel-condition-
    passing) AND both HTF timeframes AND box/ATR <= 0.40 at the cross,
    else STANDARD if it clears STANDARD_FUEL_RATIO_FLOOR, else None (no
    tier -- the caller must not fill) -- decision_engine.py's own
    _core_gate() tier formula (2026-09-06 three-outcome rebuild, 2026-09-09
    fuel-quality floor), recomputed here (not reimplemented differently),
    for a plan whose tier was left None at generation (the anticipate_
    setup() pre-cross path -- see build_trade_plan()'s docstring) because
    it genuinely couldn't be known until now.

    fuel_verdict is optional ONLY for backward compatibility with any
    caller that hasn't been updated yet -- omitting it means "assume
    FUELED," which is safe for every existing call site (all of them
    only ever call this from inside an `if verdict == "FUELED":` branch
    today). New callers that also admit CONFLICTED crosses (2026-09-07,
    DeepSeek's live-email review) MUST pass the real verdict, since
    CONFLICTED can never earn PREMIUM -- only FUELED can.

    push_ratio (2026-09-09): the push_volume.ratio decision_engine.py's
    _core_gate() also reads, needed here so this second, independent tier
    site can't silently drift from the live gate's own floor -- omitting
    it means "assume the floor passes" (same backward-compatibility stance
    as fuel_verdict), so no existing caller that hasn't been updated
    changes behavior.
    """
    import htf_fuel as _htf_fuel
    import reachability as _reachability
    import decision_engine as _decision_engine

    side = plan.get("direction")
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
    aligned = htf.get("aligned") or 0

    trigger, t2 = plan.get("trigger_price"), plan.get("t2")
    box = abs(t2 - trigger) if (trigger is not None and t2 is not None) else 0.0
    reach = _reachability.reachability(box, daily_atr14)
    ratio = reach.get("ratio")

    fueled = fuel_verdict is None or fuel_verdict == "FUELED"
    premium = fueled and aligned == 2 and ratio is not None and ratio <= _reachability.PREMIUM_BOX_ATR
    if premium:
        return "PREMIUM"

    # HTF carry (2026-09-10, Andy-approved -- see decision_engine.py::_core_gate's
    # own comment for the full evidence trail): at least one of {1H, 4H} must
    # back the direction, for BOTH fuel states. Previously CONFLICTED fuel
    # waived this entirely, so a STANDARD trade could fill with aligned == 0.
    # _core_gate() folds this into `core_passed` (a failed htf_carry check ->
    # pass=False, tier=None); this parallel path must reach the same verdict.
    if aligned == 0:
        return None

    standard_ok = push_ratio is None or push_ratio >= _decision_engine.STANDARD_FUEL_RATIO_FLOOR
    return "STANDARD" if standard_ok else None


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

    # 2026-09-07 fix (same review that caught the CONFLICTED-veto bug
    # below): without fuel_1h/fuel_4h, evaluate_fuel_gate() can only ever
    # return FUELED or CONFLICTED here -- NO_FUEL specifically requires
    # HTF opposition or divergence (fuel_gate.py's own verdict formula),
    # neither of which this call could ever supply. That silently made
    # the VETOED-for-NO_FUEL branch below unreachable from this path.
    # Pass real HTF data through when it's available (same optional
    # candles_1h/candles_4h already used for tier stamping) so a genuine
    # ghost push can actually be detected here too, matching
    # decision_engine.py's own call.
    fuel_1h = fuel_4h = None
    if candles_1h is not None and candles_4h is not None:
        import htf_fuel as _htf_fuel
        htf_for_fuel = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
        fuel_1h, fuel_4h = htf_for_fuel.get("trend_1h"), htf_for_fuel.get("trend_4h")

    fuel = fuel_gate.evaluate_fuel_gate(candles_5m, trigger, side, fuel_1h=fuel_1h, fuel_4h=fuel_4h)
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

    # 2026-09-07 fix (DeepSeek's live-email review, Kabroda AI Brain repo
    # AGENT_LOG.md 09:30/09:50 CT): this used to require verdict == "FUELED"
    # exactly, which VETOED a real, tradeable CONFLICTED-STANDARD cross --
    # a genuine regression relative to the shipped Domain 1 rebuild
    # (decision_engine.py's own gate has admitted FUELED-or-CONFLICTED
    # since that rebuild; NO_FUEL is the only fuel-based veto). Widened to
    # match. _stamp_tier_at_cross() is told the real verdict so a
    # CONFLICTED cross can never accidentally earn PREMIUM (only FUELED
    # can, per that function's own gate).
    if verdict in ("FUELED", "CONFLICTED"):
        updates["status"] = "FILLED"
        updates["fill_time"] = now_utc
        updates["fill_price"] = trigger
        # faked_first (SS9a): did the FIRST cross wick back before
        # acceptance? True only when this fill is the retest after an
        # earlier NO_FUEL cross (status was already VETOED coming in) --
        # a direct first-cross fill is a clean acceptance, not a fake.
        updates["faked_first"] = (status == "VETOED")
        push_ratio = (fuel.get("checks") or {}).get("push_volume", {}).get("ratio")
        prefix = "second " if status == "VETOED" else ""
        updates["last_transition_reason"] = f"{prefix}cross {verdict.lower()} ({push_ratio}x baseline) -- filled"
        if plan.get("tier") is None and candles_1h is not None and candles_4h is not None and daily_atr14:
            new_tier = _stamp_tier_at_cross(
                plan, candles_1h, candles_4h, daily_atr14, fuel_verdict=verdict, push_ratio=push_ratio,
            )
            if new_tier is None:
                # Cleared the fuel-condition check but not a stricter gate
                # requirement, and PREMIUM doesn't apply either -- the real-
                # cross path (decision_engine.py's _core_gate()) would return
                # pass=False/tier=None here too. Must not fall through to
                # FILLED (there is no tier to manage this trade under). Two
                # distinct causes, named separately per KABRODA_REBUILD_SPEC.md
                # §9: the HTF-carry cut (2026-09-10) is the more fundamental
                # one (_core_gate folds it into core_passed), checked first.
                import decision_engine as _decision_engine
                import htf_fuel as _htf_fuel
                _aligned = _htf_fuel.htf_fuel(candles_1h, candles_4h, side).get("aligned") or 0
                if _aligned == 0:
                    reason = (
                        "cross confirmed but neither 1H nor 4H backs the direction "
                        "(no carry fuel) -- no trade"
                    )
                else:
                    reason = (
                        f"cross confirmed but push volume ({push_ratio}x baseline) is below the "
                        f"{_decision_engine.STANDARD_FUEL_RATIO_FLOOR}x floor for standard tier -- no trade"
                    )
                return {"status": "DONE", "last_transition_reason": reason}
            updates["tier"] = new_tier
            # 2026-09-08 TIER-SPECIFIC STOP (see _build_waiting_plan()'s own
            # comment for the full backtest rationale and Andy's explicit
            # decision to ship it directly): the pre-cross anticipate_setup()
            # path generates its plan before tier is known, so
            # _build_waiting_plan() defaulted stop_price to the 24h-zone
            # value (PREMIUM's stop) and separately stored the r30 candidate
            # in stop_price_r30. Only now, with the real tier finally known,
            # do we find out this plan should have been using STANDARD's
            # wider r30 stop instead -- swap it in, and re-run the SAME R:R
            # floor check _build_waiting_plan() ran at generation (a wider
            # stop can fail 1:1 where the tighter one passed; never place a
            # real order the floor rule would have rejected just because the
            # tier wasn't known yet when the plan was first built).
            if new_tier == "STANDARD" and plan.get("stop_price_r30") is not None:
                import decision_engine as _decision_engine
                r30_stop = plan["stop_price_r30"]
                rr = sp.rr_floor_ok(trigger, r30_stop, plan.get("t1"), is_long=is_long)
                if not rr["ok"]:
                    return {
                        "status": "DONE",
                        "tier": new_tier,
                        "last_transition_reason": (
                            f"cross confirmed STANDARD, but its own execution stop (r30-based) "
                            f"fails the 1:1 R:R floor for T1 (R:R {rr['ratio']:.2f}) -- no trade"
                        ),
                    }
                updates["stop_price"] = r30_stop
                updates["stop_basis"] = (
                    f"r30 edge {'-' if is_long else '+'} {_decision_engine.STOP_BUFFER_BOX:.3f}xbox "
                    f"(STANDARD tier's own execution stop, swapped in at the real cross)"
                )
                updates["stop_dist_atr"] = round(abs(trigger - r30_stop) / daily_atr14, 4) if daily_atr14 else None
                updates["rr_ratio"] = rr["ratio"]
        return updates

    # Only NO_FUEL (a ghost push, no real volume) reaches here -- FUELED
    # and CONFLICTED are both handled above, NO_PUSH is handled earlier.
    if status == "VETOED":
        # This is already the second cross (VETOED can only be reached after
        # exactly one NO_FUEL cross -- no counter column needed).
        updates["status"] = "DONE"
        updates["last_transition_reason"] = f"second cross also {verdict} -- no energy, done for the day"
        return updates

    updates["status"] = "VETOED"
    updates["last_transition_reason"] = f"cross {verdict} (ghost push) -- waiting for the retest"
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
