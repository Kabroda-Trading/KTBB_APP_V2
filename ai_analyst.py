# ai_analyst.py
# ==============================================================================
# KABRODA AI ANALYST (GEMINI INTEGRATION)
# ==============================================================================
# Purpose: Consumes Research Lab JSON data and generates a "Tactical Synthesis"
# using the User's "Kinetic BattleBox" persona.
# ==============================================================================

import os
import time
import random
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
    Includes RETRY LOGIC for Rate Limits (429 Errors).
    """
    if not api_key:
        print(">>> AI ERROR: No API Key provided.")
        return "ERROR: No API Key provided. Check Account settings or Render Env Vars."

    genai.configure(api_key=api_key)
    
    # --- MODEL HUNTER ---
    # Prioritize 2.0 Flash (Fastest/Smartest available)
    target_model = 'gemini-2.0-flash'
    
    # Fallback logic for model selection
    try:
        model = genai.GenerativeModel(target_model)
    except:
        try:
            target_model = 'gemini-2.5-flash'
            model = genai.GenerativeModel(target_model)
        except:
            target_model = 'gemini-pro'
            model = genai.GenerativeModel(target_model)

    print(f">>> AI: Selected Model [{target_model}]")

    # Prepare Content
    prompt_content = f"""
    Analyze this Trading Simulation Data:
    {str(data_json)}
    
    Provide the Kabroda Kinetic DMR - Operational Order and Post-Game Analysis.
    """

    # --- RETRY LOGIC (THE FIX) ---
    max_retries = 3
    base_delay = 5 # Start with 5 seconds

    for attempt in range(max_retries):
        try:
            response = model.generate_content([
                KINETIC_SYSTEM_PROMPT,
                prompt_content
            ])
            return response.text

        except Exception as e:
            error_str = str(e)
            
            # Check for Rate Limit (429)
            if "429" in error_str or "quota" in error_str.lower():
                if attempt < max_retries - 1:
                    # Calculate backoff: 5s -> 10s -> 20s
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 2)
                    print(f">>> AI RATE LIMIT HIT. Cooling down for {int(sleep_time)}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(sleep_time)
                    continue # Try again
                else:
                    print(">>> AI RATE LIMIT EXHAUSTED.")
                    return "⚠️ AI BUSY: Rate limit hit. Please wait 30 seconds and try again."
            
            # Other errors (Auth, 404, etc) -> Fail immediately
            print(f">>> AI CRITICAL FAILURE: {error_str}")
            return f"AI ANALYSIS FAILED: {error_str}"
            
    return "AI ERROR: Unknown State"