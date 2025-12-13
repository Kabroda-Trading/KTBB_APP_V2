import os
from pathlib import Path
from typing import Any, Dict

# OpenAI SDK (new style)
# pip: openai>=1.0.0
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


ROOT = Path(__file__).resolve().parent

ANCHOR_PATHS = [
    ROOT / "ktbb_execution_anchor_v_4.2.md",
    ROOT / "ktbb_execution_anchor_v_4.2.md".replace("\\", "/"),  # harmless fallback
]
LOGIC_PATHS = [
    ROOT / "ktbb_trade_logic_module_v_2_0.md",
    ROOT / "ktbb_trade_logic_module_v_1.6.md",
]

def _read_first_existing(paths):
    for p in paths:
        try:
            p = Path(str(p))
            if p.exists():
                return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return ""

EXEC_ANCHOR_TEXT = _read_first_existing(ANCHOR_PATHS)
TRADE_LOGIC_TEXT = _read_first_existing(LOGIC_PATHS)

def _client():
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not installed or import failed. Ensure openai>=1.0.0.")
    return OpenAI(api_key=api_key)

def _strict_system_policy() -> str:
    # Hard guardrails: level contract, drift firewall, template lock, separation of concerns
    return f"""
You are Kabroda AI for the Kabroda BattleBox Suite.

NON-NEGOTIABLE RULES:
- Never invent, alter, or “improve” price levels. Use only the provided numbers.
- If inputs are missing or unclear: output a missing-input checklist and a blank scaffold; do not guess.
- Stay deterministic and anchored: same inputs -> same conclusions (no drift).
- Respect separation of concerns: levels are produced upstream; you only interpret them.

EXECUTION ANCHOR (policy + template lock):
{EXEC_ANCHOR_TEXT}

TRADE LOGIC MODULE (strategy/outlook rules; do not mutate levels):
{TRADE_LOGIC_TEXT}
""".strip()

def _dmr_user_prompt(symbol: str, date_str: str, dmr_payload: Dict[str, Any]) -> str:
    # The model receives the computed outputs and must write the review using the locked template.
    return f"""
INTENT: DMR

Write today’s Daily Market Review for {symbol} on {date_str}.

STYLE:
- Clean, readable, trader-first. No code dumps in the narrative.
- Follow the DMR Template Lock from the Execution Anchor exactly (eight sections).
- Bullets where required (especially section 1).
- Use the computed values below as the single source of truth.

COMPUTED OUTPUTS (JSON):
{dmr_payload}
""".strip()

def _coach_user_prompt(symbol: str, date_str: str, dmr_payload: Dict[str, Any], question: str) -> str:
    return f"""
INTENT: Battle Plan

You are the Kabroda AI Coach answering a user question for {symbol} on {date_str}.

CONSTRAINTS:
- Use ONLY the computed outputs below for numeric references.
- Do not reference any “memory” or prior chat.
- If the question asks for something not supported by the computed data, say what is missing and what to provide.

COMPUTED OUTPUTS (JSON):
{dmr_payload}

USER QUESTION:
{question}
""".strip()

def generate_daily_market_review(symbol: str, date_str: str, dmr_payload: Dict[str, Any]) -> str:
    c = _client()
    resp = c.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        messages=[
            {"role": "system", "content": _strict_system_policy()},
            {"role": "user", "content": _dmr_user_prompt(symbol, date_str, dmr_payload)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

def answer_coach_question(symbol: str, date_str: str, dmr_payload: Dict[str, Any], question: str) -> str:
    c = _client()
    resp = c.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        messages=[
            {"role": "system", "content": _strict_system_policy()},
            {"role": "user", "content": _coach_user_prompt(symbol, date_str, dmr_payload, question)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
