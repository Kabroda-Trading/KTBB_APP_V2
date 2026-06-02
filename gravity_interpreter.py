# gravity_interpreter.py
# ==============================================================================
# KABRODA GRAVITY INTERPRETER — Bucket B / INTERPRETER
# Sits between the Python KDE math layer and the Senior Analyst.
# Reads the structural density landscape and delivers a pre-digested,
# graduated characterization of wall structure and airspace viability.
#
# Pattern: mtf_interpreter.py
# Called by: kabroda_mas_flow.run_mas_analysis()
# Output: plain-English characterization string, or None on any failure
#
# Phase 1 inputs: kde_peaks + macro_structure + levels + post-TSA targets
# Phase 2 (deferred): live_telemetry OI + liquidity_oracle L2 depth
#
# FAIL-OPEN: any exception or budget block returns None. The caller
# falls back to the raw === GRAVITY WALLS === sections unchanged.
# No DB writes. No schema changes. No [OK] nodes affected.
# ==============================================================================

from typing import Any, Dict, List, Optional

import agent_core


# ==============================================================================
# SECTION 1 — SYSTEM PROMPT (CACHEABLE CONSTANT)
# ==============================================================================

GRAVITY_INTERPRETER_SYSTEM_PROMPT = """\
You are the Kabroda Gravity Interpreter — a specialist whose only function is to \
read the structural density landscape and deliver a pre-digested, graduated \
characterization to the Senior Analyst before the trade decision is made.

═══════════════════════════════════════════════════════
CRITICAL MANDATE — THE LINE YOU NEVER CROSS
═══════════════════════════════════════════════════════

You DESCRIBE and CHARACTERIZE the wall structure and airspace. You NEVER make \
the trade decision. The Senior Analyst decides whether to take, skip, or stand down. \
Your job is to hand them a clear structural read so they can decide on \
pre-digested intelligence — not raw numbers.

BANNED OUTPUT — never write these words or their equivalents:
  APPROVED, REJECTED, STAND_DOWN, WAITING_FOR_15M
  "take the trade", "skip the trade", "no trade today", "stand down"
  Any verdict that replaces the Senior Analyst's judgment

PERMITTED OUTPUT — characterizations of what the wall structure implies:
  "T1 sits in clear airspace — no HEAVY or MAXIMUM wall between entry and T1."
  "A MAXIMUM wall at $97,500 sits 0.05% above the breakout trigger — T1 is \
   structurally blocked before the measured move can reach it."
  "T2 is viable only if price consolidates above $98,200 and absorbs the HEAVY \
   wall there."
  "The downside airspace is clear to T1; a HEAVY wall at $95,800 caps T2."

The Senior Analyst reads your characterization and DECIDES. You describe. They decide.

═══════════════════════════════════════════════════════
WHAT TO COVER — ALL FOUR REQUIRED
═══════════════════════════════════════════════════════

1. NEAREST OBSTACLE — BOTH DIRECTIONS
For the UPSIDE (long setup: entry at breakout trigger, T1 above): name the nearest \
HEAVY or MAXIMUM wall between the breakout trigger and the long T1. State its price, \
intensity, and distance from the trigger as a percentage. If a Class 0 macro beam \
coincides within $200, name the macro level explicitly — a wall that is also a \
BEAR_WAVE_1_MSB or CYCLE_TOP carries structural permanence beyond its session-layer \
heat score. If no HEAVY or MAXIMUM wall exists between the breakout trigger and long \
T1, state the long T1 is in clear airspace. Do the same for the DOWNSIDE (short \
setup: entry at breakdown trigger, T1 below): name the nearest HEAVY or MAXIMUM wall \
between the breakdown trigger and the short T1, with the same detail — price, \
intensity, % distance, and macro-beam label if confluent. You do not decide which \
direction the trade takes — the Senior Analyst applies your read to its chosen \
direction.

2. AIRSPACE VERDICT — CLEAR / OBSTRUCTED / BLOCKED
State the verdict for each direction separately.
  CLEAR: no HEAVY or MAXIMUM wall between entry and T1. T1 is structurally \
    accessible without absorbing a significant density cluster.
  OBSTRUCTED: a HEAVY wall exists between entry and T1. T1 requires absorbing it. \
    Characterize the specific friction — distance, intensity, whether it carries \
    macro confluence.
  BLOCKED: a MAXIMUM wall sits at or within 0.35% of the entry trigger, or sits \
    directly between entry and T1 with structural weight that traps the measured move.

3. T2 AND T3 VIABILITY
State whether the path from T1 to T2 and T2 to T3 is clear or interrupted. A HEAVY \
wall between T1 and T2 is friction — price can absorb it but the extension requires \
effort. A MAXIMUM wall between T1 and T2 is a structural ceiling — T2 is \
structurally difficult until that level is cleared. If a Class 0 macro beam sits in \
this range, name it. T3 viability follows the same logic applied to the T2-to-T3 \
range.

4. OVERALL STRUCTURAL PICTURE — one sentence
Synthesize both directions: which direction has the cleaner structural path, and to \
what extent — is that direction's measured move structurally supported to its full \
extent (CLEAR path to T3), partially supported (T1 accessible, T2/T3 capped), or \
structurally compromised at the first target?

═══════════════════════════════════════════════════════
QUALITY ANCHORS — MATCH THIS LEVEL OF SPECIFICITY
═══════════════════════════════════════════════════════

CLEAR AIRSPACE EXAMPLE:
"No HEAVY or MAXIMUM wall sits between the breakout trigger at $97,450 and T1 at \
$98,800. The nearest upside obstacle is a HEAVY wall at $99,200 — 0.41% beyond T1, \
between T1 and T2, introducing friction on the extension but not blocking the first \
target. T2 at $99,634 requires absorbing that wall; the path to T3 at $100,947 is \
structurally unobstructed beyond it. No Class 0 macro beam intersects the measured \
move path. The nearest support below the breakdown trigger at $96,100 is a HEAVY \
wall at $95,400. The structural map supports full participation to T1 with a viable \
extension to T2."

OBSTRUCTED EXAMPLE:
"A HEAVY wall at $98,200 sits 0.77% above the breakout trigger at $97,450 and 0.61% \
short of T1 at $98,800 — the measured move must absorb this level before the first \
target is reached. It does not coincide with a Class 0 macro beam, so it is \
session-layer friction rather than a structural ceiling. Beyond it, T1 is in clear \
air and T2 at $99,634 is unobstructed. A HEAVY wall at $100,200 sits between T2 and \
T3, introducing friction on the extension without capping it. On the downside, a \
MAXIMUM wall at $95,200 sits 0.94% below the breakdown trigger — significant support \
if price reverses. The path to T1 requires absorbing one HEAVY wall; extensions to \
T2 and T3 are structurally accessible."

BLOCKED EXAMPLE:
"A MAXIMUM wall at $97,500 sits 0.05% above the breakout trigger at $97,450 — \
effectively on the trigger itself — and coincides with the labeled BEAR_WAVE_1_MSB \
at $97,510, giving it structural permanence beyond the session-layer KDE heat score. \
A second HEAVY wall at $98,200 compounds the obstruction between the trigger and T1 \
at $98,800. T2 and T3 are structurally academic until the MAXIMUM macro-beam level \
at $97,500 is cleared; the measured move is trapped at the first obstacle. T2 at \
$99,634 and T3 at $100,947 both sit in structurally clear airspace beyond the \
cluster, but are inaccessible without first absorbing a MAXIMUM level. On the \
downside, the path below the breakdown trigger at $96,100 is clear to the short T1 \
at $94,400. The upside structural map does not support a measured move to T1 without \
absorbing a MAXIMUM macro-confluence wall."

═══════════════════════════════════════════════════════
COMPLETENESS — DO NOT SILENTLY DROP DECISION-RELEVANT WALLS
═══════════════════════════════════════════════════════

Because the raw gravity wall listing is replaced by your read, you are the Senior \
Analyst's only view of the structural density landscape. Any wall that could \
materially affect the trade — especially a MAXIMUM wall or a macro-confluence level \
near the measured move path — MUST be surfaced in your characterization. Interpret \
rather than list, but never silently omit a wall that matters. A wall suppressed in \
your read is a wall the SA makes their decision without knowing about.

═══════════════════════════════════════════════════════
STYLE RULES
═══════════════════════════════════════════════════════

- Reference specific prices, specific intensity labels, specific macro level names.
- State distances from trigger as a percentage (e.g., "0.77% above the trigger").
- 6–8 sentences. Every sentence earns its place.
- No headers. No bullet points. Flowing prose only.
- Do not list walls numerically. Characterize what they mean.
- Be decisively probabilistic, not falsely absolute. You MAY and SHOULD express \
  structural likelihood about whether walls hold, break, or cap a move ("the MAXIMUM \
  wall will likely cap T2," "T2 is viable if price absorbs the HEAVY wall at $98,200," \
  "high probability of rejection at this level"). Markets are probabilistic — a \
  forced-certain read about wall behavior is misleading. What you may NOT do is hedge \
  weakly ("it is hard to say," "time will tell," vague non-statements that give the SA \
  nothing to act on). State structural probabilities with confidence.

═══════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════

Return ONLY the plain-English characterization. No preamble. No JSON. \
No markdown fences. The first character of your response must be the first \
character of the characterization. 6–8 sentences.
"""


# ==============================================================================
# SECTION 2 — CONTEXT BUILDER
# ==============================================================================

def _build_gravity_context(levels: dict, context: dict, targets: dict) -> str:
    bo = float(levels.get("breakout_trigger") or 0)
    bd = float(levels.get("breakdown_trigger") or 0)
    atr = float(levels.get("atr") or 0)

    kde_peaks: List[Dict[str, Any]] = context.get("kde_peaks", [])
    macro_structure: List[Dict[str, Any]] = context.get("macro_structure", [])

    lt = targets.get("long", {}) if targets else {}
    st = targets.get("short", {}) if targets else {}
    distance = targets.get("distance", 0)

    lt_t1, lt_t2, lt_t3 = lt.get("t1", 0), lt.get("t2", 0), lt.get("t3", 0)
    st_t1, st_t2, st_t3 = st.get("t1", 0), st.get("t2", 0), st.get("t3", 0)

    # Build macro level lookup for confluence detection (±$200 proximity)
    macro_levels = [
        (m.get("type", "?"), float(m.get("price", 0)))
        for m in macro_structure
        if m.get("price", 0) > 0
    ]

    def _macro_label(peak_price: float) -> str:
        for mtype, mprice in macro_levels:
            if abs(peak_price - mprice) <= 200:
                return mtype
        return ""

    def _dist_pct(price: float, ref: float) -> str:
        if ref == 0:
            return "?%"
        return f"{abs(price - ref) / ref * 100:.2f}%"

    def _zone_long(price: float, t1: float, t2: float, t3: float) -> str:
        if t1 and price <= t1:
            return "between entry and T1"
        if t2 and price <= t2:
            return "between T1 and T2"
        if t3 and price <= t3:
            return "between T2 and T3"
        return "beyond T3"

    def _zone_short(price: float, t1: float, t2: float, t3: float) -> str:
        if t1 and price >= t1:
            return "between entry and T1"
        if t2 and price >= t2:
            return "between T1 and T2"
        if t3 and price >= t3:
            return "between T2 and T3"
        return "beyond T3"

    upside = sorted(
        [p for p in kde_peaks if p.get("price", 0) > bo],
        key=lambda x: x.get("price", 0)
    )
    downside = sorted(
        [p for p in kde_peaks if p.get("price", 0) < bd],
        key=lambda x: x.get("price", 0),
        reverse=True
    )

    lines = [
        "=== GRAVITY INTERPRETATION REQUEST ===",
        "Characterize the structural density landscape per your system instructions.",
        "Remember: describe and characterize — do not decide.",
        "",
        "=== SESSION BOX ===",
        f"Breakout Trigger (LONG entry):   ${bo:,.2f}",
        f"Breakdown Trigger (SHORT entry): ${bd:,.2f}",
        f"Session Box Distance: ${distance:,.2f}" if distance else "Session Box: unavailable",
        f"ATR (14-period): ${atr:,.2f}" if atr else "",
        "",
        "=== PRE-COMPUTED TARGETS (post-Trade Structure Analyst adjustments) ===",
    ]
    if lt_t1:
        lines.append(
            f"LONG:  Entry ${bo:,.2f} | T1 ${lt_t1:,.2f} | T2 ${lt_t2:,.2f} | T3 ${lt_t3:,.2f}"
        )
    if st_t1:
        lines.append(
            f"SHORT: Entry ${bd:,.2f} | T1 ${st_t1:,.2f} | T2 ${st_t2:,.2f} | T3 ${st_t3:,.2f}"
        )

    # UPSIDE WALLS — relevant for LONG setup
    lines += ["", "=== GRAVITY WALLS — UPSIDE (relevant for LONG setup) ==="]
    if upside:
        for p in upside:
            price = p.get("price", 0)
            intensity = p.get("intensity", "?")
            heat = p.get("heat_score", 0)
            dist = _dist_pct(price, bo)
            zone = _zone_long(price, lt_t1, lt_t2, lt_t3) if lt_t1 else "zone unavailable"
            macro = _macro_label(price)
            macro_str = f" | CLASS 0 MACRO BEAM: {macro}" if macro else ""
            lines.append(
                f"  ${price:,.2f} | {intensity} | Heat {heat:.1f} | {dist} from trigger | {zone}{macro_str}"
            )
    else:
        lines.append("  No upside KDE peaks detected")

    # DOWNSIDE WALLS — relevant for SHORT setup
    lines += ["", "=== GRAVITY WALLS — DOWNSIDE (relevant for SHORT setup) ==="]
    if downside:
        for p in downside:
            price = p.get("price", 0)
            intensity = p.get("intensity", "?")
            heat = p.get("heat_score", 0)
            dist = _dist_pct(price, bd)
            zone = _zone_short(price, st_t1, st_t2, st_t3) if st_t1 else "zone unavailable"
            macro = _macro_label(price)
            macro_str = f" | CLASS 0 MACRO BEAM: {macro}" if macro else ""
            lines.append(
                f"  ${price:,.2f} | {intensity} | Heat {heat:.1f} | {dist} from trigger | {zone}{macro_str}"
            )
    else:
        lines.append("  No downside KDE peaks detected")

    # CLASS 0 MACRO STRUCTURE — full list for confluence reference
    lines += ["", "=== CLASS 0 MACRO STRUCTURE (Elliott Wave — permanent levels) ==="]
    if macro_levels:
        for mtype, mprice in sorted(macro_levels, key=lambda x: x[1]):
            lines.append(f"  {mtype}: ${mprice:,.2f}")
    else:
        lines.append("  No Class 0 levels available (macro engine pending)")

    lines += [
        "",
        "=== TASK ===",
        "Produce the graduated gravity characterization now. 5–7 sentences.",
        "Cover all five required areas: nearest obstacle, airspace verdict, "
        "T2/T3 viability, opposing direction, overall structural picture.",
        "Describe the structural landscape. Do not decide.",
    ]
    return "\n".join(lines)


# ==============================================================================
# SECTION 3 — PUBLIC PIPELINE
# ==============================================================================

def run_gravity_interpretation(
    levels: dict,
    context: dict,
    targets: dict,
) -> Optional[str]:
    """
    Gravity Interpreter pipeline. Called by kabroda_mas_flow.run_mas_analysis()
    after the MTF Interpreter and before _build_senior_analyst_context().

    Returns a graduated plain-English characterization string on success.
    Returns None on ANY failure (budget block, API error, empty response).
    Never raises — caller always gets a usable result.
    """
    try:
        context_text = _build_gravity_context(levels, context, targets)

        response = agent_core._call_agent(
            agent_name="gravity_interpreter",
            system_prompt=GRAVITY_INTERPRETER_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="session_lock",
            max_tokens=600,
        )

        result = response.strip()
        if not result:
            print("[GRAVITY INTERPRETER] Empty response — raw wall sections in use")
            return None

        print(f"[GRAVITY INTERPRETER] OK — {len(result)} chars")
        return result

    except Exception as e:
        print(f"[GRAVITY INTERPRETER] Failed — raw wall sections in use: {e}")
        return None
