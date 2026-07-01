# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION & BEDROCK LOGGING)
# TARGET LOGIC v3: structural measured move from break level (single target per trade),
#   ATR safety rails, strength-filtered stop zones, touch_count tracking.
#   (v2 = legacy staged T1/T2/T3, frozen 2026-07-01 — see database.py CampaignLog comment)
# ==============================================================================
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
import subprocess

from database import SessionLocal, GravityMemory, DecisionJournal, CampaignLog
import battlebox_pipeline  # <-- SINGLE SOURCE OF TRUTH ENFORCED
import notify

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# ---------------------------------------------------------------------------
# HELPER: ATR
# ---------------------------------------------------------------------------
def _calc_atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        prev_c = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


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
# BEDROCK / RADAR LOGGING (unchanged)
# ---------------------------------------------------------------------------
def log_kabroda_bedrock(symbol: str, levels: dict, lock_ts: int):
    """Logs the 7-Day macro anchors (Daily S/R, 30m boundaries, Triggers)."""
    db = SessionLocal()
    try:
        db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
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
                mem = GravityMemory(symbol=db_sym, timestamp=dt, source="7_DAY_KABRODA", level_type=l_type, price=float(price), permanence_class=2, heat_multiplier=1.0)
                db.add(mem)
        db.commit()
        print(f"|| GRAVITY BEDROCK LOGGED || {db_sym} | 6 Daily Levels Locked")
    except Exception:
        traceback.print_exc()
    finally:
        db.close()

def log_radar_anchors(symbol: str, raw_daily: List[Dict[str, Any]], raw_1h: List[Dict[str, Any]]):
    """Logs Macro (Weekly) and Micro (168H) trend anchor prices."""
    db = SessionLocal()
    try:
        db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
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
                    db.add(GravityMemory(symbol=db_sym, timestamp=last_sunday_dt, source="1W_MACRO_ANCHOR", level_type="MACRO_LINE", price=macro_price, permanence_class=1, heat_multiplier=5.0))
                    print(f"|| GRAVITY MACRO || {db_sym} | Weekly Anchor @ ${macro_price} LOCKED.")
        if raw_1h and len(raw_1h) >= 168:
            micro_candle = raw_1h[-168]
            micro_price = float(micro_candle["close"])
            micro_dt = datetime.fromtimestamp(int(micro_candle["time"]), tz=timezone.utc)
            exists = db.query(GravityMemory).filter(GravityMemory.symbol == db_sym, GravityMemory.timestamp == micro_dt, GravityMemory.source == "168H_MICRO_ANCHOR").first()
            if not exists:
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
# 4H BOS DETECTION — TARGET LOGIC v3 (SINGLE STRUCTURAL TARGET)
#
# ENTRY: 4H close beyond the most recent 4H SUPPLY (long) or DEMAND (short) zone.
# STOP:  nearest strength-qualified 4H zone on the opposing side; 60-day lookback.
#         Fallback: 1.5× 14-period ATR from entry.
# TARGETS: equal-leg measured move from the break level.
#   Base = distance from break_level_price to the nearest opposing 4H zone.
#   TARGET = break_level ± base  (1× measured move — single structural target)
#   ATR rails: base < 1.5×ATR14 → floor to 1.5×ATR (target_too_small_flag=True)
#              base > 5×ATR14   → cap to 3×ATR
#              no opposing zone → base = 2×ATR (ATR_FALLBACK)
# Macro / Class 0 levels: NEVER used for targets. Context and KDE only.
# ---------------------------------------------------------------------------
def _detect_4h_bos(symbol: str, db_sym: str, candles_4h: List[Dict[str, Any]], db) -> None:
    try:
        if not candles_4h or len(candles_4h) < 14:
            return

        current_close = float(candles_4h[-1]["close"])
        now = datetime.now(timezone.utc)
        date_key = now.strftime("%Y-%m-%d")

        # Dedup: one candidate per date_key per symbol
        existing = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.session_id == "4h_system",
                CampaignLog.date_key == date_key,
            )
            .first()
        )
        if existing:
            return

        # --- BOS TRIGGER DETECTION (near-term only: most recent zone within 15 days) ---
        bos_cutoff = now - timedelta(days=15)
        supply_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "4H_PIVOT",
                GravityMemory.level_type == "SUPPLY",
                GravityMemory.active == True,
                GravityMemory.timestamp >= bos_cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )
        demand_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "4H_PIVOT",
                GravityMemory.level_type == "DEMAND",
                GravityMemory.active == True,
                GravityMemory.timestamp >= bos_cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )

        if not supply_zone and not demand_zone:
            return

        atr14 = _calc_atr(candles_4h, 14)

        # --- STRENGTH FILTER PARAMS for stop/target zone selection ---
        TARGET_LOOKBACK = now - timedelta(days=60)
        MIN_HEAT = 2.0
        MIN_DEPARTURE = 1.5   # % departure; NULL allowed (pre-v2 zones)
        MAX_TOUCHES = 2       # exclude touch_count >= 3 (exhausted)

        def _qualified_4h(level_type: str, price_filter, price_order):
            return (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == level_type,
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.heat_multiplier >= MIN_HEAT,
                    GravityMemory.touch_count <= MAX_TOUCHES,
                    price_filter,
                    # Allow NULL departure (pre-v2 zones) or meeting minimum
                    (GravityMemory.departure_move_pct.is_(None)) |
                    (GravityMemory.departure_move_pct >= MIN_DEPARTURE),
                )
                .order_by(price_order)
                .first()
            )

        bias = None
        stop_price = None
        t1_price = None
        break_level_price = None
        htf_anchor_type = None
        htf_anchor_price_val = None
        energy_grade = "WEAK"

        if supply_zone and current_close > supply_zone.price:
            bias = "LONG"
            break_level_price = supply_zone.price
            energy_grade = _compute_energy_grade(candles_4h, "LONG")

            # STOP: nearest qualified DEMAND zone below entry
            stop_row = _qualified_4h(
                "DEMAND",
                GravityMemory.price < current_close,
                GravityMemory.price.desc(),
            )
            stop_price = stop_row.price if stop_row else round(current_close - 1.5 * atr14, 2)

            # MEASURED MOVE: base = distance from break level to nearest opposing zone below it.
            # The supply zone at break_level_price was the top of the prior consolidation range.
            # The nearest demand zone below it marks the bottom of that same range.
            # Projecting that range upward from the break level = equal-leg measured move.
            opp_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.price < break_level_price,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            if opp_row:
                raw_base = break_level_price - opp_row.price
                htf_anchor_type = "STRUCTURAL_MEASURED_MOVE"
                htf_anchor_price_val = opp_row.price
            else:
                raw_base = 2.0 * atr14
                htf_anchor_type = "ATR_FALLBACK"
                htf_anchor_price_val = None

            # ATR safety rails
            target_too_small = (raw_base < 1.5 * atr14) if atr14 > 0 else False
            base = max(raw_base, 1.5 * atr14) if atr14 > 0 else raw_base
            if atr14 > 0 and base > 5.0 * atr14:
                base = 3.0 * atr14

            t1_price = round(break_level_price + base, 2)

        elif demand_zone and current_close < demand_zone.price:
            bias = "SHORT"
            break_level_price = demand_zone.price
            energy_grade = _compute_energy_grade(candles_4h, "SHORT")

            # STOP: nearest qualified SUPPLY zone above entry
            stop_row = _qualified_4h(
                "SUPPLY",
                GravityMemory.price > current_close,
                GravityMemory.price.asc(),
            )
            stop_price = stop_row.price if stop_row else round(current_close + 1.5 * atr14, 2)

            # MEASURED MOVE: base = distance from break level to nearest opposing zone above it.
            # The demand zone at break_level_price was the bottom of the prior consolidation range.
            # The nearest supply zone above it marks the top of that same range.
            # Projecting that range downward from the break level = equal-leg measured move.
            opp_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.price > break_level_price,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            if opp_row:
                raw_base = opp_row.price - break_level_price
                htf_anchor_type = "STRUCTURAL_MEASURED_MOVE"
                htf_anchor_price_val = opp_row.price
            else:
                raw_base = 2.0 * atr14
                htf_anchor_type = "ATR_FALLBACK"
                htf_anchor_price_val = None

            # ATR safety rails
            target_too_small = (raw_base < 1.5 * atr14) if atr14 > 0 else False
            base = max(raw_base, 1.5 * atr14) if atr14 > 0 else raw_base
            if atr14 > 0 and base > 5.0 * atr14:
                base = 3.0 * atr14

            t1_price = round(break_level_price - base, 2)

        if not bias:
            return

        row = CampaignLog(
            symbol=symbol,
            date_key=date_key,
            session_id="4h_system",
            bias=bias,
            grade="4H_CANDIDATE",
            entry_price=round(current_close, 2),
            stop_loss=round(stop_price, 2),
            t1=round(t1_price, 2),
            total_contracts=0.0,
            mas_approval_status="4H_CANDIDATE",
            is_canonical=False,
            session_timeframe="4H",
            entry_filled_at=now,
            session_expires_at=now + timedelta(days=5),
            target_logic_version="v3",
            target_too_small_flag=target_too_small,
            htf_anchor_type=htf_anchor_type,
            htf_anchor_price=htf_anchor_price_val,
            energy_grade=energy_grade,
        )
        db.add(row)
        db.commit()
        flag = " [TARGET_TOO_SMALL]" if target_too_small else ""
        print(
            f"|| 4H BOS v3 || {symbol} | {bias} | Entry: ${current_close:.2f} "
            f"| Stop: ${stop_price:.2f} | Target: ${t1_price:.2f} "
            f"| HTF: {htf_anchor_type} | Energy: {energy_grade}{flag}"
        )
        try:
            notify.send_admin_email(
                subject=f"KABRODA 4H CANDIDATE OPEN — {symbol} {bias}",
                body=(
                    f"Symbol: {symbol}\nTimeframe: 4H\nBias: {bias}\n"
                    f"Entry: ${current_close:.2f}\nStop: ${stop_price:.2f}\n"
                    f"Target: ${t1_price:.2f}\nTarget logic version: v3"
                ),
            )
        except Exception as e:
            print(f"[NOTIFY ERROR] 4H open email failed: {e}")
    except Exception as e:
        print(f"[4H BOS DETECTION] {symbol} error: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# 1H BOS DETECTION — TARGET LOGIC v3 (SINGLE STRUCTURAL TARGET)
#
# ENTRY: 1H close beyond the most recent 1H SUPPLY (long) or DEMAND (short) zone.
# STOP:  nearest qualified 1H zone (opposing side), 20-day lookback.
#         Fallback: 1.0× 14-period 1H ATR.
# TARGETS: equal-leg measured move from the break level.
#   Base = distance from break_level_price to the nearest opposing 1H zone.
#   TARGET = break_level ± base  (1× measured move — single structural target)
#   ATR rails: base < 1.0×ATR14 → floor to 1.0×ATR (target_too_small_flag=True)
#              base > 5×ATR14   → cap to 3×ATR
#              no opposing zone → base = 2×ATR (ATR_FALLBACK)
# GATE:  4H trend alignment logged in energy_grade. Misalignment = WEAK energy
#         but candidate still recorded (record always, flag only).
# ---------------------------------------------------------------------------
def _detect_1h_bos(symbol: str, db_sym: str, candles_1h: List[Dict[str, Any]], candles_4h: List[Dict[str, Any]], db) -> None:
    try:
        if not candles_1h or len(candles_1h) < 14:
            return

        current_close = float(candles_1h[-1]["close"])
        now = datetime.now(timezone.utc)
        date_key = now.strftime("%Y-%m-%d")

        # Dedup
        existing = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.session_id == "1h_system",
                CampaignLog.date_key == date_key,
            )
            .first()
        )
        if existing:
            return

        # --- BOS TRIGGER DETECTION (near-term: within 7 days) ---
        bos_cutoff = now - timedelta(days=7)
        supply_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "1H_PIVOT",
                GravityMemory.level_type == "SUPPLY",
                GravityMemory.active == True,
                GravityMemory.timestamp >= bos_cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )
        demand_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "1H_PIVOT",
                GravityMemory.level_type == "DEMAND",
                GravityMemory.active == True,
                GravityMemory.timestamp >= bos_cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )

        if not supply_zone and not demand_zone:
            return

        atr14 = _calc_atr(candles_1h, 14)

        TARGET_LOOKBACK = now - timedelta(days=20)
        MIN_HEAT = 2.0
        MIN_DEPARTURE = 0.8   # lower threshold for 1H zones (smaller natural moves)
        MAX_TOUCHES = 2

        def _qualified_1h(level_type: str, price_filter, price_order):
            return (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == level_type,
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.heat_multiplier >= MIN_HEAT,
                    GravityMemory.touch_count <= MAX_TOUCHES,
                    price_filter,
                    (GravityMemory.departure_move_pct.is_(None)) |
                    (GravityMemory.departure_move_pct >= MIN_DEPARTURE),
                )
                .order_by(price_order)
                .first()
            )

        bias = None
        stop_price = None
        t1_price = None
        break_level_price = None
        htf_anchor_type = None
        htf_anchor_price_val = None

        # 4H trend alignment check for energy_grade
        energy_grade_1h = _compute_energy_grade(candles_1h, "LONG")  # will set by bias below

        if supply_zone and current_close > supply_zone.price:
            bias = "LONG"
            break_level_price = supply_zone.price
            energy_grade_1h = _compute_energy_grade(candles_1h, "LONG")
            # Degrade to WEAK if 4H trend is counter-directional
            if candles_4h and len(candles_4h) >= 50:
                closes_4h = [float(c["close"]) for c in candles_4h]
                ema30_4h = battlebox_pipeline._calc_ema_series(closes_4h, 30)[-1]
                ema50_4h = battlebox_pipeline._calc_ema_series(closes_4h, 50)[-1]
                if ema30_4h <= ema50_4h:  # 4H is BEARISH, 1H LONG = misaligned
                    energy_grade_1h = "WEAK"

            stop_row = _qualified_1h(
                "DEMAND",
                GravityMemory.price < current_close,
                GravityMemory.price.desc(),
            )
            stop_price = stop_row.price if stop_row else round(current_close - 1.0 * atr14, 2)

            # MEASURED MOVE: base = nearest opposing 1H DEMAND zone below the break level.
            opp_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.price < break_level_price,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            if opp_row:
                raw_base = break_level_price - opp_row.price
                htf_anchor_type = "STRUCTURAL_MEASURED_MOVE"
                htf_anchor_price_val = opp_row.price
            else:
                raw_base = 2.0 * atr14
                htf_anchor_type = "ATR_FALLBACK"
                htf_anchor_price_val = None

            # ATR safety rails (1H: tighter floor of 1.0×ATR)
            target_too_small = (raw_base < 1.0 * atr14) if atr14 > 0 else False
            base = max(raw_base, 1.0 * atr14) if atr14 > 0 else raw_base
            if atr14 > 0 and base > 5.0 * atr14:
                base = 3.0 * atr14

            t1_price = round(break_level_price + base, 2)

        elif demand_zone and current_close < demand_zone.price:
            bias = "SHORT"
            break_level_price = demand_zone.price
            energy_grade_1h = _compute_energy_grade(candles_1h, "SHORT")
            if candles_4h and len(candles_4h) >= 50:
                closes_4h = [float(c["close"]) for c in candles_4h]
                ema30_4h = battlebox_pipeline._calc_ema_series(closes_4h, 30)[-1]
                ema50_4h = battlebox_pipeline._calc_ema_series(closes_4h, 50)[-1]
                if ema30_4h > ema50_4h:  # 4H is BULLISH, 1H SHORT = misaligned
                    energy_grade_1h = "WEAK"

            stop_row = _qualified_1h(
                "SUPPLY",
                GravityMemory.price > current_close,
                GravityMemory.price.asc(),
            )
            stop_price = stop_row.price if stop_row else round(current_close + 1.0 * atr14, 2)

            # MEASURED MOVE: base = nearest opposing 1H SUPPLY zone above the break level.
            opp_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.active == True,
                    GravityMemory.timestamp >= TARGET_LOOKBACK,
                    GravityMemory.price > break_level_price,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            if opp_row:
                raw_base = opp_row.price - break_level_price
                htf_anchor_type = "STRUCTURAL_MEASURED_MOVE"
                htf_anchor_price_val = opp_row.price
            else:
                raw_base = 2.0 * atr14
                htf_anchor_type = "ATR_FALLBACK"
                htf_anchor_price_val = None

            # ATR safety rails (1H: tighter floor of 1.0×ATR)
            target_too_small = (raw_base < 1.0 * atr14) if atr14 > 0 else False
            base = max(raw_base, 1.0 * atr14) if atr14 > 0 else raw_base
            if atr14 > 0 and base > 5.0 * atr14:
                base = 3.0 * atr14

            t1_price = round(break_level_price - base, 2)

        if not bias:
            return

        row = CampaignLog(
            symbol=symbol,
            date_key=date_key,
            session_id="1h_system",
            bias=bias,
            grade="1H_CANDIDATE",
            entry_price=round(current_close, 2),
            stop_loss=round(stop_price, 2),
            t1=round(t1_price, 2),
            total_contracts=0.0,
            mas_approval_status="1H_CANDIDATE",
            is_canonical=False,
            session_timeframe="1H",
            entry_filled_at=now,
            session_expires_at=now + timedelta(days=2),
            target_logic_version="v3",
            target_too_small_flag=target_too_small,
            htf_anchor_type=htf_anchor_type,
            htf_anchor_price=htf_anchor_price_val,
            energy_grade=energy_grade_1h,
        )
        db.add(row)
        db.commit()
        flag = " [TARGET_TOO_SMALL]" if target_too_small else ""
        print(
            f"|| 1H BOS v3 || {symbol} | {bias} | Entry: ${current_close:.2f} "
            f"| Stop: ${stop_price:.2f} | Target: ${t1_price:.2f} "
            f"| HTF: {htf_anchor_type} | Energy: {energy_grade_1h}{flag}"
        )
        try:
            notify.send_admin_email(
                subject=f"KABRODA 1H CANDIDATE OPEN — {symbol} {bias}",
                body=(
                    f"Symbol: {symbol}\nTimeframe: 1H\nBias: {bias}\n"
                    f"Entry: ${current_close:.2f}\nStop: ${stop_price:.2f}\n"
                    f"Target: ${t1_price:.2f}\nTarget logic version: v3"
                ),
            )
        except Exception as e:
            print(f"[NOTIFY ERROR] 1H open email failed: {e}")
    except Exception as e:
        print(f"[1H BOS DETECTION] {symbol} error: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# MAIN GRAVITY INGESTION LOOP
# ---------------------------------------------------------------------------
async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background loop (v3 target logic, STRICT SSOT MODE)...")

    try:
        subprocess.Popen(["python", "kabroda_macro_engine.py"])
    except Exception as e:
        print(f"Failed to launch Macro Engine: {e}")

    loop_count = 0
    outcome_count = 0
    while True:
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

                # BOS detection — BTC only
                if symbol == "BTC/USDT":
                    _detect_4h_bos(symbol, db_sym, candles_4h, db)
                    _detect_1h_bos(symbol, db_sym, candles_1h, candles_4h, db)

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
