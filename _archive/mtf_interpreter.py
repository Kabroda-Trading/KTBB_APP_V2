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
# SECTION 1 — CONTEXT BUILDER
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
# SECTION 2 — PUBLIC PIPELINE
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
