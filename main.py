from typing import Dict, Any
from datetime import datetime
import os

from fastapi import FastAPI, Form, Depends, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sse_engine import compute_dm_levels          # KTBB SSE ENGINE
from data_feed import build_auto_inputs, resolve_symbol   # BINANCE AUTO INPUTS
from dmr_report import generate_dmr_report        # DMR text generator
from membership import (
    User,
    Tier,
    ensure_can_use_auto,
    ensure_can_use_symbol_auto,
)
from database import init_db
from auth import (
    get_db,
    get_current_user,
    create_user,
    authenticate_user,
    create_session_token,
    delete_session,
)

# Optional GPT client (only used if openai is installed + API key set)
try:
    from openai import OpenAI  # type: ignore
except ImportError:
    OpenAI = None  # type: ignore

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI) else None

# -------------------------------------------------------------------
# FASTAPI APP
# -------------------------------------------------------------------
app = FastAPI(title="KTBB – Trading Battle Box API")

# Create DB tables on startup
init_db()


# -------------------------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ktbb", "version": "0.7.0"}


# -------------------------------------------------------------------
# Pydantic models
# -------------------------------------------------------------------
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


class RegisterRequest(BaseModel):
    email: str
    password: str
    tier: Tier | None = None  # optional; if omitted, defaults to Tier2


class LoginRequest(BaseModel):
    email: str
    password: str


class AssistantChatRequest(BaseModel):
    symbol: str
    question: str


# -------------------------------------------------------------------
# MAIN DASHBOARD UI (HTML)
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def show_form(user: User = Depends(get_current_user)):
    """Main dashboard: manual inputs + auto DMR panel."""

    # Simple label for the header
    if user.id == 0:
        user_label = "Anonymous (Tier3 dev default)"
        tier_label = "Tier3_MULTI_GPT"
    else:
        user_label = user.email
        tier_label = user.tier.value

    html = """
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
      .app-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 18px;
        border-radius: 16px;
        background: linear-gradient(90deg, #0284c7, #0ea5e9, #22c55e);
        box-shadow: 0 12px 40px rgba(15,23,42,0.8);
        margin-bottom: 18px;
      }
      .app-header-left {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .logo-pill {
        width: 40px;
        height: 40px;
        border-radius: 999px;
        border: 2px solid #e5e7eb;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.9rem;
        color: #e5e7eb;
        background: rgba(15,23,42,0.25);
      }
      .brand-lines {
        display: flex;
        flex-direction: column;
      }
      .brand-main {
        font-size: 1.05rem;
        font-weight: 700;
        letter-spacing: 0.06em;
      }
      .brand-sub {
        font-size: 0.8rem;
        opacity: 0.9;
      }
      .app-header-right {
        text-align: right;
        font-size: 0.8rem;
      }
      .app-header-right a {
        color: #e5e7eb;
        text-decoration: none;
        margin-left: 8px;
      }
      .app-header-right a:hover {
        text-decoration: underline;
      }
      .badge-tier {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        border: 1px solid rgba(15,23,42,0.6);
        background: rgba(15,23,42,0.35);
        font-size: 0.75rem;
        margin-left: 4px;
      }

      h1 {
        font-size: 1.6rem;
        margin-bottom: 4px;
      }
      h2 {
        font-size: 1.1rem;
        margin-top: 0;
        margin-bottom: 10px;
        color: #9ca3af;
        font-weight: 500;
      }
      h3 {
        font-size: 1.05rem;
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
        margin-top: 8px;
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
      .symbol-row {
        margin-top: 4px;
        margin-bottom: 8px;
      }
      .symbol-select {
        background: #020617;
        color: #e5e7eb;
        border-radius: 8px;
        border: 1px solid #1f2937;
        padding: 4px 8px;
        font-size: 0.85rem;
      }
      .summary-block {
        background: #020617;
        border-radius: 12px;
        border: 1px solid #1f2937;
        padding: 10px 12px;
        margin-bottom: 10px;
        font-size: 0.85rem;
      }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px 14px;
      }
      .summary-label {
        color: #9ca3af;
      }
      .summary-value {
        font-weight: 600;
      }
      .summary-value-strong {
        font-weight: 700;
        color: #f97316;
      }
      textarea {
        width: 100%;
        min-height: 60px;
        max-height: 140px;
        resize: vertical;
        box-sizing: border-box;
        background: #020617;
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 6px 8px;
        color: #e5e7eb;
        font-size: 0.85rem;
      }
      textarea:focus {
        outline: none;
        border-color: #60a5fa;
        box-shadow: 0 0 0 1px #60a5fa33;
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <!-- Kabroda-style header -->
      <div class="app-header">
        <div class="app-header-left">
          <div class="logo-pill">KT</div>
          <div class="brand-lines">
            <div class="brand-main">KABRODA TRADING</div>
            <div class="brand-sub">Trading Battle Box · Daily Market Review</div>
          </div>
        </div>
        <div class="app-header-right">
          <div>
            <span>Logged in as </span>
            <strong>{{USER_LABEL}}</strong>
            <span class="badge-tier">{{TIER_LABEL}}</span>
          </div>
          <div style="margin-top:4px;">
            <a href="/auth/login-ui">Login</a>
            <a href="/auth/register-ui">Register</a>
            <a href="/auth/logout-ui">Logout</a>
            <a href="/billing/upgrade-ui">Upgrade</a>
          </div>
        </div>
      </div>

      <h1>Daily Market Review</h1>
      <h2>Shelves, FRVPs and Auto DMR pulled directly from Binance US.</h2>

      <div class="grid">
        <!-- LEFT: MANUAL INPUT FORM -->
        <div class="card">
          <!-- Symbol selection -->
          <div class="symbol-row">
            <label for="symbol-select">Symbol</label>
            <select id="symbol-select" class="symbol-select">
              <option value="BTC">BTC / BTCUSDT</option>
              <option value="ETH">ETH / ETHUSDT</option>
              <option value="XRP">XRP / XRPUSDT</option>
              <option value="SOL">SOL / SOLUSDT</option>
            </select>
          </div>

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
              <button type="button" class="btn-secondary" onclick="autoFillFromBTC()">Auto-fill (selected symbol)</button>
              <button type="button" class="btn-accent" onclick="runAutoDMR()">Run Auto DMR (selected symbol)</button>
            </div>
          </form>
        </div>

        <!-- RIGHT: AUTO DMR PANEL -->
        <div>
          <div class="card">
            <h3>Auto DMR - Selected Symbol</h3>
            <p class="status-line">
              Status:
              <span id="auto-status" class="status-line">waiting...</span>
            </p>

            <!-- Compact numeric summary -->
            <div id="summary-block" class="summary-block" style="display:none;">
              <div class="summary-grid">
                <div>
                  <div class="summary-label">Symbol</div>
                  <div class="summary-value" id="summary-symbol">-</div>
                </div>
                <div>
                  <div class="summary-label">Bias</div>
                  <div class="summary-value summary-value-strong" id="summary-bias">-</div>
                </div>
                <div>
                  <div class="summary-label">Daily Support</div>
                  <div class="summary-value" id="summary-support">-</div>
                </div>
                <div>
                  <div class="summary-label">Daily Resistance</div>
                  <div class="summary-value" id="summary-resistance">-</div>
                </div>
                <div>
                  <div class="summary-label">Breakout</div>
                  <div class="summary-value" id="summary-breakout">-</div>
                </div>
                <div>
                  <div class="summary-label">Breakdown</div>
                  <div class="summary-value" id="summary-breakdown">-</div>
                </div>
                <div>
                  <div class="summary-label">30m Range</div>
                  <div class="summary-value" id="summary-range">-</div>
                </div>
              </div>
            </div>

            <!-- Full text report -->
            <pre id="auto-output" class="mono">Select a symbol and click "Run Auto DMR" to pull shelves, FRVPs and levels from Binance US.</pre>
          </div>

          <!-- KTBB Assistant (Tier 3) -->
          <div class="card" style="margin-top:16px;">
            <h3>KTBB Assistant (Tier 3)</h3>
            <p class="status-line">
              Ask a question about today's DMR. Tier 3 unlocks GPT-powered coaching; lower tiers will see a membership message.
            </p>
            <textarea id="assistant-question" placeholder="Example: Where is the highest-probability long setup today?"></textarea>
            <div class="button-row">
              <button type="button" class="btn-accent" onclick="askKtbbAssistant()">Ask KTBB</button>
            </div>
            <pre id="assistant-output" class="mono">KTBB Assistant is standing by. Type a question and click "Ask KTBB".</pre>
          </div>
        </div>
      </div>
    </div>

    <script>
      function getSelectedSymbol() {
        const sel = document.getElementById("symbol-select");
        return sel ? sel.value : "BTC";
      }

      async function handleMembershipOrError(res, statusEl, outEl, actionLabel) {
        // actionLabel: "Auto DMR" or "Auto-fill"
        if (res.status === 403) {
          let detailMsg = "This action is not included in your current membership.";
          try {
            const data = await res.json();
            if (data && data.detail) {
              detailMsg = data.detail;
            }
          } catch (e) {
            // ignore JSON parse errors
          }

          statusEl.textContent = "membership limit";
          statusEl.className = "status-line status-error";
          outEl.textContent =
            detailMsg +
            "\\n\\nIf you want Auto DMR or multi-symbol access, click the 'Upgrade' link in the top bar.";

          const summaryBlock = document.getElementById("summary-block");
          if (summaryBlock) summaryBlock.style.display = "none";

          return true; // handled
        }

        // Non-403 error: show generic message
        const txt = await res.text();
        statusEl.textContent = "error";
        statusEl.className = "status-line status-error";
        outEl.textContent = actionLabel + " failed (" + res.status + ").\\n" + txt;

        const summaryBlock = document.getElementById("summary-block");
        if (summaryBlock) summaryBlock.style.display = "none";

        return true;
      }

      function updateSummary(data, bias, symbol, r30) {
        const summaryBlock = document.getElementById("summary-block");
        if (!summaryBlock) return;

        const lev = data.levels;

        document.getElementById("summary-symbol").textContent = symbol;
        document.getElementById("summary-bias").textContent = bias;
        document.getElementById("summary-support").textContent = lev.daily_support;
        document.getElementById("summary-resistance").textContent = lev.daily_resistance;
        document.getElementById("summary-breakout").textContent = lev.breakout_trigger;
        document.getElementById("summary-breakdown").textContent = lev.breakdown_trigger;
        document.getElementById("summary-range").textContent = r30.low + " - " + r30.high;

        summaryBlock.style.display = "block";
      }

      // AUTO DMR (summary + full report)
      window.runAutoDMR = async function() {
        const statusEl = document.getElementById("auto-status");
        const outEl = document.getElementById("auto-output");
        const symbol = getSelectedSymbol();

        statusEl.textContent = "running...";
        statusEl.className = "status-line";

        try {
          const res = await fetch("/api/dmr/run-auto?symbol=" + encodeURIComponent(symbol), {
            method: "POST",
            headers: { "Accept": "application/json" }
          });

          if (!res.ok) {
            await handleMembershipOrError(res, statusEl, outEl, "Auto DMR");
            return;
          }

          const data = await res.json();
          statusEl.textContent = "ok - last run just now";
          statusEl.className = "status-line status-ok";

          const lev = data.levels;
          const htf = data.htf_shelves;
          const r30 = data.range_30m;
          const bias = data.report && data.report.bias ? data.report.bias : "n/a";
          const displaySymbol = data.symbol_short || data.symbol || symbol;

          // Update compact summary block
          updateSummary(data, bias, displaySymbol, r30);

          const fullReport =
            data.report && data.report.full_text
              ? data.report.full_text
              : (
                  "HTF Shelves:\\n" +
                  "  4H Supply: " + htf.resistance[0].level + "\\n" +
                  "  4H Demand: " + htf.support[0].level + "\\n" +
                  "  1H Supply: " + htf.resistance[1].level + "\\n" +
                  "  1H Demand: " + htf.support[1].level
                );

          const text = fullReport;
          outEl.textContent = text;
        } catch (err) {
          statusEl.textContent = "error";
          statusEl.className = "status-line status-error";
          outEl.textContent = "Exception while running auto DMR:\\n" + err;

          const summaryBlock = document.getElementById("summary-block");
          if (summaryBlock) summaryBlock.style.display = "none";
        }
      };

      // AUTO-FILL FROM BINANCE FOR SELECTED SYMBOL
      window.autoFillFromBTC = async function() {
        const statusEl = document.getElementById("auto-status");
        const outEl = document.getElementById("auto-output");
        const symbol = getSelectedSymbol();

        statusEl.textContent = "auto-filling...";
        statusEl.className = "status-line";

        try {
          const res = await fetch("/api/dmr/auto-inputs?symbol=" + encodeURIComponent(symbol), {
            method: "GET",
            headers: { "Accept": "application/json" }
          });

          if (!res.ok) {
            await handleMembershipOrError(res, statusEl, outEl, "Auto-fill");
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
            "Form fields have been auto-filled from Binance for " +
            (data.symbol_short || data.symbol || symbol) + ".\\n" +
            "You can adjust any value and then click 'Run Manual DMR'.";
        } catch (err) {
          statusEl.textContent = "error on auto-fill";
          statusEl.className = "status-line status-error";
          outEl.textContent = "Exception while auto-filling:\\n" + err;
        }
      };

      // KTBB Assistant – Tier 3 GPT (or fallback)
      window.askKtbbAssistant = async function() {
        const qEl = document.getElementById("assistant-question");
        const outEl = document.getElementById("assistant-output");
        const symbol = getSelectedSymbol();
        const question = (qEl && qEl.value || "").trim();

        if (!question) {
          outEl.textContent = "Please type a question for KTBB Assistant first.";
          return;
        }

        outEl.textContent = "KTBB Assistant is thinking...";

        try {
          const res = await fetch("/api/assistant/dmr-chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: symbol, question: question })
          });

          if (res.status === 403) {
            let msg = "KTBB Assistant is available on Tier 3 only.";
            try {
              const data = await res.json();
              if (data && data.detail) msg = data.detail;
            } catch (e) {}
            outEl.textContent =
              msg + "\\n\\nClick the 'Upgrade' link in the top bar to change your plan.";
            return;
          }

          if (!res.ok) {
            const txt = await res.text();
            outEl.textContent = "Assistant request failed (" + res.status + ").\\n" + txt;
            return;
          }

          const data = await res.json();
          outEl.textContent = data.answer || "No answer returned from KTBB Assistant.";
        } catch (err) {
          outEl.textContent = "Error contacting KTBB Assistant:\\n" + err;
        }
      };
    </script>
  </body>
</html>
    """

    html = html.replace("{{USER_LABEL}}", user_label).replace("{{TIER_LABEL}}", tier_label)
    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# HTML FORM HANDLER (Manual DMR)
# -------------------------------------------------------------------
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


# -------------------------------------------------------------------
# JSON: Manual DMR
# -------------------------------------------------------------------
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


# -------------------------------------------------------------------
# JSON: Auto inputs only (for Auto-fill button)
# -------------------------------------------------------------------
@app.get("/api/dmr/auto-inputs")
async def api_auto_inputs(
    symbol: str = "BTC",
    user: User = Depends(get_current_user),
):
    symbol_short = symbol.upper()
    try:
        ensure_can_use_auto(user)
        ensure_can_use_symbol_auto(user, symbol_short)

        binance_symbol = resolve_symbol(symbol_short)
        inputs = build_auto_inputs(symbol_short)
        return {
            "status": "success",
            "symbol": binance_symbol,
            "symbol_short": symbol_short,
            "inputs": inputs,
        }
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)},
        )


# -------------------------------------------------------------------
# JSON: Full Auto DMR (for “Run Auto DMR” button)
# -------------------------------------------------------------------
@app.post("/api/dmr/run-auto")
async def api_run_dmr_auto(
    symbol: str = "BTC",
    user: User = Depends(get_current_user),
):
    symbol_short = symbol.upper()
    try:
        ensure_can_use_auto(user)
        ensure_can_use_symbol_auto(user, symbol_short)

        binance_symbol = resolve_symbol(symbol_short)
        inp = build_auto_inputs(symbol_short)

        result = compute_dm_levels(
            inp["h4_supply"], inp["h4_demand"],
            inp["h1_supply"], inp["h1_demand"],
            inp["weekly_val"], inp["weekly_poc"], inp["weekly_vah"],
            inp["f24_val"], inp["f24_poc"], inp["f24_vah"],
            inp["morn_val"], inp["morn_poc"], inp["morn_vah"],
            inp["r30_high"], inp["r30_low"],
        )

        htf_shelves = {
            "resistance": result["htf_resistance"],
            "support": result["htf_support"],
        }
        range_30m = {
            "high": inp["r30_high"],
            "low": inp["r30_low"],
        }

        report = generate_dmr_report(
            symbol=binance_symbol,
            date_str=datetime.utcnow().strftime("%Y-%m-%d"),
            inputs=inp,
            levels={
                "daily_support": result["daily_support"],
                "daily_resistance": result["daily_resistance"],
                "breakout_trigger": result["breakout_trigger"],
                "breakdown_trigger": result["breakdown_trigger"],
            },
            htf_shelves=htf_shelves,
            range_30m=range_30m,
        )

        return {
            "status": "success",
            "mode": "auto",
            "symbol": binance_symbol,
            "symbol_short": symbol_short,
            "inputs": inp,
            "levels": {
                "daily_support": result["daily_support"],
                "daily_resistance": result["daily_resistance"],
                "breakout_trigger": result["breakout_trigger"],
                "breakdown_trigger": result["breakdown_trigger"],
            },
            "htf_shelves": htf_shelves,
            "range_30m": range_30m,
            "report": report,
        }
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)},
        )


# -------------------------------------------------------------------
# KTBB Assistant – GPT-powered Q&A (Tier 3 only)
# -------------------------------------------------------------------
@app.post("/api/assistant/dmr-chat")
async def api_assistant_dmr_chat(
    req: AssistantChatRequest,
    user: User = Depends(get_current_user),
):
    # Tier gating: only Tier 3 gets the assistant
    if user.tier != Tier.TIER3_MULTI_GPT:
        raise HTTPException(
            status_code=403,
            detail="KTBB Assistant is available on Tier 3 only.",
        )

    symbol_short = req.symbol.upper()
    binance_symbol = resolve_symbol(symbol_short)
    inp = build_auto_inputs(symbol_short)

    # Compute levels the same way as Auto DMR
    result = compute_dm_levels(
        inp["h4_supply"], inp["h4_demand"],
        inp["h1_supply"], inp["h1_demand"],
        inp["weekly_val"], inp["weekly_poc"], inp["weekly_vah"],
        inp["f24_val"], inp["f24_poc"], inp["f24_vah"],
        inp["morn_val"], inp["morn_poc"], inp["morn_vah"],
        inp["r30_high"], inp["r30_low"],
    )
    htf_shelves = {
        "resistance": result["htf_resistance"],
        "support": result["htf_support"],
    }
    range_30m = {
        "high": inp["r30_high"],
        "low": inp["r30_low"],
    }

    report = generate_dmr_report(
        symbol=binance_symbol,
        date_str=datetime.utcnow().strftime("%Y-%m-%d"),
        inputs=inp,
        levels={
            "daily_support": result["daily_support"],
            "daily_resistance": result["daily_resistance"],
            "breakout_trigger": result["breakout_trigger"],
            "breakdown_trigger": result["breakdown_trigger"],
        },
        htf_shelves=htf_shelves,
        range_30m=range_30m,
    )

    # If GPT client isn't configured yet, return a deterministic fallback
    if openai_client is None:
        fallback_answer = (
            "KTBB Assistant is currently running in offline mode because the OpenAI API key "
            "is not configured on the server.\n\n"
            "Here is the latest deterministic DMR report for context:\n\n"
            f"{report['full_text']}"
        )
        return {
            "status": "success",
            "mode": "fallback",
            "answer": fallback_answer,
            "bias": report["bias"],
        }

    # Build a compact context summary for GPT
    levels_ctx = (
        f"Daily Support: {result['daily_support']:.1f}\n"
        f"Daily Resistance: {result['daily_resistance']:.1f}\n"
        f"Breakout Trigger: {result['breakout_trigger']:.1f}\n"
        f"Breakdown Trigger: {result['breakdown_trigger']:.1f}\n"
        f"30m Range: {range_30m['low']:.1f} - {range_30m['high']:.1f}\n"
    )

    system_msg = (
        "You are the KTBB (Kabroda Trading Battle Box) assistant, a professional intraday "
        "trading coach. You are given a deterministic Daily Market Review that already "
        "contains all levels, FRVPs, and shelves. You MUST NOT invent new price levels.\n\n"
        "When you answer, you:\n"
        "- reference the actual numeric levels provided\n"
        "- talk like an experienced trader (clear, concise, practical)\n"
        "- focus on risk management and scenario planning\n"
        "- avoid hype or guarantees.\n"
    )

    user_msg = (
        f"Symbol: {binance_symbol}\n"
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"Bias: {report['bias']} (confidence: {report.get('bias_confidence','n/a')})\n\n"
        "Key levels:\n"
        f"{levels_ctx}\n"
        "Full DMR report:\n"
        f"{report['full_text']}\n\n"
        f"Trader question:\n{req.question}\n\n"
        "Please answer in 2–5 short paragraphs, with clear structure and numbered bullets "
        "if helpful. Use the provided levels exactly; do not change them."
    )

    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=900,
    )

    answer = completion.choices[0].message.content
    return {
        "status": "success",
        "mode": "gpt",
        "answer": answer,
        "bias": report["bias"],
    }


# -------------------------------------------------------------------
# AUTH ENDPOINTS (JSON APIs)
# -------------------------------------------------------------------
@app.post("/auth/register")
async def register(
    req: RegisterRequest,
    db: Session = Depends(get_db),
):
    tier = req.tier or Tier.TIER2_SINGLE_AUTO
    user = create_user(db, req.email, req.password, tier)
    return {
        "status": "success",
        "id": user.id,
        "email": user.email,
        "tier": user.tier,
    }


@app.post("/auth/login")
async def login(
    req: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, req.email, req.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )
    token = create_session_token(db, user.id)
    response.set_cookie(
        key="ktbb_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # set True when on HTTPS
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return {
        "status": "success",
        "id": user.id,
        "email": user.email,
        "tier": user.tier,
    }


@app.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get("ktbb_session")
    if token:
        delete_session(db, token)
    response.delete_cookie("ktbb_session")
    return {"status": "success"}


@app.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "tier": user.tier.value,
    }


# -------------------------------------------------------------------
# Simple HTML login/register pages (for quick manual testing)
# -------------------------------------------------------------------
@app.get("/auth/login-ui", response_class=HTMLResponse)
async def login_ui():
    return """
    <html>
      <body style="background:#020617; color:#e5e7eb; font-family:system-ui; padding:40px;">
        <h2>KTBB – Login</h2>
        <form method="post" action="/auth/login-ui">
          <p>Email:<br><input type="email" name="email" required /></p>
          <p>Password:<br><input type="password" name="password" required /></p>
          <p><button type="submit">Login</button></p>
        </form>
        <p><a href="/">Back to dashboard</a></p>
      </body>
    </html>
    """


@app.post("/auth/login-ui", response_class=HTMLResponse)
async def login_ui_post(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")

    user = authenticate_user(db, email, password)
    if not user:
        return HTMLResponse(
            content="""
            <html><body style="background:#020617; color:#e5e7eb; font-family:system-ui; padding:40px;">
            <h2>Login failed</h2>
            <p>Invalid email or password.</p>
            <p><a href="/auth/login-ui">Try again</a></p>
            </body></html>
            """,
            status_code=401,
        )

    from fastapi.responses import RedirectResponse

    token = create_session_token(db, user.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key="ktbb_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.get("/auth/register-ui", response_class=HTMLResponse)
async def register_ui():
    return """
    <html>
      <body style="background:#020617; color:#e5e7eb; font-family:system-ui; padding:40px;">
        <h2>KTBB – Register</h2>
        <form method="post" action="/auth/register-ui">
          <p>Email:<br><input type="email" name="email" required /></p>
          <p>Password:<br><input type="password" name="password" required /></p>
          <p>Tier (for testing):<br>
            <select name="tier">
              <option value="tier1_manual">Tier1 – Manual only</option>
              <option value="tier2_single_auto" selected>Tier2 – Auto BTC only</option>
              <option value="tier3_multi_gpt">Tier3 – Full (multi + GPT)</option>
            </select>
          </p>
          <p><button type="submit">Create account</button></p>
        </form>
        <p><a href="/">Back to dashboard</a></p>
      </body>
    </html>
    """


@app.post("/auth/register-ui", response_class=HTMLResponse)
async def register_ui_post(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    tier_raw = str(form.get("tier") or "tier2_single_auto")

    try:
        tier = Tier(tier_raw)
    except Exception:
        tier = Tier.TIER2_SINGLE_AUTO

    from fastapi.responses import RedirectResponse
    try:
        user = create_user(db, email, password, tier)
    except Exception as e:
        return HTMLResponse(
            content=f"""
            <html><body style="background:#020617; color:#e5e7eb; font-family:system-ui; padding:40px;">
            <h2>Register failed</h2>
            <p>{e}</p>
            <p><a href="/auth/register-ui">Try again</a></p>
            </body></html>
            """,
            status_code=400,
        )

    # auto-login new user
    token = create_session_token(db, user.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key="ktbb_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.get("/auth/logout-ui", response_class=HTMLResponse)
async def logout_ui(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get("ktbb_session")
    if token:
        delete_session(db, token)
    from fastapi.responses import RedirectResponse

    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("ktbb_session")
    return resp


# -------------------------------------------------------------------
# BILLING / UPGRADE PLACEHOLDER (no Stripe yet)
# -------------------------------------------------------------------
@app.get("/billing/upgrade-ui", response_class=HTMLResponse)
async def upgrade_ui(user: User = Depends(get_current_user)):
    return f"""
    <html>
      <body style="background:#020617; color:#e5e7eb; font-family:system-ui; padding:40px;">
        <h2>KTBB – Upgrade Membership</h2>
        <p>Current account:</p>
        <ul>
          <li>Email: <strong>{user.email}</strong></li>
          <li>Tier: <strong>{user.tier.value}</strong></li>
        </ul>
        <p>
          This is a placeholder screen. In the production version, this page will start a
          Stripe Checkout session where you can choose between Tier1, Tier2, and Tier3 and
          update your subscription automatically.
        </p>
        <p>
          For now, tiers can be changed manually via the Register form (for testing) or by
          updating the database.
        </p>
        <p><a href="/">Back to dashboard</a></p>
      </body>
    </html>
    """
