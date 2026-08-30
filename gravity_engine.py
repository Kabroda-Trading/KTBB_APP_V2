# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION & BEDROCK LOGGING)
# TARGET LOGIC v4: windowed nearest-pivot stop (recency-bounded, no strength gate),
#   Fibonacci-staged T1/T2/T3 (1.0x/1.618x/2.618x of the entry-to-stop leg), ATR safety rails.
#   (v3 = single structural target from break level, frozen 2026-07-04;
#    v2 = legacy staged T1/T2/T3 from unqualified opposing zone, frozen 2026-07-01
#    — see database.py CampaignLog comment for all three shapes)
# ==============================================================================
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import subprocess

from database import SessionLocal, GravityMemory, DecisionJournal, CampaignLog
import battlebox_pipeline  # <-- SINGLE SOURCE OF TRUTH ENFORCED
import notify

# Revin Suite import removed 2026-08-17 -- was unused here (compute_revin_suite
# imported but never called in this file). mtf_confluence_scanner import
# removed 2026-08-30 -- its only caller here was the now-removed 1H/4H BOS
# detection block; nothing else in this file uses it.

# Position Sizing (IMP-003) — from bold-hubble package
from position_sizing import calc_position_size

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# ---------------------------------------------------------------------------
# HELPER: ATR
# NOTE: excludes the still-forming last candle first, matching _scan_for_pivots'
# own closed_candles = candles[:-1] convention (previously inconsistent — fixed
# 2026-07-04 as part of the v4 stop/target rework, since this directly feeds the
# ATR fallback/rails used by that math).
# ---------------------------------------------------------------------------
def _calc_atr(candles: List[Dict], period: int = 14) -> float:
    closed = candles[:-1]
    if len(closed) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        h = float(closed[i]["high"])
        l = float(closed[i]["low"])
        prev_c = float(closed[i - 1]["close"])
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


# ---------------------------------------------------------------------------
# HELPER: NEAREST PIVOT IN WINDOW (v4 stop selection)
# Replaces the old _qualified_4h/_qualified_1h closures, which gated candidate
# zones by heat_multiplier/touch_count/departure_move_pct BEFORE ordering by
# price-proximity -- confirmed (real 2026-07-03 example) to skip past the
# nearest genuinely relevant pivot in favor of a distant one that happened to
# clear the strength bar. Validated via mtf_backtest_lab.py --window-test
# (2026-07-04): recency ordering plateaus cleanly on both 1H and 4H real
# history; price-proximity ordering never stabilizes (same "expanding pool"
# artifact as the original bug) -- so this orders by TIMESTAMP, not price.
# ---------------------------------------------------------------------------
def _nearest_pivot_in_window(db, db_sym: str, source: str, level_type: str, price_filter, window_start: datetime):
    return (
        db.query(GravityMemory)
        .filter(
            GravityMemory.symbol == db_sym,
            GravityMemory.source == source,
            GravityMemory.level_type == level_type,
            GravityMemory.active == True,
            GravityMemory.timestamp >= window_start,
            price_filter,
        )
        .order_by(GravityMemory.timestamp.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# HELPER: ENERGY GRADE
# Reads trend/momentum alignment for a given TF's candles.
# STRONG = aligned + MACD magnitude trending; MODERATE = aligned but weak;
# WEAK = trend counter-directional or insufficient data.
# ---------------------------------------------------------------------------
def _compute_energy_grade(candles: List[Dict], bias: str) -> str:
    if not candles or len(candles) < 50:
        return "WEAK"
    closes = [float(c["close"]) for c in candles]
    ema30 = battlebox_pipeline._calc_ema_series(closes, 30)[-1]
    ema50 = battlebox_pipeline._calc_ema_series(closes, 50)[-1]
    trend_bullish = ema30 > ema50
    macd = battlebox_pipeline._calc_macd(closes)
    hist_bps = abs(macd["hist"] / ema50 * 10000) if ema50 != 0 else 0
    macd_strength = "STRONG" if hist_bps >= 20 else "WEAK" if hist_bps >= 5 else "DEPLETED"
    aligned = (bias == "LONG" and trend_bullish) or (bias == "SHORT" and not trend_bullish)
    if aligned and macd_strength == "STRONG":
        grade = "STRONG"
    elif aligned:
        grade = "MODERATE"
    else:
        grade = "WEAK"
    # Crown PMARP cap (Cut 5): overextended/depressed price → cap grade on trend-following entries.
    # Only runs with ≥252 bars (enough history for meaningful percentile). No cap below threshold.
    if len(closes) >= 252:
        pmarp = battlebox_pipeline._calc_pmarp(closes)
        if bias == "LONG":
            if pmarp >= 95.0:
                grade = "WEAK"                              # parabolic extension — chasing
            elif pmarp >= 85.0 and grade == "STRONG":
                grade = "MODERATE"                          # stretched, not extreme
        elif bias == "SHORT":
            if pmarp <= 5.0:
                grade = "WEAK"                              # capitulation depth — mean reversion risk
            elif pmarp <= 15.0 and grade == "STRONG":
                grade = "MODERATE"
    return grade


# ---------------------------------------------------------------------------
# HELPER: KINEMATIC GRADE (second, observational energy signal — 2026-07-05)
# Ports the 15M JEWEL's own PRIMED/TANGLED/OVEREXTENDED formula (BBWP/PMARP/
# ribbon-spread based, direction-agnostic market-state read) to 4H/1H, as a
# SECOND signal alongside _compute_energy_grade -- NOT a replacement, and
# NOT enforced. RECORD-ONLY: backtested against v4-consistent trade
# construction (mtf_backtest_lab.py, N=167 1H / N=177 4H) and found no
# clean, reliable signal at current sample sizes on either timeframe --
# this formula is actually backwards on 4H (OVEREXTENDED outperforms
# PRIMED), confirming its 252-bar lookback (42 real days on 4H vs. 2.6 days
# on 15M) doesn't transfer across timeframes. See WORK_LOG.md 2026-07-05.
# ---------------------------------------------------------------------------
def _compute_kinematic_grade(candles: List[Dict]) -> str:
    if not candles or len(candles) < 200:
        return "TANGLED"
    closes = [float(c["close"]) for c in candles]
    ema9 = battlebox_pipeline._calc_ema_series(closes, 9)[-1]
    ema55 = battlebox_pipeline._calc_ema_series(closes, 55)[-1]
    ribbon_spread = abs(ema9 - ema55) / ema55 * 100
    bbwp_val = battlebox_pipeline._calc_bbwp(closes)
    pmarp_val = battlebox_pipeline._calc_pmarp(closes)
    if pmarp_val >= 85.0:
        return "OVEREXTENDED"
    elif bbwp_val <= 30.0 and ribbon_spread > 0.05:
        return "PRIMED"
    else:
        return "TANGLED"


# ---------------------------------------------------------------------------
# HELPER: MACRO BIAS + WEEKLY 200 SMA POSITION (punch-list item #5, 2026-07-06)
# macro_bias reuses battlebox_pipeline._calculate_weekly_force() (21-day vs
# 7-day daily SMA crossover) exactly as the 15M system already computes it --
# no reimplementation. Backtested against v4-consistent construction:
#   1H: aligned-with-bias signals clearly outperform counter-trend
#       (58.3%/+0.257R, N=84 vs 46.4%/-0.028R, N=69) -- HARD GATE on 1H.
#   4H: INVERTED (counter-trend outperforms aligned, N=74-76) -- record only,
#       do not gate; blocking would remove the currently-winning subset.
# weekly_200sma_position reuses battlebox_pipeline._fetch_weekly_200sma()
# (a gravity_memory row read, written every 24h by the macro engine) + the
# same +-0.5% threshold _compute_mtf_structural_snapshot() already uses.
# RECORD-ONLY on both timeframes -- not independently backtested (would need
# ~1400+ days of daily history to test rigorously). See WORK_LOG.md 2026-07-06.
# ---------------------------------------------------------------------------
def _compute_macro_bias(candles_1d: List[Dict]) -> str:
    return battlebox_pipeline._calculate_weekly_force(candles_1d)


def _compute_weekly_200sma_position(symbol: str, current_price: float) -> Any:
    w200sma = battlebox_pipeline._fetch_weekly_200sma(symbol)
    if not w200sma or w200sma <= 0 or current_price <= 0:
        return None
    dist = (current_price - w200sma) / w200sma * 100.0
    return "ABOVE" if dist > 0.5 else "BELOW" if dist < -0.5 else "AT"


# ---------------------------------------------------------------------------
# HELPER: EXTRACT REVIN SUITE FROM CONFLUENCE DICT
# Pulls the Revin Suite fields for a specific timeframe from the MTF
# confluence scanner output. RECORD-ONLY -- feeds audit_ai.py's Revin
# alignment hypothesis, does not gate candidate creation.
# ---------------------------------------------------------------------------
# _extract_revin_from_confluence() removed 2026-08-30 -- fed Revin Suite
# fields to _detect_4h_bos()/_detect_1h_bos() only, both gone. No other caller.

# ---------------------------------------------------------------------------
# BEDROCK / RADAR LOGGING
# ---------------------------------------------------------------------------
def log_kabroda_bedrock(symbol: str, levels: dict, lock_ts: int):
    """
    Logs the 7-Day macro anchors (Daily S/R, 30m boundaries, Triggers).

    Each level_type is a single rolling per-session snapshot -- only the latest
    should be active. Before 2026-07-06 this never deactivated the previous
    session's rows: 6 new rows get written every session lock, forever, and
    this source ALSO gets an extra +1.5 KDE weight bonus specifically for
    source=="7_DAY_KABRODA" (gravity_math.py) -- making stale accumulation here
    the single largest contributor to the duplicate-inflated "wall" bug found
    2026-07-06 (see WORK_LOG.md and log_radar_anchors() above).
    """
    db = SessionLocal()
    try:
        # gravity_memory stores symbols without a slash (BTCUSDT), matching
        # calculate_gravity_kde()'s and the BOS detectors' query format --
        # see CLAUDE.md's documented gravity_memory symbol exception.
        db_sym = symbol.replace("/", "")
        dt = datetime.fromtimestamp(lock_ts, tz=timezone.utc)
        exists = db.query(GravityMemory).filter(
            GravityMemory.symbol == db_sym, GravityMemory.timestamp == dt, GravityMemory.source == "7_DAY_KABRODA"
        ).first()
        if exists: return
        mapping = {
            "BREAKOUT": levels.get("breakout_trigger", 0), "BREAKDOWN": levels.get("breakdown_trigger", 0),
            "DAILY_RESISTANCE": levels.get("daily_resistance", 0), "DAILY_SUPPORT": levels.get("daily_support", 0),
            "30M_HIGH": levels.get("range30m_high", 0), "30M_LOW": levels.get("range30m_low", 0),
        }
        for l_type, price in mapping.items():
            if float(price) > 0:
                db.query(GravityMemory).filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "7_DAY_KABRODA",
                    GravityMemory.level_type == l_type,
                    GravityMemory.active == True,
                ).update({"active": False})
                mem = GravityMemory(symbol=db_sym, timestamp=dt, source="7_DAY_KABRODA", level_type=l_type, price=float(price), permanence_class=2, heat_multiplier=1.0)
                db.add(mem)
        db.commit()
        print(f"|| GRAVITY BEDROCK LOGGED || {db_sym} | 6 Daily Levels Locked")
    except Exception:
        traceback.print_exc()
    finally:
        db.close()

def log_radar_anchors(symbol: str, raw_daily: List[Dict[str, Any]], raw_1h: List[Dict[str, Any]]):
    """
    Logs Macro (Weekly) and Micro (168H) trend anchor prices.

    Each of these is a single ROLLING reference point, not a growing history --
    only the latest snapshot should ever be active. Before 2026-07-06 this never
    deactivated superseded rows: 168H_MICRO_ANCHOR writes hourly and 1W_MACRO_ANCHOR
    writes weekly, both forever, with nothing ever setting active=False on the
    previous one (unlike 4H_PIVOT/1H_PIVOT/DAILY_PIVOT, which _update_zone_touches()
    already invalidates correctly). Confirmed via real production data: 13
    simultaneously-active 168H_MICRO_ANCHOR rows clustered in one ~$160 band,
    causing calculate_gravity_kde() to sum a duplicate-inflated density peak that
    persisted as a "MAXIMUM" wall for nearly a month despite BTC moving tens of
    thousands of dollars in between. See WORK_LOG.md 2026-07-06.
    """
    db = SessionLocal()
    try:
        # gravity_memory stores symbols without a slash (BTCUSDT), matching
        # calculate_gravity_kde()'s and the BOS detectors' query format --
        # see CLAUDE.md's documented gravity_memory symbol exception.
        db_sym = symbol.replace("/", "")
        now_utc = datetime.now(timezone.utc)
        if raw_daily:
            days_since_sunday = (now_utc.weekday() + 1) % 7
            if days_since_sunday == 0: days_since_sunday = 7
            last_sunday_dt = now_utc - timedelta(days=days_since_sunday)
            last_sunday_dt = last_sunday_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            target_ts = int(last_sunday_dt.timestamp())
            macro_candle = next((c for c in raw_daily if int(c["time"]) <= target_ts), None)
            if macro_candle:
                macro_price = float(macro_candle["close"])
                exists = db.query(GravityMemory).filter(GravityMemory.symbol == db_sym, GravityMemory.timestamp == last_sunday_dt, GravityMemory.source == "1W_MACRO_ANCHOR").first()
                if not exists:
                    db.query(GravityMemory).filter(
                        GravityMemory.symbol == db_sym,
                        GravityMemory.source == "1W_MACRO_ANCHOR",
                        GravityMemory.active == True,
                    ).update({"active": False})
                    db.add(GravityMemory(symbol=db_sym, timestamp=last_sunday_dt, source="1W_MACRO_ANCHOR", level_type="MACRO_LINE", price=macro_price, permanence_class=1, heat_multiplier=5.0))
                    print(f"|| GRAVITY MACRO || {db_sym} | Weekly Anchor @ ${macro_price} LOCKED.")
        if raw_1h and len(raw_1h) >= 168:
            micro_candle = raw_1h[-168]
            micro_price = float(micro_candle["close"])
            micro_dt = datetime.fromtimestamp(int(micro_candle["time"]), tz=timezone.utc)
            exists = db.query(GravityMemory).filter(GravityMemory.symbol == db_sym, GravityMemory.timestamp == micro_dt, GravityMemory.source == "168H_MICRO_ANCHOR").first()
            if not exists:
                db.query(GravityMemory).filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "168H_MICRO_ANCHOR",
                    GravityMemory.active == True,
                ).update({"active": False})
                db.add(GravityMemory(symbol=db_sym, timestamp=micro_dt, source="168H_MICRO_ANCHOR", level_type="MICRO_LINE", price=micro_price, permanence_class=2, heat_multiplier=3.0))
                print(f"|| GRAVITY MICRO || {db_sym} | 168H Rolling Anchor @ ${micro_price} LOCKED.")
        db.commit()
    except Exception as e:
        print(f"Radar Anchor Logging Error: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PIVOT SCANNER
# Scans closed candles for swing highs (SUPPLY) and lows (DEMAND).
# v2: computes departure_move_pct at write time using post-pivot candles.
# ---------------------------------------------------------------------------
def _calculate_average_volume(candles: List[Dict[str, Any]], current_idx: int, period=20) -> float:
    if current_idx < period: return 1.0
    vols = [float(c["volume"]) for c in candles[current_idx-period : current_idx]]
    return sum(vols) / len(vols) if vols else 1.0

def _scan_for_pivots(candles: List[Dict[str, Any]], timeframe: str, left=3, right=3):
    pivots_found = []
    closed_candles = candles[:-1]
    if len(closed_candles) < left + right + 1: return pivots_found

    if timeframe == "4h":
        p_class = 1
        source = "4H_PIVOT"
    elif timeframe == "1d":
        p_class = 1
        source = "DAILY_PIVOT"
    else:
        p_class = 2
        source = "1H_PIVOT"

    for i in range(left, len(closed_candles) - right):
        ch = float(closed_candles[i]["high"])
        cl = float(closed_candles[i]["low"])
        ts = int(closed_candles[i]["time"])
        vol = float(closed_candles[i]["volume"])

        is_supply = all(float(closed_candles[i - j]["high"]) <= ch for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j]["high"]) < ch for j in range(1, right + 1))
        is_demand = all(float(closed_candles[i - j]["low"]) >= cl for j in range(1, left + 1)) and \
                    all(float(closed_candles[i + j]["low"]) > cl for j in range(1, right + 1))

        if is_supply or is_demand:
            avg_vol = _calculate_average_volume(closed_candles, i)
            multiplier = 2.0 if (vol > avg_vol * 2.0) else 1.0

            # v2: departure magnitude — how violently price left this zone
            post = closed_candles[i + 1 : i + 4]

            if is_supply:
                if post:
                    post_closes = [float(c["close"]) for c in post]
                    departure_pct = (ch - min(post_closes)) / ch * 100 if ch > 0 else None
                else:
                    departure_pct = None
                pivots_found.append({
                    "ts": ts, "price": ch, "type": "SUPPLY", "source": source,
                    "class": p_class, "heat": multiplier, "departure_pct": departure_pct,
                })

            if is_demand:
                if post:
                    post_closes = [float(c["close"]) for c in post]
                    departure_pct = (max(post_closes) - cl) / cl * 100 if cl > 0 else None
                else:
                    departure_pct = None
                pivots_found.append({
                    "ts": ts, "price": cl, "type": "DEMAND", "source": source,
                    "class": p_class, "heat": multiplier, "departure_pct": departure_pct,
                })

    return pivots_found


# ---------------------------------------------------------------------------
# ZONE TOUCH TRACKER  (runs every gravity loop iteration)
# For each active intraday zone (4H / 1H / DAILY), checks whether the most
# recent closed candle on the matching timeframe:
#   - Approached within TOUCH_BAND → increment touch_count
#   - Closed through the zone → mark active=False (zone invalidated)
# touch_count is re-derived from the available candle window each run (idempotent).
# "Touch" = approach within 0.3% band without a close-through.
# ---------------------------------------------------------------------------
def _update_zone_touches(
    db_sym: str,
    candles_4h: List[Dict],
    candles_1h: List[Dict],
    candles_1d: List[Dict],
    db,
) -> None:
    TOUCH_BAND = 0.003        # within 0.3% of zone price = "touched"
    INVALIDATION_BUF = 0.001  # close through by >0.1% = zone dead

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=60)

    try:
        zones = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source.in_(["4H_PIVOT", "1H_PIVOT", "DAILY_PIVOT"]),
                GravityMemory.active == True,
                GravityMemory.timestamp >= cutoff,
            )
            .all()
        )

        if not zones:
            return

        # Map timeframe → relevant closed candles
        closed_4h = candles_4h[:-1] if candles_4h else []
        closed_1h = candles_1h[:-1] if candles_1h else []
        closed_1d = candles_1d[:-1] if candles_1d else []

        source_to_candles = {
            "4H_PIVOT":    closed_4h,
            "DAILY_PIVOT": closed_1d,
            "1H_PIVOT":    closed_1h,
        }

        changed = False
        for zone in zones:
            relevant = source_to_candles.get(zone.source, [])
            if not relevant:
                continue

            zone_ts = zone.timestamp.replace(tzinfo=timezone.utc) if zone.timestamp.tzinfo is None else zone.timestamp
            z = zone.price

            touches = 0
            invalidated = False

            for c in relevant:
                c_ts = datetime.fromtimestamp(int(c["time"]), tz=timezone.utc)
                if c_ts <= zone_ts:
                    continue  # only evaluate candles that closed AFTER zone was formed

                h = float(c["high"])
                l = float(c["low"])
                close = float(c["close"])

                if zone.level_type == "SUPPLY":
                    if close >= z * (1 + INVALIDATION_BUF):
                        invalidated = True
                        break
                    if h >= z * (1 - TOUCH_BAND):
                        touches += 1
                else:  # DEMAND
                    if close <= z * (1 - INVALIDATION_BUF):
                        invalidated = True
                        break
                    if l <= z * (1 + TOUCH_BAND):
                        touches += 1

            new_active = not invalidated
            new_tc = touches

            if zone.active != new_active or zone.touch_count != new_tc:
                zone.active = new_active
                zone.touch_count = new_tc
                changed = True

        if changed:
            db.commit()

    except Exception as e:
        print(f"[ZONE TOUCH UPDATE] {db_sym} error: {e}")


# ---------------------------------------------------------------------------
# DECISION JOURNAL OUTCOME BACKFILL (unchanged)
# ---------------------------------------------------------------------------
async def fill_decision_outcomes():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    db = SessionLocal()
    try:
        pending = db.query(DecisionJournal).filter(
            DecisionJournal.outcome_price_4h.is_(None),
            DecisionJournal.timestamp <= cutoff,
        ).all()

        if not pending:
            return

        price_cache: Dict[str, float] = {}
        for rec in pending:
            sym = rec.symbol
            if sym not in price_cache:
                try:
                    candles = await battlebox_pipeline.fetch_live_15m(sym, limit=5)
                    price_cache[sym] = float(candles[-1]["close"]) if candles else 0.0
                except Exception as e:
                    print(f"[OUTCOME FILL PRICE ERROR] {sym}: {e}")
                    price_cache[sym] = 0.0

            current_price = price_cache.get(sym, 0.0)
            if current_price <= 0 or not rec.asset_price or rec.asset_price <= 0:
                continue

            pct_move = ((current_price - rec.asset_price) / rec.asset_price) * 100.0
            direction = (rec.confluence_direction or "").upper()
            if direction in ("BULLISH", "LONG"):
                correct = pct_move > 0
            elif direction in ("BEARISH", "SHORT"):
                correct = pct_move < 0
            else:
                correct = None

            rec.outcome_price_4h = round(current_price, 4)
            rec.outcome_pct_move_4h = round(pct_move, 4)
            rec.outcome_direction_correct = correct

        db.commit()
        print(f"|| DECISION JOURNAL OUTCOMES FILLED || {len(pending)} records updated.")
    except Exception as e:
        print(f"Decision Outcome Fill Error: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4H BOS DETECTION — TARGET LOGIC v4 (WINDOWED STOP + FIBONACCI-STAGED TARGETS)
#
# ENTRY: 4H close beyond the most recent 4H SUPPLY (long) or DEMAND (short) zone.
# STOP:  nearest 4H pivot on the opposing side within a 5-calendar-day recency
#         window (empirically validated via mtf_backtest_lab.py --window-test,
#         2026-07-04 — plateaus cleanly at this window on real history, beats
#         the unbounded whole-history baseline). No heat/touch/departure gate
#         (that gate was confirmed to skip the nearest relevant pivot in favor
#         of a distant one that happened to clear the strength bar).
#         Fallback: 1.5× 14-period ATR from entry.
# TARGETS: Fibonacci-staged off the entry-to-stop leg — T1/T2/T3 = entry ± leg
#         × [1.0, 1.618, 2.618], matching the proven 15M Measured Move Rule.
#   ATR rails: leg < 1.5×ATR14 → floor to 1.5×ATR (target_too_small_flag=True)
#              leg > 5×ATR14   → cap to 3×ATR
#              no opposing pivot in window → leg = 2×ATR (ATR_FALLBACK)
# htf_anchor_type describes the STOP's pivot source (STOP_PIVOT | ATR_FALLBACK),
# not a target-side anchor as in v2/v3 -- there is no more opposing-zone target lookup.
# Macro / Class 0 levels: NEVER used for targets. Context and KDE only.
# ---------------------------------------------------------------------------
STOP_WINDOW_4H = timedelta(days=5)


# _detect_4h_bos() / _detect_1h_bos() removed 2026-08-30 -- these created the
# independent 1H/4H trading system (separate CampaignLog trades with their own
# entry/stop/targets, session_id 4h_system/1h_system). Already disabled via an
# early return 2026-08-27 ("doesn't follow the 15M system's discipline");
# Andy's call 2026-08-30: gone entirely, not left as dead weight
# (KABRODA_REBUILD_SPEC.md). market_radar.py's _get_tf_system_verdicts() no
# longer queries those session_ids either -- both sides of this are retired.

# ---------------------------------------------------------------------------
# MAIN GRAVITY INGESTION LOOP
# ---------------------------------------------------------------------------
async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background loop (v4 target logic, STRICT SSOT MODE)...")

    try:
        subprocess.Popen(["python", "kabroda_macro_engine.py"])
    except Exception as e:
        print(f"Failed to launch Macro Engine: {e}")

    loop_count = 0
    outcome_count = 0
    while True:
        # Health monitoring
        try:
            from main import scheduler_health_registry as _ghr
            _ghr["gravity_engine"]["last_run"] = datetime.now(timezone.utc).isoformat()
            _ghr["gravity_engine"]["status"] = "EXECUTING"
        except Exception:
            pass
        db = SessionLocal()
        try:
            for symbol in TARGETS:
                db_sym = symbol.replace("/", "")
                candles_4h = await battlebox_pipeline.fetch_live_4h(symbol, limit=50)
                candles_1h = await battlebox_pipeline.fetch_live_1h(symbol, limit=200)
                candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
                if not candles_4h or not candles_1h or not candles_1d:
                    continue

                log_radar_anchors(db_sym, candles_1d, candles_1h)

                # Pivot scanning: 4H, 1H, and daily
                new_pivots = []
                new_pivots.extend(_scan_for_pivots(candles_4h, "4h"))
                new_pivots.extend(_scan_for_pivots(candles_1h, "1h"))
                new_pivots.extend(_scan_for_pivots(candles_1d, "1d"))

                for p in new_pivots:
                    dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
                    src = p["source"]
                    exists = db.query(GravityMemory).filter(
                        GravityMemory.symbol == db_sym,
                        GravityMemory.timestamp == dt,
                        GravityMemory.source == src,
                    ).first()
                    if not exists:
                        mem = GravityMemory(
                            symbol=db_sym,
                            timestamp=dt,
                            source=src,
                            level_type=p["type"],
                            price=p["price"],
                            permanence_class=p["class"],
                            heat_multiplier=p["heat"],
                            departure_move_pct=p.get("departure_pct"),
                        )
                        db.add(mem)
                        db.commit()
                        print(
                            f"|| GRAVITY BEDROCK || {db_sym} | {src} {p['type']} @ ${p['price']:.2f} "
                            f"| Heat: {p['heat']} | Depart: {p.get('departure_pct', 'N/A')}"
                        )

                # Update zone touch counts and invalidate broken zones
                _update_zone_touches(db_sym, candles_4h, candles_1h, candles_1d, db)

                # BOS detection (1H/4H) removed 2026-08-30 -- see the module-
                # level note above _detect_4h_bos's old location. This loop
                # tick no longer needs an extra confluence scan for it.

        except Exception as e:
            print(f"Gravity Engine Iteration Error: {e}")
        finally:
            db.close()

        loop_count += 1
        if loop_count >= 96:
            try:
                subprocess.Popen(["python", "kabroda_macro_engine.py"])
            except Exception:
                pass
            loop_count = 0

        outcome_count += 1
        if outcome_count >= 16:
            try:
                await fill_decision_outcomes()
            except Exception as e:
                print(f"Decision Outcome Task Error: {e}")
            outcome_count = 0

        await asyncio.sleep(900)
