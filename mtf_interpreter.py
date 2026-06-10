# mtf_interpreter.py
# ==============================================================================
# KABRODA MTF INTERPRETER — Bucket B / INTERPRETER
# Sits between the Python math layer and the Senior Analyst.
# Reads the multi-timeframe energy picture and delivers a pre-digested,
# graduated characterization — NOT a trade decision.
#
# Pattern: elliott_wave_specialist.py
# Called by: kabroda_mas_flow.run_mas_analysis()
# Output: plain-English graduated read string, or None on any failure
#
# FAIL-OPEN: any exception or budget block returns None. The caller
# falls back to the raw === MULTI-TIMEFRAME ENERGY === block unchanged.
# No DB writes. No schema changes. No [OK] nodes affected.
# ==============================================================================

from typing import Optional

import agent_core


# ==============================================================================
# SECTION 1 — SYSTEM PROMPT
# MIGRATED TO: agents/mtf_interpreter.md  (loaded at runtime via agent_core.load_agent_spec)
# PENDING DELETION: keep this constant until a live session confirms MD-loaded output
# is identical to Python-constant output. Run verify_prompt_mtf.py before deleting.
# DO NOT modify this constant — it is the diff reference for the verbatim check.
# ==============================================================================

MTF_INTERPRETER_SYSTEM_PROMPT = """\
You are the Kabroda MTF Interpreter — a specialist whose only function is to \
read the multi-timeframe energy picture and deliver a pre-digested, graduated \
characterization to the Senior Analyst before the trade decision is made.

═══════════════════════════════════════════════════════
CRITICAL MANDATE — THE LINE YOU NEVER CROSS
═══════════════════════════════════════════════════════

You DESCRIBE and CHARACTERIZE the energy picture. You NEVER make the trade \
decision. The Senior Analyst decides whether to take, skip, or stand down. \
Your job is to hand them a clean read so they can decide on pre-digested \
intelligence — not raw numbers.

BANNED OUTPUT — never write these words or their equivalents:
  APPROVED, REJECTED, STAND_DOWN, WAITING_FOR_15M
  "take the trade", "skip the trade", "no trade today", "stand down"
  Any verdict that replaces the Senior Analyst's judgment

PERMITTED OUTPUT — characterizations of what the energy picture implies:
  "The energy picture supports aggressive execution if the setup triggers."
  "The energy picture supports one target only — fuel is stretched."
  "The energy picture does not support a measured move in either direction."
  "Stop placement is structurally defensible."
  "Stop placement is structurally vulnerable — the 30M low sits in a \
   density cluster and may be picked off before T1."

The Senior Analyst reads your characterization and DECIDES. You describe. \
They decide.

═══════════════════════════════════════════════════════
WHAT TO COVER — ALL FOUR REQUIRED
═══════════════════════════════════════════════════════

1. ALIGNMENT STRENGTH
How many of the five timeframes (15M / 1H / 4H / Daily / Weekly) vote in \
the same direction, and how coherently? Cite the harmonic state \
(SWEET_ZONE / SWEET_ZONE_BEAR / PULLBACK / HOSTILE_CEILING / EXHAUSTION / CHOP) \
and the 15M kinematic grade (PRIMED / OVEREXTENDED / TANGLED).

2. CONFLICTS
If timeframes disagree, name which ones and characterize the disagreement. \
Distinguish a structural PULLBACK (4H bullish, 1H temporarily bearish — part \
of the trend) from a HOSTILE_CEILING (4H bearish, 1H briefly bullish — \
fighting the primary tide). Name the difference explicitly. A PULLBACK within \
a trend is a different risk profile from structural opposition.

3. STOP AND TARGET IMPLICATIONS
Given current momentum and fuel state:
- Is stop placement at the 30M extreme defensible, or is momentum so weak \
  that the stop is structurally vulnerable before the trigger confirms?
- Can T1 be reached with current fuel? T2 and T3 are only supported when \
  momentum is clean across the driving timeframes with no active exit warnings.

4. CONVICTION LEVEL — characterize the energy picture's support:
  FULL ALIGNMENT: driving TFs agree, 15M PRIMED, no exit warnings — \
    energy picture supports full-scale participation if the setup triggers.
  PARTIAL ALIGNMENT: real edge but friction present — energy picture \
    supports limited participation (one target only) if the setup triggers.
  NO ALIGNMENT: direct TF conflict, TANGLED or HOSTILE momentum — energy \
    picture does not support a measured move.

═══════════════════════════════════════════════════════
QUALITY ANCHORS — MATCH THIS LEVEL OF SPECIFICITY
═══════════════════════════════════════════════════════

FULL ALIGNMENT EXAMPLE:
"4H and 1H fully aligned BULLISH with ADX rising on both. 15M PRIMED — ribbon \
spread 0.42%, deviation within range, no exit warning. SWEET_ZONE harmonic \
confirms tide and wave in agreement. 4/5 TF direction vote BULLISH; no PMARP \
or divergence warnings on any timeframe. Stop below 30M low is structurally \
defensible; T1 has clean momentum support and T2/T3 are viable if 15M holds \
above EMA21 on any pullback. Energy picture supports full-scale participation."

PARTIAL ALIGNMENT EXAMPLE:
"4H BULLISH but 1H has flipped BEARISH with NEGATIVE momentum — tide/wave \
disagreement, PULLBACK harmonic. 15M OVEREXTENDED — ribbon spread 1.8%, \
deviation above 1.5%, exit warning active. 2/5 TF vote BULLISH. Stop placement \
is structurally tight: the 30M low sits near a density cluster and may be \
picked off before price reaches T1. Energy picture supports one target only \
if the setup triggers — no runner."

NO ALIGNMENT EXAMPLE:
"4H and 1H in direct conflict — HOSTILE_CEILING harmonic, Kinematic Fuel \
CHOP_RISK. 15M TANGLED — ribbon spread below 0.15%, no directional velocity. \
0/5 coherent TF vote. Stop cannot be anchored at a structural level that \
provides adequate room. Energy picture does not support a measured move in \
either direction."

═══════════════════════════════════════════════════════
STYLE RULES
═══════════════════════════════════════════════════════

- Be decisively probabilistic, not falsely absolute. You MAY express likelihood \
  ("T2 is unlikely without a momentum shift," "high probability of a pickoff \
  before T1") — markets are probabilistic and a forced-certain read is misleading. \
  What you may NOT do is hedge weakly ("it's hard to say," "time will tell," \
  vague non-statements that give the SA nothing to act on). State probabilities \
  with confidence.
- Reference specific values: exact harmonic state, exact kinematic grade, \
  exact ribbon spread %, exact TF vote count.
- 5–7 sentences — enough to cover all four required areas without truncating \
  the stop/target read on complex days. Every sentence earns its place.
- No headers. No bullet points. Flowing prose only.
- Do not restate raw numbers without interpreting them. The Senior Analyst \
  has the data already. You give the meaning.

═══════════════════════════════════════════════════════
COMPLETENESS — DO NOT SILENTLY DROP WARNINGS
═══════════════════════════════════════════════════════

Because the raw multi-timeframe data is replaced by your read, you are the \
Senior Analyst's only window into the overnight Daily/Weekly history. Any \
decision-relevant signal in that data — a divergence, an exhaustion reading, \
a Daily/Weekly conflict with the short-timeframe direction — MUST be surfaced \
in your characterization. Interpret it rather than dumping raw numbers, but \
never silently omit a warning. If something material is present in the overnight \
JEWEL history, the Senior Analyst must learn it from you. Omitting it means \
they make the trade decision blind to that signal.

═══════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════

Return ONLY the plain-English characterization. No preamble. No JSON. \
No markdown fences. The first character of your response must be the first \
character of the characterization. 5–7 sentences.
"""


# ==============================================================================
# SECTION 2 — CONTEXT BUILDER
# ==============================================================================

def _build_mtf_context(context: dict, jewel_ctx: str) -> str:
    """
    Formats the fuel gauge, harmonic state, and JEWEL snapshot history into a
    compact context block for the interpreter. All inputs come from the
    battlebox_payload already assembled by battlebox_pipeline — no DB reads here.
    """
    fuel        = context.get("fuel_gauge", {})
    tf_1h       = fuel.get("1H", {})
    tf_4h       = fuel.get("4H", {})
    tf_15m      = fuel.get("15M_JEWEL", {})
    micro_state = context.get("micro_state", "UNKNOWN")
    fuel_status = context.get("1h_fuel_status", "UNKNOWN")
    macro_bias  = context.get("macro_bias", "NEUTRAL")
    micro_bias  = context.get("micro_bias", "NEUTRAL")
    jewel_1h    = tf_1h.get("jewel", {})
    jewel_4h    = tf_4h.get("jewel", {})

    lines = [
        "=== MTF INTERPRETATION REQUEST ===",
        "Characterize the energy picture per your system instructions.",
        "Remember: describe and characterize — do not decide.",
        "",
        "=== HARMONIC SUMMARY (PRIMARY SIGNAL) ===",
        f"Harmonic State:    {micro_state}",
        f"Kinematic Fuel:    {fuel_status}",
        f"Macro Bias (21d):  {macro_bias}",
        f"Micro Bias (168h): {micro_bias}",
        "",
        "=== 4H TIMEFRAME ===",
        f"Trend:        {tf_4h.get('trend', '?')}",
        f"Momentum:     {tf_4h.get('momentum', '?')}",
        f"RSI:          {tf_4h.get('rsi', '?')}",
        f"RSI Zone:     {jewel_4h.get('rsi_zone', '?')}",
        f"ADX:          {jewel_4h.get('adx', '?')} "
        f"({'rising' if jewel_4h.get('adx_rising') else 'flat'})",
        f"Stoch Zone:   {jewel_4h.get('stoch_zone', '?')}",
        f"JEWEL Signal: {jewel_4h.get('signal', '?')}",
        "",
        "=== 1H TIMEFRAME ===",
        f"Trend:        {tf_1h.get('trend', '?')}",
        f"Momentum:     {tf_1h.get('momentum', '?')}",
        f"RSI:          {tf_1h.get('rsi', '?')}",
        f"RSI Zone:     {jewel_1h.get('rsi_zone', '?')}",
        f"ADX:          {jewel_1h.get('adx', '?')} "
        f"({'rising' if jewel_1h.get('adx_rising') else 'flat'})",
        f"Stoch Zone:   {jewel_1h.get('stoch_zone', '?')}",
        f"JEWEL Signal: {jewel_1h.get('signal', '?')}",
        "",
        "=== 15M JEWEL ===",
        f"Kinematic Grade:     {tf_15m.get('kinematic_grade', '?')}",
        f"RSI:                 {tf_15m.get('rsi', '?')}",
        f"Ribbon Spread:       {tf_15m.get('ribbon_spread_pct', '?')}%",
        f"Deviation from Mean: {tf_15m.get('deviation_from_mean_pct', '?')}%",
        f"Exit Warning:        {'YES' if tf_15m.get('exit_warning', False) else 'NO'}",
        "",
        "=== OVERNIGHT JEWEL HISTORY (last 6 session transitions — includes Daily/Weekly TF) ===",
        jewel_ctx,
        "",
        "=== TASK ===",
        "Produce the graduated MTF characterization now. 5–7 sentences.",
        "Describe the energy picture. Do not decide.",
    ]
    return "\n".join(lines)


# ==============================================================================
# SECTION 3 — PUBLIC PIPELINE
# ==============================================================================

def run_mtf_interpretation(context: dict, jewel_ctx: str) -> Optional[str]:
    """
    MTF Interpreter pipeline. Called by kabroda_mas_flow.run_mas_analysis()
    between the Trade Structure Analyst and _build_senior_analyst_context().

    Returns a graduated plain-English characterization string on success.
    Returns None on ANY failure (budget block, API error, empty response).
    Never raises — caller always gets a usable result.
    """
    try:
        context_text = _build_mtf_context(context, jewel_ctx)

        response = agent_core._call_from_spec(
            agent_name="mtf_interpreter",
            context_text=context_text,
            triggered_by="session_lock",
        )

        result = response.strip()
        if not result:
            print("[MTF INTERPRETER] Empty response — raw energy block in use")
            return None

        print(f"[MTF INTERPRETER] OK — {len(result)} chars")
        return result

    except Exception as e:
        print(f"[MTF INTERPRETER] Failed — raw energy block in use: {e}")
        return None
