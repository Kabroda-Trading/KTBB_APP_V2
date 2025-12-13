import os
import json
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

def generate_daily_market_review(symbol: str, date_str: str, dmr_payload: dict) -> str:
    """
    KTBB DMR writer (Execution Anchor v4.2 compliant).
    Uses trade_logic summary when available for Strategy Outlook.
    """
    client = _client()
    model = os.getenv("OPENAI_DMR_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    # Only pass what the model is allowed to reference
    context = {
        "symbol": symbol,
        "date": date_str,
        "levels": dmr_payload.get("levels", {}) or {},
        "range_30m": dmr_payload.get("range_30m", {}) or {},
        "htf_shelves": dmr_payload.get("htf_shelves", {}) or {},
        "trade_logic": dmr_payload.get("trade_logic", None),
    }

    system = (
        "You are Kabroda Trading BattleBox (KTBB).\n"
        "Hard rules:\n"
        "1) Use ONLY the provided context. Do NOT invent prices/levels.\n"
        "2) Levels are immutable. Do NOT modify or reinterpret them.\n"
        "3) Follow the Execution Anchor v4.2 8-section template lock:\n"
        "   1) Market Momentum Summary (exactly 4 bullets: 4H, 1H, 15M, 5M)\n"
        "   2) Sentiment Snapshot\n"
        "   3) Key Support & Resistance\n"
        "   4) Trade Strategy Outlook (use KTBB S0–S8 language; prefer context.trade_logic if present)\n"
        "   5) News-Based Risk Alert (if no news provided, state: 'No scheduled news injected today')\n"
        "   6) Execution Considerations\n"
        "   7) Weekly Zone Reference\n"
        "   8) YAML Key Level Output Block\n"
        "4) No filler. No textbook explanations. No disclaimers."
    )

    user = f"""
Context JSON (source of truth):
{json.dumps(context, ensure_ascii=False)}

Output requirements:
- Headings must be numbered 1–8 exactly as the template lock.
- Section 1 MUST be exactly 4 bullets labeled: 4H:, 1H:, 15M:, 5M:.
- In Section 4, if context.trade_logic exists, incorporate it (especially any outlook_text).
- YAML block must include:
  triggers.breakout, triggers.breakdown, daily_resistance, daily_support,
  range_30m.high, range_30m.low.

Now output the KTBB DMR.
""".strip()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=float(os.getenv("OPENAI_DMR_TEMPERATURE", "0.35")),
    )
    return (resp.choices[0].message.content or "").strip()
def answer_coach_question(symbol: str, date_str: str, dmr_payload: dict, question: str) -> str:
    """
    KTBB Elite coach — strategy-aware, level-anchored, no drift.
    """
    client = _client()
    model = os.getenv("OPENAI_COACH_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    context = {
        "symbol": symbol,
        "date": date_str,
        "levels": dmr_payload.get("levels", {}) or {},
        "range_30m": dmr_payload.get("range_30m", {}) or {},
        "htf_shelves": dmr_payload.get("htf_shelves", {}) or {},
        "trade_logic": dmr_payload.get("trade_logic", None),
        "question": (question or "").strip(),
    }

    system = (
        "You are Kabroda Trading BattleBox (KTBB) Coach.\n"
        "Rules:\n"
        "- Use ONLY the provided context; do NOT invent prices/levels.\n"
        "- Anchor answers to breakout/breakdown triggers + daily S/R + OR.\n"
        "- If strategy is asked, map to KTBB S0–S8 using context.trade_logic when present.\n"
        "- Be concise and actionable.\n"
        "- No disclaimers."
    )

    user = f"""
Context JSON:
{json.dumps(context, ensure_ascii=False)}

Answer format:
1) Direct answer (2–6 bullets)
2) Strategy mapping (S#) + why
3) Execution guardrails (risk anchor + invalidation)

User question:
{context["question"]}
""".strip()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=float(os.getenv("OPENAI_COACH_TEMPERATURE", "0.4")),
    )
    return (resp.choices[0].message.content or "").strip()
