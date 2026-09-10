# decision_engine.py
# ==============================================================================
# THE CALIBRATED GATE — replaces the old graded-conviction model entirely.
#
# Rebuilt 2026-08-30 per KABRODA_REBUILD_SPEC.md (Kabroda AI Brain repo),
# Andy's direct, explicit authorization for a full replacement, not a patch:
# "we're basically replacing what's there with this." Source of truth for the
# numbers: CALIBRATION.md SS11-12 in that repo (1,913-trigger-break backtest,
# 2021-2026, train/test split, corroborated against kabroda.com's own 123 real
# VRVP locks). Source of truth for the logic: brain/engine/verdict.py,
# reachability.py, htf_fuel.py, fuel_gate.py, market_regime.py, regime.py.
#
# WHAT THIS REPLACES:
#   - The old STRONG/LEAN/NEUTRAL graded-conviction model (structure hard
#     gate -> trend hard veto -> volatility/momentum graded -> 1H/4H booster).
#     That model was itself a real improvement over an even older rigid gate
#     (CONFLUENCE_RESEARCH_REPORT.md, 2026-08-27) -- but it was never
#     backtested against real outcomes at scale. This one was: 71 of
#     kabroda.com's own real filled trades lost money (29.8% win, -0.30R avg,
#     -21.4R total) under the old logic.
#   - trade_structure_analyst.py's ATR + gravity-wall stop/target snapping.
#     The new stop/target formula (SS THE PLAN below) is pure box arithmetic --
#     no gravity dependency. Gravity is being decoupled from the trade
#     decision entirely (Andy's call, 2026-08-30) and becomes its own
#     reference page, not an input here.
#   - The old 2-consecutive-close acceptance gate AS THE ENTRY SIGNAL. The
#     backtest evaluates on the FIRST 5m close beyond BO/BD (KABRODA_REBUILD_
#     SPEC.md SS2) -- the 4-condition gate (which includes real volume
#     confirmation) is the false-breakout filter now, not a close count.
#     Deploying the gate behind the old 2-close requirement would mean
#     running something that was never actually backtested.
#
# THE CORE GATE (§2):
#   1. reachability -- box <= 0.55x daily ATR(14)          (reachability.py)
#   2. fuel         -- 5M push volume FUELED or CONFLICTED  (fuel_gate.py)
#   3. HTF carry    -- >=1 of {1H, 4H} trend backs the side, but ONLY required
#                      on the FUELED path. Rebuilt 2026-09-06 per
#                      GATE_REBUILD_SPEC.md §1 (Kabroda AI Brain repo):
#                      STANDARD's fuel-CONFLICTED path carries no HTF
#                      qualifier -- verified against the real 80-row
#                      CONFLICTED corpus (calibration_data/replay_verdict.csv),
#                      profitable at HTF=0/1/2 alike, majority at HTF=1.
#                      Gating it on carry would have thrown out real,
#                      validated performance.                (htf_fuel.py)
#   4. session hour -- not a dead-tape hour                  (DEAD_HOURS below)
#
# HARD VETOES cap the result below TAKE even if the gate passes:
#   - ghost push (NO_FUEL)
#   - DEAD 15m regime (no participation)                     (micro_regime.py)
#   - counter-trend on a GOOD daily table                    (market_regime.py)
#
# The old 4th hard veto -- 15M momentum divergence against the side, sourced
# from mtf_confluence_scanner.py -- was REMOVED 2026-09-06 per
# GATE_REBUILD_SPEC.md §2 / Andy's explicit call: it predates the 2026-08-30
# rebuild, never validated against backtest data, and couldn't even fire on
# real fills (trade_plan_engine.py, the only call site that places real
# orders, already passed confluence_15m=None). mtf_confluence_scanner.py
# itself is untouched -- it keeps running for its other, non-decision uses
# (the Gravity Map treatment: real tool, just not a decision input).
#
# THREE OUTCOMES ONLY -- no grades, no score, no "HOLD FIRE", no
# ALMOST/NEEDS-CONFIRMATION limbo (removed 2026-09-06, same spec: the real
# corpus showed that population outperforming what the gate already took):
#   TAKE_PREMIUM / TAKE_STANDARD / PASS
#
# No LLM call anywhere in this file. No prose generation beyond a plain-
# English headline built from the same booleans that decided the outcome.
# Cost is zero.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from harness.unified_audit_writer import gauge as _gauge

import fuel_gate as _fuel_gate
import htf_fuel as _htf_fuel
import market_regime as _market_regime
import micro_regime as _micro_regime
import reachability as _reachability

GaugeTuple = Tuple[str, str, Optional[float], Optional[str]]

_LONG, _SHORT = "LONG", "SHORT"

# KABRODA_REBUILD_SPEC.md §2.4 / CALIBRATION.md §12: <12 UTC -> 32% T1-reach,
# 18-21 UTC -> 42%. 12-18 UTC is fine. Andy-locked MEDIUM operating point.
DEAD_HOURS = set(range(0, 12)) | {18, 19, 20}

# STANDARD fuel-quality floor (2026-09-09, Andy-approved). Backtest finding:
# STANDARD's fuel-CONFLICTED-or-thin population (push ratio < ~1.0x the 24h
# baseline) is where the tier's real losses concentrate; above this floor it
# is solidly profitable. PREMIUM is untouched -- it already requires FUELED
# (ratio >= VOL_FUELED = 0.8) specifically, a separate, stricter condition.
# This does NOT replace that -- it's an additional floor on the STANDARD
# path only, since STANDARD's gate otherwise accepts CONFLICTED (any ratio
# down to VOL_THIN) as well as sub-PREMIUM FUELED reads.
#
# 1.1, not 1.0: a real walk-forward (fit 2021-2023, freeze, verify on
# 2024-2026 the fit never saw) independently picked 1.1 as the strongest
# threshold, on BOTH the fit window (best avgR of any threshold 0.8-1.3)
# and the full 2021-2026 period (best absolute sumR of the same range,
# +118.2R vs +93.3R with no floor at all). CORRECTION 2026-09-09 (later
# same day): an earlier version of this comment cited "2025 is a real
# losing year at exactly 1.0" as the specific reason for 1.1 over 1.0 --
# that check itself had a bug (replay_v3_5yr.py was using PREMIUM's zone
# stop for STANDARD trades too, not STANDARD's real r30 stop shipped
# 2026-09-08). Fixed and rechecked: with the correct stop, 2025 is
# positive even at 1.0. The "1.0 fails" claim is retracted -- 1.1 is kept
# anyway because it's still the genuine full-period and fit-window
# optimum, not because 1.0 is broken. Full trail: Kabroda AI Brain repo
# AGENT_LOG.md, 2026-09-09 19:55 CT and the correction that follows it.
STANDARD_FUEL_RATIO_FLOOR = 1.1

# §6 management rule -- Andy's fib convention: anchor 0 = BD, 1.0 = BO.
T1_BOX, T2_BOX, T3_BOX = 0.618, 1.0, 1.618
STOP_BUFFER_BOX = 0.12      # swept 2026-08-29, robust train/test (Brain repo)
SUBTRIG_BOX = 0.15          # runner stop after T1


def _plan_for_side(side: str, bo: float, bd: float, r30_high: float, r30_low: float) -> Dict[str, Any]:
    """§6: entry at the trigger, stop off the 30M range, T1/T2/T3 as box
    multiples, runner stop after T1. No gravity, no ATR in the stop -- box only."""
    box = bo - bd
    sgn = 1 if side == _LONG else -1
    trig = bo if side == _LONG else bd
    entry = round(float(trig), 2)

    stop = (r30_low - STOP_BUFFER_BOX * box) if side == _LONG else (r30_high + STOP_BUFFER_BOX * box)
    subtrig_stop = trig - sgn * SUBTRIG_BOX * box

    t1 = trig + sgn * T1_BOX * box
    t2 = trig + sgn * T2_BOX * box
    t3 = trig + sgn * T3_BOX * box

    return {
        "entry": entry, "stop": round(float(stop), 2),
        "t1": round(float(t1), 2), "t2": round(float(t2), 2), "t3": round(float(t3), 2),
        # subtrig_stop: a GateLog-only diagnostic value (logged via
        # kabroda_mas_flow.py/trade_plan_engine.py for the forward-
        # incubation record) -- NOT the real management rule anymore.
        # It traces to the pre-audit fixed-runner-stop concept
        # (superseded 2026-09-07: the audited rule keeps the ORIGINAL
        # stop through T1, moves to breakeven at T2 for PREMIUM only --
        # see executor_live_engine.py). Kept computed/logged so GateLog
        # rows don't silently lose a column the Brain repo may still
        # read, but nothing in the live management path acts on it.
        "subtrig_stop": round(float(subtrig_stop), 2),
        "box": round(box, 2),
        # Tier isn't known yet at this call (computed downstream by
        # _core_gate()) -- kept tier-neutral and accurate rather than
        # guessing. See MANAGEMENT_TEXT in trade_plan.py / CLAUDE.md's
        # "Calibrated Gate" section for the full, tier-specific rule this
        # describes; TradePlan.management is the authoritative text
        # actually emailed, not this field.
        "management": (
            f"Take 50% off at T1 {round(t1, 2):,.0f}, stop stays at the original "
            f"level. PREMIUM only: stop moves to breakeven at T2 {round(t2, 2):,.0f}. "
            f"STANDARD: stop stays original through T3 {round(t3, 2):,.0f}. Either "
            "way, the runner (50%) exits at T3 or its stop."
        ),
    }


def _core_gate(*, box: float, atr: float, fuel: Dict[str, Any],
                htf: Dict[str, Any], session_hour: Optional[int]) -> Dict[str, Any]:
    reach = _reachability.reachability(box, atr)
    fuel_verdict = fuel.get("verdict")
    fueled = fuel_verdict == "FUELED"
    conflicted = fuel_verdict == "CONFLICTED"
    fuel_ok = fueled or conflicted
    aligned = htf.get("aligned") or 0
    # HTF carry is required on the FUELED path only. GATE_REBUILD_SPEC.md §1's
    # STANDARD definition lists no HTF qualifier for the fuel-CONFLICTED case,
    # and the real 80-row CONFLICTED corpus backs that exactly: all three HTF
    # buckets are profitable (avg R +0.44/+0.24/+1.41 at HTF=0/1/2), majority
    # at HTF=1 -- gating CONFLICTED on carry would exclude the majority of
    # real, validated performance for no reason the data supports.
    htf_ok = True if conflicted else (aligned >= 1)
    hour_ok = session_hour is None or session_hour not in DEAD_HOURS

    checks = {"reachability": reach["ok"], "fuel": fuel_ok, "htf_carry": htf_ok, "session_hour": hour_ok}
    misses: List[str] = []
    if not reach["ok"]:
        misses.append(reach["note"])
    if not fuel_ok:
        misses.append(f"5M push volume not FUELED or CONFLICTED (fuel = {fuel_verdict or 'unknown'})")
    if not htf_ok:
        misses.append("neither 1H nor 4H backs the direction (no carry fuel)")
    if not hour_ok:
        misses.append(f"{session_hour:02d}:00 UTC is a dead-tape hour")

    core_passed = not misses  # the original 4-condition gate, unchanged by the floor below
    ratio = reach.get("ratio")
    # PREMIUM still requires FUELED specifically (not just fuel-condition-
    # passing) plus both HTF timeframes plus a tight box -- unchanged from
    # before this rebuild. CONFLICTED can only ever earn STANDARD.
    premium = bool(core_passed and fueled and aligned == 2 and ratio is not None and ratio <= _reachability.PREMIUM_BOX_ATR)

    # STANDARD's additional fuel-quality floor -- see STANDARD_FUEL_RATIO_FLOOR's
    # own comment for the evidence and why 1.1, not the originally-proposed 1.0.
    # Reads the SAME push_volume.ratio already computed by fuel_gate.py and
    # already logged to GateLog as fuel_push_ratio (below, via evaluate_15m_
    # decision()'s own dict) -- no new measurement, no new data, no latency.
    push_ratio = (fuel.get("checks", {}) or {}).get("push_volume", {}).get("ratio")
    standard_ok = core_passed and push_ratio is not None and push_ratio >= STANDARD_FUEL_RATIO_FLOOR

    # `passed` (returned as "pass") must reflect whether a REAL tier was
    # actually assigned, not just the original 4-condition gate -- the only
    # caller (evaluate_15m_decision()) branches on gate["pass"] alone and
    # falls through to TAKE_STANDARD whenever it's True, so a core-gate-passed-
    # but-no-tier-assigned case must report pass=False or it would silently
    # take the trade anyway, defeating the whole point of this floor.
    if premium:
        tier = "PREMIUM"
        passed = True
    elif standard_ok:
        tier = "STANDARD"
        passed = True
    else:
        tier = None
        passed = False
        if core_passed:
            # The 4-condition gate passed but didn't clear the stricter
            # STANDARD-only floor -- name it, per KABRODA_REBUILD_SPEC.md §9
            # ("log every detail," every PASS names its specific reason).
            ratio_text = f"{push_ratio}" if push_ratio is not None else "unavailable"
            misses.append(
                f"push volume below the {STANDARD_FUEL_RATIO_FLOOR}x-baseline floor "
                f"for standard tier (ratio = {ratio_text})"
            )
    return {"pass": passed, "tier": tier, "checks": checks, "misses": misses, "reach": reach, "htf_aligned": aligned}


def evaluate_15m_decision(
    *,
    levels: Dict[str, Any],
    candles_5m: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    candles_1h: List[Dict[str, Any]],
    candles_4h: List[Dict[str, Any]],
    candles_1d: List[Dict[str, Any]],
    session_hour_utc: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
    """Returns (decision_dict, gauge_readings). decision_dict has the
    ExecutiveBrief field names plus `verdict_state` (TAKE_PREMIUM/
    TAKE_STANDARD/PASS), `side`, `tier`, `gate` (full detail dict for
    the DB log). Callers do
    ExecutiveBrief(**{k: v for k, v in decision_dict.items() if k in ExecutiveBrief.__fields__})."""

    bo = float(levels.get("breakout_trigger") or 0)
    bd = float(levels.get("breakdown_trigger") or 0)
    r30_high = float(levels.get("range30m_high") or bo)
    r30_low = float(levels.get("range30m_low") or bd)
    atr = float(levels.get("daily_atr14") or 0)
    price = float(levels.get("price") or 0)
    box = (bo - bd) if (bo and bd and bo > bd) else 0.0

    # Set before the earliest possible _result() call (the no-signal-yet
    # early return below) so the closure always has a value, even when the
    # gate short-circuits before market_regime.py/micro_regime.py ever run.
    daily: Optional[Dict[str, Any]] = None
    micro: Optional[Dict[str, Any]] = None
    htf: Optional[Dict[str, Any]] = None
    fuel: Optional[Dict[str, Any]] = None

    def _result(state: str, side: Optional[str], headline: str, gate: Optional[Dict[str, Any]],
                plan: Optional[Dict[str, Any]], gauges: List[GaugeTuple]) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
        is_take = state in ("TAKE_PREMIUM", "TAKE_STANDARD")
        d: Dict[str, Any] = {
            "approval_status": "APPROVED" if is_take else "STAND_DOWN",
            "conviction": state,          # kept for CampaignLog schema compatibility
            "verdict_state": state,
            "side": side,
            "tier": (gate or {}).get("tier"),
            "tactical_brief": headline,
            "bias": side or "NEUTRAL",
            "entry_price": (plan or {}).get("entry", 0.0),
            "stop_loss": (plan or {}).get("stop", 0.0),
            "t1": (plan or {}).get("t1", 0.0),
            "t2": (plan or {}).get("t2", 0.0),
            "t3": (plan or {}).get("t3", 0.0),
            "formatted_newsletter_md": "",
            "gate": gate,
            "plan": plan,
            # The REAL, validated regime classification (KABRODA_REBUILD_SPEC.md /
            # CALIBRATION.md, Kabroda AI Brain repo) -- already computed for the
            # counter-trend/dead-tape vetoes below, now actually surfaced for
            # display instead of being silently discarded after the veto check.
            "market_regime_table":   (daily or {}).get("table"),
            "market_regime_quality": (daily or {}).get("quality"),
            "micro_regime":          (micro or {}).get("regime"),
            # Raw diagnostic values (2026-08-31, SS9a forward-test log) --
            # already computed above for the gate's own checks/gauges, just
            # not previously surfaced on decision_dict itself. Purely
            # additive: no formula, threshold, or verdict changes here.
            "fuel_verdict":   (fuel or {}).get("verdict"),
            "fuel_push_ratio": ((fuel or {}).get("checks", {}).get("push_volume", {}) or {}).get("ratio"),
            "trend_1h": (htf or {}).get("trend_1h"),
            "trend_4h": (htf or {}).get("trend_4h"),
            "htf_aligned": (htf or {}).get("aligned"),
            "htf_opposed": (htf or {}).get("opposed"),
        }
        return d, gauges

    # --- side: LONG if price beyond BO, SHORT if beyond BD, else no signal yet.
    # Per KABRODA_REBUILD_SPEC.md §2: evaluated on the first close beyond
    # either trigger -- not the old 2-consecutive-close acceptance count.
    side: Optional[str] = None
    if bo and price and price > bo:
        side = _LONG
    elif bd and price and price < bd:
        side = _SHORT

    base_gauges: List[GaugeTuple] = [g for g in [
        _gauge("15M", "box", box),
        _gauge("15M", "daily_atr14", atr),
        _gauge("15M", "price", price),
        _gauge("15M", "candidate_side", side),
        _gauge("15M", "session_hour_utc", session_hour_utc),
    ] if g]

    if side is None:
        return _result("PASS", None,
                        "Price is inside the box -- no trigger crossed yet. Waiting for BO/BD.",
                        None, None, base_gauges)

    micro = _micro_regime.classify_regime(candles_15m)
    daily = _market_regime.classify_market_regime(candles_1d)
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
    trigger_price = bo if side == _LONG else bd
    fuel = _fuel_gate.evaluate_fuel_gate(
        candles_5m, trigger_price, side,
        fuel_1h=htf.get("trend_1h"), fuel_4h=htf.get("trend_4h"),
    )

    plan = _plan_for_side(side, bo, bd, r30_high, r30_low) if box > 0 else None
    reach = _reachability.reachability(box, atr)

    gauges = base_gauges + [g for g in [
        _gauge("15M", "regime", micro.get("regime")),
        _gauge("1D", "market_table", daily.get("table")),
        _gauge("1D", "market_quality", daily.get("quality")),
        _gauge("1H", "trend", htf.get("trend_1h")),
        _gauge("4H", "trend", htf.get("trend_4h")),
        _gauge("15M", "fuel_verdict", fuel.get("verdict")),
        _gauge("15M", "fuel_push_ratio", (fuel.get("checks", {}).get("push_volume", {}) or {}).get("ratio")),
    ] if g]

    # --- hard vetoes -- cap below TAKE regardless of the gate ---
    daily_bias = (daily.get("policy") or {}).get("bias")
    counter_trend = bool(
        daily.get("quality") == "GOOD" and daily_bias and
        ((daily_bias == "UP" and side == _SHORT) or (daily_bias == "DOWN" and side == _LONG))
    )
    dead_tape = micro.get("regime") == "DEAD"
    fuel_verdict = fuel.get("verdict")
    no_fuel = fuel_verdict == "NO_FUEL"

    def _veto_gate(veto_reason: str) -> Dict[str, Any]:
        # Same shape as _core_gate()'s return, even though a veto short-
        # circuits before the 4-condition check -- KABRODA_REBUILD_SPEC.md §9
        # ("log every detail"): reachability/fuel/htf are still knowable here
        # and must not go missing from the record just because a veto fired.
        conflicted = fuel_verdict == "CONFLICTED"
        return {"pass": False, "tier": None, "reach": reach, "htf_aligned": htf.get("aligned"),
                "checks": {"reachability": reach["ok"], "fuel": fuel_verdict in ("FUELED", "CONFLICTED"),
                           "htf_carry": True if conflicted else (htf.get("aligned") or 0) >= 1,
                           "session_hour": session_hour_utc is None or session_hour_utc not in DEAD_HOURS},
                "misses": [veto_reason]}

    if dead_tape:
        return _result("PASS", side, f"{side}: 15M regime is DEAD -- no participation. Stand aside.",
                        _veto_gate("dead 15m tape"), plan, gauges)
    if counter_trend:
        return _result("PASS", side,
                        f"{side} against a {daily_bias} daily trend on a good table -- don't fight it.",
                        _veto_gate("counter-trend on a GOOD daily table"), plan, gauges)
    if no_fuel:
        return _result("PASS", side, f"{side}: ghost push -- no real volume behind the move. Stand down.",
                        _veto_gate("ghost push (NO_FUEL)"), plan, gauges)

    gate = _core_gate(box=box, atr=atr, fuel=fuel, htf=htf, session_hour=session_hour_utc)
    gauges = gauges + [g for g in [
        _gauge("15M", "gate_reachability_ok", gate["checks"]["reachability"]),
        _gauge("15M", "gate_fuel_ok", gate["checks"]["fuel"]),
        _gauge("15M", "gate_htf_carry_ok", gate["checks"]["htf_carry"]),
        _gauge("15M", "gate_session_hour_ok", gate["checks"]["session_hour"]),
        _gauge("15M", "gate_box_atr_ratio", (gate["reach"] or {}).get("ratio")),
        _gauge("15M", "gate_tier", gate["tier"]),
    ] if g]

    if gate["pass"]:
        if gate["tier"] == "PREMIUM":
            headline = (f"{side}: PREMIUM -- tight box, 5M fuel, BOTH higher timeframes carrying. "
                        "Size up, hold the runner to T3.")
            return _result("TAKE_PREMIUM", side, headline, gate, plan, gauges)
        headline = (f"{side}: box in reach, fuel live, a higher timeframe backs it. "
                    "Standard trade -- take it, normal size, runner to T3.")
        return _result("TAKE_STANDARD", side, headline, gate, plan, gauges)

    headline = f"{side}: PASS -- " + "; ".join(gate["misses"])
    return _result("PASS", side, headline, gate, plan, gauges)
