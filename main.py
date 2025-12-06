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
    """Main dashboard: three-panel layout (inputs / DMR / Assistant)."""

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
        max-width: 1280px;
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
        margin-bottom: 2px;
      }
      h2 {
        font-size: 1.0rem;
        margin-top: 0;
        margin-bottom: 10px;
        color: #9ca3af;
        font-weight: 500;
      }
      p {
        margin-top: 4px;
        margin-bottom: 6px;
        color: #9ca3af;
        font-size: 0.9rem;
      }

      /* 3-column layout */
      .layout {
        display: grid;
        grid-template-columns: 280px minmax(0, 2.4fr) minmax(0, 1.7fr);
        gap: 16px;
        align-items: flex-start;
        margin-top: 4px;
      }

      .card {
        background: #020617;
        border-radius: 16px;
        border: 1px solid #111827;
        padding: 14px 16px 16px;
        box-shadow: 0 12px 40px rgba(15,23,42,0.8);
      }

      label {
        display: block;
        font-size: 0.8rem;
        color: #9ca3af;
        margin-bottom: 2px;
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

      select.symbol-select {
        width: 100%;
        background: #020617;
        color: #e5e7eb;
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 6px 8px;
        font-size: 0.85rem;
      }

      .row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
      }
      .row-2 {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
      }

      button {
        border-radius: 999px;
        border: none;
        padding: 7px 14px;
        font-size: 0.8rem;
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
      .btn-primary:hover { background: #16a34a; }
      .btn-secondary:hover { background: #020617; }
      .btn-accent:hover { background: #2563eb; }

      .button-row {
        margin-top: 12px;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }

      pre {
        background: #020617;
        border-radius: 10px;
        border: 1px solid #111827;
        padding: 8px 10px;
        font-size: 0.8rem;
        overflow-x: auto;
        white-space: pre-wrap;
      }
      .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      }

      .status-line {
        font-size: 0.8rem;
        color: #9ca3af;
        margin-top: 2px;
      }
      .status-ok { color: #22c55e; }
      .status-error { color: #f97316; }

      /* Summary block */
      .summary-block {
        background: #020617;
        border-radius: 12px;
        border: 1px solid #1f2937;
        padding: 8px 10px;
        margin-bottom: 10px;
        font-size: 0.82rem;
      }
      .summary-header {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 4px;
      }
      .summary-title {
        font-weight: 600;
      }
      .summary-bias {
        font-weight: 700;
      }
      .summary-bias-bullish { color: #22c55e; }
      .summary-bias-bearish { color: #f97316; }
      .summary-bias-neutral { color: #eab308; }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 4px 12px;
      }
      .summary-label {
        color: #9ca3af;
        font-size: 0.75rem;
      }
      .summary-value {
        font-weight: 600;
      }

      /* Accordion-style DMR sections */
      .dmr-section {
        border-radius: 10px;
        border: 1px solid #1f2937;
        margin-bottom: 6px;
        overflow: hidden;
      }
      .dmr-section-header {
        padding: 6px 10px;
        background: #020617;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.82rem;
      }
      .dmr-section-header:hover {
        background: #030712;
      }
      .dmr-section-title {
        font-weight: 600;
      }
      .dmr-section-body {
        padding: 6px 10px 8px;
        display: none;
      }
      .dmr-section-body.open {
        display: block;
      }
      .dmr-toggle {
        font-size: 0.8rem;
        color: #9ca3af;
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

      details {
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 6px 8px 8px;
        margin-bottom: 8px;
        background: #020617;
      }
      details summary {
        cursor: pointer;
        list-style: none;
        font-size: 0.8rem;
        font-weight: 600;
      }
      details summary::-webkit-details-marker {
        display: none;
      }
      details summary::before {
        content: "▾ ";
        color: #9ca3af;
      }
      details[open] summary::before {
        content: "▴ ";
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <!-- Kabroda header -->
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

      <div class="layout">
        <!-- LEFT COLUMN: SYMBOL + INPUTS -->
        <div class="card">
          <label for="symbol-select">Symbol</label>
          <select id="symbol-select" class="symbol-select">
            <option value="BTC">BTC / BTCUSDT</option>
            <option value="ETH">ETH / ETHUSDT</option>
            <option value="XRP">XRP / XRPUSDT</option>
            <option value="SOL">SOL / SOLUSDT</option>
          </select>

          <form id="dmr-form" method="post" action="/run-dmr">
            <div class="button-row" style="margin-top:10px;">
              <button type="button" class="btn-secondary" onclick="autoFillFromBTC()">Auto-fill</button>
              <button type="button" class="btn-accent" onclick="runAutoDMR()">Run Auto DMR</button>
            </div>
            <div class="button-row" style="margin-top:6px;">
              <button type="submit" class="btn-primary">Run Manual DMR</button>
            </div>

            <details open>
              <summary>HTF Shelves (4H / 1H)</summary>
              <div class="row-2" style="margin-top:6px;">
                <div>
                  <label>4H Supply</label>
                  <input name="h4_supply" type="number" step="0.1" required />
                </div>
                <div>
                  <label>4H Demand</label>
                  <input name="h4_demand" type="number" step="0.1" required />
                </div>
              </div>
              <div class="row-2" style="margin-top:6px;">
                <div>
                  <label>1H Supply</label>
                  <input name="h1_supply" type="number" step="0.1" required />
                </div>
                <div>
                  <label>1H Demand</label>
                  <input name="h1_demand" type="number" step="0.1" required />
                </div>
              </div>
            </details>

            <details open>
              <summary>Weekly VRVP</summary>
              <div class="row" style="margin-top:6px;">
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
            </details>

            <details open>
              <summary>24h FRVP</summary>
              <div class="row" style="margin-top:6px;">
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
            </details>

            <details open>
              <summary>Morning FRVP</summary>
              <div class="row" style="margin-top:6px;">
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
            </details>

            <details open>
              <summary>30m Opening Range</summary>
              <div class="row-2" style="margin-top:6px;">
                <div>
                  <label>High</label>
                  <input name="r30_high" type="number" step="0.1" required />
                </div>
                <div>
                  <label>Low</label>
                  <input name="r30_low" type="number" step="0.1" required />
                </div>
              </div>
            </details>
          </form>
        </div>

        <!-- CENTER COLUMN: SUMMARY + DMR -->
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <h3 style="margin:0; font-size:1rem;">Auto DMR – Selected Symbol</h3>
            <span id="auto-status" class="status-line">status: waiting...</span>
          </div>

          <!-- Summary -->
          <div id="summary-block" class="summary-block" style="display:none;">
            <div class="summary-header">
              <div class="summary-title">
                <span id="summary-symbol">BTC</span>
              </div>
              <div>
                <span class="summary-label">Bias:&nbsp;</span>
                <span id="summary-bias" class="summary-bias summary-bias-neutral">neutral</span>
              </div>
            </div>
            <div class="summary-grid">
              <div>
                <div class="summary-label">Daily Support</div>
                <div id="summary-support" class="summary-value">-</div>
              </div>
              <div>
                <div class="summary-label">Daily Resistance</div>
                <div id="summary-resistance" class="summary-value">-</div>
              </div>
              <div>
                <div class="summary-label">30m Range</div>
                <div id="summary-range" class="summary-value">-</div>
              </div>
              <div>
                <div class="summary-label">Breakout</div>
                <div id="summary-breakout" class="summary-value">-</div>
              </div>
              <div>
                <div class="summary-label">Breakdown</div>
                <div id="summary-breakdown" class="summary-value">-</div>
              </div>
            </div>
          </div>

          <!-- Accordion DMR sections -->
          <div id="dmr-sections">
            <div class="dmr-section">
              <div class="dmr-section-header" onclick="toggleSection('dmr-sec1')">
                <div class="dmr-section-title">1) Market Momentum Summary</div>
                <div class="dmr-toggle">toggle</div>
              </div>
              <div id="dmr-sec1" class="dmr-section-body"></div>
            </div>
            <div class="dmr-section">
              <div class="dmr-section-header" onclick="toggleSection('dmr-sec2')">
                <div class="dmr-section-title">2) Sentiment Snapshot</div>
                <div class="dmr-toggle">toggle</div>
              </div>
              <div id="dmr-sec2" class="dmr-section-body"></div>
            </div>
            <div class="dmr-section">
              <div class="dmr-section-header" onclick="toggleSection('dmr-sec3')">
                <div class="dmr-section-title">3) Key Structure & Levels</div>
                <div class="dmr-toggle">toggle</div>
              </div>
              <div id="dmr-sec3" class="dmr-section-body"></div>
            </div>
            <div class="dmr-section">
              <div class="dmr-section-header" onclick="toggleSection('dmr-sec4')">
                <div class="dmr-section-title">4) Trade Strategy Outlook</div>
                <div class="dmr-toggle">toggle</div>
              </div>
              <div id="dmr-sec4" class="dmr-section-body"></div>
            </div>
            <div class="dmr-section">
              <div class="dmr-section-header" onclick="toggleSection('dmr-sec5')">
                <div class="dmr-section-title">5) Execution Notes</div>
                <div class="dmr-toggle">toggle</div>
              </div>
              <div id="dmr-sec5" class="dmr-section-body"></div>
            </div>
          </div>

          <!-- Full report + YAML + copy buttons -->
          <div class="button-row" style="margin-top:10px;">
            <button type="button" class="btn-secondary" onclick="copyFullDMR()">Copy full DMR</button>
            <button type="button" class="btn-secondary" onclick="copyYaml()">Copy YAML</button>
          </div>

          <h4 style="margin-top:10px; margin-bottom:4px; font-size:0.9rem;">Full DMR Report</h4>
          <pre id="auto-output" class="mono">Select a symbol and click "Run Auto DMR" to pull shelves, FRVPs and levels from Binance US.</pre>

          <h4 style="margin-top:10px; margin-bottom:4px; font-size:0.9rem;">YAML Block</h4>
          <pre id="dmr-yaml" class="mono">YAML will appear here after Auto DMR runs.</pre>
        </div>

        <!-- RIGHT COLUMN: KTBB Assistant -->
        <div class="card">
          <h3 style="margin-top:0;">KTBB Assistant (Tier 3)</h3>
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

    <script>
      function getSelectedSymbol() {
        const sel = document.getElementById("symbol-select");
        return sel ? sel.value : "BTC";
      }

      function toggleSection(id) {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.classList.contains("open")) {
          el.classList.remove("open");
        } else {
          el.classList.add("open");
        }
      }

      function setBiasBadge(bias) {
        const el = document.getElementById("summary-bias");
        if (!el) return;
        const lc = (bias || "").toLowerCase();
        el.className = "summary-bias";
        if (lc === "bullish") el.classList.add("summary-bias-bullish");
        else if (lc === "bearish") el.classList.add("summary-bias-bearish");
        else el.classList.add("summary-bias-neutral");
        el.textContent = lc || "neutral";
      }

      function updateSummary(data, bias, symbol, r30) {
        const block = document.getElementById("summary-block");
        if (!block) return;

        const lev = data.levels;
        document.getElementById("summary-symbol").textContent = symbol;
        document.getElementById("summary-support").textContent = lev.daily_support;
        document.getElementById("summary-resistance").textContent = lev.daily_resistance;
        document.getElementById("summary-breakout").textContent = lev.breakout_trigger;
        document.getElementById("summary-breakdown").textContent = lev.breakdown_trigger;
        document.getElementById("summary-range").textContent = r30.low + " - " + r30.high;
        setBiasBadge(bias);
        block.style.display = "block";
      }

      async function handleMembershipOrError(res, statusEl, outEl, actionLabel) {
        if (res.status === 403) {
          let detailMsg = "This action is not included in your current membership.";
          try {
            const data = await res.json();
            if (data && data.detail) detailMsg = data.detail;
          } catch (e) {}
          statusEl.textContent = "membership limit";
          statusEl.className = "status-line status-error";
          outEl.textContent =
            detailMsg +
            "\\n\\nIf you want Auto DMR or multi-symbol access, click the 'Upgrade' link in the top bar.";
          const block = document.getElementById("summary-block");
          if (block) block.style.display = "none";
          return true;
        }
        const txt = await res.text();
        statusEl.textContent = "error";
        statusEl.className = "status-line status-error";
        outEl.textContent = actionLabel + " failed (" + res.status + ").\\n" + txt;
        const block = document.getElementById("summary-block");
        if (block) block.style.display = "none";
        return true;
      }

      // Copy helpers
      function copyFullDMR() {
        const el = document.getElementById("auto-output");
        if (!el) return;
        navigator.clipboard.writeText(el.textContent || "").then(
          () => { el.textContent = el.textContent + "\\n\\n[DMR copied to clipboard]"; },
          () => {}
        );
      }
      function copyYaml() {
        const el = document.getElementById("dmr-yaml");
        if (!el) return;
        navigator.clipboard.writeText(el.textContent || "").then(
          () => {},
          () => {}
        );
      }

      // AUTO DMR
      window.runAutoDMR = async function() {
        const statusEl = document.getElementById("auto-status");
        const outEl = document.getElementById("auto-output");
        const yamlEl = document.getElementById("dmr-yaml");
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
          const bias = data.report && data.report.bias ? data.report.bias : "neutral";
          const displaySymbol = data.symbol_short || data.symbol || symbol;

          updateSummary(data, bias, displaySymbol, r30);

          // Sections
          const sections = (data.report && data.report.sections) || {};
          const secIds = [
            ["market_momentum", "dmr-sec1"],
            ["sentiment_snapshot", "dmr-sec2"],
            ["key_structure", "dmr-sec3"],
            ["strategy_outlook", "dmr-sec4"],
            ["execution_notes", "dmr-sec5"],
          ];
          secIds.forEach(([key, id]) => {
            const el = document.getElementById(id);
            if (el) {
              const text = sections[key] || "";
              el.textContent = text;
              if (text && !el.classList.contains("open")) {
                el.classList.add("open");
              }
            }
          });

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
          outEl.textContent = fullReport;

          if (yamlEl) {
            yamlEl.textContent = (data.report && data.report.yaml_block) || "No YAML block available.";
          }
        } catch (err) {
          statusEl.textContent = "error";
          statusEl.className = "status-line status-error";
          outEl.textContent = "Exception while running auto DMR:\\n" + err;
          const block = document.getElementById("summary-block");
          if (block) block.style.display = "none";
        }
      };

      // AUTO-FILL
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

      // KTBB Assistant (same as before)
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

