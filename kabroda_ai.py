# kabroda_ai.py
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Any, Dict

from openai import OpenAI

CLIENT = None


def _client() -> OpenAI:
    global CLIENT
    if CLIENT is not None:
        return CLIENT
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    CLIENT = OpenAI(api_key=key)
    return CLIENT


DMR_SYSTEM = """
You are Kabroda Trading BattleBox.
Write a Daily Market Review using the provided computed levels and shelves.
Be concise, structured, and actionable.

Requirements:
- Use the levels provided (daily support/resistance, breakout/breakdown triggers, opening range).
- If a value is missing, explicitly say it is missing (do not invent).
- Output must be clean narrative + include a YAML block of key levels at the end.
"""

COACH_SYSTEM = """
You are Kabroda AI Coach.
You will receive a JSON CONTEXT containing today's computed levels and DMR.
That JSON is INTERNAL CONTEXT — DO NOT repeat it, do not quote it, do not print it.
Answer in plain English.

Rules:
- If user greets (hi/hello), respond like a normal coach and ask what they want to do.
- Otherwise answer anchored to today's levels.
- Use bullets; give a primary plan and an invalidation point.
- If context values are missing, ask for exactly what is missing.
"""


def generate_daily_market_review(symbol: str, date_str: str, context: Dict[str, Any]) -> str:
    c = _client()
    user_msg = (
        f"SYMBOL: {symbol}\nDATE: {date_str}\n"
        f"CONTEXT(JSON, do not repeat):\n{json.dumps(context)}\n\n"
        "Write the Daily Market Review now."
    )
    resp = c.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_DMR", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": DMR_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def answer_coach_question(symbol: str, date_str: str, context: Dict[str, Any], question: str) -> str:
    q = (question or "").strip()
    c = _client()

    user_msg = (
        f"SYMBOL: {symbol}\nDATE: {date_str}\n"
        f"CONTEXT(JSON, do not repeat):\n{json.dumps(context)}\n\n"
        f"QUESTION:\n{q}"
    )

    resp = c.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_COACH", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": COACH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    return resp.choices[0].message.content.strip()


def run_ai_coach(user_message: str, dmr_context: Dict[str, Any], tier: str = "free") -> str:
    """
    Wrapper expected by main.py.
    """
    symbol = (dmr_context.get("symbol") or "BTCUSDT")
    date_str = (dmr_context.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    return answer_coach_question(symbol=symbol, date_str=date_str, context=dmr_context, question=user_message)
