# test_new_format.py
# Fires one Senior Analyst call with realistic BTC data.
# Prints the full raw tactical_brief and narrative_text so you can
# verify the new ## section format before checking the War Room.
#
# Run: ! $env:ANTHROPIC_API_KEY = "sk-ant-..."; .venv/Scripts/python test_new_format.py

import os, sys, json

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: set ANTHROPIC_API_KEY first.")
    sys.exit(1)

from database import Base, engine
Base.metadata.create_all(bind=engine)

SYMBOL     = "BTC/USDT"
SESSION_ID = "us_ny_futures"
DATE_KEY   = "2026-05-27"

PAYLOAD = {
    "levels": {
        "breakout_trigger":  107850.0,
        "breakdown_trigger": 105200.0,
        "daily_resistance":  109200.0,
        "daily_support":     103800.0,
        "range30m_high":     107700.0,
        "range30m_low":      105350.0,
    },
    "context": {
        "macro_bias":     "BULLISH",
        "micro_bias":     "BULLISH",
        "micro_state":    "SWEET_ZONE",
        "1h_fuel_status": "BUILDING",
        "fuel_gauge": {
            "4H": {"trend": "BULLISH", "momentum": "POSITIVE", "rsi": 58.4},
            "1H": {"trend": "BULLISH", "momentum": "POSITIVE", "rsi": 54.1},
            "15M_JEWEL": {
                "kinematic_grade":         "PRIMED",
                "rsi":                     61.3,
                "ribbon_spread_pct":       0.38,
                "deviation_from_mean_pct": 0.22,
                "stoch_rsi":               "VALUE_HIGH",
            },
        },
        "kde_peaks": [
            {"price": 108200.0, "heat_score": 4.2,  "intensity": "LIGHT"},
            {"price": 109250.0, "heat_score": 8.7,  "intensity": "MODERATE"},
            {"price": 110500.0, "heat_score": 3.1,  "intensity": "LIGHT"},
            {"price": 112000.0, "heat_score": 18.5, "intensity": "HEAVY"},
            {"price": 104800.0, "heat_score": 6.3,  "intensity": "MODERATE"},
            {"price": 103200.0, "heat_score": 22.1, "intensity": "MAXIMUM"},
        ],
        "macro_structure": [
            {"type": "CYCLE_ORIGIN",     "price": 15476.0},
            {"type": "BULL_WAVE_1_TOP",  "price": 73738.0},
            {"type": "BEAR_WAVE_4_LOW",  "price": 49000.0},
            {"type": "BEAR_WAVE_4_TOP",  "price": 80000.0},
            {"type": "BULL_WAVE_5_PROJ", "price": 135000.0},
        ],
        "macro_environment": {
            "SPX":  "+0.4% — risk-on tone",
            "DXY":  "101.2 — dollar soft",
            "VIX":  "14.8 — low fear",
            "BIAS": "RISK-ON",
        },
    },
}

print("=" * 70)
print("SENIOR ANALYST FORMAT TEST — 2026-05-27")
print("=" * 70)
print("Calling run_mas_analysis() — live API call, ~15-20 seconds...")
print()

from kabroda_mas_flow import run_mas_analysis
result = run_mas_analysis(
    symbol=SYMBOL,
    session_id=SESSION_ID,
    date_key=DATE_KEY,
    battlebox_payload=PAYLOAD,
)

if result.get("status") != "SUCCESS":
    print(f"FAILED: {result.get('message')}")
    sys.exit(1)

brief = result["brief"]
print(f"Status:         {brief['approval_status']}")
print(f"Bias:           {brief['bias']}")
print(f"Entry:          ${brief['entry_price']:,.2f}")
print(f"Stop:           ${brief['stop_loss']:,.2f}")
print(f"T1/T2/T3:       ${brief['t1']:,.2f} / ${brief['t2']:,.2f} / ${brief['t3']:,.2f}")
print()

print("=" * 70)
print("NARRATIVE TEXT (cross-day memory / THE BIGGER PICTURE paragraph):")
print("=" * 70)
print(brief.get("narrative_text", "(empty)"))
print()

print("=" * 70)
print("TACTICAL BRIEF (tactical_text stored in DB / rendered in War Room):")
print("=" * 70)
print(brief.get("tactical_brief", "(empty)"))
print()

print("=" * 70)
print("FORMAT CHECKS:")
print("=" * 70)
tac = brief.get("tactical_brief", "")
sections = ["## TODAY'S ENERGY", "## TODAY'S TRADE SETUP", "## THE LEVELS",
            "## STAND DOWN IF", "## THE OTHER SIDE"]
for s in sections:
    found = s in tac
    print(f"  {'OK' if found else 'MISSING'} — {s}")

print()
print("Done.")
