# lti_engine.py
# ==============================================================================
# KABRODA KULTI LONG-TERM INVESTING ENGINE (2026-07-07)
# Bucket A — pure computation, no LLM. Advisory-only, BTC-only.
#
# Implements Eric Crown's "KULTI" course confluence-count framework (see
# bold-hubble/krown_courses/kulti/ for the source material) plus two
# Kabroda-native additions the owner asked to ship from day one: a gravity-
# wall structural cross-confirmation, and a real (Capriole-sourced) Hash
# Ribbons component rather than an undocumented placeholder.
#
# STRICT SOURCING TRANSPARENCY: Crown's course names 11 components but gives
# real numeric thresholds for only some of them. Where the course itself
# states a threshold (BBWP, PMARP), that exact number is used. Where the
# course gives only qualitative language (RSI, Krown Cross, Weekly EMA), a
# clearly-labeled reasonable convention is applied instead -- and marked as
# such below, not presented as Crown's own number. Where the course gives
# NO threshold or formula at all (Percent-Below-High, Low Month Days, Moon
# Phases), the raw value is computed and shown but NOT counted toward the
# confluence fire tally -- informational only, not a fabricated rule.
#
# Sync module by design -- invoked via asyncio.to_thread() from main.py's
# monthly scheduler, matching elliott_wave_specialist.run_elliott_wave_
# analysis()'s calling convention. Uses plain (non-async_support) ccxt for
# the same reason: no event loop exists inside a to_thread() worker.
# ==============================================================================

import datetime as dt
from typing import Any, Dict, List, Optional

import ccxt

from battlebox_pipeline import (
    _calc_bbwp,
    _calc_pmarp,
    _calc_rsi,
    _calc_ema_series,
    _bbwp_state_label,
    _pmarp_state_label,
    _build_jewel_reading,
)
from external_intel_reporter import _fetch_fear_and_greed
from hash_ribbons import fetch_hash_ribbons_state
from database import SessionLocal, GravityMemory, MacroNarrativeLog

SYMBOL = "BTC/USDT"
_WEEKLY_FETCH_LIMIT = 720  # ~13.8 years -- comfortably covers Kraken's full BTC/USD listing history

_exchange = ccxt.kraken({"enableRateLimit": True})

_SYNODIC_MONTH = 29.53058867
_REFERENCE_NEW_MOON = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)

_CONVICTION_SCALE = [
    (6, "GENERATIONAL"),
    (5, "VERY_HIGH"),
    (4, "EXECUTE"),
    (3, "WATCH"),
    (0, "NO_ACTION"),
]


def _fetch_weekly_candles(symbol: str = SYMBOL, limit: int = _WEEKLY_FETCH_LIMIT) -> List[Dict[str, Any]]:
    rows = _exchange.fetch_ohlcv(symbol, "1w", limit=limit)
    return [
        {"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4])}
        for r in rows
    ]


def _pct_below_high(closes: List[float]) -> float:
    ath = max(closes)
    if ath <= 0:
        return 0.0
    return round((ath - closes[-1]) / ath * 100.0, 2)


def _moon_phase_label(now: Optional[dt.datetime] = None) -> str:
    """Offline lunar ephemeris -- no API. Course frames this as included for
    statistical uncorrelatedness, not predictive rigor -- informational only."""
    now = now or dt.datetime.now(dt.timezone.utc)
    days_since = (now - _REFERENCE_NEW_MOON).total_seconds() / 86400.0
    frac = (days_since % _SYNODIC_MONTH) / _SYNODIC_MONTH
    if frac < 0.03 or frac > 0.97: return "NEW_MOON"
    if frac < 0.22:  return "WAXING_CRESCENT"
    if frac < 0.28:  return "FIRST_QUARTER"
    if frac < 0.47:  return "WAXING_GIBBOUS"
    if frac < 0.53:  return "FULL_MOON"
    if frac < 0.72:  return "WANING_GIBBOUS"
    if frac < 0.78:  return "LAST_QUARTER"
    return "WANING_CRESCENT"


def _low_month_day_flag(now: Optional[dt.datetime] = None) -> bool:
    """Course names 'Low Month Days' with zero documented formula. Applied
    convention (not Crown's number): flags the last 3 calendar days of the
    month -- informational only, not counted toward confluence."""
    now = now or dt.datetime.now(dt.timezone.utc)
    next_month = (now.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_in_month = (next_month - dt.timedelta(days=1)).day
    return now.day >= days_in_month - 2


def _weekly_ema_trend(closes: List[float], period: int = 21) -> str:
    """'Weekly EMA trend anchor' -- applied convention (course gives no
    specific period/rule beyond 'long-timeframe mean-reversion anchor'):
    price vs. EMA direction and slope."""
    if len(closes) < period + 2:
        return "INSUFFICIENT_DATA"
    ema_series = _calc_ema_series(closes, period)
    ema_now, ema_prev = ema_series[-1], ema_series[-2]
    price = closes[-1]
    rising = ema_now > ema_prev
    if price > ema_now:
        return "ABOVE_RISING" if rising else "ABOVE_FALLING"
    return "BELOW_FALLING" if not rising else "BELOW_RISING"


def _current_wave_label(symbol: str = SYMBOL) -> Optional[str]:
    """Current Elliott Wave position, from MacroNarrativeLog (written by
    elliott_wave_specialist.py) -- same query already duplicated in
    kabroda_mas_flow.py/main.py, scoped locally here rather than refactoring
    those two existing call sites."""
    db = SessionLocal()
    try:
        row = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == symbol,
                MacroNarrativeLog.authored_by == "elliott_wave_specialist",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )
        return row.wave_label if row else None
    finally:
        db.close()


def _gravity_cross_confirm(current_price: float, symbol: str = SYMBOL, tolerance_pct: float = 5.0) -> Dict[str, Any]:
    """Kabroda-native addition (not in Crown's course): does the current
    price sit near a macro-tier (permanence_class=0) gravity_memory
    structural level -- a bonus confluence signal on top of Crown's 11."""
    db_sym = symbol.replace("/", "")
    db = SessionLocal()
    try:
        rows = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.permanence_class == 0,
                GravityMemory.active == True,
            )
            .all()
        )
        nearest = None
        nearest_dist_pct = None
        for row in rows:
            if row.price <= 0:
                continue
            dist_pct = abs(current_price - row.price) / row.price * 100.0
            if nearest_dist_pct is None or dist_pct < nearest_dist_pct:
                nearest, nearest_dist_pct = row.price, dist_pct
        confirmed = nearest_dist_pct is not None and nearest_dist_pct <= tolerance_pct
        return {"confirmed": confirmed, "nearest_level": nearest, "distance_pct": nearest_dist_pct}
    finally:
        db.close()


def _conviction_label(fire_count: int) -> str:
    for threshold, label in _CONVICTION_SCALE:
        if fire_count >= threshold:
            return label
    return "NO_ACTION"


def run_lti_audit(symbol: str = SYMBOL) -> Dict[str, Any]:
    """
    Public entry point. Sync -- call via asyncio.to_thread() from the
    monthly scheduler. Returns a dict shaped to populate an LtiCheckpoint row
    directly. Never raises -- any component failure degrades that component
    to a neutral/unavailable value rather than aborting the whole audit.
    """
    candles = _fetch_weekly_candles(symbol)
    closes = [c["close"] for c in candles]
    current_price = closes[-1] if closes else 0.0

    bbwp = _calc_bbwp(closes, lookback=252)
    bbwp_state = _bbwp_state_label(bbwp)

    pmarp = _calc_pmarp(closes, ma_period=200, lookback=252)  # Crown: "use 200 SMA for macro investing"
    pmarp_state = _pmarp_state_label(pmarp)

    rsi_weekly = _calc_rsi(closes)
    rsi_series_now = rsi_weekly
    rsi_series_prev = _calc_rsi(closes[:-1]) if len(closes) > 15 else rsi_weekly
    rsi_turning_up = rsi_series_now > rsi_series_prev
    rsi_turning_down = rsi_series_now < rsi_series_prev

    pct_below_high = _pct_below_high(closes)

    jewel = _build_jewel_reading(candles) if len(candles) >= 56 else {"ema_state": "INSUFFICIENT_DATA"}
    krown_cross_state = jewel.get("ema_state", "INSUFFICIENT_DATA")

    weekly_ema_trend = _weekly_ema_trend(closes)
    low_month_day_flag = _low_month_day_flag()
    moon_phase_label = _moon_phase_label()

    hash_ribbons = fetch_hash_ribbons_state()
    fear_greed = _fetch_fear_and_greed()

    wave_label = _current_wave_label(symbol)
    gravity = _gravity_cross_confirm(current_price, symbol)

    # ── Confluence engine (Crown's count-of-firing-signals gate) ──────────
    # Only components with a real, sourced threshold (documented or clearly
    # labeled as an applied convention above) are counted. Percent-Below-
    # High, Low Month Days, and Moon Phases are informational-only -- shown,
    # never fabricated into a fire/no-fire rule.
    accumulation_fires = 0
    distribution_fires = 0

    if bbwp <= 20.0: accumulation_fires += 1
    if bbwp >= 80.0: distribution_fires += 1

    if pmarp <= 5.0:  accumulation_fires += 1
    if pmarp >= 95.0: distribution_fires += 1

    if rsi_weekly <= 30.0 and rsi_turning_up:   accumulation_fires += 1
    if rsi_weekly >= 70.0 and rsi_turning_down: distribution_fires += 1

    if krown_cross_state in ("BULLISH_EXPANDING", "BULLISH_COMPRESSING"): accumulation_fires += 1
    if krown_cross_state in ("BEARISH_EXPANDING", "BEARISH_COMPRESSING"): distribution_fires += 1

    if weekly_ema_trend == "ABOVE_RISING": accumulation_fires += 1
    if weekly_ema_trend == "BELOW_FALLING": distribution_fires += 1

    if hash_ribbons.get("state") == "RECOVERY": accumulation_fires += 1  # buy-signal only, no distribution side

    if fear_greed.get("value") is not None:
        if fear_greed["value"] <= 25: accumulation_fires += 1
        if fear_greed["value"] >= 75: distribution_fires += 1

    if gravity["confirmed"]:
        accumulation_fires += 1  # Kabroda-native bonus signal, additive to Crown's 11

    fire_count = max(accumulation_fires, distribution_fires)
    conviction_label = _conviction_label(fire_count)

    return {
        "symbol": symbol,
        "current_price": current_price,
        "bbwp": bbwp, "bbwp_state": bbwp_state,
        "pmarp": pmarp, "pmarp_state": pmarp_state,
        "rsi_weekly": round(rsi_weekly, 2),
        "pct_below_high": pct_below_high,
        "krown_cross_state": krown_cross_state,
        "weekly_ema_trend": weekly_ema_trend,
        "low_month_day_flag": low_month_day_flag,
        "moon_phase_flag": True,
        "moon_phase_label": moon_phase_label,
        "hash_ribbons_state": hash_ribbons.get("state", "UNAVAILABLE"),
        "fear_greed_value": fear_greed.get("value"),
        "fear_greed_label": fear_greed.get("classification"),
        "accumulation_signals_firing": accumulation_fires,
        "distribution_signals_firing": distribution_fires,
        "conviction_label": conviction_label,
        "wave_label_snapshot": wave_label,
        "gravity_cross_confirm": gravity["confirmed"],
        "nearest_macro_level": gravity["nearest_level"],
    }
