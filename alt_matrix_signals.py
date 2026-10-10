# alt_matrix_signals.py
# ==============================================================================
# ALT MATRIX D1 SIGNALS -- pure functions, no I/O, no DB. Candles are plain
# dicts with "time"/"open"/"high"/"low"/"close" keys (epoch seconds), the
# same shape market_data.py's fetch_bitunix_* functions already return --
# matching gate_traveler.py's own established convention of recomputing
# everything fresh from a passed-in candle window each call, no carried
# state between calls.
#
# PARITY, NOT INVENTION: this module's indicator formulas are written to
# reproduce brain/audit_evidence/altcoin_study_{sol,eth}/walkforward_htf_
# {sol,eth}.py bar-for-bar (see tests/test_alt_matrix_signals.py's parity
# tests against the real committed CSVs) -- NOT to "correct" that script's
# math unilaterally. Two real discrepancies between that script and
# ALT_MATRIX_D1_D2_D3_SPEC.md's own wording were found by audit (2026-10-09,
# independently confirmed by reading the script directly) and are exposed
# here as EXPLICIT, labeled choices rather than silently picked:
#
#   1. ATR14: the backtest uses atr14_range_mean() -- a plain (high-low)
#      rolling mean, NOT Wilder's true-range ATR. atr14_wilder() is
#      provided alongside it so a future ruling to switch is a one-line
#      default change, not a rewrite -- but nothing has measured Wilder
#      ATR's behavior yet, so it must not become the default without a
#      fresh walk-forward run.
#   2. Macro gate timing: the backtest's own df4["day_epoch"] bucketing
#      reads EACH 4H bar's own day's daily SMA200 -- which for a bar
#      earlier in the day uses that day's own close before the day has
#      actually finished (a real, if unintentional, look-ahead). The
#      spec's own wording is "the daily bar fully closed as of 00:00 UTC"
#      -- i.e. the PRIOR day's close. macro_gate_mode="spec" (the only
#      mode evaluate_d1() uses by default) implements the spec literally;
#      macro_gate_mode="backtest_lookahead" reproduces the script's actual
#      behavior and exists ONLY so a parity test can measure the size of
#      the discrepancy -- it must never be the live default.
#
# Both discrepancies, plus the funding veto (untested in the backtest at
# all) and the entry-bar stop-hit gap, are the Part 0 findings in the
# approved plan -- DeepSeek/Andy need to re-run the walk-forward with the
# real D3 management logic before this module's output is wired to real
# money. D1's own gate math (this module) is on much firmer ground.
# ==============================================================================

from typing import Any, Dict, List, Optional

DAY_SECONDS = 86400
FUNDING_VETO_THRESHOLD = 0.0005   # +0.05% per 8h -- spec 2.3


def _closes(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(c["close"]) for c in candles]


def ema_series(values: List[float], span: int) -> List[Optional[float]]:
    """pandas' Series.ewm(span=span, adjust=False).mean(), reproduced
    exactly: EMA[0] = values[0], EMA[t] = alpha*values[t] + (1-alpha)*EMA[t-1],
    alpha = 2/(span+1). Returns one value per input bar (never None once
    there's at least one bar -- unlike a windowed indicator, EMA has no
    warm-up gap with adjust=False)."""
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def sma_series(values: List[float], window: int) -> List[Optional[float]]:
    """pandas' Series.rolling(window).mean(), reproduced exactly: None
    (NaN) for every index before `window` values exist, then the plain
    mean of the trailing `window` values."""
    out: List[Optional[float]] = []
    running_sum = 0.0
    for i, v in enumerate(values):
        running_sum += v
        if i >= window:
            running_sum -= values[i - window]
        out.append(running_sum / window if i >= window - 1 else None)
    return out


def atr14_range_mean_series(candles: List[Dict[str, Any]]) -> List[Optional[float]]:
    """The backtest's OWN formula: (high-low).rolling(14).mean() -- a
    plain range mean, not Wilder's true-range ATR. See this module's own
    header for why this is the default despite not being the textbook
    ATR: it's the only formula actually measured so far."""
    ranges = [float(c["high"]) - float(c["low"]) for c in candles]
    return sma_series(ranges, 14)


def atr14_wilder_series(candles: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Standard Wilder true-range ATR -- NOT the backtest's formula, NOT
    the current default anywhere in this module. Provided only so a
    future ruling to switch has somewhere to switch to. Do not wire this
    into evaluate_d1() without a fresh walk-forward measurement first."""
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


def _day_epoch(epoch: int) -> int:
    return int(epoch) - (int(epoch) % DAY_SECONDS)


def daily_sma200_lookup(candles_1d_confirmed: List[Dict[str, Any]]) -> Dict[int, Optional[float]]:
    """day_epoch -> SMA200 of daily closes THROUGH that day's own close
    (inclusive). Callers pick which day_epoch to actually look up based on
    macro_gate_mode -- this function itself makes no timing decision."""
    closes = _closes(candles_1d_confirmed)
    sma = sma_series(closes, 200)
    return {_day_epoch(int(c["time"])): sma[i] for i, c in enumerate(candles_1d_confirmed)}


def macro_gate_value(
    candles_1d_confirmed: List[Dict[str, Any]],
    four_h_bar_epoch: int,
    macro_gate_mode: str = "spec",
) -> Dict[str, Optional[float]]:
    """Returns {"daily_close": ..., "sma200": ...} for the 4H bar at
    four_h_bar_epoch, using either the spec-correct prior-day lookup or
    the backtest's actual same-day lookup -- see this module's own header.
    Returns Nones if the needed daily bar doesn't exist yet (not enough
    history / too early), never a guessed value."""
    lookup = daily_sma200_lookup(candles_1d_confirmed)
    day = _day_epoch(four_h_bar_epoch)
    target_day = day if macro_gate_mode == "backtest_lookahead" else day - DAY_SECONDS

    daily_close = None
    for c in candles_1d_confirmed:
        if _day_epoch(int(c["time"])) == target_day:
            daily_close = float(c["close"])
            break
    sma200 = lookup.get(target_day)
    return {"daily_close": daily_close, "sma200": sma200}


def evaluate_d1(
    candles_4h_confirmed: List[Dict[str, Any]],
    candles_1d_confirmed: List[Dict[str, Any]],
    funding_rate: Optional[float],
    *,
    macro_gate_mode: str = "spec",
) -> Dict[str, Any]:
    """The full D1 gate, evaluated against the LATEST confirmed 4H bar.
    Returns a verdict dict with every field needed to populate
    AltMatrixPlan, plus "signal" (bool) and "reason" (why not, when False).

    candles_4h_confirmed / candles_1d_confirmed: already run through
    market_data.confirmed_closes() by the caller (14400/86400 respectively)
    -- this function does not strip a still-forming bar itself, matching
    gate_traveler.py's own "caller strips first" convention.

    funding_rate: None means "fetch failed" -- fails closed (vetoed), per
    the approved plan ("if the fetch fails, veto the entry"), never
    silently treated as 0% / safe.
    """
    if len(candles_4h_confirmed) < 56:
        return {"signal": False, "reason": "insufficient_4h_history"}
    if len(candles_1d_confirmed) < 201:
        return {"signal": False, "reason": "insufficient_daily_history"}

    closes_4h = _closes(candles_4h_confirmed)
    ema21 = ema_series(closes_4h, 21)
    ema55 = ema_series(closes_4h, 55)
    atr14 = atr14_range_mean_series(candles_4h_confirmed)

    last = len(candles_4h_confirmed) - 1
    ema21_now, ema21_prev = ema21[last], ema21[last - 1]
    ema55_now, ema55_prev = ema55[last], ema55[last - 1]
    atr_now = atr14[last]
    bar_epoch = int(candles_4h_confirmed[last]["time"])

    macro = macro_gate_value(candles_1d_confirmed, bar_epoch, macro_gate_mode)
    daily_close, sma200 = macro["daily_close"], macro["sma200"]

    out: Dict[str, Any] = {
        "ema21": ema21_now, "ema21_prev": ema21_prev,
        "ema55": ema55_now, "ema55_prev": ema55_prev,
        "atr14": atr_now,
        "daily_close": daily_close, "sma200": sma200,
        "funding_rate": funding_rate,
    }

    if daily_close is None or sma200 is None:
        out.update(signal=False, reason="insufficient_daily_history")
        return out
    if atr_now is None or atr_now <= 0:
        out.update(signal=False, reason="insufficient_4h_history")
        return out

    macro_pass = daily_close > sma200
    cross_pass = ema21_now is not None and ema55_now is not None and ema21_prev is not None and ema55_prev is not None \
        and ema21_now > ema55_now and ema21_prev <= ema55_prev
    funding_pass = funding_rate is not None and funding_rate < FUNDING_VETO_THRESHOLD

    out["macro_pass"] = macro_pass
    out["cross_pass"] = cross_pass
    out["funding_pass"] = funding_pass

    if not macro_pass:
        out.update(signal=False, reason="SKIPPED_MACRO")
    elif not cross_pass:
        out.update(signal=False, reason="no_cross_this_bar")
    elif not funding_pass:
        out.update(signal=False, reason="SKIPPED_FUNDING")
    else:
        out.update(signal=True, reason=None)
    return out
