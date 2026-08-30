# market_regime.py
# ==============================================================================
# MACRO MARKET REGIME — "is this a good table?"
#
# Ported (2026-08-30) from `Kabroda AI Brain`'s brain/engine/market_regime.py.
# Backs KABRODA_REBUILD_SPEC.md §5 Hard Veto #2: counter-trend on a GOOD table
# gets capped below TAKE regardless of what the core gate says. The old
# "ev_bar_mult" policy effect is dead weight here (EV doesn't gate anymore,
# per the spec) — only `quality`/`bias` are used, for the counter-trend check.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List

from market_data import _calc_adx, _calc_ema_series

ER_TREND = 0.45          # efficiency ratio >= this = trending
ER_CHOP = 0.30           # <= this = chop
ADX_D_TREND = 22.0
LOOKBACK = 20            # daily bars (~4 weeks)


def _efficiency_ratio(closes: List[float], n: int = LOOKBACK) -> float:
    seg = closes[-(n + 1):]
    if len(seg) < n + 1:
        return 0.0
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    return net / path if path else 0.0


def _fakeout_rate(candles: List[Dict[str, Any]], n: int = LOOKBACK, brk: int = 10) -> float:
    seg = candles[-(n + brk + 3):]
    if len(seg) < brk + 6:
        return 0.0
    breaks = fails = 0
    for i in range(brk, len(seg) - 3):
        prior_hi = max(float(c["high"]) for c in seg[i - brk:i])
        prior_lo = min(float(c["low"]) for c in seg[i - brk:i])
        c = float(seg[i]["close"])
        if c > prior_hi:
            breaks += 1
            if any(float(seg[i + j]["close"]) < prior_hi for j in range(1, 4)):
                fails += 1
        elif c < prior_lo:
            breaks += 1
            if any(float(seg[i + j]["close"]) > prior_lo for j in range(1, 4)):
                fails += 1
    return fails / breaks if breaks else 0.0


def _vol_trend(candles: List[Dict[str, Any]], n: int = LOOKBACK) -> float:
    tr = []
    for i in range(1, len(candles)):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr) < n * 2:
        return 1.0
    prior = sum(tr[-n * 2:-n]) / n
    recent = sum(tr[-n:]) / n
    return recent / prior if prior else 1.0


def classify_market_regime(daily_candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not daily_candles or len(daily_candles) < LOOKBACK + 15:
        return {"table": "UNKNOWN", "quality": "MARGINAL",
                "notes": "insufficient daily history",
                "policy": {"bias": None}}

    closes = [float(c["close"]) for c in daily_candles]
    er = _efficiency_ratio(closes)
    adx_d = _calc_adx(daily_candles).get("adx", 0.0)
    fake = _fakeout_rate(daily_candles)
    vt = _vol_trend(daily_candles)

    e20 = _calc_ema_series(closes, 20)
    e50 = _calc_ema_series(closes, 50) if len(closes) >= 50 else e20
    direction = "UP" if e20 and e50 and e20[-1] > e50[-1] else "DOWN"
    net20 = (closes[-1] - closes[-(LOOKBACK + 1)]) / closes[-(LOOKBACK + 1)] * 100 \
        if len(closes) > LOOKBACK else 0.0

    trending = er >= ER_TREND and adx_d >= ADX_D_TREND
    dead = vt < 0.6 and adx_d < 15 and er < ER_CHOP
    chop = (er <= ER_CHOP or fake >= 0.5) and not trending

    if dead:
        table, quality = "DEAD", "BAD"
    elif trending:
        table = "TRENDING_UP" if net20 >= 0 else "TRENDING_DOWN"
        quality = "GOOD"
    elif chop:
        table, quality = "CHOP", "BAD"
    else:
        table = "VOLATILE_NO_TREND" if vt > 1.2 else "MIXED"
        quality = "MARGINAL"

    bias = direction if quality == "GOOD" else None
    note = (f"ER {er:.2f}, daily ADX {adx_d:.0f}, fakeout rate {fake:.0%}, "
            f"vol trend {vt:.2f}x, 20d net {net20:+.1f}% -> {table} ({quality})")

    return {
        "table": table,
        "quality": quality,
        "trend_direction": direction if trending else "NONE",
        "efficiency_ratio": round(er, 3),
        "adx_daily": round(adx_d, 1),
        "fakeout_rate": round(fake, 3),
        "vol_trend": round(vt, 2),
        "net_20d_pct": round(net20, 2),
        "policy": {"bias": bias},
        "notes": note,
    }
