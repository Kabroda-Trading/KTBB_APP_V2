# elliott_wave_specialist.py
# ==============================================================================
# KABRODA ELLIOTT WAVE SPECIALIST — Phase 3B
# Reads Class 0 structural levels from gravity_memory. Determines the active
# Elliott Wave and writes verified wave parameters to macro_narrative_log.
#
# Cadence: Weekly — Sunday 23:00 UTC (scheduler in Phase 4)
#
# PUBLIC API:
#   run_elliott_wave_analysis(symbol, current_price, date_key)
#
# ARCHITECTURAL NOTE:
#   This agent does NOT re-derive wave math. kabroda_macro_engine.py writes
#   the structural labels. This Specialist interprets them — identifying which
#   labeled wave is currently active and what the structural conditions are.
#
# LABELING HONESTY:
#   Uses "labeled" not "confirmed" throughout. Structural states are:
#     IN_PROGRESS  — wave is active but ZigZag has not yet locked the closing pivot
#     CONFIRMED    — wave label exists AND price respects all structural rules
#     PENDING      — wave projected by structure but entry conditions not yet met
#     QUESTIONABLE — labels may be stale (e.g., price above CYCLE_TOP)
# ==============================================================================

import json
import re
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

import agent_core
from database import SessionLocal, GravityMemory, MacroNarrativeLog


# ==============================================================================
# SECTION 1 — OUTPUT SCHEMA
# ==============================================================================

class WaveAnalysis(BaseModel):
    wave_label: str = Field(
        description="Active wave label, e.g. 'BEAR_WAVE_4_BOUNCE', 'BEAR_WAVE_5', 'BULL_WAVE_5'"
    )
    wave_status: str = Field(
        description="IN_PROGRESS | CONFIRMED | PENDING | QUESTIONABLE"
    )
    wave_origin_price: float = Field(
        description="Price level where the active wave began (from labeled Class 0 levels)"
    )
    wave_target_price: float = Field(
        description="Structural price target for wave completion (a price condition, never a time estimate)"
    )
    invalidation_price: float = Field(
        description="Price level where the current wave count is invalidated per EWT rules"
    )
    confirmation_condition: str = Field(
        description="Specific price events that confirm wave completion and trigger the next wave"
    )
    wave_reasoning: str = Field(
        description="Full structural analysis citing specific EWT rules and labeled price levels"
    )


# ==============================================================================
# SECTION 2 — SYSTEM PROMPT (CACHEABLE CONSTANT)
# ==============================================================================

ELLIOTT_WAVE_SPECIALIST_SYSTEM_PROMPT = """\
You are the Kabroda Elliott Wave Specialist — a structural analyst whose sole \
function is to identify the active Elliott Wave from labeled Class 0 gravity \
levels and state the structural conditions governing it.

═══════════════════════════════════════════════════════
LABELING STANDARD (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════

The gravity_memory labels are algorithmically derived, not hand-confirmed by \
an expert. You MUST use the word "labeled" when describing wave structure. \
Never use "confirmed" when referring to the algorithmic labels.

CORRECT:   "Per labeled Class 0 structure, BTC is in BEAR_WAVE_4_BOUNCE."
INCORRECT: "BTC is confirmed in a Bear Wave 4 bounce."

wave_status categories:
  IN_PROGRESS  — wave is active but the closing ZigZag pivot is not yet locked \
                 (price has not reversed 20% from the wave extreme to confirm it)
  CONFIRMED    — label exists AND current price respects all structural EWT rules
  PENDING      — wave projected by the labeled structure but conditions not yet met
  QUESTIONABLE — labels are potentially stale (e.g., current price exceeds \
                 labeled CYCLE_TOP — flag this explicitly)

═══════════════════════════════════════════════════════
WAVE DETERMINATION RULES
═══════════════════════════════════════════════════════

Read the labeled Class 0 levels and current price provided in your context. \
Determine the active wave by price bracketing:

BEAR MARKET CONTEXT (price below CYCLE_TOP):
  Bouncing UP from BEAR_WAVE_3_LOW toward BEAR_WAVE_1_MSB:
    → wave_label = "BEAR_WAVE_4_BOUNCE" | wave_status = "IN_PROGRESS"
    → wave_origin_price = BEAR_WAVE_3_LOW price
    → wave_target_price = BEAR_WAVE_1_MSB price (structural ceiling)
    → invalidation_price = BEAR_WAVE_1_MSB price
    → EWT rule to cite: "Wave 4 cannot exceed the end of Wave 1. \
      Invalidation at $[BEAR_WAVE_1_MSB price]."

  Falling DOWN from BEAR_WAVE_1_MSB/BEAR_WAVE_4_BOUNCE toward new low:
    → wave_label = "BEAR_WAVE_5" | wave_status = "IN_PROGRESS"
    → wave_origin_price = BEAR_WAVE_4_BOUNCE price (if labeled) or BEAR_WAVE_1_MSB
    → wave_target_price = structural projection (typically BEAR_WAVE_3_LOW or lower)
    → EWT rule to cite: "Wave 5 must close below Wave 3 low. \
      Confirmation at weekly close below $[BEAR_WAVE_3_LOW price]."

  Price ABOVE BEAR_WAVE_1_MSB:
    → wave_status = "QUESTIONABLE" — bear sequence invalidated
    → wave_reasoning must state: "Bear Wave 4 cannot exceed end of Wave 1 \
      ($[BEAR_WAVE_1_MSB price]). Current price violates this rule. \
      Bear count requires re-examination."

BULL MARKET CONTEXT (price below CYCLE_TOP, above BULL_WAVE_4):
  Price between BULL_WAVE_4 and CYCLE_TOP with no confirmed bear peak:
    → wave_label = "BULL_WAVE_5" (the final impulse from W4 to CYCLE_TOP)
    → Apply applicable bull wave overlap rules

CYCLE_TOP EXCEEDED:
  If current price > labeled CYCLE_TOP:
    → wave_status = "QUESTIONABLE"
    → Flag: "Current price exceeds labeled CYCLE_TOP. Bear wave labels may \
      be from a prior cycle. Macro engine re-run required for current context."

═══════════════════════════════════════════════════════
EWT RULES TO CITE EXPLICITLY (MANDATORY)
═══════════════════════════════════════════════════════

You must cite the applicable inviolable Elliott Wave rule when stating \
invalidation conditions. Do not paraphrase — use this exact form:

Bear Wave 4:
  "Per Elliott Wave Theory, Wave 4 cannot exceed the end of Wave 1. \
  Invalidation at $[BEAR_WAVE_1_MSB price] (BEAR_WAVE_1_MSB)."

Bear Wave 5 confirmation:
  "Wave 5 must produce a weekly close below Wave 3 low. \
  Confirmation at weekly close below $[BEAR_WAVE_3_LOW price]."

Bull Wave 2:
  "Wave 2 cannot retrace 100% of Wave 1. \
  Invalidation at $[CYCLE_ORIGIN price] (CYCLE_ORIGIN)."

Bull Wave 4:
  "Wave 4 cannot enter Wave 1 territory. \
  Invalidation at $[BULL_WAVE_1 price] (BULL_WAVE_1)."

═══════════════════════════════════════════════════════
COMPLETION AND PROGRESS
═══════════════════════════════════════════════════════

You may state the current structural position as a percentage of the wave \
distance from origin to target. This is a backward-looking observation — \
it states WHERE price is in the structural range, not WHEN it will complete.

CORRECT:   "Current price is 81% of the structural distance from \
            BEAR_WAVE_3_LOW to BEAR_WAVE_1_MSB."
INCORRECT: "The wave is 81% complete and should finish in the coming weeks."

═══════════════════════════════════════════════════════
BANNED CONTENT (ABSOLUTE PROHIBITION)
═══════════════════════════════════════════════════════

TIME PROJECTIONS — never state or imply when a wave will complete:
  Never write: "in the next [time period]"
  Never write: "within [time period]"
  Never write: "over the next [time period]"
  Never write: "typically takes [time]"
  Never write: "average duration"
  Never write: "by [date or month]"
  Never write: "expect [event] in [time]"
  Never write: "should complete by"
  Never write: "within weeks" or "within months"

The structure says WHAT and WHERE. Never WHEN. Time to completion is unknown \
and must not be estimated, implied, or approximated.

HEDGING WORDS — never use: could, might, may, perhaps, potentially, consider, \
possibly, likely (unless in a percentage).

═══════════════════════════════════════════════════════
OUTPUT FORMAT (MANDATORY)
═══════════════════════════════════════════════════════

Return ONLY a valid JSON object. No markdown fences. No preamble. No text \
after the closing brace. All seven fields are required.

{
  "wave_label": "<active wave label>",
  "wave_status": "<IN_PROGRESS|CONFIRMED|PENDING|QUESTIONABLE>",
  "wave_origin_price": <float>,
  "wave_target_price": <float>,
  "invalidation_price": <float>,
  "confirmation_condition": "<specific price events that confirm wave completion and next wave trigger>",
  "wave_reasoning": "<full structural analysis with explicit EWT rule citations and labeled level prices>"
}
"""


# ==============================================================================
# SECTION 3 — CLASS 0 LEVEL READER
# ==============================================================================

def _read_class0_levels(symbol: str) -> list:
    """
    Reads all active Class 0 levels from gravity_memory for the symbol.
    Returns a list of dicts sorted by price ascending.
    Note: gravity_memory uses no-slash symbol format (BTCUSDT).
    """
    db_sym = symbol.replace("/", "")
    db = SessionLocal()
    try:
        rows = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == db_sym,
                GravityMemory.source == "MACRO_ENGINE_CLASS_0",
                GravityMemory.active == True,
            )
            .order_by(GravityMemory.price.asc())
            .all()
        )
        return [
            {
                "level_type": r.level_type,
                "price": r.price,
                "written": r.timestamp.strftime("%Y-%m-%d %H:%M UTC") if r.timestamp else "unknown",
            }
            for r in rows
        ]
    finally:
        db.close()


# ==============================================================================
# SECTION 4 — CONTEXT BUILDER
# ==============================================================================

def _build_wave_context(
    symbol: str,
    date_key: str,
    current_price: float,
    levels: list,
) -> str:
    below = [l for l in levels if l["price"] <= current_price]
    above = [l for l in levels if l["price"] > current_price]

    immediate_below = below[-1] if below else None
    immediate_above = above[0] if above else None
    cycle_top = next((l for l in levels if l["level_type"] == "CYCLE_TOP"), None)

    lines = [
        "=== ELLIOTT WAVE SPECIALIST CONTEXT ===",
        f"Symbol: {symbol} | Date: {date_key} | Current Price: ${current_price:,.2f}",
        f"Labels written: {levels[0]['written'] if levels else 'unknown'}",
        "",
        "=== CLASS 0 STRUCTURAL LEVELS (gravity_memory / MACRO_ENGINE_CLASS_0) ===",
        "(Listed price-ascending. Use 'labeled' not 'confirmed' in all output.)",
        "",
    ]

    for l in levels:
        is_bracket_below = immediate_below and l["level_type"] == immediate_below["level_type"]
        is_bracket_above = immediate_above and l["level_type"] == immediate_above["level_type"]
        tag = " ◄ PRICE SITTING ABOVE THIS" if is_bracket_below else (
              " ◄ PRICE SITTING BELOW THIS" if is_bracket_above else "")
        lines.append(f"  {l['level_type']:<25} ${l['price']:>12,.2f}{tag}")

    lines.append("")
    lines.append("=== PRICE BRACKET ANALYSIS ===")
    lines.append(f"Current price: ${current_price:,.2f}")

    if immediate_below:
        dist_below = current_price - immediate_below["price"]
        pct_below  = (dist_below / immediate_below["price"]) * 100
        lines.append(
            f"Level below:  {immediate_below['level_type']:<25} ${immediate_below['price']:>12,.2f} "
            f"(+${dist_below:,.2f} / +{pct_below:.2f}% above)"
        )
    else:
        lines.append("Level below:  NONE — price is below all labeled levels")

    if immediate_above:
        dist_above = immediate_above["price"] - current_price
        pct_above  = (dist_above / current_price) * 100
        lines.append(
            f"Level above:  {immediate_above['level_type']:<25} ${immediate_above['price']:>12,.2f} "
            f"(-${dist_above:,.2f} / -{pct_above:.2f}% below)"
        )
    else:
        lines.append("Level above:  NONE — price is above all labeled levels")

    if cycle_top and current_price > cycle_top["price"]:
        lines += [
            "",
            "WARNING: Current price ($" + f"{current_price:,.2f}) exceeds labeled CYCLE_TOP "
            f"(${cycle_top['price']:,.2f}). Bear wave labels are from the prior cycle.",
            "Set wave_status = QUESTIONABLE. Flag this in wave_reasoning.",
        ]

    lines += [
        "",
        "=== ZigZag NOTE ===",
        "The macro engine ZigZag algorithm records a wave extreme (PEAK or TROUGH) only when",
        "price subsequently reverses 20% from that extreme. If price is currently trending",
        "in one direction without a 20% reversal, the current wave extreme is NOT yet labeled.",
        "In this case, the wave is IN_PROGRESS — state this explicitly in wave_reasoning.",
        "",
        "=== INSTRUCTIONS ===",
        "Determine the active wave from the price bracket and labeled levels above.",
        "Cite the applicable EWT rules explicitly in wave_reasoning.",
        "State structural conditions (price events) only — no time projections.",
        "Return ONLY the JSON object.",
    ]

    return "\n".join(lines)


# ==============================================================================
# SECTION 5 — JSON PARSER
# ==============================================================================

def _parse_wave_response(text: str) -> WaveAnalysis:
    """Strips markdown fences, extracts JSON, validates against WaveAnalysis."""
    cleaned = text.strip()

    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

    brace_start = cleaned.find("{")
    brace_end   = cleaned.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start : brace_end + 1]

    data = json.loads(cleaned)
    return WaveAnalysis(**data)


# ==============================================================================
# SECTION 6 — MACRO NARRATIVE LOG WRITER
# ==============================================================================

def _write_wave_log(
    symbol: str,
    date_key: str,
    analysis: WaveAnalysis,
    current_price: float,
) -> None:
    """
    Writes Elliott Wave Specialist output to macro_narrative_log.
    Wave fields are populated. narrative_text / tactical_text / performance_note
    remain NULL — those are the Senior Analyst's and Performance Auditor's columns.
    completion_pct is computed in Python from (current_price - origin) / (target - origin).
    wave_day_count left NULL — origin date not tracked (see Phase 3B notes).
    """
    db = SessionLocal()
    try:
        origin = analysis.wave_origin_price
        target = analysis.wave_target_price
        if target != origin:
            completion = round((current_price - origin) / (target - origin) * 100, 1)
        else:
            completion = None

        row = MacroNarrativeLog(
            symbol=symbol,
            date_key=date_key,
            authored_by="elliott_wave_specialist",
            wave_label=analysis.wave_label,
            wave_status=analysis.wave_status,
            wave_origin_date=None,          # Not tracked — operator config if needed later
            wave_origin_price=analysis.wave_origin_price,
            wave_target_price=analysis.wave_target_price,
            wave_day_count=None,            # Not tracked
            completion_pct=completion,
            invalidation_price=analysis.invalidation_price,
            wave_reasoning=analysis.wave_reasoning,
            confirmation_condition=analysis.confirmation_condition,
            narrative_text=None,            # Senior Analyst writes this
            tactical_text=None,             # Senior Analyst writes this
            performance_note=None,          # Performance Auditor writes this
        )
        db.add(row)
        db.commit()
        print(
            f"|| WAVE LOG || {symbol} {date_key} | "
            f"{analysis.wave_label} | {analysis.wave_status} | "
            f"completion: {completion}%"
        )
    except Exception as e:
        print(f"WAVE LOG WRITE ERROR: {e}")
    finally:
        db.close()


# ==============================================================================
# SECTION 7 — PUBLIC PIPELINE
# ==============================================================================

def run_elliott_wave_analysis(
    symbol: str,
    current_price: float,
    date_key: str,
) -> Dict[str, Any]:
    """
    Elliott Wave Specialist pipeline. Called weekly by scheduler (Phase 4).
    Reads Class 0 levels from gravity_memory, determines active wave,
    writes wave parameters to macro_narrative_log.

    Args:
        symbol:        BTC/USDT format
        current_price: Live price fetched by caller before invoking
        date_key:      YYYY-MM-DD
    """
    print(f">>> ELLIOTT WAVE SPECIALIST: {symbol} @ ${current_price:,.2f} | {date_key}")

    # 1. Read structural levels
    levels = _read_class0_levels(symbol)
    if not levels:
        msg = f"No Class 0 levels in gravity_memory for {symbol}. Run macro engine first."
        print(f"    ERROR: {msg}")
        return {"status": "ERROR", "message": msg}

    print(f"    {len(levels)} Class 0 levels loaded.")

    # 2. Build context
    context_text = _build_wave_context(
        symbol=symbol,
        date_key=date_key,
        current_price=current_price,
        levels=levels,
    )

    # 3. Call agent through budget gate
    try:
        response_text = agent_core._call_agent(
            agent_name="elliott_wave_specialist",
            system_prompt=ELLIOTT_WAVE_SPECIALIST_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="weekly_scheduler",
            max_tokens=1024,
        )
    except RuntimeError as e:
        return {"status": "ERROR", "message": str(e)}
    except Exception as e:
        print(f"ELLIOTT WAVE SPECIALIST API ERROR: {e}")
        return {"status": "ERROR", "message": str(e)}

    # 4. Parse JSON response
    try:
        analysis = _parse_wave_response(response_text)
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Parse failed: {e}",
            "raw_response": response_text,
        }

    # 5. Write to macro_narrative_log
    _write_wave_log(symbol, date_key, analysis, current_price)

    return {"status": "SUCCESS", "wave": analysis.dict()}
