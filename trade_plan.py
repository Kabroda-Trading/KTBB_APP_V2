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
# management text: v2 (2026-09-11 rebuild, see MANAGEMENT_TEXT's own
# comment below): SPLIT 50/50, stop never moves either leg, no tier, no
# BE-move at T2 for anyone. v1's PREMIUM-only mechanical BE-move at T2 is
# still literally in executor_live_engine.py but has been unreachable
# since the v2 rewrite (tier is always None now) -- open question flagged
# to DeepSeek 2026-09-15 (AGENT_LOG.md): is "no BE move, ever" the intended
# v2/traveler rule, or does this need a new v2-native trigger? Do not let
# this text drift from executor_live_engine.py's real behavior once that's
# resolved, same as the check that caught the original drift.
#
# Intraday state machine design (2026-08-31, gate mechanism updated 2026-
# 09-11 for v2): TradePlan's own monitoring only covers the PRE-FILL gated
# entry logic (WAITING/VETOED/FILLED) -- that's genuinely new, CampaignLog's
# own fill detection (ledger_closing_engine.py Phase 1) is price-only, no
# gate check at all. POST-fill
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

import stop_planner as sp

# v2 (2026-09-11): fuel is retired from the gate entirely (decision_engine.py's
# own header comment) -- this constant used to describe the fuel-based
# FUELED/CONFLICTED/NO_FUEL veto and PREMIUM/STANDARD split. Replaced with
# the real v2 gate requirement, same four conditions decision_engine.py's
# _core_gate() checks. render_brief()'s old "FUEL RULE" section reads this
# under a renamed "GATE" label -- see that function.
GATE_REQUIREMENT_TEXT = (
    "at the cross: box must still be <=0.55x daily ATR14, >=1 of {1H, 4H} must "
    "back the direction (the old 9/21 EMA read), Krown Cross (21/55 EMA stack + "
    "6-bar slope) must agree on BOTH 1H and 4H, and the 4H RSI(14) read frozen "
    "at this morning's lock must sit in the control zone for this side "
    "(62-80 LONG / 20-38 SHORT). All four or no trade -- there is no partial "
    "credit and no tier."
)
# Kept as an alias so any caller still reading the old name gets the new text
# rather than a NameError while the rest of the codebase catches up.
FUEL_REQUIREMENT_TEXT = GATE_REQUIREMENT_TEXT

# v2 (2026-09-11): one management rule for every trade, no tier branching --
# SPLIT 50/50 (CC_PACKAGE.md §1): half off at T1, half rides to T3, stop
# never moves either leg. Replaces the old tier-differentiated 50/50 + PREMIUM
# BE-at-T2 rule -- see decision_engine.py's _plan_for_side() for the matching
# per-plan text; this is the module-level default used before a plan is built.
MANAGEMENT_TEXT = (
    "50% off at T1, stop stays at the original level. The other 50% rides to "
    "T3 or the same stop -- it never moves."
)

COMMIT_OFFSET_MINUTES = 45  # anchor_time + 45min = 08:45 CT / 09:45 ET (the open-window rule)

# v2 (2026-09-11): decision_engine.py returns a single "TAKE" verdict_state
# now, no more TAKE_PREMIUM/TAKE_STANDARD. Kept as a tuple (not a bare
# string-equality check) so this doesn't need to change again if the shape
# ever grows back -- but there's only one v2 value.
_TAKE_STATES = ("TAKE",)


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
    Andy rests a trigger order AT THE LEVEL before the cross.

    UPDATED 2026-09-11 (v2 rebuild): this answers the ONE question
    decision_engine.py's real gate genuinely can't answer pre-cross -- which
    direction to anticipate. Krown Cross votes and the RSI zone check are
    BOTH side-dependent (you need to know which trigger got crossed to know
    which side's zone applies), so -- same reasoning as v1's fuel check --
    they can't be resolved here either; they're evaluated properly, with a
    real side, at the actual cross. Reachability is the one v2 condition
    fully knowable at lock (box and ATR are both frozen then); the rest of
    this function is a heuristic for WHICH side is worth watching and
    displaying, not a second copy of the real gate. Dead-hour/dead-tape are
    no longer gate conditions at all in v2 (measured -- see decision_
    engine.py's own header comment) -- this function no longer treats
    either as blocking.

    Returns {"viable": False, "reason": str} (-> NO_PLAN) or
    {"viable": True, "side": "LONG"|"SHORT", "reason": str} (-> WAITING;
    the real gate re-evaluates fully, with this side, at the actual cross).

    A genuinely undetermined direction (no daily bias, no HTF lean) is NOT
    guessed at -- it returns viable=False, deferring to the ORIGINAL
    cross-based path (unchanged, still correct): the plan stays NO_PLAN
    until an actual cross gives decision_engine.py's real gate a side to
    evaluate, same as today's behavior for every case, not just this one.
    """
    import htf_fuel as _htf_fuel
    import market_regime as _market_regime
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

    # DEAD_HOUR/DEAD_TAPE early-returns REMOVED 2026-09-11 (v2 rebuild):
    # decision_engine.py's gate no longer vetoes on either (measured against
    # the real v2 candidate, brain/audit_evidence/d0_veto_stack_on_candidate.py
    # -- see CANON.md §8/decision_engine.py's own header comment). Returning
    # viable=False for either here would report NO_PLAN for a reason that no
    # longer blocks a real trade at the actual cross. `candles_15m`/
    # micro_regime.py are no longer read by this function at all -- kept as
    # a parameter for call-site compatibility (Phase 2/3 cleanup, not yet
    # done, may drop it from the signature later).
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
    side/entry/t1/t2/t3 differs.

    v2 (2026-09-11 rebuild, CC_PACKAGE.md §1 / CANON.md §8): ONE stop
    formula for every trade -- r30 edge -+ STOP_BUFFER_BOX*box, the same
    formula that used to be STANDARD-only (shipped 2026-09-08) and, before
    that, was purely the R-bookkeeping basis (never sent to the exchange).
    There is no more PREMIUM/STANDARD split, so there is no more zone-stop
    branch -- stop_planner.py's plan_stop() (the 24h-core-zone stop) is no
    longer called here at all. `tier` is still accepted as a parameter for
    call-site compatibility (both callers still pass one) but no longer
    affects anything in this function; kept for a later cleanup pass, not
    load-bearing.

    candles_24h/f24_vah/f24_val are accepted for the same call-site-
    compatibility reason -- unused now that plan_stop() isn't called."""
    import decision_engine as _decision_engine
    is_long = side == "LONG"
    box = abs(t2 - entry_price)
    if not daily_atr14 or daily_atr14 <= 0:
        # Can't compute a real stop-distance-in-ATR diagnostic without it,
        # and a plan with no known ATR shouldn't have passed reachability
        # in the first place -- NO_PLAN rather than guess.
        return {**base, "status": "NO_PLAN", "no_plan_reason": "daily ATR14 unavailable"}
    r30_stop_raw = (r30_low - _decision_engine.STOP_BUFFER_BOX * box) if is_long \
        else (r30_high + _decision_engine.STOP_BUFFER_BOX * box)
    stop_price_r30 = round(float(r30_stop_raw), 2)

    active_stop_price = stop_price_r30
    active_stop_basis = (f"r30 edge {'-' if is_long else '+'} "
                          f"{_decision_engine.STOP_BUFFER_BOX:.3f}xbox (v2's one stop, level-anchored)")
    active_stop_dist_atr = round(abs(entry_price - stop_price_r30) / daily_atr14, 4)

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
                f"r30 stop too wide for T1 -- R:R {rr['ratio']:.2f} "
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
    rsi_4h_at_lock: Optional[float] = None,
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

    rsi_4h_at_lock (2026-09-11, v2 gate): the SAME lock-time RSI value
    battlebox_pipeline.py freezes into levels["rsi_4h_at_lock"] -- passed
    through here (not recomputed) so it can persist on the TradePlan row
    (a real DB column, TradePlan.rsi_4h_at_lock) and survive from generation
    through to the real cross, where advance_waiting_plan() reads it back
    for the v2 gate's RSI-zone check. Unlike breakout_trigger/r30_high/etc.
    above, this one IS a real column, not transient-only.

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
        "rsi_4h_at_lock": rsi_4h_at_lock,
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
    v2 (2026-09-11): PROMOTED_PUSH_FLOOR is retired along with fuel entirely
    (decision_engine.py no longer has the constant -- fuel was found to be a
    post-fill information artifact, not decision-time computable; see that
    file's own header comment). There is no more fuel-strength re-check on
    promotion -- a real TAKE at any later cross promotes straight to FILLED,
    same as any other TAKE. The stop is v2's one formula (r30 edge, no
    tier branching) -- see _build_waiting_plan()'s own comment for the
    full rationale.
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
    entry_price = float(decision_dict["entry_price"])
    t1 = float(decision_dict["t1"])
    t2 = float(decision_dict["t2"])
    t3 = float(decision_dict["t3"])
    is_long = side == "LONG"
    box = abs(t2 - entry_price)  # t2 = trigger +/- 1.0*box, so this recovers box

    if not daily_atr14 or daily_atr14 <= 0:
        return None  # can't plan a real stop without it -- keep NO_PLAN, retry next poll

    stop_r30 = (r30_low - _decision_engine.STOP_BUFFER_BOX * box) if is_long \
        else (r30_high + _decision_engine.STOP_BUFFER_BOX * box)
    stop_price_r30 = round(float(stop_r30), 2)
    active_stop = stop_price_r30
    active_basis = (f"r30 edge {'-' if is_long else '+'} {_decision_engine.STOP_BUFFER_BOX:.3f}xbox "
                    f"(v2's one stop, level-anchored -- NO_PLAN promotion)")
    active_dist_atr = round(abs(entry_price - stop_price_r30) / daily_atr14, 4)

    rr = sp.rr_floor_ok(entry_price, active_stop, t1, is_long=is_long)
    if not rr["ok"]:
        # NO_PLAN-preserving philosophy, same as build_trade_plan(): never
        # place a real order the floor rule would reject; a bad stop this
        # poll doesn't mean it stays bad next poll.
        return None

    headline = decision_dict.get("tactical_brief") or f"{side} real cross confirmed"
    return {
        "status": "FILLED",
        "direction": side, "tier": None,
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
        # fuel_at_cross: kept as a DB column (database.py) but fuel itself
        # is retired -- None, not a fabricated "FUELED", now that nothing
        # computes it.
        "cross_time": now_utc, "fuel_at_cross": None,
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
    FULLY ALIGNED/PARTIAL/CONFLICTED. Returns (tier_word, fuel_word), or
    (None, None) if htf_aligned is unavailable (e.g. a NO_PLAN morning
    before any real cross) rather than guessing a tier.

    v2 (2026-09-11): `fuel_word`'s second slot is retired along with fuel
    (decision_dict["fuel_verdict"] is always None now) -- kept as a return
    slot for the two callers below rather than changing their signature,
    but always None here. htf_aligned alone still carries real information
    (same CALIBRATION.md carry-not-win-rate finding this was always about)."""
    if htf_aligned is None:
        return None, None
    if htf_aligned >= 2:
        tier_word = "FULLY ALIGNED"
    elif htf_aligned == 1:
        tier_word = "PARTIAL"
    else:
        tier_word = "CONFLICTED"
    return tier_word, None


def classify_alignment(fuel_verdict: Optional[str], htf_aligned: Optional[int]) -> Optional[str]:
    """Plain-words alignment tag, e.g. "FULLY ALIGNED" -- built from
    htf_aligned (the count of {1H, 4H} trends agreeing with the trade
    direction, 0-2), no new decision input, no effect on sizing or the
    gate. `fuel_verdict` is accepted for call-site compatibility but no
    longer used (v2 has no fuel -- see _alignment_words()'s own comment).
    See build_alignment_email_line() for the full email copy this feeds
    into."""
    tier_word, _ = _alignment_words(fuel_verdict, htf_aligned)
    if tier_word is None:
        return None
    return tier_word


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
    classify_alignment()). v2 (2026-09-11): the "Fuel <word> |" lead-in is
    dropped -- fuel is retired, there's nothing real to show there any
    more (see _alignment_words()'s own comment)."""
    tier_word, _ = _alignment_words(plan.get("fuel_verdict"), plan.get("htf_aligned"))
    if tier_word is None:
        return None

    trend_bits = [f"1H trend {plan['trend_1h']}"] if plan.get("trend_1h") else []
    if plan.get("trend_4h"):
        trend_bits.append(f"4H trend {plan['trend_4h']}")
    lead = ("".join(f"{b} | " for b in trend_bits)) + tier_word

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
    trigger = plan.get("trigger_price")
    stop = plan.get("stop_price")
    stop_basis = plan.get("stop_basis")
    t1, t2, t3 = plan.get("t1"), plan.get("t2"), plan.get("t3")
    commit_after = plan.get("commit_after")
    commit_str = commit_after.strftime("%H:%M UTC") if isinstance(commit_after, datetime.datetime) else str(commit_after)
    verb = "BUY" if direction == "LONG" else "SELL"

    # v2 (2026-09-11): no more tier line -- there is no tier left to be
    # "TBD" about (the old line promised a stamp-at-cross event that no
    # longer happens). plan.get("tier") is always None now; not printed.
    alignment_line = build_alignment_email_line(plan)

    lines = [
        f"TRADE PLAN — {date_key} — {symbol} — STATUS: {plan.get('status')}",
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
        f"T3: {t3:,.2f} (the other 50% rides here or to the stop -- it never moves)",
        "",
        f"  IF ALREADY BROKEN OUT AT COMMIT TIME: do not chase. Switch to "
        f"ORDER 2 — a limit at the line ({trigger:,.2f}); most breaks retest "
        f"the line before continuing.",
        "",
        f"  GATE: {plan.get('fuel_requirement')}",
        "",
        f"  MANAGEMENT: {plan.get('management')}",
        "",
        "  ONE TRADE TODAY.",
    ]
    return "\n".join(lines)


# ==============================================================================
# INTRADAY STATE MACHINE (SS5) — pre-fill, Krown-Cross+RSI-gated entry logic
# ==============================================================================

def _confirm_v2_gate_at_cross(
    plan: Dict[str, Any],
    candles_1h: List[Dict[str, Any]],
    candles_4h: List[Dict[str, Any]],
    daily_atr14: float,
) -> Dict[str, Any]:
    """v2 (2026-09-11): replaces _stamp_tier_at_cross() -- there is no more
    tier to stamp, so this re-checks the SAME 4-condition gate decision_
    engine.py's _core_gate() implements (reachability, HTF aligned>=1,
    Krown Cross votes==2, RSI-at-lock in zone), recomputed here (not
    reimplemented differently) for a plan whose side was anticipated at
    lock (the anticipate_setup() pre-cross path -- see build_trade_plan()'s
    docstring) and now has a real cross to confirm against.

    Returns {"pass": bool, "misses": [str, ...]} -- same shape as
    decision_engine._core_gate()'s own return, so callers can report the
    real reason a plan didn't fill instead of a bare "no trade."

    RSI is read from plan["rsi_4h_at_lock"] (frozen at generation, NOT
    recomputed here) -- see database.py's TradePlan.rsi_4h_at_lock and
    decision_engine.py's header comment for why RSI is frozen-at-lock
    while Krown Cross/HTF-aligned are not."""
    import htf_fuel as _htf_fuel
    import reachability as _reachability
    import decision_engine as _decision_engine

    side = plan.get("direction")
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
    aligned = htf.get("aligned") or 0
    cross = _htf_fuel.krown_cross_votes(candles_1h, candles_4h, side)
    votes = cross.get("votes") or 0

    trigger, t2 = plan.get("trigger_price"), plan.get("t2")
    box = abs(t2 - trigger) if (trigger is not None and t2 is not None) else 0.0
    reach = _reachability.reachability(box, daily_atr14)

    rsi_4h_at_lock = plan.get("rsi_4h_at_lock")
    rsi_lo, rsi_hi = _decision_engine.RSI_ZONE_LONG if side == "LONG" else _decision_engine.RSI_ZONE_SHORT
    if rsi_4h_at_lock is None:
        rsi_ok = False
    elif side == "LONG":
        rsi_ok = rsi_lo <= rsi_4h_at_lock < rsi_hi
    else:
        rsi_ok = rsi_lo < rsi_4h_at_lock <= rsi_hi

    misses: List[str] = []
    if not reach["ok"]:
        misses.append(reach["note"])
    if aligned < 1:
        misses.append("neither 1H nor 4H backs the direction (no carry)")
    if votes < 2:
        misses.append(f"Krown Cross votes={votes}/2 (need both 1H and 4H)")
    if not rsi_ok:
        rsi_text = f"{rsi_4h_at_lock:.1f}" if rsi_4h_at_lock is not None else "unavailable"
        misses.append(f"4H RSI at lock ({rsi_text}) outside the {rsi_lo}-{rsi_hi} control zone")

    return {"pass": not misses, "misses": misses}


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
    """Pre-fill transitions ONLY: WAITING -> FILLED/DONE.

    Held until commit_after (the open-window rule) — SS1's "no plan
    generated intraday" rule doesn't mean no MONITORING before commit_after,
    it means the plan's fixed fields (trigger/stop/targets) never change;
    whether/when it fires is exactly what this function decides.

    v2 (2026-09-11): there is no more fuel gate, so there is no more NO_PUSH/
    FUELED/CONFLICTED/NO_FUEL verdict and no more VETOED-then-retest state --
    once the anticipated side's trigger is actually touched, the v2 gate
    (reachability, HTF aligned>=1, Krown Cross votes==2, RSI-at-lock in
    zone -- _confirm_v2_gate_at_cross()) either passes or it doesn't; there
    is no "ghost push, maybe next time" concept left to retry. A plan still
    sitting with status=="VETOED" from before this rebuild is treated the
    same as WAITING here (there is nothing left to distinguish them by).

    candles_1h/candles_4h/daily_atr14 (all optional) feed
    _confirm_v2_gate_at_cross() -- omitting any of them means "can't
    re-check the gate," which returns None (stay WAITING, retry next poll)
    rather than guessing.

    ARMED and FILLED collapse into one transition here: a stop/limit order
    sitting exactly at trigger_price fills the instant price touches it --
    this is advisory tracking (SS1 point 2, never real order placement), so
    there's no meaningful gap between "gate confirmed + touched" and
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

    touched = (live_price >= trigger) if is_long else (live_price <= trigger)
    if not touched:
        # P0 FIX (2026-09-01, confirmed live -- Kabroda AI Brain repo
        # AGENT_LOG.md "CONFIRMED P0: state machine missed a live cross"):
        # not touched on the anticipated side does NOT mean nothing
        # happened -- anticipate_setup() picks ONE direction at lock, but
        # price can break the OPPOSITE trigger instead (a genuine counter-
        # trend move). Without this check the plan sat WAITING forever
        # while price moved 200+ points through the other trigger with
        # zero detection, no email, nothing. box is derivable from
        # already-known fields (t2 = trigger +/- 1.0*box).
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

    updates: Dict[str, Any] = {"cross_time": now_utc}

    # entry_mode decided the first time price actually reaches the trigger
    # at/after commit_after -- SS2's "already broken out at commit time" rule.
    if plan.get("entry_mode") is None:
        already_broken_out = (live_price > trigger) if is_long else (live_price < trigger)
        updates["entry_mode"] = "RETEST_LIMIT_AT_LINE" if already_broken_out else "TRIGGER_AT_LEVEL"

    if candles_1h is None or candles_4h is None or not daily_atr14:
        return None  # can't re-check the gate this poll -- try again next time

    gate = _confirm_v2_gate_at_cross(plan, candles_1h, candles_4h, daily_atr14)
    if not gate["pass"]:
        reason = "; ".join(gate["misses"]) or "gate declined"
        return {**updates, "status": "DONE",
                "last_transition_reason": f"cross confirmed but the gate declined -- {reason}"}

    updates["status"] = "FILLED"
    updates["fill_time"] = now_utc
    updates["fill_price"] = trigger
    updates["faked_first"] = False  # no more retest state to have faked out of
    updates["last_transition_reason"] = "cross confirmed, gate passed -- filled"
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
    exercised this path. The guard is left in place even though the SS8
    re-entry chain that used to set reentry_used=True is now removed (see
    the module's own re-entry-retirement note below) -- it's cheap
    protection for any pre-rebuild row that might still carry the flag,
    not load-bearing for anything new.
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


# SS8's fuel-gated re-entry-after-wick-fake (resolve_reentry_fill(),
# check_reentry_eligibility(), advance_reentry_plan()) REMOVED 2026-09-15
# (Andy-approved, v1-dead-machinery audit) -- confirmed fully unreachable:
# trade_plan_engine.py's STOPPED branch resolves unconditionally to DONE
# now (v2, 2026-09-11, "no leg 2" -- see that file's own header comment),
# so check_reentry_eligibility() (the only thing that could ever set
# REENTRY_ARMED) was never called from anywhere in production, which made
# advance_reentry_plan() and resolve_reentry_fill() unreachable in turn.
# This was genuinely dead code, not just an unused-but-real utility (unlike
# fuel_gate.py itself, kept for other reasons) -- Andy's "remove, don't
# patch" bar for machinery a rewrite makes unreachable. If a v2-native
# re-entry design is ever built, it needs a new v2-consistent eligibility
# signal (there is no fuel to check "still worth it" against any more) --
# this is a fresh design, not a resurrection of these three functions.
# Full text preserved in git history (this file, pre-2026-09-15).
#
# TradePlan.reentry_used/reentry_cross_time/reentry_fill_price columns are
# left in the schema (nothing currently sets them, and mirror_campaign_
# outcome() above still guards on reentry_used defensively) -- a later
# schema cleanup, not done here.
