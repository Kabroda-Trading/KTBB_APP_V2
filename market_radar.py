# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v14.4 (MORNING BRIEF UPGRADE)
# AUDIT: Removed conflicting Harmonic/Kinematic gating. Strictly evaluates
# structural breakouts against the 6 primary triggers (bo, bd, res, sup, r30).
# v14.4 ADD: MTF Confluence brief injected per symbol. No existing logic changed.
# ==============================================================================
import os
import json
import asyncio
import datetime
from datetime import timedelta
import battlebox_pipeline
import gravity_math
import mtf_confluence_scanner
from database import SessionLocal, SessionLock, MtfReading, DecisionJournal, CampaignLog

TARGETS = ["BTCUSDT"]


def _get_tf_system_verdicts(symbol_norm: str) -> dict:
    """
    Query campaign_logs for today's 4H, 1H, and 15M (MAS) statuses.
    All three come from a single DB session — DB-only, no exchange calls.
    """
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    result = {
        "4H":  {"status": "MONITORING", "bias": None, "entry": None, "stop": None, "t1": None},
        "1H":  {"status": "MONITORING", "bias": None, "entry": None, "stop": None, "t1": None},
        "15M": {"status": "PENDING",    "bias": None, "entry": None, "stop": None, "t1": None},
    }
    try:
        with SessionLocal() as db:
            c4h = db.query(CampaignLog).filter(
                CampaignLog.symbol == symbol_norm,
                CampaignLog.session_id == "4h_system",
                CampaignLog.date_key == today,
            ).first()
            if c4h:
                result["4H"] = {
                    "status": "BOS_ACTIVE",
                    "bias": c4h.bias,
                    "entry": c4h.entry_price,
                    "stop": c4h.stop_loss,
                    "t1": c4h.t1,
                }
            c1h = db.query(CampaignLog).filter(
                CampaignLog.symbol == symbol_norm,
                CampaignLog.session_id == "1h_system",
                CampaignLog.date_key == today,
            ).first()
            if c1h:
                result["1H"] = {
                    "status": "BOS_ACTIVE",
                    "bias": c1h.bias,
                    "entry": c1h.entry_price,
                    "stop": c1h.stop_loss,
                    "t1": c1h.t1,
                }
            c15m = (
                db.query(CampaignLog)
                .filter(
                    CampaignLog.symbol == symbol_norm,
                    CampaignLog.date_key == today,
                    CampaignLog.is_canonical == True,
                )
                .order_by(CampaignLog.id.desc())
                .first()
            )
            if c15m:
                result["15M"] = {
                    "status": c15m.mas_approval_status or "PENDING",
                    "bias":   c15m.bias,
                    "entry":  c15m.entry_price,
                    "stop":   c15m.stop_loss,
                    "t1":     c15m.t1,
                }
    except Exception as e:
        print(f"[TF VERDICTS] {symbol_norm}: {e}")
    return result


def _which_tf_today(tf_verdicts: dict) -> dict:
    """
    Return the highest-priority active timeframe for today.
    Priority: 4H BOS > 1H BOS > 15M APPROVED > NONE.
    Used to drive the TRADE THIS ★ badge on the radar TF stack.
    """
    v4h  = tf_verdicts.get("4H",  {})
    v1h  = tf_verdicts.get("1H",  {})
    v15m = tf_verdicts.get("15M", {})
    if v4h.get("status") == "BOS_ACTIVE":
        return {"primary_tf": "4H",   "flag": "4H_ACTIVE",      "bias": v4h.get("bias")}
    if v1h.get("status") == "BOS_ACTIVE":
        return {"primary_tf": "1H",   "flag": "1H_ACTIVE",      "bias": v1h.get("bias")}
    if v15m.get("status") == "APPROVED":
        return {"primary_tf": "15M",  "flag": "15M_APPROVED",   "bias": v15m.get("bias")}
    return {"primary_tf": "NONE", "flag": "ALL_STAND_DOWN", "bias": None}


def _compute_daily_regime(mtf_snap: dict) -> str:
    """Derive plain-English daily regime label from MTF snapshot fields."""
    direction = (mtf_snap.get("daily_21ema_direction") or "FLAT").upper()
    pos200 = (mtf_snap.get("daily_200sma_position") or "").upper()
    if direction == "SLOPING_UP" and pos200 in ("ABOVE", "AT", ""):
        return "DAILY_BULL"
    if direction == "SLOPING_UP" and pos200 == "BELOW":
        return "DAILY_RECOVERY"
    if direction == "SLOPING_DOWN" and pos200 == "BELOW":
        return "DAILY_BEAR"
    if direction == "SLOPING_DOWN" and pos200 in ("ABOVE", "AT"):
        return "DAILY_DISTRIBUTION"
    return "DAILY_NEUTRAL"

async def _try_locked_shortcut(symbol: str):
    """
    If a SessionLock already exists for today's us_ny_futures session, read it
    directly from the DB. No MEXC call — price is read from the locked packet.
    Returns a battlebox-compatible response dict, or None if no lock exists.
    Bypasses the 1500-candle MEXC fetch when the session is already established.
    """
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    norm = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
    try:
        with SessionLocal() as db:
            lock = db.query(SessionLock).filter(
                SessionLock.symbol == norm,
                SessionLock.session_id == "us_ny_futures",
                SessionLock.date_key == today
            ).first()
        if not lock:
            return None
        pkt = json.loads(lock.packet_data)
    except Exception:
        return None

    levels = pkt.get("levels", {})
    price = float(levels.get("anchor_price") or 0.0)

    return {
        "status": "OK",
        "price": price,
        "battlebox": {
            "levels": levels,
            "context": pkt.get("context", {})
        }
    }

async def _get_bb_data(symbol: str):
    """Shortcut-first battlebox fetch — avoids full MEXC pull when lock exists."""
    try:
        shortcut = await _try_locked_shortcut(symbol)
        if shortcut:
            return shortcut
        return await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id="us_ny_futures")
    except IndexError:
        print(f"[RADAR] Empty candle list for {symbol} — MEXC may be rate-limiting")
        return {"status": "ERROR", "message": "empty_candle_list"}

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _run_measured_move_audit(entry: float, vector: str, bo: float, bd: float, peaks: list):
    """
    Calculates targets based strictly on the Session Box (BO - BD)
    and checks for Gravity Wall blockages in the airspace.
    """
    audit = {
        "stop": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0,
        "airspace_clear": True, "blocking_wall": 0.0
    }

    # 1. Define the Session Box Risk
    box_size = abs(bo - bd)
    if box_size == 0: box_size = entry * 0.01

    # 2. Extract Gravity Obstacles
    overhead = sorted([p for p in peaks if p["price"] > entry], key=lambda x: x["price"])
    underneath = sorted([p for p in peaks if p["price"] < entry], key=lambda x: x["price"], reverse=True)

    if vector == "LONG":
        audit["stop"] = bd
        audit["t1"] = entry + box_size
        audit["t2"] = entry + (box_size * 1.618)
        audit["t3"] = entry + (box_size * 2.618)

        heavy_ovr = [p for p in overhead if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_ovr and heavy_ovr[0]["price"] < audit["t1"]:
            audit["airspace_clear"] = False
            audit["blocking_wall"] = heavy_ovr[0]["price"]

    elif vector == "SHORT":
        audit["stop"] = bo
        audit["t1"] = entry - box_size
        audit["t2"] = entry - (box_size * 1.618)
        audit["t3"] = entry - (box_size * 2.618)

        heavy_und = [p for p in underneath if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_und and heavy_und[0]["price"] > audit["t1"]:
            audit["airspace_clear"] = False
            audit["blocking_wall"] = heavy_und[0]["price"]

    return audit, box_size

def _score_setup(vector: str, macro_bias: str, micro_bias: str, entry: float, bo: float, bd: float, peaks: list):
    checks = []
    missing = []

    audit, box_size = _run_measured_move_audit(entry, vector, bo, bd, peaks)

    # GATE 1: Session Box Risk
    box_pct = (box_size / entry) * 100
    if box_pct > 1.5:
        return "STAND DOWN", 0, [], f"🔴 HALT: Session Box is too wide ({box_pct:.2f}%).", audit, box_size

    # GATE 2: Airspace Clearance (Gravity Walls)
    if not audit["airspace_clear"]:
        return "STAND DOWN", 0, [], f"🔴 HALT: Airspace Blocked by Gravity Wall at {audit['blocking_wall']}.", audit, box_size

    # THE WEIGHTED SCORING MATRIX
    score = 0
    max_score = 10

    # Ensure Macro Bias supports the structural breakout
    if vector == "LONG" and macro_bias == "BULLISH":
        score += 6; checks.append("✓ Macro Bias Alignment Confirmed")
    elif vector == "SHORT" and macro_bias == "BEARISH":
        score += 6; checks.append("✓ Macro Bias Alignment Confirmed")
    else:
        missing.append("Macro Bias Alignment")

    score += 4; checks.append("✓ Clear Airspace to Target 1")

    pct = max(0, (score / max_score) * 100)

    grade = "STAND DOWN"
    rejection_reason = ""

    if pct == 100:
        grade = "GRADE A"
    elif pct >= 60:
        grade = "GRADE B"
    else:
        grade = "STAND DOWN"
        rejection_reason = f"🔴 ABORT: Missing alignment: " + ", ".join(missing)

    return grade, pct, checks, rejection_reason, audit, box_size

def _build_dossier(symbol, price, levels, macro_bias, micro_bias, kde_peaks):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))

    favored = "NEUTRAL"
    if micro_bias == "BULLISH": favored = "LONG"
    elif micro_bias == "BEARISH": favored = "SHORT"

    entry = bo if favored == "LONG" else bd

    if favored == "NEUTRAL" or bo == 0 or bd == 0:
        return {
            "favored": "NEUTRAL", "grade": "STAND DOWN", "score_pct": 0, "color_code": "GRAY",
            "briefing": "Market is in absolute neutral consolidation or missing triggers.",
            "checks": [], "diagnostic_ledger": {}, "plan": {"valid": False}
        }

    grade, score_pct, checks, rejection_reason, audit, box_size = _score_setup(favored, macro_bias, micro_bias, entry, bo, bd, kde_peaks)

    color = "GRAY"
    briefing = ""

    if grade == "GRADE A":
        color = "GREEN"
        briefing = "🟢 ELITE ALIGNMENT. Structural Breakout Verified against Macro Trend."
    elif grade == "GRADE B":
        color = "YELLOW"
        briefing = "🟡 STANDARD OPERATION. Executable, but expect friction. Scale out aggressively."
    elif grade == "STAND DOWN":
        color = "RED"
        briefing = rejection_reason

    plan = {
        "valid": grade in ["GRADE A", "GRADE B"],
        "bias": favored,
        "entry": entry,
        "stop": audit["stop"],
        "targets": [audit["t1"], audit["t2"], audit["t3"]]
    }

    diagnostic_ledger = {
        "vector_direction": favored,
        "session_box_size": box_size,
        "airspace_clear": audit["airspace_clear"]
    }
    if not plan["valid"]:
        diagnostic_ledger["rejection_reason"] = rejection_reason

    key = f"{plan['bias']}|{grade}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|{macro_bias}|{micro_bias}" if plan["valid"] else ""

    return {
        "favored": favored, "grade": grade, "score_pct": score_pct, "color_code": color,
        "briefing": briefing, "checks": checks, "diagnostic_ledger": diagnostic_ledger,
        "plan": plan, "key": key
    }


async def get_mtf_brief(symbol: str) -> dict:
    """
    Returns Morning Brief data for a symbol using the MTF confluence scanner.
    Energy status is derived from 15M StochRSI zone and curl direction.
    btc_master_switch is only populated when symbol is BTC.
    """
    try:
        scan = await mtf_confluence_scanner.run_mtf_confluence_scan(symbol)
    except Exception as e:
        print(f"[MTF BRIEF ERROR] {symbol}: {e}")
        return {"error": str(e)}

    direction = scan.get("dominant_direction", "NEUTRAL")
    score = scan.get("confluence_score", 0)
    conviction = scan.get("conviction", "LOW")
    timeframes = scan.get("timeframes", {})
    tf_15m = timeframes.get("15M", {})
    stoch = tf_15m.get("stoch_rsi", {})
    zone_15m = stoch.get("zone", "NEUTRAL")
    curl_15m = stoch.get("curl", "FLAT")

    # Energy status: derived from 15M StochRSI relative to directional bias
    if direction == "BULLISH":
        if zone_15m == "OVERBOUGHT":
            energy = "EXHAUSTED"
        elif zone_15m == "VALUE_HIGH":
            energy = "BURNING"
        else:
            energy = "BUILDING"
    elif direction == "BEARISH":
        if zone_15m == "OVERSOLD":
            energy = "EXHAUSTED"
        elif zone_15m == "VALUE_LOW":
            energy = "BURNING"
        else:
            energy = "BUILDING"
    else:
        energy = "BUILDING"

    # Plain-English action sentence
    base = symbol.replace("USDT", "").replace("/", "")
    if direction == "BULLISH":
        action = f"{base} bullish on {score}/5 TFs ({conviction} conviction). Energy: {energy}. Watch breakout trigger."
    elif direction == "BEARISH":
        action = f"{base} bearish on {score}/5 TFs ({conviction} conviction). Energy: {energy}. Watch breakdown trigger."
    else:
        action = f"{base} split — no directional confluence ({score}/5). Await trigger break for clarity."

    is_btc = "BTC" in symbol.upper()
    btc_master_switch = (direction == "BULLISH" and score >= 3) if is_btc else None

    return {
        "confluence_score": score,
        "confluence_direction": direction,
        "energy_status": energy,
        "action_sentence": action,
        "btc_master_switch": btc_master_switch,
        "conviction": conviction,
        "nearest_resistance": scan.get("nearest_resistance"),
        "nearest_support": scan.get("nearest_support"),
        "summary": scan.get("summary", ""),
    }

def _build_action_sentence(direction: str, energy: str, bo: float, bd: float) -> str:
    bo_str = f"${bo:,.2f}" if bo > 0 else "trigger"
    bd_str = f"${bd:,.2f}" if bd > 0 else "trigger"

    if direction == "BULLISH":
        if energy == "EXHAUSTED":
            return f"Momentum exhausted. Longs overextended — do not chase. Pullback toward {bd_str} possible."
        elif energy == "BURNING":
            return f"Trend running hot above {bo_str}. Long bias active. Scale out aggressively near resistance."
        else:
            return f"Momentum building. Long setup active above {bo_str}. Higher timeframes aligned."
    elif direction == "BEARISH":
        if energy == "EXHAUSTED":
            return f"Energy burned out. Watch for breakdown below {bd_str}. Do not chase longs."
        elif energy == "BURNING":
            return f"Bear trend running hot below {bd_str}. Short bias active. Cover aggressively near support."
        else:
            return f"Bearish pressure building. Short setup active below {bd_str}. Higher timeframes aligned."
    else:
        return "No clear direction. Stay flat until confluence improves."


async def analyze_target(symbol):
    data = await _get_bb_data(symbol)
    if data.get("status") == "ERROR": return {"ok": False}
    if data.get("status") == "CALIBRATING": return {"ok": True, "result": {"status": "CALIBRATING"}}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})

    macro_bias = context.get("macro_bias", "NEUTRAL")
    micro_bias = context.get("micro_bias", "NEUTRAL")
    kde_peaks = context.get("kde_peaks", [])

    dossier = _build_dossier(symbol, price, levels, macro_bias, micro_bias, kde_peaks)

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "levels": levels, "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(data, default=str), **dossier
        }
    }

async def scan_sector():
    radar_grid = []

    # Run battlebox scans and MTF briefs in parallel for all targets.
    # _get_bb_data uses the session-lock shortcut when available, skipping the
    # 1500-candle MEXC fetch for sessions that are already established.
    bb_tasks = [_get_bb_data(sym) for sym in TARGETS]
    mtf_tasks = [get_mtf_brief(sym) for sym in TARGETS]
    all_results = await asyncio.gather(*bb_tasks, *mtf_tasks, return_exceptions=True)
    bb_results = all_results[:len(TARGETS)]
    mtf_results = all_results[len(TARGETS):]

    for sym, res, mtf in zip(TARGETS, bb_results, mtf_results):
        if isinstance(res, Exception) or res.get("status") == "ERROR":
            print(f"[RADAR SCAN] {sym} failed: {res}")
            continue
        if res.get("status") == "CALIBRATING":
            radar_grid.append({"symbol": sym, "status": "CALIBRATING", "sort_weight": 0})
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        context = res.get("battlebox", {}).get("context", {})

        macro_bias = context.get("macro_bias", "NEUTRAL")
        micro_bias = context.get("micro_bias", "NEUTRAL")
        kde_peaks = context.get("kde_peaks", [])

        dossier = _build_dossier(sym, price, levels, macro_bias, micro_bias, kde_peaks)

        # TF system verdicts (4H + 1H candidates) and daily regime
        sym_norm = sym.replace("USDT", "/USDT") if "/" not in sym else sym
        mtf_snap = context.get("mtf_structural_snapshot", {}) or {}
        tf_verdicts = _get_tf_system_verdicts(sym_norm)
        tf_today    = _which_tf_today(tf_verdicts)
        daily_regime = _compute_daily_regime(mtf_snap)
        weekly_pos = mtf_snap.get("weekly_200sma_position") or ""

        mtf_brief = mtf if isinstance(mtf, dict) and "error" not in mtf else {}

        bo_val = float(levels.get("breakout_trigger", 0) or 0)
        bd_val = float(levels.get("breakdown_trigger", 0) or 0)
        if mtf_brief:
            direction = mtf_brief.get("confluence_direction", "NEUTRAL")
            energy    = mtf_brief.get("energy_status", "BUILDING")
            mtf_brief["action_sentence"] = _build_action_sentence(direction, energy, bo_val, bd_val)
            dist = abs(bo_val - bd_val)
            if direction == "BULLISH" and bo_val > 0 and dist > 0:
                mtf_brief["t1"] = round(bo_val + dist, 2)
                mtf_brief["t2"] = round(bo_val + dist * 1.618, 2)
                mtf_brief["t3"] = round(bo_val + dist * 2.618, 2)
            elif direction == "BEARISH" and bd_val > 0 and dist > 0:
                mtf_brief["t1"] = round(bd_val - dist, 2)
                mtf_brief["t2"] = round(bd_val - dist * 1.618, 2)
                mtf_brief["t3"] = round(bd_val - dist * 2.618, 2)
            else:
                mtf_brief["t1"] = mtf_brief["t2"] = mtf_brief["t3"] = 0.0

        radar_item = {
            "symbol": sym, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "indicator_string": _make_indicator_string(levels), "full_intel": json.dumps(res, default=str),
            "levels": levels,
            "mtf_brief": mtf_brief,
            "tf_verdicts": tf_verdicts,
            "tf_today": tf_today,
            "daily_regime": daily_regime,
            "weekly_200sma_position": weekly_pos,
            **dossier
        }

        radar_item["sort_weight"] = dossier["score_pct"]
        radar_grid.append(radar_item)

        try:
            with SessionLocal() as db:
                reading = MtfReading(
                    symbol=sym.replace("USDT", "/USDT"),
                    timestamp=datetime.datetime.utcnow(),
                    confluence_score=mtf_brief.get("confluence_score", 0) if mtf_brief else 0,
                    confluence_direction=mtf_brief.get("confluence_direction", "NEUTRAL") if mtf_brief else "NEUTRAL",
                    energy_status=mtf_brief.get("energy_status", "BUILDING") if mtf_brief else "BUILDING",
                    timeframe_data=json.dumps(mtf_brief, default=str),
                    bo_price=bo_val,
                    bd_price=bd_val,
                    asset_price=price,
                    session_date=datetime.datetime.utcnow().strftime("%Y-%m-%d")
                )
                db.add(reading)
                db.commit()
        except Exception as e:
            print(f"[MTF DB SAVE ERROR] {sym}: {e}")

        # --- DECISION JOURNAL (Performance Auditor foundation — data collection only) ---
        try:
            grade = dossier.get("grade", "STAND DOWN")
            decision_type = {
                "GRADE A": "GRADE_A",
                "GRADE B": "GRADE_B",
                "STAND DOWN": "STAND_DOWN",
            }.get(grade, "STAND_DOWN")

            with SessionLocal() as db:
                journal = DecisionJournal(
                    symbol=sym.replace("USDT", "/USDT"),
                    timestamp=datetime.datetime.utcnow(),
                    decision_type=decision_type,
                    confluence_score=mtf_brief.get("confluence_score", 0) if mtf_brief else 0,
                    confluence_direction=mtf_brief.get("confluence_direction", "NEUTRAL") if mtf_brief else "NEUTRAL",
                    energy_status=mtf_brief.get("energy_status", "BUILDING") if mtf_brief else "BUILDING",
                    bo_price=bo_val,
                    bd_price=bd_val,
                    asset_price=price,
                    session_date=datetime.datetime.utcnow().strftime("%Y-%m-%d"),
                    source="market_radar",
                    decision_reason=dossier.get("briefing", ""),
                    full_context_json=json.dumps(radar_item, default=str),
                )
                db.add(journal)
                db.commit()
        except Exception as e:
            print(f"[DECISION JOURNAL SAVE ERROR] {sym}: {e}")

    radar_grid.sort(key=lambda x: x['sort_weight'], reverse=True)
    return radar_grid
