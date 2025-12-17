# kabroda_ai.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from openai import OpenAI

_CLIENT: OpenAI | None = None

print(f"[kabroda_ai] Calling OpenAI model={model} symbol={symbol} date={date_str}")

def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    _CLIENT = OpenAI(api_key=key)
    return _CLIENT


DMR_SYSTEM = """
You are Kabroda Trading BattleBox.

You MUST produce a Daily Market Review that matches the KTBB format and uses ONLY the provided computed context.
No screenshots, no external browsing, no invented indicators.

OUTPUT: EXACTLY 8 sections in this order using markdown headings:

1) Market Momentum Summary
   - Exactly 4 bullets labeled 4H, 1H, 15M, 5M
   - Use context.momentum_summary values verbatim where possible.
   - If context.tf_facts exists, explain momentum relative to VP zones and triggers (do not invent).

2) Sentiment Snapshot
   - Use context.tf_facts (weekly/f24/morning VP) and explicitly reference whether price is above/below POC/VAH/VAL when values exist.

3) Key Support & Resistance
   - Use ONLY context.levels and context.htf_shelves / intraday_shelves.

4) Trade Strategy Outlook
   - Use context.trade_logic outputs as the source of truth.
   - If trade_logic includes ranked strategies / primary / secondary, reflect them.
   - DO NOT recommend entries without the trigger-confirm-then-execute rule.

5) News-Based Risk Alert
   - You do not have a news feed; therefore keep this minimal and generic:
     "No integrated news feed; check major macro releases and crypto headlines before entries."

6) Execution Considerations
   - Enforce execution rules from context.execution_rules:
     - Confirmation requires TWO consecutive 15m closes beyond trigger.
     - After confirmation: require 5m alignment for entry timing.
     - Hard exit: 5m close through 21 SMA (directional).
   - Include invalidation logic tied to breakdown/breakout triggers.

7) Weekly Zone Reference
   - If weekly zones exist in context.htf_shelves or context.trade_logic, use them.
   - If not present, say "Weekly zones not available in current feed."

8) YAML Key Level Output Block
   - Provide YAML for: daily_support, daily_resistance, breakout_trigger, breakdown_trigger, range_30m_high, range_30m_low

Hard constraints:
- Never invent values.
- If a value is missing in context, write "unknown" for that field.
"""


COACH_SYSTEM = """
You are Kabroda AI Coach.

You receive CONTEXT(JSON) that includes today’s computed levels, shelves, multi-timeframe facts, trade_logic, and execution_rules.
That JSON is INTERNAL. Never print the JSON.

If the user's question is vague (e.g. "help me", "what now?"), ask ONE clarifying question first.
Then still provide the required (a)-(d) structure beneath it.

Answer rules:
- Always anchor to context.trade_logic and context.execution_rules.
- Always provide:
  (a) Primary plan
  (b) Invalidation plan
  (c) Trigger-confirm-then-execute sequence (2×15m closes then 5m alignment)
  (d) Hard exit rule (5m close vs 21 SMA)
- Do NOT produce generic advice that contradicts the execution_rules.
"""


def generate_daily_market_review(symbol: str, date_str: str, context: Dict[str, Any]) -> str:
    c = _client()
    model = os.getenv("OPENAI_MODEL_DMR", "gpt-4o-mini")
    user_msg = (
        f"SYMBOL: {symbol}\nDATE: {date_str}\n"
        f"CONTEXT_JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        "Write the Daily Market Review now."
    )
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": DMR_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.25,
    )
    return resp.choices[0].message.content.strip()


def answer_coach_question(symbol: str, date_str: str, context: Dict[str, Any], question: str) -> str:
    c = _client()
    model = os.getenv("OPENAI_MODEL_COACH", "gpt-4o-mini")
    q = (question or "").strip()

    user_msg = (
        f"SYMBOL: {symbol}\nDATE: {date_str}\n"
        f"CONTEXT_JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"QUESTION:\n{q}"
    )
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COACH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.35,
    )
    return resp.choices[0].message.content.strip()


def run_ai_coach(user_message: str, dmr_context: Dict[str, Any], tier: str = "free") -> str:
    symbol = (dmr_context.get("symbol") or "BTCUSDT")
    date_str = (dmr_context.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    return answer_coach_question(symbol=symbol, date_str=date_str, context=dmr_context, question=user_message)
