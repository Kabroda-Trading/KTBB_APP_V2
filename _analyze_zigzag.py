"""
ZigZag threshold comparison: 20% vs 12% vs 8%.
Pivot list filtered to bull-run window: 2022-11-01 to 2025-07-01.
No file changes.
"""
import asyncio, copy
from datetime import datetime, timezone
from typing import List, Dict, Any
import ccxt.async_support as ccxt

_ex = ccxt.mexc({"enableRateLimit": True})

WINDOW_START = datetime(2022, 11, 1,  tzinfo=timezone.utc).timestamp()
WINDOW_END   = datetime(2025, 7,  1,  tzinfo=timezone.utc).timestamp()

async def fetch(days=1200):
    out, now_ts = [], int(datetime.now(timezone.utc).timestamp() * 1000)
    since = now_ts - days * 86400 * 1000
    while len(out) < days:
        rows = await _ex.fetch_ohlcv("BTC/USDT", "1d", since=since, limit=1000)
        if not rows: break
        for r in rows:
            out.append({"time": int(r[0]/1000), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4])})
        since = int(rows[-1][0]) + 1
        await asyncio.sleep(0.3)
    return out[-days:]

def zigzag(candles, dev):
    pivots, trend = [], 1
    ep, ei = candles[0]["high"], 0
    for i, c in enumerate(candles):
        h, l = c["high"], c["low"]
        if trend == 1:
            if h > ep:              ep, ei = h, i
            elif l < ep*(1-dev):    pivots.append({"t":"PEAK",   "p":ep,"i":ei}); trend=-1; ep,ei=l,i
        else:
            if l < ep:              ep, ei = l, i
            elif h > ep*(1+dev):    pivots.append({"t":"TROUGH", "p":ep,"i":ei}); trend= 1; ep,ei=h,i
    return pivots

def anchors(candles, dev):
    for i,c in enumerate(candles): c["ai"] = i
    raw = zigzag(candles, dev)

    top_c  = max(candles, key=lambda c: c["high"])
    top_p, top_i = top_c["high"], top_c["ai"]
    orig_c = min(candles[:top_i+1], key=lambda c: c["low"])
    orig_p, orig_i = orig_c["low"], orig_c["ai"]

    result = [("CYCLE_ORIGIN", orig_p), ("CYCLE_TOP", top_p)]

    bull = [p for p in raw if orig_i < p["i"] < top_i]
    pks  = [p for p in bull if p["t"]=="PEAK"]
    trs  = [p for p in bull if p["t"]=="TROUGH"]
    axiom = "FAIL"

    if len(pks)>=2 and len(trs)>=2:
        mid = orig_p + (top_p - orig_p)*0.5
        ut  = [t for t in trs if t["p"] > mid]
        if ut:
            w4 = min(ut, key=lambda t: t["p"])
            vw3 = [p for p in pks if p["i"] < w4["i"]]
            if vw3:
                w3 = max(vw3, key=lambda p: p["p"])
                vw2 = [t for t in trs if t["i"] < w3["i"]]
                if vw2:
                    w2 = min(vw2, key=lambda t: t["p"])
                    vw1 = [p for p in pks if p["i"] < w2["i"]]
                    if vw1:
                        w1 = max(vw1, key=lambda p: p["p"])
                        if w4["p"] > w1["p"] and w2["p"] > orig_p:
                            result += [("BULL_WAVE_1",w1["p"]),("BULL_WAVE_2",w2["p"]),
                                       ("BULL_WAVE_3",w3["p"]),("BULL_WAVE_4",w4["p"])]
                            axiom = "PASS"

    bear = [p for p in raw if p["i"] > top_i]
    bpks = [p for p in bear if p["t"]=="PEAK"]
    btrs = [p for p in bear if p["t"]=="TROUGH"]
    if btrs:
        bw3 = min(btrs, key=lambda t: t["p"])
        result.append(("BEAR_WAVE_3_LOW", bw3["p"]))
        vbw4 = [p for p in bpks if p["i"] > bw3["i"]]
        if vbw4: result.append(("BEAR_WAVE_4_BOUNCE", max(vbw4,key=lambda p:p["p"])["p"]))
        vbw2 = [p for p in bpks if p["i"] < bw3["i"]]
        if vbw2:
            bw2 = max(vbw2, key=lambda p: p["p"])
            vbw1 = [t for t in btrs if t["i"] < bw2["i"]]
            if vbw1:
                bw1 = min(vbw1, key=lambda t: t["p"])
                if bw2["p"] < top_p:
                    result += [("BEAR_WAVE_1_MSB",bw1["p"]),("BEAR_WAVE_2",bw2["p"])]

    bull_n = len([p for p in raw if orig_i < p["i"] < top_i])
    bear_n = len([p for p in raw if p["i"] > top_i])
    return result, raw, axiom, bull_n, bear_n

async def main():
    print("Fetching 1200 days of BTC/USDT daily from MEXC...")
    c = await fetch(1200)
    print(f"Got {len(c)} candles  ({datetime.fromtimestamp(c[0]['time'],tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(c[-1]['time'],tz=timezone.utc).strftime('%Y-%m-%d')})")
    print(f"Range: low=${min(x['low'] for x in c):,.0f}  high=${max(x['high'] for x in c):,.0f}")

    print()
    print("NOTE: Current code already uses c['high']/c['low'] for wick extremes.")
    print("      The wick requirement is already met. Only deviation_pct changes.")

    for label, dev in [("CURRENT  20%", 0.20), ("OPTION   12%", 0.12), ("OPTION    8%", 0.08)]:
        c2 = copy.deepcopy(c)
        anc, raw, axiom, bull_n, bear_n = anchors(c2, dev)
        total = len(raw)

        print()
        print("="*68)
        print(f"  {label}  |  axiom: {axiom}  |  total pivots: {total}  (bull {bull_n} / bear {bear_n})")
        print("-"*68)
        notes = {
            "CYCLE_ORIGIN":       "cycle low",
            "CYCLE_TOP":          "ATH",
            "BULL_WAVE_1":        "W1 peak",
            "BULL_WAVE_2":        "W2 low",
            "BULL_WAVE_3":        "W3 peak",
            "BULL_WAVE_4":        "W4 low",
            "BEAR_WAVE_3_LOW":    "bear W3 low",
            "BEAR_WAVE_4_BOUNCE": "bear W4 bounce",
            "BEAR_WAVE_1_MSB":    "bear W1 low",
            "BEAR_WAVE_2":        "bear W2 peak",
        }
        for name, price in anc:
            print(f"  {name:28s}  ${price:>11,.2f}   {notes.get(name,'')}")

        # Pivot list filtered to bull-run window only
        window = [p for p in raw if WINDOW_START <= c[p["i"]]["time"] <= WINDOW_END]
        print()
        print(f"  --- Bull-run pivots  2022-11-01 to 2025-07-01  ({len(window)} pivots) ---")
        for p in window:
            dt = datetime.fromtimestamp(c[p["i"]]["time"], tz=timezone.utc).strftime("%Y-%m-%d")
            kind = "PEAK  " if p["t"]=="PEAK" else "TROUGH"
            print(f"  {kind}  {dt}   ${p['p']:>11,.2f}")

    await _ex.close()

asyncio.run(main())
