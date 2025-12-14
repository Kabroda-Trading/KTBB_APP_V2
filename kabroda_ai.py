# kabroda_ai.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from openai import OpenAI


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")
    return OpenAI(api_key=api_key)


def _strict_system_policy() -> str:
    # Keep this tight: you want deterministic structure + no hallucinations.
    return (
        "You are the Kabroda Trading BattleBox (KTBB) engine.\n"
        "Hard rules:\n"
        "- Use ONLY the provided JSON context.\n"
        "- NEVER invent prices/levels/news.\n"
        "- If an input is missing, say it is missing.\n"
        "- Output must follow the required DMR template exactly.\n"
        "- Section 1 must be exactly four bullets labeled: 4H:, 1H:, 15M:, 5M:.\n"
        "- Section 5: if no news items are provided, say 'No scheduled news injected today.'\n"
        "- Keep it actionable, not verbose.\n"
    )


def _dmr_skeleton() -> str:
    return """1) Market Momentum Summary
- 4H: <...>
- 1H: <...>
- 15M: <...>
- 5M: <...>

2) Sentiment Snapshot
<...>

3) Key Support & Resistance
<...>

4) Trade Strategy Outlook
<...>

5) News-Based Risk Alert
<...>

6) Execution Considerations
<...>

7) Weekly Zone Reference
<...>

8) YAML Key Level Output Block
```yaml
<...>
```"""


def generate_daily_market_review(symbol: str, date_str: str, dmr_payload: Dict[str, Any]) -> str:
    """
    Writes the 8-section DMR narrative using the deterministic SSE + trade logic output.
    """
    ctx = {
        "symbol": symbol,
        "date": date_str,
        "bias_label": dmr_payload.get("bias_label"),
        "levels": dmr_payload.get("levels") or {},
        "range_30m": dmr_payload.get("range_30m") or {},
        "htf_shelves": dmr_payload.get("htf_shelves") or {},
        "intraday_shelves": dmr_payload.get("intraday_shelves") or {},
        "trade_logic": dmr_payload.get("trade_logic") or {},
        "inputs": dmr_payload.get("inputs") or {},
        "news": dmr_payload.get("news") or [],
    }

    system = _strict_system_policy()

    user = (
        "INTENT: DMR\n\n"
        "SOURCE OF TRUTH JSON:\n"
        f"{json.dumps(ctx, ensure_ascii=False)}\n\n"
        "REQUIREMENTS:\n"
        "- Output MUST match the 8-section skeleton exactly.\n"
        "- Use ONLY the JSON.\n"
        "- Do NOT restate numbers without meaning; explain interaction and plan.\n"
        "- Section 8 must output YAML with the key levels.\n\n"
        "SKELETON:\n"
        f"{_dmr_skeleton()}\n"
    )

    model = os.getenv("OPENAI_DMR_MODEL", "gpt-4o-mini").strip()
    temperature = float(os.getenv("OPENAI_DMR_TEMPERATURE", "0.2"))

    resp = _client().chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def answer_coach_question(
    symbol: str,
    date_str: str,
    dmr_payload: Dict[str, Any],
    question: str,
) -> str:
    """
    Elite coach chat. Must stay anchored to SSE levels + trade logic.
    """
    ctx = {
        "symbol": symbol,
        "date": date_str,
        "bias_label": dmr_payload.get("bias_label"),
        "levels": dmr_payload.get("levels") or {},
        "range_30m": dmr_payload.get("range_30m") or {},
        "htf_shelves": dmr_payload.get("htf_shelves") or {},
        "intraday_shelves": dmr_payload.get("intraday_shelves") or {},
        "trade_logic": dmr_payload.get("trade_logic") or {},
        "inputs": dmr_payload.get("inputs") or {},
        "news": dmr_payload.get("news") or [],
        "question": question.strip(),
    }

    system = (
        _strict_system_policy()
        + "\nCoach rules:\n"
        "- Be concise.\n"
        "- Reference: daily_support, daily_resistance, breakout_trigger, breakdown_trigger, OR.\n"
        "- If asked for a plan, map to the strategy IDs already present in trade_logic.\n"
    )

    user = (
        "Answer the user's question using ONLY the JSON.\n\n"
        f"{json.dumps(ctx, ensure_ascii=False)}"
    )

    model = os.getenv("OPENAI_COACH_MODEL", "gpt-4o-mini").strip()
    temperature = float(os.getenv("OPENAI_COACH_TEMPERATURE", "0.2"))

    resp = _client().chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
