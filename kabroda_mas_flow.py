# kabroda_mas_flow.py
# ==============================================================================
# KABRODA MULTI-AGENT SYSTEM (MAS) ORCHESTRATOR
# Purpose: Autonomous tactical analysis of locked Battlebox states.
# Enforces SSOT and Measured Move logic (No fixed 1:1 exits).
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
    tactical_brief: str = Field(description="The 'Ghost Lead' plain-English execution directive.")
    bias: str = Field(description="'LONG', 'SHORT', or 'NEUTRAL'")
    entry_price: float = Field(description="The exact trigger entry price.")
    stop_loss: float = Field(description="The exact stop loss price (the opposing trigger).")
    t1: float = Field(description="Target 1 (Distance between triggers added to entry).")
    t2: float = Field(description="Target 2 (Distance * 1.618 added to entry).")
    t3: float = Field(description="Target 3 (Distance * 2.618 added to entry).")

# --- 2. AGENT DEFINITIONS ---
def _build_agents() -> Dict[str, Agent]:
    # Using temperature 0.0 to eliminate LLM hallucination and force strict analytical reading.
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
        goal="Synthesize agent reports, enforce Kabroda risk parameters, and issue the final Executive Brief.",
        backstory=(
            "You are the Ghost Lead and final gatekeeper of capital. You audit the Macro Architect, "
            "Liquidity Scavenger, and Momentum Quant. If their intel conflicts, you reject the setup. "
            "You enforce the strict Kabroda Measured Move rule: Targets are derived exclusively from the distance "
            "between the breakout and breakdown triggers. You NEVER guess a 1:1 exit. You output a precise, tactical brief."
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

# --- 3. EXECUTION PIPELINE ---
def run_mas_analysis(symbol: str, session_id: str, date_key: str, battlebox_payload: Dict[str, Any]):
    """
    Ingests the locked SSOT payload from battlebox_pipeline.py, runs the CrewAI MAS,
    and injects the final Executive Brief into the PostgreSQL database.
    """
    print(f">>> MAS INITIATED: Orchestrating Tactical Analysis for {symbol} | {session_id}")
    
    # Extract the isolated SSOT segments to prevent context bleed
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

    agents = _build_agents()

    # Define Tasks explicitly passing only the required JSON context strings
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
        description=f"Analyze this Kinematic data: {json.dumps(quant_data)}. Confirm if momentum supports a breakout or if the market is exhausted.",
        expected_output="A summary of kinetic velocity and EMA alignment.",
        agent=agents["quant"]
    )

    task_cro = Task(
        description=(
            f"Review the reports from the Macro, Micro, and Quant agents. "
            f"The breakout trigger is {levels.get('breakout_trigger')} and the breakdown trigger is {levels.get('breakdown_trigger')}. "
            "Determine the final tactical execution. Enforce the Measured Move math for targets based on the distance between the two triggers. "
            "Output the final directive conforming strictly to the ExecutiveBrief JSON schema."
        ),
        expected_output="A strictly typed JSON object matching the ExecutiveBrief schema.",
        agent=agents["cro"],
        output_pydantic=ExecutiveBrief
    )

    # Initialize the Crew
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

# --- 4. DATABASE INJECTION ---
def _inject_brief_to_database(symbol: str, session_id: str, date_key: str, brief: ExecutiveBrief):
    db = SessionLocal()
    try:
        # Find the existing campaign log for this exact session lock
        log = db.query(CampaignLog).filter(
            CampaignLog.symbol == symbol,
            CampaignLog.session_id == session_id,
            CampaignLog.date_key == date_key
        ).first()

        if log:
            log.mas_executive_brief = brief.tactical_brief
            log.mas_approval_status = brief.approval_status
            # Automatically update the core math targets if the CRO adjusts them based on Measured Moves
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