from typing import Dict, Any

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from sse_engine import compute_dm_levels          # KTBB SSE ENGINE
from data_feed import build_auto_inputs_for_btc   # BINANCE AUTO INPUTS


app = FastAPI(title="KTBB – Trading Battle Box API")


# ------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ktbb", "version": "0.2.0"}


# ------------------------------------------------------------
# Pydantic model for JSON manual DMR
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


# ------------------------------------------------------------
# MAIN DASHBOARD UI (HTML)
# ------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def show_form():
    return """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>KTBB - Trading Battle Box</title>
    <style>
      body {
        margin: 0;
        padding: 0;
        background: #020617;
        color: #e5e7eb;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      .shell {
        max-width: 1200px;
        margin: 0 auto;
        padding: 24px 16px 40px;
      }
      h1 {
        font-size: 1.8rem;
        margin-bottom: 4px;
      }
      h2 {
        font-size: 1.3rem;
        margin-top: 0;
        margin-bottom: 4px;
      }
      h3 {
        font-size: 1.1rem;
        margin-top: 16px;
        margin-bottom: 8px;
      }
      p {
        margin-top: 4px;
        margin-bottom: 6px;
        color: #9ca3af;
        font-size: 0.9rem;
      }
      .grid {
        display: grid;
        grid-template-columns: 1.1fr 1fr;
        gap: 20px;
        margin-top: 20px;
      }
      .card {
        background: #020617;
        border-radius: 16px;
        border: 1px solid #111827;
        padding: 16px 18px 18px;
        box-shadow: 0 12px 40px rgba(15,23,42,0.8);
      }
      label {
        display: block;
        font-size: 0.8rem;
        color: #9ca3af;
        margin-bottom: 2px;
      }
      .row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }
      .row-2 {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      input[type="number"] {
        width: 100%;
        box-sizing: border-box;
        background: #020617;
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 6px 8px;
        color: #e5e7eb;
        font-size: 0.85rem;
      }
      input[type="number"]:focus {
        outline: none;
        border-color: #60a5fa;
        box-shadow: 0 0 0 1px #60a5fa33;
      }
      .button-row {
        margin-top: 14px;
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      button {
        border-radius: 999px;
        border: none;
        padding: 8px 16px;
        font-size: 0.85rem;
        cursor: pointer;
        font-weight: 500;
      }
      .btn-primary {
        background: #22c55e;
        color: #022c22;
      }
      .btn-secondary {
        background: #111827;
        color: #e5e7eb;
        border: 1px solid #1f2937;
      }
      .btn-accent {
        background: #3b82f6;
        color: #e5e7eb;
      }
      .btn-primary:hover {
        background: #16a34a;
      }
      .btn-secondary:hover {
        background: #020617;
      }
      .btn-accent:hover {
        background: #2563eb;
      }
      pre {
        background: #020617;
        border-radius: 10px;
        border: 1px solid #111827;
        padding: 10px 12px;
        font-size: 0.8rem;
        overflow-x: auto;
        white-space: pre-wrap;
      }
      .status-line {
        font-size: 0.8rem;
        color: #9ca3af;
        margin-top: 4px;
      }
      .status-ok {
        color: #22c55e;
      }
      .status-error {
        color: #f97316;
      }
      .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <h1>KABRODA TRADING</h1>
      <h2>Trading Battle Box - Daily Market Review</h2>
      <p>
        You can either enter your own shelves and FRVP levels manually, or click
        <strong>Auto-fill</strong> / <strong>Run Auto DMR</strong>
        to have the engine pull everything from Binance US (BTCUSDT).
      </p>

      <div class="grid">
        <!-- LEFT: MANUAL INPUT FORM -->
        <div class="card">
          <form id="dmr-form" method="post" action="/run-dmr">
            <h3>HTF Shelves (4H / 1H)</h3>
            <div class="row-2">
              <div>
                <label>4H Supply</label>
                <input name="h4_supply" type="number" step="0.1" required />
              </div>
              <div>
                <label>4H Demand</label>
                <input name="h4_demand" type="number" step="0.1" required />
              </div>
            </div>
            <div class="row-2" style="margin-top: 6px;">
              <div>
                <label>1H Supply</label>
                <input name="h1_supply" type="number" step="0.1" required />
              </div>
              <div>
                <label>1H Demand</label>
                <input name="h1_demand" type="number" step="0.1" required />
              </div>
            </div>

            <h3>Weekly VRVP</h3>
            <div class="row">
              <div>
                <label>VAL</label>
                <input name="weekly_val" type="number" step="0.1" required />
              </div>
              <div>
                <label>POC</label>
                <input name="weekly_poc" type="number" step="0.1" required />
              </div>
              <div>
                <label>VAH</label>
                <input name="weekly_vah" type="number" step="0.1" required />
              </div>
            </div>

            <h3>24h FRVP</h3>
            <div class="row">
              <div>
                <label>VAL</label>
                <input name="f24_val" type="number" step="0.1" required />
              </div>
              <div>
                <label>POC</label>
                <input name="f24_poc" type="number" step="0.1" required />
              </div>
              <div>
                <label>VAH</label>
                <input name="f24_vah" type="number" step="0.1" required />
              </div>
            </div>

            <h3>Morning FRVP</h3>
            <div class="row">
              <div>
                <label>VAL</label>
                <input name="morn_val" type="number" step="0.1" required />
              </div>
              <div>
                <label>POC</label>
                <input name="morn_poc" type="number" step="0.1" required />
              </div>
              <div>
                <label>VAH</label>
                <input name="morn_vah" type="number" step="0.1" required />
              </div>
            </div>

            <h3>30m Opening Range</h3>
            <div class="row-2">
              <div>
                <label>High</label>
                <input name="r30_high" type="number" step="0.1" required />
              </div>
              <div>
                <label>Low</label>
                <input name="r30_low" type="number" step="0.1" required />
              </div>
            </div>

            <div class="button-row">
              <button type="submit" class="btn-primary">Run Manual DMR</button>
              <button type="button" class="btn-secondary" onclick="autoFillFromBTC()">Auto-fill from Binance (BTCUSDT)</button>
              <button type="button" class="btn-accent" onclick="runAutoDMR()">Run Auto DMR (BTCUSDT)</button>
            </div>
          </form>
        </div>

        <!-- RIGHT: AUTO DMR PANEL -->
        <div class="card">
          <h3>Auto DMR - BTCUSDT</h3>
          <p class="status-line">
            Status:
            <span id="auto-status" class="status-line">waiting...</span>
          </p>
          <pre id="auto-output" class="mono">Click "Run Auto DMR (BTCUSDT)" to pull shelves, FRVPs and levels from Binance US.</pre>
        </div>
      </div>
    </div>

    <script>
      // expose functions on window so onclick="" can see them
      window.runAutoDMR = async function() {
        const statusEl = document.getElementById("auto-status");
        const outEl = document.getElementById("auto-output");

        statusEl.textContent = "running...";
        statusEl.className = "status-line";

        try {
          const res = await fetch("/api/dmr/run-auto", {
            method: "POST",
            headers: { "Accept": "application/json" }
          });

          if (!res.ok) {
            const txt = await res.text();
            statusEl.textContent = "error";
            statusEl.className = "status-line status-error";
            outEl.textContent = "Auto DMR failed (" + res.status + ").\\n" + txt;
            return;
          }

          const data = await res.json();
          statusEl.textContent = "ok - last run just now";
          statusEl.className = "status-line status-ok";

          const lev = data.levels;
          const htf = data.htf_shelves;
          const r30 = data.range_30m;

          const text =
            "Symbol: " + (data.symbol || "BTCUSDT") + "\\n" +
            "\\nDaily Support:  " + lev.daily_support +
            "\\nDaily Resistance: " + lev.daily_resistance +
            "\\nBreakout Trigger: " + lev.breakout_trigger +
            "\\nBreakdown Trigger: " + lev.breakdown_trigger +
            "\\n\\n30m Opening Range: " + r30.high + " - " + r30.low +
            "\\n\\nHTF Shelves:" +
            "\\n  4H Supply: " + htf.resistance[0].level +
            "\\n  4H Demand: " + htf.support[0].level +
            "\\n  1H Supply: " + htf.resistance[1].level +
            "\\n  1H Demand: " + htf.support[1].level;

          outEl.textContent = text;
        } catch (err) {
          statusEl.textContent = "error";
          statusEl.className = "status-line status-error";
          outEl.textContent = "Exception while running auto DMR:\\n" + err;
        }
      };

      window.autoFillFromBTC = async function() {
        const statusEl = document.getElementById("auto-status");
        const outEl = document.getElementById("auto-output");

        statusEl.textContent = "auto-filling...";
        statusEl.className = "status-line";

        try {
          const res = await fetch("/api/dmr/auto-inputs", {
            method: "GET",
            headers: { "Accept": "application/json" }
          });

          if (!res.ok) {
            const txt = await res.text();
            statusEl.textContent = "error on auto-fill";
            statusEl.className = "status-line status-error";
            outEl.textContent = "Auto-fill failed (" + res.status + ").\\n" + txt;
            return;
          }

          const data = await res.json();
          const inp = data.inputs || {};

          function setField(name, value) {
            const el = document.querySelector('input[name="' + name + '"]');
            if (el && value !== undefined && value !== null) {
              el.value = value;
            }
          }

          setField("h4_supply",  inp.h4_supply);
          setField("h4_demand",  inp.h4_demand);
          setField("h1_supply",  inp.h1_supply);
          setField("h1_demand",  inp.h1_demand);

          setField("weekly_val", inp.weekly_val);
          setField("weekly_poc", inp.weekly_poc);
          setField("weekly_vah", inp.weekly_vah);

          setField("f24_val",    inp.f24_val);
          setField("f24_poc",    inp.f24_poc);
          setField("f24_vah",    inp.f24_vah);

          setField("morn_val",   inp.morn_val);
          setField("morn_poc",   inp.morn_poc);
          setField("morn_vah",   inp.morn_vah);

          setField("r30_high",   inp.r30_high);
          setField("r30_low",    inp.r30_low);

          statusEl.textContent = "auto-fill complete - review then Run Manual DMR";
          statusEl.className = "status-line status-ok";
          outEl.textContent =
            "Form fields have been auto-filled from Binance (BTCUSDT).\\n" +
            "You can adjust any value and then click 'Run Manual DMR'.";
        } catch (err) {
          statusEl.textContent = "error on auto-fill";
          statusEl.className = "status-line status-error";
          outEl.textContent = "Exception while auto-filling:\\n" + err;
        }
      };
    </script>
  </body>
</html>
    """


# ------------------------------------------------------------
# HTML FORM HANDLER (Manual DMR)
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
    result = compute_dm_levels(
        h4_supply, h4_demand,
        h1_supply, h1_demand,
        weekly_val, weekly_poc, weekly_vah,
        f24_val, f24_poc, f24_vah,
        morn_val, morn_poc, morn_vah,
        r30_high, r30_low,
    )

    yaml_block = f"""triggers:
  breakout: {result['breakout_trigger']}
  breakdown: {result['breakdown_trigger']}

daily_resistance: {result['daily_resistance']}
daily_support: {result['daily_support']}

range_30m:
  high: {r30_high}
  low: {r30_low}
"""

    html = f"""
    <html>
      <head>
        <title>KTBB – DMR Result</title>
      </head>
      <body style="background:#020617; color:#e5e7eb;
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
                    border-radius:6px; border:1px solid #111827;">{yaml_block}</pre>

        <p><a href="/" style="color:#60a5fa;">&larr; Back to dashboard</a></p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


# ------------------------------------------------------------
# JSON: Manual DMR
# ------------------------------------------------------------
@app.post("/api/dmr/run-manual")
async def api_run_dmr_manual(req: ManualDMRRequest):
    result = compute_dm_levels(
        req.h4_supply, req.h4_demand,
        req.h1_supply, req.h1_demand,
        req.weekly_val, req.weekly_poc, req.weekly_vah,
        req.f24_val, req.f24_poc, req.f24_vah,
        req.morn_val, req.morn_poc, req.morn_vah,
        req.r30_high, req.r30_low,
    )
    return {
        "status": "success",
        "mode": "manual-json",
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
        },
    }


# ------------------------------------------------------------
# JSON: Auto inputs only (for Auto-fill button)
# ------------------------------------------------------------
@app.get("/api/dmr/auto-inputs")
async def api_auto_inputs():
    try:
        inputs = build_auto_inputs_for_btc()
        return {"status": "success", "symbol": "BTCUSDT", "inputs": inputs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


# ------------------------------------------------------------
# JSON: Full Auto DMR (for “Run Auto DMR” button)
# ------------------------------------------------------------
@app.post("/api/dmr/run-auto")
async def api_run_dmr_auto():
    try:
        inp = build_auto_inputs_for_btc()

        result = compute_dm_levels(
            inp["h4_supply"], inp["h4_demand"],
            inp["h1_supply"], inp["h1_demand"],
            inp["weekly_val"], inp["weekly_poc"], inp["weekly_vah"],
            inp["f24_val"], inp["f24_poc"], inp["f24_vah"],
            inp["morn_val"], inp["morn_poc"], inp["morn_vah"],
            inp["r30_high"], inp["r30_low"],
        )

        return {
            "status": "success",
            "mode": "auto",
            "symbol": "BTCUSDT",
            "inputs": inp,
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
                "high": inp["r30_high"],
                "low": inp["r30_low"],
            },
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})
