# gravity_engine.py
# ==============================================================================
# KABRODA GRAVITY ENGINE (BACKGROUND INGESTION & BEDROCK LOGGING)
# AUDIT FIX: Initiated Daily Macro Structural Scan Trigger.
# ==============================================================================
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
import subprocess

from database import SessionLocal, GravityMemory, DecisionJournal, CampaignLog
import battlebox_pipeline  # <-- SINGLE SOURCE OF TRUTH ENFORCED

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

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
    except Exception as e:
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

def _calculate_average_volume(candles: List[Dict[str, Any]], current_idx: int, period=20) -> float:
    if current_idx < period: return 1.0
    vols = [float(c["volume"]) for c in candles[current_idx-period : current_idx]]
    return sum(vols) / len(vols) if vols else 1.0

def _scan_for_pivots(symbol: str, candles: List[Dict[str, Any]], timeframe: str, left=3, right=3):
    pivots_found = []
    closed_candles = candles[:-1]
    if len(closed_candles) < left + right + 1: return pivots_found
    for i in range(left, len(closed_candles) - right):
        ch = float(closed_candles[i]["high"]) 
        cl = float(closed_candles[i]["low"]) 
        ts = int(closed_candles[i]["time"])
        vol = float(closed_candles[i]["volume"])
        is_supply = all(float(closed_candles[i - j]["high"]) <= ch for j in range(1, left + 1)) and all(float(closed_candles[i + j]["high"]) < ch for j in range(1, right + 1))
        is_demand = all(float(closed_candles[i - j]["low"]) >= cl for j in range(1, left + 1)) and all(float(closed_candles[i + j]["low"]) > cl for j in range(1, right + 1))
        if is_supply or is_demand:
            avg_vol = _calculate_average_volume(closed_candles, i)
            multiplier = 2.0 if (vol > avg_vol * 2.0) else 1.0
            p_class = 1 if timeframe == "4h" else 2
            if is_supply: pivots_found.append({"ts": ts, "price": ch, "type": "SUPPLY", "class": p_class, "heat": multiplier})
            if is_demand: pivots_found.append({"ts": ts, "price": cl, "type": "DEMAND", "class": p_class, "heat": multiplier})
    return pivots_found

async def fill_decision_outcomes():
    """Backfill 4H outcomes for DecisionJournal records older than 4 hours with null outcomes.

    Foundation for the Performance Auditor — pure data collection, no judgement agent.
    """
    cutoff = datetime.utcnow() - timedelta(hours=4)
    db = SessionLocal()
    try:
        pending = db.query(DecisionJournal).filter(
            DecisionJournal.outcome_price_4h.is_(None),
            DecisionJournal.timestamp <= cutoff,
        ).all()

        if not pending:
            return

        # Fetch each symbol's current price once and reuse.
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


def _detect_4h_bos(symbol: str, db_sym: str, candles_4h: List[Dict[str, Any]], db) -> None:
    """
    Detect a 4H Break of Structure against the most recent 4H structural zone in
    gravity_memory, then write a campaign_log CANDIDATE row for audit observation.

    Rules:
    - Long BOS:  current 4H close > most recent 4H SUPPLY zone price.
    - Short BOS: current 4H close < most recent 4H DEMAND zone price.
    - One record per date_key (same dedup pattern as the 15M system).
    - mas_approval_status = '4H_CANDIDATE' → monitored by ledger_closing_engine Phase 4.
    - entry_filled_at set to detection time (BOS close = entry signal, no waiting).
    - session_expires_at set to now + 5 days (hard cap; Phase 4 closes at expiry).
    - Zones must be within the last 10 days (50 × 4H bars = gravity engine fetch window).
    """
    try:
        if not candles_4h or len(candles_4h) < 10:
            return

        current_close = float(candles_4h[-1]["close"])
        now = datetime.now(timezone.utc)
        date_key = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=10)

        # De-dup: one candidate per day per symbol
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

        # Most recent 4H SUPPLY and DEMAND zones within the fetch window
        supply_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "4H_PIVOT",
                GravityMemory.level_type == "SUPPLY",
                GravityMemory.timestamp >= cutoff,
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
                GravityMemory.timestamp >= cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )

        if not supply_zone and not demand_zone:
            return

        bias = None
        stop_price = None
        t1_price = None

        if supply_zone and current_close > supply_zone.price:
            bias = "LONG"
            # Stop: nearest DEMAND zone below current price
            stop_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.price < current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            # T1: nearest SUPPLY zone above current price
            t1_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.price > current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            stop_price = stop_row.price if stop_row else round(current_close * 0.95, 2)
            t1_price   = t1_row.price   if t1_row   else round(current_close * 1.05, 2)

        elif demand_zone and current_close < demand_zone.price:
            bias = "SHORT"
            # Stop: nearest SUPPLY zone above current price
            stop_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.price > current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            # T1: nearest DEMAND zone below current price
            t1_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "4H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.price < current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            stop_price = stop_row.price if stop_row else round(current_close * 1.05, 2)
            t1_price   = t1_row.price   if t1_row   else round(current_close * 0.95, 2)

        if not bias:
            return

        # T2/T3: structural extension approximated from the T1 distance
        t1_dist = abs(t1_price - current_close)
        if bias == "LONG":
            t2 = round(t1_price + t1_dist, 2)
            t3 = round(t1_price + t1_dist * 1.618, 2)
        else:
            t2 = round(t1_price - t1_dist, 2)
            t3 = round(t1_price - t1_dist * 1.618, 2)

        row = CampaignLog(
            symbol=symbol,
            date_key=date_key,
            session_id="4h_system",
            bias=bias,
            grade="4H_CANDIDATE",
            entry_price=round(current_close, 2),
            stop_loss=round(stop_price, 2),
            t1=round(t1_price, 2),
            t2=t2,
            t3=t3,
            total_contracts=0.0,
            mas_approval_status="4H_CANDIDATE",
            is_canonical=False,
            session_timeframe="4H",
            entry_filled_at=now,
            session_expires_at=now + timedelta(days=5),
        )
        db.add(row)
        db.commit()
        print(
            f"|| 4H BOS || {symbol} | {bias} | Close: ${current_close:.2f} "
            f"| Stop: ${stop_price:.2f} | T1: ${t1_price:.2f} | CANDIDATE RECORDED"
        )
    except Exception as e:
        print(f"[4H BOS DETECTION] {symbol} error: {e}")


def _detect_1h_bos(symbol: str, db_sym: str, candles_1h: List[Dict[str, Any]], db) -> None:
    """
    Detect a 1H Break of Structure against the most recent 1H structural zone in
    gravity_memory, then write a campaign_log CANDIDATE row for audit observation.

    Gating: only fires when the 4H fuel gauge direction is aligned (read from the most
    recent 4H_CANDIDATE row's bias, if one exists today). If no 4H_CANDIDATE exists,
    allows the 1H signal through — the gating is informational at the candidate stage.

    One record per date_key. Zones must be within the last 3 days (1H fetch window).
    """
    try:
        if not candles_1h or len(candles_1h) < 10:
            return

        current_close = float(candles_1h[-1]["close"])
        now = datetime.now(timezone.utc)
        date_key = now.strftime("%Y-%m-%d")
        cutoff = now - timedelta(days=3)

        # De-dup: one candidate per day per symbol
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

        # Most recent 1H SUPPLY and DEMAND zones within the fetch window
        supply_zone = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "1H_PIVOT",
                GravityMemory.level_type == "SUPPLY",
                GravityMemory.timestamp >= cutoff,
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
                GravityMemory.timestamp >= cutoff,
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )

        if not supply_zone and not demand_zone:
            return

        bias = None
        stop_price = None
        t1_price = None

        if supply_zone and current_close > supply_zone.price:
            bias = "LONG"
            stop_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.price < current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            t1_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.price > current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            stop_price = stop_row.price if stop_row else round(current_close * 0.99, 2)
            t1_price   = t1_row.price   if t1_row   else round(current_close * 1.02, 2)

        elif demand_zone and current_close < demand_zone.price:
            bias = "SHORT"
            stop_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "SUPPLY",
                    GravityMemory.price > current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.asc())
                .first()
            )
            t1_row = (
                db.query(GravityMemory)
                .filter(
                    GravityMemory.symbol == db_sym,
                    GravityMemory.source == "1H_PIVOT",
                    GravityMemory.level_type == "DEMAND",
                    GravityMemory.price < current_close,
                    GravityMemory.timestamp >= cutoff,
                )
                .order_by(GravityMemory.price.desc())
                .first()
            )
            stop_price = stop_row.price if stop_row else round(current_close * 1.01, 2)
            t1_price   = t1_row.price   if t1_row   else round(current_close * 0.98, 2)

        if not bias:
            return

        t1_dist = abs(t1_price - current_close)
        if bias == "LONG":
            t2 = round(t1_price + t1_dist, 2)
            t3 = round(t1_price + t1_dist * 1.618, 2)
        else:
            t2 = round(t1_price - t1_dist, 2)
            t3 = round(t1_price - t1_dist * 1.618, 2)

        row = CampaignLog(
            symbol=symbol,
            date_key=date_key,
            session_id="1h_system",
            bias=bias,
            grade="1H_CANDIDATE",
            entry_price=round(current_close, 2),
            stop_loss=round(stop_price, 2),
            t1=round(t1_price, 2),
            t2=t2,
            t3=t3,
            total_contracts=0.0,
            mas_approval_status="1H_CANDIDATE",
            is_canonical=False,
            session_timeframe="1H",
            entry_filled_at=now,
            session_expires_at=now + timedelta(days=2),
        )
        db.add(row)
        db.commit()
        print(
            f"|| 1H BOS || {symbol} | {bias} | Close: ${current_close:.2f} "
            f"| Stop: ${stop_price:.2f} | T1: ${t1_price:.2f} | CANDIDATE RECORDED"
        )
    except Exception as e:
        print(f"[1H BOS DETECTION] {symbol} error: {e}")


async def run_gravity_ingestion_loop():
    print(">>> GRAVITY ENGINE: Initializing background loop (STRICT SSOT MODE)...")
    
    # KABRODA ARCHITECTURE UPGRADE: Trigger Macro Engine on boot
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
                if not candles_4h or not candles_1h or not candles_1d: continue
                log_radar_anchors(db_sym, candles_1d, candles_1h)
                new_pivots = []
                new_pivots.extend(_scan_for_pivots(db_sym, candles_4h, "4h"))
                new_pivots.extend(_scan_for_pivots(db_sym, candles_1h, "1h"))
                for p in new_pivots:
                    dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
                    src = "4H_PIVOT" if p["class"] == 1 else "1H_PIVOT"
                    exists = db.query(GravityMemory).filter(GravityMemory.symbol == db_sym, GravityMemory.timestamp == dt, GravityMemory.source == src).first()
                    if not exists:
                        mem = GravityMemory(symbol=db_sym, timestamp=dt, source=src, level_type=p["type"], price=p["price"], permanence_class=p["class"], heat_multiplier=p["heat"])
                        db.add(mem)
                        db.commit()
                        print(f"|| GRAVITY BEDROCK || {db_sym} | {src} {p['type']} @ ${p['price']} LOCKED. Heat: {p['heat']}")
                # BOS detection — BTC only (campaign infrastructure is BTC/USDT only)
                if symbol == "BTC/USDT":
                    _detect_4h_bos(symbol, db_sym, candles_4h, db)
                    _detect_1h_bos(symbol, db_sym, candles_1h, db)
        except Exception as e:
            print(f"Gravity Engine Iteration Error: {e}")
        finally:
            db.close()
            
        loop_count += 1
        # Re-run macro engine once every ~24 hours (assuming 900s sleep = 15 mins -> 96 loops)
        if loop_count >= 96:
            try:
                subprocess.Popen(["python", "kabroda_macro_engine.py"])
            except Exception: pass
            loop_count = 0

        outcome_count += 1
        # Fill DecisionJournal 4H outcomes every ~4 hours (900s sleep = 15 mins -> 16 loops)
        if outcome_count >= 16:
            try:
                await fill_decision_outcomes()
            except Exception as e:
                print(f"Decision Outcome Task Error: {e}")
            outcome_count = 0

        await asyncio.sleep(900)