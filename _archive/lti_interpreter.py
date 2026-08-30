# lti_interpreter.py
# ==============================================================================
# KABRODA LTI INTERPRETER (2026-07-07)
# Bucket B — reads a monthly KULTI confluence audit (from lti_engine.py) and
# produces a qualitative synthesis: the confluence count alongside Kabroda's
# own Elliott Wave macro position and gravity cross-confirmation. Describes,
# never decides -- matches this module's own source framework's stated
# boundary ("the framework flags WHEN, you decide HOW MUCH").
#
# Mirrors gravity_interpreter.py's exact shape: a context builder + a public
# run_*_interpretation() entry point calling agent_core._call_from_spec(),
# fail-open (Optional[str]).
#
# Called by main.py's monthly LTI scheduler work function, which also owns
# the InterpreterLog write (same split as kabroda_mas_flow.py's
# _log_interpreter() calling gravity_interpreter -- the interpreter module
# itself never writes to the DB).
# ==============================================================================

from typing import Any, Dict, Optional

import agent_core


def _build_lti_context(audit: Dict[str, Any]) -> str:
    lines = [
        "=== KULTI MONTHLY CONFLUENCE AUDIT ===",
        f"Symbol: {audit.get('symbol')}",
        f"Current price: ${audit.get('current_price', 0):,.2f}",
        "",
        "=== RAW COMPONENT READINGS ===",
        f"BBWP: {audit.get('bbwp')} ({audit.get('bbwp_state')})",
        f"PMARP (200 SMA, macro mode): {audit.get('pmarp')} ({audit.get('pmarp_state')})",
        f"Weekly RSI: {audit.get('rsi_weekly')}",
        f"Percent below all-time high: {audit.get('pct_below_high')}% (informational only -- Crown's course gives no threshold for this component)",
        f"Krown Cross (21/55 EMA) state: {audit.get('krown_cross_state')}",
        f"Weekly EMA trend anchor: {audit.get('weekly_ema_trend')}",
        f"Hash Ribbons (Capriole methodology): {audit.get('hash_ribbons_state')}",
        f"Fear & Greed proxy: {audit.get('fear_greed_value')} ({audit.get('fear_greed_label')})",
        f"Low Month Days flag: {audit.get('low_month_day_flag')} (informational only -- no documented threshold)",
        f"Moon phase: {audit.get('moon_phase_label')} (informational only -- course frames this as uncorrelated noise, not signal)",
        "",
        "=== CONFLUENCE ENGINE OUTPUT ===",
        f"Accumulation-side signals firing: {audit.get('accumulation_signals_firing')}",
        f"Distribution-side signals firing: {audit.get('distribution_signals_firing')}",
        f"Conviction label (Crown's Conviction Scale): {audit.get('conviction_label')}",
        "",
        "=== KABRODA-NATIVE CONTEXT (not part of Crown's original course) ===",
        f"Current Elliott Wave position: {audit.get('wave_label_snapshot') or 'unavailable'}",
        f"Gravity cross-confirmation: {'CONFIRMED' if audit.get('gravity_cross_confirm') else 'not confirmed'}"
        + (f" -- nearest macro level ${audit.get('nearest_macro_level'):,.2f}" if audit.get('gravity_cross_confirm') else ""),
        "",
        "=== TASK ===",
        "Produce the graduated LTI characterization now. 5-7 sentences.",
        "Cover all four required areas: the confluence read (name which signals are firing),",
        "whether the picture is genuinely stacked or actually split, the macro wave context,",
        "and the gravity cross-confirmation. Describe. Do not decide.",
    ]
    return "\n".join(lines)


def run_lti_interpretation(audit: Dict[str, Any]) -> Optional[str]:
    """
    LTI Interpreter pipeline. Called by main.py's monthly LTI scheduler work
    function after lti_engine.run_lti_audit() returns.

    Returns a graduated plain-English characterization string on success.
    Returns None on ANY failure (budget block, API error, empty response).
    Never raises -- caller always gets a usable result.
    """
    try:
        context_text = _build_lti_context(audit)

        response = agent_core._call_from_spec(
            agent_name="lti_interpreter",
            context_text=context_text,
            triggered_by="monthly_lti_audit",
        )

        result = response.strip()
        if not result:
            print("[LTI INTERPRETER] Empty response — raw component readout in use")
            return None

        print(f"[LTI INTERPRETER] OK — {len(result)} chars")
        return result

    except Exception as e:
        print(f"[LTI INTERPRETER] Failed — raw component readout in use: {e}")
        return None
