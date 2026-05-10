# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v14.1 (HARMONIC SSOT UPGRADE)
# AUDIT: Replaced lagging ADX with Pipeline micro_state gating. Adjusted airspace.
# ==============================================================================
import os
import json
import asyncio
import datetime
from datetime import timedelta
import gspread
from google.oauth2.service_account import Credentials

import battlebox_pipeline
import gravity_math

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _run_measured_move_audit(entry: float, vector: str, bo: float, bd: float, peaks: list, is_sweet_zone: bool):
    """
    Calculates targets based strictly on the 15m Session Box (BO - BD).
    Airspace tolerance relaxes if pipeline declares SWEET_ZONE.
    """
    audit = {
        "stop": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0,
        "airspace_clear": True, "blocking_wall": 0.0
    }
    
    # 1. Define the Session Box
    box_size = abs(bo - bd)
    if box_size == 0: box_size = entry * 0.01 
    
    # 2. Extract Gravity Obstacles
    overhead = sorted([p for p in peaks if p["price"] > entry], key=lambda x: x["price"])
    underneath = sorted([p for p in peaks if p["price"] < entry], key=lambda x: x["price"], reverse=True)
    
    # Define required clearance (100% normally, 70% if SWEET_ZONE)
    clearance_multiplier = 0.7 if is_sweet_zone else 1.0

    if vector == "LONG":
        audit["stop"] = bd
        audit["t1"] = entry + box_size
        audit["t2"] = entry + (box_size * 1.618)
        audit["t3"] = entry + (box_size * 2.618)
        
        required_clearance_price = entry + (box_size * clearance_multiplier)
        heavy_ovr = [p for p in overhead if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_ovr and heavy_ovr[0]["price"] < required_clearance_price:
            audit["airspace_clear"] = False
            audit["blocking_wall"] = heavy_ovr[0]["price"]

    elif vector == "SHORT":
        audit["stop"] = bo
        audit["t1"] = entry - box_size
        audit["t2"] = entry - (box_size * 1.618)
        audit["t3"] = entry - (box_size * 2.618)
        
        required_clearance_price = entry - (box_size * clearance_multiplier)
        heavy_und = [p for p in underneath if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_und and heavy_und[0]["price"] > required_clearance_price:
            audit["airspace_clear"] = False
            audit["blocking_wall"] = heavy_und[0]["price"]

    return audit, box_size

def _score_setup(vector: str, macro: str, micro: str, fuel: dict, context: dict, entry: float, bo: float, bd: float, peaks: list):
    checks = []
    missing = []
    
    # Extract SSOT Pipeline State
    micro_state = context.get("micro_state", "CHOP")
    is_sweet_zone = micro_state in ["SWEET_ZONE", "SWEET_ZONE_BEAR"]
    
    audit, box_size = _run_measured_move_audit(entry, vector, bo, bd, peaks, is_sweet_zone)

    # GATE 1: Session Box Risk
    box_pct = (box_size / entry) * 100
    if box_pct > 1.5:
        return "STAND DOWN", 0, [], f"🔴 HALT: Session Box is too wide ({box_pct:.2f}%).", audit, box_size

    # GATE 2: Pipeline SSOT Enforcement
    if micro_state == "HOSTILE_CEILING":
        return "STAND DOWN", 0, [], "🔴 HALT: PIPELINE LOCKDOWN. 1H is bouncing into a 4H descending ceiling. Chop risk extreme.", audit, box_size
    if micro_state == "CHOP":
        return "STAND DOWN", 0, [], "🔴 HALT: PIPELINE LOCKDOWN. Timeframes are in harmonic conflict.", audit, box_size
    if micro_state == "PULLBACK":
        return "STAND DOWN", 0, [], "🟡 HALT: PIPELINE LOCKDOWN. 1H is refueling. Radar Breakouts are disabled until 1H aligns with 4H.", audit, box_size

    # GATE 3: Kinematics (Overextension)
    j_15m = fuel.get("15M_JEWEL", {})
    if j_15m.get("kinematic_grade") == "OVEREXTENDED":
        return "STAND DOWN", 0, [], "🔴 HALT: Price is overextended from 15m 200 SMA. Mean Reversion Risk.", audit, box_size

    # GATE 4: The Polarity Trap
    if vector == "SHORT" and micro_state == "SWEET_ZONE":
        return "STAND DOWN", 0, [], "🟡 HALT: Breaking Down, but Pipeline is in Bullish Sweet Zone. Do not short into macro support.", audit, box_size
    if vector == "LONG" and micro_state == "SWEET_ZONE_BEAR":
        return "STAND DOWN", 0, [], "🟡 HALT: Breaking Out, but Pipeline is in Bearish Sweet Zone. Do not buy into macro resistance.", audit, box_size

    # THE WEIGHTED SCORING MATRIX
    score = 0
    max_score = 10

    if is_sweet_zone:
        score += 6; checks.append("✓ Pipeline: Harmonic Sweet Zone Confirmed")
    else: missing.append("Pipeline: Harmonic Alignment Missing")

    if audit["airspace_clear"]:
        score += 4; checks.append("✓ Clear Airspace to Target 1")
    else: missing.append(f"Airspace Blocked by Wall at {audit['blocking_wall']}")

    pct = max(0, (score / max_score) * 100)
    
    grade = "STAND DOWN"
    rejection_reason = ""
    
    if pct == 100: 
        grade = "GRADE A"
    elif pct >= 60: 
        grade = "GRADE B"
    else: 
        grade = "STAND DOWN"
        rejection_reason = f"🔴 ABORT: Missing alignment: " + ", ".join(missing)

    return grade, pct, checks, rejection_reason, audit, box_size

def _build_dossier(symbol, price, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, context):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    favored = "NEUTRAL"
    if micro_bias == "BULLISH": favored = "LONG"
    elif micro_bias == "BEARISH": favored = "SHORT"
    
    entry = bo if favored == "LONG" else bd
    
    if favored == "NEUTRAL" or bo == 0 or bd == 0:
        return {
            "favored": "NEUTRAL", "grade": "STAND DOWN", "score_pct": 0, "color_code": "GRAY",
            "briefing": "Market is in absolute neutral consolidation or missing triggers.",
            "checks": [], "diagnostic_ledger": {}, "plan": {"valid": False}
        }
        
    grade, score_pct, checks, rejection_reason, audit, box_size = _score_setup(favored, macro_bias, micro_bias, fuel_gauge, context, entry, bo, bd, kde_peaks)
    
    color = "GRAY"
    briefing = ""
    
    if grade == "GRADE A": 
        color = "GREEN"
        briefing = "🟢 ELITE ALIGNMENT. Pipeline Harmonic Alignment confirmed. Airspace cleared."
    elif grade == "GRADE B": 
        color = "YELLOW"
        briefing = "🟡 STANDARD OPERATION. Executable, but expect friction. Scale out aggressively."
    elif grade == "STAND DOWN": 
        color = "RED"
        briefing = rejection_reason

    plan = {
        "valid": grade in ["GRADE A", "GRADE B"],
        "bias": favored,
        "entry": entry,
        "stop": audit["stop"],
        "targets": [audit["t1"], audit["t2"], audit["t3"]]
    }
    
    diagnostic_ledger = {
        "vector_direction": favored,
        "session_box_size": box_size,
        "pipeline_state": context.get("micro_state", "UNKNOWN"),
        "airspace_clear": audit["airspace_clear"]
    }
    if not plan["valid"]:
        diagnostic_ledger["rejection_reason"] = rejection_reason
    
    key = f"{plan['bias']}|{grade}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|{macro_bias}|{micro_bias}" if plan["valid"] else ""

    return {
        "favored": favored, "grade": grade, "score_pct": score_pct, "color_code": color,
        "briefing": briefing, "checks": checks, "diagnostic_ledger": diagnostic_ledger,
        "plan": plan, "key": key
    }

def log_to_google_sheet(radar_item):
    if radar_item.get("symbol") not in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]: return
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        google_creds_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not google_creds_str: return

        creds_dict = json.loads(google_creds_str)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open("Market Radar Tracking").sheet1

        now = datetime.datetime.now()
        day_str = str(now.day)
        today_iso = now.strftime("%Y-%m-%d")                       
        today_slash = now.strftime("%m/%d/%Y")                     
        today_text = now.strftime("%b ") + day_str + now.strftime(", %Y")  
        today_text_padded = now.strftime("%b %d, %Y")              
        
        existing_dates = sheet.col_values(1)
        existing_symbols = sheet.col_values(2)
        already_logged = False
        
        for i in range(min(len(existing_dates), len(existing_symbols))):
            date_cell = str(existing_dates[i])
            if (today_iso in date_cell or today_slash in date_cell or today_text in date_cell or today_text_padded in date_cell):
                if existing_symbols[i] == radar_item["symbol"]:
                    already_logged = True
                    break
                
        if already_logged: return

        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        plan = radar_item.get("plan", {})
        
        try:
            bo, bd, dr, ds, r30h, r30l = radar_item.get("indicator_string", "0,0,0,0,0,0").split(',')
        except:
            bo = bd = dr = ds = r30h = r30l = 0

        row_data = [
            timestamp,                  
            radar_item["symbol"],                  
            radar_item.get("macro_bias", ""),              
            radar_item.get("micro_bias", ""),              
            radar_item.get("favored", ""),                               
            radar_item.get("grade", ""),                                  
            "Yes" if plan.get("valid") else "No",                            
            plan.get("entry", 0),                  
            plan.get("stop", 0),                   
            plan.get("targets", [0,0,0])[0],       
            plan.get("targets", [0,0,0])[1],       
            plan.get("targets", [0,0,0])[2],       
            radar_item.get("score_pct", 0),                       
            "", "", "", "",                        
            0, 0,                             
            r30h, r30l, bo, bd, dr, ds                                     
        ]

        sheet.append_row(row_data)
    except Exception as e:
        print(f"❌ Failed to log to Google Sheets: {e}")

async def analyze_target(symbol):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id="us_ny_futures")
    if data.get("status") == "ERROR": return {"ok": False}
    if data.get("status") == "CALIBRATING": return {"ok": True, "result": {"status": "CALIBRATING"}}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})
    
    macro_bias = context.get("macro_bias", "NEUTRAL")
    micro_bias = context.get("micro_bias", "NEUTRAL")
    fuel_gauge = context.get("fuel_gauge", {})
    kde_peaks = context.get("kde_peaks", [])
    macro_fibs = context.get("macro_fibs", {})

    dossier = _build_dossier(symbol, price, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, context)
    
    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "levels": levels, "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(data, default=str), **dossier
        }
    }

async def scan_sector():
    radar_grid = []
    tasks = [battlebox_pipeline.get_live_battlebox(sym, "MANUAL", manual_id="us_ny_futures") for sym in TARGETS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, res in zip(TARGETS, results):
        if isinstance(res, Exception) or res.get("status") == "ERROR": continue
        if res.get("status") == "CALIBRATING":
            radar_grid.append({"symbol": sym, "status": "CALIBRATING", "sort_weight": 0})
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        context = res.get("battlebox", {}).get("context", {})
        
        macro_bias = context.get("macro_bias", "NEUTRAL")
        micro_bias = context.get("micro_bias", "NEUTRAL")
        fuel_gauge = context.get("fuel_gauge", {})
        kde_peaks = context.get("kde_peaks", [])
        macro_fibs = context.get("macro_fibs", {})

        dossier = _build_dossier(sym, price, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, context)
        
        radar_item = {
            "symbol": sym, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "indicator_string": _make_indicator_string(levels), "full_intel": json.dumps(res, default=str),
            **dossier
        }
        
        radar_item["sort_weight"] = dossier["score_pct"]
        radar_grid.append(radar_item)
        log_to_google_sheet(radar_item)
        
    radar_grid.sort(key=lambda x: x['sort_weight'], reverse=True)
    return radar_grid