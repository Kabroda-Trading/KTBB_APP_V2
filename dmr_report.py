"""
DMR Report generator for KTBB – Trading Battle Box.

This is a rule-based “AI style” narrative engine that takes the raw levels
computed by `compute_dm_levels` plus the FRVP inputs and generates a
structured Daily Market Review.

The FastAPI app calls:

    generate_dmr_report(
        symbol=binance_symbol,
        date_str="YYYY-MM-DD",
        inputs=inp,        # FRVP + shelves + OR (from build_auto_inputs)
        levels=levels,     # daily_support, daily_resistance, triggers
        htf_shelves=...,   # 4H/1H shelves with strength
        range_30m=...,     # 30m OR
    )

and then expects:

    report["bias"]       -> "bullish" / "bearish" / "neutral"
    report["full_text"]  -> multi-section human-readable text

You can iterate on the heuristics here without touching main.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple


@dataclass
class BiasResult:
    label: str              # "bullish", "bearish", "neutral"
    confidence: str         # "high", "medium", "low"
    rationale: str          # one-line explanation


def _safe_get(d: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        v = d.get(key, default)
        return float(v)
    except Exception:
        return float(default)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _summarize_htf_shelves(htf_shelves: Dict[str, Any]) -> Tuple[str, str, float]:
    """
    Returns:
      - human text describing HTF structure band
      - which side is stronger: "support", "resistance", or "balanced"
      - strength spread between the dominant side and the other
    """
    support_list = htf_shelves.get("support") or []
    resist_list = htf_shelves.get("resistance") or []

    # Try to pick 4H and 1H shelves if metadata exists; otherwise just first two
    def pick_level(entries: List[Dict[str, Any]], tf_hint: str) -> Dict[str, Any] | None:
        if not entries:
            return None
        # prefer matching timeframe tag if present
        for e in entries:
            tf = str(e.get("tf", "")).lower()
            if tf_hint in tf:
                return e
        return entries[0]

    h4_sup = pick_level(support_list, "4")
    h1_sup = pick_level(support_list, "1")
    h4_res = pick_level(resist_list, "4")
    h1_res = pick_level(resist_list, "1")

    # Levels
    h4_sup_lvl = _safe_get(h4_sup or {}, "level", 0.0)
    h1_sup_lvl = _safe_get(h1_sup or {}, "level", 0.0)
    h4_res_lvl = _safe_get(h4_res or {}, "level", 0.0)
    h1_res_lvl = _safe_get(h1_res or {}, "level", 0.0)

    # Strengths (fallback to 0 if missing)
    h4_sup_str = _safe_get(h4_sup or {}, "strength", 0.0)
    h1_sup_str = _safe_get(h1_sup or {}, "strength", 0.0)
    h4_res_str = _safe_get(h4_res or {}, "strength", 0.0)
    h1_res_str = _safe_get(h1_res or {}, "strength", 0.0)

    support_strength = h4_sup_str + h1_sup_str
    resistance_strength = h4_res_str + h1_res_str

    if support_strength > resistance_strength + 1.0:
        side = "support"
    elif resistance_strength > support_strength + 1.0:
        side = "resistance"
    else:
        side = "balanced"

    spread = abs(support_strength - resistance_strength)

    # Build structural band blurb
    if all(v == 0.0 for v in [h4_sup_lvl, h1_sup_lvl, h4_res_lvl, h1_res_lvl]):
        text = "HTF shelves are not available; structure band could not be resolved."
    else:
        lows = [v for v in [h4_sup_lvl, h1_sup_lvl] if v > 0.0]
        highs = [v for v in [h4_res_lvl, h1_res_lvl] if v > 0.0]
        if lows and highs:
            band_low = min(lows)
            band_high = max(highs)
            width_pct = (band_high - band_low) / max(band_low, 1e-6) * 100.0
            text = (
                f"HTF structural band framed by demand near {band_low:,.1f} "
                f"and supply near {band_high:,.1f}; price is rotating inside a "
                f"{width_pct:,.1f}% high-timeframe band."
            )
        else:
            text = (
                "HTF shelves have partial data; demand and supply zones are present "
                "but the full structural band is not completely defined."
            )

    return text, side, spread


def _classify_bias(
    levels: Dict[str, Any],
    inputs: Dict[str, Any],
    htf_shelves: Dict[str, Any],
    range_30m: Dict[str, Any],
) -> BiasResult:
    """
    Simple rule-based bias engine.

    Signals considered:
      - Where 24h POC sits inside today's daily band.
      - 30m Opening Range vs daily support/resistance.
      - HTF shelf strength tilt (support vs resistance).
      - Distance between breakout/breakdown triggers.
    """
    ds = _safe_get(levels, "daily_support", 0.0)
    dr = _safe_get(levels, "daily_resistance", 0.0)
    bt = _safe_get(levels, "breakout_trigger", 0.0)
    bd = _safe_get(levels, "breakdown_trigger", 0.0)

    band_width = max(dr - ds, 1e-6)
    band_mid = (dr + ds) / 2.0

    poc24 = _safe_get(inputs, "f24_poc", 0.0)
    val24 = _safe_get(inputs, "f24_val", 0.0)
    vah24 = _safe_get(inputs, "f24_vah", 0.0)

    or_high = _safe_get(range_30m, "high", 0.0)
    or_low = _safe_get(range_30m, "low", 0.0)
    or_mid = (or_high + or_low) / 2.0 if (or_high and or_low) else poc24

    # Normalize POC location inside today's band
    poc_pos = _clamp((poc24 - ds) / band_width)
    or_pos = _clamp((or_mid - ds) / band_width)

    # HTF strength tilt
    htf_text, htf_side, htf_spread = _summarize_htf_shelves(htf_shelves)

    score_bull = 0.0
    score_bear = 0.0
    reasons: List[str] = []

    # 1) Value location vs daily band
    if poc_pos < 0.33:
        score_bull += 1.5
        reasons.append("24h POC is building in the lower third of today's band (value-long).")
    elif poc_pos > 0.67:
        score_bear += 1.5
        reasons.append("24h POC is building in the upper third of today's band (value-short).")
    else:
        reasons.append("24h POC is near the middle of today's band (balanced value).")

    # 2) Opening range vs band
    if or_pos < 0.33:
        score_bull += 1.0
        reasons.append("Opening drive is anchored in the lower third of today's band.")
    elif or_pos > 0.67:
        score_bear += 1.0
        reasons.append("Opening drive is anchored in the upper third of today's band.")
    else:
        reasons.append("Opening drive is centered within today's band.")

    # 3) HTF strength tilt
    if htf_side == "support":
        score_bull += 1.0 + 0.3 * min(htf_spread, 3.0)
        reasons.append("HTF shelf strength tilts toward demand (buyers defending deeper dips).")
    elif htf_side == "resistance":
        score_bear += 1.0 + 0.3 * min(htf_spread, 3.0)
        reasons.append("HTF shelf strength tilts toward supply (sellers leaning on rallies).")
    else:
        reasons.append("HTF shelf strength is balanced between supply and demand.")

    # 4) Trigger spacing
    trigger_span = max(bt - bd, 0.0)
    trigger_ratio = trigger_span / band_width
    if trigger_ratio < 0.35:
        reasons.append(
            "Breakout/breakdown triggers are compressed; expect aggressive rotations once either side breaks."
        )
    elif trigger_ratio > 0.7:
        reasons.append(
            "Breakout/breakdown triggers are wide apart; expect extended range behavior before expansion."
        )
    else:
        reasons.append(
            "Breakout/breakdown triggers sit comfortably inside the daily band; balanced breakout posture."
        )

    # 5) Direction of triggers vs mid
    if bt > band_mid and bd > band_mid:
        # both triggers skewed higher
        score_bear += 0.5
        reasons.append("Both breakout and breakdown triggers are skewed to the upper half of the band.")
    elif bt < band_mid and bd < band_mid:
        score_bull += 0.5
        reasons.append("Both breakout and breakdown triggers are skewed to the lower half of the band.")

    # Decide label
    delta = score_bull - score_bear
    abs_delta = abs(delta)

    if abs_delta < 0.75:
        label = "neutral"
    elif delta > 0:
        label = "bullish"
    else:
        label = "bearish"

    # Confidence based on signal separation
    if abs_delta >= 2.0:
        confidence = "high"
    elif abs_delta >= 1.0:
        confidence = "medium"
    else:
        confidence = "low"

    rationale = "; ".join(reasons[:3])  # short version for summary

    return BiasResult(label=label, confidence=confidence, rationale=rationale)


def generate_dmr_report(
    symbol: str,
    date_str: str,
    inputs: Dict[str, Any],
    levels: Dict[str, Any],
    htf_shelves: Dict[str, Any],
    range_30m: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Main entrypoint used by FastAPI.

    Returns a dict with:
      - bias: "bullish" | "bearish" | "neutral"
      - bias_confidence: "high" | "medium" | "low"
      - full_text: multi-section DMR string
      - yaml_block: optional TradingView-friendly YAML for triggers and daily band
      - sections: individual section strings (for future UI use)
    """

    ds = _safe_get(levels, "daily_support", 0.0)
    dr = _safe_get(levels, "daily_resistance", 0.0)
    bt = _safe_get(levels, "breakout_trigger", 0.0)
    bd = _safe_get(levels, "breakdown_trigger", 0.0)

    band_width = max(dr - ds, 1e-6)
    band_mid = (dr + ds) / 2.0

    poc24 = _safe_get(inputs, "f24_poc", 0.0)
    val24 = _safe_get(inputs, "f24_val", 0.0)
    vah24 = _safe_get(inputs, "f24_vah", 0.0)

    poc_week = _safe_get(inputs, "weekly_poc", 0.0)
    val_week = _safe_get(inputs, "weekly_val", 0.0)
    vah_week = _safe_get(inputs, "weekly_vah", 0.0)

    poc_morn = _safe_get(inputs, "morn_poc", 0.0)
    val_morn = _safe_get(inputs, "morn_val", 0.0)
    vah_morn = _safe_get(inputs, "morn_vah", 0.0)

    or_high = _safe_get(range_30m, "high", 0.0)
    or_low = _safe_get(range_30m, "low", 0.0)

    # HTF shelves summary
    htf_text, htf_side, htf_spread = _summarize_htf_shelves(htf_shelves)

    # Bias classification
    bias_result = _classify_bias(levels, inputs, htf_shelves, range_30m)

    # Value location descriptors
    def value_band_phrase(poc: float, low: float, high: float) -> str:
        span = max(high - low, 1e-6)
        pos = _clamp((poc - low) / span)
        if pos < 0.25:
            return "anchored in the lower quartile of the band"
        if pos < 0.5:
            return "cycling in the lower half of the band"
        if pos < 0.75:
            return "cycling in the upper half of the band"
        return "stretched into the upper quartile of the band"

    daily_value_phrase = value_band_phrase(poc24, ds, dr)
    weekly_value_phrase = value_band_phrase(poc_week, val_week, vah_week) if (val_week and vah_week) else ""
    morning_value_phrase = value_band_phrase(poc_morn, val24, vah24) if (val24 and vah24) else ""

    # Simple YAML snippet for TradingView / notes
    yaml_block = f"""triggers:
  breakout: {bt:.1f}
  breakdown: {bd:.1f}

daily_resistance: {dr:.1f}
daily_support: {ds:.1f}

range_30m:
  high: {or_high:.1f}
  low: {or_low:.1f}
"""

    # ------------------------------------------------------------------
    # SECTION 1 – Market Momentum Summary
    # ------------------------------------------------------------------
    section1 = (
        "1) Market Momentum Summary\n"
        f"- HTF Structure: {htf_text}\n"
        f"- Daily band: support near {ds:,.1f}, resistance near {dr:,.1f} "
        f"(width ~ {band_width / max(band_mid, 1e-6) * 100.0:,.1f}%).\n"
        f"- Breakout trigger at {bt:,.1f} and breakdown trigger at {bd:,.1f} "
        "define the intraday battle lines inside today's band.\n"
    )

    # ------------------------------------------------------------------
    # SECTION 2 – Sentiment Snapshot
    # ------------------------------------------------------------------
    bias_line = (
        f"Pre-market structural bias: **{bias_result.label}** "
        f"with **{bias_result.confidence}-confidence**."
    )
    section2_lines = [
        "2) Sentiment Snapshot",
        f"- {bias_line}",
        f"- Daily value (24h POC {poc24:,.1f}) is {daily_value_phrase}.",
    ]

    if weekly_value_phrase:
        section2_lines.append(
            f"- Weekly value (VRVP POC {poc_week:,.1f}) is {weekly_value_phrase}."
        )
    if morning_value_phrase:
        section2_lines.append(
            f"- Morning session value (FRVP POC {poc_morn:,.1f}) is {morning_value_phrase}."
        )

    section2_lines.append(f"- Rationale: {bias_result.rationale}")
    section2 = "\n".join(section2_lines) + "\n"

    # ------------------------------------------------------------------
    # SECTION 3 – Key Structure & Levels
    # ------------------------------------------------------------------
    section3 = (
        "3) Key Structure & Levels\n"
        f"- HTF shelves: {htf_text}\n"
        f"- Daily Support: {ds:,.1f}\n"
        f"- Daily Resistance: {dr:,.1f}\n"
        f"- Breakout Trigger: {bt:,.1f}\n"
        f"- Breakdown Trigger: {bd:,.1f}\n"
        f"- 30m Opening Range: {or_low:,.1f} – {or_high:,.1f}\n"
        f"- 24h FRVP: VAL {val24:,.1f}, POC {poc24:,.1f}, VAH {vah24:,.1f}\n"
        f"- Weekly VRVP: VAL {val_week:,.1f}, POC {poc_week:,.1f}, VAH {vah_week:,.1f}\n"
        f"- Morning FRVP: VAL {val_morn:,.1f}, POC {poc_morn:,.1f}, VAH {vah_morn:,.1f}\n"
    )

    # ------------------------------------------------------------------
    # SECTION 4 – Trade Strategy Outlook
    # ------------------------------------------------------------------
    if bias_result.label == "bullish":
        outlook = (
            "- Primary idea: favor long setups, buying dips back toward daily support "
            "or the lower third of the band as long as breakdown trigger holds.\n"
            "- Aggressive continuation: use confirmed 15m/5m closes above the breakout trigger "
            "to target the upper HTF shelf and prior HVNs.\n"
            "- Invalidations: loss of breakdown trigger with heavy volume turns the tape into a "
            "failed-long / liquidation environment."
        )
    elif bias_result.label == "bearish":
        outlook = (
            "- Primary idea: favor short setups, selling rips back toward daily resistance "
            "or the upper third of the band as long as breakout trigger caps price.\n"
            "- Aggressive continuation: use confirmed 15m/5m closes below the breakdown trigger "
            "to press into lower HTF demand and prior HVNs.\n"
            "- Invalidations: sustained acceptance above the breakout trigger transitions the tape "
            "into squeeze / trend-day conditions."
        )
    else:  # neutral
        outlook = (
            "- Primary idea: treat the session as a rotational day inside the daily band, "
            "fading extremes back toward the mid until one of the triggers truly breaks.\n"
            "- Be selective on breakout trades; wait for clear volume expansion and follow-through "
            "before committing size.\n"
            "- Expect liquidity hunts around both triggers before the market chooses a side."
        )

    section4 = "4) Trade Strategy Outlook\n" + outlook + "\n"

    # ------------------------------------------------------------------
    # SECTION 5 – Execution Notes
    # ------------------------------------------------------------------
    section5 = (
        "5) Execution Notes\n"
        "- 5m execution timeframe: focus on pullbacks toward VWAP / 21 EMA in the direction of the "
        "active bias once a trigger confirms.\n"
        "- Keep risk anchored to the opposite side of the 30m Opening Range or the invalidation trigger, "
        "whichever is cleaner on the tape.\n"
        "- Avoid over-trading the chop between triggers; the best risk-reward usually appears when the "
        "market expands away from this center zone.\n"
    )

    header = f"KTBB – Daily Market Review ({symbol}) – {date_str}\n"
    full_text = (
        "--- DMR Report ---\n\n"
        f"{header}\n"
        f"{section1}\n"
        f"{section2}\n"
        f"{section3}\n"
        f"{section4}\n"
        f"{section5}\n"
        "YAML Block (for TradingView / scripting):\n"
        f"{yaml_block}"
    )

    return {
        "bias": bias_result.label,
        "bias_confidence": bias_result.confidence,
        "full_text": full_text,
        "yaml_block": yaml_block,
        "sections": {
            "market_momentum": section1,
            "sentiment_snapshot": section2,
            "key_structure": section3,
            "strategy_outlook": section4,
            "execution_notes": section5,
        },
    }
