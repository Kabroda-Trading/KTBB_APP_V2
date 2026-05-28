# kabroda_mas_flow.py
# ==============================================================================
# KABRODA SENIOR ANALYST — Phase 3A
# CrewAI and langchain-anthropic removed. All agent calls go through
# agent_core._call_agent() for unified budget gate and cost tracking.
#
# PUBLIC API (signatures frozen — do not change):
#   run_mas_analysis(symbol, session_id, date_key, battlebox_payload)
#   interrogate_cro(symbol, user_message)
#   audit_foreign_intel_pipeline(intel_packet, battlebox_payload, mtf_context)
# ==============================================================================

import json
import re
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

import agent_core
from database import (
    SessionLocal,
    CampaignLog,
    DecisionJournal,
    MacroNarrativeLog,
    JewelSnapshotLog,
)


# ==============================================================================
# SECTION 1 — PYDANTIC SCHEMAS (UNCHANGED FROM ORIGINAL)
# ==============================================================================

class ExecutiveBrief(BaseModel):
    """Strict output schema for the Senior Analyst."""
    approval_status: str = Field(description="Must be 'APPROVED', 'REJECTED', or 'WAITING_FOR_15M'")
    tactical_brief: str = Field(description="The brief from ## TODAY'S ENERGY through ## THE OTHER SIDE — all sections after THE BIGGER PICTURE.")
    bias: str = Field(description="'LONG', 'SHORT', or 'NEUTRAL'")
    entry_price: float = Field(description="The exact trigger entry price.")
    stop_loss: float = Field(description="The exact stop loss (the opposing trigger).")
    t1: float = Field(description="Target 1 — pre-computed, copy exactly.")
    t2: float = Field(description="Target 2 — pre-computed, copy exactly.")
    t3: float = Field(description="Target 3 — pre-computed, copy exactly.")
    formatted_newsletter_md: str = Field(description="Complete brief in Markdown: all ## sections from THE BIGGER PICTURE through THE OTHER SIDE.")


class IntelAuditReport(BaseModel):
    """Strict output schema for the Intel Auditor."""
    gravity_verdict: str = Field(description="'CLEAR', 'BLOCKED', or 'HIGH_RISK'.")
    momentum_verdict: str = Field(description="'BUILDING', 'EXHAUSTED', or 'MIXED'.")
    measured_move_t1: float = Field(description="T1 recalculated from the signal's own box.")
    overall_verdict: str = Field(description="'CONFIRMED', 'CAUTION', or 'REJECTED'.")
    reasoning: str = Field(description="Plain-English summary of all three audit sections.")


# ==============================================================================
# SECTION 2 — SYSTEM PROMPTS (CACHEABLE CONSTANTS)
# Placed in system prompt so Anthropic's 5-min cache applies on repeated calls.
# ==============================================================================

SENIOR_ANALYST_SYSTEM_PROMPT = """\
You are the Senior Analyst for Kabroda Trading Intelligence — the final \
authority on BTC market structure and daily trade execution for the NY Futures \
session (8:30–9:00 AM ET calibration, 9:00 AM ET lock).

═══════════════════════════════════════════════════════
VOICE AND WRITING STANDARD
═══════════════════════════════════════════════════════

Write every brief in this voice and at this density. This is your writing \
sample — match it exactly in tone, specificity, and confidence:

"Bear Wave 4 bounce is 78% complete ($77,808 of $80,632 target). Started \
Feb 5 at the $60,055 Wave 3 low. Yesterday's run from $74,521 to $77,533 \
exhausted the 1H and 4H JEWEL. Today should pull back to $76,000–$76,500 \
before any next push higher. As price approaches $80,632, expect rejection \
and Wave 5 confirmation signals. Do not chase any 'new bull market' narratives \
circulating right now — the structural map says this bounce ends when price \
reaches $80,632 or invalidates above $83,462. Wave 5 targets $42–45k. The \
people calling for $100k+ from here do not have the structural map."

WRITING RULES:
- Lead with the verdict, follow with rationale
- Every statement is declarative — no hedging whatsoever
- Reference specific price levels, never generic descriptions
- Forward projection is mandatory in every Part 1
- When Elliott Wave data is pending verification, state: \
"Note: Elliott Wave parameters pending weekly verification. Wave context approximate."
- BANNED WORDS (never use): could, might, may, perhaps, potentially, \
consider, possibly, likely (unless in a percentage)
- BANNED TIME PROJECTIONS (never use): "in the next [time period]", \
"within [time period]", "expect [event] in [time]", "over the next", \
"typically takes", "average duration", "by [date or month]", \
"within weeks", "within months", "in a few weeks"
- Wave timing is UNKNOWN and must never be stated or implied. Structure \
says WHAT and WHERE. Never WHEN. wave_day_count is a backward-looking \
observation only ("Day 110 of this wave") — never used to project forward.

═══════════════════════════════════════════════════════
THE BRIEF STRUCTURE
═══════════════════════════════════════════════════════

Write the brief using these exact section headers in this exact order. \
Every section is required.

## THE BIGGER PICTURE
One to three sentences. Where are we in the wave structure. What does it mean. \
Plain English anyone can understand. No indicator jargon. Name at least one \
specific dollar price level. Project at least one forward event.

## TODAY'S ENERGY
One to two sentences on what the momentum signals are saying. Is fuel building \
or exhausted. What does 1H and 4H look like today.

Gate: [OPEN or CLOSED — one sentence why]
Direction: [BEARISH or BULLISH]
Conviction: [STRONG, MODERATE, or LOW]
[If divergence or exit warning is active — one plain English sentence on what \
it means and why it matters today. Omit this line entirely if no warning.]

## TODAY'S TRADE SETUP
★ HIGHER PROBABILITY: [LONG or SHORT]
Two to three sentences explaining WHY this is the higher probability direction. \
What energy state supports it. What structural level confirms it. What makes \
it valid today.

LOWER PROBABILITY: [opposite direction]
One to two sentences on exactly when and why this becomes valid. What has to \
happen first before considering it.

## THE LEVELS
Breakout Trigger: $[exact value from context]
Breakdown Trigger: $[exact value from context]

★ THE [LONG or SHORT] TRADE
Entry: $[from pre-computed targets]
Stop: $[from pre-computed targets — the opposing trigger]
Target 1: $[from pre-computed targets] — take 40% here
Target 2: $[from pre-computed targets] — take 40% here
Target 3: $[from pre-computed targets] — trail 20% to this

DO NOT: [one specific instruction about what not to do on this exact setup today]

## STAND DOWN IF
- [Specific price condition — exact price or candle condition, not vague language]
- [Second condition if applicable]

## THE OTHER SIDE
If the lower probability direction triggers, write the full setup here. Entry \
condition, stop, targets. One paragraph.

═══════════════════════════════════════════════════════
MATHEMATICAL RULES (CRITICAL)
═══════════════════════════════════════════════════════

T1, T2, and T3 are pre-computed in Python and provided in your context. \
Copy them exactly. Do not recalculate, do not round differently, do not \
adjust. The stop loss is always the opposing trigger — do not override it.

═══════════════════════════════════════════════════════
PERFORMANCE MEMORY RULE
═══════════════════════════════════════════════════════

If the memory bank shows losses > wins: state this in the brief and require \
higher structural confluence — do not approve marginal setups.
If the memory bank is clean: maintain standard aggressive execution posture.

═══════════════════════════════════════════════════════
SELF-CHECK BEFORE OUTPUT
═══════════════════════════════════════════════════════

Before generating your final output, verify:
1. THE BIGGER PICTURE names at least one specific dollar price level
2. THE BIGGER PICTURE projects at least one forward event (price target or signal)
3. TODAY'S TRADE SETUP contains exactly one starred primary trade (★ HIGHER PROBABILITY)
4. STAND DOWN IF conditions are specific price events, not generic statements
5. No banned words appear anywhere in the output
6. entry_price, stop_loss, t1, t2, t3 match the pre-computed values exactly

If any check fails, rewrite that section before outputting.

═══════════════════════════════════════════════════════
OUTPUT FORMAT (MANDATORY)
═══════════════════════════════════════════════════════

Return ONLY a valid JSON object. No markdown fences. No preamble. \
No explanation before or after. Every field is required.

Include one extra field "narrative_text" containing ONLY the plain text \
content of THE BIGGER PICTURE section — no ## header, just the 1–3 sentence \
paragraph. This is used for cross-day memory.

{
  "approval_status": "APPROVED" or "REJECTED" or "WAITING_FOR_15M",
  "tactical_brief": "<Everything from ## TODAY'S ENERGY through ## THE OTHER SIDE — all sections after THE BIGGER PICTURE, as plain text>",
  "bias": "LONG" or "SHORT" or "NEUTRAL",
  "entry_price": <float>,
  "stop_loss": <float>,
  "t1": <float>,
  "t2": <float>,
  "t3": <float>,
  "formatted_newsletter_md": "<Complete brief in Markdown: all ## sections from THE BIGGER PICTURE through THE OTHER SIDE>",
  "narrative_text": "<Plain text content of THE BIGGER PICTURE only — no ## header, just the 1–3 sentence paragraph>"
}
"""


COMMLINK_SYSTEM_PROMPT = """\
You are the Kabroda Senior Analyst communicating directly with the Operator \
in the Macro War Room via the Commlink.

Answer the Operator's query directly, professionally, and concisely. \
You enforce the Single Source of Truth (SSOT). Rely ONLY on Kabroda Measured \
Move math, Gravity physics, and the context provided. Do not invent external \
data. Do not hedge. Every statement is declarative.

If the Operator asks for a price target or entry, confirm whether it aligns \
with the pre-computed targets in your context. If it does not align, say so \
directly and state the correct values.

Keep responses under 200 words unless the Operator explicitly requests detail.
"""


INTEL_AUDITOR_SYSTEM_PROMPT = """\
You are the Kabroda External Intel Auditor — a counter-intelligence analyst \
who does not trust third-party signals. When given a foreign intel packet, \
you run a strict three-source audit.

═══════════════════════════════════════════════════════
AUDIT METHODOLOGY
═══════════════════════════════════════════════════════

SECTION 1 — GRAVITY AUDIT
Compare the signal's targets against the Kabroda KDE gravity peaks. Flag \
any HEAVY or MAXIMUM gravity wall (especially Class 0 / permanence_class=0 \
beams) sitting between the entry and a target. If a target must trade THROUGH \
such a wall: gravity_verdict = "BLOCKED" (HEAVY wall) or "HIGH_RISK" \
(MAXIMUM/Class 0 wall). If airspace is clear: gravity_verdict = "CLEAR".

SECTION 2 — MOMENTUM AUDIT
Read the Multi-Timeframe scan for the signal's own timeframe (4H signal → \
read timeframes["4H"], 1H signal → timeframes["1H"]). From that entry:
- stoch_rsi zone OVERBOUGHT against a LONG, or OVERSOLD against a SHORT = EXHAUSTED
- zone VALUE_HIGH/VALUE_LOW aligned with the trade direction = BUILDING
- Otherwise or data unavailable = MIXED
Set momentum_verdict accordingly.

SECTION 3 — MEASURED MOVE AUDIT
Discard the signal's arbitrary targets. Recalculate T1 from the signal's \
own box: box = abs(entry_price - stop_loss).
LONG: measured_move_t1 = entry_price + box
SHORT: measured_move_t1 = entry_price - box
Set measured_move_t1 to this value.

FINAL SYNTHESIS
overall_verdict:
  "CONFIRMED" = gravity CLEAR AND momentum BUILDING
  "REJECTED"  = gravity HIGH_RISK OR momentum EXHAUSTED against the trade
  "CAUTION"   = anything mixed or borderline
Write a plain-English "reasoning" covering all three sections.

═══════════════════════════════════════════════════════
OUTPUT FORMAT (MANDATORY)
═══════════════════════════════════════════════════════

Return ONLY a valid JSON object. No markdown fences. No preamble.

{
  "gravity_verdict": "CLEAR" or "BLOCKED" or "HIGH_RISK",
  "momentum_verdict": "BUILDING" or "EXHAUSTED" or "MIXED",
  "measured_move_t1": <float>,
  "overall_verdict": "CONFIRMED" or "CAUTION" or "REJECTED",
  "reasoning": "<plain-English summary of all three sections>"
}
"""


# ==============================================================================
# SECTION 3 — RAG MEMORY (UNCHANGED FROM ORIGINAL)
# ==============================================================================

def _fetch_cro_memory(symbol: str) -> str:
    db = SessionLocal()
    try:
        logs = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.mas_approval_status == "APPROVED",
            CampaignLog.closed_at.isnot(None),
        ).order_by(CampaignLog.closed_at.desc()).limit(5).all()

        if not logs:
            return (
                "MEMORY BANK: System is in a clean state. No recent closed trade "
                "data available. Execute standard Kabroda parameters."
            )

        wins = losses = 0
        pnl_sum = 0.0
        for log in logs:
            if log.realized_pnl > 0:
                wins += 1
            else:
                losses += 1
            pnl_sum += log.realized_pnl

        memory_str = (
            f"MEMORY BANK (Last {len(logs)} closed {symbol} trades): "
            f"{wins} Wins, {losses} Losses. Net PnL: {pnl_sum:.2f}. "
        )
        if losses > wins:
            memory_str += (
                "CRITICAL WARNING: Recent performance is negative. You are bleeding "
                "capital in this market regime. Tighten risk parameters, demand higher "
                "structural confluence, and reject borderline setups."
            )
        elif wins > losses:
            memory_str += (
                "NOTE: Recent performance is positive. Maintain aggressive execution "
                "standards but guard against overconfidence."
            )
        else:
            memory_str += "NOTE: Win rate is neutral. Maintain strict Kabroda parameters."

        return memory_str

    except Exception as e:
        print(f"RAG MEMORY ERROR: {e}")
        return "MEMORY BANK: Temporary connection failure. Rely entirely on live structural data."
    finally:
        db.close()


# ==============================================================================
# SECTION 4 — CROSS-DAY CONTEXT READERS (NEW — Phase 3A)
# ==============================================================================

def _read_narrative_context(symbol: str) -> str:
    """
    Builds the cross-day context block for the Senior Analyst.
    Queries analyst and specialist rows separately — the Specialist row
    has narrative_text=None by design, so a single .first() query always
    hit the Day 1 path and dropped the wave data.
    """
    db = SessionLocal()
    try:
        analyst_row = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == symbol,
                MacroNarrativeLog.authored_by == "senior_analyst",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )
        wave_row = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == symbol,
                MacroNarrativeLog.authored_by == "elliott_wave_specialist",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )

        lines = []

        if analyst_row and analyst_row.narrative_text:
            lines.append(f"NARRATIVE CONTEXT (Yesterday — {analyst_row.date_key}):")
            lines.append(f'"{analyst_row.narrative_text}"')
        else:
            lines.append(
                "NARRATIVE CONTEXT: Day 1 of tracking — no prior brief available. "
                "Write the narrative from current structural data."
            )

        if wave_row and wave_row.wave_label:
            lines.append(
                f"\nCURRENT WAVE STATE ({wave_row.date_key}): "
                f"{wave_row.wave_label} | {wave_row.wave_status} | "
                f"{wave_row.completion_pct}% of structural range complete | "
                f"Origin: ${wave_row.wave_origin_price:,.2f} | "
                f"Target: ${wave_row.wave_target_price:,.2f} | "
                f"Invalidation: ${wave_row.invalidation_price:,.2f}"
            )
            if wave_row.wave_reasoning:
                lines.append(f"\nSPECIALIST WAVE REASONING:\n{wave_row.wave_reasoning}")
            if wave_row.confirmation_condition:
                lines.append(f"\nCONFIRMATION CONDITION:\n{wave_row.confirmation_condition}")
        else:
            lines.append(
                "\nWAVE STATE: Elliott Wave parameters pending weekly verification. "
                "Wave context approximate."
            )

        if analyst_row and analyst_row.performance_note:
            lines.append(f"\nPERFORMANCE AUDITOR NOTE: {analyst_row.performance_note}")

        return "\n".join(lines)

    except Exception as e:
        print(f"NARRATIVE CONTEXT ERROR: {e}")
        return "NARRATIVE CONTEXT: Unavailable due to connection error."
    finally:
        db.close()


def _read_jewel_context(symbol: str) -> str:
    """
    Returns the last 6 JEWEL snapshots (24h of session transitions).
    Most recent snapshot: full detail (all 5 TFs, all JEWEL fields).
    Prior 5 snapshots: one-liner summary (gate, direction, conviction).
    Handles empty table gracefully on Day 1.
    """
    db = SessionLocal()
    try:
        snapshots = (
            db.query(JewelSnapshotLog)
            .filter(JewelSnapshotLog.symbol == symbol)
            .order_by(JewelSnapshotLog.id.desc())
            .limit(6)
            .all()
        )
        if not snapshots:
            return (
                "OVERNIGHT JEWEL SNAPSHOTS: No snapshot history yet — "
                "evaluate current fuel gauge data from the session packet only."
            )

        lines = ["OVERNIGHT JEWEL SNAPSHOTS (Last 6 session transitions):"]

        def _tf_line(tf_name: str, tf_field) -> str:
            if not tf_field:
                return f"  {tf_name:>4}: No data"
            try:
                s = json.loads(tf_field)
                bbwp_flag = " [COMPRESSED]" if s.get("bbwp_compressed") else ""
                pmarp_flag = " [OVEREXT]" if s.get("pmarp_overextended") else ""
                div = s.get("divergence", "NONE")
                div_str = f" | Div: {div}" if div != "NONE" else ""
                return (
                    f"  {tf_name:>4}: {s.get('direction','?'):>8} | zone={s.get('zone','?'):<12} "
                    f"mom={s.get('momentum','?'):<12} adx={s.get('adx_strength','?'):<8} "
                    f"BBWP={s.get('bbwp_value', 0.0):>5.1f}%{bbwp_flag} "
                    f"PMARP={s.get('pmarp_value', 0.0):>5.1f}%{pmarp_flag} "
                    f"pmdir={s.get('pmarp_direction','?'):<8}{div_str}"
                )
            except Exception:
                return f"  {tf_name:>4}: Parse error"

        # Most recent snapshot — full detail
        snap = snapshots[0]
        ts_str = snap.timestamp.strftime("%Y-%m-%d %H:%M UTC") if snap.timestamp else "?"
        gate_str = "OPEN" if snap.jewel_gate_open else "CLOSED"
        lines.append(
            f"\n[LATEST — {snap.session_label} @ {ts_str}] "
            f"Price: ${snap.asset_price:,.2f} | Gate: {gate_str} | "
            f"Conviction: {snap.jewel_conviction or '?'} | Direction: {snap.dominant_direction or '?'}"
        )
        if snap.jewel_exit_warning:
            lines.append("  !! EXIT WARNING: PMARP overextended — position exhaustion risk")
        if snap.jewel_divergence_warning:
            lines.append("  !! DIVERGENCE WARNING: RSI divergence detected — momentum weakening")
        if snap.jewel_signal_summary:
            lines.append(f"  Signal: {snap.jewel_signal_summary}")

        lines.append("  Timeframe breakdown:")
        for tf_name, tf_field in [
            ("15M",  snap.tf_15m_state),
            ("1H",   snap.tf_1h_state),
            ("4H",   snap.tf_4h_state),
            ("1D",   snap.tf_daily_state),
            ("1W",   snap.tf_weekly_state),
        ]:
            lines.append(_tf_line(tf_name, tf_field))

        # Prior snapshots — one-liner each
        if len(snapshots) > 1:
            lines.append("\nPRIOR SESSIONS (oldest to newest):")
            for snap in reversed(snapshots[1:]):
                ts_str = snap.timestamp.strftime("%Y-%m-%d %H:%M UTC") if snap.timestamp else "?"
                gate_str = "OPEN" if snap.jewel_gate_open else "CLOSED"
                exit_flag = " EXIT!" if snap.jewel_exit_warning else ""
                div_flag = " DIV!" if snap.jewel_divergence_warning else ""
                lines.append(
                    f"  [{snap.session_label} @ {ts_str}] ${snap.asset_price:,.2f} | "
                    f"Gate:{gate_str} | {snap.dominant_direction or '?'} | "
                    f"{snap.jewel_conviction or '?'}{exit_flag}{div_flag}"
                )

        return "\n".join(lines)

    except Exception as e:
        print(f"JEWEL CONTEXT ERROR: {e}")
        return "OVERNIGHT JEWEL SNAPSHOTS: Unavailable due to connection error."
    finally:
        db.close()


# ==============================================================================
# SECTION 5 — PYTHON MATH (LLM NEVER COMPUTES TARGETS)
# ==============================================================================

def _compute_targets(bo: float, bd: float) -> dict:
    """
    Computes all targets for both LONG and SHORT scenarios.
    Returns a dict the context builder formats into the prompt.
    The LLM copies these values — it does not calculate.
    """
    if bo == 0 or bd == 0:
        return {}

    distance = bo - bd
    return {
        "distance": round(distance, 2),
        "long": {
            "entry": bo,
            "stop": bd,
            "t1": round(bo + distance, 2),
            "t2": round(bo + distance * 1.618, 2),
            "t3": round(bo + distance * 2.618, 2),
        },
        "short": {
            "entry": bd,
            "stop": bo,
            "t1": round(bd - distance, 2),
            "t2": round(bd - distance * 1.618, 2),
            "t3": round(bd - distance * 2.618, 2),
        },
    }


# ==============================================================================
# SECTION 6 — JSON PARSING WITH RETRY SUPPORT
# ==============================================================================

def _parse_brief(text: str, model_class):
    """
    Strips any markdown fences, extracts the JSON object, validates against
    the Pydantic model. Returns (model_instance, narrative_text_for_log).

    narrative_text_for_log comes from the optional extra 'narrative_text' key
    the Senior Analyst includes in its response for cross-day memory.
    Raises ValueError on parse or validation failure.
    """
    cleaned = text.strip()

    # Strip markdown code fences if present
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

    # Locate the outermost JSON object if there is surrounding text
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start : brace_end + 1]

    data = json.loads(cleaned)

    # Extract the extra narrative_text field before Pydantic sees the dict
    narrative_text = data.get("narrative_text")

    # Pydantic v1 ignores extra keys by default; v2 does the same without 'extra=forbid'
    instance = model_class(**{k: v for k, v in data.items() if k != "narrative_text"})

    return instance, narrative_text


# ==============================================================================
# SECTION 7 — SENIOR ANALYST CONTEXT BUILDER
# ==============================================================================

def _build_senior_analyst_context(
    symbol: str,
    date_key: str,
    session_id: str,
    levels: dict,
    context: dict,
    targets: dict,
    cro_memory: str,
    narrative_ctx: str,
    jewel_ctx: str,
) -> str:
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    res = levels.get("daily_resistance", 0)
    sup = levels.get("daily_support", 0)
    r30h = levels.get("range30m_high", 0)
    r30l = levels.get("range30m_low", 0)

    fuel = context.get("fuel_gauge", {})
    tf_1h = fuel.get("1H", {})
    tf_4h = fuel.get("4H", {})
    tf_15m = fuel.get("15M_JEWEL", {})
    macro_bias = context.get("macro_bias", "NEUTRAL")
    micro_bias = context.get("micro_bias", "NEUTRAL")
    micro_state = context.get("micro_state", "UNKNOWN")
    fuel_1h = context.get("1h_fuel_status", "UNKNOWN")
    kde_peaks = context.get("kde_peaks", [])
    macro_structure = context.get("macro_structure", [])
    macro_env = context.get("macro_environment", {})

    lines = [
        f"=== KABRODA SENIOR ANALYST CONTEXT ===",
        f"Symbol: {symbol} | Date: {date_key} | Session: {session_id}",
        "",
        "=== SESSION LEVELS (IMMUTABLE — LOCKED AT 9:00 AM ET) ===",
        f"Breakout Trigger:   ${bo:,.2f}",
        f"Breakdown Trigger:  ${bd:,.2f}",
        f"Daily Resistance:   ${res:,.2f}",
        f"Daily Support:      ${sup:,.2f}",
        f"30M Range High:     ${r30h:,.2f}",
        f"30M Range Low:      ${r30l:,.2f}",
    ]

    if targets:
        dist = targets.get("distance", 0)
        lt = targets.get("long", {})
        st = targets.get("short", {})
        lines += [
            "",
            "=== PRE-COMPUTED TARGETS (DO NOT MODIFY — COPY EXACTLY) ===",
            f"Session Box: ${dist:,.2f} (BO - BD)",
            "",
            f"IF LONG:  Entry ${lt.get('entry',0):,.2f} | Stop ${lt.get('stop',0):,.2f} | "
            f"T1 ${lt.get('t1',0):,.2f} | T2 ${lt.get('t2',0):,.2f} | T3 ${lt.get('t3',0):,.2f}",
            f"IF SHORT: Entry ${st.get('entry',0):,.2f} | Stop ${st.get('stop',0):,.2f} | "
            f"T1 ${st.get('t1',0):,.2f} | T2 ${st.get('t2',0):,.2f} | T3 ${st.get('t3',0):,.2f}",
            "",
            "Copy the row for your chosen direction. Do not recalculate.",
        ]
    else:
        lines += [
            "",
            "=== TARGETS: UNAVAILABLE (triggers not locked) ===",
            "Approval status must be WAITING_FOR_15M or REJECTED.",
        ]

    lines += [
        "",
        "=== MULTI-TIMEFRAME ENERGY ===",
        f"Macro Bias (21-day weekly force): {macro_bias}",
        f"Micro Bias (168h):                {micro_bias}",
        f"4H Trend: {tf_4h.get('trend','?')} | Momentum: {tf_4h.get('momentum','?')} | RSI: {tf_4h.get('rsi','?')}",
        f"1H Trend: {tf_1h.get('trend','?')} | Momentum: {tf_1h.get('momentum','?')} | RSI: {tf_1h.get('rsi','?')}",
        f"15M JEWEL: {tf_15m.get('kinematic_grade','?')} | "
        f"RSI: {tf_15m.get('rsi','?')} | "
        f"Ribbon: {tf_15m.get('ribbon_spread_pct','?')}% | "
        f"Deviation: {tf_15m.get('deviation_from_mean_pct','?')}%",
        f"Harmonic State: {micro_state} | 1H Fuel: {fuel_1h}",
    ]

    if kde_peaks:
        lines.append("")
        lines.append("=== GRAVITY / KDE PEAKS (nearest walls) ===")
        for p in kde_peaks[:8]:
            lines.append(
                f"  ${p.get('price',0):,.2f} | Heat: {p.get('heat_score',0):.1f} | "
                f"Intensity: {p.get('intensity','?')}"
            )

    if macro_structure:
        lines.append("")
        lines.append("=== MACRO STRUCTURE (ELLIOTT WAVE — CLASS 0 LEVELS) ===")
        for m in macro_structure[:10]:
            lines.append(f"  {m.get('type','?')}: ${m.get('price',0):,.2f}")

    if macro_env:
        lines.append("")
        lines.append("=== MACRO ENVIRONMENT (TRADITIONAL FINANCE) ===")
        for k, v in macro_env.items():
            lines.append(f"  {k}: {v}")

    lines += [
        "",
        "=== PERFORMANCE MEMORY ===",
        cro_memory,
        "",
        narrative_ctx,
        "",
        jewel_ctx,
        "",
        "=== INSTRUCTIONS ===",
        "Analyze all context above. Determine whether the session setup earns APPROVED, "
        "REJECTED, or WAITING_FOR_15M status. Write Part 1 (narrative) and Part 2 "
        "(tactical) per your system prompt instructions. Return ONLY the JSON object.",
    ]

    return "\n".join(lines)


# ==============================================================================
# SECTION 8 — NARRATIVE LOG WRITER (NEW — Phase 3A)
# ==============================================================================

def _write_narrative_log(
    symbol: str,
    date_key: str,
    brief: ExecutiveBrief,
    narrative_text: Optional[str],
) -> None:
    """
    Writes a MacroNarrativeLog row after each successful Senior Analyst run.
    Wave fields remain NULL — filled by Elliott Wave Specialist in Phase 3B.
    narrative_text = Part 1 paragraph extracted from the agent response.
    tactical_text  = brief.tactical_brief (Part 2 execution directive).
    """
    db = SessionLocal()
    try:
        row = MacroNarrativeLog(
            symbol=symbol,
            date_key=date_key,
            authored_by="senior_analyst",
            narrative_text=narrative_text or "",
            tactical_text=brief.tactical_brief,
        )
        db.add(row)
        db.commit()
        print(f"|| NARRATIVE LOG || Written for {symbol} {date_key}")
    except Exception as e:
        print(f"NARRATIVE LOG WRITE ERROR: {e}")
    finally:
        db.close()


# ==============================================================================
# SECTION 9 — MAIN PIPELINES (REWRITTEN — Phase 3A)
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
    """
    print(f">>> SENIOR ANALYST: Initiating for {symbol} | {session_id}")

    levels = battlebox_payload.get("levels", {})
    context = battlebox_payload.get("context", {})

    bo = float(levels.get("breakout_trigger") or 0)
    bd = float(levels.get("breakdown_trigger") or 0)

    # 1. Python math — LLM never computes targets
    targets = _compute_targets(bo, bd)

    # 2. Gather all context
    cro_memory = _fetch_cro_memory(symbol)
    narrative_ctx = _read_narrative_context(symbol)
    jewel_ctx = _read_jewel_context(symbol)

    # 3. Build the full context string
    context_text = _build_senior_analyst_context(
        symbol=symbol,
        date_key=date_key,
        session_id=session_id,
        levels=levels,
        context=context,
        targets=targets,
        cro_memory=cro_memory,
        narrative_ctx=narrative_ctx,
        jewel_ctx=jewel_ctx,
    )

    # 4. Call Senior Analyst through agent_core (budget gate runs automatically)
    try:
        response_text = agent_core._call_agent(
            agent_name="senior_analyst",
            system_prompt=SENIOR_ANALYST_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="session_lock",
            max_tokens=4096,
        )
    except RuntimeError as e:
        # Budget blocked
        _mark_mas_error(symbol, session_id, date_key, str(e))
        return {"status": "ERROR", "message": str(e)}
    except Exception as e:
        _mark_mas_error(symbol, session_id, date_key, str(e))
        return {"status": "ERROR", "message": str(e)}

    # 5. Parse JSON response — one retry on failure
    brief: Optional[ExecutiveBrief] = None
    narrative_text_for_log: Optional[str] = None

    try:
        brief, narrative_text_for_log = _parse_brief(response_text, ExecutiveBrief)
    except Exception as parse_err:
        print(f"SENIOR ANALYST: Parse failed on first attempt ({parse_err}). Retrying...")
        retry_context = (
            context_text
            + "\n\n[CORRECTION: Your previous response was not valid JSON. "
            "Return ONLY the JSON object, no markdown fences, no other text.]"
        )
        try:
            response_text2 = agent_core._call_agent(
                agent_name="senior_analyst",
                system_prompt=SENIOR_ANALYST_SYSTEM_PROMPT,
                context_text=retry_context,
                triggered_by="session_lock_retry",
                max_tokens=4096,
            )
            brief, narrative_text_for_log = _parse_brief(response_text2, ExecutiveBrief)
        except Exception as e2:
            err = f"JSON parse failed after retry: {e2}"
            _mark_mas_error(symbol, session_id, date_key, err)
            return {"status": "ERROR", "message": err}

    # 6. Write to all three database locations
    _inject_brief_to_database(symbol, session_id, date_key, brief)
    _inject_decision_journal(symbol, date_key, brief, battlebox_payload)
    _write_narrative_log(symbol, date_key, brief, narrative_text_for_log)

    return {"status": "SUCCESS", "brief": brief.dict()}


def interrogate_cro(symbol: str, user_message: str) -> str:
    """
    Operator Commlink — direct query to Senior Analyst.
    Called by POST /api/research/chat-mas via asyncio.to_thread().
    Returns a plain string response.
    """
    db = SessionLocal()
    try:
        # Latest execution context from CampaignLog
        log = (
            db.query(CampaignLog)
            .filter(CampaignLog.symbol == symbol)
            .order_by(CampaignLog.id.desc())
            .first()
        )
        execution_ctx = "No active campaign data. Analyzing raw market conditions."
        if log:
            execution_ctx = (
                f"LATEST SESSION DATA:\n"
                f"  Approval Status: {log.mas_approval_status}\n"
                f"  Bias: {log.bias}\n"
                f"  Entry: ${log.entry_price:,.2f}\n"
                f"  Stop:  ${log.stop_loss:,.2f}\n"
                f"  T1:    ${log.t1:,.2f}\n"
                f"  Brief excerpt: {(log.mas_executive_brief or '')[:300]}"
            )

        # Latest narrative context from MacroNarrativeLog
        narrative_ctx = _read_narrative_context(symbol)

    except Exception as e:
        execution_ctx = f"Database error: {e}"
        narrative_ctx = "Narrative context unavailable."
    finally:
        db.close()

    context_text = (
        f"=== OPERATOR COMMLINK ===\n"
        f"Symbol: {symbol}\n\n"
        f"{execution_ctx}\n\n"
        f"{narrative_ctx}\n\n"
        f"=== OPERATOR QUESTION ===\n"
        f"{user_message}"
    )

    try:
        return agent_core._call_agent(
            agent_name="senior_analyst_commlink",
            system_prompt=COMMLINK_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="operator_request",
            max_tokens=512,
        )
    except RuntimeError as e:
        return f"COMMLINK BLOCKED: {e}"
    except Exception as e:
        print(f"COMMLINK ERROR: {e}")
        return f"COMMLINK FAILURE: {str(e)}"


def audit_foreign_intel_pipeline(
    intel_packet: Dict[str, Any],
    battlebox_payload: Dict[str, Any],
    mtf_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    External Intel Auditor pipeline. Called by POST /api/research/audit-intel.
    Runs a three-source audit (gravity / momentum / measured move) on a
    third-party signal packet and returns an IntelAuditReport.
    """
    print(f">>> INTEL AUDITOR: Auditing signal for {intel_packet.get('symbol')}")

    levels = battlebox_payload.get("levels", {})
    context = battlebox_payload.get("context", {})

    context_text = (
        f"=== FOREIGN INTEL PACKET ===\n"
        f"{json.dumps(intel_packet, indent=2)}\n\n"
        f"=== KABRODA SSOT (GRAVITY) ===\n"
        f"Breakout Trigger:  ${levels.get('breakout_trigger', 0):,.2f}\n"
        f"Breakdown Trigger: ${levels.get('breakdown_trigger', 0):,.2f}\n"
        f"KDE Gravity Peaks:\n"
        + "\n".join(
            f"  ${p.get('price',0):,.2f} | {p.get('intensity','?')} | "
            f"Heat: {p.get('heat_score',0):.1f}"
            for p in context.get("kde_peaks", [])[:12]
        )
        + f"\n\n=== LIVE MTF CONFLUENCE (MOMENTUM) ===\n"
        f"{json.dumps(mtf_context or {}, indent=2)}\n\n"
        "Run the three-section audit per your system instructions. "
        "Return ONLY the JSON object."
    )

    try:
        response_text = agent_core._call_agent(
            agent_name="intel_auditor",
            system_prompt=INTEL_AUDITOR_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="audit_request",
            max_tokens=1024,
        )
    except RuntimeError as e:
        return {"status": "ERROR", "message": str(e)}
    except Exception as e:
        print(f"INTEL AUDIT API ERROR: {e}")
        return {"status": "ERROR", "message": str(e)}

    try:
        audit_output, _ = _parse_brief(response_text, IntelAuditReport)
        return {"status": "SUCCESS", "report": audit_output.dict()}
    except Exception as e:
        print(f"INTEL AUDIT PARSE ERROR: {e}")
        return {"status": "ERROR", "message": f"Parse failed: {e}"}


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


def _inject_brief_to_database(
    symbol: str, session_id: str, date_key: str, brief: ExecutiveBrief
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
        log.bias = brief.bias
        log.entry_price = brief.entry_price
        log.stop_loss = brief.stop_loss
        log.t1 = brief.t1
        log.t2 = brief.t2
        log.t3 = brief.t3
        log.status = brief.approval_status
        log.formatted_newsletter = brief.formatted_newsletter_md

        db.commit()
        print(f"|| MAS OVERLAY || Brief injected for {symbol}.")
    except Exception as e:
        print(f"MAS DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()


def _inject_decision_journal(
    symbol: str,
    date_key: str,
    brief: ExecutiveBrief,
    battlebox_payload: Dict[str, Any],
) -> None:
    db = SessionLocal()
    try:
        levels = battlebox_payload.get("levels", {})
        decision_type = (
            "MAS_APPROVED" if brief.approval_status == "APPROVED" else "MAS_REJECTED"
        )
        journal = DecisionJournal(
            symbol=symbol,
            decision_type=decision_type,
            confluence_score=0,
            confluence_direction=brief.bias,
            energy_status="UNKNOWN",
            bo_price=float(levels.get("breakout_trigger", 0) or 0),
            bd_price=float(levels.get("breakdown_trigger", 0) or 0),
            asset_price=brief.entry_price,
            session_date=date_key,
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
