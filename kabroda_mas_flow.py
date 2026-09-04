# kabroda_mas_flow.py
# ==============================================================================
# KABRODA SENIOR ANALYST — Phase 3A
# CrewAI and langchain-anthropic removed. All agent calls go through
# agent_core._call_agent() for unified budget gate and cost tracking.
#
# PUBLIC API (signatures frozen — do not change):
#   run_mas_analysis(symbol, session_id, date_key, battlebox_payload)
#
# 2026-08-30: no LLM tied to Kabroda's cost path, period (Andy's call). Both
# other public functions this file used to expose are gone, not stubbed:
#   - audit_foreign_intel_pipeline() (the Intel Auditor) -- its gravity-as-
#     decision-gate and third measured-move formula had gone stale under
#     this session's calibrated-gate rebuild anyway.
#   - interrogate_cro() (the Operator Commlink chat) -- already a stub since
#     2026-08-17; interactive Q&A is Kabroda AI Brain's job now.
# run_mas_analysis() itself has been LLM-free since the calibrated gate
# replaced the old decision layer earlier this session -- it calls
# decision_engine.py, deterministic, zero LLM, zero cost.
# ==============================================================================

import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import pytz

from pydantic import BaseModel, Field

import asyncio

import agent_core
import decision_engine
import market_data
import session_manager
import trade_plan
from database import (
    SessionLocal,
    CampaignLog,
    DecisionJournal,
    GateLog,
    TradePlan,
)


# ==============================================================================
# SECTION 1 — PYDANTIC SCHEMAS (UNCHANGED FROM ORIGINAL)
# ==============================================================================

class ExecutiveBrief(BaseModel):
    """Strict output schema for the decision layer. Originally the Senior
    Analyst LLM's output schema, kept unchanged in shape for existing
    consumers (CampaignLog injection, dashboards) across two rewrites: the
    graded coded decision layer (2026-08-27) and the calibrated-gate rebuild
    (2026-08-30, KABRODA_REBUILD_SPEC.md) that replaced it."""
    approval_status: str = Field(description="'APPROVED' or 'STAND_DOWN' (REJECTED/WAITING_FOR_15M are legacy LLM-era values, no longer produced)")
    conviction: str = Field(default="PASS", description="TAKE_PREMIUM/TAKE_STANDARD/ALMOST/PASS — the calibrated gate's four-outcome verdict (2026-08-30 rebuild). approval_status is derived from this (TAKE_* -> APPROVED, ALMOST/PASS -> STAND_DOWN).")
    tactical_brief: str = Field(description="Short, deterministic reason string (the matched confirmation legs, or the stand-down reason). No LLM prose generated here anymore.")
    bias: str = Field(description="'LONG', 'SHORT', or 'NEUTRAL'")
    entry_price: float = Field(description="The exact trigger entry price.")
    stop_loss: float = Field(description="The exact stop loss (the opposing trigger).")
    t1: float = Field(description="Target 1 — pre-computed, copy exactly.")
    t2: float = Field(description="Target 2 — pre-computed, copy exactly.")
    t3: float = Field(description="Target 3 — pre-computed, copy exactly.")
    formatted_newsletter_md: str = Field(description="Complete brief in Markdown: all ## sections from THE BIGGER PICTURE through THE OTHER SIDE.")
    side: Optional[str] = Field(default=None, description="LONG/SHORT/None — the calibrated gate's candidate side (2026-08-30 rebuild).")
    tier: Optional[str] = Field(default=None, description="PREMIUM/STANDARD/None — the calibrated gate's tier (2026-08-30 rebuild).")


# IntelAuditReport removed 2026-08-30 -- schema for the removed Intel Auditor.


# ==============================================================================
# SECTION 2 — SYSTEM PROMPTS -- all three removed 2026-08-30, zero LLM calls
# left anywhere in this file's decision path:
#   - SENIOR_ANALYST_SYSTEM_PROMPT (the old LLM Senior Analyst's ~400-line
#     prompt) -- zero callers anywhere (grepped) since run_mas_analysis()
#     was rewritten around the coded gate earlier this session; missed in
#     that pass, caught later while auditing readiness.
#   - COMMLINK_SYSTEM_PROMPT -- prompt for the removed interrogate_cro()
#     Operator Commlink.
#   - INTEL_AUDITOR_SYSTEM_PROMPT -- prompt for the removed Intel Auditor;
#     its gravity-as-decision-gate and third measured-move formula had both
#     gone stale under this session's calibrated-gate rebuild.
# ==============================================================================


# ==============================================================================
# SECTIONS 3-9a (RAG memory reader, cross-day context readers for narrative/
# jewel history, JSON-retry parser, Senior Analyst LLM prompt builder, and
# its two log writers for MacroNarrativeLog/InterpreterLog) removed
# 2026-08-30. All of it fed or was fed by the old LLM Senior Analyst /
# interpreter pipeline; grepped and confirmed zero live references anywhere
# in the file post-rebuild. run_mas_analysis() below reads/writes only what
# the calibrated gate (decision_engine.py) actually needs -- see its own
# docstring for the current, real pipeline.
# ==============================================================================

# ==============================================================================
# SECTION 9 — MAIN PIPELINE
# ==============================================================================

def run_mas_analysis(
    symbol: str,
    session_id: str,
    date_key: str,
    battlebox_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Primary MAS pipeline. Fired at session lock (9:00 AM ET) by battlebox_pipeline.py.
    Produces an ExecutiveBrief and writes it to CampaignLog, DecisionJournal,
    and MacroNarrativeLog.

    REBUILT 2026-08-30 (KABRODA_REBUILD_SPEC.md, Kabroda AI Brain repo).
    Andy's explicit, direct authorization for a full replacement: the old
    graded-conviction model (2026-08-27) and the ATR+gravity-wall stop/target
    math (trade_structure_analyst.py) are both gone, not patched around.
    decision_engine.py now implements the calibrated 4-condition gate
    (reachability + fuel + HTF carry + live hour), validated on a 1,913-trade
    backtest AND on kabroda.com's own 123 real locks. No LLM call anywhere in
    this path, no publishing/newsletter generation, no gravity dependency.
    """
    print(f">>> GATE: Evaluating {symbol} | {session_id}")

    levels = dict(battlebox_payload.get("levels", {}))
    context = battlebox_payload.get("context", {})
    confluence_scan = context.get("confluence_scan", {})

    # The gate needs candles this packet doesn't carry (5m/15m/1h/4h/1d for
    # fuel/HTF/regime/daily-ATR reads) -- fetched fresh here rather than
    # threading them through battlebox_pipeline's whole context-build chain,
    # since this function fires once per session (or on restart recovery),
    # not on every hot-path call. Runs inside its own thread (asyncio.to_thread
    # per the caller), so a fresh event loop via asyncio.run() is safe here --
    # market_data.py gives that fresh loop its own exchange client (2026-08-30
    # fix, see market_data.py's own comment) rather than reusing the main
    # loop's, which used to hang this function forever, silently, on every
    # real session lock. Close that loop-scoped client before the loop exits
    # (asyncio.run() tears the loop down right after this coroutine returns)
    # so it doesn't leak an unclosed connection every time this fires.
    async def _fetch_all():
        try:
            return await asyncio.gather(
                market_data.fetch_live_5m(symbol, limit=400),
                market_data.fetch_live_15m(symbol, limit=300),
                market_data.fetch_live_1h(symbol, limit=100),
                market_data.fetch_live_4h(symbol, limit=100),
                market_data.fetch_live_daily(symbol, limit=60),
            )
        finally:
            await market_data.close_exchange_for_current_loop()
    try:
        candles_5m, candles_15m, candles_1h, candles_4h, candles_1d = asyncio.run(_fetch_all())
    except Exception as e:
        print(f"GATE CANDLE FETCH ERROR: {e}")
        candles_5m = candles_15m = candles_1h = candles_4h = candles_1d = []

    # 2026-09-04 P0 fix (Kabroda AI Brain repo AGENT_LOG.md): strip a
    # still-forming trailing 5m candle before it can be read as a
    # confirmed close -- see market_data.confirmed_5m_closes()'s own
    # docstring for the incident. This is the SSOT lock-time gate call;
    # market_radar.py's live dossier and trade_plan_engine.py's polling
    # both get the same fix, so all three evaluators of decision_engine's
    # gate agree on what "confirmed" means.
    candles_5m = market_data.confirmed_5m_closes(candles_5m)

    daily_atr14 = market_data._calc_daily_atr14(candles_1d)
    levels["daily_atr14"] = daily_atr14
    levels["price"] = float(candles_5m[-1]["close"]) if candles_5m else 0.0
    now_utc = datetime.now(timezone.utc)
    # bo/bd: fixed 2026-08-30 -- these were previously left undefined in this
    # function's scope (a stale reference to a same-named local in the now-
    # dead _build_senior_analyst_prompt()), silently caught by the try/except
    # around the step-7 audit write below and swallowing bo_trigger/bd_trigger
    # from every audit row. Real bug, not a style fix.
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)

    decision_dict, decision_gauges = decision_engine.evaluate_15m_decision(
        levels=levels,
        confluence_15m=confluence_scan.get("15M"),
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        candles_1d=candles_1d,
        session_hour_utc=now_utc.hour,
    )
    brief = ExecutiveBrief(**{k: v for k, v in decision_dict.items() if k in ExecutiveBrief.model_fields})

    _inject_brief_to_database(symbol, session_id, date_key, brief, decision_dict.get("gate"))
    _inject_decision_journal(symbol, session_id, date_key, brief, battlebox_payload)
    _inject_gate_log(symbol, date_key, now_utc, levels, decision_dict)

    # 6b. Trade Plan (KABRODA_COM_TRADE_PLAN_SPEC.md, additive -- SS3/SS4).
    # Built from the SAME decision_dict just written to CampaignLog above,
    # never a second, independently-computed decision -- the "two call
    # sites must never disagree" invariant extends to this new layer too.
    # Non-blocking: TradePlan is additive; a failure here must never affect
    # the SSOT CampaignLog/GateLog writes above.
    try:
        _anchor_pkt = session_manager.resolve_anchor_time(session_id)
        anchor_time = datetime.fromtimestamp(_anchor_pkt["anchor_ts"], tz=timezone.utc)
        plan_fields = trade_plan.build_trade_plan(
            symbol=symbol,
            date_key=date_key,
            session_id=session_id,
            decision_dict=decision_dict,
            anchor_time=anchor_time,
            candles_24h=candles_5m,
            r30_high=levels.get("range30m_high", 0.0),
            r30_low=levels.get("range30m_low", 0.0),
            f24_vah=levels.get("f24_vah", 0.0),
            f24_val=levels.get("f24_val", 0.0),
            daily_atr14=daily_atr14,
            # 2026-08-31 fix (WAITING-visibility gap, Andy via the live
            # site) -- feeds trade_plan.anticipate_setup() so a plan can
            # generate at lock EVEN BEFORE any trigger has crossed, instead
            # of always falling to NO_PLAN until decision_dict already has
            # a side. See build_trade_plan()'s own docstring.
            breakout_trigger=bo,
            breakdown_trigger=bd,
            candles_15m=candles_15m,
            candles_1d=candles_1d,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            session_hour_utc=now_utc.hour,
        )
        _inject_trade_plan_to_database(symbol, session_id, date_key, plan_fields)
    except Exception as _tp_err:
        print(f"[TRADE PLAN] Non-critical failure -- MAS unaffected: {_tp_err}")

    # 7. Forward-audit record — frozen at decision time (non-blocking).
    try:
        from harness.audit_writer import write_decision_record as _write_audit
        import json as _audit_json
        _fuel = context.get("fuel_gauge", {})
        _mtf = context.get("mtf_structural_snapshot", {}) or {}
        # Extract Component 0 extension fields
        _macro_struct = context.get("macro_structure", [])
        _macro_json = None
        try:
            _macro_json = _audit_json.dumps(
                [m.get("type") for m in _macro_struct if m.get("type")]
            )
        except Exception:
            pass
        _tf1h = _fuel.get("1H", {})
        _tf4h = _fuel.get("4H", {})
        _j1h = _tf1h.get("jewel", {}) or {}
        _j4h = _tf4h.get("jewel", {}) or {}
        def _adx_label(j: dict):
            adx = j.get("adx")
            if adx is None:
                return None
            if j.get("adx_trending"):
                return "STRONG"
            if adx > 20:
                return "MODERATE"
            return "WEAK"
        _write_audit(
            symbol=symbol,
            date_key=date_key,
            session_id=session_id,
            approval_status=brief.approval_status,
            bias=brief.bias,
            entry_price=brief.entry_price,
            stop_loss=brief.stop_loss,
            t1=brief.t1,
            t2=brief.t2,
            t3=brief.t3,
            bo_trigger=bo if bo else None,
            bd_trigger=bd if bd else None,
            energy_status=context.get("1h_fuel_status"),
            kinematic_grade=_fuel.get("15M_JEWEL", {}).get("kinematic_grade"),
            micro_state=context.get("micro_state"),
            kde_peaks=context.get("kde_peaks"),
            rag_memory_snapshot=None,  # no LLM/RAG memory consumed by the coded decision layer
            agent_chain={"decision_engine": json.dumps(decision_dict, default=str)},
            model_version=agent_core._MODEL,
            daily_21ema_direction=_mtf.get("daily_21ema_direction"),
            daily_21ema_position=_mtf.get("daily_21ema_position"),
            daily_21ema_distance_pct=_mtf.get("daily_21ema_distance_pct"),
            tf4h_200sma_position=_mtf.get("tf4h_200sma_position"),
            tf4h_200sma_distance_pct=_mtf.get("tf4h_200sma_distance_pct"),
            tf1h_200sma_position=_mtf.get("tf1h_200sma_position"),
            tf1h_200sma_distance_pct=_mtf.get("tf1h_200sma_distance_pct"),
            weekly_200sma_position=_mtf.get("weekly_200sma_position"),
            weekly_200sma_distance_pct=_mtf.get("weekly_200sma_distance_pct"),
            weekly_200sma_test_count=_mtf.get("weekly_200sma_test_count"),
            macro_structure_json=_macro_json,
            tf1h_trend=_tf1h.get("trend"),
            tf1h_rsi=_tf1h.get("rsi"),
            tf1h_adx_strength=_adx_label(_j1h),
            tf4h_trend=_tf4h.get("trend"),
            tf4h_rsi=_tf4h.get("rsi"),
            tf4h_adx_strength=_adx_label(_j4h),
            tf4h_macd_hist=_tf4h.get("macd_hist"),
            daily_200sma_position=_mtf.get("daily_200sma_position"),
            daily_200sma_distance_pct=_mtf.get("daily_200sma_distance_pct"),
            # Crown Surgery Cut 4 — BBWP/PMARP from 15M JEWEL at decision time
            bbwp_15m=_fuel.get("15M_JEWEL", {}).get("bbwp"),
            bbwp_state=_fuel.get("15M_JEWEL", {}).get("bbwp_state"),
            pmarp_15m=_fuel.get("15M_JEWEL", {}).get("pmarp"),
            pmarp_state=_fuel.get("15M_JEWEL", {}).get("pmarp_state"),
            rsi_divergence_type="NONE",
        )
        # Read-back heartbeat — proves the row actually landed, visible in Render logs
        try:
            from database import SessionLocal as _HB_SL, SessionAuditLog as _HB_SAL
            _hb_db = _HB_SL()
            try:
                _wrote = _hb_db.query(_HB_SAL).filter(
                    _HB_SAL.symbol == symbol,
                    _HB_SAL.date_key == date_key,
                    _HB_SAL.session_id == session_id,
                ).first() is not None
            finally:
                _hb_db.close()
            print(f"[HEARTBEAT] session_audit_log: {'YES' if _wrote else 'NO — row missing after write'} ({date_key})")
        except Exception as _hb_ex:
            print(f"[HEARTBEAT] session_audit_log check FAILED: {_hb_ex}")
    except Exception as _audit_err:
        print(f"[AUDIT WRITER] Non-critical failure — MAS unaffected: {_audit_err}")
        print(f"[HEARTBEAT] session_audit_log: NO — write path threw ({type(_audit_err).__name__}: {_audit_err})")

    # 7b. Unified Audit System dual-write (Phase 1, additive-only). See
    # UNIFIED_AUDIT_SYSTEM_PLAN.md v1.6 for the decision_type mapping and the
    # gauge source list — every value below is copied from the exact same
    # already-verified extraction the step-7 _write_audit() call above uses,
    # not re-derived, to avoid inventing a second, possibly-wrong source path.
    # Non-blocking: any failure here must never affect the MAS decision path.
    try:
        from harness.unified_audit_writer import write_decision_log, gauge as _g
        from database import SessionAuditLog as _SAL

        # approval_status has 4 real values; WAITING_FOR_15M means "not yet
        # evaluated" and is excluded entirely (same treatment as 4H/1H's
        # INSUFFICIENT_CANDLES) — see v1.6.
        _decision_type_map = {
            "APPROVED": ("TRADE", None),
            "STAND_DOWN": ("STAND_DOWN", None),
            "REJECTED": ("STAND_DOWN", "CRO_REJECTED"),
        }
        _mapped = _decision_type_map.get(brief.approval_status)
        if _mapped is not None:
            _decision_type, _sd_reason = _mapped
            _decided_at = datetime.now(timezone.utc)

            _campaign_log_id = None
            _session_audit_log_id = None
            _lu_db = SessionLocal()
            try:
                _cl_row = (
                    _lu_db.query(CampaignLog)
                    .filter(CampaignLog.symbol == symbol, CampaignLog.session_id == session_id, CampaignLog.date_key == date_key)
                    .first()
                )
                _campaign_log_id = _cl_row.id if _cl_row else None
                _sal_row = (
                    _lu_db.query(_SAL)
                    .filter(_SAL.symbol == symbol, _SAL.session_id == session_id, _SAL.date_key == date_key)
                    .first()
                )
                _session_audit_log_id = _sal_row.id if _sal_row else None
            finally:
                _lu_db.close()

            _15m_jewel = _fuel.get("15M_JEWEL", {})
            # "1H"/"trend" and "4H"/"trend" removed from this local list 2026-08-30
            # -- decision_gauges (below) already supplies both, from decision_
            # engine.py's own htf_fuel.py-based (9/21 EMA, KABRODA_REBUILD_SPEC.md
            # SS2) reading. This list's version was battlebox_pipeline.py's older,
            # unvalidated EMA30/50 fuel_gauge trend -- a different computation
            # sharing the same gauge_name, which was silently colliding on the
            # DecisionGaugeReading (decision_id, timeframe, gauge_name) unique
            # constraint and failing the whole gauge-readings insert on every
            # single call (confirmed: reproduces on a fresh DB, first call,
            # zero concurrency involved -- not a race condition). Keeping only
            # the validated decision_engine.py reading is also the correct
            # choice on the merits, not just the deduplication.
            _gauges = [g for g in [
                _g("15M", "energy_status", context.get("1h_fuel_status")),
                _g("15M", "kinematic_grade", _15m_jewel.get("kinematic_grade")),
                _g("15M", "bbwp", _15m_jewel.get("bbwp")),
                _g("15M", "bbwp_state", _15m_jewel.get("bbwp_state")),
                _g("15M", "pmarp", _15m_jewel.get("pmarp")),
                _g("15M", "pmarp_state", _15m_jewel.get("pmarp_state")),
                _g("15M", "rsi_divergence_type", "NONE"),
                _g("1H", "rsi", _tf1h.get("rsi")),
                _g("1H", "adx_strength", _adx_label(_j1h)),
                _g("4H", "rsi", _tf4h.get("rsi")),
                _g("4H", "adx_strength", _adx_label(_j4h)),
                _g("4H", "macd_hist", _tf4h.get("macd_hist")),
                _g("Daily", "daily_21ema_direction", _mtf.get("daily_21ema_direction")),
                _g("Daily", "daily_200sma_position", _mtf.get("daily_200sma_position")),
                _g("Weekly", "weekly_200sma_position", _mtf.get("weekly_200sma_position")),
            ] if g] + decision_gauges  # the graded model's own checklist -- conviction tier + which legs confirmed

            _atr_val = levels.get("atr")
            write_decision_log(
                symbol=symbol,
                decision_timeframe="15M",
                decision_type=_decision_type,
                date_key=date_key,
                decided_at=_decided_at,
                session_id=session_id,
                bias=brief.bias,
                entry_price=brief.entry_price,
                stop_loss=brief.stop_loss,
                t1=brief.t1,
                t2=brief.t2,
                t3=brief.t3,
                atr_pct_at_decision=(
                    round(float(_atr_val) / float(brief.entry_price) * 100.0, 4)
                    if _atr_val and brief.entry_price else None
                ),
                # STAND_DOWN/TRADE window: the 30-min calibration window that
                # produced the SSOT triggers this decision was locked from
                # (CLAUDE.md: exactly 1800s from anchor_time to lock). TRADE
                # rows get backfilled with their real lifetime at close in a
                # later phase.
                candle_window_start=_decided_at - timedelta(minutes=30),
                candle_window_end=_decided_at,
                stand_down_reason=_sd_reason,
                campaign_log_id=_campaign_log_id,
                session_audit_log_id=_session_audit_log_id,
                gauge_readings=_gauges,
            )
    except Exception as _unified_audit_err:
        print(f"[UNIFIED AUDIT] Non-critical failure — MAS unaffected: {_unified_audit_err}")

    # Step 8 (Content Publishing Engine / publisher_crew.run_publisher()) removed
    # 2026-08-27 -- no newsletter/narrative gets generated by the graded coded
    # decision layer (decision_engine.py), so there's nothing left to publish.

    return {"status": "SUCCESS", "brief": brief.dict()}


# interrogate_cro() (the Operator Commlink chat feature) removed 2026-08-30 --
# Andy's call: no LLM tied to Kabroda's cost path, period. It had already
# been a stub since 2026-08-17 (zero live cost), kept only pending "the coded
# decision layer" -- that rebuild happened (the calibrated gate), but Andy's
# direction was to retire this rather than re-enable it: interactive Q&A is
# Kabroda AI Brain's job now, a dedicated tool, not a second, smaller one
# living inside kabroda.com. POST /api/research/chat-mas (main.py) and the
# chat box in templates/macro_war_room.html are both removed too.


# audit_foreign_intel_pipeline() removed 2026-08-30 -- the Intel Auditor.
# Andy's call: gone entirely, the last LLM-based tool in this file. See the
# module header comment for the full reasoning.


# ==============================================================================
# SECTION 10 — DATABASE INJECTION (UNCHANGED FROM ORIGINAL)
# ==============================================================================

def _mark_mas_error(
    symbol: str, session_id: str, date_key: str, error_msg: str
) -> None:
    db = SessionLocal()
    try:
        log = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.session_id == session_id,
                CampaignLog.date_key == date_key,
            )
            .first()
        )
        if log:
            log.mas_approval_status = "MAS_ERROR"
            log.mas_executive_brief = f"[SYSTEM ERROR] {error_msg[:500]}"
            db.commit()
    except Exception as e:
        print(f"MAS ERROR MARKER FAILED: {e}")
    finally:
        db.close()


_NY_TZ = pytz.timezone("America/New_York")

# Session close times in ET. Source: owner specification — the NY Futures session
# boundary for BTC monitoring is the US equity cash close (3:00 PM ET). This is
# NOT derived from any exchange API or session_manager.py (which only defines
# open times). If the session boundary changes, update this dict and redeploy.
_SESSION_CLOSE_ET: Dict[str, tuple] = {
    "us_ny_futures": (15, 0),   # 3:00 PM ET — US equity cash close
    "us_ny_equity":  (16, 0),   # 4:00 PM ET
    "us_ny_pm":      (16, 15),  # 4:15 PM ET
}


def _compute_session_expires_at(session_id: str, date_key: str) -> datetime:
    """
    Returns timezone-aware UTC datetime for the session close boundary.

    NY Futures = 3:00 PM ET (US equity cash close). Not from any API — hardcoded
    per owner specification. pytz.localize() handles DST automatically so the
    UTC offset is correct year-round (EDT = UTC-4, EST = UTC-5).
    """
    close_h, close_m = _SESSION_CLOSE_ET.get(session_id, (15, 0))
    date = datetime.strptime(date_key, "%Y-%m-%d")
    local_close = _NY_TZ.localize(
        date.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    )
    return local_close.astimezone(timezone.utc)


def _inject_brief_to_database(
    symbol: str, session_id: str, date_key: str, brief: ExecutiveBrief,
    structure_reasoning: Optional[dict] = None,
) -> None:
    db = SessionLocal()
    try:
        log = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.session_id == session_id,
                CampaignLog.date_key == date_key,
            )
            .first()
        )

        if not log:
            log = CampaignLog(
                symbol=symbol,
                session_id=session_id,
                date_key=date_key,
                bias=brief.bias,
                grade="MAS_AUTO",
                entry_price=brief.entry_price,
                stop_loss=brief.stop_loss,
                t1=brief.t1,
                t2=brief.t2,
                t3=brief.t3,
                total_contracts=0.0,
                status=brief.approval_status,
            )
            db.add(log)
            print(f"|| MAS OVERLAY || New CampaignLog created for {symbol} | {session_id}.")

        log.mas_executive_brief = brief.tactical_brief
        log.mas_approval_status = brief.approval_status
        log.conviction = brief.conviction
        log.tier = brief.tier
        log.bias = brief.bias
        log.entry_price = brief.entry_price
        log.stop_loss = brief.stop_loss
        log.t1 = brief.t1
        log.t2 = brief.t2
        log.t3 = brief.t3
        log.status = brief.approval_status
        log.formatted_newsletter = brief.formatted_newsletter_md
        if structure_reasoning:
            log.structure_reasoning = json.dumps(structure_reasoning, default=str)

        # Auto-mark canonical: all BTC/USDT records are track-record quality.
        # Unconditional — covers APPROVED, STAND_DOWN, REJECTED, WAITING_FOR_15M.
        if symbol == "BTC/USDT" and not log.is_canonical:
            log.is_canonical = True

        # Set session expiry on APPROVED records so the lifecycle monitor knows
        # when to expire unfilled setups. Only set once — don't overwrite.
        if brief.approval_status == "APPROVED" and log.session_expires_at is None:
            log.session_expires_at = _compute_session_expires_at(session_id, date_key)

        db.commit()
        print(f"|| MAS OVERLAY || Brief injected for {symbol}.")
    except Exception as e:
        print(f"MAS DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()


def _inject_trade_plan_to_database(
    symbol: str, session_id: str, date_key: str, plan_fields: Dict[str, Any],
) -> None:
    """Create-only upsert for TradePlan -- deliberately NOT the same
    always-update pattern _inject_brief_to_database() above uses.
    KABRODA_COM_TRADE_PLAN_SPEC.md SS1's anti-flip-flop rule: a TradePlan
    is generated ONCE at session lock and never re-generated intraday. If
    a row already exists for this (symbol, date_key, session_id) -- e.g. a
    restart-recovery re-run of run_mas_analysis() -- skip entirely rather
    than overwrite: by the time a row exists it may already carry real
    intraday state-machine progress (WAITING/VETOED/FILLED/STOPPED/...)
    written by the monitoring loop, and CampaignLog's own always-update
    pattern silently resetting terminal state on a restart re-run is a
    pre-existing, separate landmine this function must not repeat.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(TradePlan)
            .filter(
                TradePlan.symbol == symbol,
                TradePlan.session_id == session_id,
                TradePlan.date_key == date_key,
            )
            .first()
        )
        if existing is not None:
            print(f"|| TRADE PLAN || Row already exists for {symbol} | {session_id} | {date_key} -- not re-generated (SS1 anti-flip-flop rule).")
            return

        fields = dict(plan_fields)
        fields["plan_text"] = trade_plan.render_brief(fields)
        valid_cols = set(TradePlan.__table__.columns.keys())
        row = TradePlan(**{k: v for k, v in fields.items() if k in valid_cols})
        db.add(row)
        db.commit()
        print(f"|| TRADE PLAN || {fields.get('status')} plan written for {symbol} | {session_id} | {date_key}.")

        # Daily lock email (Andy's build request, trade_plan_notify.py) --
        # fires for EVERY session lock now, WAITING or NO_PLAN alike (see
        # that module's header for why the old WAITING-only default was
        # reversed 2026-09-02 -- it caused a real production incident,
        # zero emails on a NO_PLAN morning). Built from `fields` (the raw
        # dict build_trade_plan() returned), not `row.__dict__` -- `fields`
        # still carries the transient breakout_trigger/breakdown_trigger/
        # r30_high/r30_low keys the email needs, which `row.__dict__` lost
        # (they're not real TradePlan columns, dropped by the valid_cols
        # filter above). `id` is only assigned by the db.add()/commit()
        # above, so it's added in here rather than being in `fields`
        # already. Non-blocking: a notification failure must never affect
        # the already-committed plan write above.
        try:
            import notify
            import trade_plan_notify
            mail_fields = dict(fields)
            mail_fields["id"] = row.id
            subject, body = trade_plan_notify.build_lock_email(mail_fields)
            notify.send_admin_email(subject, body)
        except Exception as _notify_err:
            print(f"[TRADE PLAN] Lock-email notification failed: {_notify_err}")
    except Exception as e:
        print(f"TRADE PLAN DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()


def _inject_gate_log(
    symbol: str,
    date_key: str,
    evaluated_at: datetime,
    levels: Dict[str, Any],
    decision_dict: Dict[str, Any],
) -> None:
    """KABRODA_REBUILD_SPEC.md §9 — one row per gate evaluation, TAKE or PASS
    alike. Andy's explicit call: log every detail. Backfill (24h outcome)
    fields are left null here -- ledger_closing_engine.py wiring is a
    fast-follow, not yet built (flagged, not silently skipped)."""
    gate = decision_dict.get("gate") or {}
    plan = decision_dict.get("plan") or {}
    reach = gate.get("reach") or {}
    checks = gate.get("checks") or {}
    db = SessionLocal()
    try:
        row = GateLog(
            date_key=date_key,
            lock_ts=evaluated_at,
            symbol=symbol,
            side=decision_dict.get("side"),
            breakout_trigger=levels.get("breakout_trigger"),
            breakdown_trigger=levels.get("breakdown_trigger"),
            box=reach.get("ratio") and (levels.get("breakout_trigger", 0) - levels.get("breakdown_trigger", 0)),
            anchor=levels.get("anchor_price"),
            range30m_high=levels.get("range30m_high"),
            range30m_low=levels.get("range30m_low"),
            daily_atr14=levels.get("daily_atr14"),
            box_atr_ratio=reach.get("ratio"),
            trigger_hour_utc=evaluated_at.hour,
            hour_ok=checks.get("session_hour"),
            veto=None if decision_dict.get("verdict_state") in ("TAKE_PREMIUM", "TAKE_STANDARD", "ALMOST") else (
                (decision_dict.get("tactical_brief") or "")[:200] if gate.get("tier") is None and gate.get("misses") else None
            ),
            gate_pass=gate.get("pass"),
            gate_tier=gate.get("tier"),
            daily_regime_table=decision_dict.get("market_regime_table"),
            daily_regime_quality=decision_dict.get("market_regime_quality"),
            micro_regime=decision_dict.get("micro_regime"),
            state=decision_dict.get("verdict_state", "PASS"),
            headline=decision_dict.get("tactical_brief"),
            entry=plan.get("entry"),
            stop=plan.get("stop"),
            t1=plan.get("t1"),
            t2=plan.get("t2"),
            t3=plan.get("t3"),
            subtrig_stop=plan.get("subtrig_stop"),
            gate_detail_json=json.dumps(gate, default=str),
            # Columns that existed in the schema already but were never
            # actually passed here (2026-08-31 fix -- confirmed by reading
            # this constructor call, not assumed): decision_engine.py now
            # surfaces these raw diagnostic values on decision_dict itself
            # (see its own 2026-08-31 comment) purely so this can populate
            # them without re-deriving anything.
            push_vol_ratio=decision_dict.get("fuel_push_ratio"),
            fuel_state=decision_dict.get("fuel_verdict"),
            trend_1h=decision_dict.get("trend_1h"),
            trend_4h=decision_dict.get("trend_4h"),
            htf_aligned=decision_dict.get("htf_aligned"),
            htf_opposed=decision_dict.get("htf_opposed"),
            # SS9a locked levels -- genuinely available in `levels` (sse_
            # engine.py's compute_sse_levels() output), just not previously
            # captured here.
            daily_support=levels.get("daily_support"),
            daily_resistance=levels.get("daily_resistance"),
            f24_poc=levels.get("f24_poc"),
            f24_vah=levels.get("f24_vah"),
            f24_val=levels.get("f24_val"),
            slope=levels.get("slope"),
            structure_score=levels.get("structure_score"),
        )
        db.add(row)
        db.commit()
        print(f"|| GATE LOG || {symbol} {date_key} -> {row.state}")
    except Exception as e:
        print(f"GATE LOG INJECTION ERROR: {e}")
    finally:
        db.close()


def _inject_decision_journal(
    symbol: str,
    session_id: str,
    date_key: str,
    brief: ExecutiveBrief,
    battlebox_payload: Dict[str, Any],
) -> None:
    db = SessionLocal()
    try:
        levels = battlebox_payload.get("levels", {})
        context = battlebox_payload.get("context", {})
        fuel_gauge = context.get("fuel_gauge", {})

        # Real energy_status from battlebox harmonic matrix
        energy_status = context.get("1h_fuel_status", "UNKNOWN")

        # Real kinematic_grade from 15M JEWEL
        kinematic_grade = fuel_gauge.get("15M_JEWEL", {}).get("kinematic_grade", "UNKNOWN")

        # Confluence score: 0-3 count of TFs aligned with brief.bias
        bias = brief.bias
        tf_1h = fuel_gauge.get("1H", {})
        tf_4h = fuel_gauge.get("4H", {})
        tf_15m = fuel_gauge.get("15M_JEWEL", {})
        score = 0
        if bias == "LONG":
            if tf_1h.get("trend") == "BULLISH":
                score += 1
            if tf_4h.get("trend") == "BULLISH":
                score += 1
            if tf_15m.get("kinematic_grade") == "PRIMED":
                score += 1
        elif bias == "SHORT":
            if tf_1h.get("trend") == "BEARISH":
                score += 1
            if tf_4h.get("trend") == "BEARISH":
                score += 1
            if tf_15m.get("kinematic_grade") == "PRIMED":
                score += 1

        decision_type = {
            "APPROVED":        "MAS_APPROVED",
            "REJECTED":        "MAS_REJECTED",
            "STAND_DOWN":      "MAS_STAND_DOWN",
            "WAITING_FOR_15M": "MAS_WAITING",
        }.get(brief.approval_status, "MAS_REJECTED")
        journal = DecisionJournal(
            symbol=symbol,
            decision_type=decision_type,
            confluence_score=score,
            confluence_direction=brief.bias,
            energy_status=energy_status,
            kinematic_grade=kinematic_grade,
            bo_price=float(levels.get("breakout_trigger", 0) or 0),
            bd_price=float(levels.get("breakdown_trigger", 0) or 0),
            asset_price=brief.entry_price,
            session_date=date_key,
            session_id=session_id,
            source="mas_flow",
            decision_reason=brief.tactical_brief,
            full_context_json=json.dumps(
                {"brief": brief.dict(), "battlebox": battlebox_payload}, default=str
            ),
        )
        db.add(journal)
        db.commit()
        print(f"|| DECISION JOURNAL || {symbol} | {decision_type}")
    except Exception as e:
        print(f"DECISION JOURNAL INJECTION ERROR: {e}")
    finally:
        db.close()
