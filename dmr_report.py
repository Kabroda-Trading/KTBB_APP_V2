# KTBB_app_v2/dmr_report.py

from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List


def _fmt(x: float) -> str:
    """Format price to 1 decimal place."""
    return f"{x:,.1f}"


def _compute_structural_bias(
    daily_support: float,
    daily_resistance: float,
    f24_poc: float,
) -> str:
    """
    Very simple, deterministic pre-market structural bias:

    - If 24h POC is in the lower third of the S/R band → long bias.
    - If 24h POC is in the upper third of the S/R band → short bias.
    - Otherwise → neutral.

    This is *commentary only* and does not override Trade Logic v1.6.
    """
    span = max(daily_resistance - daily_support, 1.0)
    rel = (f24_poc - daily_support) / span

    if rel <= 1 / 3:
        return "long"
    if rel >= 2 / 3:
        return "short"
    return "neutral"


def _htf_primary(
    shelves: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Return the primary shelf, or the strongest if none flagged primary."""
    if not shelves:
        return None
    primaries = [s for s in shelves if s.get("primary")]
    if primaries:
        return primaries[0]
    # fallback = strongest by strength
    return max(shelves, key=lambda s: s.get("strength", 0.0))


def generate_dmr_report(
    *,
    symbol: str,
    date_str: str | None,
    inputs: Dict[str, float],
    levels: Dict[str, float],
    htf_shelves: Dict[str, List[Dict[str, Any]]],
    range_30m: Dict[str, float],
) -> Dict[str, Any]:
    """
    Build a full-text KTBB Daily Market Review using the fixed 8-section
    Execution Anchor v4.2 template.

    All phrasing is deterministic and based only on numeric inputs.
    """

    # Unpack inputs we care about
    weekly_val = inputs["weekly_val"]
    weekly_poc = inputs["weekly_poc"]
    weekly_vah = inputs["weekly_vah"]

    f24_val = inputs["f24_val"]
    f24_poc = inputs["f24_poc"]
    f24_vah = inputs["f24_vah"]

    morn_val = inputs["morn_val"]
    morn_poc = inputs["morn_poc"]
    morn_vah = inputs["morn_vah"]

    r30_high = range_30m["high"]
    r30_low = range_30m["low"]

    daily_support = levels["daily_support"]
    daily_resistance = levels["daily_resistance"]
    breakout_trigger = levels["breakout_trigger"]
    breakdown_trigger = levels["breakdown_trigger"]

    htf_res = htf_shelves.get("resistance", [])
    htf_sup = htf_shelves.get("support", [])

    primary_res = _htf_primary(htf_res)
    primary_sup = _htf_primary(htf_sup)

    span = max(daily_resistance - daily_support, 1.0)
    mid_band = daily_support + span / 2.0

    bias = _compute_structural_bias(daily_support, daily_resistance, f24_poc)

    # ------------------------------------------------------------------
    # Section 1: Market Momentum Summary (bullets only)
    # ------------------------------------------------------------------
    # We don’t know actual trend, so we describe the structural box and value.
    s1_lines = []

    # 4H
    if primary_sup and primary_res:
        s1_lines.append(
            f"- 4H: Structural band framed by demand near {_fmt(primary_sup['level'])} "
            f"and supply near {_fmt(primary_res['level'])}; {symbol} is boxed inside a "
            f"{span / mid_band * 100:.1f}% HTF range."
        )
    else:
        s1_lines.append(
            f"- 4H: HTF structural shelves detected between {_fmt(daily_support)} "
            f"and {_fmt(daily_resistance)}."
        )

    # 1H
    if len(htf_sup) == 2 and len(htf_res) == 2:
        s1_lines.append(
            f"- 1H: Demand anchored near {_fmt(htf_sup[1]['level'])} and "
            f"supply near {_fmt(htf_res[1]['level'])}; intraday rotations are "
            f"occurring inside the HTF band."
        )
    else:
        s1_lines.append(
            f"- 1H: Price action expected to respect the same {symbol} HTF band; "
            "watch for shelves forming around the triggers."
        )

    # 15M
    s1_lines.append(
        f"- 15M: Breakout trigger at {_fmt(breakout_trigger)} and breakdown trigger at "
        f"{_fmt(breakdown_trigger)} define the active battle lines inside today’s band."
    )

    # 5M
    s1_lines.append(
        "- 5M: Execution timeframe; focus on pullbacks and 5M 21 SMA behavior once "
        "either trigger has confirmed."
    )

    section1 = "\n".join(s1_lines)

    # ------------------------------------------------------------------
    # Section 2: Sentiment Snapshot
    # ------------------------------------------------------------------
    if bias == "long":
        bias_sentence = (
            "Pre-market structural bias: **long**. Value (24h POC) is building "
            "in the lower third of today’s range, suggesting buyers are defending "
            "dips toward support."
        )
    elif bias == "short":
        bias_sentence = (
            "Pre-market structural bias: **short**. Value (24h POC) is building "
            "in the upper third of today’s range, suggesting sellers are leaning "
            "against resistance."
        )
    else:
        bias_sentence = (
            "Pre-market structural bias: **neutral**. Value (24h POC) is clustered "
            "near the middle of today’s HTF band; expect rotational behavior until "
            "one of the triggers decisively breaks."
        )

    section2 = (
        f"{bias_sentence}\n\n"
        f"- Weekly value area: {_fmt(weekly_val)} – {_fmt(weekly_vah)} "
        f"(POC {_fmt(weekly_poc)}).\n"
        f"- 24h value area:    {_fmt(f24_val)} – {_fmt(f24_vah)} "
        f"(POC {_fmt(f24_poc)}).\n"
        f"- Morning value:     {_fmt(morn_val)} – {_fmt(morn_vah)} "
        f"(POC {_fmt(morn_poc)})."
    )

    # ------------------------------------------------------------------
    # Section 3: Key Support & Resistance
    # ------------------------------------------------------------------
    section3_lines = [
        f"- Daily Support (HTF demand): **{_fmt(daily_support)}**",
        f"- Daily Resistance (HTF supply): **{_fmt(daily_resistance)}**",
        f"- Breakout trigger (intraday long line): **{_fmt(breakout_trigger)}**",
        f"- Breakdown trigger (intraday short line): **{_fmt(breakdown_trigger)}**",
        f"- 30m opening range: **{_fmt(r30_low)} – {_fmt(r30_high)}**",
        "",
        "HTF shelves (strength 0–10):",
    ]

    for s in htf_sup:
        tag = "PRIMARY" if s.get("primary") else "secondary"
        section3_lines.append(
            f"  - Support {s['tf']} @ {_fmt(s['level'])} "
            f"(strength {s['strength']}, {tag})"
        )

    for s in htf_res:
        tag = "PRIMARY" if s.get("primary") else "secondary"
        section3_lines.append(
            f"  - Resistance {s['tf']} @ {_fmt(s['level'])} "
            f"(strength {s['strength']}, {tag})"
        )

    section3 = "\n".join(section3_lines)

    # ------------------------------------------------------------------
    # Section 4: Trade Strategy Outlook
    # ------------------------------------------------------------------
    if bias == "long":
        outlook_bias_line = (
            "Base case: favor **longs** on confirmed holds above the breakout "
            "trigger, using pullbacks toward that level as opportunity."
        )
    elif bias == "short":
        outlook_bias_line = (
            "Base case: favor **shorts** on confirmed holds below the breakdown "
            "trigger, fading rallies back into resistance."
        )
    else:
        outlook_bias_line = (
            "Base case: remain **tactically neutral** until a clean break and hold "
            "beyond either trigger establishes directional control."
        )

    section4 = (
        f"{outlook_bias_line}\n\n"
        "- Long scenario:\n"
        f"  - 15M closes: two consecutive closes **above {_fmt(breakout_trigger)}**.\n"
        "  - After confirmation, look for 5M pullbacks toward the trigger or 5M 21 SMA "
        "with trend aligned to the upside.\n"
        "- Short scenario:\n"
        f"  - 15M closes: two consecutive closes **below {_fmt(breakdown_trigger)}**.\n"
        "  - After confirmation, look for 5M rallies back toward the trigger or 5M 21 SMA "
        "with trend aligned to the downside."
    )

    # ------------------------------------------------------------------
    # Section 5: News-Based Risk Alert
    # ------------------------------------------------------------------
    section5 = (
        "This engine does **not** ingest news or macro data directly.\n\n"
        "- Treat economic releases, FOMC, and major headlines as **external risk overlays**.\n"
        "- On high-impact calendar days, reduce size, widen stops modestly, or wait for "
        "post-event structure to rebuild around the triggers.\n"
        "- If news produces a fast impulse that jumps across both triggers inside "
        "one 15M bar, delay entries until the 15M structure stabilizes."
    )

    # ------------------------------------------------------------------
    # Section 6: Execution Considerations
    # ------------------------------------------------------------------
    section6 = (
        "Execution is governed by Trade Logic Module v1.6:\n\n"
        "- **Trigger confirmation (15M):**\n"
        f"  - Long bias: two 15M closes above **{_fmt(breakout_trigger)}**.\n"
        f"  - Short bias: two 15M closes below **{_fmt(breakdown_trigger)}**.\n"
        "- **Entry filter (5M):**\n"
        "  - Longs: price & 21 SMA above or turning up toward 200 SMA, with an oscillator reset.\n"
        "  - Shorts: price & 21 SMA below or turning down from 200 SMA, with an oscillator reset.\n"
        "- **Hard exit:**\n"
        "  - Longs: 5M close below 21 SMA.\n"
        "  - Shorts: 5M close above 21 SMA."
    )

    # ------------------------------------------------------------------
    # Section 7: Weekly Zone Reference
    # ------------------------------------------------------------------
    weekly_vs_24h = (
        "24h value is **inside** the weekly value area."
        if (weekly_val <= f24_poc <= weekly_vah)
        else (
            "24h value is **above** the weekly value area."
            if f24_poc > weekly_vah
            else "24h value is **below** the weekly value area."
        )
    )

    section7 = (
        f"- Weekly VRVP: VAL {_fmt(weekly_val)}, POC {_fmt(weekly_poc)}, "
        f"VAH {_fmt(weekly_vah)}.\n"
        f"- 24h FRVP: VAL {_fmt(f24_val)}, POC {_fmt(f24_poc)}, VAH {_fmt(f24_vah)}.\n"
        f"- Relationship: {weekly_vs_24h}"
    )

    # ------------------------------------------------------------------
    # Section 8: YAML Key Level Output Block
    # ------------------------------------------------------------------
    yaml_lines = [
        "triggers:",
        f"  breakout: {breakout_trigger}",
        f"  breakdown: {breakdown_trigger}",
        "",
        f"daily_resistance: {daily_resistance}",
        f"daily_support: {daily_support}",
        "",
        "range_30m:",
        f"  high: {r30_high}",
        f"  low: {r30_low}",
        "",
        "htf_shelves:",
        "  resistance:",
    ]

    for s in htf_res:
        yaml_lines.append("    - tf: " + s["tf"])
        yaml_lines.append(f"      level: {s['level']}")
        yaml_lines.append(f"      strength: {s['strength']}")
        yaml_lines.append(f"      primary: {bool(s.get('primary'))}")

    yaml_lines.append("  support:")
    for s in htf_sup:
        yaml_lines.append("    - tf: " + s["tf"])
        yaml_lines.append(f"      level: {s['level']}")
        yaml_lines.append(f"      strength: {s['strength']}")
        yaml_lines.append(f"      primary: {bool(s.get('primary'))}")

    yaml_lines.append("intraday_shelves:")
    yaml_lines.append("  breakout_side: []")
    yaml_lines.append("  breakdown_side: []")

    yaml_block = "\n".join(yaml_lines)

    # ------------------------------------------------------------------
    # Assemble full text (8 sections)
    # ------------------------------------------------------------------
    today_label = date_str or datetime.utcnow().strftime("%Y-%m-%d")

    header = f"KTBB – Daily Market Review ({symbol}) – {today_label}"

    sections = {
        "1_market_momentum_summary": section1,
        "2_sentiment_snapshot": section2,
        "3_key_support_resistance": section3,
        "4_trade_strategy_outlook": section4,
        "5_news_risk_alert": section5,
        "6_execution_considerations": section6,
        "7_weekly_zone_reference": section7,
        "8_yaml_block": yaml_block,
    }

    full_text_parts = [
        header,
        "",
        "1) Market Momentum Summary",
        section1,
        "",
        "2) Sentiment Snapshot",
        section2,
        "",
        "3) Key Support & Resistance",
        section3,
        "",
        "4) Trade Strategy Outlook",
        section4,
        "",
        "5) News-Based Risk Alert",
        section5,
        "",
        "6) Execution Considerations",
        section6,
        "",
        "7) Weekly Zone Reference",
        section7,
        "",
        "8) YAML Key Level Output Block",
        yaml_block,
    ]

    full_text = "\n".join(full_text_parts)

    return {
        "bias": bias,
        "sections": sections,
        "yaml_block": yaml_block,
        "full_text": full_text,
    }
