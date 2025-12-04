from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Dict, Any
from data_feed import build_auto_inputs_for_btc

from sse_engine import compute_dm_levels   # <-- ENGINE IMPORT (DO NOT REMOVE)


app = FastAPI(title="KTBB – Trading Battle Box API")


# ------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ktbb", "version": "0.2.0"}


# ------------------------------------------------------------
# ROOT DASHBOARD – INPUT FORM
# ------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def show_form():
    # Dashboard-style layout with manual inputs + Auto DMR button
    return """
    <html>
      <head>
        <title>KTBB – Trading Battle Box</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body style="margin:0; background:#020617; color:#e5e7eb; 
                   font-family:system-ui,-apple-system,BlinkMacSystemFont,
                   'Segoe UI',sans-serif;">
        <!-- Top Nav -->
        <div style="background:#020617; border-bottom:1px solid #111827; padding:14px 24px;
                    display:flex; align-items:center; justify-content:space-between;">
          <div style="display:flex; align-items:center; gap:8px;">
            <div style="width:26px; height:26px; border-radius:999px; 
                        background:linear-gradient(135deg,#22c55e,#0ea5e9);"></div>
            <div>
              <div style="font-weight:600; letter-spacing:0.04em; text-transform:uppercase;
                          font-size:11px; color:#9ca3af;">
                Kabroda Trading
              </div>
              <div style="font-weight:600; font-size:15px;">Trading Battle Box</div>
            </div>
          </div>
          <div style="font-size:12px; color:#9ca3af;">
            Environment: <span style="color:#22c55e;">DEV</span>
          </div>
        </div>

        <!-- Content Layout -->
        <div style="display:flex; flex-wrap:wrap; padding:24px; gap:24px;">

          <!-- LEFT COLUMN: INPUT FORM -->
          <div style="flex:1 1 320px; max-width:520px;">
            <div style="margin-bottom:16px;">
              <h2 style="margin:0 0 4px 0; font-size:22px;">Daily Market Review</h2>
              <p style="margin:0; font-size:13px; color:#9ca3af;">
                You can either enter your own levels manually, or click
                <strong>Run Auto DMR (BTCUSDT)</strong> to let the engine pull
                everything from Binance US.
              </p>
            </div>

            <form method="post" action="/run-dmr" 
                  style="background:#020617; border:1px solid #111827; border-radius:16px;
                         padding:18px 18px 20px 18px; display:flex; flex-direction:column; gap:16px;">

              <div>
                <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:6px;">
                  HTF Shelves (4H / 1H)
                </div>
                <div style="display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px;">
                  <label style="font-size:12px;">
                    4H Supply
                    <input name="h4_supply" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    4H Demand
                    <input name="h4_demand" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    1H Supply
                    <input name="h1_supply" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    1H Demand
                    <input name="h1_demand" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                </div>
              </div>

              <div style="height:1px; background:#111827;"></div>

              <div>
                <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:6px;">
                  Weekly VRVP
                </div>
                <div style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;">
                  <label style="font-size:12px;">
                    VAL
                    <input name="weekly_val" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    POC
                    <input name="weekly_poc" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    VAH
                    <input name="weekly_vah" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                </div>
              </div>

              <div>
                <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:6px;">
                  24h FRVP
                </div>
                <div style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;">
                  <label style="font-size:12px;">
                    VAL
                    <input name="f24_val" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    POC
                    <input name="f24_poc" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    VAH
                    <input name="f24_vah" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                </div>
              </div>

              <div>
                <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:6px;">
                  Morning FRVP
                </div>
                <div style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;">
                  <label style="font-size:12px;">
                    VAL
                    <input name="morn_val" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    POC
                    <input name="morn_poc" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    VAH
                    <input name="morn_vah" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                </div>
              </div>

              <div>
                <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:6px;">
                  30m Opening Range
                </div>
                <div style="display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px;">
                  <label style="font-size:12px;">
                    High
                    <input name="r30_high" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                  <label style="font-size:12px;">
                    Low
                    <input name="r30_low" type="number" step="0.1" required
                           style="margin-top:2px; width:100%; padding:6px 8px;
                                  background:#020617; border-radius:8px;
                                  border:1px solid #1f2933; color:#e5e7eb; font-size:12px;">
                  </label>
                </div>
              </div>

              <!-- BUTTON ROW -->
              <div style="display:flex; justify-content:space-between; margin-top:8px; gap:8px; flex-wrap:wrap;">
                <button type="submit"
                        style="background:#111827;
                               color:#e5e7eb; border:1px solid #1f2933; border-radius:999px;
                               padding:8px 18px; font-size:13px; font-weight:500;
                               cursor:pointer;">
                  Run Manual DMR
                </button>

                <button type="button" id="auto-dmr-btn"
                        onclick="runAutoDMR()"
                        style="background:linear-gradient(135deg,#22c55e,#0ea5e9);
                               color:#020617; border:none; border-radius:999px;
                               padding:8px 18px; font-size:13px; font-weight:600;
                               cursor:pointer;">
                  Run Auto DMR (BTCUSDT)
                </button>
              </div>
            </form>
          </div>

          <!-- RIGHT COLUMN: EXPLANATION + AUTO DMR OUTPUT -->
          <div style="flex:1 1 320px; min-width:260px; max-width:560px;">
            <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                        padding:18px 18px 20px 18px; margin-bottom:16px;">
              <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                          color:#9ca3af; margin-bottom:4px;">
                How this prototype works
              </div>
              <p style="margin:0 0 6px 0; font-size:13px; color:#e5e7eb;">
                The manual mode lets you enter shelves and FRVP levels from your chart.
                The automated mode asks the backend to pull BTCUSDT data from Binance US,
                build volume profiles, and then run the same KTBB SSE engine
                to choose daily support/resistance and trigger levels.
              </p>
            </div>

            <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                        padding:18px 18px 20px 18px;">
              <div style="font-size:12px; text-transform:uppercase; letter-spacing:0.08em;
                          color:#9ca3af; margin-bottom:6px;">
                Auto DMR – BTCUSDT
              </div>
              <div id="auto-dmr-status"
                   style="font-size:12px; color:#9ca3af; margin-bottom:4px;">
                Status: idle (click “Run Auto DMR (BTCUSDT)”)
              </div>
              <pre id="auto-dmr-output"
                   style="background:#020617; border-radius:8px; border:1px solid #111827;
                          padding:10px; font-size:12px; white-space:pre-wrap; min-height:120px;">
No auto DMR run yet.
              </pre>
              <div id="auto-dmr-error"
                   style="font-size:12px; color:#f97373; margin-top:4px;"></div>
            </div>
          </div>
        </div>

        <!-- SIMPLE FRONTEND SCRIPT -->
        <script>
          function buildBiasSummary(levels) {
            if (!levels) return "No levels returned from engine.";
            var breakout = levels.breakout_trigger;
            var breakdown = levels.breakdown_trigger;
            return (
              "Above " + breakout + " = bullish continuation scenario. " +
              "Below " + breakdown + " = bearish continuation scenario. " +
              "Inside this band, expect more range and mean reversion behaviour."
            );
          }

          async function runAutoDMR() {
            var btn = document.getElementById("auto-dmr-btn");
            var statusEl = document.getElementById("auto-dmr-status");
            var outputEl = document.getElementById("auto-dmr-output");
            var errorEl = document.getElementById("auto-dmr-error");

            statusEl.textContent = "Status: running auto DMR…";
            errorEl.textContent = "";
            outputEl.textContent = "";
            btn.disabled = true;

            try {
              const resp = await fetch("/api/dmr/run-auto", {
                method: "POST",
                headers: { "Accept": "application/json" }
              });

              let data;
              try {
                data = await resp.json();
              } catch (e) {
                data = null;
              }

              if (!resp.ok) {
                const msg = data && data.detail ? data.detail : (resp.status + " " + resp.statusText);
                throw new Error(msg);
              }

              const levels = data.levels || {};
              const range30 = data.range_30m || {};
              const symbol = data.symbol || "BTCUSDT";
              const bias = buildBiasSummary(levels);

              const lines = [
                "Symbol: " + symbol,
                "",
                "Daily Support: " + levels.daily_support,
                "Daily Resistance: " + levels.daily_resistance,
                "Breakout Trigger: " + levels.breakout_trigger,
                "Breakdown Trigger: " + levels.breakdown_trigger,
                "",
                "30m Opening Range: " + range30.low + " – " + range30.high,
                "",
                "Bias Summary:",
                bias
              ];

              outputEl.textContent = lines.join("\\n");
              statusEl.textContent = "Status: last updated at " + new Date().toLocaleTimeString();
            } catch (err) {
              errorEl.textContent = "Error: " + err.message;
              statusEl.textContent = "Status: error";
            } finally {
              btn.disabled = false;
            }
          }
        </script>
      </body>
    </html>
    """



# ------------------------------------------------------------
# PROCESS FORM + RUN ENGINE – RESULT VIEW
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
        r30_high, r30_low
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
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body style="margin:0; background:#020617; color:#e5e7eb; 
                   font-family:system-ui,-apple-system,BlinkMacSystemFont,
                   'Segoe UI',sans-serif;">
        <!-- Top Nav -->
        <div style="background:#020617; border-bottom:1px solid #111827; padding:14px 24px;
                    display:flex; align-items:center; justify-content:space-between;">
          <div style="display:flex; align-items:center; gap:8px;">
            <div style="width:26px; height:26px; border-radius:999px; 
                        background:linear-gradient(135deg,#22c55e,#0ea5e9);"></div>
            <div>
              <div style="font-weight:600; letter-spacing:0.04em; text-transform:uppercase;
                          font-size:11px; color:#9ca3af;">
                Kabroda Trading
              </div>
              <div style="font-weight:600; font-size:15px;">Trading Battle Box</div>
            </div>
          </div>
          <div style="font-size:12px; color:#9ca3af;">
            Environment: <span style="color:#22c55e;">DEV</span>
          </div>
        </div>

        <div style="padding:24px; display:flex; flex-wrap:wrap; gap:24px;">

          <!-- LEFT: SUMMARY CARDS -->
          <div style="flex:1 1 320px; max-width:520px;">
            <div style="margin-bottom:16px;">
              <h2 style="margin:0 0 4px 0; font-size:22px;">DMR Output</h2>
              <p style="margin:0; font-size:13px; color:#9ca3af;">
                Structural levels selected by the SSE engine using your inputs.
              </p>
            </div>

            <div style="display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px;">

              <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                          padding:12px 14px;">
                <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:4px;">
                  Daily Support
                </div>
                <div style="font-size:18px; font-weight:600;">
                  {result['daily_support']}
                </div>
              </div>

              <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                          padding:12px 14px;">
                <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:4px;">
                  Daily Resistance
                </div>
                <div style="font-size:18px; font-weight:600;">
                  {result['daily_resistance']}
                </div>
              </div>

              <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                          padding:12px 14px;">
                <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:4px;">
                  Breakout Trigger
                </div>
                <div style="font-size:18px; font-weight:600;">
                  {result['breakout_trigger']}
                </div>
              </div>

              <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                          padding:12px 14px;">
                <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                            color:#9ca3af; margin-bottom:4px;">
                  Breakdown Trigger
                </div>
                <div style="font-size:18px; font-weight:600;">
                  {result['breakdown_trigger']}
                </div>
              </div>
            </div>

            <div style="margin-top:18px; background:#020617; border-radius:16px; 
                        border:1px solid #111827; padding:12px 14px;">
              <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                          color:#9ca3af; margin-bottom:4px;">
                30m Opening Range
              </div>
              <div style="display:flex; gap:18px; font-size:13px;">
                <span><strong>High:</strong> {r30_high}</span>
                <span><strong>Low:</strong> {r30_low}</span>
              </div>
            </div>
          </div>

          <!-- RIGHT: SHELVES + YAML -->
          <div style="flex:1 1 320px; min-width:260px; max-width:560px;
                      display:flex; flex-direction:column; gap:16px;">

            <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                        padding:12px 14px;">
              <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                          color:#9ca3af; margin-bottom:6px;">
                HTF Shelves Overview
              </div>
              <div style="display:flex; gap:24px; font-size:13px;">
                <div>
                  <div style="color:#9ca3af; font-size:12px; margin-bottom:4px;">Resistance</div>
                  <ul style="margin:0; padding-left:16px;">
                    <li>4H @ {result['htf_resistance'][0]['level']}</li>
                    <li>1H @ {result['htf_resistance'][1]['level']}</li>
                  </ul>
                </div>
                <div>
                  <div style="color:#9ca3af; font-size:12px; margin-bottom:4px;">Support</div>
                  <ul style="margin:0; padding-left:16px;">
                    <li>4H @ {result['htf_support'][0]['level']}</li>
                    <li>1H @ {result['htf_support'][1]['level']}</li>
                  </ul>
                </div>
              </div>
            </div>

            <div style="background:#020617; border-radius:16px; border:1px solid #111827;
                        padding:12px 14px;">
              <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
                          color:#9ca3af; margin-bottom:6px;">
                YAML Key Level Block
              </div>
              <pre style="background:#020617; padding:10px; border-radius:8px;
                          border:1px solid #111827; font-size:12px; white-space:pre-wrap;">
{yaml_block}
              </pre>
            </div>

            <div>
              <a href="/" style="display:inline-flex; align-items:center; gap:6px;
                                 font-size:13px; color:#60a5fa; text-decoration:none;">
                &#8592; Run another DMR
              </a>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


# ------------------------------------------------------------
# JSON API ENDPOINT – SAME ENGINE, NO HTML
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
from fastapi import HTTPException  # <-- add this to your imports at top if not there yet
# from fastapi import FastAPI, Form, HTTPException  (you can merge it into the first line)

@app.post("/api/dmr/run-auto")
async def api_run_dmr_auto() -> Dict[str, Any]:
    """
    Fully automated DMR for BTCUSDT.

    Fetches OHLCV from Binance, builds VRVP/FRVP and HTF shelves,
    then runs the existing KTBB compute_dm_levels() engine.
    """
    try:
        inputs = build_auto_inputs_for_btc()
    except Exception as e:
        # If Binance / volume-profile / anything in data_feed fails,
        # return a clear error so we can see what went wrong.
        raise HTTPException(
            status_code=500,
            detail=f"Error while building auto inputs for BTCUSDT: {type(e).__name__}: {e}",
        )

    try:
        result = compute_dm_levels(
            inputs["h4_supply"], inputs["h4_demand"],
            inputs["h1_supply"], inputs["h1_demand"],
            inputs["weekly_val"], inputs["weekly_poc"], inputs["weekly_vah"],
            inputs["f24_val"], inputs["f24_poc"], inputs["f24_vah"],
            inputs["morn_val"], inputs["morn_poc"], inputs["morn_vah"],
            inputs["r30_high"], inputs["r30_low"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error while running compute_dm_levels: {type(e).__name__}: {e}",
        )

    return {
        "status": "success",
        "mode": "auto",
        "symbol": "BTCUSDT",
        "inputs": inputs,
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
            "high": inputs["r30_high"],
            "low": inputs["r30_low"],
        }
    }
