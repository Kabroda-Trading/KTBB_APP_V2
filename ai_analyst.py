# ai_analyst.py
# ==============================================================================
# KABRODA AI ANALYST (GEMINI INTEGRATION)
# ==============================================================================
# Purpose: Consumes Research Lab JSON data and generates a "Tactical Synthesis"
# using the User's "Kinetic BattleBox" persona.
# ==============================================================================

import os
import google.generativeai as genai
from typing import Dict, Any

# --- THE PERSONA (YOUR GEMS) ---
KINETIC_SYSTEM_PROMPT = """
YOU ARE THE KABRODA KINETIC BATTLEBOX ANALYST.
Your job is to review a trading simulation JSON log and output a "Tactical Synthesis".

DATA PROVIDED:
1. Simulation Settings (Start Bal, Risk %, Risk Cap).
2. Equity Curve Stats (Max Drawdown, Win Rate, Ending Balance).
3. Session-by-Session Breakdown (Kinetic Scores, Trade Outcomes).

YOUR LOGIC (THE "LAWS"):
1. KINETIC MATH:
   - Energy (BPS): <100 is Super Coiled, >300 is Exhausted.
   - Space (Gap/ATR): >2.0x is Blue Sky, <1.0x is Blocked.
   - Momentum (Slope): >0.5 is Helping, Negative is Fighting.
   
2. OMEGA PROTOCOL:
   - Score < 40: DOGFIGHT (Defensive). If the user took a trade here and lost, SCOLD THEM.
   - Score > 71: FERRARI (Aggressive). Momentum overrides allowed.

3. OUTPUT STYLE:
   - Be direct, military, and professional.
   - Do not fluff. Use bullet points.
   - If they "Busted" (Balance < 0), analyze WHY (e.g., "Over-risking on low-quality setups").
   - Highlight the "Best Win" and the "Worst Decision."

FORMAT YOUR RESPONSE IN MARKDOWN.
Start with an "EXECUTIVE SUMMARY" of the simulation.
Then provide a "MONTHLY TACTICAL REVIEW".
End with a "CLOSING DIRECTIVE".
"""

def generate_report(data_json: Dict[str, Any], api_key: str) -> str:
    """
    Sends the Research Lab JSON to Gemini and gets the text report.
    """
    if not api_key:
        return "ERROR: No API Key provided."

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')

        # Convert JSON to string for the prompt
        prompt_content = f"""
        Analyze this Trading Simulation Data:
        {str(data_json)}
        
        Provide the Kabroda Kinetic DMR - Operational Order and Post-Game Analysis.
        """

        # Generate
        response = model.generate_content([
            KINETIC_SYSTEM_PROMPT,
            prompt_content
        ])
        
        return response.text

    except Exception as e:
        return f"AI ANALYSIS FAILED: {str(e)}"