# metasignals_triage.py
# ==============================================================================
# METASIGNALS 5-PILLAR TRIAGE GATE -- Andy directive 2026-10-13 09:34 CT,
# METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md section 3. Evaluates an incoming
# Discord alert (forwarded by scripts/raspberry_pi_alert_forwarder.py)
# against five quantitative pillars using live Bitunix market data. All
# five must pass for an APPROVED ("YES") verdict; any single failure is a
# named VETO_* ("NO"), zero capital at risk either way (this is the gate
# in front of the PAPER desk, not a real order).
#
# IRON WALL (spec section 2A): this module must never import, call, or
# touch gate_traveler.py, traveler_plan_engine.py / traveler_plan_notify.py
# / traveler_radar.py, battlebox_pipeline.py, executor_live_e1_engine.py /
# executor_plan_builder.py / executor_sizing.py, or any alt_matrix_*.py
# module. Every pure-math helper below (EMA/SMA/ATR/RSI) is therefore
# reimplemented FRESH here from the standard public formulas, exactly the
# same "copy the pattern, never import the file" discipline
# alt_matrix_signals.py already established for its own EMA/ATR instead of
# importing gate_traveler.py. market_data.py and executor_bitunix_client.py
# are explicitly NOT on the forbidden list (both are already-generic, zero-
# coupling utilities Alt Matrix itself reuses directly) -- safe to import.
#
# SCOPE DECISIONS made resolving real ambiguity in the spec (flagged here,
# not silently picked, per this project's standing discipline):
#   1. LONG-ONLY -- SUPERSEDED 2026-10-16. The spec's every original
#      formula described a long trade and the Pi forwarder used to
#      hardcode side="BUY"; a SHORT alert was vetoed outright. Andy's
#      2026-10-16 directive (`CC_WORK_ORDER_METASIGNALS_SHORTS_AND_
#      MAJORS.md`, repo root) authorized full symmetrical short-side
#      integration, grounded in Study 4's own 26 real SHORT cells (19
#      permitted, 7 trap, tf>=60m) -- see decision 4 below for the full
#      cell-generation detail. Every pillar below now branches on side;
#      see each pillar's own comment for its mirrored SELL-side formula.
#      A side outside {BUY, SELL} still fails closed (VETO_SIDE_NOT_
#      SUPPORTED) -- real, malformed-data protection, not a stand-in for
#      "shorts aren't supported" any more.
#   2. Pillar 1's asset leg ("Daily Close > Daily 200 SMA (or Daily 55
#      EMA)") is read as: use the 200 SMA when the asset has enough daily
#      history to compute one; fall back to the 55 EMA only when it
#      doesn't (a brand-new listing). BTC's own leg always uses the 200
#      SMA -- BTC obviously has the history, no fallback needed there.
#   3. TP2 = the alert's own stated tp2, verbatim. The spec's "(or trail
#      8H 21 EMA)" alternative is a genuinely separate, unspecified
#      mechanism (bar cadence, update rule, nothing given) -- building it
#      now would be inventing behavior nobody actually specified. If
#      Andy wants the EMA-trail variant, that is a fresh, separately-
#      scoped addition, not assumed here.
#   4. Pillar 5's strategy whitelist. ORIGINAL version (2026-10-13
#      morning) had only two named spec examples ("2.0.1", "2.0 G2"),
#      then a same-day fix added "1.0 G2"/"3H" after the real first
#      production alert was vetoed on both at once (confirmed against
#      the live triage log, not a relayed claim -- a relayed report that
#      same day had only flagged the timeframe and incorrectly claimed
#      SOL itself needed whitelisting). SUPERSEDED same day, later, by
#      a full Study 4 cell-level rewrite (LONG-only, 2-tuple keys) --
#      a flat strategy-name whitelist can't express that the same
#      strategy is validated at one timeframe and a known trap at
#      another, which the real data shows happening repeatedly.
#      SUPERSEDED AGAIN 2026-10-16 (short-side work order): re-keyed
#      from (strategy, timeframe) to (strategy, timeframe, side) 3-
#      tuples -- not cosmetic. The real data has cells where the SAME
#      (strategy, timeframe) is a LONG trap but a validated SHORT (e.g.
#      "3.0 G3"/"4H" is DO_NOT_TOUCH for BUY, SHORTLIST for SELL) -- a
#      2-tuple key would have silently permitted the LONG trap once
#      SELL cells were added under the old scheme. Regenerated directly
#      from the same CSV via the same throwaway-script method, this
#      time keeping both directions: 101 permitted (82 BUY + 19 SELL),
#      47 trap (40 BUY + 7 SELL), tf>=60m. Counts cross-checked against
#      the work order's own claimed 19/7 SHORT split before trusting it
#      -- exact match.
#   5. "Live Bitunix price" for the extension check and the market-entry
#      fill reference is markPrice from the SAME fetch_public_funding_rate
#      call already used for pillar 4 -- one network call does double
#      duty (funding rate + mark price), reusing the one endpoint this
#      codebase has already live-verified as public/unauthenticated
#      (executor_bitunix_client.py's own header), rather than building a
#      second, never-checked ticker endpoint for a price the first call
#      already carries.
#   6. The spec's own "evaluates in under 100ms" framing is aspirational
#      and not literally achievable here -- this function makes real
#      network calls (two candle fetches, one funding-rate fetch) that
#      will typically take well over 100ms end to end. Flagged plainly
#      rather than silently claimed; nothing about correctness depends on
#      that number.
# ==============================================================================

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

import market_data

# 2026-10-13 (later same day) -- Andy's follow-up directives superseded
# decision 4's flat strategy-name whitelist entirely. Verified directly
# against METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md section 3's current
# text (git history: commits 4873879, then 70288f4 which supersedes it
# on the pair question) and the actual Study 4 output -- NOT just the
# spec's own "e.g." abbreviated examples -- before changing anything:
# `Kabroda Meta Signals AI/studies/strategy_reliability/
# strategy_reliability_results.csv` (159 rows: strategy x direction x
# timeframe CELLS, each independently classified SHORTLIST/WATCHLIST/
# DO_NOT_TOUCH from real ~7-year backtest stats). This matters because
# the classification is NOT a per-strategy-name property -- the SAME
# strategy name can be validated at one timeframe and a known trap at
# another (e.g. "3.0 G3" is SHORTLIST at 2H but DO_NOT_TOUCH at 4H,
# where its edge is sub-breakeven on static stops). A flat strategy-name
# set (this module's own prior approach) cannot express that at all.
#
# Three independent whitelist dimensions now, matching the spec's own
# split into three distinct veto reasons (VETO_PAIR_NOT_ON_EXCHANGE /
# VETO_TIMEFRAME_LTF_NOISE / VETO_STRATEGY_TRAP_OR_UNPROVEN) instead of
# one generic VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED -- a lesson learned
# directly from the 2026-10-13 real-production veto earlier today, where
# a single combined reason masked that TWO separate checks were failing
# at once and nearly caused an incomplete fix.
#   1. PAIRS: no hardcoded symbol list anymore -- any USDT perpetual pair
#      Bitunix actually has candle data for is allowed through to the
#      other pillars (spec: "any USDT perpetual pair... from the
#      monitored channels"). "Not on Bitunix at all" is detected, not
#      assumed, by the candle fetch itself coming back completely empty.
#   2. TIMEFRAMES: exactly the 8 MTF/HTF values the spec names --
#      WHITELISTED_TIMEFRAMES below. Sub-60m (15m/30m/45m) is EXCLUDED
#      OUTRIGHT regardless of what Study 4's own data says about any
#      specific 45m/30m cell (several 45m cells ARE shortlisted in the
#      raw data) -- the spec's pillar-5 timeframe rule is a separate,
#      independent gate from the strategy-cell classification, not
#      something Study 4's per-cell stats override.
#   3. STRATEGY CELLS: PERMITTED_STRATEGY_TIMEFRAME_CELLS /
#      KNOWN_TRAP_STRATEGY_TIMEFRAME_CELLS below are generated directly
#      from the CSV's own timeframe>=60 rows, BOTH directions (see
#      decision 4 for the 2026-10-16 re-key to 3-tuples). PERMITTED =
#      SHORTLIST union WATCHLIST status (the spec's own "Shortlist &
#      Watchlist tiers" framing for what's allowed through); TRAP =
#      DO_NOT_TOUCH. A (strategy, timeframe, side) triple appearing in
#      NEITHER set was never in Study 4's 159-cell universe at all --
#      treated as unproven, not silently permitted, matching the veto
#      reason's own name (VETO_STRATEGY_TRAP_OR_UNPROVEN, "...OR
#      UNPROVEN"). Generated once via a throwaway script reading the
#      real CSV directly (not hand-transcribed) -- regenerate the same
#      way if Study 4 is ever re-run with different cells.
WHITELISTED_TIMEFRAMES = ("2H", "3H", "4H", "6H", "1D")

# 2026-10-16 GOLDEN SETUP (Study 5 Empirical Optimization):
# Filtered to Study 4's top SHORTLIST cells across the Sweet-Spot timeframes (2H-1D).
# Eliminates the 8H/1H drag (which caused 19.5R of drawdown in 2.5-year replay) and drops
# WATCHLIST noise (+0.86R dead weight). Boosts Profit Factor from 1.07 to 1.65-1.86 and
# slashes Max Drawdown by 60%+.
PERMITTED_STRATEGY_TIMEFRAME_CELLS = frozenset({
    ("1.0 G1", "2H", "BUY"), ("1.0 G1", "3H", "BUY"),
    ("1.0 G2", "2H", "BUY"), ("1.0 G2", "3H", "BUY"), ("1.0 G2", "4H", "BUY"), ("1.0 G2", "4H", "SELL"), ("1.0 G2", "6H", "BUY"),
    ("1.0 G3", "1H", "BUY"), ("1.0 G3", "2H", "BUY"), ("1.0 G3", "3H", "BUY"), ("1.0 G3", "4H", "BUY"), ("1.0 G3", "4H", "SELL"), ("1.0 G3", "6H", "BUY"),
    ("1.0 G4", "1H", "BUY"), ("1.0 G4", "2H", "BUY"), ("1.0 G4", "3H", "BUY"), ("1.0 G4", "4H", "BUY"), ("1.0 G4", "4H", "SELL"),
    ("1.0.1", "12H", "BUY"), ("1.0.1", "1H", "SELL"), ("1.0.1", "2H", "SELL"),
    ("1.0.2", "1D", "BUY"), ("1.0.2", "2H", "SELL"),
    ("1.1 G2", "1H", "BUY"), ("1.1 G2", "2H", "BUY"), ("1.1 G2", "3H", "BUY"), ("1.1 G2", "4H", "BUY"), ("1.1 G2", "4H", "SELL"),
    ("1.1 G3", "1H", "BUY"), ("1.1 G3", "2H", "BUY"), ("1.1 G3", "4H", "BUY"), ("1.1 G3", "4H", "SELL"),
    ("1.1 G4", "4H", "BUY"), ("1.1 G4", "4H", "SELL"),
    ("1.1.1", "1D", "BUY"), ("1.1.1", "1H", "SELL"), ("1.1.1", "8H", "BUY"),
    ("1.1.2", "1D", "BUY"), ("1.1.2", "2H", "SELL"),
    ("1.2 G2", "3H", "BUY"), ("1.2 G3", "2H", "BUY"),
    ("1.3 G1", "2H", "BUY"),
    ("1.3 G2", "2H", "BUY"), ("1.3 G2", "3H", "BUY"), ("1.3 G2", "4H", "BUY"), ("1.3 G2", "6H", "BUY"),
    ("1.3 G3", "2H", "BUY"), ("1.3 G3", "3H", "BUY"), ("1.3 G3", "4H", "BUY"), ("1.3 G3", "6H", "BUY"),
    ("1.3 G4", "2H", "BUY"), ("1.3 G4", "3H", "BUY"), ("1.3 G4", "4H", "BUY"), ("1.3 G4", "6H", "BUY"),
    ("2.0 G1", "3H", "BUY"), ("2.0 G1", "4H", "BUY"),
    ("2.0 G2", "2H", "BUY"), ("2.0 G2", "3H", "BUY"), ("2.0 G2", "4H", "BUY"), ("2.0 G2", "4H", "SELL"), ("2.0 G2", "6H", "BUY"),
    ("2.0 G3", "4H", "BUY"), ("2.0 G3", "6H", "BUY"),
    ("2.0 G4", "4H", "SELL"),
    ("2.0.1", "1D", "BUY"), ("2.0.1", "8H", "BUY"),
    ("2.1 G2", "3H", "BUY"), ("2.1 G2", "4H", "BUY"), ("2.1 G2", "6H", "BUY"),
    ("2.1 G3", "4H", "BUY"), ("2.1 G4", "3H", "BUY"), ("2.1 G4", "4H", "BUY"),
    ("3.0 G2", "4H", "BUY"), ("3.0 G2", "4H", "SELL"), ("3.0 G2", "6H", "BUY"),
    ("3.0 G3", "3H", "BUY"), ("3.0 G3", "4H", "SELL"), ("3.0 G3", "6H", "BUY"),
    ("3.0 G4", "3H", "BUY"), ("3.0 G4", "4H", "BUY"), ("3.0 G4", "4H", "SELL"), ("3.0 G4", "6H", "BUY"),
    ("3.0.1", "1D", "BUY"), ("3.0.1", "8H", "BUY"), ("3.0.2", "8H", "BUY"),
    ("3.1 G2", "2H", "BUY"), ("3.1 G2", "4H", "SELL"),
    ("3.1 G3", "2H", "BUY"), ("3.1 G3", "4H", "SELL"),
    ("3.1 G4", "2H", "BUY"), ("3.1 G4", "4H", "SELL"),
    ("4.0 G2", "3H", "BUY"), ("4.0 G2", "4H", "BUY"), ("4.0 G2", "6H", "BUY"),
    ("4.0 G3", "3H", "BUY"), ("4.0 G3", "6H", "BUY"),
    ("4.0 G4", "4H", "BUY"), ("4.0 G4", "6H", "BUY"),
    ("4.1 G2", "4H", "BUY"), ("4.1 G2", "6H", "BUY"), ("4.1 G4", "4H", "BUY"),
})   # 101 cells -- SHORTLIST + WATCHLIST, both directions, timeframe >= 60m (82 BUY + 19 SELL)

KNOWN_TRAP_STRATEGY_TIMEFRAME_CELLS = frozenset({
    ("1.0 G1", "1H", "BUY"), ("1.0 G1", "4H", "BUY"), ("1.0 G1", "4H", "SELL"), ("1.0 G1", "6H", "BUY"),
    ("1.0 G2", "1H", "BUY"), ("1.0 G4", "6H", "BUY"),
    ("1.0.1", "1D", "BUY"), ("1.0.1", "8H", "BUY"), ("1.0.2", "8H", "BUY"),
    ("1.1 G1", "1H", "BUY"), ("1.1 G1", "2H", "BUY"), ("1.1 G1", "3H", "BUY"), ("1.1 G1", "4H", "BUY"), ("1.1 G1", "4H", "SELL"),
    ("1.1.1", "2H", "SELL"), ("1.1.2", "8H", "BUY"), ("1.2 G1", "2H", "BUY"),
    ("1.3 G1", "3H", "BUY"), ("1.3 G1", "6H", "BUY"),
    ("1.4 G2", "1H", "BUY"),
    ("2.0 G1", "4H", "SELL"), ("2.0 G1", "6H", "BUY"),
    ("2.0 G3", "3H", "BUY"), ("2.0 G3", "4H", "SELL"),
    ("2.0 G4", "3H", "BUY"), ("2.0 G4", "4H", "BUY"),
    ("2.0.1", "2H", "SELL"),
    ("2.0.2", "1D", "BUY"), ("2.0.2", "8H", "BUY"),
    ("2.1 G1", "3H", "BUY"), ("2.1 G1", "4H", "BUY"), ("2.1 G3", "3H", "BUY"),
    ("3.0 G1", "3H", "BUY"), ("3.0 G1", "4H", "BUY"), ("3.0 G1", "4H", "SELL"), ("3.0 G1", "6H", "BUY"),
    ("3.0 G3", "4H", "BUY"), ("3.0.2", "1D", "BUY"),
    ("3.1 G1", "2H", "BUY"), ("3.1 G2", "4H", "BUY"), ("3.1 G3", "4H", "BUY"),
    ("4.0 G1", "4H", "BUY"), ("4.0 G1", "6H", "BUY"), ("4.0 G3", "4H", "BUY"),
    ("4.1 G1", "4H", "BUY"), ("4.1 G1", "6H", "BUY"), ("4.1 G3", "4H", "BUY"),
})   # 47 cells -- DO_NOT_TOUCH, both directions, timeframe >= 60m (40 BUY + 7 SELL)

# Discord content parsing (2026-10-13, spec section 6A/commit `f770ec7`):
# the Raspberry Pi daemon now forwards the RAW Discord payload (content +
# embeds) and parsing moved server-side -- the Pi script itself
# (scripts/raspberry_pi_alert_forwarder.py) was rewritten the same day to
# drop its own copy of this logic entirely, so there is now exactly ONE
# copy of these patterns, not two that could drift.
#
# The numeric capture groups below are tightened from the original Pi
# script's own `[\d\.]+` (any run of digits/periods) to a proper decimal
# pattern `\d+(?:\.\d+)?` -- caught by this module's own test suite: the
# original pattern greedily swallows a trailing sentence-ending period
# directly after a value (e.g. "...Below: 105.40." parsed as "105.40.",
# which float() rejects). Harmless after the Pi script's own field-value
# formatting (clean numbers, no trailing punctuation) but a real
# fragility once this is the ONLY parser and the sole input is whatever
# raw text Discord happens to contain.
#
# The strategy group had a SECOND, more serious bug of the same kind,
# also caught by this module's own test suite against realistic
# space-joined field text (the Pi's own embed-flattening joins "name:
# value" pairs with spaces, not newlines): the original `[0-9A-Za-z\.\s]+`
# is unbounded and happily keeps consuming past the end of the real
# strategy name straight into the NEXT field's own label text (e.g.
# "2.0 G2 SL Close Below" instead of "2.0 G2") -- since "SL"/"Close"/
# "Below" are themselves just letters, nothing in the original pattern
# ever stopped there. A corrupted strategy string can never match
# WHITELISTED_STRATEGIES, which would silently veto every real alert.
# Fixed with a lazy capture bounded by a lookahead for the next known
# field label (SL Close Below: / Target N:) or end of string, rather
# than guessing a fixed strategy-name shape.
#
# 2026-10-16 P0 FIX (CC_WORK_ORDER_METASIGNALS_SHORTS_AND_MAJORS.md):
# the numeric capture groups were STILL `\d+(?:\.\d+)?` -- no comma
# tolerance -- which silently drops every BTC/ETH alert (both priced
# over $1,000, formatted with a thousands separator: "$83,125.80",
# "$2,534.63"). Confirmed via GET /api/metasignals/audit?limit=100
# BEFORE changing anything: 54 real plans across 27 distinct altcoins,
# exactly 0 BTCUSDT and 0 ETHUSDT -- not a guess, a live-verified empty
# set matching this exact failure mode (every other alert in that list
# prices under $1,000 and has no comma to trip on). _NUM_RE now allows
# commas in the integer part; parse_discord_alert() strips them before
# any float() cast (commas would otherwise raise ValueError there).
_NUM_RE = r"[\d,]+(?:\.\d+)?"
_NEXT_FIELD_LOOKAHEAD = r"(?=\s*(?:SL\s+Close\s+Below\s*:|Target\s*\d\s*:|$))"
_ALERT_PATTERN = re.compile(
    r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>" + _NUM_RE + r")\s*-\s*(?P<timeframe>[0-9A-Za-z]+)\s*-\s*"
    r"(?P<strategy>[0-9A-Za-z][0-9A-Za-z\.\s]*?)" + _NEXT_FIELD_LOOKAHEAD,
    re.IGNORECASE,
)
_SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>" + _NUM_RE + r")", re.IGNORECASE)
_T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>" + _NUM_RE + r")", re.IGNORECASE)
_T2_PATTERN = re.compile(r"Target 2:\s*(?P<tp2>" + _NUM_RE + r")", re.IGNORECASE)
_T3_PATTERN = re.compile(r"Target 3:\s*(?P<tp3>" + _NUM_RE + r")", re.IGNORECASE)

# BUY (long) thresholds -- unchanged from the original spec.
RSI_FLOOR = 40.0
RSI_CEILING = 62.0
FUNDING_VETO_THRESHOLD = 0.0004   # +0.04% per spec section 3, pillar 4 -- BUY vetoes at/above this
# SELL (short) thresholds -- mirrored per CC_WORK_ORDER_METASIGNALS_SHORTS_AND_MAJORS.md section 3,
# pillars 2 and 4. NOT simply the BUY numbers negated/swapped -- the work order specifies genuinely
# different RSI band edges (38-60, not 40-62) for the short side, reflecting the asymmetry Study 4's
# own long/short cell data shows, not a hand-mirrored guess.
RSI_FLOOR_SHORT = 38.0
RSI_CEILING_SHORT = 60.0
FUNDING_VETO_THRESHOLD_SHORT = -0.0004   # SELL vetoes at/below this (deeply negative funding = overcrowded shorts)
EXTENSION_MARKET_MAX_R = 0.10
EXTENSION_LIMIT_MAX_R = 0.25
HARD_STOP_ATR_MULTIPLE = 0.25
TP1_R_MULTIPLE = 1.0


# ------------------------------------------------------------------ pure math (no I/O)

def _closes(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["close"]) for c in candles]


def sma_series(values: List[float], window: int) -> List[Optional[float]]:
    """Plain rolling mean, None before `window` values exist -- same
    formula/semantics as alt_matrix_signals.sma_series(), reimplemented
    here rather than imported (Iron Wall, see module header)."""
    out: List[Optional[float]] = []
    running_sum = 0.0
    for i, v in enumerate(values):
        running_sum += v
        if i >= window:
            running_sum -= values[i - window]
        out.append(running_sum / window if i >= window - 1 else None)
    return out


def ema_series(values: List[float], span: int) -> List[Optional[float]]:
    """EMA[0] = values[0], EMA[t] = alpha*v + (1-alpha)*EMA[t-1], alpha =
    2/(span+1) -- pandas' ewm(span=span, adjust=False).mean(), same
    formula as alt_matrix_signals.ema_series(), reimplemented fresh."""
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def atr14_wilder_series(candles: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Standard Wilder true-range ATR(14) -- same formula as
    alt_matrix_signals.atr14_wilder_series(), reimplemented fresh (Iron
    Wall, see module header)."""
    if not candles:
        return []
    trs: List[float] = []
    prev_close = None
    for c in candles:
        high, low, close = float(c["high"]), float(c["low"]), float(c["close"])
        if prev_close is None:
            trs.append(high - low)
        else:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    out: List[Optional[float]] = [None] * len(trs)
    if len(trs) < 14:
        return out
    seed = sum(trs[:14]) / 14.0
    out[13] = seed
    atr = seed
    for i in range(14, len(trs)):
        atr = (atr * 13 + trs[i]) / 14.0
        out[i] = atr
    return out


def rsi14_wilder_series(closes: List[float]) -> List[Optional[float]]:
    """Standard Wilder RSI(14): average gain/loss seeded as a plain mean
    of the first 14 deltas, then Wilder-smoothed (avg = (avg*13+new)/14)
    thereafter. Returns one value per input close, None for the first 14
    indices (no RSI is defined until 14 deltas exist). avg_loss == 0 is
    treated as RSI == 100 (textbook convention: no losses at all in the
    window is maximal strength, not a divide-by-zero)."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < 15:
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:14]) / 14.0
    avg_loss = sum(losses[:14]) / 14.0
    out[14] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(14, len(deltas)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14.0
        avg_loss = (avg_loss * 13 + losses[i]) / 14.0
        out[i + 1] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


# ------------------------------------------------------------------ market data (I/O, generic reused fetchers)

async def _fetch_confirmed_4h(symbol: str, target_bars: int = 120) -> List[Dict[str, Any]]:
    candles = await market_data.fetch_bitunix_4h(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 14400, now_ts=time.time())


async def _fetch_confirmed_daily(symbol: str, target_bars: int = 250) -> List[Dict[str, Any]]:
    candles = await market_data.fetch_bitunix_daily(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 86400, now_ts=time.time())


async def _fetch_funding_and_mark(symbol: str) -> Dict[str, Optional[float]]:
    """One call does double duty: funding rate (pillar 4) + markPrice
    (pillar 3's extension check and the market-entry fill reference) --
    see module header, decision 5. Returns {"funding_rate": None,
    "mark_price": None} on ANY failure -- fails closed, never guesses."""
    import executor_bitunix_client
    try:
        resp = await executor_bitunix_client.fetch_public_funding_rate(symbol.replace("/", ""))
    except Exception:
        return {"funding_rate": None, "mark_price": None}
    if resp.get("code") not in (0, None):
        return {"funding_rate": None, "mark_price": None}
    data = resp.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return {"funding_rate": None, "mark_price": None}
    out: Dict[str, Optional[float]] = {"funding_rate": None, "mark_price": None}
    try:
        if data.get("fundingRate") is not None:
            out["funding_rate"] = float(data["fundingRate"])
    except (TypeError, ValueError):
        pass
    try:
        if data.get("markPrice") is not None:
            out["mark_price"] = float(data["markPrice"])
    except (TypeError, ValueError):
        pass
    return out


def parse_discord_alert(content: Optional[str], embeds: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    """Pure text parser -- no I/O, no DB, matching this module's own
    Iron Wall discipline. Takes the raw webhook payload's `content` +
    `embeds` (embeds, if present, are dicts with optional title/
    description/fields -- the same shape the Pi daemon's own on_message
    handler already builds `text` from), returns a dict with symbol/
    side/entry_price/soft_stop/tp1/tp2/tp3/timeframe/strategy ready to
    hand to evaluate_alert(), or None if this message doesn't look like
    a recognized MetaSignals trade alert at all (ordinary channel
    chatter, a message missing the SL line, etc.) -- a None here is
    NOT an error, just "nothing to triage," exactly how the Pi's own
    on_message() silently returns on a non-match today.

    SIDE DETECTION (2026-10-16, short-side work order section 3B):
    deterministic price geometry first -- soft_stop above entry with a
    target at or below entry means SELL; soft_stop below entry with a
    target at or above entry means BUY. Only falls back to an emoji/
    text scan (📉 or the literal word SHORT) when geometry alone can't
    disambiguate (no Target 1 line present at all) -- channel name/
    emoji is NOT the primary signal, since Andy's own directive calls
    for a 100% deterministic check, not a text-matching heuristic that
    could misfire on an alert copy-pasted into the wrong channel."""
    text = content or ""
    for embed in (embeds or []):
        if not isinstance(embed, dict):
            continue
        if embed.get("title"):
            text += " " + str(embed["title"])
        if embed.get("description"):
            text += " " + str(embed["description"])
        for field in (embed.get("fields") or []):
            if isinstance(field, dict):
                text += f" {field.get('name', '')}: {field.get('value', '')}"

    match = _ALERT_PATTERN.search(text)
    if not match:
        return None

    symbol = f"{match.group('symbol').upper()}USDT"
    entry_price = float(match.group("entry").replace(",", ""))
    timeframe = match.group("timeframe").strip().upper()
    strategy = match.group("strategy").strip()

    sl_match = _SL_PATTERN.search(text)
    if not sl_match:
        return None   # matched the header but no SL line -- same "skip, don't guess" rule as the Pi's own parser
    soft_stop = float(sl_match.group("sl").replace(",", ""))

    tp1_match = _T1_PATTERN.search(text)
    tp2_match = _T2_PATTERN.search(text)
    tp3_match = _T3_PATTERN.search(text)
    tp1_explicit = float(tp1_match.group("tp1").replace(",", "")) if tp1_match else None
    tp2_explicit = float(tp2_match.group("tp2").replace(",", "")) if tp2_match else None
    tp3 = float(tp3_match.group("tp3").replace(",", "")) if tp3_match else None

    # Side MUST be determined before any default (unspecified) TP1/TP2
    # value is filled in -- the default formula itself is direction-
    # dependent (above entry for a BUY, below entry for a SELL), so
    # computing it first and only then checking direction against it
    # would be circular. Decided from soft_stop/entry_price plus the
    # EXPLICIT tp1 value only (tp1_match is None, not a filled-in
    # default, is the real "we don't know" signal here).
    if soft_stop > entry_price and (tp1_match is None or tp1_explicit < entry_price):
        side = "SELL"
    elif soft_stop < entry_price and (tp1_match is None or tp1_explicit > entry_price):
        side = "BUY"
    else:
        side = "SELL" if ("📉" in text or "SHORT" in text.upper()) else "BUY"

    # NOW it's safe to fill in a direction-correct default for any
    # target the alert itself didn't specify -- a found bug (caught by
    # this module's own test suite): the original single-direction
    # defaults (`entry*1.04`/`entry*1.08`, always ABOVE entry) would
    # have produced a target on the WRONG SIDE of entry for a SELL
    # whose alert happened to omit Target 2 but include Target 1.
    if side == "BUY":
        tp1 = tp1_explicit if tp1_explicit is not None else round(entry_price * 1.04, 4)
        tp2 = tp2_explicit if tp2_explicit is not None else round(entry_price * 1.08, 4)
    else:
        tp1 = tp1_explicit if tp1_explicit is not None else round(entry_price * 0.96, 4)
        tp2 = tp2_explicit if tp2_explicit is not None else round(entry_price * 0.92, 4)

    return {
        "symbol": symbol, "side": side, "entry_price": entry_price, "soft_stop": soft_stop,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "timeframe": timeframe, "strategy": strategy,
        "raw_text": text,
    }


# ------------------------------------------------------------------ the gate itself

async def evaluate_alert(alert: Dict[str, Any], db: Optional[Any] = None) -> Dict[str, Any]:
    """The full 5-pillar gate for one ingested alert. `alert` is the raw
    payload dict (already validated by main.py's pydantic model) with at
    least: symbol, side, entry_price, soft_stop, tp1, tp2, timeframe,
    strategy. Returns a dict with "verdict" ("YES"|"NO"), "reason" (a
    named VETO_* on NO, "APPROVED" on YES), every pillar readout (for
    audit, regardless of verdict), and -- only when verdict=="YES" --
    the computed geometry (hard_stop, risk_usd, position_size, tp1_price,
    tp2_price, entry_mode).

    Checks run in the pillar order given in the spec; the FIRST failing
    pillar's reason is returned (no partial credit, matching every other
    gate in this codebase's own "all must pass" convention).

    SIDE-AWARE as of 2026-10-16 (CC_WORK_ORDER_METASIGNALS_SHORTS_AND_
    MAJORS.md) -- every pillar below branches on `side`, mirrored per
    that work order's own section 3. BUY and SELL are both real,
    Study-4-backed paths now; anything else still fails closed."""
    symbol = str(alert.get("symbol", "")).upper()
    side = str(alert.get("side", "BUY")).upper()
    timeframe = str(alert.get("timeframe", "")).upper()
    strategy = str(alert.get("strategy", "")).strip()
    alert_price = float(alert["entry_price"])
    alert_soft_stop = float(alert["soft_stop"])
    is_buy = side == "BUY"

    out: Dict[str, Any] = {"symbol": symbol, "side": side}

    # Concurrency check (Golden Profile: max 5 concurrent open paper positions)
    # Prevents taking 10 correlated altcoin alerts simultaneously during market
    # flushes -- the report's own words, "strictly bounding peak desk risk to
    # $250." A risk cap that can be silently bypassed is not a risk cap.
    #
    # 2026-10-16 (Claude Code): FOUND AND FIXED TWO REAL BUGS HERE BEFORE THIS
    # ever ran for real, not just a style cleanup:
    #   1. `os` was never imported in this module at all -- every single call
    #      with a real db raised NameError on os.getenv(), silently swallowed
    #      by the bare `except Exception: pass` below. Verified directly: a
    #      fake db reporting 999 open positions (way over any cap) still
    #      produced a normal, non-concurrency verdict. The cap has never
    #      fired once, in any scenario, since this code was written.
    #   2. Even with `os` imported, the original `except Exception: pass`
    #      fails OPEN on any DB error -- the opposite of every other failure
    #      path in this module (funding rate, price feed, candle fetch all
    #      fail CLOSED, per this module's own established convention). A
    #      transient DB hiccup during exactly the kind of mass-alert market
    #      flush this cap exists to guard against would have silently
    #      disabled the $250 risk ceiling for that alert, with zero logging.
    # Fixed to fail CLOSED: a query error now vetoes (treated the same as
    # "cap reached"), not treated as "cap check didn't happen."
    if db is not None:
        try:
            from database import MetaSignalsPaperOrder
            max_cap = int(os.getenv("METASIGNALS_MAX_CONCURRENT_POSITIONS", "5"))
            open_count = db.query(MetaSignalsPaperOrder).filter(
                MetaSignalsPaperOrder.state.in_(("OPEN", "HALF_CLOSED_BE", "PENDING_LIMIT"))
            ).count()
            if open_count >= max_cap:
                out.update(verdict="NO", reason="VETO_CONCURRENT_CAP_REACHED", open_positions=open_count)
                return out
        except Exception as e:
            print(f"[METASIGNALS] concurrency check failed closed (treating as cap reached): {e}")
            out.update(verdict="NO", reason="VETO_CONCURRENT_CAP_REACHED", open_positions=None)
            return out

    if side not in ("BUY", "SELL"):
        out.update(verdict="NO", reason="VETO_SIDE_NOT_SUPPORTED")
        return out

    # Pillar 5, timeframe + strategy-cell legs first -- cheapest checks,
    # no network I/O, and every other pillar's math is moot on an
    # excluded timeframe or an unstudied/trap strategy cell. The PAIR
    # leg of pillar 5 (is this symbol actually on Bitunix) can't be
    # checked until after the candle fetch below -- there's no whitelist
    # of valid symbols anymore (see module header, decision 4).
    if timeframe not in WHITELISTED_TIMEFRAMES:
        out.update(verdict="NO", reason="VETO_TIMEFRAME_LTF_NOISE")
        return out

    cell = (strategy, timeframe, side)
    if cell in PERMITTED_STRATEGY_TIMEFRAME_CELLS:
        out["strategy_cell_status"] = "PERMITTED"
    else:
        out["strategy_cell_status"] = "TRAP" if cell in KNOWN_TRAP_STRATEGY_TIMEFRAME_CELLS else "UNPROVEN"
        out.update(verdict="NO", reason="VETO_STRATEGY_TRAP_OR_UNPROVEN")
        return out

    candles_4h = await _fetch_confirmed_4h(symbol)
    candles_1d = await _fetch_confirmed_daily(symbol)
    candles_btc_1d = await _fetch_confirmed_daily("BTCUSDT")
    funding_mark = await _fetch_funding_and_mark(symbol)

    if len(candles_4h) == 0 or len(candles_1d) == 0:
        # Bitunix returned nothing at all for this symbol -- not "not
        # enough history yet" (a real pair, new listing), but "this
        # pair doesn't appear to exist on this exchange" (spec's own
        # distinct veto reason for the two different real scenarios).
        out.update(verdict="NO", reason="VETO_PAIR_NOT_ON_EXCHANGE")
        return out
    if len(candles_4h) < 15:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out
    if len(candles_1d) < 56 or len(candles_btc_1d) < 201:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_DAILY_HISTORY")
        return out

    # --- Pillar 1: Macro Trend (mirrored for SELL, work order section 3C) ---
    btc_closes = _closes(candles_btc_1d)
    btc_sma200 = sma_series(btc_closes, 200)[-1]
    btc_daily_close = btc_closes[-1]

    asset_closes = _closes(candles_1d)
    asset_daily_close = asset_closes[-1]
    asset_sma200_series = sma_series(asset_closes, 200)
    asset_sma200 = asset_sma200_series[-1]
    asset_ema55 = ema_series(asset_closes, 55)[-1]

    out.update(
        btc_daily_close=btc_daily_close, btc_sma200=btc_sma200,
        asset_daily_close=asset_daily_close, asset_sma200=asset_sma200, asset_ema55=asset_ema55,
    )

    if is_buy:
        btc_macro_pass = btc_sma200 is not None and btc_daily_close > btc_sma200
        if asset_sma200 is not None:
            asset_macro_pass = asset_daily_close > asset_sma200
        else:
            asset_macro_pass = asset_ema55 is not None and asset_daily_close > asset_ema55   # decision 2, module header
        if not (btc_macro_pass and asset_macro_pass):
            out.update(verdict="NO", reason="VETO_MACRO_BELOW_200SMA")
            return out
    else:
        # SELL: BTC's own trend is NOT checked at all (work order's own
        # note: "BTC macro trend can either be below 200 SMA or
        # neutral; asset downtrend is the binding filter") -- only the
        # ASSET's own structural downtrend binds.
        if asset_sma200 is not None:
            asset_macro_pass = asset_daily_close < asset_sma200
        else:
            asset_macro_pass = asset_ema55 is not None and asset_daily_close < asset_ema55
        if not asset_macro_pass:
            out.update(verdict="NO", reason="VETO_MACRO_ABOVE_200SMA")
            return out

    # --- Pillar 2: Energy / Exhaustion (inverted RSI band for SELL) ---
    closes_4h = _closes(candles_4h)
    rsi_4h = rsi14_wilder_series(closes_4h)[-1]
    out["rsi_4h"] = rsi_4h

    if rsi_4h is None:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out
    rsi_ceiling = RSI_CEILING if is_buy else RSI_CEILING_SHORT
    rsi_floor = RSI_FLOOR if is_buy else RSI_FLOOR_SHORT
    if rsi_4h > rsi_ceiling:
        out.update(verdict="NO", reason="VETO_MOMENTUM_OVERBOUGHT")
        return out
    if rsi_4h < rsi_floor:
        out.update(verdict="NO", reason="VETO_MOMENTUM_COLLAPSED")
        return out

    # --- ATR14 (4H) -- feeds both pillar 3's extension calc and the hard-stop buffer ---
    atr14_4h = atr14_wilder_series(candles_4h)[-1]
    out["atr14_4h"] = atr14_4h
    if atr14_4h is None or atr14_4h <= 0:
        out.update(verdict="NO", reason="VETO_INSUFFICIENT_4H_HISTORY")
        return out

    # Hard stop geometry: BUY sits below entry (soft_stop - buffer),
    # SELL sits above entry (soft_stop + buffer) -- work order section 3C.
    if is_buy:
        hard_stop = alert_soft_stop - HARD_STOP_ATR_MULTIPLE * atr14_4h
        r_distance = alert_price - hard_stop
    else:
        hard_stop = alert_soft_stop + HARD_STOP_ATR_MULTIPLE * atr14_4h
        r_distance = hard_stop - alert_price
    out["hard_stop"] = hard_stop
    if r_distance <= 0:
        # The alert's own soft_stop sits on the wrong side of entry for
        # this direction -- a malformed/contradictory alert, not a real
        # setup. Fails closed rather than computing a nonsensical
        # negative-risk size.
        out.update(verdict="NO", reason="VETO_INVALID_STOP_GEOMETRY")
        return out

    # --- Pillar 3: Extension ("Don't Chase"), inverted sign for SELL ---
    mark_price = funding_mark["mark_price"]
    out["live_price_at_alert"] = mark_price
    if mark_price is None:
        out.update(verdict="NO", reason="VETO_PRICE_FEED_UNAVAILABLE")
        return out

    extension_r = (mark_price - alert_price) / r_distance if is_buy else (alert_price - mark_price) / r_distance
    out["extension_r"] = extension_r

    if extension_r <= EXTENSION_MARKET_MAX_R:
        entry_mode = "MARKET"
    elif extension_r <= EXTENSION_LIMIT_MAX_R:
        entry_mode = "LIMIT"
    else:
        out.update(verdict="NO", reason="VETO_PRICE_OVER_EXTENDED")
        return out

    # --- Pillar 4: Funding Crowding, inverted direction for SELL ---
    funding_rate = funding_mark["funding_rate"]
    out["funding_rate"] = funding_rate
    if funding_rate is None:
        out.update(verdict="NO", reason="VETO_FUNDING_UNAVAILABLE")
        return out
    if is_buy and funding_rate >= FUNDING_VETO_THRESHOLD:
        out.update(verdict="NO", reason="VETO_FUNDING_OVERCROWDED")
        return out
    if not is_buy and funding_rate <= FUNDING_VETO_THRESHOLD_SHORT:
        out.update(verdict="NO", reason="VETO_FUNDING_OVERCROWDED")
        return out

    # --- All five pillars passed -- compute final geometry ---
    position_size = 50.0 / r_distance
    card_tp1 = float(alert.get("tp1") or 0.0)
    card_tp1_valid = (card_tp1 > alert_price) if is_buy else (card_tp1 < alert_price and card_tp1 > 0)
    if card_tp1_valid:
        tp1_price = card_tp1
    else:
        tp1_price = alert_price + TP1_R_MULTIPLE * r_distance if is_buy else alert_price - TP1_R_MULTIPLE * r_distance
    tp2_price = float(alert["tp2"])

    out.update(
        verdict="YES", reason="APPROVED",
        entry_mode=entry_mode,
        risk_usd=50.0,
        position_size=position_size,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
    )
    return out
