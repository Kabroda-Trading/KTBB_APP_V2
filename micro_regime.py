# micro_regime.py
# ==============================================================================
# 15M BOARD-TEXTURE REGIME — is there participation, or is the tape dead?
#
# Ported (2026-08-30) from `Kabroda AI Brain`'s brain/engine/regime.py (named
# micro_regime here, not regime, to avoid colliding with structure_state's
# existing vocabulary in this codebase). Backs KABRODA_REBUILD_SPEC.md §5
# Hard Veto #4: DEAD 15m regime -> no participation -> stand aside.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List

from market_data import _calc_adx, _calc_bbwp, _calc_ema_series

BBWP_COMPRESSED = 38.0
BBWP_EXPANDED = 70.0
ADX_TRENDING = 24.0
ADX_DEAD = 14.0


def _recent_range_pct(candles: List[Dict[str, Any]], lookback: int = 20) -> float:
    seg = candles[-lookback:]
    if not seg:
        return 0.0
    hi = max(float(c["high"]) for c in seg)
    lo = min(float(c["low"]) for c in seg)
    mid = (hi + lo) / 2.0
    return (hi - lo) / mid * 100.0 if mid else 0.0


def _vol_trend(candles: List[Dict[str, Any]], lookback: int = 30) -> float:
    seg = [float(c.get("volume") or 0) for c in candles[-lookback * 2:]]
    if len(seg) < lookback * 2:
        return 1.0
    prior = sum(seg[:lookback]) / lookback
    recent = sum(seg[lookback:]) / lookback
    return round(recent / prior, 2) if prior > 0 else 1.0


def classify_regime(candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candles_15m or len(candles_15m) < 60:
        return {"regime": "UNKNOWN", "notes": "insufficient 15m data"}

    closes = [float(c["close"]) for c in candles_15m]
    bbwp_v = _calc_bbwp(closes)
    adx_d = _calc_adx(candles_15m)
    adx_v = adx_d.get("adx", 0.0)
    adx_rising = adx_d.get("rising", False)
    rng = _recent_range_pct(candles_15m)
    vt = _vol_trend(candles_15m)

    e9 = _calc_ema_series(closes, 9)
    e55 = _calc_ema_series(closes, 55)
    ribbon_aligned = bool(e9 and e55 and abs(e9[-1] - e55[-1]) / e55[-1] * 100.0 > 0.15)

    if bbwp_v <= BBWP_COMPRESSED and adx_v < ADX_DEAD and vt < 0.7:
        regime = "DEAD"
    elif adx_v >= ADX_TRENDING and (adx_rising or ribbon_aligned):
        regime = "TRENDING"
    elif bbwp_v <= BBWP_COMPRESSED and adx_v < ADX_TRENDING:
        regime = "COILED"
    elif bbwp_v >= BBWP_EXPANDED:
        regime = "EXPANSION"
    else:
        regime = "TRANSITIONAL"

    note = (f"BBWP {bbwp_v:.0f}, ADX {adx_v:.0f}"
            f"{' rising' if adx_rising else ''}, 20-bar range {rng:.2f}%, "
            f"vol trend {vt}x -> {regime}")

    return {
        "regime": regime,
        "bbwp": round(bbwp_v, 1),
        "adx": round(adx_v, 1),
        "adx_rising": adx_rising,
        "range_pct": round(rng, 2),
        "vol_trend": vt,
        "notes": note,
    }
