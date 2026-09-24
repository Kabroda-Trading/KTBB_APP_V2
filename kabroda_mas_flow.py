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
# replaced the old decision layer (2026-08-30). 2026-09-24 (V2 Crown
# retirement): decision_engine.py's own gate call is gone too -- this
# function now only preps shared candle/levels data and writes
# TravelerPlan (GATE_TRAVELER's own D1 plan object). See CLAUDE.md's
# "Strategic Direction" section and V2_RETIREMENT_MAP.md.
# ==============================================================================

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytz

import asyncio

import market_data
from database import (
    SessionLocal,
    TravelerPlan,
    SessionLock,
)


# ==============================================================================
# SECTION 1 — PYDANTIC SCHEMAS -- ExecutiveBrief (the decision layer's
# output schema, originally the Senior Analyst LLM's own schema) removed
# 2026-09-24 (V2 Crown retirement) along with decision_engine.py itself
# and every V2-only _inject_* function that consumed it. See CLAUDE.md's
# "Strategic Direction" section and V2_RETIREMENT_MAP.md for the full
# retirement record.
# ==============================================================================


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
# the shared candle/levels prep and the Traveler injection need -- see its
# own docstring for the current, real pipeline.
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
    Primary MAS pipeline. Fired at session lock (9:00 AM ET) by
    battlebox_pipeline.py. Writes TravelerPlan (GATE_TRAVELER's own D1
    plan object) from the session's locked levels, then sets
    SessionLock.mas_completed_at -- the real completion marker
    main.py's Senior Analyst scheduler dedup check reads.

    REBUILT 2026-09-24 (V2 Crown retirement, CLAUDE.md's "Strategic
    Direction" section, V2_RETIREMENT_MAP.md). Andy's explicit
    authorization: the calibrated gate (decision_engine.py) and
    everything that consumed its output (CampaignLog/TradePlan/GateLog/
    DecisionJournal injection, the two forward-audit writer blocks) are
    retired, not patched around -- the Traveler (gate_traveler.py/
    MGMT_E1_STACK) is now the sole live decision system. This function
    keeps only the shared candle/levels prep both systems always needed
    and the Traveler injection block that was always independent of
    decision_engine.py's own verdict.
    """
    print(f">>> GATE: Evaluating {symbol} | {session_id}")

    levels = dict(battlebox_payload.get("levels", {}))
    context = battlebox_payload.get("context", {})

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
    # docstring for the incident. Applied here so this function's own
    # daily_atr14/price reads use the same "confirmed" definition every
    # other evaluator of these candles (V2's now-retired gate, still-live
    # trade_plan_engine.py polling, the Traveler's own polling) agrees on.
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

    # Traveler Plan (Phase 2, CC_WORK_ORDER_PHASE2.md) -- GATE_TRAVELER's
    # own D1/D2 plan object, built from the locked levels alone. Always was
    # independent of decision_engine.py's own verdict (GATE_TRAVELER never
    # used V2's Krown-Cross/RSI-zone/fuel gate -- its own taken-gate is
    # trigger-touch-fill + tercile-skip, evaluated later by traveler_plan_
    # engine.py at the real cross), which is why this block needed no
    # changes at all when V2's own decision/injection block (formerly here,
    # removed 2026-09-24) was retired. Non-blocking: additive, a failure
    # here must never affect the SessionLock levels already locked above.
    try:
        if bo and bd and bo > bd:
            _inject_traveler_plan_to_database(
                symbol, session_id, date_key,
                breakout_trigger=bo, breakdown_trigger=bd,
                r30_high=levels.get("range30m_high", 0.0), r30_low=levels.get("range30m_low", 0.0),
                rsi_4h_at_lock=levels.get("rsi_4h_at_lock"),
            )
    except Exception as _trav_err:
        print(f"[TRAVELER PLAN] Non-critical failure -- MAS unaffected: {_trav_err}")

    # Both forward-audit writer blocks that used to live here (the SS9
    # frozen-decision-record write via harness/audit_writer.py, and the
    # Unified Audit System dual-write via harness/unified_audit_writer.py)
    # were entirely decision_engine.py-verdict-shaped -- keyed off `brief`/
    # `decision_dict`/`decision_gauges`, none of which exist anymore.
    # Removed outright 2026-09-24 (V2 Crown retirement) along with the V2
    # decision block above, not ported to read Traveler data instead --
    # no Andy ruling asked for a Traveler-shaped replacement of either
    # writer, and CLAUDE.md's division of labor ("kabroda.com is the
    # recorder, the Brain is the auditor") already routes the Traveler's
    # own forward-test audit through GET /api/export/traveler-log.csv
    # (3a, main.py), not through SessionAuditLog/DecisionGaugeReading.

    # 2026-09-24 (V2 Crown retirement) -- the real completion marker
    # main.py's Senior Analyst scheduler dedup check needs (see
    # SessionLock.mas_completed_at's own comment). Set unconditionally
    # here, at the true end of the pipeline, regardless of whether the
    # Traveler injection block above produced a tradeable plan -- this
    # must mean "the pipeline ran," not "a plan was written," or a day
    # where that block's own bo/bd check is false would falsely loop-retry
    # forever (the same class of dedup bug already hit once with
    # MacroNarrativeLog). Non-blocking, same reasoning as every other
    # write in this function: a failure here must never affect the SSOT
    # writes already committed above.
    try:
        _completion_db = SessionLocal()
        try:
            _lock_row = _completion_db.query(SessionLock).filter(
                SessionLock.symbol == symbol,
                SessionLock.session_id == session_id,
                SessionLock.date_key == date_key,
            ).first()
            if _lock_row is not None:
                _lock_row.mas_completed_at = datetime.now(timezone.utc)
                _completion_db.commit()
        finally:
            _completion_db.close()
    except Exception as _completion_err:
        print(f"[MAS COMPLETION MARKER] Non-critical failure -- MAS unaffected: {_completion_err}")

    return {"status": "SUCCESS", "symbol": symbol, "session_id": session_id, "date_key": date_key}


# interrogate_cro() (the Operator Commlink chat feature) removed 2026-08-30 --
# Andy's call: no LLM tied to Kabroda's cost path, period. It had already
# been a stub since 2026-08-17 (zero live cost), kept only pending "the coded
# decision layer" -- that rebuild happened (the calibrated gate), but Andy's
# direction was to retire this rather than re-enable it: interactive Q&A is
# Kabroda AI Brain's job now, a dedicated tool, not a second, smaller one
# living inside kabroda.com. POST /api/research/chat-mas (main.py) and the
# chat box that used to live in the Macro War Room page are both removed
# too (that page itself was removed entirely 2026-09-23 -- Andy's call
# during the strategic site audit, see main.py's own removal comment).


# audit_foreign_intel_pipeline() removed 2026-08-30 -- the Intel Auditor.
# Andy's call: gone entirely, the last LLM-based tool in this file. See the
# module header comment for the full reasoning.


# ==============================================================================
# SECTION 10 — DATABASE INJECTION (UNCHANGED FROM ORIGINAL)
# ==============================================================================

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

    DEFERRED-DEAD as of 2026-09-24 (V2 Crown retirement, Step 3e): this
    function's only remaining caller in THIS file (_inject_brief_to_
    database()) is removed in the same edit pass as the V2 decision block
    above. But trade_plan_engine.py still imports this function at MODULE
    LEVEL (`from kabroda_mas_flow import _compute_session_expires_at`),
    and that file is V2-only but scheduled for deletion in Step 3f, not
    this one. Deleting this function (or _NY_TZ/_SESSION_CLOSE_ET below)
    now would break trade_plan_engine.py's import before its own
    scheduled removal. Leave all three exactly as-is until Step 3f
    deletes their last real caller.
    """
    close_h, close_m = _SESSION_CLOSE_ET.get(session_id, (15, 0))
    date = datetime.strptime(date_key, "%Y-%m-%d")
    local_close = _NY_TZ.localize(
        date.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    )
    return local_close.astimezone(timezone.utc)


# _inject_brief_to_database() (CampaignLog) and _inject_trade_plan_to_
# database() (TradePlan) removed 2026-09-24 (V2 Crown retirement) --
# both were entirely decision_engine.py-verdict-shaped (ExecutiveBrief/
# plan_fields), with no Traveler equivalent needed: TravelerPlan already
# has its own independent create-only writer just below,
# _inject_traveler_plan_to_database(), which never depended on either of
# these two.


def _inject_traveler_plan_to_database(
    symbol: str, session_id: str, date_key: str,
    breakout_trigger: float, breakdown_trigger: float,
    r30_high: float, r30_low: float, rsi_4h_at_lock: Optional[float],
) -> None:
    """Create-only upsert for TravelerPlan -- same anti-flip-flop reasoning
    as _inject_trade_plan_to_database() above (a restart-recovery re-run of
    run_mas_analysis() must never overwrite a row the polling loop may
    already have advanced). Unconditional WAITING_CROSS write whenever real
    levels exist -- GATE_TRAVELER has no lock-time gate to evaluate (its
    only gate, trigger-touch-fill + tercile-skip, is evaluated at the real cross
    by traveler_plan_engine.py), so there's no NO_PLAN-equivalent state
    here at all."""
    db = SessionLocal()
    try:
        existing = (
            db.query(TravelerPlan)
            .filter(
                TravelerPlan.symbol == symbol,
                TravelerPlan.session_id == session_id,
                TravelerPlan.date_key == date_key,
            )
            .first()
        )
        if existing is not None:
            print(f"|| TRAVELER PLAN || Row already exists for {symbol} | {session_id} | {date_key} -- not re-generated.")
            return

        row = TravelerPlan(
            symbol=symbol, session_id=session_id, date_key=date_key,
            status="WAITING_CROSS",
            breakout_trigger=breakout_trigger, breakdown_trigger=breakdown_trigger,
            r30_high=r30_high, r30_low=r30_low,
            rsi_4h_at_lock=rsi_4h_at_lock,
        )
        db.add(row)
        db.commit()
        print(f"|| TRAVELER PLAN || WAITING_CROSS plan written for {symbol} | {session_id} | {date_key}.")

        # Ruling C (DeepSeek, relayed by Andy 2026-09-15): TRAVELER's own
        # LOCK briefing email -- GATE_TRAVELER has no lock-time gate (see
        # this function's own docstring), so unlike v2's four-disposition
        # lock email this is always the same shape: levels + "watching for
        # a cross." Same non-blocking, own-try/except pattern as the
        # TradePlan LOCK email above -- a notify failure must never affect
        # the already-committed plan write.
        try:
            import notify
            import traveler_plan_notify
            mail_fields = {
                "id": row.id, "symbol": symbol,
                "breakout_trigger": breakout_trigger, "breakdown_trigger": breakdown_trigger,
                "r30_high": r30_high, "r30_low": r30_low, "rsi_4h_at_lock": rsi_4h_at_lock,
            }
            subject, body = traveler_plan_notify.build_traveler_lock_email(mail_fields)
            notify.send_admin_email(subject, body)
        except Exception as _notify_err:
            print(f"[TRAVELER PLAN] Lock-email notification failed: {_notify_err}")
    except Exception as e:
        print(f"TRAVELER PLAN DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()


# _inject_gate_log() (GateLog) and _inject_decision_journal()
# (DecisionJournal) removed 2026-09-24 (V2 Crown retirement). Both were
# entirely decision_engine.py-verdict-shaped (decision_dict/ExecutiveBrief)
# with no Traveler equivalent needed -- the Traveler's own forward-test
# audit trail is GET /api/export/traveler-log.csv (3a, main.py), per
# CLAUDE.md's "kabroda.com is the recorder, the Brain is the auditor"
# division of labor.
