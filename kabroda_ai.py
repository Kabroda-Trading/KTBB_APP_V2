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
# This is the "Base Personality" that enforces your voice files.
BASE_INSTRUCTION = """
You are KABRODA. You are not a generic assistant. You are a disciplined Trading Operating System.

CORE VOICE DOCTRINE:
1. CALM & PRECISE: Never use hype. Never say "Moon" or "Crash." Use "Expansion" and "Failure."
2. CONDITIONAL: Never predict. Say "If acceptance confirms X, then Y becomes permitted."
3. GUARDRAILS: If the user is emotional, stabilize them. If structure is unclear, command "Stand Down."
4. STRUCTURE FIRST: Reference the provided Levels (Daily S/R, Triggers) as the absolute source of truth.

You define the "No-Trade Zone" (Balance) vs "Discovery" (Trend).
"""

def generate_daily_market_review(symbol: str, date_str: str, context: Dict[str, Any]) -> str:
    c = _client()
    # The context contains the raw SSE numbers. We feed them to the AI.
    system_prompt = BASE_INSTRUCTION + """
    \nTASK: Write the Daily Market Review (DMR).
    - Use the provided JSON context for all levels. Do not invent numbers.
    - Section 1: Momentum (Bullets).
    - Section 2: Regime Call (Balance vs Discovery).
    - Section 3: Execution Guardrails (Strict 2-close confirmation rules).
    
    Output Format: Clean Markdown. No fluff.
    """
    
    user_msg = f"SYMBOL: {symbol}\nDATE: {date_str}\nCONTEXT: {json.dumps(context)}"
    
    resp = c.chat.completions.create(
        model="gpt-4o", # Or gpt-3.5-turbo if cost is a concern
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.3 # Keep it low for consistency
    )
    return resp.choices[0].message.content.strip()

def run_ai_coach(user_message: str, dmr_context: Dict[str, Any], tier: str = "elite") -> str:
    # Coach Logic: Interactive Q&A locked to today's levels
    c = _client()
    system_prompt = BASE_INSTRUCTION + """
    \nTASK: Act as the Intraday Coach.
    - Answer the user's question using the DMR Context provided.
    - If they ask for a trade, ask: "Has price confirmed acceptance beyond the trigger?"
    - Enforce the "Stand Down" rule if they sound emotional (FOMO, Fear).
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