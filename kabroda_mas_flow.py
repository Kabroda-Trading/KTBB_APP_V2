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
from langchain_openai import ChatOpenAI
from database import SessionLocal, CampaignLog

# --- 1. PYDANTIC SCHEMAS (THE SSOT ENFORCERS) ---
class ExecutiveBrief(BaseModel):
    """The strict output schema for the Chief Risk Officer."""
    approval_status: str = Field(description="Must be 'APPROVED', 'REJECTED', or 'WAITING_FOR_15M'")
    tactical_brief: str = Field(description="The 'Ghost Lead' plain-English execution directive. Must reference historical memory if provided.")
    bias: str = Field(description="'LONG', 'SHORT', or 'NEUTRAL'")
    entry_price: float = Field(description="The exact trigger entry price.")
    stop_loss: float = Field(description="The exact stop loss price (the opposing trigger).")
    t1: float = Field(description="Target 1 (Distance between triggers added to entry).")
    t2: float = Field(description="Target 2 (Distance * 1.618 added to entry).")
    t3: float = Field(description="Target 3 (Distance * 2.618 added to entry).")

# --- 2. RAG MEMORY INJECTION (POSTGRESQL) ---
def _fetch_cro_memory(symbol: str) -> str:
    """Queries PostgreSQL to build short-term tactical memory for the CRO."""
    db = SessionLocal()
    try:
        # Fetch the last 5 CLOSED trades to establish a win/loss context
        logs = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.mas_approval_status == 'APPROVED',
            CampaignLog.closed_at.isnot(None) # Only look at trades that have finished
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
        
        # Self-Correcting Directives
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
    # Temperature 0.0 eliminates hallucination.
    llm = ChatOpenAI(temperature=0.0, model="gpt-4o")

    macro_architect = Agent(
        role="Macro Structural Architect",
        goal="Analyze the 1D Elliott Wave structure and daily levels to determine the overarching directional bias.",
        backstory=(
            "You are a veteran macro analyst. You ignore intraday noise and operate strictly on daily structures. "
            "You read the provided Macro Structure arrays and Daily Support/Resistance levels to determine if the "
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
        goal="Synthesize agent reports, consult historical performance memory, enforce Kabroda risk parameters, and issue the final Executive Brief.",
        backstory=(
            "You are the Ghost Lead and final gatekeeper of capital. You audit the Macro Architect, "
            "Liquidity Scavenger, and Momentum Quant. If their intel conflicts, you reject the setup. "
            "You enforce the strict Kabroda Measured Move rule: Targets are derived exclusively from the distance "
            "between the breakout and breakdown triggers. You NEVER guess a 1:1 exit. "
            "You MUST factor in the provided 'MEMORY BANK' data. If recent performance is poor, you must reject marginal setups."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    return {
        "macro": macro_architect,
        "micro": liquidity_scavenger,
        "quant": momentum_quant,
        "cro": chief_risk_officer
    }

# --- 4. EXECUTION PIPELINE ---
def run_mas_analysis(symbol: str, session_id: str, date_key: str, battlebox_payload: Dict[str, Any]):
    """
    Ingests the locked SSOT payload, builds RAG memory, runs CrewAI MAS,
    and injects the final Executive Brief into PostgreSQL.
    """
    print(f">>> MAS INITIATED: Orchestrating Tactical Analysis for {symbol} | {session_id}")
    
    # Extract isolated SSOT segments
    levels = battlebox_payload.get("levels", {})
    context = battlebox_payload.get("context", {})
    
    macro_data = {
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

    # Fetch PostgreSQL Memory
    cro_memory_context = _fetch_cro_memory(symbol)

    agents = _build_agents()

    task_macro = Task(
        description=f"Analyze this Macro data: {json.dumps(macro_data)}. Identify the directional bias and structural strength.",
        expected_output="A summary of the daily trend and macro structural alignment.",
        agent=agents["macro"]
    )

    task_micro = Task(
        description=f"Analyze this Micro/Gravity data: {json.dumps(micro_data)}. Identify if the airspace around the triggers is clear or blocked by Gravity Walls.",
        expected_output="A summary of liquidity blockages and airspace clearance.",
        agent=agents["micro"]
    )

    task_quant = Task(
        description=f"Analyze this Kinematic data: {json.bleshooting(quant_data)}. Confirm if momentum supports a breakout or if the market is exhausted.",
        expected_output="A summary of kinetic velocity and EMA alignment.",
        agent=agents["quant"]
    )

    task_cro = Task(
        description=(
            f"{cro_memory_context}\n\n"
            f"Review the reports from the Macro, Micro, and Quant agents. "
            f"The breakout trigger is {levels.get('breakout_trigger')} and the breakdown trigger is {levels.get('breakdown_trigger')}. "
            "Determine the final tactical execution. Enforce the Measured Move math for targets based on the distance between the two triggers. "
            "Output the final directive conforming strictly to the ExecutiveBrief JSON schema."
        ),
        expected_output="A strictly typed JSON object matching the ExecutiveBrief schema.",
        agent=agents["cro"],
        output_pydantic=ExecutiveBrief
    )

    # Initialize Crew
    trading_crew = Crew(
        agents=[agents["macro"], agents["micro"], agents["quant"], agents["cro"]],
        tasks=[task_macro, task_micro, task_quant, task_cro],
        process=Process.sequential,
        verbose=False
    )

    # Fire the MAS Pipeline
    try:
        result = trading_crew.kickoff()
        brief_output: ExecutiveBrief = task_cro.output.pydantic
        
        _inject_brief_to_database(symbol, session_id, date_key, brief_output)
        return {"status": "SUCCESS", "brief": brief_output.dict()}

    except Exception as e:
        print(f"MAS EXECUTION ERROR: {e}")
        return {"status": "ERROR", "message": str(e)}

# --- 5. DATABASE INJECTION ---
def _inject_brief_to_database(symbol: str, session_id: str, date_key: str, brief: ExecutiveBrief):
    db = SessionLocal()
    try:
        log = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.session_id == session_id,
            CampaignLog.date_key == date_key
        ).first()

        if log:
            log.mas_executive_brief = brief.tactical_brief
            log.mas_approval_status = brief.approval_status
            log.bias = brief.bias
            log.entry_price = brief.entry_price
            log.stop_loss = brief.stop_loss
            log.t1 = brief.t1
            log.t2 = brief.t2
            log.t3 = brief.t3
            log.status = brief.approval_status
            
            db.commit()
            print(f"|| MAS OVERLAY SECURED || Executive Brief injected for {symbol}.")
        else:
            print(f"|| MAS WARNING || No CampaignLog found for {symbol} | {session_id} to inject brief.")
    except Exception as e:
        print(f"MAS DATABASE INJECTION ERROR: {e}")
    finally:
        db.close()