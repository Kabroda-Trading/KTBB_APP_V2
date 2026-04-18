# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v10.3 (PHASE 2 COINGLASS EVALUATION)
# UPDATE: Integrates Liquidity Oracle data locked by Phase 1.
# - Parses raw liquidity JSON to find massive Asks/Bids.
# - Assigns the ⭐ (Directional Star) to the heaviest gravitational pull.
# - Automatically adjusts Stop Losses to hide behind liquidity walls.
# ==============================================================================
import os
import json
import asyncio
import battlebox_pipeline
import datetime
import gspread
from google.oauth2.service_account import Credentials

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _get_thresholds(symbol):
    if "BTC" in symbol: return 0.5, 1.5, 2.25, True
    if "ETH" in symbol: return 0.8, 2.5, 3.50, True
    if "SOL" in symbol: return 1.5, 4.0, 6.00, True
    return 0.8, 2.5, 3.5, False

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

def _eval_side(symbol, trigger, wall, is_inverted):
    if trigger == 0: return 0.0, "WAITING"
    min_gap, primal_max, exhaust_max, allow_jb = _get_thresholds(symbol)
    
    gap_pct = (abs(wall - trigger) / trigger) * 100
    
    if is_inverted:
        if gap_pct < min_gap: return gap_pct, "DEATH ZONE (TOO TIGHT)"
        if allow_jb: return gap_pct, "JAILBREAK"
        return gap_pct, "DEATH ZONE (UNCONFIRMED)"
    else:
        if gap_pct < min_gap: return gap_pct, "DEATH ZONE (CHOP)"
        if gap_pct > exhaust_max: return gap_pct, "DEATH ZONE (EXHAUSTION)"
        if gap_pct > primal_max: return gap_pct, "EXTENDED MAGNET"
        return gap_pct, "MAGNET"

def _get_plan(symbol, static_entry, vector, tier, levels):
    plan = {"valid": False, "bias": vector, "entry": 0, "stop": 0, "targets": [0,0,0]}
    if "WAITING" in tier or "DEATH ZONE" in tier: return plan
    
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    entry_price = static_entry if "SNIPER" in tier else (bo if vector == "LONG" else bd)
    stop_price = _find_predator_stop(symbol, entry_price, vector, levels, tier)
    
    if "SNIPER" in tier:
        if vector == "LONG":
            t1, t2, t3 = entry_price * 1.02, dr, entry_price * 1.05
        else:
            t1, t2, t3 = entry_price * 0.98, ds, entry_price * 0.95
    else:
        gap = abs(bo - bd) or (entry_price * 0.02)
        if vector == "LONG":
            t1, t2, t3 = entry_price + (gap * 0.618), entry_price + gap, entry_price + (gap * 1.618)
        else:
            t1, t2, t3 = entry_price - (gap * 0.618), entry_price - gap, entry_price - (gap * 1.618)
    
    plan.update({"valid": True, "entry": entry_price, "stop": stop_price, "targets": [t1, t2, t3]})
    return plan

# --- PHASE 2 INJECTION: ORACLE EVALUATOR ---
def _evaluate_oracle(anchor, l_plan, s_plan, liquidity_walls):
    oracle_star = "NONE"
    l_note = "⚪ Oracle Standby"
    s_note = "⚪ Oracle Standby"
    
    status = liquidity_walls.get("status", "NONE")
    
    if status == "SUCCESS":
        raw = liquidity_walls.get("raw_data", {})
        try:
            # Defensively extract asks (above) and bids (below)
            asks = raw.get("asks", [])
            bids = raw.get("bids", [])
            
            # Find strongest magnet within 5% radius
            max_ask = max([a for a in asks if a[0] < anchor * 1.05], key=lambda x: x[1], default=[0,0])
            max_bid = max([b for b in bids if b[0] > anchor * 0.95], key=lambda x: x[1], default=[0,0])
            
            # Star Assignment: Requires 50% dominance in liquidity
            if max_ask[1] > (max_bid[1] * 1.5):
                oracle_star = "LONG"
            elif max_bid[1] > (max_ask[1] * 1.5):
                oracle_star = "SHORT"
                
            # Stop Loss Shielding
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
            
    return oracle_star, l_note, s_note
# -------------------------------------------

def _make_key(plan, verdict, macro_bias, micro_bias):
    if not plan["valid"]: return f"NEUTRAL|HOLD|0|0|0|0|0|{macro_bias}|{micro_bias}"
    clean_verdict = verdict.split(" (")[0]
    return f"{plan['bias']}|{clean_verdict}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}|{macro_bias}|{micro_bias}"

def _generate_omni_roe(favored, fav_tier, macro_bias, micro_bias):
    bias_text = ""
    if favored == "NEUTRAL":
        return "MARKET NEUTRAL: 168h bias is flat. Stand down and wait for momentum."
    elif favored == "LONG":
        if macro_bias == "BEARISH":
            bias_text = "⚠ COUNTER-TREND LONG: Micro is UP, but Macro is DOWN. Strict targets."
        elif macro_bias == "BULLISH":
            bias_text = "🟢 FULL ALIGNMENT: Both Macro and Micro are BULLISH. Runners permitted."
        else: 
            bias_text = "Micro momentum is Bullish. Execute strictly level-to-level."
    elif favored == "SHORT":
        if macro_bias == "BULLISH":
            bias_text = "⚠ COUNTER-TREND SHORT: Micro is DOWN, but Macro is UP. Strict targets."
        elif macro_bias == "BEARISH":
            bias_text = "🔴 FULL ALIGNMENT: Both Macro and Micro are BEARISH. Runners permitted."
        else: 
            bias_text = "Micro momentum is Bearish. Execute strictly level-to-level."

    struct_text = ""
    if "CHOP" in fav_tier or "TOO TIGHT" in fav_tier:
        struct_text = "WARNING: CHOP ZONE. Gap is too small. STAND DOWN."
    elif "EXHAUSTION" in fav_tier:
        struct_text = "WARNING: EXHAUSTION. Gap is massive. STAND DOWN."
    elif "JAILBREAK" in fav_tier:
        struct_text = "CRITICAL STRUCTURAL FAILURE. Triggers are OUTSIDE walls. Trail stop loosely."
    elif fav_tier == "EXTENDED MAGNET":
        struct_text = "CAUTION: EXTENDED RUNWAY. Secure profits early."
    elif fav_tier == "MAGNET":
        struct_text = "STANDARD OPERATION. Gap is in sweet spot. Take profit at the Wall."
    elif "SNIPER" in fav_tier:
        struct_text = "CRITICAL ALPHA. Price is touching Daily EMA. Execute."

    return f"{bias_text} | {struct_text}"

def _build_dossier(symbol, anchor, levels, macro_bias, micro_bias, liquidity_walls):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))

    l_gap, l_tier = _eval_side(symbol, bo, dr, (bo > dr and dr > 0))
    s_gap, s_tier = _eval_side(symbol, bd, ds, (bd < ds and ds > 0))

    l_plan = _get_plan(symbol, bo, "LONG", l_tier, levels)
    s_plan = _get_plan(symbol, bd, "SHORT", s_tier, levels)
    
    # Run Phase 2 Oracle Evaluation
    oracle_star, l_note, s_note = _evaluate_oracle(anchor, l_plan, s_plan, liquidity_walls)
    
    favored = "NEUTRAL"
    if micro_bias == "BULLISH": favored = "LONG"
    elif micro_bias == "BEARISH": favored = "SHORT"
    
    fav_tier = l_tier if favored == "LONG" else (s_tier if favored == "SHORT" else "DEATH ZONE")
    
    color, sort_weight = "GRAY", 0
    if "SNIPER" in fav_tier: color, sort_weight = ("NEON_GREEN" if favored=="LONG" else "NEON_RED"), 100
    elif fav_tier == "MAGNET": color, sort_weight = "GREEN", 90
    elif fav_tier == "EXTENDED MAGNET": color, sort_weight = "YELLOW", 85
    elif "JAILBREAK" in fav_tier: color, sort_weight = "PURPLE", 80
    elif "DEATH ZONE" in fav_tier: color, sort_weight = "RED", 10
    
    roe_text = _generate_omni_roe(favored, fav_tier, macro_bias, micro_bias)

    return {
        "favored": favored, "color_code": color, "sort_weight": sort_weight, "roe": roe_text,
        "oracle_star": oracle_star, 
        "liquidity_status": liquidity_walls.get("status", "NONE"),
        "long": {"gap": l_gap, "tier": l_tier, "plan": l_plan, "key": _make_key(l_plan, l_tier, macro_bias, micro_bias), "oracle_note": l_note},
        "short": {"gap": s_gap, "tier": s_tier, "plan": s_plan, "key": _make_key(s_plan, s_tier, macro_bias, micro_bias), "oracle_note": s_note}
    }

def log_to_google_sheet(radar_item):
    if radar_item.get("symbol") not in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        return
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
        permission = "Yes" if ("MAGNET" in tier or "SNIPER" in tier or "JAILBREAK" in tier) else "No"

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
        macro_bias = context.get("macro_bias", "NEUTRAL")
        micro_bias = context.get("micro_bias", "NEUTRAL")

        dossier = _build_dossier(symbol=sym, anchor=price, levels=levels, macro_bias=macro_bias, micro_bias=micro_bias, liquidity_walls=liquidity_walls)
        
        radar_item = {
            "symbol": sym, "price": price, "macro_bias": macro_bias, "micro_bias": micro_bias,
            "indicator_string": _make_indicator_string(levels), "full_intel": json.dumps(res, default=str),
            **dossier
        }
        
        radar_grid.append(radar_item)
        log_to_google_sheet(radar_item)
        
    radar_grid.sort(key=lambda x: x['sort_weight'], reverse=True)
    return radar_grid