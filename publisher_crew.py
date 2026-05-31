# publisher_crew.py
# ==============================================================================
# KABRODA CONTENT PUBLISHING ENGINE — Phase 6
# Runs synchronously at the end of run_mas_analysis(), after the Senior
# Analyst brief is written to the database.
#
# Pipeline (all Python — one LLM call total):
#   1. external_intel_reporter.fetch_market_intel()  → HTTP, no LLM
#   2. _fetch_archivist_data()                        → DB reads, no LLM
#   3. _build_publisher_context()                     → string assembly
#   4. agent_core._call_agent("publisher_agent")      → single LLM call
#   5. _write_newsletter_log()                        → DB write (DRAFT)
#
# PUBLIC API (signature frozen):
#   run_publisher(symbol, session_id, date_key, brief)
# ==============================================================================

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel

import agent_core
import external_intel_reporter
from database import CampaignLog, NewsletterLog, SessionLocal


# ==============================================================================
# SECTION 1 — OUTPUT SCHEMA
# ==============================================================================

class NewsletterBrief(BaseModel):
    headline: str
    newsletter_md: str


# ==============================================================================
# SECTION 2 — PUBLISHER SYSTEM PROMPT (CACHEABLE CONSTANT)
# ==============================================================================

PUBLISHER_SYSTEM_PROMPT = """\
You are the Editor-in-Chief of Kabroda Trading Intelligence — a premium \
daily financial newsletter read by institutional traders and sophisticated \
market participants. Your brief arrives each morning from the Senior Analyst \
immediately after the NY Futures session opens. Your job is to synthesize \
the analyst's execution data, live market sentiment, and recent performance \
history into a single authoritative newsletter article.

═══════════════════════════════════════════════════════
VOICE AND EDITORIAL STANDARD
═══════════════════════════════════════════════════════

Your editorial standard is Bloomberg Terminal commentary crossed with The \
Financial Times. Every sentence earns its place. Write for a reader who \
manages capital professionally, reads price charts fluently, and has zero \
patience for filler.

TONE RULES:
- Declarative and institutional — never breathless, never hyperbolic
- Structurally precise: name the exact dollar level, not "near resistance"
- Frame price action as structural reality, not prediction
- Conviction comes from structural evidence, not enthusiasm
- One idea per sentence. Short sentences beat long ones.

═══════════════════════════════════════════════════════
BANNED WORDS AND CONSTRUCTIONS (ABSOLUTE — NO EXCEPTIONS)
═══════════════════════════════════════════════════════

PREDICTIVE CERTAINTY — never use:
  "will", "won't", "is going to", "guaranteed", "certain", "definitely",
  "without a doubt", "must", "has to"

HEDGED SPECULATION — never use:
  "could see", "might see", "may see", "I think", "I believe",
  "in my opinion", "perhaps", "possibly"

RETAIL VERNACULAR — never use:
  "moon", "bull run", "bear market" (use structural descriptions instead),
  "rekt", "hodl", "dip", "pump", "dump"

TIME PROJECTIONS — never use:
  "by end of week", "within days", "soon", "shortly", "in the coming hours",
  "over the next X", "within X period"

REPLACEMENTS:
  Instead of "will break out" → "the structure supports a breakout attempt"
  Instead of "will go to $X" → "the path of least resistance points toward $X"
  Instead of "will hold" → "$X represents the structural floor for this session"
  Instead of "could rally" → "the setup is positioned for a rally if structure holds"

═══════════════════════════════════════════════════════
JARGON TRANSLATION TABLE (MANDATORY — NO EXCEPTIONS)
═══════════════════════════════════════════════════════

You receive raw internal Kabroda system data. Every piece of internal \
terminology must be translated before it appears in the newsletter. \
The reader does not know what a JEWEL is. Translate the value, not the label.

MOMENTUM / FUEL STATE:
  PRIMED            → "momentum aligned across timeframes — directional velocity is building"
  TANGLED           → "momentum is compressed across timeframes — no clear directional velocity"
  OVEREXTENDED      → "momentum is exhausted at current extension — reversion risk is elevated"
  SWEET_ZONE        → "constructive trend continuation environment — timeframes are aligned"
  PULLBACK          → "constructive retracement within the primary trend"
  HOSTILE_CEILING   → "conflicting timeframe structure — short-term strength against broader pressure"
  EXHAUSTION        → "trend fatigue — current velocity is structurally unsustainable"
  CHOP              → "low-conviction rotational price action — no structural directional commitment"
  SWEET_ZONE_BEAR   → "constructive bearish continuation environment — timeframes aligned to the downside"

APPROVAL STATUS:
  APPROVED          → "Session structure confirmed"
  REJECTED          → "Session structure rejected — setup does not meet confluence criteria"
  WAITING_FOR_15M   → "Session structure pending — awaiting short-term momentum confirmation"
  STAND_DOWN        → "Session vetoed — structural energy insufficient for a valid trade"

LEVELS AND STRUCTURE:
  Breakout Trigger      → "Upper Structural Boundary"
  Breakdown Trigger     → "Lower Structural Boundary"
  30M Range / Opening Range → "opening range"
  T1                    → "First Target"
  T2                    → "Second Target"
  T3                    → "Final Target" or "extension target"
  KDE peak / gravity wall   → "structural price density zone" or "key structural level"
  HEAVY gravity wall    → "major structural cluster"
  MAXIMUM gravity wall  → "critical structural pivot"
  Measured move         → "proportional structural projection (1:1 range extension)"
  Session lock          → "structural parameters established at the open"
  Structure_reasoning   → omit entirely from newsletter

DIRECTIONAL BIAS:
  LONG / bias=LONG  → "long-side setup" or "bullish structure"
  SHORT / bias=SHORT → "short-side setup" or "bearish structure"
  NEUTRAL           → "no directional commitment"

MACRO / WAVE (keep as-is — Elliott Wave is universally understood):
  BULL_WAVE_1 through _5, BEAR_WAVE_1 through _5 → write as-is
  CYCLE_ORIGIN          → "long-cycle structural origin"
  macro_bias BULLISH    → "dominant weekly trend: bullish"
  macro_bias BEARISH    → "dominant weekly trend: bearish"
  RISK-ON               → "risk appetite elevated — crypto-favorable environment"
  RISK-OFF              → "risk appetite suppressed — headwinds for risk assets"
  HIGH VOLATILITY       → "elevated volatility regime — capital preservation priority"

TIMEFRAME / INDICATOR LABELS (translate the value, never print the label):
  15M JEWEL / kinematic_grade → omit the label; describe the reading only
  1H / 4H                    → "hourly" / "four-hour"
  RSI zone OVERBOUGHT_EXTREME → "momentum at statistically extreme overbought"
  RSI zone OVERSOLD_EXTREME   → "momentum at statistically extreme oversold"
  RSI zone VALUE_ZONE         → "momentum in neutral territory — no exhaustion signal"
  exit_warning = YES          → "cross-timeframe exhaustion signal is active"
  exit_warning = NO           → omit — do not mention its absence
  ADX rising                  → "trend strength is building"
  ADX flat / not rising       → "trend strength is flat — momentum not accelerating"

═══════════════════════════════════════════════════════
NEWSLETTER STRUCTURE (WRITE IN THIS EXACT ORDER)
═══════════════════════════════════════════════════════

# [HEADLINE]
Concise, factual, maximum 12 words. No exclamation marks. No clickbait. \
The headline states the structural reality of the session.

[LEAD PARAGRAPH — 2-3 sentences]
The single most important thing a reader needs to know today. Specific price \
levels required in at least one sentence. No filler, no restatement of the \
headline.

---

## The Structural Picture

2-3 sentences. Where is price in the macro wave structure. What does that \
mean for today's session. Name at least one specific price level. A forward \
structural observation is required — what event is next on the structural map.

## Market Sentiment

2-3 sentences. State the Fear & Greed reading and whether it is historically \
elevated or depressed. State the total crypto market cap and 24-hour volume \
and whether capital is flowing in or out. Contextualise the sentiment reading \
against the structural setup — do they confirm or conflict.

If sentiment data is marked UNAVAILABLE: omit this section entirely. \
Do not write "data unavailable" — simply skip the section.

## Today's Energy

2-3 sentences. Translate the fuel gauge and momentum state into plain \
language. Is there velocity for a sustained move, or is the market compressed \
and waiting for a catalyst. Be specific about which timeframes are aligned \
and which are not.

## The Setup

**Status: [Session structure confirmed — LONG/SHORT | rejected | pending]**

2-3 sentences explaining the structural basis for the status. What makes \
this setup valid or what is missing. Specific price levels required.

[If APPROVED or WAITING_FOR_15M — include the full level table:]
**Upper Structural Boundary: $[exact price]**
**Lower Structural Boundary: $[exact price]**
**Entry: $[exact price]**
**Risk Level: $[exact price]** *(position invalidated on a confirmed close beyond this level)*
**First Target: $[exact price]**
**Second Target: $[exact price]**
**Final Target: $[exact price]**

[Allocation guidance in plain English:]
IF the brief shows "exit full position at T1" (fuel exhaustion conditions):
  Write: "Given the exhausted momentum environment, the structural playbook \
  calls for a full position exit at the First Target. No extension trades."

IF the brief shows 40/40/20 split:
  Write: "The momentum environment supports a scaled exit: 40% at the First \
  Target, 40% at the Second Target, trailing 20% toward the Final Target."

[If REJECTED:]
Write a clear, one-paragraph explanation of what structural criteria were \
not met. No targets needed.

[If STAND_DOWN:]
Write two focused paragraphs using the ## WHY THE SYSTEM STANDS DOWN and \
## WHAT WOULD CHANGE THIS content from the brief as your source material. \
Paragraph 1: Translate the specific veto condition into institutional language — \
what the structural map shows and why a trade cannot be issued today. \
Paragraph 2 — the educational component: what specific conditions would need to \
change for the next valid setup to emerge. This is the most valuable content \
in a no-trade newsletter. No target table needed.

## Performance Ledger

One sentence. State the most recent closed trade result and the 7-day \
win/loss record. Numbers only — no editorialising.

Example: "Most recent closed trade: Long from $97,450 — T1 hit. 7-day \
record: 3 wins, 1 loss."

If no closed trades exist yet: "Performance ledger: No closed trades on \
record. System in early deployment."

## Risk Advisory

Translate the STAND DOWN IF conditions from the Senior Analyst brief into \
plain institutional language. Bullet points. Specific price conditions only. \
No vague language.

Example:
- A confirmed 15-minute close below $96,800 negates the long structure
- BTC dominance dropping below 60% mid-session — risk-off signal

---

*This newsletter is published for informational and educational purposes \
only. It does not constitute investment advice or a solicitation to buy or \
sell any financial instrument. Structural observations are based on \
mathematical models and historical price data. Past structural setups do not \
guarantee future outcomes. All trading involves risk. Manage position size \
accordingly. © Kabroda Trading Intelligence.*

═══════════════════════════════════════════════════════
OUTPUT FORMAT (MANDATORY)
═══════════════════════════════════════════════════════

Return ONLY a valid JSON object. No markdown fences. No preamble. \
No explanation before or after. The `{` must be the absolute first character.

CRITICAL: Every line break inside newsletter_md must be written as \\n \
(backslash + n) — never embed a literal newline inside a JSON string value \
or the parser will crash.

{
  "headline": "<concise factual headline — max 12 words, no exclamation mark>",
  "newsletter_md": "<complete newsletter in Markdown — all sections from headline through disclaimer>"
}
"""


# ==============================================================================
# SECTION 3 — ARCHIVIST (DB READS — NO LLM)
# ==============================================================================

def _fetch_archivist_data(symbol: str) -> Dict[str, Any]:
    """
    Read-only DB queries. Returns recent closed trade results and weekly stats.
    All reads — no locking concern. Fails gracefully with sentinel values.
    """
    db = SessionLocal()
    try:
        last_closed = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.closed_at.isnot(None),
            )
            .order_by(CampaignLog.closed_at.desc())
            .first()
        )

        since = datetime.now(timezone.utc) - timedelta(days=7)
        weekly = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.closed_at.isnot(None),
                CampaignLog.closed_at >= since,
            )
            .all()
        )

        wins    = sum(1 for t in weekly if t.realized_pnl > 0)
        losses  = sum(1 for t in weekly if t.realized_pnl <= 0)
        net_pnl = sum(t.realized_pnl for t in weekly)

        if last_closed:
            outcome = last_closed.target_hit or ("WIN" if last_closed.realized_pnl > 0 else "LOSS")
            last_trade_str = (
                f"{last_closed.date_key} | {last_closed.bias} | "
                f"Entry ${last_closed.entry_price:,.2f} | "
                f"Outcome: {outcome} | PnL: {last_closed.realized_pnl:+.2f}R"
            )
        else:
            last_trade_str = "No closed trades on record — system in early deployment."

        return {
            "last_trade":          last_trade_str,
            "weekly_wins":         wins,
            "weekly_losses":       losses,
            "weekly_net_pnl":      round(net_pnl, 2),
            "weekly_trade_count":  len(weekly),
        }

    except Exception as e:
        print(f"[ARCHIVIST] DB query failed: {e}")
        return {
            "last_trade":          "Trade history temporarily unavailable.",
            "weekly_wins":         0,
            "weekly_losses":       0,
            "weekly_net_pnl":      0.0,
            "weekly_trade_count":  0,
        }
    finally:
        db.close()


# ==============================================================================
# SECTION 4 — CONTEXT BUILDER
# ==============================================================================

def _build_publisher_context(
    symbol: str,
    date_key: str,
    brief: Any,
    intel: Dict[str, Any],
    archivist: Dict[str, Any],
) -> str:
    fng = intel.get("fear_and_greed", {})
    cg  = intel.get("crypto_global", {})

    lines = [
        "=== PUBLISHER CONTEXT ===",
        f"Symbol: {symbol} | Date: {date_key}",
        "",
        "=== SENIOR ANALYST BRIEF (SOURCE MATERIAL) ===",
        f"Approval Status:  {brief.approval_status}",
        f"Bias:             {brief.bias}",
        f"Entry Price:      ${brief.entry_price:,.2f}",
        f"Stop Loss:        ${brief.stop_loss:,.2f}",
        f"First Target:     ${brief.t1:,.2f}",
        f"Second Target:    ${brief.t2:,.2f}",
        f"Final Target:     ${brief.t3:,.2f}",
        "",
        "FULL BRIEF (narrative + tactical):",
        brief.formatted_newsletter_md or brief.tactical_brief or "",
        "",
    ]

    # Fear & Greed
    lines.append("=== MARKET SENTIMENT (EXTERNAL INTEL) ===")
    if fng.get("status") == "OK":
        lines += [
            f"Fear & Greed Index: {fng['value']}/100 — {fng['classification']}",
            f"Contextual note: {fng['narrative']}",
        ]
    else:
        lines.append("Fear & Greed: UNAVAILABLE")

    # CoinGecko global
    if cg.get("status") == "OK":
        sign = "+" if (cg.get("market_cap_change_24h_pct") or 0) >= 0 else ""
        lines += [
            f"Total Crypto Market Cap: {cg['total_market_cap_formatted']} "
            f"({sign}{cg['market_cap_change_24h_pct']}% 24h)",
            f"24H Volume: {cg['total_volume_24h_formatted']}",
            f"BTC Dominance: {cg['btc_dominance_pct']}%",
        ]
    else:
        lines.append("Crypto Market Data: UNAVAILABLE")

    lines += [
        "",
        "=== ARCHIVIST REPORT ===",
        f"Most Recent Closed Trade: {archivist['last_trade']}",
        f"7-Day Record: {archivist['weekly_wins']}W / {archivist['weekly_losses']}L "
        f"| Net: {archivist['weekly_net_pnl']:+.2f}R "
        f"across {archivist['weekly_trade_count']} closed trades",
        "",
        "=== INSTRUCTIONS ===",
        "Synthesize all context above into a premium institutional newsletter per your system "
        "instructions. Translate all internal Kabroda terminology using the jargon table. "
        "Return ONLY the JSON object.",
    ]

    return "\n".join(lines)


# ==============================================================================
# SECTION 5 — JSON PARSER
# ==============================================================================

def _parse_newsletter(text: str) -> NewsletterBrief:
    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()
    brace_start = cleaned.find("{")
    brace_end   = cleaned.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start : brace_end + 1]
    data = json.loads(cleaned)
    return NewsletterBrief(**data)


# ==============================================================================
# SECTION 6 — DB WRITER
# ==============================================================================

def _write_newsletter_log(
    symbol: str,
    session_id: str,
    date_key: str,
    approval_status: str,
    newsletter: NewsletterBrief,
) -> None:
    db = SessionLocal()
    try:
        row = NewsletterLog(
            symbol=symbol,
            session_id=session_id,
            date_key=date_key,
            approval_status=approval_status,
            headline=newsletter.headline,
            newsletter_md=newsletter.newsletter_md,
            publish_status="DRAFT",
        )
        db.add(row)
        db.commit()
        print(f"|| PUBLISHER || Newsletter DRAFT written: '{newsletter.headline}'")
    except Exception as e:
        print(f"[PUBLISHER] DB write failed: {e}")
    finally:
        db.close()


# ==============================================================================
# SECTION 7 — MAIN ENTRY POINT
# ==============================================================================

def run_publisher(
    symbol: str,
    session_id: str,
    date_key: str,
    brief: Any,
) -> Dict[str, Any]:
    """
    Primary publisher pipeline. Called at the end of run_mas_analysis().
    brief is an ExecutiveBrief instance — typed as Any to avoid circular import.
    Returns a status dict. All exceptions are caught internally; caller wraps
    this in try/except so a publisher failure never propagates to MAS.
    """
    print(f">>> PUBLISHER: Initiating for {symbol} | {date_key}")

    # 1. Gather external intel (two HTTP GETs, timeout=5 each)
    intel = external_intel_reporter.fetch_market_intel()

    # 2. Archivist — DB reads, no LLM
    archivist = _fetch_archivist_data(symbol)

    # 3. Assemble context
    context_text = _build_publisher_context(symbol, date_key, brief, intel, archivist)

    # 4. Single LLM call — Publisher/Editor-in-Chief
    try:
        response_text = agent_core._call_agent(
            agent_name="publisher_agent",
            system_prompt=PUBLISHER_SYSTEM_PROMPT,
            context_text=context_text,
            triggered_by="mas_completion",
            max_tokens=6000,
        )
    except RuntimeError as e:
        print(f"[PUBLISHER] Budget blocked: {e}")
        return {"status": "BUDGET_BLOCKED", "message": str(e)}
    except Exception as e:
        print(f"[PUBLISHER] Agent call failed: {e}")
        return {"status": "ERROR", "message": str(e)}

    # 5. Parse — one retry on failure
    newsletter: Optional[NewsletterBrief] = None
    try:
        newsletter = _parse_newsletter(response_text)
    except Exception as parse_err:
        print(f"[PUBLISHER] Parse failed ({parse_err}). Retrying...")
        retry_context = (
            context_text
            + "\n\n[CORRECTION: Your previous response was not valid JSON. "
            "Return ONLY the JSON object with 'headline' and 'newsletter_md' fields. "
            "No markdown fences. No other text. The { must be the first character.]"
        )
        try:
            response_text2 = agent_core._call_agent(
                agent_name="publisher_agent",
                system_prompt=PUBLISHER_SYSTEM_PROMPT,
                context_text=retry_context,
                triggered_by="mas_completion_retry",
                max_tokens=6000,
            )
            newsletter = _parse_newsletter(response_text2)
        except Exception as e2:
            print(f"[PUBLISHER] Parse failed after retry: {e2}")
            return {"status": "ERROR", "message": f"Parse failed after retry: {e2}"}

    # 6. Write to newsletter_log as DRAFT
    _write_newsletter_log(symbol, session_id, date_key, brief.approval_status, newsletter)

    return {"status": "SUCCESS", "headline": newsletter.headline}
