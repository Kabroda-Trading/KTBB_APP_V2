# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v11.4 (THE DECISION ENGINE)
# UPDATE: 10-Point Weighted Scorecard Integrated.
# FIX: Radar now strictly consumes locked KDE and Fib data from the Pipeline SSOT.
#      Live math bypasses removed to permanently eliminate intraday drift.
# ==============================================================================
import os
import json
import asyncio
import datetime
from datetime import timedelta
import pytz
import gspread
from google.oauth2.service_account import Credentials

import battlebox_pipeline
import gravity_math

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _run_gravity_audit(entry: float, vector: str, peaks: list, fibs: dict, levels: dict):
    """Scans the KDE Gravity Map for Shields, Blockades, and Targets."""
    audit = {
        "shield_price": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0,
        "has_shield": False, "airspace_clear": True
    }
    
    overhead = sorted([p for p in peaks if p["price"] > entry], key=lambda x: x["price"])
    underneath = sorted([p for p in peaks if p["price"] < entry], key=lambda x: x["price"], reverse=True)
    
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    gap = abs(bo - bd) or (entry * 0.02)
    
    if vector == "LONG":
        audit["t1"] = entry + (gap * 0.618)
        
        heavy_und = [p for p in underneath if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_und:
            audit["shield_price"] = heavy_und[0]["price"] * 0.998
            audit["has_shield"] = True
        else:
            audit["shield_price"] = entry * 0.99 
            
        heavy_ovr = [p for p in overhead if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_ovr and heavy_ovr[0]["price"] < audit["t1"]:
            audit["airspace_clear"] = False
            
        if heavy_ovr:
            audit["t2"] = heavy_ovr[0]["price"]
            audit["t3"] = heavy_ovr[1]["price"] if len(heavy_ovr) > 1 else fibs.get("ext_up_1618", entry * 1.05)
        else:
            audit["t2"] = fibs.get("ext_up_1272", entry * 1.03)
            audit["t3"] = fibs.get("ext_up_1618", entry * 1.05)

    elif vector == "SHORT":
        audit["t1"] = entry - (gap * 0.618)
        
        heavy_ovr = [p for p in overhead if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_ovr:
            audit["shield_price"] = heavy_ovr[0]["price"] * 1.002
            audit["has_shield"] = True
        else:
            audit["shield_price"] = entry * 1.01 
            
        heavy_und = [p for p in underneath if p["intensity"] in ["HEAVY", "MAXIMUM"]]
        if heavy_und and heavy_und[0]["price"] > audit["t1"]:
            audit["airspace_clear"] = False
            
        if heavy_und:
            audit["t2"] = heavy_und[0]["price"]
            audit["t3"] = heavy_und[1]["price"] if len(heavy_und) > 1 else fibs.get("ext_dn_1618", entry * 0.95)
        else:
            audit["t2"] = fibs.get("ext_dn_1272", entry * 0.97)
            audit["t3"] = fibs.get("ext_dn_1618", entry * 0.95)

    return audit

def _score_setup(vector: str, macro: str, micro: str, fuel: dict, audit: dict):
    """The 10-Point Kabroda Matrix."""
    score = 0
    max_score = 15
    checks = []

    if vector == "LONG" and macro == "BULLISH": score += 2; checks.append("Macro Aligned")
    elif vector == "SHORT" and macro == "BEARISH": score += 2; checks.append("Macro Aligned")
    
    if vector == "LONG" and micro == "BULLISH": score += 2; checks.append("Micro Aligned")
    elif vector == "SHORT" and micro == "BEARISH": score += 2; checks.append("Micro Aligned")

    f_1h = fuel.get("1H", {})
    if vector == "LONG" and f_1h.get("trend") == "BULLISH": score += 3; checks.append("1H EMA Fuel Aligned")
    elif vector == "SHORT" and f_1h.get("trend") == "BEARISH": score += 3; checks.append("1H EMA Fuel Aligned")

    f_4h = fuel.get("4H", {})
    if f_4h.get("momentum") == "POSITIVE" and vector == "LONG": score += 2; checks.append("4H MACD Momentum")
    elif f_4h.get("momentum") == "NEGATIVE" and vector == "SHORT": score += 2; checks.append("4H MACD Momentum")

    if audit["airspace_clear"]: score += 3; checks.append("Clear Structural Airspace")
    if audit["has_shield"]: score += 2; checks.append("Gravity Shield Protected")
    
    score += 1; checks.append("Primal Gap Confirmed")

    pct = (score / max_score) * 100
    
    if pct >= 86: grade = "GRADE A"
    elif pct >= 73: grade = "GRADE B"
    else: grade = "STAND DOWN"

    return grade, pct, checks

def _audit_predator_candle(plan: dict, raw_15m: list, session_id: str):
    """Audits the 9:30 AM EST 15m candle against the locked Kabroda structural map."""
    if session_id not in ["us_ny_futures", "us_ny_equity"]:
        return {"active": False}

    if not plan.get("valid"):
        return {"active": False}

    ny_tz = pytz.timezone("America/New_York")
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ny = now_utc.astimezone(ny_tz)

    target_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_ny.hour < 8:
        target_ny -= timedelta(days=1)

    target_end_ny = target_ny + timedelta(minutes=15)
    
    if now_ny < target_end_ny:
        return {"active": True, "status": "AWAITING", "message": "AWAITING PREDATOR RESOLUTION (09:45 EST)"}

    target_ts = int(target_ny.timestamp())
    predator_candle = next((c for c in raw_15m if int(c["time"]) == target_ts), None)

    if not predator_candle:
        return {"active": True, "status": "AWAITING", "message": "AWAITING CANDLE DATA..."}

    c_close = float(predator_candle["close"])
    c_high = float(predator_candle["high"])
    c_low = float(predator_candle["low"])
    stop = float(plan["stop"])
    t1 = float(plan["targets"][0])
    bias = plan["bias"]

    if bias == "SHORT":
        if c_close > stop:
            return {"active": True, "status": "ABORT", "message": "🔴 STRUCTURE COMPROMISED: Bulls claimed the Gravity Well."}
        elif c_low <= t1:
            return {"active": True, "status": "ABORT", "message": "🟡 MOVE EXHAUSTED: Volatility cleared Target 1 liquidity."}
        else:
            return {"active": True, "status": "EXECUTE", "message": "🟢 INTEGRITY CONFIRMED: Structure held. Set Trigger Order."}
            
    elif bias == "LONG":
        if c_close < stop:
            return {"active": True, "status": "ABORT", "message": "🔴 STRUCTURE COMPROMISED: Bears claimed the Gravity Well."}
        elif c_high >= t1:
            return {"active": True, "status": "ABORT", "message": "🟡 MOVE EXHAUSTED: Volatility cleared Target 1 liquidity."}
        else:
            return {"active": True, "status": "EXECUTE", "message": "🟢 INTEGRITY CONFIRMED: Structure held. Set Trigger Order."}

    return {"active": False}

def _build_dossier(symbol, anchor, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, raw_15m, session_id):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    favored = "NEUTRAL"
    if micro_bias == "BULLISH": favored = "LONG"
    elif micro_bias == "BEARISH": favored = "SHORT"
    elif fuel_gauge.get("1H", {}).get("trend") == "BULLISH": favored = "LONG"
    elif fuel_gauge.get("1H", {}).get("trend") == "BEARISH": favored = "SHORT"
    
    entry = bo if favored == "LONG" else bd
    
    if favored == "NEUTRAL":
        return {
            "favored": "NEUTRAL", "grade": "STAND DOWN", "score_pct": 0, "color_code": "GRAY",
            "briefing": "Market is in absolute neutral consolidation. Stand down.",
            "checks": [], "plan": {"valid": False}, "predator_audit": {"active": False}
        }
        
    audit = _run_gravity_audit(entry, favored, kde_peaks, macro_fibs, levels)
    grade, score_pct, checks = _score_setup(favored, macro_bias, micro_bias, fuel_gauge, audit)
    
    color = "GRAY"
    if grade == "GRADE A": color = "GREEN"
    elif grade == "GRADE B": color = "YELLOW"
    elif grade == "STAND DOWN": color = "RED"

    briefing = ""
    if grade == "GRADE A":
        briefing = "🟢 ELITE ALIGNMENT. Fuel and structure are synchronized. 70% Ride / 30% Scale recommended. Maximum capture."
    elif grade == "GRADE B":
        briefing = "🟡 STANDARD OPERATION. Executable, but strictly level-to-level. 30% Ride / 70% Scale. Secure early profits at Target 1."
    else:
        briefing = "🔴 ABORT. Insufficient fuel or heavy structural blockades detected. Probability is too low for execution."

    plan = {
        "valid": grade in ["GRADE A", "GRADE B"],
        "bias": favored,
        "entry": entry,
        "stop": audit["shield_price"],
        "targets": [audit["t1"], audit["t2"], audit["t3"]]
    }
    
    predator_audit = _audit_predator_candle(plan, raw_15m, session_id)
    
    key = f"{plan['bias']}|{grade}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|{macro_bias}|{micro_bias}" if plan["valid"] else ""

    return {
        "favored": favored, "grade": grade, "score_pct": score_pct, "color_code": color,
        "briefing": briefing, "checks": checks, "plan": plan, "key": key,
        "predator_audit": predator_audit
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

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}
    if data.get("status") == "CALIBRATING": return {"ok": True, "result": {"status": "CALIBRATING"}}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})
    raw_15m = data.get("battlebox", {}).get("raw_15m", [])
    
    macro_bias = context.get("macro_bias", "NEUTRAL")
    micro_bias = context.get("micro_bias", "NEUTRAL")
    fuel_gauge = context.get("fuel_gauge", {})
    kde_peaks = context.get("kde_peaks", [])
    macro_fibs = context.get("macro_fibs", {})

    dossier = _build_dossier(symbol, price, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, raw_15m, session_id)
    
    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "levels": levels, "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(data, default=str), **dossier
        }
    }

async def scan_sector(session_id="us_ny_futures"):
    radar_grid = []
    tasks = [battlebox_pipeline.get_live_battlebox(sym, "MANUAL", manual_id=session_id) for sym in TARGETS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, res in zip(TARGETS, results):
        if isinstance(res, Exception) or res.get("status") == "ERROR": continue
        if res.get("status") == "CALIBRATING":
            radar_grid.append({"symbol": sym, "status": "CALIBRATING", "sort_weight": 0})
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        context = res.get("battlebox", {}).get("context", {})
        raw_15m = res.get("battlebox", {}).get("raw_15m", [])
        
        macro_bias = context.get("macro_bias", "NEUTRAL")
        micro_bias = context.get("micro_bias", "NEUTRAL")
        fuel_gauge = context.get("fuel_gauge", {})

        kde_peaks = context.get("kde_peaks", [])
        macro_fibs = context.get("macro_fibs", {})

        dossier = _build_dossier(sym, price, levels, macro_bias, micro_bias, fuel_gauge, kde_peaks, macro_fibs, raw_15m, session_id)
        
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