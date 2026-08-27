# phase4_backtest.py
# ==============================================================================
# PHASE 4 BACKTEST — replays the ACTUAL production functions against real
# historical BTC/USDT data, not reimplemented approximations.
#   sse_engine.compute_sse_levels()        -- real trigger computation
#   structure_state_engine.compute_structure_state() -- real 2-close gate
#   decision_engine.evaluate_15m_decision() -- the new coded decision layer
#   ledger_closing_engine._frac_r()        -- the same R-accounting live uses
#
# Historical data: paginated OKX OHLCV fetch. NOTE, verified directly by
# testing before writing this, not assumed: Kraken's public API (what
# market_data.py uses live) does NOT honor `since` for 5m/15m candles -- it
# silently returns only the most recent ~500 bars regardless of requested
# start date. MEXC honors `since` but only retains ~350 days. OKX genuinely
# retains multi-year 5m history (confirmed: real, price-verified candles back
# to 2022 -- ~$20k BTC then, matches known history) -- the only one of the
# four public APIs tested (Kraken/MEXC/OKX/Binance -- Binance geo-blocked
# from this environment) that supports a real multi-regime backtest window.
# This means backtest prices come from a different exchange than live trading
# does -- a real, small source of noise (different order books) worth
# knowing, not hidden.
#
# Real 5-minute candles are used, not a 15m substitute -- the 2-consecutive-
# close acceptance gate is defined in 5m-bar terms (CLAUDE.md) and using a
# coarser timeframe would silently change what "2 closes" means.
#
# Scores BOTH sides, per Andy's direct correction to the first draft of this
# plan: APPROVED win rate, AND stand-down quality (SAVED / OVERCAUTIOUS /
# UNRESOLVED) -- a system that never trades and one that overtrades are both
# failures this backtest needs to be able to see.
#
# Usage: python phase4_backtest.py [--days 90] [--symbol BTC/USDT]
# ==============================================================================

import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import ccxt.async_support as ccxt

import sse_engine
import structure_state_engine
import decision_engine
import trade_structure_analyst
from ledger_closing_engine import _frac_r
from battlebox_pipeline import _calculate_weekly_force
from market_data import _calc_ema_series, _calc_bbwp, _calc_pmarp
from battlebox_pipeline import _calc_stochastic_cross

NY_TZ = ZoneInfo("America/New_York")


async def _fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, until_ms: int,
                        page_limit: int = 300) -> List[Dict]:
    """Paginated ccxt fetch, bounded to [since_ms, until_ms]. page_limit must
    match the exchange's real per-call cap -- OKX silently caps at 300
    regardless of what's requested (verified directly: asked for 500, got
    300 back). A mismatched cap here would make the loop's "last page"
    detection fire after the very first call, silently truncating the whole
    fetch to one page -- caught and fixed before running the real pull."""
    all_rows: List[List[float]] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = await exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
        if not batch:
            break
        all_rows.extend(batch)
        next_cursor = batch[-1][0] + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < page_limit:
            break
    return [
        {"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
        for r in all_rows if r[0] <= until_ms
    ]


def _daily_anchor_timestamps(start: datetime, end: datetime) -> List[int]:
    """8:30 AM ET, every day in range -- matches session_manager's hardcoded
    AUTO-mode session (us_ny_futures), correctly DST-aware via zoneinfo."""
    out = []
    d = start.date()
    while d <= end.date():
        local = datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY_TZ)
        out.append(int(local.astimezone(timezone.utc).timestamp()))
        d += timedelta(days=1)
    return out


def _confluence_reading(candles: List[Dict]) -> Dict[str, Any]:
    """Same math run_mtf_confluence_scan()/_analyze_timeframe() use, applied
    to a historical candle window ending at decision time -- not an
    approximation, the actual shared functions (market_data.py)."""
    if len(candles) < 60:
        return {"direction_vote": "UNKNOWN", "bbwp_value": 50.0, "pmarp_value": 50.0}
    closes = [c["close"] for c in candles]
    ema21 = _calc_ema_series(closes, 21)
    ema55 = _calc_ema_series(closes, 55)
    if not ema21 or not ema55:
        return {"direction_vote": "UNKNOWN", "bbwp_value": 50.0, "pmarp_value": 50.0}
    direction = "BULLISH" if ema21[-1] > ema55[-1] else "BEARISH"
    return {
        "direction_vote": direction,
        "bbwp_value": _calc_bbwp(closes),
        "pmarp_value": _calc_pmarp(candles),
    }


def _classify_stand_down(post_lock_5m: List[Dict], bo: float, bd: float, distance: float) -> str:
    """Would a trade at either trigger have won within a reasonable window?
    Reuses the same measured-move T1 distance as the real target math, walked
    forward against real candles -- not a separate invented heuristic."""
    window = post_lock_5m[:400]  # ~1.4 days of 5m bars, matches session_expires_at scale
    if not window:
        return "STAND_DOWN_UNRESOLVED"
    long_t1 = bo + distance
    short_t1 = bd - distance
    long_hit, short_hit = False, False
    for c in window:
        if c["high"] >= long_t1:
            long_hit = True
        if c["low"] <= short_t1:
            short_hit = True
        if long_hit or short_hit:
            break
    if long_hit or short_hit:
        return "STAND_DOWN_OVERCAUTIOUS"
    return "STAND_DOWN_SAVED"


async def run_backtest(symbol: str = "BTC/USDT", days: int = 90) -> None:
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 15000})
    try:
        end = datetime.now(timezone.utc)
        # +5 days padding at the front for indicator warmup (BBWP/PMARP need
        # 252+/350+ bars of 15m-resampled history before the first real test).
        start = end - timedelta(days=days + 20)
        print(f"Fetching {days + 20} days of 5m {symbol} candles from OKX...")
        candles_5m = await _fetch_ohlcv(exchange, symbol, "5m",
                                         int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        print(f"Fetched {len(candles_5m)} 5m candles.")
        if len(candles_5m) < 2000:
            print("Not enough history returned -- aborting.")
            return

        candles_daily = await _fetch_ohlcv(exchange, symbol, "1d",
                                            int((start - timedelta(days=400)).timestamp() * 1000),
                                            int(end.timestamp() * 1000))

        anchors = _daily_anchor_timestamps(end - timedelta(days=days), end)
        results: List[Dict[str, Any]] = []

        for anchor_ts in anchors:
            lock_end_ts = anchor_ts + 1800
            calibration = [c for c in candles_5m if anchor_ts <= c["time"] < lock_end_ts]
            if len(calibration) < 6:
                continue

            history_before_lock = [c for c in candles_5m if c["time"] < lock_end_ts]
            if len(history_before_lock) < 2000:
                continue  # not enough warmup for reliable BBWP/PMARP percentile ranks
            # Bounded to THIS session only (through the next day's anchor) --
            # NOT open-ended to the rest of the fetched history. Real bug
            # found and fixed here: compute_structure_state() evaluates
            # candles_5m_post_lock[-(acceptance_required+6):] -- the LAST 8
            # candles of whatever list it's given. An unbounded post_lock_5m
            # meant almost every session was silently checking the last 8
            # candles of the entire 90-day dataset instead of the 8 candles
            # actually following that session's own lock.
            session_end_ts = anchor_ts + 86400
            post_lock_5m = [c for c in candles_5m if lock_end_ts <= c["time"] < session_end_ts]
            if not post_lock_5m:
                continue

            context_24h = [c for c in history_before_lock if c["time"] >= lock_end_ts - 86400]
            session_open = calibration[0]["open"]
            r30_high = max(c["high"] for c in calibration)
            r30_low = min(c["low"] for c in calibration)
            last_price = context_24h[-1]["close"] if context_24h else session_open
            daily_slice = [c for c in candles_daily if c["time"] < lock_end_ts]
            # macro_bias: the SAME already-proven, already-backtested signal
            # that gates _detect_1h_bos today (battlebox_pipeline.py's
            # _calculate_weekly_force). Computed here purely to TAG each
            # trade for analysis -- not yet used to gate anything. This is
            # the price-outcome-independent signal Andy asked to check
            # against the results, not a bucket reverse-engineered from
            # which trades happened to win.
            macro_bias = _calculate_weekly_force(daily_slice)

            sse_input = {
                "locked_history_5m": context_24h,
                "slice_24h_5m": context_24h,
                "raw_daily_candles": daily_slice,
                "session_open_price": session_open,
                "r30_high": r30_high,
                "r30_low": r30_low,
                "last_price": last_price,
            }
            levels_result = sse_engine.compute_sse_levels(sse_input)
            if "error" in levels_result or "levels" not in levels_result:
                continue
            levels = levels_result["levels"]
            bo, bd = levels.get("breakout_trigger"), levels.get("breakdown_trigger")
            if not bo or not bd:
                continue

            structure_state = structure_state_engine.compute_structure_state(levels, post_lock_5m)

            distance = round(bo - bd, 2)
            raw_targets = {
                "distance": distance,
                "long": {"entry": bo, "stop": bd, "t1": round(bo + distance, 2),
                          "t2": round(bo + distance * 1.618, 2), "t3": round(bo + distance * 2.618, 2)},
                "short": {"entry": bd, "stop": bo, "t1": round(bd - distance, 2),
                           "t2": round(bd - distance * 1.618, 2), "t3": round(bd - distance * 2.618, 2)},
            }
            # Real structural stop (CLAUDE.md rule #5): the executable stop is
            # r30_low/high +/- ATR*0.5, NOT the raw opposing trigger -- using
            # the raw trigger (as an earlier version of this backtest did)
            # makes entry-to-stop always exactly equal entry-to-target,
            # which silently forces every trade to resolve at exactly +/-1R.
            # kde_peaks=[] (can't be reconstructed historically from OHLCV
            # alone) -- so the further gravity-wall stop/target snap doesn't
            # fire here; this is a known, stated simplification, not hidden.
            targets = trade_structure_analyst.apply_trade_structure(
                levels, {"kde_peaks": []}, raw_targets
            )

            resampled_15m = sse_engine._resample(history_before_lock, 15)
            resampled_1h = sse_engine._resample(history_before_lock, 60)
            resampled_4h = sse_engine._resample(history_before_lock, 240)
            conf_15m = _confluence_reading(resampled_15m)
            conf_1h = _confluence_reading(resampled_1h)
            conf_4h = _confluence_reading(resampled_4h)
            stoch = _calc_stochastic_cross(resampled_15m) if len(resampled_15m) >= 17 else None

            decision, _gauges = decision_engine.evaluate_15m_decision(
                levels, targets, structure_state, conf_15m, conf_1h, conf_4h, stoch
            )

            entry_dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc)
            if decision["approval_status"] == "APPROVED":
                bias = decision["bias"]
                is_long = bias == "LONG"
                entry, stop = decision["entry_price"], decision["stop_loss"]
                t1, t2, t3 = decision["t1"], decision["t2"], decision["t3"]
                # Real gap Andy caught: ledger_closing_engine.py (verified
                # directly, not assumed: grep shows candle["l"]<=stop /
                # candle["h"]>=stop) resolves real trades against 1-MINUTE
                # candle wicks, checked every 60s. Resolving against 5m bars
                # here can't tell which of a stop/target hit first if both
                # occurred inside the same 5m bar -- a real precision gap, not
                # a rounding error. Fetch the real 1m candles for just this
                # trade's resolution window and resolve against those instead.
                resolution_candles = await _fetch_ohlcv(
                    exchange, symbol, "1m", lock_end_ts * 1000, session_end_ts * 1000
                )
                if not resolution_candles:
                    resolution_candles = post_lock_5m  # fallback if the 1m fetch comes back empty
                # Real gap Andy caught: exiting every win at T1 (1x measured
                # move) measures a far more conservative style than actually
                # holding for the staged T2 (1.618x)/T3 (2.618x) targets --
                # understating real reward potential. Track the FURTHEST
                # target reached before the stop is hit, not just T1/none.
                outcome, exit_r, furthest = "UNRESOLVED_IN_WINDOW", None, None
                for c in resolution_candles:
                    hit_stop = c["low"] <= stop if is_long else c["high"] >= stop
                    if hit_stop:
                        if furthest is None:
                            outcome, exit_r = "CLOSED_LOSS", _frac_r(entry, stop, stop, is_long)
                        else:
                            # Stop-loss-to-breakeven-or-better is the realistic
                            # policy once T1 is already banked -- exit price is
                            # whichever target was last confirmed reached.
                            exit_price = {"T1": t1, "T2": t2, "T3": t3}[furthest]
                            outcome, exit_r = f"CLOSED_WIN_AT_{furthest}", _frac_r(entry, stop, exit_price, is_long)
                        break
                    if (c["high"] >= t3 if is_long else c["low"] <= t3):
                        furthest = "T3"
                    elif (c["high"] >= t2 if is_long else c["low"] <= t2) and furthest != "T3":
                        furthest = "T2"
                    elif (c["high"] >= t1 if is_long else c["low"] <= t1) and furthest is None:
                        furthest = "T1"
                if outcome == "UNRESOLVED_IN_WINDOW" and furthest is not None:
                    # Window ended still in-trade past T1 -- unresolved, but
                    # record how far it had gotten for the reward-distribution
                    # analysis below (not counted as a win/loss either way).
                    outcome = f"UNRESOLVED_PAST_{furthest}"
                results.append({"date": entry_dt.date().isoformat(), "type": "APPROVED",
                                 "template": decision["tactical_brief"], "bias": bias,
                                 "outcome": outcome, "r": exit_r,
                                 "entry": entry, "stop": stop, "t1": t1,
                                 "macro_bias": macro_bias})
            else:
                sd_class = _classify_stand_down(post_lock_5m, bo, bd, distance)
                results.append({"date": entry_dt.date().isoformat(), "type": "STAND_DOWN",
                                 "reason": decision["tactical_brief"], "outcome": sd_class, "r": None})

        _report(results)
    finally:
        await exchange.close()


def _report(results: List[Dict[str, Any]]) -> None:
    approved = [r for r in results if r["type"] == "APPROVED"]
    stand_downs = [r for r in results if r["type"] == "STAND_DOWN"]
    print(f"\n{'='*70}\nPHASE 4 BACKTEST RESULTS -- N={len(results)} sessions evaluated\n{'='*70}")

    print(f"\n-- APPROVED (N={len(approved)}) --")
    resolved = [r for r in approved if r["r"] is not None]
    if resolved:
        wins = [r for r in resolved if r["outcome"] == "CLOSED_WIN"]
        win_rate = len(wins) / len(resolved) * 100
        avg_r = sum(r["r"] for r in resolved) / len(resolved)
        print(f"  Resolved: {len(resolved)}  Win rate: {win_rate:.1f}%  Avg R: {avg_r:+.3f}")
        for tmpl in sorted(set(r["template"] for r in approved)):
            sub = [r for r in resolved if r["template"] == tmpl]
            if sub:
                w = sum(1 for r in sub if r["outcome"] == "CLOSED_WIN")
                print(f"    {tmpl}: N={len(sub)} win_rate={w/len(sub)*100:.1f}%")
    unresolved = len(approved) - len(resolved)
    if unresolved:
        print(f"  Unresolved (window ended before stop/T1): {unresolved}")

    print(f"\n-- STAND_DOWN (N={len(stand_downs)}) -- quality, not just count --")
    for label in ["STAND_DOWN_SAVED", "STAND_DOWN_OVERCAUTIOUS", "STAND_DOWN_UNRESOLVED"]:
        n = sum(1 for r in stand_downs if r["outcome"] == label)
        if stand_downs:
            print(f"  {label}: {n} ({n/len(stand_downs)*100:.1f}%)")
    reasons: Dict[str, int] = {}
    for r in stand_downs:
        key = r["reason"].split(" (")[0]
        reasons[key] = reasons.get(key, 0) + 1
    print("  Reasons:", dict(sorted(reasons.items(), key=lambda x: -x[1])))

    # Split-period consistency: does the edge hold across both halves of the
    # sample, or does it come entirely from one lucky stretch?
    if resolved:
        resolved_sorted = sorted(resolved, key=lambda r: r["date"])
        mid = len(resolved_sorted) // 2
        first_half, second_half = resolved_sorted[:mid], resolved_sorted[mid:]
        print("\n-- SPLIT-PERIOD CHECK (does the edge hold in both halves?) --")
        for label, half in [("First half", first_half), ("Second half", second_half)]:
            if half:
                w = sum(1 for r in half if r["outcome"] == "CLOSED_WIN")
                avg_r_half = sum(r["r"] for r in half) / len(half)
                print(f"  {label} (N={len(half)}, {half[0]['date']} to {half[-1]['date']}): "
                      f"win_rate={w/len(half)*100:.1f}% avg_R={avg_r_half:+.3f}")

    if resolved:
        print(f"\n-- INDIVIDUAL RESOLVED TRADES (N={len(resolved)}) --")
        for r in sorted(resolved, key=lambda r: r["date"]):
            print(f"  {r['date']}  {r['template']:<28} {r['bias']:<5} "
                  f"macro_bias={r.get('macro_bias', '?'):<8} "
                  f"entry={r['entry']:<12.2f} stop={r['stop']:<12.2f} t1={r['t1']:<12.2f} "
                  f"-> {r['outcome']:<12} R={r['r']:+.3f}")

    if resolved:
        print("\n-- QUARTERLY BREAKDOWN (does the edge cluster by calendar quarter?) --")
        by_q: Dict[str, List[Dict[str, Any]]] = {}
        for r in resolved:
            d = r["date"]
            q = (int(d[5:7]) - 1) // 3 + 1
            key = f"{d[:4]}-Q{q}"
            by_q.setdefault(key, []).append(r)
        for key in sorted(by_q.keys()):
            sub = by_q[key]
            w = sum(1 for r in sub if r["outcome"] == "CLOSED_WIN")
            avg_r_q = sum(r["r"] for r in sub) / len(sub)
            longs = sum(1 for r in sub if r["bias"] == "LONG")
            shorts = len(sub) - longs
            print(f"  {key}: N={len(sub)} (L={longs}/S={shorts}) win_rate={w/len(sub)*100:.1f}% avg_R={avg_r_q:+.3f}")

        print("\n-- MACRO_BIAS ALIGNMENT (does trading WITH vs AGAINST macro_bias explain the split?) --")
        aligned = [r for r in resolved if (r["bias"] == "LONG" and r.get("macro_bias") == "BULLISH")
                   or (r["bias"] == "SHORT" and r.get("macro_bias") == "BEARISH")]
        against = [r for r in resolved if (r["bias"] == "LONG" and r.get("macro_bias") == "BEARISH")
                   or (r["bias"] == "SHORT" and r.get("macro_bias") == "BULLISH")]
        neutral = [r for r in resolved if r.get("macro_bias") == "NEUTRAL"]
        for label, sub in [("WITH macro_bias", aligned), ("AGAINST macro_bias", against), ("macro_bias NEUTRAL", neutral)]:
            if sub:
                w = sum(1 for r in sub if r["outcome"] == "CLOSED_WIN")
                avg_r_m = sum(r["r"] for r in sub) / len(sub)
                print(f"  {label}: N={len(sub)} win_rate={w/len(sub)*100:.1f}% avg_R={avg_r_m:+.3f}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbol", type=str, default="BTC/USDT")
    args = parser.parse_args()
    asyncio.run(run_backtest(args.symbol, args.days))
