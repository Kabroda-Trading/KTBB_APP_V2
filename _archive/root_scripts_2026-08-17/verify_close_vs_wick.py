# verify_close_vs_wick.py
# ==============================================================================
# STANDALONE READ-ONLY VERIFICATION TOOL — punch-list item #2
#
# NOT wired into main.py. NOT production code. Makes ZERO writes to the
# database. Run directly:
#   DATABASE_URL=<production postgres url> python verify_close_vs_wick.py
#   python verify_close_vs_wick.py --database-url <production postgres url>
#   python verify_close_vs_wick.py --limit 50 --symbol BTC/USDT
#
# PURPOSE: quantify, on real historical closed trades, how often the outcome
# would differ between the OLD mechanism (ledger_closing_engine.py's current
# 1-minute wick-touch stop/T1 detection) and the NEW mechanism (a confirmed
# close on the trade's own trading timeframe -- 15M/1H/4H) BEFORE that fix
# is deployed to production. Reads existing CampaignLog rows only; refetches
# real Kraken OHLCV for the relevant historical window per row and
# independently recomputes both outcomes.
#
# HONEST SCOPE: this recomputes what each mechanism WOULD have said, using
# the same OHLCV inputs the production engine itself uses (ccxt kraken).
# recorded_status (what production actually wrote) should match
# wick_recomputed exactly -- if it doesn't for some row, that is a signal
# the recompute logic or the fetched history diverges from what production
# saw at the time (rate-limited gaps, symbol formatting, etc.), not a
# reason to trust the close_recomputed column for that row without checking.
# ==============================================================================

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

_TIMEFRAME_TO_CCXT = {"15M": "15m", "4H": "4h", "1H": "1h"}
_TIMEFRAME_TO_MINUTES = {"15M": 15, "4H": 240, "1H": 60}


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, until_ms: int) -> List[Dict]:
    """Paginated ccxt kraken fetch, bounded to [since_ms, until_ms]."""
    fmt = symbol if "/" in symbol else symbol.replace("USDT", "/USDT")
    all_rows = []
    cursor = since_ms
    while cursor < until_ms:
        batch = await exchange.fetch_ohlcv(fmt, timeframe, since=cursor, limit=500)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        next_cursor = last_ts + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 500:
            break
    return [
        {"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
        for r in all_rows
        if r[0] <= until_ms
    ]


def _recompute_wick(candles_1m: List[Dict], bias: str, stop: float, t1: Optional[float]) -> str:
    """Old mechanism: first 1m candle whose high/low touches stop or T1. Stop-first tiebreak."""
    for candle in candles_1m:
        if bias == "LONG":
            hit_stop = candle["l"] <= stop
            hit_t1 = t1 is not None and candle["h"] >= t1
        else:
            hit_stop = candle["h"] >= stop
            hit_t1 = t1 is not None and candle["l"] <= t1
        if hit_stop:
            return "CLOSED_LOSS"
        if hit_t1:
            return "CLOSED_WIN"
    return "UNRESOLVED_IN_WINDOW"


def _recompute_close(candles_native: List[Dict], bias: str, stop: float, t1: Optional[float]) -> str:
    """New mechanism: first native-interval candle whose CLOSE confirms stop or T1."""
    for candle in candles_native:
        if bias == "LONG":
            hit_stop = candle["c"] <= stop
            hit_t1 = t1 is not None and candle["c"] >= t1
        else:
            hit_stop = candle["c"] >= stop
            hit_t1 = t1 is not None and candle["c"] <= t1
        if hit_stop:
            return "CLOSED_LOSS"
        if hit_t1:
            return "CLOSED_WIN"
    return "UNRESOLVED_IN_WINDOW"


async def main():
    ap = argparse.ArgumentParser(description="Compare wick-based vs. close-based stop/T1 outcomes on real historical closed trades.")
    ap.add_argument("--database-url", default=None, help="Production DATABASE_URL. If omitted, reads the DATABASE_URL env var (falls back to local sqlite).")
    ap.add_argument("--limit", type=int, default=200, help="Max number of historical rows to check (most recent first).")
    ap.add_argument("--symbol", default=None, help="Filter to one symbol, e.g. BTC/USDT.")
    args = ap.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    # Import after DATABASE_URL is set -- database.py builds its engine at import time.
    import database
    from database import CampaignLog

    import ccxt.async_support as ccxt

    db = database.SessionLocal()
    query = db.query(CampaignLog).filter(
        CampaignLog.is_canonical == True,
        CampaignLog.closed_at.isnot(None),
        CampaignLog.entry_filled_at.isnot(None),
        CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
    )
    if args.symbol:
        query = query.filter(CampaignLog.symbol == args.symbol)
    rows = query.order_by(CampaignLog.closed_at.desc()).limit(args.limit).all()
    db.close()

    print(f"Found {len(rows)} historical closed rows (is_canonical, CLOSED_WIN/CLOSED_LOSS) to check.\n")
    if not rows:
        print("Nothing to compare. Exiting.")
        return

    exchange = ccxt.kraken({"enableRateLimit": True})
    results = []

    try:
        for i, c in enumerate(rows):
            timeframe_label = c.session_timeframe or "15M"
            interval = _TIMEFRAME_TO_CCXT.get(timeframe_label)
            if interval is None:
                print(f"[{i+1}/{len(rows)}] {c.symbol} id={c.id}: unrecognized session_timeframe={timeframe_label!r}, skipping.")
                continue

            since_dt = _as_utc(c.entry_filled_at)
            until_dt = _as_utc(c.closed_at) + timedelta(minutes=_TIMEFRAME_TO_MINUTES[timeframe_label])
            since_ms = int(since_dt.timestamp() * 1000)
            until_ms = int(until_dt.timestamp() * 1000)

            try:
                candles_1m = await _fetch_ohlcv(exchange, c.symbol, "1m", since_ms, until_ms)
                candles_native = await _fetch_ohlcv(exchange, c.symbol, interval, since_ms, until_ms)
            except Exception as e:
                print(f"[{i+1}/{len(rows)}] {c.symbol} id={c.id}: fetch error, skipping ({e}).")
                continue

            if not candles_1m or not candles_native:
                print(f"[{i+1}/{len(rows)}] {c.symbol} id={c.id}: no candle data returned, skipping.")
                continue

            wick_outcome = _recompute_wick(candles_1m, c.bias, c.stop_loss, c.t1)
            close_outcome = _recompute_close(candles_native, c.bias, c.stop_loss, c.t1)
            agree = wick_outcome == close_outcome
            wick_matches_recorded = wick_outcome == c.status

            results.append({
                "id": c.id, "symbol": c.symbol, "tf": timeframe_label, "bias": c.bias,
                "recorded": c.status, "wick_recomputed": wick_outcome, "close_recomputed": close_outcome,
                "agree": agree, "wick_matches_recorded": wick_matches_recorded,
            })
            print(f"[{i+1}/{len(rows)}] id={c.id} {c.symbol} {timeframe_label} {c.bias}: "
                  f"recorded={c.status} wick={wick_outcome} close={close_outcome} "
                  f"{'MATCH' if agree else 'FLIP'}{'  [wick-recompute != recorded!]' if not wick_matches_recorded else ''}")
    finally:
        await exchange.close()

    print(f"\n{'='*90}")
    print("SUMMARY")
    print(f"{'='*90}")

    n = len(results)
    if n == 0:
        print("No rows successfully compared.")
        return

    n_agree = sum(1 for r in results if r["agree"])
    n_flip = n - n_agree
    n_wick_mismatch = sum(1 for r in results if not r["wick_matches_recorded"])
    win_to_loss = sum(1 for r in results if r["wick_recomputed"] == "CLOSED_WIN" and r["close_recomputed"] == "CLOSED_LOSS")
    loss_to_win = sum(1 for r in results if r["wick_recomputed"] == "CLOSED_LOSS" and r["close_recomputed"] == "CLOSED_WIN")
    win_to_unresolved = sum(1 for r in results if r["wick_recomputed"] == "CLOSED_WIN" and r["close_recomputed"] == "UNRESOLVED_IN_WINDOW")
    loss_to_unresolved = sum(1 for r in results if r["wick_recomputed"] == "CLOSED_LOSS" and r["close_recomputed"] == "UNRESOLVED_IN_WINDOW")

    print(f"N compared: {n}")
    print(f"Agree (wick outcome == close outcome): {n_agree} ({n_agree/n*100:.1f}%)")
    print(f"Flip (would resolve differently under close-confirmation): {n_flip} ({n_flip/n*100:.1f}%)")
    print(f"  WIN -> LOSS flips:        {win_to_loss}")
    print(f"  LOSS -> WIN flips:        {loss_to_win}")
    print(f"  WIN -> unresolved-in-window:  {win_to_unresolved}")
    print(f"  LOSS -> unresolved-in-window: {loss_to_unresolved}")
    print(f"\nSanity check -- wick_recomputed should equal the DB's recorded status "
          f"(production already used the wick mechanism): {n - n_wick_mismatch}/{n} match.")
    if n_wick_mismatch:
        print(f"  {n_wick_mismatch} row(s) where recompute != recorded -- inspect these individually "
              f"before trusting their close_recomputed value (likely a fetch/rate-limit gap, not a real disagreement).")

    if n < 30:
        print(f"\nNOTE: N={n} is below this project's own N>=30 trust threshold. Treat the flip rate above "
              f"as a directional first read, not a statistically confident estimate.")


if __name__ == "__main__":
    asyncio.run(main())
