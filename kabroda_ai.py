from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict

# OpenAI SDK (new style) pip: openai>=1.0.0
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

ROOT = Path(__file__).resolve().parent

ANCHOR_PATHS = [
    ROOT / "ktbb_execution_anchor_v_4.2.md",
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


resp = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    temperature=float(os.getenv("OPENAI_DMR_TEMPERATURE", "0.35")),
)

text = (resp.choices[0].message.content or "").strip()

# SEATBELT: if we have outlook_text but it didn't appear, inject it under Section 4.
if outlook_text:
    if outlook_text.splitlines()[0] not in text:
        text = text.replace(
            "4) Trade Strategy Outlook",
            "4) Trade Strategy Outlook\n" + outlook_text.strip() + "\n",
            1,
        )

return text


def answer_coach_question(symbol: str, date_str: str, dmr_payload: dict, question: str) -> str:
    """
    KTBB Elite coach — strategy-aware, level-anchored, no drift.
    """
    client = _client()
    model = os.getenv("OPENAI_COACH_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    trade_logic = dmr_payload.get("trade_logic") or {}
    outlook_text = (trade_logic.get("outlook_text") or "").strip()
    if outlook_text.startswith("4) Trade Strategy Outlook"):
        parts = outlook_text.split("\n", 1)
        outlook_text = parts[1].lstrip() if len(parts) > 1 else ""

    context = {
        "symbol": symbol,
        "date": date_str,
        "bias_label": dmr_payload.get("bias_label", None),
        "levels": dmr_payload.get("levels", {}) or {},
        "range_30m": dmr_payload.get("range_30m", {}) or {},
        "htf_shelves": dmr_payload.get("htf_shelves", {}) or {},
        "trade_logic": trade_logic if trade_logic else None,
        "trade_logic_outlook_text": outlook_text if outlook_text else None,
        "question": (question or "").strip(),
    }

    system = (
        _strict_system_policy()
        + "\n\nYou are the Kabroda Trading BattleBox (KTBB) Coach.\n"
          "Rules:\n"
          "- Use ONLY the provided context; do NOT invent prices/levels.\n"
          "- Anchor answers to breakout/breakdown triggers + daily S/R + OR.\n"
          "- If strategy is asked, map to KTBB S0–S8 using context.trade_logic when present.\n"
          "- Be concise and actionable.\n"
          "- No disclaimers."
    )

    user = f"""
INTENT: Coach

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
