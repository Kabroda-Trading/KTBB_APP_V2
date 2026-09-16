# study_indicators.py
# ==============================================================================
# TRAVELER-STUDY INDICATOR PORTS -- byte-identical to the walk-forward-
# validated construction, NOT the site's existing indicator variants.
#
# Ported 2026-09-15 (CC_WORK_ORDER_PHASE2.md step 3) from `Kabroda AI Brain`'s
# brain/audit_evidence/traveler_study/lab_exhaustion_conditions.py (rsi_series,
# bbwp_series) -- the exact functions the ship-path candidate (E1 + F_A gate +
# C5_T-M_EXIT + BBWP_BURN_70, CANON §9d) was walk-forward-validated against.
#
# WHY A NEW MODULE, NOT A REUSE OF battlebox_pipeline.py::_calc_rsi():
# confirmed by direct comparison (2026-09-15 Phase-0 verification, AGENT_LOG.md
# both repos) that the two are the SAME FAMILY (Wilder-style exponential
# smoothing) but NOT NUMERICALLY IDENTICAL -- _calc_rsi() is classic Wilder
# (SMA-seeded first `period` bars, then Wilder-smoothed); the study's
# rsi_series() is a pure pandas ewm(alpha=1/period, adjust=False), UNSEEDED
# (no warm-up SMA -- the running average starts at the very first diff).
# They converge with enough history (diff 8.57 RSI points at n=30, 0.02 at
# n=120, 0.0000 at n=300+) but are not guaranteed equal near the convergence
# boundary, which matters right at the RSI-zone/tercile-skip thresholds this
# module feeds. Do not touch _calc_rsi() (other callers) -- this is a
# deliberate second implementation, not a duplicate to be merged later.
#
# bbwp_series() here is the mathematically-simplified form the ACTUAL frozen
# exit-stack walk uses (`lab_touchfill_arms.py::walk_from_fill`, CANON §9d:
# `bb_w = close.rolling(96).std(); bbwp = bb_w / bb_w.rolling(768).max() * 100`)
# rather than lab_exhaustion_conditions.py::bbwp_series()'s high/low-carrying
# form (`width = 4*sd; bbwp = width/width.rolling(768).max()*100`) -- the two
# are algebraically IDENTICAL (the 4x factor cancels exactly in the ratio),
# confirmed by hand before porting the simpler one; the simpler form also
# needs only closes, matching what the live executor already fetches.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional

RSI_PERIOD = 14        # CANON: KROWN_CROSS/RSI_PERIOD row, same period as the site's own gate
BBWP_PERIOD = 96        # 4H bars (96 * 4h = 16 days) -- lab_exhaustion_conditions.py:56
BBWP_LOOKBACK = 768      # 4H bars (768 * 4h = 128 days) -- lab_exhaustion_conditions.py:61


def rsi_series(closes: List[float], period: int = RSI_PERIOD) -> List[Optional[float]]:
    """Port of lab_exhaustion_conditions.py::rsi_series() (and identically,
    lab_composite_stack.py/lab_mafuel_arms.py/lab_touchfill_arms.py's own
    inlined copies of the same function) -- verbatim formula:

        d = close.diff()
        up = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        rs = up / dn.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    pandas ewm(adjust=False) recurrence: y[0] = x[0]; y[t] = y[t-1] + alpha*(x[t]-y[t-1]).
    Returns one value per input close, None where undefined (index 0, since
    diff() has no prior bar; and any bar where the smoothed down-move average
    is exactly 0.0, matching pandas' `dn.replace(0, np.nan)` -> NaN exactly,
    not a fabricated 100.0)."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < 2:
        return out
    alpha = 1.0 / period
    up_ewm: Optional[float] = None
    dn_ewm: Optional[float] = None
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        up = d if d > 0 else 0.0
        dn = -d if d < 0 else 0.0
        up_ewm = up if up_ewm is None else up_ewm + alpha * (up - up_ewm)
        dn_ewm = dn if dn_ewm is None else dn_ewm + alpha * (dn - dn_ewm)
        if dn_ewm == 0.0:
            out[i] = None  # matches pandas' dn.replace(0, np.nan) -> NaN
        else:
            rs = up_ewm / dn_ewm
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _rolling_std(values: List[float], period: int) -> List[Optional[float]]:
    """Sample standard deviation (ddof=1), matching pandas' Series.rolling().std()
    default -- NOT population std (ddof=0). Naive O(n*period) implementation;
    fine at the site's live candle-window sizes (100-300 bars), not meant for
    the study's own bulk 5-year-corpus recomputation."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / (period - 1)
        out[i] = var ** 0.5
    return out


def _rolling_max(values: List[Optional[float]], period: int) -> List[Optional[float]]:
    n = len(values)
    out: List[Optional[float]] = [None] * n
    for i in range(n):
        lo = max(0, i - period + 1)
        window = [v for v in values[lo:i + 1] if v is not None]
        out[i] = max(window) if len(window) == (i - lo + 1) else None
    return out


def bbwp_series(closes: List[float], period: int = BBWP_PERIOD,
                 lookback: int = BBWP_LOOKBACK) -> List[Optional[float]]:
    """Port of the frozen exit-stack's BBWP construction (`lab_touchfill_arms.py`
    lines 123-125, CANON §9d -- algebraically identical to lab_exhaustion_
    conditions.py::bbwp_series(), see this module's header comment):

        bb_w = close.rolling(period).std()          # sample std, ddof=1
        bbwp = (bb_w / bb_w.rolling(lookback).max()) * 100.0

    Returns one value per input close, None wherever either rolling window
    isn't yet full (matching pandas' NaN-until-window-full semantics)."""
    std = _rolling_std(closes, period)
    std_max = _rolling_max(std, lookback)
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    for i in range(n):
        s, m = std[i], std_max[i]
        if s is None or m is None or m == 0:
            continue
        out[i] = (s / m) * 100.0
    return out


def c5_momentum_decay(rsi: List[Optional[float]], i: int, lookback_bars: int = 6) -> bool:
    """C5_T-M_EXIT's per-timeframe condition (lab_composite_stack.py:77-78:
    `h1["c5"] = h1.rsi < h1.rsi.shift(6)`; same for 4H) -- true when the RSI
    at index i is below the RSI `lookback_bars` bars prior. False (not None)
    when there isn't enough history yet -- shift() would be NaN there too,
    and `NaN < x` is False in pandas, never True."""
    if i < lookback_bars or i >= len(rsi):
        return False
    cur, prior = rsi[i], rsi[i - lookback_bars]
    if cur is None or prior is None:
        return False
    return cur < prior


def bbwp_burn(bbwp: List[Optional[float]], i: int, threshold: float = 70.0) -> bool:
    """BBWP_BURN_70's condition (lab_mafuel_arms.py:152 / lab_touchfill_arms.py:198:
    `bbwp_4h > thr AND bbwp_4h.diff() < 0`, i.e. above the threshold AND falling
    from the prior bar) at index i."""
    if i < 1 or i >= len(bbwp):
        return False
    cur, prior = bbwp[i], bbwp[i - 1]
    if cur is None or prior is None:
        return False
    return cur > threshold and (cur - prior) < 0
