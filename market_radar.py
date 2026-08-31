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
import session_manager
from database import SessionLocal, SessionLock, DecisionJournal, CampaignLog

TARGETS = ["BTCUSDT"]


def _current_session_date_key() -> str:
    """The active us_ny_futures session's date_key -- anchored to the 13:00
    UTC session lock (session_manager.resolve_current_session()), NOT raw
    UTC calendar midnight. Found 2026-08-30 while auditing radar readiness:
    both this function's former callers (_try_locked_shortcut(),
    _get_tf_system_verdicts()) used to compute "today" as
    datetime.utcnow().strftime("%Y-%m-%d") directly -- which rolls over 13
    hours EARLY relative to the session's real date_key rollover (13:00
    UTC, not 00:00 UTC). For that whole 13-hour window every single day,
    the radar looked up a date_key one day ahead of the still-active
    session's real one: _try_locked_shortcut() always missed the real,
    valid SessionLock (falling back to the slow full-fetch path every
    poll, not a correctness break but a real performance regression), and
    _get_tf_system_verdicts() always missed the real, valid CampaignLog row
    (showing PENDING in the TF-stack detail row for a session that had
    already made its real TAKE/PASS call, confirmed reproducible)."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    session = session_manager.resolve_current_session(now_utc, "AUTO")
    return session["date_key"]


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
    today = _current_session_date_key()
    result = {
        "4H":  {"status": "RETIRED"},
        "1H":  {"status": "RETIRED"},
        "15M": {"status": "PENDING", "state": None, "tier": None, "headline": None,
                "bias": None, "entry": None, "stop": None, "t1": None, "t2": None, "t3": None},
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
                    "status":   c15m.mas_approval_status or "PENDING",
                    "state":    c15m.conviction,    # TAKE_PREMIUM/TAKE_STANDARD/ALMOST/PASS
                    "tier":     c15m.tier,          # PREMIUM/STANDARD/None
                    "headline": c15m.mas_executive_brief,  # the real reason, plain English
                    "bias":     c15m.bias,
                    "entry":    c15m.entry_price,
                    "stop":     c15m.stop_loss,
                    "t1":       c15m.t1,
                    "t2":       c15m.t2,
                    "t3":       c15m.t3,
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


# _compute_daily_regime() removed 2026-08-30 -- a separate, never-validated
# heuristic (EMA-slope + 200SMA-position -> DAILY_BULL/BEAR/RECOVERY/
# DISTRIBUTION), disconnected from and predating market_regime.py's real,
# calibrated classification (Kaufman Efficiency Ratio + ADX + fakeout-rate +
# vol-trend, backtested against 1,913 trades). Andy's call: kabroda.com
# should show what was actually tested, not a guess sitting next to it.
# The gate already computes the real regime (for its own counter-trend veto)
# -- callers now read that instead (dossier["market_regime_table"] /
# GateLog.daily_regime_table).

async def _try_locked_shortcut(symbol: str):
    """
    If a SessionLock already exists for today's us_ny_futures session, read it
    directly from the DB. Avoids the full 1500-candle multi-timeframe MEXC
    pull, but still does one lightweight live 5m fetch to get a fresh price.
    Returns a battlebox-compatible response dict, or None if no lock exists.
    """
    today = _current_session_date_key()
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

    # structure_state_engine.compute_structure_state() call removed 2026-08-30
    # -- decision_engine.evaluate_15m_decision() never reads a structure_state
    # parameter (confirmed: the old 2-consecutive-close acceptance gate it
    # computed was superseded by the calibrated gate's own first-5m-close
    # test), and no template displays it. This lightweight live 5m fetch is
    # kept only for a fresh price.
    try:
        live_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=300)
        if live_5m:
            price = float(live_5m[-1]["close"])
    except Exception as _live_price_err:
        print(f"[RADAR SHORTCUT] Live price refresh failed (using frozen lock-time price): {_live_price_err}")

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
    rules say. Candles are fetched fresh on every call -- the calibrated
    gate needs real 5m/1h/4h/1d reads, not a cached summary
    (KABRODA_REBUILD_SPEC.md §2-3, 2026-08-30 rebuild)."""
    bo = float(levels.get("breakout_trigger", 0) or 0)
    bd = float(levels.get("breakdown_trigger", 0) or 0)

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
            levels=gate_levels,
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
        # The real, validated regime read (market_regime.py/micro_regime.py,
        # 2026-08-30) -- already computed by the gate itself for its
        # counter-trend/dead-tape vetoes, surfaced here instead of a separate,
        # never-validated heuristic (_compute_daily_regime(), removed).
        "market_regime_table":   decision.get("market_regime_table"),
        "market_regime_quality": decision.get("market_regime_quality"),
        "micro_regime":          decision.get("micro_regime"),
    }


# get_mtf_brief() / _build_action_sentence() removed 2026-08-30 -- both built
# entirely on the old confluence vote-tally (confluence_score/dominant_
# direction/conviction) that mtf_confluence_scanner.run_mtf_confluence_scan()
# no longer returns. Andy's call: strip it out entirely, not patch around the
# now-missing fields. The radar's real per-symbol call is decision_engine.py's
# gate (via _build_dossier() below), not a second, separate "morning brief."


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

    # 2026-08-30: mtf_tasks/get_mtf_brief() removed -- the old "morning
    # brief" built entirely on the retired confluence vote-tally. Only the
    # real battlebox/gate pipeline runs now.
    bb_tasks = [_get_bb_data(sym) for sym in TARGETS]
    bb_results = await asyncio.gather(*bb_tasks, return_exceptions=True)

    for sym, res in zip(TARGETS, bb_results):
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

        # TF system verdicts (15M only -- 1H/4H retired) and the real,
        # validated regime read (from the dossier -- decision_engine.py
        # already computed it live via market_regime.py/micro_regime.py;
        # _compute_daily_regime()'s old, never-validated EMA/200SMA heuristic
        # is removed, not just unused).
        sym_norm = sym.replace("USDT", "/USDT") if "/" not in sym else sym
        mtf_snap = context.get("mtf_structural_snapshot", {}) or {}
        tf_verdicts = _get_tf_system_verdicts(sym_norm)
        tf_today    = _which_tf_today(tf_verdicts, current_price=price)
        daily_regime = dossier.get("market_regime_table")
        # weekly_200sma_position is real, separate, live infrastructure
        # (battlebox_pipeline._fetch_weekly_200sma()) -- unrelated to the old
        # daily-regime heuristic just replaced, not touched here.
        weekly_pos = mtf_snap.get("weekly_200sma_position") or ""

        bo_val = float(levels.get("breakout_trigger", 0) or 0)
        bd_val = float(levels.get("breakdown_trigger", 0) or 0)

        radar_item = {
            "symbol": sym, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "indicator_string": _make_indicator_string(levels), "full_intel": json.dumps(res, default=str),
            "levels": levels,
            # Full live per-timeframe confluence (real 21/55 EMA, BBWP/PMARP,
            # divergence) -- genuinely live as of 2026-08-27 (_try_locked_shortcut
            # now recomputes this fresh every call, not frozen at session lock).
            "confluence_scan": context.get("confluence_scan", {}),
            "tf_verdicts": tf_verdicts,
            "tf_today": tf_today,
            "daily_regime": daily_regime,
            "weekly_200sma_position": weekly_pos,
            **dossier
        }

        radar_item["sort_weight"] = dossier["score_pct"]
        radar_grid.append(radar_item)

        # MtfReading write removed 2026-08-30 -- existed only to snapshot the
        # old mtf_brief (confluence_score/direction/energy_status), gone.

        # --- DECISION JOURNAL (Performance Auditor foundation — data collection only) ---
        # confluence_score/confluence_direction/energy_status columns are no
        # longer populated with meaningful data (their source, the old
        # confluence vote-tally, is retired) -- left at their column defaults
        # rather than dropped from the schema; everything else here (the real
        # gate state, levels, briefing, full context) is untouched.
        try:
            # dossier["grade"] is the real calibrated-gate state from
            # decision_engine.py (TAKE_PREMIUM/TAKE_STANDARD/ALMOST/PASS,
            # 2026-08-30 rebuild) -- written as-is, no remapping needed.
            decision_type = dossier.get("grade", "PASS")

            with SessionLocal() as db:
                journal = DecisionJournal(
                    symbol=sym.replace("USDT", "/USDT"),
                    timestamp=datetime.datetime.utcnow(),
                    decision_type=decision_type,
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
