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
from database import (
    SessionLocal,
    CampaignLog,
    DecisionJournal,
    GateLog,
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
# SECTION 2 — SYSTEM PROMPTS (CACHEABLE CONSTANTS)
# Placed in system prompt so Anthropic's 5-min cache applies on repeated calls.
#
# 2026-08-17 (Kabroda Audit AUDIT_FINDINGS.md #19 / REBUILD_PLAN.md): removed
# "15M Kinematic Grade is OVEREXTENDED" from STAND_DOWN CONDITION 2 (was
# sub-condition c, at least-2-of-3 -- now both-of-2) and from the ALLOCATION
# RULE's single-target trigger list. battlebox_pipeline.py's kinematic_grade
# formula has zero validation evidence anywhere in the codebase for its 15M
# form -- the only backtest ever run against it was of a ported copy on other
# timeframes, and that one failed (gravity_engine.py's own docstring). Not
# removed from the raw context dump below (still informational, same as
# every other non-gating signal already shown to the model) -- only from the
# two places it was functioning as an unvalidated formal gate.
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
- Every statement is declarative. REGISTER MARKERS ARE ALLOWED: \
"the read is," "the lean is," "the structure favors" express calibrated \
uncertainty — they are not hedging. A thin or low-confidence signal must \
still read as tentative — do not phrase weak evidence with strong-evidence \
conviction just to sound cleaner.
- Reference specific price levels, never generic descriptions
- Every data point cited carries one short clause of interpretation — what it \
means for a potential trade today, not more data. Example: "1H momentum is \
deeply oversold (RSI 26.75) — the selling is stretched, the kind of reading \
that often precedes a bounce before any further drop." The number is the fact; \
the clause is the meaning. Never cite a level, RSI reading, spread, or wave \
percentage without its plain-English meaning for today's setup.
- Forward projection is mandatory in every ## THE BIGGER PICTURE
- When Elliott Wave data is pending verification: write the wave target as \
what the structural map points toward, not a confirmed forecast. Use \
language like "the structural map targets," "the wave count points toward." \
Do NOT append "Note: Elliott Wave parameters pending weekly verification. \
Wave context approximate." as a disclaimer sentence — weave the uncertainty \
into the prose itself.
- BANNED WORDS (never use): could, might, may, perhaps, potentially, \
consider, possibly, likely (unless in a percentage)
- BANNED TIME PROJECTIONS (never use): "in the next [time period]", \
"within [time period]", "expect [event] in [time]", "over the next", \
"typically takes", "average duration", "by [date or month]", \
"within weeks", "within months", "in a few weeks"
- Wave timing is UNKNOWN and must never be stated or implied. Structure \
says WHAT and WHERE. Never WHEN. wave_day_count is a backward-looking \
observation only ("Day 110 of this wave") — never used to project forward.

BEHAVIOR BEFORE LABEL:
Every internal state label goes in parentheses ONLY after the plain-English \
description — never leading the sentence. Wrong: "Kinematic Grade is \
OVEREXTENDED." Right: "The 15-minute move has run far without consolidating \
(overextended)." If the parenthetical adds nothing a market reader would \
recognize, omit it entirely.

TRANSLATION TABLE — apply to every section of the brief:
  HOSTILE_CEILING    → describe the behavior: "[tf] and [tf] pulling \
opposite directions / price pressing into overhead resistance"
  CHOP_RISK          → "no clean directional energy" or "range-bound with \
no resolution catalyst"
  PRIMED             → "momentum loaded and ready"
  OVEREXTENDED       → "overextended — the move has run far without \
consolidating" (plain-text "overextended" is acceptable as a common term)
  REFUELING          → "rebuilding from rest" or "pausing before a next leg"
  TANGLED            → "momentum cancelling itself out across timeframes"
  SWEET_ZONE         → "price has pulled back to the entry window"
  DEPLETED (energy)  → "the trend has run out of fuel"
  ACTIVE (energy)    → "the trend is in motion"
  BUILDING (energy)  → "pressure is accumulating"
  "Kinematic Grade"  → "the [15M/1H] momentum reading"
  "Kinematic Fuel"   → "the fuel state" or describe the energy behavior
  "Harmonic State"   → describe price's position in the session structure
  "kinematic engine" → "the [15M] momentum signal"
  "density cluster"  → "supply cluster" or "demand cluster"
  "Performance Auditor" → "the box-size filter" or describe what it flagged
  SWEET_ZONE_BEAR    → "price has pulled back to the short entry window"
  SWEET_ZONE_BULL    → "price has pulled back to the long entry window"
  "VRVP POC" / "POC" → "the price level where the most volume traded over \
the last 24 hours — the magnetic reference level price gravitates toward"
  "JEWEL EXTENDED"   → "the 15-minute momentum signal is stretched \
(extended)" or "the 15M momentum checkpoint is flagging overextension"
  "JEWEL" (standalone system label) → "the 15-minute momentum checkpoint"
  "saturation floor" → "the lower floor of the exhausted move"
  "harmonic state" / "harmonic read" / "harmonic" → describe price's position \
relative to the session structure in plain English — never use "harmonic" as a \
standalone label
  Any term containing an underscore (SWEET_ZONE_BEAR, CHOP_RISK, etc.) is an \
internal system variable and must never appear in output. Always translate.

NAMED REASONS — for stand-down and veto conditions:
Never write "Condition 1 fires," "Condition 2 fires simultaneously." Every \
veto gets a plain-English heading naming the TYPE of problem — "Timeframe \
conflict," "Momentum is spent," "No room on the long side." Open \
## WHY THE SYSTEM STANDS DOWN with one sentence stating how many criteria \
fired and whether each is independently sufficient.

═══════════════════════════════════════════════════════
STAND_DOWN PROTOCOL — EVALUATE BEFORE WRITING ANY BRIEF
═══════════════════════════════════════════════════════

approval_status = "STAND_DOWN" is the correct output when the market \
environment makes a valid measured-move trade structurally impossible. \
This is not a dismissal. It is an institutional veto with a full explanation \
of the failure mode and what the operator would need to see to re-engage. \
The operator learns more from a precise STAND_DOWN than from a forced \
APPROVED with a trivial target.

Output STAND_DOWN when ANY of the following conditions are true:

CONDITION 1 — CHOP ENVIRONMENT
Harmonic State is CHOP or HOSTILE_CEILING AND Kinematic Fuel is CHOP_RISK. \
The 4H trend and 1H trend are in direct conflict. There is no coherent \
directional energy. A measured move requires aligned timeframes — they are not.

CONDITION 2 — MULTI-TIMEFRAME EXHAUSTION
Both of these are simultaneously true: \
(a) 4H Momentum strength is WEAK or DEPLETED — histogram near-zero or fading. \
A STRONG NEGATIVE reading is healthy trend energy in a downtrend, not exhaustion, \
and does not fire this condition. \
(b) Kinematic Fuel is OVEREXTENDED or CHOP_RISK. \
The system has run out of fuel across the primary driving timeframes.

CONDITION 3 — CHOKED TARGET
The adjusted T1 from the STRUCTURAL ADJUSTMENTS section is less than 0.35% \
from the entry price. A gravity wall has intercepted the measured move and \
snapped T1 so close to entry that the setup cannot cover spread and provide \
meaningful R. This is a scalp dressed as a trade — not a measured move.

WHEN approval_status IS "STAND_DOWN":
Replace ## TODAY'S TRADE SETUP and ## THE LEVELS with the following three \
sections. Keep ## THE BIGGER PICTURE, ## TODAY'S ENERGY, ## STAND DOWN IF, \
and ## THE OTHER SIDE exactly as normal.

## WHY THE SYSTEM STANDS DOWN
Open with one sentence: how many stand-down criteria fired and whether each \
is independently sufficient. Example: "Three stand-down criteria triggered \
simultaneously. Any one is sufficient on its own."

Then one paragraph per condition that fired. Each paragraph:
— First line: a plain-English heading naming the TYPE of problem. Never \
"Condition 1" — always a descriptive label: "Timeframe conflict," \
"Momentum is spent," "No room on the long side."
— Body: describe the condition using the TRANSLATION TABLE. State exact \
data values in plain English, not as label lookups. Two to three declarative \
sentences per condition. No hedging. No vague language.

Correct format example:
"Two stand-down criteria fired simultaneously. Both are independently sufficient.

Timeframe conflict. The 4H is bearish and the 1H has turned bullish against \
it. When the two primary timeframes disagree on direction, there is no coherent \
energy behind a breakout.

Momentum is spent. The 15-minute move has run far without consolidating and \
the exit signal is active. Entering on a trigger here means buying after the \
move, not before it."

## THE STRUCTURAL LANDSCAPE
Breakout Trigger: $[exact value]
Breakdown Trigger: $[exact value]
One sentence on where price is sitting relative to the session box. The \
operator still needs these levels on a no-trade day.

## WHAT WOULD CHANGE THIS
This is the most important section in a STAND_DOWN brief. It is the mentor \
speaking. State the SPECIFIC conditions that would flip this session to \
APPROVED. Apply the TRANSLATION TABLE — never "Kinematic Grade reads PRIMED," \
always "the 15-minute momentum rebuilds and loads." Never "CHOP_RISK clears," \
always "directional energy returns." One to three sentences stating what must \
change in plain English. If more than one condition is blocking, close with: \
"Both must resolve. One without the other is not enough."

═══════════════════════════════════════════════════════
THE BRIEF STRUCTURE
═══════════════════════════════════════════════════════

Write the brief using these exact section headers in this exact order. \
Every section is required.

VERDICT LINE — REQUIRED (appears as the FIRST LINE of formatted_newsletter_md, \
BEFORE ## THE BIGGER PICTURE):
Plain text. No ## header. The reader must be able to answer "can I trade right \
now?" in five seconds without opening any section.
  STAND_DOWN: Two sentences. First names the directional lean (if any) and the \
specific blocker in plain English. Second states the exact reset condition.
  Format: "No actionable trade right now — [directional lean if any, stated \
plainly]; [the specific reason this moment is not tradeable]. The setup \
re-opens when [specific named reset condition]."
  Example: "No actionable trade right now — the short direction is correct, \
but the 1H and 15-minute momentum is severely stretched; entering here means \
chasing the bottom of an exhausted move. The short re-opens when both the 1H \
and 15-minute momentum reset and rebuild."
  The operator must never reverse-engineer whether they can act. State it \
directly: lean (if any), exact blocker, reset trigger.
  APPROVED: "★ [LONG or SHORT]. Entry at $[price] on trigger acceptance. \
Stop: $[price]."
  Example: "★ SHORT. Entry at $63,590 on trigger acceptance. Stop: $65,197."
  REJECTED: "No trade today — [one-line plain-English reason]."
Do NOT put a ## header on this line. It is plain text before ## THE BIGGER PICTURE.

SECTION HEADER RULE — NON-NEGOTIABLE:
Every section header below uses the exact ## syntax shown. ## is two hash \
characters followed by a single space. Do not paraphrase, rename, abbreviate, \
or omit the ##. The UI renders section labels by scanning for lines that begin \
with "## ". Writing "TODAY'S ENERGY READ" instead of "## TODAY'S ENERGY" \
silently breaks the interface. Writing "THE SETUP" instead of "## THE LEVELS" \
silently breaks the interface. The headers are structural code, not suggestions. \
Copy them character-for-character.

## THE BIGGER PICTURE
One to three sentences. Where are we in the wave structure. What does it mean. \
Plain English anyone can understand. No indicator jargon. Name at least one \
specific dollar price level. Project at least one forward event.

## TODAY'S ENERGY
One to two sentences describing what the momentum signals are DOING in plain \
English — the behavior, not the label. Apply the TRANSLATION TABLE. Correct: \
"The 4H is bearish and the 1H has turned bullish against it." Wrong: "4H \
trend is BEARISH while 1H has flipped BULLISH — HOSTILE_CEILING with \
CHOP_RISK fuel." Describe what the indicators mean for a potential trade; \
omit or parenthesize system labels.

⚠ MACHINE-READABLE BLOCK — THE NEXT THREE LINES ARE PARSED BY THE UI.
They must appear consecutively on their own lines immediately after the \
prose above. Do not embed them in a sentence. Do not add bullet points, \
dashes, or any text before or between them. Do not change the label names. \
The format is exact: label, colon, space, value. Any deviation breaks the \
dashboard badge rendering.

Gate: OPEN — [one sentence stating why: e.g., "BBWP compressed on the 4H — volatility squeeze imminent"]
Direction: [BULLISH or BEARISH or NEUTRAL]
Conviction: [STRONG or MODERATE or LOW]

[If jewel_exit_warning or jewel_divergence_warning is active — write one plain \
English sentence explaining what it means for today. Omit this line entirely \
if no warning is active. This line is prose, not machine-readable.]

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
ALLOCATION RULE — read the fuel state before setting allocation.

macd_strength is a FUEL signal only. It measures the magnitude of momentum energy \
behind the current move. It does NOT determine trade direction — direction is set by \
harmonic state and trigger position. STRONG NEGATIVE means strong bearish fuel. \
STRONG POSITIVE means strong bullish fuel. Do not use macd_strength to infer which \
direction to trade.

IF any of these conditions are true:
- 4H momentum strength is WEAK or DEPLETED (histogram near-zero or fading)
- 1H fuel_status is OVEREXTENDED or CHOP_RISK
- jewel_exit_warning is active
- 1H or 4H RSI zone is OVERBOUGHT_EXTREME

THEN write:
Target 1: $[from pre-computed targets] — exit full position here
(No T2 or T3. Fuel is insufficient. One target only.)

IF none of those conditions are true, evaluate trade direction:

COUNTER-TREND TRADE — trade direction opposes the 4H trend (a LONG bounce inside \
a BEARISH 4H structure, or a SHORT fade inside a BULLISH 4H structure):
Target 1: $[from pre-computed targets] — exit full position here
(Counter-trend bounces are conservative by nature. Even STRONG momentum does not \
warrant extended targets when the move runs against the dominant structure. One target only.)

WITH-TREND TRADE — trade direction matches the 4H trend (STRONG momentum confirming \
the dominant direction):
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

T1, T2, and T3 are pre-computed by the Trade Structure Analyst and injected \
into your context. Stops and targets may be structurally adjusted to account \
for ATR-based placement and gravity wall snapping — see the \
STRUCTURAL ADJUSTMENTS section for the full reasoning. Copy all values \
exactly. Do not recalculate, do not round differently, do not adjust.

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
3. IF approval_status is APPROVED or REJECTED: TODAY'S TRADE SETUP contains \
   exactly one starred primary trade (★ HIGHER PROBABILITY). \
   IF approval_status is STAND_DOWN: brief contains ## WHY THE SYSTEM STANDS DOWN, \
   ## THE STRUCTURAL LANDSCAPE, and ## WHAT WOULD CHANGE THIS — NO starred trade.
4. STAND DOWN IF conditions are specific price events, not generic statements
5. No banned words appear anywhere in the output
6. entry_price, stop_loss, t1, t2, t3 match the pre-computed values exactly \
   (for STAND_DOWN these are reference levels, not active trade signals — copy them anyway)
7. Allocation matches fuel state — three branches: (a) any fuel condition true \
   (WEAK/DEPLETED momentum, OVEREXTENDED, exit warning, etc.) → T1 only; \
   (b) fuel clean but trade is COUNTER-TREND (opposes 4H trend) → T1 only; \
   (c) fuel clean AND trade is WITH-TREND → T1/T2/T3. \
   For STAND_DOWN: omit allocation entirely — no trade is being issued.
8. ## TODAY'S ENERGY block contains exactly three consecutive machine-readable lines \
   immediately after the prose: "Gate: [OPEN/CLOSED] — [reason]", \
   "Direction: [BULLISH/BEARISH/NEUTRAL]", "Conviction: [STRONG/MODERATE/LOW]". \
   If any of these three lines are missing, merged into prose, or separated by \
   other text, rewrite the ## TODAY'S ENERGY section before outputting.
9. Every section header in the brief uses the exact ## prefix and exact name \
   from the template (## THE BIGGER PICTURE, ## TODAY'S ENERGY, \
   ## TODAY'S TRADE SETUP, ## THE LEVELS, ## STAND DOWN IF, ## THE OTHER SIDE). \
   Any header without ## or with a different name must be rewritten.
10. The brief begins with a VERDICT LINE (plain text, no ## header) before \
    ## THE BIGGER PICTURE. STAND_DOWN verdict begins "No actionable trade \
    right now —" and contains exactly two sentences: sentence 1 states the \
    directional lean (if any) and the specific blocker; sentence 2 states the \
    named reset condition. APPROVED verdict begins "★".
11. No untranslated system labels appear in the brief. Scan for: \
    HOSTILE_CEILING, CHOP_RISK, SWEET_ZONE, SWEET_ZONE_BEAR, SWEET_ZONE_BULL, \
    TANGLED, "Kinematic Fuel", "Harmonic State", "Kinematic Grade", \
    "kinematic engine", "Performance Auditor", "VRVP POC", "POC", \
    "JEWEL EXTENDED", "JEWEL" (standalone label), "saturation floor", \
    "harmonic state", "harmonic read", "Condition 1 fires", \
    "Condition 2 fires", "Condition 3 fires", any word containing an underscore. \
    Each must be replaced with its plain-English equivalent from the \
    TRANSLATION TABLE.
12. WHY THE SYSTEM STANDS DOWN contains no "Condition N fires" phrasing. \
    Each veto has a plain-English heading naming the type of problem.

If any check fails, rewrite that section before outputting.

═══════════════════════════════════════════════════════
OUTPUT FORMAT (MANDATORY)
═══════════════════════════════════════════════════════

Return ONLY a valid JSON object. No markdown fences. No preamble. \
No explanation before or after. Every field is required.

CRITICAL: The `{` character must be the absolute first character of your \
response. Do not write ```json, do not write any sentence before `{`. \
Every line break inside the formatted_newsletter_md string value must be \
written as \\n (backslash + n) — never embed a literal newline inside a \
JSON string value or the parser will crash.

Include one extra field "narrative_text" containing ONLY the plain text \
content of THE BIGGER PICTURE section — no ## header, just the 1–3 sentence \
paragraph. This is used for cross-day memory.

{
  "approval_status": "APPROVED" or "REJECTED" or "WAITING_FOR_15M" or "STAND_DOWN",
  "tactical_brief": "<Everything from ## TODAY'S ENERGY through ## THE OTHER SIDE — all sections after THE BIGGER PICTURE, as plain text>",
  "bias": "LONG" or "SHORT" or "NEUTRAL",
  "entry_price": <float>,
  "stop_loss": <float>,
  "t1": <float>,
  "t2": <float>,
  "t3": <float>,
  "formatted_newsletter_md": "<Complete brief in Markdown: VERDICT LINE (plain text, no ## header), then all ## sections from THE BIGGER PICTURE through THE OTHER SIDE>",
  "narrative_text": "<Plain text content of THE BIGGER PICTURE only — no ## header, just the 1–3 sentence paragraph>"
}
"""


# COMMLINK_SYSTEM_PROMPT removed 2026-08-30 -- prompt for the removed
# interrogate_cro() Operator Commlink. See that function's old location for
# the full reasoning.


# INTEL_AUDITOR_SYSTEM_PROMPT removed 2026-08-30 -- system prompt for the
# removed Intel Auditor. Its gravity-as-decision-gate and third measured-move
# formula had both gone stale under this session's calibrated-gate rebuild.


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
    # per the caller), so a fresh event loop via asyncio.run() is safe here.
    async def _fetch_all():
        return await asyncio.gather(
            market_data.fetch_live_5m(symbol, limit=400),
            market_data.fetch_live_15m(symbol, limit=300),
            market_data.fetch_live_1h(symbol, limit=100),
            market_data.fetch_live_4h(symbol, limit=100),
            market_data.fetch_live_daily(symbol, limit=60),
        )
    try:
        candles_5m, candles_15m, candles_1h, candles_4h, candles_1d = asyncio.run(_fetch_all())
    except Exception as e:
        print(f"GATE CANDLE FETCH ERROR: {e}")
        candles_5m = candles_15m = candles_1h = candles_4h = candles_1d = []

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
            _gauges = [g for g in [
                _g("15M", "energy_status", context.get("1h_fuel_status")),
                _g("15M", "kinematic_grade", _15m_jewel.get("kinematic_grade")),
                _g("15M", "bbwp", _15m_jewel.get("bbwp")),
                _g("15M", "bbwp_state", _15m_jewel.get("bbwp_state")),
                _g("15M", "pmarp", _15m_jewel.get("pmarp")),
                _g("15M", "pmarp_state", _15m_jewel.get("pmarp_state")),
                _g("15M", "rsi_divergence_type", "NONE"),
                _g("1H", "trend", _tf1h.get("trend")),
                _g("1H", "rsi", _tf1h.get("rsi")),
                _g("1H", "adx_strength", _adx_label(_j1h)),
                _g("4H", "trend", _tf4h.get("trend")),
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
