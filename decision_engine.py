# decision_engine.py
# ==============================================================================
# THE CALIBRATED GATE, v2 — Krown Cross + 4H RSI. Replaces the v1 fuel-based
# gate entirely.
#
# Rebuilt 2026-09-11 per CC_PACKAGE.md (Kabroda AI Brain repo), Andy's direct
# lock ("lock it for cc to look at it", 22:29 CT) after a full-day audit found
# the v1 gate's "fuel" signal (push volume vs a 24h baseline) is measured
# using ~60 minutes of data AFTER the entry fill -- not decision-time
# computable, confirmed in BOTH the calibration scripts and this file's own
# fuel_gate.py (see AGENT_LOG.md, Kabroda AI Brain repo, 2026-09-11). Every
# v1 number built on that gate (STANDARD_FUEL_RATIO_FLOOR, PROMOTED_PUSH_
# FLOOR, the PREMIUM/STANDARD tier split) inherited the same defect. v2 uses
# only signals knowable at the moment they're evaluated -- no volume, no
# forward window, no bail-if-wrong mechanism needed because nothing about
# the gate can turn out to have been wrong after the fact.
#
# THE v2 GATE (CC_PACKAGE.md §1, CANON.md §8) -- FOUR conditions, all must
# pass (source: d1_meas_base.build_base() + d1_meas6_combo.py, Kabroda AI
# Brain repo, CC-verified 2026-09-11 by directly running the measurement
# scripts, not just reading the printout -- n=136, avgR +0.5216 leg-1 /
# +0.6709 with SPLIT management, all 5 years positive, reproduced exactly):
#   1. reachability  -- box <= 0.55x daily ATR(14)              (reachability.py)
#   2. HTF aligned>=1 -- the OLD 9/21 EMA read (>=1 of 1H/4H backs the side).
#                        Real and load-bearing -- the n=136 population is
#                        built on TOP of this pre-filter, not independent of
#                        it (found while auditing the evidence package;
#                        CC_PACKAGE.md's own system paragraph omits this --
#                        flagged, not yet corrected there).      (htf_fuel.py)
#   3. Krown Cross votes==2 -- a SEPARATE, stricter 21/55 EMA stack + 6-bar
#                        slope, BOTH 1H and 4H must agree with the side.
#                        Not redundant with #2 -- the 9/21 and 21/55 pairs
#                        can and do disagree on some crosses.    (htf_fuel.py)
#   4. 4H RSI(14) Wilder in the control zone AT LOCK (LONG 62-80, SHORT
#                        20-38) -- evaluated once at the 13:00 UTC lock and
#                        frozen (battlebox_pipeline.py), NOT recomputed at
#                        cross/fill time. This is a deliberate asymmetry
#                        in the measured system (Krown Cross uses "current"
#                        candles at cross time; RSI uses the frozen lock
#                        read) -- do not "simplify" it into computing RSI
#                        fresh at cross time, that changes what was measured.
#
# WHAT v1 HAD THAT v2 DROPS:
#   - Fuel (fuel_gate.py) as a decision input. Retired with receipts -- see
#     the header comment above. fuel_gate.py itself is untouched (same
#     treatment as the Gravity Map / mtf_confluence_scanner.py: a real tool,
#     just not a decision input any more) in case something else reads it.
#   - The PREMIUM/STANDARD tier split, STANDARD_FUEL_RATIO_FLOOR,
#     PROMOTED_PUSH_FLOOR. All fuel-derived, all gone. One gate, one
#     population, one TAKE outcome now.
#   - The dead-hour / dead-tape / counter-trend veto stack. Measured
#     2026-09-11 against the REAL n=136 v2 candidate (not the old fuel-era
#     base) -- brain/audit_evidence/d0_veto_stack_on_candidate.py: counter-
#     trend never fires on this population (n=0); dead-hour has only 4
#     samples, too few to read either direction; dead-tape vetoes 26 real
#     trades averaging +0.35R (still solidly positive) to raise the kept-
#     108's avgR from +0.52 to +0.57 -- a real but modest frequency-for-
#     selectivity tradeoff, not the clear anti-filter the v1 vetoes were.
#     CC's call (documented CANON.md §8, reversible): skip it, the added
#     complexity doesn't clearly earn its keep here. market_regime.py and
#     micro_regime.py are still called and surfaced on the decision dict
#     for display/diagnostic purposes (same "kept, not a decision input"
#     treatment) -- just no longer vetoes.
#   - The old 2-consecutive-close acceptance gate AS THE ENTRY SIGNAL
#     (unchanged from v1's own header note, still true): the gate evaluates
#     on the FIRST 5m close beyond BO/BD, not a close count.
#
# ONE OUTCOME NOW, NOT THREE: TAKE / PASS. No PREMIUM, no STANDARD -- there
# is one population, sized and managed the same way for every trade
# (executor_sizing.py's banded rule already doesn't care about tier).
#
# No LLM call anywhere in this file. No prose generation beyond a plain-
# English headline built from the same booleans that decided the outcome.
# Cost is zero.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from harness.unified_audit_writer import gauge as _gauge

import htf_fuel as _htf_fuel
import market_regime as _market_regime
import micro_regime as _micro_regime
import reachability as _reachability

GaugeTuple = Tuple[str, str, Optional[float], Optional[str]]

_LONG, _SHORT = "LONG", "SHORT"

# 4H RSI(14) Wilder control zones (CC_PACKAGE.md §1, d1_meas6_combo.py's
# rsi4_at()). Evaluated against `levels["rsi_4h_at_lock"]` -- frozen at the
# 13:00 UTC lock by battlebox_pipeline.py, see this file's header comment.
RSI_ZONE_LONG = (62, 80)   # 62 <= r < 80
RSI_ZONE_SHORT = (20, 38)  # 20 <  r <= 38

# §6 management rule (v2) -- Andy's fib convention: anchor 0 = BD, 1.0 = BO.
# T1 moved 0.618 -> 1.0 box (CC_PACKAGE.md §1: measured better on both legs
# of the SPLIT management than the v1 0.618 anchor). No more T2/BE-at-T2 --
# the SPLIT rule never moves the stop, so T2_BOX is kept only as the price
# level t1 already sits at (T1_BOX_V2 == 1.0 == the old T2_BOX) for any
# caller that still reads a "t2" key; nothing acts on it as a management
# trigger any more.
T1_BOX, T2_BOX, T3_BOX = 1.0, 1.0, 1.618
STOP_BUFFER_BOX = 0.12      # unchanged from v1 -- same STOP_BUFFER_BOX/formula
SUBTRIG_BOX = 0.15          # runner stop after T1 (diagnostic only, see below)


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
        # incubation record) -- NOT the real management rule anymore, and
        # predates v2 too. Kept computed/logged so GateLog rows don't
        # silently lose a column the Brain repo may still read, but nothing
        # in the live management path acts on it.
        "subtrig_stop": round(float(subtrig_stop), 2),
        "box": round(box, 2),
        # v2: ONE management rule for every trade, no tier branching --
        # SPLIT 50/50 (CC_PACKAGE.md §1): 50% off at T1, 50% rides to T3,
        # stop never moves either leg. t2 == t1 now (see T1_BOX/T2_BOX
        # comment above); not displayed separately to avoid implying two
        # different prices exist.
        "management": (
            f"50% off at T1 {round(t1, 2):,.0f}, stop stays at the original level. "
            f"The other 50% rides to T3 {round(t3, 2):,.0f} or the same stop -- "
            "it never moves."
        ),
    }


def _core_gate(*, box: float, atr: float, cross: Dict[str, Any],
                htf: Dict[str, Any], rsi_4h_at_lock: Optional[float],
                side: str) -> Dict[str, Any]:
    """v2's 4-condition gate (CC_PACKAGE.md §1, CANON.md §8) -- see this
    file's header comment for the full rationale and evidence."""
    reach = _reachability.reachability(box, atr)
    aligned = htf.get("aligned") or 0
    htf_ok = aligned >= 1
    votes = cross.get("votes") or 0
    cross_ok = votes >= 2

    rsi_lo, rsi_hi = RSI_ZONE_LONG if side == _LONG else RSI_ZONE_SHORT
    if rsi_4h_at_lock is None:
        rsi_ok = False
    elif side == _LONG:
        rsi_ok = rsi_lo <= rsi_4h_at_lock < rsi_hi
    else:
        rsi_ok = rsi_lo < rsi_4h_at_lock <= rsi_hi

    checks = {"reachability": reach["ok"], "htf_aligned": htf_ok,
              "krown_cross": cross_ok, "rsi_4h_zone": rsi_ok}
    misses: List[str] = []
    if not reach["ok"]:
        misses.append(reach["note"])
    if not htf_ok:
        misses.append("neither 1H nor 4H backs the direction (no carry)")
    if not cross_ok:
        misses.append(f"Krown Cross votes={votes}/2 (need both 1H and 4H)")
    if not rsi_ok:
        rsi_text = f"{rsi_4h_at_lock:.1f}" if rsi_4h_at_lock is not None else "unavailable"
        misses.append(f"4H RSI at lock ({rsi_text}) outside the {rsi_lo}-{rsi_hi} control zone")

    passed = not misses
    return {"pass": passed, "checks": checks, "misses": misses, "reach": reach,
            "htf_aligned": aligned, "krown_cross_votes": votes, "rsi_4h_at_lock": rsi_4h_at_lock}


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
    cross: Optional[Dict[str, Any]] = None

    def _result(state: str, side: Optional[str], headline: str, gate: Optional[Dict[str, Any]],
                plan: Optional[Dict[str, Any]], gauges: List[GaugeTuple]) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
        is_take = state == "TAKE"
        d: Dict[str, Any] = {
            "approval_status": "APPROVED" if is_take else "STAND_DOWN",
            "conviction": state,          # kept for CampaignLog schema compatibility
            "verdict_state": state,
            "side": side,
            # v2 has no tier -- kept as a key (always None) so any consumer
            # still reading decision.get("tier") gets a clean None instead of
            # a KeyError while Phase 3 removes the remaining tier-branching
            # code in trade_plan.py/executor_live_engine.py/templates.
            "tier": None,
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
            # market_regime.py/micro_regime.py are no longer decision inputs
            # in v2 (see this file's header comment) but stay computed and
            # surfaced for display -- same "real tool, not a decision input"
            # treatment as the Gravity Map.
            "market_regime_table":   (daily or {}).get("table"),
            "market_regime_quality": (daily or {}).get("quality"),
            "micro_regime":          (micro or {}).get("regime"),
            # fuel_gate.py is retired from the decision entirely (v1's fuel
            # signal used ~60min of post-fill data -- not decision-time
            # computable, see header comment). Keys kept, always None, for
            # any consumer/schema still reading them.
            "fuel_verdict":   None,
            "fuel_push_ratio": None,
            "trend_1h": (htf or {}).get("trend_1h"),
            "trend_4h": (htf or {}).get("trend_4h"),
            "htf_aligned": (htf or {}).get("aligned"),
            "htf_opposed": (htf or {}).get("opposed"),
            # v2's own gate signals, newly surfaced.
            "krown_cross_votes": (cross or {}).get("votes"),
            "rsi_4h_at_lock": (gate or {}).get("rsi_4h_at_lock"),
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

    # market_regime.py/micro_regime.py: no longer vetoes in v2 (see header
    # comment + CANON.md §8's veto-stack measurement), still computed for
    # display -- same treatment as the Gravity Map.
    micro = _micro_regime.classify_regime(candles_15m)
    daily = _market_regime.classify_market_regime(candles_1d)
    htf = _htf_fuel.htf_fuel(candles_1h, candles_4h, side)
    cross = _htf_fuel.krown_cross_votes(candles_1h, candles_4h, side)
    rsi_4h_at_lock = levels.get("rsi_4h_at_lock")
    rsi_4h_at_lock = float(rsi_4h_at_lock) if rsi_4h_at_lock is not None else None

    plan = _plan_for_side(side, bo, bd, r30_high, r30_low) if box > 0 else None

    gauges = base_gauges + [g for g in [
        _gauge("15M", "regime", micro.get("regime")),
        _gauge("1D", "market_table", daily.get("table")),
        _gauge("1D", "market_quality", daily.get("quality")),
        _gauge("1H", "trend", htf.get("trend_1h")),
        _gauge("4H", "trend", htf.get("trend_4h")),
        _gauge("1H", "krown_cross", cross.get("cross_1h")),
        _gauge("4H", "krown_cross", cross.get("cross_4h")),
        _gauge("4H", "rsi_at_lock", rsi_4h_at_lock),
    ] if g]

    gate = _core_gate(box=box, atr=atr, cross=cross, htf=htf,
                       rsi_4h_at_lock=rsi_4h_at_lock, side=side)
    gauges = gauges + [g for g in [
        _gauge("15M", "gate_reachability_ok", gate["checks"]["reachability"]),
        _gauge("15M", "gate_htf_aligned_ok", gate["checks"]["htf_aligned"]),
        _gauge("15M", "gate_krown_cross_ok", gate["checks"]["krown_cross"]),
        _gauge("15M", "gate_rsi_zone_ok", gate["checks"]["rsi_4h_zone"]),
        _gauge("15M", "gate_box_atr_ratio", (gate["reach"] or {}).get("ratio")),
    ] if g]

    if gate["pass"]:
        headline = (f"{side}: Krown Cross both timeframes, 4H RSI in the control zone, box in reach. "
                    "Take it -- 50% off at T1, the rest rides to T3, stop never moves.")
        return _result("TAKE", side, headline, gate, plan, gauges)

    headline = f"{side}: PASS -- " + "; ".join(gate["misses"])
    return _result("PASS", side, headline, gate, plan, gauges)
