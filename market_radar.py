# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v11.0 (TACTICAL BOOKMAP INJECTION)
# ==============================================================================
import os
import json
import asyncio
import battlebox_pipeline
import datetime
import gspread
from google.oauth2.service_account import Credentials
import liquidity_oracle
import live_telemetry

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _find_predator_stop(symbol, entry, direction, levels, verdict):
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))

    if "SOL" in symbol:
        if direction == "LONG": return pred_l if pred_l > 0 else entry * 0.98
        elif direction == "SHORT": return pred_h if pred_h > 0 else entry * 1.02
        return 0
        
    if "ETH" in symbol:
        eth_buffer = entry * 0.002
        if "JAILBREAK" in verdict: 
            return pred_l if direction == "LONG" and pred_l > 0 else (pred_h if direction == "SHORT" and pred_h > 0 else entry)
        else:
            if direction == "LONG":
                if pred_l > 0 and pred_l < entry: return pred_l - eth_buffer
                return entry - eth_buffer
            elif direction == "SHORT":
                if pred_h > 0 and pred_h > entry: return pred_h + eth_buffer
                return entry + eth_buffer

    if "SNIPER" in verdict:
        if direction == "SHORT": return entry * 1.017
        if direction == "LONG": return entry * 0.983

    btc_buffer = entry * 0.001 
    if direction == "LONG":
        if pred_l > 0 and pred_l < entry: return pred_l - btc_buffer
        return entry * 0.99 
    elif direction == "SHORT":
        if pred_h > 0 and pred_h > entry: return pred_h + btc_buffer
        return entry * 1.01 
        
    return 0

def _get_plan(symbol, static_entry, vector, tier, levels, true_target):
    plan = {"valid": True, "bias": vector, "entry": 0, "stop": 0, "targets": [0,0,0]}
    
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    entry_price = static_entry if "SNIPER" in tier else (bo if vector == "LONG" else bd)
    stop_price = _find_predator_stop(symbol, entry_price, vector, levels, tier)
    
    t2 = true_target if true_target > 0 else (dr if vector == "LONG" else ds)
    gap = abs(t2 - entry_price)

    if vector == "LONG":
        t1, t3 = entry_price + (gap * 0.618), entry_price + (gap * 1.618)
    else:
        t1, t3 = entry_price - (gap * 0.618), entry_price - (gap * 1.618)
    
    plan.update({"valid": True, "entry": entry_price, "stop": stop_price, "targets": [t1, t2, t3]})
    return plan

def _evaluate_oracle(anchor, l_plan, s_plan, liquidity_walls):
    l_note = "⚪ Oracle Standby"
    s_note = "⚪ Oracle Standby"
    macro_upper = []
    macro_lower = []
    
    status = liquidity_walls.get("status", "NONE")
    
    if status == "SUCCESS":
        raw = liquidity_walls.get("raw_data", {})
        try:
            asks = raw.get("asks", [])
            bids = raw.get("bids", [])
            
            max_ask = max([a for a in asks if a[0] < anchor * 1.05], key=lambda x: x[1], default=[0,0])
            max_bid = max([b for b in bids if b[0] > anchor * 0.95], key=lambda x: x[1], default=[0,0])
            
            top_asks = sorted(asks, key=lambda x: x[1], reverse=True)[:3]
            top_bids = sorted(bids, key=lambda x: x[1], reverse=True)[:3]
            
            macro_upper = [{"price": a[0], "vol": a[1]} for a in sorted(top_asks, key=lambda x: x[0])]
            macro_lower = [{"price": b[0], "vol": b[1]} for b in sorted(top_bids, key=lambda x: x[0], reverse=True)]
                
            if l_plan["valid"] and max_bid[0] > 0:
                if l_plan["stop"] > max_bid[0]:
                    l_plan["stop"] = max_bid[0] * 0.999
                    l_note = f"⚠️ VULNERABLE STOP SHIFTED: Behind {max_bid[0]} Wall"
                else:
                    l_note = "🛡️ STOP SECURE: Protected by Lower Wall"
                    
            if s_plan["valid"] and max_ask[0] > 0:
                if s_plan["stop"] < max_ask[0]:
                    s_plan["stop"] = max_ask[0] * 1.001
                    s_note = f"⚠️ VULNERABLE STOP SHIFTED: Behind {max_ask[0]} Wall"
                else:
                    s_note = "🛡️ STOP SECURE: Protected by Upper Wall"
                    
        except Exception as e:
            print(f"Oracle Eval Error: {e}")
            
    return l_note, s_note, macro_upper, macro_lower

def _enforce_risk_reward(plan, tier, note):
    if not plan["valid"] or plan["stop"] == 0:
        return plan, tier, note, 0.0
        
    risk = abs(plan["entry"] - plan["stop"])
    reward = abs(plan["targets"][1] - plan["entry"]) 
    
    rr_ratio = reward / risk if risk > 0 else 0.0
    
    if rr_ratio < 0.50:
        tier = "DEATH ZONE (BAD R:R)"
        note = f"⛔ TRADE INVALIDATED: Target 2 R:R is only {rr_ratio:.2f}."
        
    return plan, tier, note, rr_ratio

def _make_key(plan, verdict, macro_bias, micro_bias):
    if not plan["valid"]: return f"NEUTRAL|HOLD|0|0|0|0|0|{macro_bias}|{micro_bias}"
    clean_verdict = verdict.split(" (")[0]
    return f"{plan['bias']}|{clean_verdict}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|{macro_bias}|{micro_bias}"

def _generate_omni_roe(favored, fav_tier, macro_bias, micro_bias, campaign_state):
    camp_bias = campaign_state.get("bias", "NEUTRAL")
    
    if camp_bias == "SHORT" and favored == "LONG":
        return "RELOAD ZONE (TRAP): Macro Campaign is SHORT. Current bounce is a liquidity sweep. DO NOT LONG."
    if camp_bias == "LONG" and favored == "SHORT":
        return "RELOAD ZONE (TRAP): Macro Campaign is LONG. Current drop is a liquidity sweep. DO NOT SHORT."
    
    struct_text = ""
    if "CRATER" in fav_tier: struct_text = "DEATH ZONE (CRATER): Immediate wall absorbing energy. STAND DOWN."
    elif "EXHAUSTION" in fav_tier: struct_text = "DEATH ZONE (EXHAUSTION): Target is too far away. STAND DOWN."
    elif "BAD R:R" in fav_tier: struct_text = "DEATH ZONE: Risk to Reward is terrible. STAND DOWN."
    elif "SPEEDBUMP" in fav_tier: struct_text = "PRIMAL ZONE: Obstacle is a speedbump. Target confirmed. EXECUTE."
    elif "DIRECT MAGNET" in fav_tier: struct_text = "PRIMAL ZONE: Open runway to target. EXECUTE."
    elif "JAILBREAK" in fav_tier: struct_text = "JAILBREAK: Triggers outside walls. Trail stop loosely."
    elif "EXTENDED" in fav_tier: struct_text = "CAUTION: EXTENDED RUNWAY. Secure profits early."
    else: struct_text = "WAITING ON SYSTEM ALIGNMENT."

    return struct_text

def _build_dossier(symbol, anchor, levels, macro_bias, micro_bias, liquidity_walls, middle_brain, campaign_state):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))

    l_gap = middle_brain.get("long_gap", 0)
    l_tier = middle_brain.get("long_tier", "WAITING")
    l_target = middle_brain.get("long_target", 0)
    
    s_gap = middle_brain.get("short_gap", 0)
    s_tier = middle_brain.get("short_tier", "WAITING")
    s_target = middle_brain.get("short_target", 0)

    l_plan = _get_plan(symbol, bo, "LONG", l_tier, levels, l_target)
    s_plan = _get_plan(symbol, bd, "SHORT", s_tier, levels, s_target)
    
    l_note, s_note, macro_upper, macro_lower = _evaluate_oracle(anchor, l_plan, s_plan, liquidity_walls)
    
    l_plan, l_tier, l_note, l_rr = _enforce_risk_reward(l_plan, l_tier, l_note)
    s_plan, s_tier, s_note, s_rr = _enforce_risk_reward(s_plan, s_tier, s_note)
    
    favored = "NEUTRAL"
    if micro_bias == "BULLISH": favored = "LONG"
    elif micro_bias == "BEARISH": favored = "SHORT"
    
    fav_tier = l_tier if favored == "LONG" else (s_tier if favored == "SHORT" else "DEATH ZONE")
    
    color, sort_weight = "GRAY", 0
    if "PRIMAL" in fav_tier: color, sort_weight = "GREEN", 90
    elif "EXTENDED" in fav_tier: color, sort_weight = "YELLOW", 85
    elif "JAILBREAK" in fav_tier: color, sort_weight = "PURPLE", 80
    elif "DEATH ZONE" in fav_tier: color, sort_weight = "RED", 10
    
    roe_text = _generate_omni_roe(favored, fav_tier, macro_bias, micro_bias, campaign_state)

    return {
        "favored": favored, "color_code": color, "sort_weight": sort_weight, "roe": roe_text,
        "campaign_bias": campaign_state.get("bias", "NEUTRAL"),
        "liquidity_status": liquidity_walls.get("status", "NONE"),
        "macro_upper": macro_upper, "macro_lower": macro_lower,
        "raw_liquidity": liquidity_walls.get("raw_data", {}), 
        "long": {"gap": l_gap, "tier": l_tier, "plan": l_plan, "key": _make_key(l_plan, l_tier, macro_bias, micro_bias), "oracle_note": l_note, "rr": l_rr},
        "short": {"gap": s_gap, "tier": s_tier, "plan": s_plan, "key": _make_key(s_plan, s_tier, macro_bias, micro_bias), "oracle_note": s_note, "rr": s_rr}
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
        today_iso = now.strftime("%Y-%m-%d")                       
        
        existing_dates = sheet.col_values(1)
        existing_symbols = sheet.col_values(2)
        
        for i in range(min(len(existing_dates), len(existing_symbols))):
            if today_iso in str(existing_dates[i]) and existing_symbols[i] == radar_item["symbol"]:
                return

        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        favored = radar_item["favored"]
        plan_dir = "long" if favored == "LONG" else ("short" if favored == "SHORT" else "long")
        tier = radar_item[plan_dir]["tier"]
        plan = radar_item[plan_dir]["plan"]
        permission = "Yes" if ("PRIMAL" in tier or "MAGNET" in tier or "JAILBREAK" in tier) else "No"

        try:
            bo, bd, dr, ds, r30h, r30l = radar_item.get("indicator_string", "0,0,0,0,0,0").split(',')
        except:
            bo = bd = dr = ds = r30h = r30l = 0

        row_data = [
            timestamp, radar_item["symbol"], radar_item["macro_bias"], radar_item["micro_bias"], favored, tier, permission,
            plan.get("entry", 0), plan.get("stop", 0), plan.get("targets", [0,0,0])[0], plan.get("targets", [0,0,0])[1], plan.get("targets", [0,0,0])[2],
            round(radar_item[plan_dir].get("gap", 0), 2), "", "", "", "", 
            round(radar_item.get("long", {}).get("gap", 0), 2), round(radar_item.get("short", {}).get("gap", 0), 2),
            r30h, r30l, bo, bd, dr, ds 
        ]
        sheet.append_row(row_data)
    except Exception as e:
        print(f"❌ Failed to log to Google Sheets: {e}")

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
        liquidity_walls = res.get("battlebox", {}).get("liquidity_walls", {})
        campaign_state = res.get("battlebox", {}).get("campaign_state", {})
        middle_brain = res.get("battlebox", {}).get("middle_brain", {})
        
        macro_bias = context.get("macro_bias", "NEUTRAL")
        micro_bias = context.get("micro_bias", "NEUTRAL")

        dossier = _build_dossier(sym, price, levels, macro_bias, micro_bias, liquidity_walls, middle_brain, campaign_state)
        
        radar_item = {
            "symbol": sym, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "indicator_string": _make_indicator_string(levels), "full_intel": json.dumps(res, default=str),
            **dossier
        }
        
        radar_grid.append(radar_item)
        log_to_google_sheet(radar_item)
        
    radar_grid.sort(key=lambda x: x['sort_weight'], reverse=True)
    return radar_grid

async def generate_tactical_override(symbol: str, session_id: str = "us_ny_futures"):
    res = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if res.get("status") in ["CALIBRATING", "ERROR"]:
        return {"status": "ERROR", "message": "Radar is not locked. Wait for calibration."}

    live_liquidity = await liquidity_oracle.fetch_liquidation_magnets(symbol)
    live_telemetry_data = await live_telemetry.fetch_live_telemetry(symbol)

    levels = res["battlebox"]["levels"]
    campaign_state = res["battlebox"]["campaign_state"]
    macro_bias = res["battlebox"]["context"].get("macro_bias", "NEUTRAL")
    micro_bias = res["battlebox"]["context"].get("micro_bias", "NEUTRAL")
    price = res["price"]

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))

    asks = live_liquidity.get("raw_data", {}).get("asks", [])
    bids = live_liquidity.get("raw_data", {}).get("bids", [])
    fuel_mult = float(live_telemetry_data.get("fuel_multiplier", 1.0))

    l_gap, l_tier, l_target = battlebox_pipeline._analyze_true_gap(symbol, bo, dr, asks, "LONG", fuel_mult)
    s_gap, s_tier, s_target = battlebox_pipeline._analyze_true_gap(symbol, bd, ds, bids, "SHORT", fuel_mult)

    live_middle_brain = {
        "long_tier": l_tier, "long_target": l_target, "long_gap": l_gap,
        "short_tier": s_tier, "short_target": s_target, "short_gap": s_gap
    }

    live_dossier = _build_dossier(symbol, price, levels, macro_bias, micro_bias, live_liquidity, live_middle_brain, campaign_state)

    return {
        "status": "SUCCESS",
        "symbol": symbol,
        "live_dossier": live_dossier
    }