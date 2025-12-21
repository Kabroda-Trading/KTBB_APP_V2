# kabroda_ai.py
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from typing import Any, Dict
from openai import OpenAI

_CLIENT: OpenAI | None = None

def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT: return _CLIENT
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key: raise RuntimeError("OPENAI_API_KEY missing")
    _CLIENT = OpenAI(api_key=key, timeout=60, max_retries=2)
    return _CLIENT

# --- THE DOCTRINE CORE ---
BASE_INSTRUCTION = """
You are KABRODA. You are not a generic assistant. You are a disciplined Trading Operating System.

CORE VOICE DOCTRINE:
1. DEFINITIONS (CRITICAL): 
   - "VAH" = Value Area High. "VAL" = Value Area Low. "POC" = Point of Control.
   - Never say "Virginia Area".
2. CALM & PRECISE: Never use hype. Never say "Moon" or "Crash." Use "Expansion" and "Failure."
3. CONDITIONAL: Never predict. Say "If acceptance confirms X, then Y becomes permitted."
4. GUARDRAILS: If the user is emotional, stabilize them. If structure is unclear, command "Stand Down."
5. STRUCTURE FIRST: Reference the provided Levels (Daily S/R, Triggers) as the absolute source of truth.

You define the "No-Trade Zone" (Balance) vs "Discovery" (Trend).
"""

def generate_daily_market_review(symbol: str, date_str: str, context: Dict[str, Any]) -> str:
    c = _client()
    system_prompt = BASE_INSTRUCTION + """
    \nTASK: Write the Daily Market Review (DMR).
    
    REQUIRED STRUCTURE (Use Markdown Headers):
    # 1. Momentum & Regime
    - Define if we are in "Balance" (Range) or "Discovery" (Trend).
    
    # 2. Execution Guardrails
    - List the Breakout Trigger and Breakdown Trigger clearly.
    - State condition: "Requires 2x 15m closes to confirm acceptance."
    
    # 3. Behavioral Guidance
    - "If price is between triggers, we are in rotation. Patience required."
    - "Hard Exit: 5m close across the 21 SMA."
    
    Output Format: Clean Markdown with bolding for levels. No fluff.
    """
    
    user_msg = f"SYMBOL: {symbol}\nDATE: {date_str}\nCONTEXT: {json.dumps(context)}"
    
    resp = c.chat.completions.create(
        model="gpt-4o", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.3
    )
    return resp.choices[0].message.content.strip()

def run_ai_coach(user_message: str, dmr_context: Dict[str, Any], tier: str = "elite") -> str:
    c = _client()
    system_prompt = BASE_INSTRUCTION + """
    \nTASK: Act as the Intraday Coach.
    - Answer the user's question using the DMR Context provided.
    - If they ask for a trade, ask: "Has price confirmed acceptance beyond the trigger?"
    - Use Markdown formatting (bolding, lists) to make text readable.
    """
    
    user_msg = f"CONTEXT: {json.dumps(dmr_context)}\nUSER QUESTION: {user_message}"
    
    resp = c.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ]
    )
    return resp.choices[0].message.content.strip()