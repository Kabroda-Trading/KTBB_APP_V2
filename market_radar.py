# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v15 (PHASE 4 LOCK-IN, 2026-08-27)
# Reads the real graded decision (decision_engine.py) directly, live, on every
# scan -- replaces the independent _build_dossier()/_score_setup() scorer that
# used to compute its own separate GRADE A/B/STAND DOWN verdict, disconnected
# from the actual decision layer. See AGENT_LOG.md 2026-08-27 for why.
# ==============================================================================
import json
import asyncio
import datetime
from typing import Optional
import battlebox_pipeline
import decision_engine
import market_data
import mtf_confluence_scanner
import structure_state_engine
from database import SessionLocal, SessionLock, MtfReading, DecisionJournal, CampaignLog

TARGETS = ["BTCUSDT"]


# _tf_candidate_verdict() removed 2026-08-30 -- built tf_verdicts entries for
# 4H/1H CampaignLog candidate rows. The independent 1H/4H trading system that
# produced those rows is retired (see _get_tf_system_verdicts() below);
# nothing constructs 4h_system/1h_system CampaignLog rows anymore, so this
# had no remaining caller.


def _get_tf_system_verdicts(symbol_norm: str) -> dict:
    """
    15M only. The independent 1H/4H trading system is retired (its trade-
    creating functions, gravity_engine.py's _detect_4h_bos/_detect_1h_bos,
    were already disabled 2026-08-27; Andy's call 2026-08-30: gone entirely,
    not just de-emphasized -- KABRODA_REBUILD_SPEC.md). 4H/1H always report
    RETIRED now instead of querying the frozen 4h_system/1h_system
    CampaignLog rows from before the disable, which would otherwise keep
    showing an old, closed trade as if it mattered forever.
    """
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    result = {
        "4H":  {"status": "RETIRED", "bias": None, "entry": None, "stop": None, "t1": None, "t2": None, "t3": None, "macro_bias": None, "dominant_direction": None, "outcome": None, "realized_pnl": None},
        "1H":  {"status": "RETIRED", "bias": None, "entry": None, "stop": None, "t1": None, "t2": None, "t3": None, "macro_bias": None, "dominant_direction": None, "outcome": None, "realized_pnl": None},
        "15M": {"status": "PENDING",    "bias": None, "entry": None, "stop": None, "t1": None},
    }
    try:
        with SessionLocal() as db:
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


# _candidate_is_live() removed 2026-08-30 -- price-drift staleness check for
# 4H/1H BOS candidates. Only caller was _which_tf_today()'s now-removed 4H/1H
# branches; the independent 1H/4H trading system is retired.


def _which_tf_today(tf_verdicts: dict, current_price: float = 0.0) -> dict:
    """15M only now -- the independent 1H/4H trading system is retired
    (Andy's call, 2026-08-30, KABRODA_REBUILD_SPEC.md). Drives the TRADE
    THIS star badge on the radar."""
    v15m = tf_verdicts.get("15M", {})
    if v15m.get("status") == "APPROVED":
        return {"primary_tf": "15M", "flag": "15M_APPROVED", "bias": v15m.get("bias")}
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
    directly from the DB. Avoids the full 1500-candle multi-timeframe MEXC
    pull, but still does one lightweight live 5m fetch to recompute structure
    state fresh -- Phase 4 (2026-08-27): the acceptance gate is only earned
    over the course of the day as post-lock candles close beyond the trigger,
    so a structure_state frozen at lock time (from pkt["context"]) would show
    stale/no-permission state for the rest of the day, every day. The radar
    needs the live answer, not the moment-of-lock snapshot -- that's what
    decision_engine.py actually gates on. Returns a battlebox-compatible
    response dict, or None if no lock exists.
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
    context = pkt.get("context", {})

    try:
        lock_time = int(pkt.get("lock_time") or 0)
        live_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=300)
        if live_5m:
            price = float(live_5m[-1]["close"])
            post_lock = [c for c in live_5m if int(c["time"]) >= lock_time]
            context = dict(context)
            context["structure_state"] = structure_state_engine.compute_structure_state(
                levels=levels, candles_5m_post_lock=post_lock
            )
    except Exception as _live_state_err:
        print(f"[RADAR SHORTCUT] Live structure-state refresh failed (using frozen lock-time state): {_live_state_err}")

    # Live confluence_scan refresh -- same reasoning as structure_state above.
    # The packet's own confluence_scan was computed ONCE, at lock, and frozen
    # in SessionLock.packet_data ever since -- trend/BBWP/PMARP/divergence are
    # NOT static facts the way bo/bd are, they're meant to keep updating all
    # day. Real gap, found while answering Andy's direct question about
    # whether Brain gets genuinely live tool data (2026-08-27): it didn't,
    # for this field, until now.
    try:
        context["confluence_scan"] = await mtf_confluence_scanner.run_mtf_confluence_scan(norm)
    except Exception as _live_conf_err:
        print(f"[RADAR SHORTCUT] Live confluence-scan refresh failed (using frozen lock-time data): {_live_conf_err}")

    return {
        "status": "OK",
        "price": price,
        "battlebox": {
            "levels": levels,
            "context": context
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

# _run_measured_move_audit()/_score_setup() (independent, disconnected scorer
# with its own separate GRADE A/B/STAND DOWN verdict -- and a real bug: GRADE B
# was mathematically unreachable, "Clear Airspace" always added its 4 points
# unconditionally) removed 2026-08-27, Phase 4 lock-in. _build_dossier() now
# reads the real, live graded decision (decision_engine.py) directly instead
# of computing a second, parallel one -- see AGENT_LOG.md for the discovery.

# Legacy-shaped compatibility fields, kept only until the §8 radar rebuild
# lands (KABRODA_REBUILD_SPEC.md) -- score_pct/grade/color_code are explicitly
# on the CUT list (§11.1: "0-100 score_pct as the verdict -- non-monotonic
# with outcome"), not a real signal. They're mapped here so the current
# frontend doesn't break while the new four-outcome headline card (TAKE
# PREMIUM/TAKE STANDARD/ALMOST/PASS) is still being built.
_STATE_COLOR = {
    "TAKE_PREMIUM": "GREEN", "TAKE_STANDARD": "GREEN",
    "ALMOST": "YELLOW", "PASS": "GRAY",
}


def _legacy_briefing(state: str, side: Optional[str], headline: str) -> str:
    if state == "TAKE_PREMIUM":
        return f"🟢🟢 TAKE — PREMIUM ({side}) — {headline}"
    if state == "TAKE_STANDARD":
        return f"🟢 TAKE — STANDARD ({side}) — {headline}"
    if state == "ALMOST":
        return f"🟡 ALMOST — {headline}"
    return f"⚪ PASS — {headline}"


async def _build_dossier(symbol: str, price: float, levels: dict, context: dict) -> dict:
    """Calls decision_engine.evaluate_15m_decision() directly with live data
    -- the exact same function run_mas_analysis() calls, so the radar and the
    official daily decision record can never silently disagree about what the
    rules say. structure_state here is LIVE (recomputed fresh by
    _try_locked_shortcut()/get_live_battlebox() on every call, not frozen at
    lock time). Candles are fetched fresh on every call -- the calibrated
    gate needs real 5m/1h/4h/1d reads, not a cached summary
    (KABRODA_REBUILD_SPEC.md §2-3, 2026-08-30 rebuild)."""
    bo = float(levels.get("breakout_trigger", 0) or 0)
    bd = float(levels.get("breakdown_trigger", 0) or 0)

    structure_state = context.get("structure_state", {})
    confluence_scan = context.get("confluence_scan", {})

    if bo == 0 or bd == 0:
        decision = {"verdict_state": "PASS", "side": None, "tier": None,
                    "tactical_brief": "Missing triggers.", "bias": "NEUTRAL",
                    "entry_price": 0.0, "stop_loss": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0}
    else:
        candles_5m, candles_15m, candles_1h, candles_4h, candles_1d = await asyncio.gather(
            market_data.fetch_live_5m(symbol, limit=400),
            market_data.fetch_live_15m(symbol, limit=300),
            market_data.fetch_live_1h(symbol, limit=100),
            market_data.fetch_live_4h(symbol, limit=100),
            market_data.fetch_live_daily(symbol, limit=60),
        )
        gate_levels = dict(levels)
        gate_levels["daily_atr14"] = market_data._calc_daily_atr14(candles_1d)
        gate_levels["price"] = float(candles_5m[-1]["close"]) if candles_5m else price
        decision, _gauges = decision_engine.evaluate_15m_decision(
            levels=gate_levels, structure_state=structure_state,
            confluence_15m=confluence_scan.get("15M"),
            candles_5m=candles_5m, candles_15m=candles_15m,
            candles_1h=candles_1h, candles_4h=candles_4h, candles_1d=candles_1d,
            session_hour_utc=datetime.datetime.now(datetime.timezone.utc).hour,
        )

    state = decision["verdict_state"]
    side = decision.get("side")
    favored = decision["bias"]
    is_valid = state in ("TAKE_PREMIUM", "TAKE_STANDARD")

    plan = {
        "valid": is_valid, "bias": favored, "tier": decision.get("tier"), "state": state,
        "entry": decision["entry_price"], "stop": decision["stop_loss"],
        "targets": [decision["t1"], decision["t2"], decision["t3"]],
    }
    # 9-field shape matches the JS-side buildMissionKey9() contract exactly
    # (bias|status|entry|stop|tp1|tp2|tp3|macro|micro).
    key = (
        f"{favored}|{state}|{plan['entry']:.2f}|{plan['stop']:.2f}|"
        f"{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|"
        f"{context.get('macro_bias', 'NEUTRAL')}|{context.get('micro_bias', 'NEUTRAL')}"
        if is_valid else ""
    )

    return {
        "favored": favored,
        "verdict_state": state,          # TAKE_PREMIUM/TAKE_STANDARD/ALMOST/PASS -- the real answer
        "grade": state,                  # legacy field name, same value -- see module note above
        "score_pct": 100 if state == "TAKE_PREMIUM" else (75 if state == "TAKE_STANDARD" else (40 if state == "ALMOST" else 0)),
        "color_code": _STATE_COLOR.get(state, "GRAY"),
        "briefing": _legacy_briefing(state, side, decision["tactical_brief"]),
        "checks": [], "diagnostic_ledger": {"reason": decision["tactical_brief"], "gate": decision.get("gate")},
        "plan": plan, "key": key,
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

    dossier = await _build_dossier(symbol, price, levels, context)

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

        dossier = await _build_dossier(sym, price, levels, context)

        # TF system verdicts (4H + 1H candidates) and daily regime
        sym_norm = sym.replace("USDT", "/USDT") if "/" not in sym else sym
        mtf_snap = context.get("mtf_structural_snapshot", {}) or {}
        tf_verdicts = _get_tf_system_verdicts(sym_norm)
        tf_today    = _which_tf_today(tf_verdicts, current_price=price)
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
            # Full live per-timeframe confluence (real 21/55 EMA, BBWP/PMARP,
            # divergence) -- genuinely live as of 2026-08-27 (_try_locked_shortcut
            # now recomputes this fresh every call, not frozen at session lock).
            # mtf_brief above is only a summary; this is the real detail.
            "confluence_scan": context.get("confluence_scan", {}),
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
            # dossier["grade"] is now the real conviction tier from
            # decision_engine.py (STRONG_LONG/LEAN_LONG/NEUTRAL/LEAN_SHORT/
            # STRONG_SHORT) -- written as-is, no remapping needed.
            decision_type = dossier.get("grade", "NEUTRAL")

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
