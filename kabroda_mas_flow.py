# kabroda_mas_flow.py
# ==============================================================================
# KABRODA MULTI-AGENT SYSTEM (MAS) ORCHESTRATOR
# Purpose: Autonomous tactical analysis of locked Battlebox states.
# Enforces SSOT, Measured Move logic, and RAG PostgreSQL Memory Injection.
# ==============================================================================

import json
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from database import SessionLocal, CampaignLog

# --- 1. PYDANTIC SCHEMAS (THE SSOT ENFORCERS) ---
class ExecutiveBrief(BaseModel):
    """The strict output schema for the Chief Risk Officer and Chief Content Officer."""
    approval_status: str = Field(description="Must be 'APPROVED', 'REJECTED', or 'WAITING_FOR_15M'")
    tactical_brief: str = Field(description="The 'Ghost Lead' plain-English execution directive. Must reference historical memory if provided.")
    bias: str = Field(description="'LONG', 'SHORT', or 'NEUTRAL'")
    entry_price: float = Field(description="The exact trigger entry price.")
    stop_loss: float = Field(description="The exact stop loss price (the opposing trigger).")
    t1: float = Field(description="Target 1 (Distance between triggers added to entry).")
    t2: float = Field(description="Target 2 (Distance * 1.618 added to entry).")
    t3: float = Field(description="Target 3 (Distance * 2.618 added to entry).")
    formatted_newsletter_md: str = Field(description="The final Markdown formatted newsletter article.")

class IntelAuditReport(BaseModel):
    """The strict output schema for the External Intel Auditor (three-source audit)."""
    gravity_verdict: str = Field(description="Gravity audit result. Must be 'CLEAR', 'BLOCKED', or 'HIGH_RISK'.")
    momentum_verdict: str = Field(description="Momentum audit result from the signal's timeframe JEWEL state. Must be 'BUILDING', 'EXHAUSTED', or 'MIXED'.")
    measured_move_t1: float = Field(description="Target 1 recalculated from the signal's own box (entry/stop). Long: entry + abs(entry-stop). Short: entry - abs(entry-stop).")
    overall_verdict: str = Field(description="The final synthesized verdict. Must be 'CONFIRMED', 'CAUTION', or 'REJECTED'.")
    reasoning: str = Field(description="A plain-English summary tying together the gravity, momentum, and measured-move findings.")

# --- 2. RAG MEMORY INJECTION (POSTGRESQL) ---
def _fetch_cro_memory(symbol: str) -> str:
    db = SessionLocal()
    try:
        logs = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.mas_approval_status == 'APPROVED',
            CampaignLog.closed_at.isnot(None) 
        ).order_by(CampaignLog.closed_at.desc()).limit(5).all()

        if not logs:
            return "MEMORY BANK: System is in a clean state. No recent closed trade data available. Execute standard Kabroda parameters."

        wins = 0
        losses = 0
        pnl_sum = 0.0

        for log in logs:
            if log.realized_pnl > 0: wins += 1
            else: losses += 1
            pnl_sum += log.realized_pnl

        memory_str = f"MEMORY BANK (Last {len(logs)} closed {symbol} trades): {wins} Wins, {losses} Losses. Net PnL: {pnl_sum:.2f}. "
        
        if losses > wins:
            memory_str += "CRITICAL WARNING: Recent performance is negative. You are bleeding capital in this market regime. You MUST tighten risk parameters, demand higher structural confluence, and be highly skeptical of breakouts."
        elif wins > losses:
            memory_str += "NOTE: Recent performance is highly positive. Maintain aggressive execution standards but guard against overconfidence."
        else:
            memory_str += "NOTE: Win rate is neutral. Maintain strict adherence to Kabroda parameters."

        return memory_str
    except Exception as e:
        print(f"RAG MEMORY ERROR: {e}")
        return "MEMORY BANK: Temporary connection failure. Rely entirely on live structural data."
    finally:
        db.close()

# --- 3. AGENT DEFINITIONS ---
def _build_agents() -> Dict[str, Agent]:
    llm = ChatAnthropic(temperature=0.0, model="claude-sonnet-4-6")

    macro_architect = Agent(
        role="Macro Structural Architect",
        goal="Analyze the 1D Elliott Wave structure, daily levels, and Global Macro Environment to determine directional bias.",
        backstory=(
            "You are a veteran macro analyst. You ignore intraday noise and operate strictly on daily structures and traditional finance risk posture (SPX, DXY, VIX). "
            "You read the provided Macro Structure arrays, Daily Support/Resistance levels, and the Macro Oracle data to determine if the "
            "market is in an impulsive trend or a corrective chop."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    liquidity_scavenger = Agent(
        role="Micro Liquidity Scavenger",
        goal="Analyze the Gravity Map KDE peaks to pinpoint heavy kinematic friction and magnetic zones.",
        backstory=(
            "You are an order flow specialist. You read Gravity Map Peaks (KDE Density). "
            "Your job is to identify if the airspace above a breakout trigger or below a breakdown trigger "
            "is blocked by a heavy Gravity Wall or Class 0 Beam."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    momentum_quant = Agent(
        role="Kinematic Momentum Quant",
        goal="Evaluate the 15m/1H/4H fuel gauges and EMA alignments to confirm if there is sufficient velocity.",
        backstory=(
            "You calculate velocity and exhaustion. You read the Fuel Gauge and Harmonic Matrix data. "
            "You determine if the market is primed for a move or mathematically overextended."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    chief_risk_officer = Agent(
        role="Chief Risk Officer (Ghost Lead)",
        goal="Synthesize agent reports, consult historical performance memory, enforce Kabroda risk parameters, and issue the final technical execution plan.",
        backstory=(
            "You are the Ghost Lead and final gatekeeper of capital. You audit the Macro Architect, "
            "Liquidity Scavenger, and Momentum Quant. If their intel conflicts, or if the Macro Risk Posture is hostile, you reject the setup. "
            "You enforce the strict Kabroda Measured Move rule: Targets are derived exclusively from the distance "
            "between the breakout and breakdown triggers. You NEVER guess a 1:1 exit. "
            "You MUST factor in the provided 'MEMORY BANK' data. If recent performance is poor, you must reject marginal setups."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )
    
    chief_content_officer = Agent(
        role="Chief Content Officer",
        goal="Convert the CRO's technical decision into an institutional-grade, Markdown-formatted newsletter brief.",
        backstory=(
            "You write for professional traders. No fluff. No generic financial advice. You take the strict math and rulings from the Chief Risk Officer "
            "and format them into an aggressive, readable 'Tactical Perimeter Brief' using terms like 'Kinetic Friction', 'Ghost Lead Verdict', and 'Tactical Perimeter'."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    intel_auditor = Agent(
        role="External Intel Auditor",
        goal="Ruthlessly cross-examine third-party trading signals across three Kabroda data sources: Gravity Map physics, Multi-Timeframe momentum, and Measured Move math.",
        backstory=(
            "You are a counter-intelligence analyst. You do not trust external signals. "
            "When handed a foreign intel packet (like MetaSignals), you run a three-source audit. "
            "First, you compare their Entry and Targets against Kabroda's KDE Gravity Peaks — if a target must push "
            "through a HEAVY or MAXIMUM (Class 0) Gravity Wall, the path is BLOCKED or HIGH_RISK. "
            "Second, you read the live Multi-Timeframe Confluence scan for the signal's own timeframe to judge whether "
            "momentum is BUILDING behind the trade or EXHAUSTED against it. "
            "Third, you discard their arbitrary RR targets and recalculate Target 1 from the signal's own box "
            "(the distance between its entry and stop), measured one box in the trade's direction."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    return {
        "macro": macro_architect,
        "micro": liquidity_scavenger,
        "quant": momentum_quant,
        "cro": chief_risk_officer,
        "cco": chief_content_officer,
        "auditor": intel_auditor
    }

# --- 4. EXECUTION PIPELINE ---
def run_mas_analysis(symbol: str, session_id: str, date_key: str, battlebox_payload: Dict[str, Any]):
    print(f">>> MAS INITIATED: Orchestrating Tactical Analysis for {symbol} | {session_id}")
    
    levels = battlebox_payload.get("levels", {})
    context = battlebox_payload.get("context", {})
    
    macro_data = {
        "macro_environment": context.get("macro_environment"),
        "macro_bias": context.get("macro_bias"),
        "daily_resistance": levels.get("daily_resistance"),
        "daily_support": levels.get("daily_support"),
        "macro_structure": context.get("macro_structure")
    }
    
    micro_data = {
        "breakout_trigger": levels.get("breakout_trigger"),
        "breakdown_trigger": levels.get("breakdown_trigger"),
        "kde_peaks": context.get("kde_peaks")
    }
    
    quant_data = {
        "fuel_gauge": context.get("fuel_gauge"),
        "1h_fuel_status": context.get("1h_fuel_status"),
        "micro_state": context.get("micro_state")
    }

    cro_memory_context = _fetch_cro_memory(symbol)
    agents = _build_agents()

    task_macro = Task(
        description=f"Analyze this Macro data: {json.dumps(macro_data)}. Specifically note the Global Macro Environment risk posture. Identify the directional bias and structural strength.",
        expected_output="A summary of the daily trend, macro structural alignment, and traditional finance risk posture.",
        agent=agents["macro"]
    )

    task_micro = Task(
        description=f"Analyze this Micro/Gravity data: {json.dumps(micro_data)}. Identify if the airspace around the triggers is clear or blocked by Gravity Walls.",
        expected_output="A summary of liquidity blockages and airspace clearance.",
        agent=agents["micro"]
    )

    task_quant = Task(
        description=f"Analyze this Kinematic data: {json.dumps(quant_data)}. Confirm if momentum supports a breakout or if the market is exhausted.",
        expected_output="A summary of kinetic velocity and EMA alignment.",
        agent=agents["quant"]
    )

    task_cro = Task(
        description=(
            f"{cro_memory_context}\n\n"
            f"Review the reports from the Macro, Micro, and Quant agents. "
            f"The breakout trigger is {levels.get('breakout_trigger')} and the breakdown trigger is {levels.get('breakdown_trigger')}. "
            "Determine the final tactical execution. Enforce the Measured Move math for targets based on the distance between the two triggers. "
            "Output a highly technical summary of the exact execution plan, including approval status, entry, stop, and exact mathematical targets. Do not output JSON."
        ),
        expected_output="A detailed technical summary of the execution plan.",
        agent=agents["cro"]
    )

    task_cco = Task(
        description=(
            "Take the Chief Risk Officer's technical summary and format it into a clean, aggressive Markdown article for professional subscribers. "
            "Use our terms: 'Kinetic Friction', 'Ghost Lead Verdict', and 'Tactical Perimeter'. "
            "Then, output the final directive conforming strictly to the ExecutiveBrief JSON schema, placing your Markdown article into the 'formatted_newsletter_md' field."
        ),
        expected_output="A strictly typed JSON object matching the ExecutiveBrief schema.",
        agent=agents["cco"],
        output_pydantic=ExecutiveBrief
    )

    trading_crew = Crew(
        agents=[agents["macro"], agents["micro"], agents["quant"], agents["cro"], agents["cco"]],
        tasks=[task_macro, task_micro, task_quant, task_cro, task_cco],
        process=Process.sequential,
        verbose=False
    )

    try:
        trading_crew.kickoff()
        brief_output: ExecutiveBrief = task_cco.output.pydantic

        if brief_output is None:
            raise ValueError("CCO task produced no Pydantic output — LLM may not have returned valid JSON.")

        _inject_brief_to_database(symbol, session_id, date_key, brief_output)
        return {"status": "SUCCESS", "brief": brief_output.dict()}

    except Exception as e:
        print(f"MAS EXECUTION ERROR: {e}")
        _mark_mas_error(symbol, session_id, date_key, str(e))
        return {"status": "ERROR", "message": str(e)}

# --- 5. EXTERNAL INTEL AUDIT PIPELINE ---
def audit_foreign_intel_pipeline(intel_packet: Dict[str, Any], battlebox_payload: Dict[str, Any], mtf_context: Dict[str, Any] = None):
    print(f">>> MAS INITIATED: Auditing Foreign Intel for {intel_packet.get('symbol')}")

    levels = battlebox_payload.get("levels", {})
    context = battlebox_payload.get("context", {})

    kabroda_data = {
        "kabroda_breakout_trigger": levels.get("breakout_trigger"),
        "kabroda_breakdown_trigger": levels.get("breakdown_trigger"),
        "gravity_kde_peaks": context.get("kde_peaks")
    }

    if mtf_context is None:
        mtf_context = {}

    agents = _build_agents()

    task_audit = Task(
        description=(
            f"Audit this Foreign Intel Packet: {json.dumps(intel_packet)}\n\n"
            f"Against this Internal Kabroda SSOT (gravity): {json.dumps(kabroda_data)}\n\n"
            f"And this live Multi-Timeframe Confluence scan (momentum): {json.dumps(mtf_context)}\n\n"
            "The signal carries a 'timeframe' field (e.g. '4H', '1H') and a 'bias' field (LONG or SHORT). "
            "Run a THREE-SECTION audit and report each section explicitly in your reasoning.\n\n"
            "=== SECTION 1 — GRAVITY AUDIT ===\n"
            "Check each of the signal's targets against the KDE gravity peaks. "
            "Flag any HEAVY or MAXIMUM gravity wall (especially Class 0 beams) sitting in the path between entry and a target. "
            "If a target must trade THROUGH such a wall, the path is BLOCKED (or HIGH_RISK if it is a MAXIMUM/Class 0 wall). "
            "If the airspace toward the targets is clear, it is CLEAR. Set 'gravity_verdict' to CLEAR, BLOCKED, or HIGH_RISK.\n\n"
            "=== SECTION 2 — MOMENTUM AUDIT ===\n"
            "Read the Multi-Timeframe scan's 'timeframes' map for the SIGNAL'S OWN timeframe "
            "(if the signal is 4H, read timeframes['4H']; if 1H, read timeframes['1H'], etc.). "
            "From that timeframe report: (a) is momentum BUILDING, BURNING, or EXHAUSTED — "
            "derive this from its stoch_rsi zone and curl (a zone of OVERBOUGHT against a LONG or OVERSOLD against a SHORT = EXHAUSTED; "
            "an aligned VALUE_HIGH/VALUE_LOW with the trade = BURNING; otherwise BUILDING); "
            "(b) is the fast EMA (ema21) above or below the slow EMA (ema55); "
            "(c) does that momentum SUPPORT the signal's trade direction? "
            "Set 'momentum_verdict' to BUILDING if momentum supports the direction, EXHAUSTED if momentum is spent or against it, "
            "or MIXED if the timeframe data is unavailable or the signals conflict.\n\n"
            "=== SECTION 3 — MEASURED MOVE AUDIT ===\n"
            "Discard the signal's arbitrary targets. Recalculate Target 1 from the signal's OWN box, using the timeframe of the signal "
            "(a 4H signal's box is its own 4H entry/stop range — NOT the 15M box). "
            "Let box = abs(entry_price - stop_loss). "
            "For a LONG signal: measured_move_t1 = entry_price + box. "
            "For a SHORT signal: measured_move_t1 = entry_price - box. "
            "Put this value in 'measured_move_t1'.\n\n"
            "=== FINAL ===\n"
            "Synthesize the three sections into 'overall_verdict': CONFIRMED (gravity clear AND momentum building), "
            "REJECTED (gravity HIGH_RISK or momentum EXHAUSTED against the trade), or CAUTION (anything mixed/borderline). "
            "Write a plain-English 'reasoning' summary covering all three sections."
        ),
        expected_output="A strictly typed JSON object matching the IntelAuditReport schema.",
        agent=agents["auditor"],
        output_pydantic=IntelAuditReport
    )

    audit_crew = Crew(
        agents=[agents["auditor"]],
        tasks=[task_audit],
        process=Process.sequential,
        verbose=False
    )

    try:
        result = audit_crew.kickoff()
        audit_output: IntelAuditReport = task_audit.output.pydantic
        return {"status": "SUCCESS", "report": audit_output.dict()}
    except Exception as e:
        print(f"INTEL AUDIT ERROR: {e}")
        return {"status": "ERROR", "message": str(e)}

# --- 6. OPERATOR COMMLINK (DIRECT INTERROGATION) ---
def interrogate_cro(symbol: str, user_message: str) -> str:
    """Directly interrogates the Chief Risk Officer regarding current session context."""
    db = SessionLocal()
    try:
        log = db.query(CampaignLog).filter(CampaignLog.symbol == symbol).order_by(CampaignLog.id.desc()).first()
        
        context_str = "No active campaign data found. You are analyzing raw market conditions."
        if log:
            context_str = (
                f"LATEST SESSION DATA:\n"
                f"Approval Status: {log.mas_approval_status}\n"
                f"Bias: {log.bias}\n"
                f"Entry Price: {log.entry_price}\n"
                f"Stop Loss: {log.stop_loss}\n"
                f"Target 1: {log.t1}\n"
                f"Executive Brief Authored by you: {log.mas_executive_brief}\n"
            )

        llm = ChatAnthropic(temperature=0.2, model="claude-sonnet-4-6")
        sys_prompt = SystemMessage(content=(
            "You are the Kabroda Chief Risk Officer (Ghost Lead). "
            "You are communicating directly with the human Operator in the Macro War Room. "
            "Answer the Operator's query directly, professionally, and concisely. "
            "You enforce the Single Source of Truth (SSOT). Rely ONLY on Kabroda Measured Move math, "
            "Gravity physics, and the context provided. Do not invent external data. "
            f"CURRENT CONTEXT FOR {symbol}: {context_str}"
        ))
        
        human_prompt = HumanMessage(content=user_message)

        response = llm.invoke([sys_prompt, human_prompt])
        return response.content

    except Exception as e:
        print(f"COMMLINK ERROR: {e}")
        return f"COMMLINK FAILURE: {str(e)}"
    finally:
        db.close()

# --- 7. ERROR MARKER ---
def _mark_mas_error(symbol: str, session_id: str, date_key: str, error_msg: str):
    db = SessionLocal()
    try:
        log = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.session_id == session_id,
            CampaignLog.date_key == date_key
        ).first()
        if log:
            log.mas_approval_status = "MAS_ERROR"
            log.mas_executive_brief = f"[SYSTEM ERROR] {error_msg[:500]}"
            db.commit()
    except Exception as e:
        print(f"MAS ERROR MARKER FAILED: {e}")
    finally:
        db.close()

# --- 8. DATABASE INJECTION ---
def _inject_brief_to_database(symbol: str, session_id: str, date_key: str, brief: ExecutiveBrief):
    db = SessionLocal()
    try:
        log = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.session_id == session_id,
            CampaignLog.date_key == date_key
        ).first()

        if not log:
            log = CampaignLog(
                symbol=symbol,
                session_id=session_id,
                date_key=date_key,
                bias=brief.bias,
                grade="MAS_AUTO",
                entry_price=brief.entry_price,
                stop_loss=brief.stop_loss,
                t1=brief.t1,
                t2=brief.t2,
                t3=brief.t3,
                total_contracts=0.0,
                status=brief.approval_status,
            )
            db.add(log)
            print(f"|| MAS OVERLAY SECURED || New CampaignLog created for {symbol} | {session_id}.")

        log.mas_executive_brief = brief.tactical_brief
        log.mas_approval_status = brief.approval_status
        log.bias = brief.bias
        log.entry_price = brief.entry_price
        log.stop_loss = brief.stop_loss
        log.t1 = brief.t1
        log.t2 = brief.t2
        log.t3 = brief.t3
        log.status = brief.approval_status
        log.formatted_newsletter = brief.formatted_newsletter_md

        db.commit()
        print(f"|| MAS OVERLAY SECURED || Executive Brief & Newsletter injected for {symbol}.")
    except Exception as e:
        print(f"MAS DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()