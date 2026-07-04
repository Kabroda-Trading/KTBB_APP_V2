# mtf_backtest_lab.py
# ==============================================================================
# STANDALONE 4H/1H STOP/TARGET + ENERGY VALIDATION TOOL
#
# NOT wired into main.py. NOT production code. Run directly:
#   python mtf_backtest_lab.py --tf 1h --days 60
#   python mtf_backtest_lab.py --tf 4h --days 120
#
# HONEST SCOPE: this validates the NEW pivot-based stop/target + energy-grade
# logic against real historical price action fetched fresh from MEXC's public
# API. It does NOT replay what the OLD gravity_memory-zone-lookup mechanism
# would have picked historically -- that depends on production database state
# (which supply/demand zones existed in gravity_memory at each past moment)
# that isn't reconstructable from OHLCV alone. This tool answers "does the
# NEW logic produce sane, profitable-shaped signals against real history,"
# not "how would the old system have compared." Two different questions --
# don't let output from this tool be mistaken for the second one.
#
# Every fix from the punch list gets validated here before touching
# gravity_engine.py, ledger_closing_engine.py, or battlebox_pipeline.py.
# ==============================================================================

import argparse
import json
import math
import urllib.request
import datetime
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# 1) DATA FETCH — real MEXC public klines, no credentials needed
# ---------------------------------------------------------------------------

def fetch_candles(symbol: str, interval: str, start_dt: datetime.datetime, end_dt: datetime.datetime) -> List[Dict]:
    """Fetches real historical candles from MEXC's public API, paginating as needed."""
    all_raw = []
    cursor = start_dt
    while cursor < end_dt:
        start_ms = int(cursor.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        url = (f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval={interval}"
               f"&startTime={start_ms}&endTime={end_ms}&limit=1000")
        with urllib.request.urlopen(url, timeout=15) as resp:
            batch = json.loads(resp.read())
        if not batch:
            break
        all_raw.extend(batch)
        last_ts = batch[-1][0]
        next_cursor = datetime.datetime.fromtimestamp(last_ts / 1000, tz=datetime.timezone.utc) + datetime.timedelta(seconds=1)
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        # NOTE: do NOT break on len(batch) < 1000 -- MEXC's public klines API silently caps
        # every response at 500 candles regardless of the requested limit=1000. Treating a
        # 500-row batch as "end of history" was truncating every multi-page fetch to ~500
        # candles (confirmed empirically 2026-07-04). The outer `while cursor < end_dt` and
        # the `if not batch` check above are sufficient to terminate correctly.

    seen = {}
    for c in all_raw:
        seen[c[0]] = c
    raw = [seen[k] for k in sorted(seen.keys())]

    candles = []
    for c in raw:
        candles.append({
            "ts": c[0], "open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
            "close": float(c[4]), "volume": float(c[5]),
            "dt": datetime.datetime.fromtimestamp(c[0] / 1000, tz=datetime.timezone.utc),
        })
    return candles


# ---------------------------------------------------------------------------
# 2) PIVOT DETECTION — matches Kabroda's own left=3/right=3 convention
#    (gravity_engine._scan_for_pivots, sse_engine._find_pivots)
# ---------------------------------------------------------------------------

def find_confirmed_pivots(candles: List[Dict], end_idx: int, left: int = 3, right: int = 3, direction: str = "low"):
    """Walks backward from end_idx, returns all confirmed pivots, most recent first."""
    pivots = []
    for i in range(end_idx - right, left - 1, -1):
        if direction == "low":
            wv = candles[i]["low"]
            is_pivot = all(candles[i - j]["low"] >= wv for j in range(1, left + 1)) and \
                       all(candles[i + j]["low"] >= wv for j in range(1, right + 1))
        else:
            wv = candles[i]["high"]
            is_pivot = all(candles[i - j]["high"] <= wv for j in range(1, left + 1)) and \
                       all(candles[i + j]["high"] <= wv for j in range(1, right + 1))
        if is_pivot:
            pivots.append((i, wv))
    return pivots


def find_confirmed_pivots_windowed(candles: List[Dict], end_idx: int, window_bars: int,
                                    left: int = 3, right: int = 3, direction: str = "low"):
    """Same convention as find_confirmed_pivots, but only scans indices >= end_idx - window_bars.
    Validates the production fix: a recency-bounded, non-strength-gated nearest-pivot lookup
    (replaces gravity_engine's heat_multiplier/touch_count/departure_move_pct qualification gate,
    which was confirmed to skip past the nearest relevant pivot in favor of a distant one that
    happened to clear the strength bar -- see WORK_LOG.md 2026-07-03 entry)."""
    pivots = []
    floor_idx = max(left, end_idx - window_bars)
    for i in range(end_idx - right, floor_idx - 1, -1):
        if direction == "low":
            wv = candles[i]["low"]
            is_pivot = all(candles[i - j]["low"] >= wv for j in range(1, left + 1)) and \
                       all(candles[i + j]["low"] >= wv for j in range(1, right + 1))
        else:
            wv = candles[i]["high"]
            is_pivot = all(candles[i - j]["high"] <= wv for j in range(1, left + 1)) and \
                       all(candles[i + j]["high"] <= wv for j in range(1, right + 1))
        if is_pivot:
            pivots.append((i, wv))
    return pivots


# ---------------------------------------------------------------------------
# 3) INDICATOR MATH — mirrors battlebox_pipeline.py exactly
# ---------------------------------------------------------------------------


def calc_atr(candles: List[Dict], period: int = 14) -> float:
    """Mirrors gravity_engine._calc_atr(), but correctly excludes the still-forming last
    candle first -- matching _scan_for_pivots' own closed_candles = candles[:-1] convention.
    Production currently calls _calc_atr on the raw (unsliced) candle list; that inconsistency
    is fixed here from the start rather than ported and fixed later."""
    closed = candles[:-1]
    if len(closed) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        h = closed[i]["high"]
        l = closed[i]["low"]
        prev_c = closed[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)

def calc_ema_series(prices: List[float], period: int) -> List[float]:
    if not prices or len(prices) < period:
        return []
    ema = [sum(prices[:period]) / period]
    mult = 2 / (period + 1)
    for p in prices[period:]:
        ema.append((p - ema[-1]) * mult + ema[-1])
    return ema


def calc_macd(prices: List[float], fast=12, slow=26, signal=9) -> Dict:
    if len(prices) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    fast_ema = calc_ema_series(prices, fast)
    slow_ema = calc_ema_series(prices, slow)
    macd_line = [f - s for f, s in zip(fast_ema[-(len(slow_ema)):], slow_ema)]
    signal_line = calc_ema_series(macd_line, signal)
    if not signal_line:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    return {"macd": macd_line[-1], "signal": signal_line[-1], "hist": macd_line[-1] - signal_line[-1]}


def calc_bbwp(closes: List[float], bb_period=20, bb_std=2.0, lookback=252) -> float:
    if len(closes) < bb_period + 1:
        return 50.0
    bbw = [None] * len(closes)
    for i in range(bb_period - 1, len(closes)):
        window = closes[i - bb_period + 1: i + 1]
        sma = sum(window) / bb_period
        if sma == 0:
            continue
        var = sum((x - sma) ** 2 for x in window) / bb_period
        std = var ** 0.5
        bbw[i] = (sma + bb_std * std - (sma - bb_std * std)) / sma
    cur = bbw[-1]
    if cur is None:
        return 50.0
    hist = [v for v in bbw[max(0, len(closes) - lookback):] if v is not None]
    if not hist:
        return 50.0
    return round(sum(1 for v in hist if v < cur) / len(hist) * 100.0, 2)


def calc_pmarp(closes: List[float], ma_period=50, lookback=252) -> float:
    if len(closes) < ma_period + 1:
        return 50.0
    pmar = [None] * len(closes)
    for i in range(ma_period - 1, len(closes)):
        sma = sum(closes[i - ma_period + 1: i + 1]) / ma_period
        if sma > 0:
            pmar[i] = closes[i] / sma
    cur = pmar[-1]
    if cur is None:
        return 50.0
    hist = [v for v in pmar[max(0, len(closes) - lookback):] if v is not None]
    if not hist:
        return 50.0
    return round(sum(1 for v in hist if v < cur) / len(hist) * 100.0, 2)


def energy_grade_current(candles: List[Dict], bias: str) -> str:
    """Mirrors gravity_engine._compute_energy_grade() exactly (EMA30/50 + MACD + PMARP cap)."""
    if not candles or len(candles) < 50:
        return "WEAK"
    closes = [c["close"] for c in candles]
    ema30 = calc_ema_series(closes, 30)[-1]
    ema50 = calc_ema_series(closes, 50)[-1]
    trend_bullish = ema30 > ema50
    macd = calc_macd(closes)
    hist_bps = abs(macd["hist"] / ema50 * 10000) if ema50 != 0 else 0
    macd_strength = "STRONG" if hist_bps >= 20 else "WEAK" if hist_bps >= 5 else "DEPLETED"
    aligned = (bias == "LONG" and trend_bullish) or (bias == "SHORT" and not trend_bullish)
    if aligned and macd_strength == "STRONG":
        grade = "STRONG"
    elif aligned:
        grade = "MODERATE"
    else:
        grade = "WEAK"
    if len(closes) >= 252:
        pmarp = calc_pmarp(closes)
        if bias == "LONG":
            if pmarp >= 95.0:
                grade = "WEAK"
            elif pmarp >= 85.0 and grade == "STRONG":
                grade = "MODERATE"
        elif bias == "SHORT":
            if pmarp <= 5.0:
                grade = "WEAK"
            elif pmarp <= 15.0 and grade == "STRONG":
                grade = "MODERATE"
    return grade


def kinematic_grade_15m_style(candles: List[Dict]) -> str:
    """Mirrors battlebox_pipeline._build_synthetic_jewel()'s PRIMED/TANGLED/OVEREXTENDED logic."""
    closes = [c["close"] for c in candles]
    if len(closes) < 200:
        return "TANGLED"
    ema9 = calc_ema_series(closes, 9)[-1]
    ema55 = calc_ema_series(closes, 55)[-1]
    ribbon_spread = abs(ema9 - ema55) / ema55 * 100
    bbwp_val = calc_bbwp(closes)
    pmarp_val = calc_pmarp(closes)
    if pmarp_val >= 85.0:
        return "OVEREXTENDED"
    elif bbwp_val <= 30.0 and ribbon_spread > 0.05:
        return "PRIMED"
    else:
        return "TANGLED"


def calc_adx(candles: List[Dict], period: int = 14) -> Dict:
    """Mirrors battlebox_pipeline._calc_adx() exactly -- the corrected, post-W-7-fix Wilder version."""
    if len(candles) < period * 2 + 1:
        return {"adx": 0.0, "rising": False}
    plus_dm_vals, minus_dm_vals, tr_vals = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i-1]["high"], candles[i-1]["low"], candles[i-1]["close"]
        up, dn = h - ph, pl - l
        plus_dm_vals.append(up if (up > dn and up > 0) else 0.0)
        minus_dm_vals.append(dn if (dn > up and dn > 0) else 0.0)
        tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))

    def wilder(vals):
        if len(vals) < period:
            return []
        s = [sum(vals[:period]) / period]
        for v in vals[period:]:
            s.append(s[-1] - s[-1] / period + v / period)
        return s

    sm_pdm, sm_mdm, sm_tr = wilder(plus_dm_vals), wilder(minus_dm_vals), wilder(tr_vals)
    if not sm_tr:
        return {"adx": 0.0, "rising": False}
    dx_vals = []
    for i in range(len(sm_tr)):
        tr = sm_tr[i]
        if tr == 0:
            dx_vals.append(0.0)
            continue
        pdi, mdi = 100 * sm_pdm[i] / tr, 100 * sm_mdm[i] / tr
        dsum = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / dsum if dsum > 0 else 0.0)
    adx_vals = wilder(dx_vals)
    if not adx_vals:
        return {"adx": 0.0, "rising": False}
    return {"adx": round(adx_vals[-1], 2), "rising": len(adx_vals) >= 2 and adx_vals[-1] > adx_vals[-2]}


def volume_confirmed(candles: List[Dict], idx: int, lookback: int = 20) -> bool:
    """Breakout candle's volume vs. its own rolling average -- cited by external research
    as a real fake-out filter, currently used by zero energy formula in Kabroda."""
    if idx < lookback:
        return False
    avg_vol = sum(c["volume"] for c in candles[idx - lookback:idx]) / lookback
    return candles[idx]["volume"] > avg_vol


def bbwp_descending_adx_elevated(candles: List[Dict], lookback_bars: int = 3, adx_floor: float = 25.0) -> bool:
    """The specific 'energy building' regime found testing candidate 112: BBWP trending DOWN
    (not just a static <=30 snapshot) while ADX is already elevated -- distinct from both the
    current formula (no BBWP/ADX at all) and the 15M-style formula's static threshold check."""
    closes = [c["close"] for c in candles]
    if len(closes) < 260:
        return False
    bbwp_now = calc_bbwp(closes)
    bbwp_prev = calc_bbwp(closes[:-lookback_bars])
    descending = bbwp_now < bbwp_prev
    adx = calc_adx(candles)
    elevated = adx["adx"] >= adx_floor
    return descending and elevated


# ---------------------------------------------------------------------------
# 4) BREAKOUT SCANNER — Crown Strategy 1 style: close breaks a confirmed pivot
# ---------------------------------------------------------------------------

def scan_breakouts(candles: List[Dict], min_history: int = 260) -> List[Dict]:
    """
    Walks through history looking for a candle whose close breaks above the
    nearest confirmed pivot high (LONG signal) or below the nearest confirmed
    pivot low (SHORT signal) -- momentum/breakout entry style, matching what
    the real 4H/1H BOS detector actually does (not a pullback entry).
    """
    signals = []
    for i in range(min_history, len(candles) - 1):
        c = candles[i]
        highs = find_confirmed_pivots(candles, i, direction="high")
        lows = find_confirmed_pivots(candles, i, direction="low")
        if highs and c["close"] > highs[0][1] and candles[i - 1]["close"] <= highs[0][1]:
            signals.append({"idx": i, "bias": "LONG", "entry": c["close"]})
        elif lows and c["close"] < lows[0][1] and candles[i - 1]["close"] >= lows[0][1]:
            signals.append({"idx": i, "bias": "SHORT", "entry": c["close"]})
    return signals


# ---------------------------------------------------------------------------
# 5) STOP/TARGET CONSTRUCTION — the new pivot-based design
# ---------------------------------------------------------------------------

def build_trade_plan(candles: List[Dict], idx: int, bias: str, entry: float) -> Optional[Dict]:
    direction = "low" if bias == "LONG" else "high"
    pivots = find_confirmed_pivots(candles, idx, direction=direction)
    if not pivots:
        return None
    _, stop = pivots[0]
    leg = abs(entry - stop)
    if leg <= 0:
        return None
    if bias == "LONG":
        t1, t2, t3 = entry + leg, entry + leg * 1.618, entry + leg * 2.618
    else:
        t1, t2, t3 = entry - leg, entry - leg * 1.618, entry - leg * 2.618
    return {"stop": stop, "t1": t1, "t2": t2, "t3": t3, "leg": leg}


def build_trade_plan_windowed(candles: List[Dict], idx: int, bias: str, entry: float,
                               window_bars: int, atr_floor_mult: float, order_by: str = "recency") -> Optional[Dict]:
    """Validates the production fix directly: stop = nearest confirmed pivot within a
    recency-bounded window (no heat/touch/departure strength gate), leg = |entry - stop|,
    targets = Fibonacci-staged (1.0x/1.618x/2.618x) off that leg, with the same ATR floor/cap
    rails gravity_engine applies (1.5x floor for 4H, 1.0x floor for 1H, 3x cap above 5x).

    order_by: "recency" (nearest confirmed pivot in time -- matches build_trade_plan's existing,
    already-validated convention) or "price" (nearest confirmed pivot by price distance to entry --
    matches gravity_engine's CURRENT query shape, a minimal diff from today's production code).
    These are a real fork once a window can contain 2+ confirmed pivots -- test both, don't assume.
    """
    direction = "low" if bias == "LONG" else "high"
    pivots = find_confirmed_pivots_windowed(candles, idx, window_bars, direction=direction)
    if not pivots:
        return None
    if order_by == "price":
        pivots = sorted(pivots, key=lambda p: p[1], reverse=(bias == "SHORT"))
    _, stop = pivots[0]
    raw_leg = abs(entry - stop)
    if raw_leg <= 0:
        return None

    atr14 = calc_atr(candles[:idx + 1])
    target_too_small = (raw_leg < atr_floor_mult * atr14) if atr14 > 0 else False
    leg = max(raw_leg, atr_floor_mult * atr14) if atr14 > 0 else raw_leg
    if atr14 > 0 and leg > 5.0 * atr14:
        leg = 3.0 * atr14

    if bias == "LONG":
        t1, t2, t3 = entry + leg, entry + leg * 1.618, entry + leg * 2.618
    else:
        t1, t2, t3 = entry - leg, entry - leg * 1.618, entry - leg * 2.618
    return {"stop": stop, "t1": t1, "t2": t2, "t3": t3, "leg": leg, "target_too_small": target_too_small}


# ---------------------------------------------------------------------------
# 6) FORWARD WALK — what actually happened after the signal
# ---------------------------------------------------------------------------

def walk_forward(candles: List[Dict], idx: int, bias: str, plan: Dict, max_bars: int = 200) -> Dict:
    """Walks forward bar by bar to see whether stop, T1, T2, or T3 was hit first."""
    stop, t1, t2, t3 = plan["stop"], plan["t1"], plan["t2"], plan["t3"]
    result = {"outcome": "NO_RESOLUTION", "bars_to_resolve": None, "r_achieved": 0.0}
    for j in range(idx + 1, min(idx + 1 + max_bars, len(candles))):
        bar = candles[j]
        if bias == "LONG":
            hit_stop = bar["low"] <= stop
            hit_t3 = bar["high"] >= t3
            hit_t2 = bar["high"] >= t2
            hit_t1 = bar["high"] >= t1
        else:
            hit_stop = bar["high"] >= stop
            hit_t3 = bar["low"] <= t3
            hit_t2 = bar["low"] <= t2
            hit_t1 = bar["low"] <= t1

        if hit_stop:
            result = {"outcome": "STOP", "bars_to_resolve": j - idx, "r_achieved": -1.0}
            return result
        if hit_t3:
            result = {"outcome": "T3", "bars_to_resolve": j - idx, "r_achieved": 2.618}
            return result
        if hit_t2:
            result = {"outcome": "T2", "bars_to_resolve": j - idx, "r_achieved": 1.618}
            return result
        if hit_t1:
            result = {"outcome": "T1", "bars_to_resolve": j - idx, "r_achieved": 1.0}
            return result
    return result


# ---------------------------------------------------------------------------
# 7) WINDOW-SIZE GRID TEST — validates the stop-selection fix BEFORE it ships
#    (punch-list item #1: which recency window + tie-break ordering replaces
#    gravity_engine's broken heat/touch/departure qualification gate)
# ---------------------------------------------------------------------------

def test_window_sizes(candles: List[Dict], signals: List[Dict], window_options: List[int],
                       atr_floor_mult: float, order_by: str = "recency") -> List[tuple]:
    """Grid-tests build_trade_plan_windowed across candidate window sizes (in bars, NOT
    calendar days -- see main()'s conversion note). Includes a BASELINE row using today's
    unrestricted build_trade_plan() (whole-history nearest pivot) so the windowed approach
    can be compared against what the tool already validated, not just against itself."""
    results = []
    for window_bars in window_options:
        rows = []
        for sig in signals:
            idx, bias, entry = sig["idx"], sig["bias"], sig["entry"]
            plan = build_trade_plan_windowed(candles, idx, bias, entry, window_bars, atr_floor_mult, order_by=order_by)
            if not plan:
                continue
            rows.append({"leg": plan["leg"], "entry": entry, **walk_forward(candles, idx, bias, plan)})
        results.append((f"window={window_bars}", rows))

    baseline_rows = []
    for sig in signals:
        idx, bias, entry = sig["idx"], sig["bias"], sig["entry"]
        plan = build_trade_plan(candles, idx, bias, entry)
        if not plan:
            continue
        baseline_rows.append({"leg": plan["leg"], "entry": entry, **walk_forward(candles, idx, bias, plan)})
    results.append(("BASELINE(whole-history, current tool default)", baseline_rows))
    return results


def print_window_grid(results: List[tuple]) -> None:
    print(f"\n{'Window':42s} {'N':>4s} {'WinRate':>8s} {'AvgR':>8s} {'Stopped':>8s} {'Unresolved':>11s} {'AvgLeg%':>8s}")
    for label, rows in results:
        if not rows:
            print(f"{label:42s} N=0, no resolvable signals")
            continue
        n = len(rows)
        avg_r = sum(r["r_achieved"] for r in rows) / n
        wins = sum(1 for r in rows if r["r_achieved"] > 0)
        stops = sum(1 for r in rows if r["outcome"] == "STOP")
        unresolved = sum(1 for r in rows if r["outcome"] == "NO_RESOLUTION")
        avg_leg_pct = sum(r["leg"] / r["entry"] * 100 for r in rows) / n
        print(f"{label:42s} {n:4d} {wins/n*100:7.1f}% {avg_r:+8.3f} {stops:8d} {unresolved:11d} {avg_leg_pct:7.2f}%")
    print(
        "\nNOTE: at N<30 (this project's own bar) or N<100 (the sourced quant_prime_ai standard), "
        "these numbers indicate direction only -- they cannot statistically prove one window beats "
        "another. Prefer a window that's stable across 2-3 neighboring sizes over a lonely spike, "
        "and be suspicious of a window that avoids stops only by being the widest option tested "
        "(that's evasion, not improvement)."
    )


# ---------------------------------------------------------------------------
# 8) MAIN — run the full scan, report honest aggregate stats
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Validate the new pivot-based 4H/1H stop/target + energy logic against real history.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--tf", choices=["1h", "4h"], default="1h")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--window-test", action="store_true",
                     help="Grid-test windowed stop-selection (punch-list item #1 validation) instead of the default single-plan report.")
    ap.add_argument("--windows", type=str, default="24,36,48,60,72,96",
                     help="Comma-separated candidate window sizes IN BARS (not calendar days) to grid-test.")
    ap.add_argument("--order-by", choices=["recency", "price"], default="recency",
                     help="Tie-break when a window contains 2+ confirmed pivots: nearest-by-time (recency, matches the tool's existing validated convention) or nearest-by-price (matches gravity_engine's current production query shape).")
    args = ap.parse_args()

    interval = "60m" if args.tf == "1h" else "4h"
    end_dt = datetime.datetime.now(datetime.timezone.utc)
    start_dt = end_dt - datetime.timedelta(days=args.days)

    print(f"Fetching {args.symbol} {args.tf} candles, {args.days} days...")
    candles = fetch_candles(args.symbol, interval, start_dt, end_dt)
    print(f"Got {len(candles)} candles, {candles[0]['dt']} to {candles[-1]['dt']}\n")

    signals = scan_breakouts(candles)
    print(f"Found {len(signals)} breakout signals (momentum/close-through-pivot style)\n")

    if args.window_test:
        atr_floor_mult = 1.5 if args.tf == "4h" else 1.0
        window_options = [int(w.strip()) for w in args.windows.split(",") if w.strip()]
        bar_hours = 4 if args.tf == "4h" else 1
        print(f"WINDOW-SIZE GRID TEST -- tf={args.tf}  order_by={args.order_by}  atr_floor_mult={atr_floor_mult}")
        print("Bar-to-calendar conversion for the windows below (bars are the tool's native unit; "
              "gravity_engine's lookback is a timedelta in DAYS -- this conversion must be applied "
              "explicitly when porting a chosen window into production, not eyeballed):")
        for w in window_options:
            print(f"  {w} bars  ==  {w * bar_hours / 24:.1f} calendar days")
        results = test_window_sizes(candles, signals, window_options, atr_floor_mult, order_by=args.order_by)
        print_window_grid(results)
        return

    rows = []
    for sig in signals:
        idx, bias, entry = sig["idx"], sig["bias"], sig["entry"]
        plan = build_trade_plan(candles, idx, bias, entry)
        if not plan:
            continue
        hist = candles[:idx + 1]
        eg_current = energy_grade_current(hist, bias)
        eg_15m = kinematic_grade_15m_style(hist)
        vol_conf = volume_confirmed(candles, idx)
        bbwp_adx = bbwp_descending_adx_elevated(hist)
        outcome = walk_forward(candles, idx, bias, plan)
        rows.append({
            "dt": candles[idx]["dt"], "bias": bias, "entry": entry, "stop": plan["stop"],
            "t1": plan["t1"], "risk": plan["leg"],
            "energy_current": eg_current, "energy_15m": eg_15m,
            "vol_confirmed": vol_conf, "bbwp_desc_adx_elevated": bbwp_adx,
            **outcome,
        })

    print(f"{'Date':17s} {'Bias':5s} {'Entry':>10s} {'Risk%':>7s} {'EnergyCur':>10s} {'Energy15M':>11s} {'Vol':>5s} {'BBWPdesc':>9s} {'Outcome':>8s} {'R':>6s}")
    for r in rows:
        risk_pct = r["risk"] / r["entry"] * 100
        print(f"{r['dt'].strftime('%Y-%m-%d %H:%M'):17s} {r['bias']:5s} {r['entry']:10.2f} {risk_pct:6.2f}% {r['energy_current']:>10s} {r['energy_15m']:>11s} {str(r['vol_confirmed']):>5s} {str(r['bbwp_desc_adx_elevated']):>9s} {r['outcome']:>8s} {r['r_achieved']:6.2f}")

    print(f"\n{'='*90}")
    print("AGGREGATE STATS")
    print(f"{'='*90}")

    def report(label, subset):
        if not subset:
            print(f"{label}: N=0, no signals")
            return
        n = len(subset)
        avg_r = sum(r["r_achieved"] for r in subset) / n
        wins = sum(1 for r in subset if r["r_achieved"] > 0)
        stops = sum(1 for r in subset if r["outcome"] == "STOP")
        unresolved = sum(1 for r in subset if r["outcome"] == "NO_RESOLUTION")
        print(f"{label}: N={n}  win_rate={wins/n*100:.1f}%  avg_R={avg_r:+.3f}  stopped_out={stops}  unresolved={unresolved}")

    report("ALL signals", rows)
    report("energy_current == STRONG only", [r for r in rows if r["energy_current"] == "STRONG"])
    report("energy_current in [STRONG, MODERATE]", [r for r in rows if r["energy_current"] in ("STRONG", "MODERATE")])
    report("energy_current == WEAK only", [r for r in rows if r["energy_current"] == "WEAK"])
    report("energy_15m == PRIMED only", [r for r in rows if r["energy_15m"] == "PRIMED"])
    report("energy_15m == TANGLED only", [r for r in rows if r["energy_15m"] == "TANGLED"])
    report("energy_15m == OVEREXTENDED only", [r for r in rows if r["energy_15m"] == "OVEREXTENDED"])
    print()
    report("volume_confirmed == True", [r for r in rows if r["vol_confirmed"]])
    report("volume_confirmed == False", [r for r in rows if not r["vol_confirmed"]])
    print()
    report("BBWP-descending + ADX-elevated == True", [r for r in rows if r["bbwp_desc_adx_elevated"]])
    report("BBWP-descending + ADX-elevated == False", [r for r in rows if not r["bbwp_desc_adx_elevated"]])
    print()
    report("volume_confirmed AND energy_15m==PRIMED", [r for r in rows if r["vol_confirmed"] and r["energy_15m"] == "PRIMED"])


if __name__ == "__main__":
    main()
