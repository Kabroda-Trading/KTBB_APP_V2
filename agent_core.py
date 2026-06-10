# agent_core.py
# ==============================================================================
# KABRODA AGENT CORE — Phase 1: Cost Infrastructure
# Single entry point for all agent invocations across Phases 3-4.
#
# Enforces:
#   1. Daily budget gate (AGENT_DAILY_BUDGET_USD env var, default $5.00)
#   2. Prompt caching on every system prompt (cache_control: ephemeral)
#   3. Full token + cost logging to agent_run_log on every call
#
# Sonnet 4.6 rates (per token):
#   Input:       $3.00 / 1M
#   Output:     $15.00 / 1M
#   Cache read:  $0.30 / 1M  (~10x cheaper than input)
#   Cache write: $3.75 / 1M
# ==============================================================================

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import anthropic

from database import SessionLocal, AgentRunLog

_MODEL = "claude-sonnet-4-6"
_SPEC_DIR = Path(__file__).parent / "agents"

_RATES = {
    "input":       3.00 / 1_000_000,
    "output":     15.00 / 1_000_000,
    "cache_read":  0.30 / 1_000_000,
    "cache_write": 3.75 / 1_000_000,
}

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    return (
        input_tokens       * _RATES["input"]
        + output_tokens      * _RATES["output"]
        + cache_read_tokens  * _RATES["cache_read"]
        + cache_write_tokens * _RATES["cache_write"]
    )


def _log_run(
    agent_name: str,
    model: str,
    triggered_by: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    estimated_cost_usd: float,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        row = AgentRunLog(
            agent_name=agent_name,
            model=model,
            triggered_by=triggered_by,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            estimated_cost_usd=estimated_cost_usd,
            status=status,
            error_message=error_message,
        )
        db.add(row)
        db.commit()
    except Exception as e:
        print(f"[AGENT_CORE] Log write failed: {e}")
    finally:
        db.close()


def _check_budget_before_run(agent_name: str) -> bool:
    """
    Returns True if the daily budget allows this call.
    Returns False and logs a BUDGET_BLOCKED row if the cap is exceeded.
    Fails open (returns True) if the DB is unavailable — agents are
    more important than the accounting when infra is down.
    """
    daily_cap = float(os.getenv("AGENT_DAILY_BUDGET_USD", "10.00"))
    db = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = db.query(AgentRunLog).filter(
            AgentRunLog.created_at >= since,
            AgentRunLog.status != "BUDGET_BLOCKED",
        ).all()
        spent = sum(r.estimated_cost_usd for r in rows)

        if spent >= daily_cap:
            _log_run(
                agent_name=agent_name,
                model=_MODEL,
                triggered_by="budget_gate",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                estimated_cost_usd=0.0,
                status="BUDGET_BLOCKED",
                error_message=(
                    f"Daily cap ${daily_cap:.2f} exceeded. "
                    f"24h spend: ${spent:.4f}"
                ),
            )
            print(
                f"[AGENT_CORE] BUDGET BLOCKED — {agent_name}. "
                f"Spent ${spent:.4f} of ${daily_cap:.2f} daily cap."
            )
            return False

        return True

    except Exception as e:
        print(f"[AGENT_CORE] Budget check DB error (failing open): {e}")
        return True
    finally:
        db.close()


def _call_agent(
    agent_name: str,
    system_prompt: str,
    context_text: str,
    triggered_by: str = "manual",
    model: str = _MODEL,
    max_tokens: int = 2048,
) -> str:
    """
    Unified agent call. All agents in the Kabroda system use this function.

    Flow:
      1. Budget gate — blocks call and raises RuntimeError if cap exceeded
      2. Fires messages.create() with prompt caching on system prompt
      3. Extracts token counts from response.usage
      4. Calculates cost at Sonnet 4.6 rates
      5. Logs one row to agent_run_log (SUCCESS or ERROR)
      6. Returns the response text

    Raises:
      RuntimeError  — budget blocked (logged, no API call made)
      Exception     — API or DB error (logged with ERROR status)
    """
    if not _check_budget_before_run(agent_name):
        raise RuntimeError(f"[BUDGET_BLOCKED] {agent_name} blocked by daily cap.")

    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    cost = 0.0

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": context_text}],
        )

        usage = response.usage
        input_tokens       = getattr(usage, "input_tokens", 0) or 0
        output_tokens      = getattr(usage, "output_tokens", 0) or 0
        cache_read_tokens  = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

        cost = _calculate_cost(
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )

        result_text = response.content[0].text

        _log_run(
            agent_name=agent_name,
            model=model,
            triggered_by=triggered_by,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            estimated_cost_usd=cost,
            status="SUCCESS",
        )

        print(
            f"[AGENT_CORE] {agent_name} OK | "
            f"{input_tokens}in / {output_tokens}out / "
            f"{cache_read_tokens}cr / {cache_write_tokens}cw | "
            f"${cost:.6f}"
        )
        return result_text

    except RuntimeError:
        raise

    except Exception as e:
        _log_run(
            agent_name=agent_name,
            model=model,
            triggered_by=triggered_by,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            estimated_cost_usd=cost,
            status="ERROR",
            error_message=str(e)[:500],
        )
        print(f"[AGENT_CORE] {agent_name} ERROR: {e}")
        raise


class AgentSpec:
    __slots__ = ("name", "model", "max_tokens", "body")

    def __init__(self, name: str, model: str, max_tokens: int, body: str) -> None:
        self.name = name
        self.model = model
        self.max_tokens = max_tokens
        self.body = body


def load_agent_spec(agent_name: str) -> AgentSpec:
    """
    Reads agents/{agent_name}.md, parses YAML frontmatter for model/max_tokens,
    and returns the body as the system prompt text.

    Raises FileNotFoundError if the spec file is missing — never silently falls
    back to an empty prompt.
    """
    path = _SPEC_DIR / f"{agent_name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"[AGENT_CORE] Spec file not found: {path}. "
            f"Cannot call agent '{agent_name}' without its spec."
        )
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2].lstrip('\n')
    return AgentSpec(
        name=agent_name,
        model=meta.get("model", _MODEL),
        max_tokens=int(meta.get("max_tokens", 2048)),
        body=body,
    )


def _call_from_spec(
    agent_name: str,
    context_text: str,
    triggered_by: str = "manual",
) -> str:
    """
    Loads the agent's MD spec from agents/{agent_name}.md and calls _call_agent().
    model and max_tokens are read from the spec frontmatter — no hardcoding at
    call sites. To change a model: edit the frontmatter, no Python change needed.
    """
    spec = load_agent_spec(agent_name)
    return _call_agent(
        agent_name=spec.name,
        system_prompt=spec.body,
        context_text=context_text,
        triggered_by=triggered_by,
        model=spec.model,
        max_tokens=spec.max_tokens,
    )


def get_cost_summary() -> dict:
    """
    Returns today's (24h) and 7-day cost summary by agent.
    Used by GET /api/agents/cost.
    """
    daily_cap = float(os.getenv("AGENT_DAILY_BUDGET_USD", "10.00"))
    now = datetime.now(timezone.utc)
    today_cutoff = now - timedelta(hours=24)
    week_cutoff = now - timedelta(days=7)

    db = SessionLocal()
    try:
        all_rows = db.query(AgentRunLog).filter(
            AgentRunLog.created_at >= week_cutoff
        ).order_by(AgentRunLog.id.desc()).all()

        today_rows = [r for r in all_rows if r.created_at.replace(tzinfo=timezone.utc) >= today_cutoff]

        def _summarize(rows: list) -> dict:
            by_agent: dict = {}
            total = 0.0
            for r in rows:
                ag = r.agent_name
                if ag not in by_agent:
                    by_agent[ag] = {"calls": 0, "usd": 0.0, "statuses": {}}
                by_agent[ag]["calls"] += 1
                by_agent[ag]["usd"] = round(by_agent[ag]["usd"] + r.estimated_cost_usd, 6)
                s = r.status
                by_agent[ag]["statuses"][s] = by_agent[ag]["statuses"].get(s, 0) + 1
                total += r.estimated_cost_usd
            return {"total_usd": round(total, 6), "by_agent": by_agent}

        today_summary = _summarize(today_rows)
        week_summary = _summarize(all_rows)

        last_10 = all_rows[:10]

        return {
            "ok": True,
            "today": {
                **today_summary,
                "budget_usd": daily_cap,
                "budget_pct": round(
                    (today_summary["total_usd"] / daily_cap) * 100, 1
                ) if daily_cap > 0 else 0.0,
            },
            "seven_day": week_summary,
            "last_10_calls": [
                {
                    "id": r.id,
                    "agent_name": r.agent_name,
                    "triggered_by": r.triggered_by,
                    "status": r.status,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cache_read_tokens": r.cache_read_tokens,
                    "cache_write_tokens": r.cache_write_tokens,
                    "estimated_cost_usd": round(r.estimated_cost_usd, 6),
                    "error_message": r.error_message,
                    "created_at": (
                        r.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                        if r.created_at else None
                    ),
                }
                for r in last_10
            ],
        }

    except Exception as e:
        print(f"[AGENT_CORE] get_cost_summary error: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
