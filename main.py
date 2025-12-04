from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Dict, Any

from sse_engine import compute_dm_levels   # <-- ENGINE IMPORT (DO NOT REMOVE)


app = FastAPI(title="KTBB – Trading Battle Box API")


# ------------------------------------------------------------
# HEALTH CHECK (for local + Render + monitors)
# ------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ktbb", "version": "0.1.0"}


# ------------------------------------------------------------
# ROOT: SHOW EXISTING INPUT FORM (KEPT AS-IS FOR NOW)
# ------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def show_form():
    return """
    <html>
      <head>
        <title>KTBB – DMR Prototype</title>
      </head>
      <body style="background:#0b0f19; color:#e5e7eb; 
                   font-family:system-ui,-apple-system,BlinkMacSystemFont,
                   'Segoe UI',sans-serif; padding:40px;">
        <h1>KTBB – Trading Battle Box</h1>
        <h2>Daily Market Review – Input</h2>

        <form method="post" action="/run-dmr">
          <h3>HTF Shelves</h3>
          <label>4H Supply: <input name="h4_supply" type="number" step="0.1" required /></label><br/>
          <label>4H Demand: <input name="h4_demand" type="number" step="0.1" required /></label><br/>
          <label>1H Supply: <input name="h1_supply" type="number" step="0.1" required /></label><br/>
          <label>1H Demand: <input name="h1_demand" type="number" step="0.1" required /></label><br/><br/>

          <h3>Weekly VRVP</h3>
          <label>Weekly VAL: <input name="weekly_val" type="number" step="0.1" required /></label><br/>
          <label>Weekly POC: <input name="weekly_poc" type="number" step="0.1" required /></label><br/>
          <label>Weekly VAH: <input name="weekly_vah" type="number" step="0.1" required /></label><br/><br/>

          <h3>24h FRVP</h3>
          <label>24h VAL: <input name="f24_val" type="number" step="0.1" required /></label><br/>
          <label>24h POC: <input name="f24_poc" type="number" step="0.1" required /></label><br/>
          <label>24h VAH: <input name="f24_vah" type="number" step="0.1" required /></label><br/><br/>

          <h3>Morning FRVP</h3>
          <label>Morning VAL: <input name="morn_val" type="number" step="0.1" required /></label><br/>
          <label>Morning POC: <input name="morn_poc" type="number" step="0.1" required /></label><br/>
          <label>Morning VAH: <input name="morn_vah" type="number" step="0.1" required /></label><br/><br/>

          <h3>30m Opening Range</h3>
          <label>30m High: <input name="r30_high" type="number" step="0.1" required /></label><br/>
          <label>30m Low: <input name="r30_low" type="number" step="0.1" required /></label><br/><br/>

          <button type="submit">Run DMR</button>
        </form>
      </body>
    </html>
    """


# ------------------------------------------------------------
# PROCESS FORM + RUN ENGINE (YOUR EXISTING HTML FLOW)
# ------------------------------------------------------------
@app.post("/run-dmr", response_class=HTMLResponse)
async def run_dmr(
    h4_supply: float = Form(...),
    h4_demand: float = Form(...),
    h1_supply: float = Form(...),
    h1_demand: float = Form(...),
    weekly_val: float = Form(...),
    weekly_poc: float = Form(...),
    weekly_vah: float = Form(...),
    f24_val: float = Form(...),
    f24_poc: float = Form(...),
    f24_vah: float = Form(...),
    morn_val: float = Form(...),
    morn_poc: float = Form(...),
    morn_vah: float = Form(...),
    r30_high: float = Form(...),
    r30_low: float = Form(...),
):

    # ---- Run the SSE / DMR engine ----
    result = compute_dm_levels(
        h4_supply, h4_demand,
        h1_supply, h1_demand,
        weekly_val, weekly_poc, weekly_vah,
        f24_val, f24_poc, f24_vah,
        morn_val, morn_poc, morn_vah,
        r30_high, r30_low
    )

    # ---- YAML OUTPUT BLOCK (includes 30m range) ----
    yaml_block = f"""triggers:
  breakout: {result['breakout_trigger']}
  breakdown: {result['breakdown_trigger']}

daily_resistance: {result['daily_resistance']}
daily_support: {result['daily_support']}

range_30m:
  high: {r30_high}
  low: {r30_low}
"""

    # ---- HTML OUTPUT PAGE ----
    html = f"""
    <html>
      <head>
        <title>KTBB – DMR Result</title>
      </head>
      <body style="background:#0b0f19; color:#e5e7eb;
                   font-family:system-ui,-apple-system,BlinkMacSystemFont,
                   'Segoe UI',sans-serif; padding:40px;">

        <h1>KTBB – Trading Battle Box</h1>
        <h2>Daily Market Review – Output</h2>

        <h3>Daily Levels</h3>
        <p><strong>Daily Support:</strong> {result['daily_support']}</p>
        <p><strong>Daily Resistance:</strong> {result['daily_resistance']}</p>
        <p><strong>Breakout Trigger:</strong> {result['breakout_trigger']}</p>
        <p><strong>Breakdown Trigger:</strong> {result['breakdown_trigger']}</p>

        <h3>30m Opening Range</h3>
        <p><strong>High:</strong> {r30_high}</p>
        <p><strong>Low:</strong> {r30_low}</p>

        <h3>HTF Shelves</h3>
        <p><strong>Resistance:</strong></p>
        <ul>
          <li>4H @ {result['htf_resistance'][0]['level']}</li>
          <li>1H @ {result['htf_resistance'][1]['level']}</li>
        </ul>

        <p><strong>Support:</strong></p>
        <ul>
          <li>4H @ {result['htf_support'][0]['level']}</li>
          <li>1H @ {result['htf_support'][1]['level']}</li>
        </ul>

        <h3>YAML Block</h3>
        <pre style="background:#020617; padding:10px; 
                    border-radius:6px; border:1px solid #111827;">
{yaml_block}
        </pre>

        <p><a href="/" style="color:#60a5fa;">&larr; Run another DMR</a></p>
      </body>
    </html>
    """

    return HTMLResponse(content=html)


# ------------------------------------------------------------
# NEW: JSON API ENDPOINT (SAME ENGINE, NO HTML)
# ------------------------------------------------------------

class ManualDMRRequest(BaseModel):
    h4_supply: float
    h4_demand: float
    h1_supply: float
    h1_demand: float
    weekly_val: float
    weekly_poc: float
    weekly_vah: float
    f24_val: float
    f24_poc: float
    f24_vah: float
    morn_val: float
    morn_poc: float
    morn_vah: float
    r30_high: float
    r30_low: float


@app.post("/api/dmr/run-manual")
async def api_run_dmr_manual(req: ManualDMRRequest) -> Dict[str, Any]:
    """
    JSON version of the DMR run.
    For now it uses the same manual inputs as the HTML form.
    Later we'll replace these inputs with automatically computed values.
    """
    result = compute_dm_levels(
        req.h4_supply, req.h4_demand,
        req.h1_supply, req.h1_demand,
        req.weekly_val, req.weekly_poc, req.weekly_vah,
        req.f24_val, req.f24_poc, req.f24_vah,
        req.morn_val, req.morn_poc, req.morn_vah,
        req.r30_high, req.r30_low
    )

    return {
        "status": "success",
        "input": req.dict(),
        "levels": {
            "daily_support": result["daily_support"],
            "daily_resistance": result["daily_resistance"],
            "breakout_trigger": result["breakout_trigger"],
            "breakdown_trigger": result["breakdown_trigger"],
        },
        "htf_shelves": {
            "resistance": result["htf_resistance"],
            "support": result["htf_support"],
        },
        "range_30m": {
            "high": req.r30_high,
            "low": req.r30_low,
        }
    }
