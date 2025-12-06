from typing import Dict, Any
from datetime import datetime
import os

from fastapi import (
    FastAPI,
    Form,
    Depends,
    Request,
    Response,
    HTTPException,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sse_engine import compute_dm_levels              # KTBB SSE ENGINE
from data_feed import build_auto_inputs, resolve_symbol   # BINANCE AUTO INPUTS
from dmr_report import generate_dmr_report            # DMR text generator
from membership import (
    User,
    Tier,
    ensure_can_use_auto,
    ensure_can_use_symbol_auto,
    ensure_can_use_gpt,
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
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI) else None

# -------------------------------------------------------------------
# FASTAPI APP + DB INIT
# -------------------------------------------------------------------

app = FastAPI(title="KTBB – Trading Battle Box API")

# Create DB tables on startup
init_db()

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
# Helpers
# -------------------------------------------------------------------


def _short_symbol(symbol: str) -> str:
    """
    Try to map a symbol into a short KTBB-style symbol for gating.
    """
    sym = symbol.upper()
    if sym.endswith("USDT"):
        return sym[:-4]
    return sym


def _compute_dmr_result(symbol: str, inputs: Dict[str, float]) -> Dict[str, Any]:
    """
    Shared helper for manual + auto DMR:
    - runs SSE engine
    - builds HTF shelves / 30m range
    - calls DMR narrative engine
    Returns a dict shaped exactly how the frontend JS expects.
    """
    # 1) Run SSE engine
    levels_full = compute_dm_levels(
        h4_supply=inputs["h4_supply"],
        h4_demand=inputs["h4_demand"],
        h1_supply=inputs["h1_supply"],
        h1_demand=inputs["h1_demand"],
        weekly_val=inputs["weekly_val"],
        weekly_poc=inputs["weekly_poc"],
        weekly_vah=inputs["weekly_vah"],
        f24_val=inputs["f24_val"],
        f24_poc=inputs["f24_poc"],
        f24_vah=inputs["f24_vah"],
        morn_val=inputs["morn_val"],
        morn_poc=inputs["morn_poc"],
        morn_vah=inputs["morn_vah"],
        r30_high=inputs["r30_high"],
        r30_low=inputs["r30_low"],
    )

    # Split HTF shelves out of levels dict for nicer JSON
    htf_shelves = {
        "resistance": levels_full.get("htf_resistance", []),
        "support": levels_full.get("htf_support", []),
    }
    levels = {
        k: v
        for k, v in levels_full.items()
        if not k.startswith("htf_")
    }

    # 30m OR as a separate sub-dict
    range_30m = {
        "high": inputs["r30_high"],
        "low": inputs["r30_low"],
    }

    # 2) DMR narrative engine
    today = datetime.utcnow().strftime("%Y-%m-%d")
    binance_symbol = resolve_symbol(symbol)
    report = generate_dmr_report(
        symbol=binance_symbol,
        date_str=today,
        inputs=inputs,
        levels=levels,
        htf_shelves=htf_shelves,
        range_30m=range_30m,
    )

    return {
        "symbol": binance_symbol,
        "symbol_short": _short_symbol(symbol),
        "inputs": inputs,
        "levels": levels,
        "htf_shelves": htf_shelves,
        "range_30m": range_30m,
        "report": report,
        "date": today,
    }


# -------------------------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ktbb", "version": "0.8.0"}


# -------------------------------------------------------------------
# MAIN DASHBOARD UI (HTML)
# -------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def show_form(user: User = Depends(get_current_user)):
    """
    Main dashboard: three-panel layout (inputs / DMR / Assistant).

    NOTE: This is a pure string HTML template; we just swap in the user label.
    """

    if user.id == 0:
        user_label = "Anonymous (dev default Tier3)"
        tier_label = "tier3_multi_gpt"
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
      a { color: #93c5fd; text-decoration: none; font-size: 0.8rem; }
      a:hover { text-decoration: underline; }

      .shell {
        max-width: 1280px;
        margin: 0 auto;
        padding: 24px 16px 40px;
      }

      .app-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      }
      .app-header-left {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .logo-pill {
        width: 32px;
        height: 32px;
        border-radius: 9999px;
        background: linear-gradient(135deg, #3b82f6, #22c55e);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.9rem;
      }
      .brand-lines {
        display: flex;
        flex-direction: column;
      }
      .brand-main {
        font-weight: 600;
        letter-spacing: 0.04em;
        font-size: 0.9rem;
      }
      .brand-sub {
        font-size: 0.78rem;
        color: #9ca3af;
      }
      .app-header-right {
        text-align: right;
        font-size: 0.8rem;
      }
      .badge-tier {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 9999px;
        background: #111827;
        border: 1px solid #1f2937;
        margin-left: 4px;
        font-size: 0.7rem;
      }

      h1 {
        margin: 4px 0 2px;
        font-size: 1.3rem;
      }
      h2 {
        margin: 0 0 14px;
        font-weight: 400;
        color: #9ca3af;
        font-size: 0.9rem;
      }

      .layout {
        display: grid;
        grid-template-columns: 1.1fr 1.5fr 1.1fr;
        gap: 14px;
      }
      @media (max-width: 1024px) {
        .layout {
          grid-template-columns: 1fr;
        }
      }

      .card {
        background: #020617;
        border-radius: 14px;
        border: 1px solid #1f2937;
        padding: 12px 12px 14px;
        box-shadow: 0 10px 25px rgba(15,23,42,0.6);
      }

      label {
        font-size: 0.78rem;
        color: #9ca3af;
        display: block;
        margin-bottom: 2px;
      }
      input[type="number"],
      select {
        width: 100%;
        box-sizing: border-box;
        background: #020617;
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 5px 7px;
        color: #e5e7eb;
        font-size: 0.82rem;
      }
      input:focus,
      select:focus {
        outline: none;
        border-color: #60a5fa;
        box-shadow: 0 0 0 1px #60a5fa33;
      }

      .symbol-select {
        margin-top: 4px;
        margin-bottom: 8px;
      }

      .row {
        display: flex;
        gap: 6px;
      }
      .row > div {
        flex: 1;
      }
      .row-2 {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
      }

      button {
        font-size: 0.8rem;
        border-radius: 9999px;
        border: none;
        cursor: pointer;
        padding: 6px 12px;
      }
      .btn-primary {
        background: #60a5fa;
        color: #020617;
        font-weight: 600;
      }
      .btn-secondary {
        background: #111827;
        color: #e5e7eb;
        border: 1px solid #1f2937;
      }
      .btn-accent {
        background: #22c55e;
        color: #022c22;
        font-weight: 600;
      }
      .btn-primary:hover { background: #3b82f6; }
      .btn-secondary:hover { background: #020617; }
      .btn-accent:hover { background: #16a34a; }

      .button-row {
        margin-top: 10px;
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
        font-size: 0.78rem;
        color: #9ca3af;
        margin-top: 2px;
      }
      .status-ok { color: #22c55e; }
      .status-error { color: #f97316; }

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
      details summary::-webkit-details-marker { display: none; }
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
        <!-- LEFT: inputs -->
        <div class="card">
          <label for="symbol-select">Symbol</label>
          <select id="symbol-select" class="symbol-select">
            <option value="BTC">BTC / BTCUSDT</option>
            <option value="ETH">ETH / ETHUSDT</option>
            <option value="XRP">XRP / XRPUSDT</option>
            <option value="SOL">SOL / SOLUSDT</option>
          </select>

          <form id="dmr-form" method="post" action="/run-dmr">
            <div class="button-row">
              <button type="button" class="btn-secondary" onclick="autoFillFromBTC()">Auto-fill</button>
              <button type="button" class="btn-accent" onclick="runAutoDMR()">Run Auto DMR</button>
            </div>
            <div class="button-row">
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

        <!-- CENTER: DMR summary + sections -->
        <div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <h3 style="margin:0; font-size:1rem;">Auto DMR – Selected Symbol</h3>
            <span id="auto-status" class="status-line">status: waiting.</span>
          </div>

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
                <div class="dmr-section-title">3) Key Structure &amp; Levels</div>
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

          <div class="button-row" style="margin-top:10px;">
            <button type="button" class="btn-secondary" onclick="copyFullDMR()">Copy full DMR</button>
            <button type="button" class="btn-secondary" onclick="copyYaml()">Copy YAML</button>
          </div>

          <h4 style="margin-top:10px; margin-bottom:4px; font-size:0.9rem;">Full DMR Report</h4>
          <pre id="auto-output" class="mono">Select a symbol and click "Run Auto DMR" to pull shelves, FRVPs and levels from Binance US.</pre>

          <h4 style="margin-top:10px; margin-bottom:4px; font-size:0.9rem;">YAML Block</h4>
          <pre id="dmr-yaml" class="mono">YAML will appear here after Auto DMR runs.</pre>
        </div>

        <!-- RIGHT: KTBB Assistant -->
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
        statusEl.textContent = "running.";
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

          const htf = data.htf_shelves;
          const r30 = data.range_30m;
          const bias = data.report && data.report.bias ? data.report.bias : "neutral";
          const displaySymbol = data.symbol_short || data.symbol || symbol;

          updateSummary(data, bias, displaySymbol, r30);

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
                  "  4H Supply: " + (htf.resistance?.[0]?.level ?? "-") + "\\n" +
                  "  4H Demand: " + (htf.support?.[0]?.level ?? "-") + "\\n" +
                  "  1H Supply: " + (htf.resistance?.[1]?.level ?? "-") + "\\n" +
                  "  1H Demand: " + (htf.support?.[1]?.level ?? "-")
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
        statusEl.textContent = "auto-filling.";
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

      // KTBB Assistant
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
# MANUAL DMR (form POST)
# -------------------------------------------------------------------


@app.post("/run-dmr", response_class=HTMLResponse)
async def run_manual_dmr(
    request: Request,
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
    """
    Simple manual DMR path.
    Renders a minimal result page; primary UX is Auto DMR.
    """
    symbol = "BTC"  # manual form currently doesn't send symbol; default BTC
    inputs = {
        "h4_supply": h4_supply,
        "h4_demand": h4_demand,
        "h1_supply": h1_supply,
        "h1_demand": h1_demand,
        "weekly_val": weekly_val,
        "weekly_poc": weekly_poc,
        "weekly_vah": weekly_vah,
        "f24_val": f24_val,
        "f24_poc": f24_poc,
        "f24_vah": f24_vah,
        "morn_val": morn_val,
        "morn_poc": morn_poc,
        "morn_vah": morn_vah,
        "r30_high": r30_high,
        "r30_low": r30_low,
    }

    result = _compute_dmr_result(symbol, inputs)
    report = result["report"]
    levels = result["levels"]

    html = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>KTBB Manual DMR Result</title>
      </head>
      <body style="background:#020617; color:#e5e7eb; font-family:system-ui;">
        <div style="max-width:800px; margin:32px auto;">
          <h2>Manual DMR Result – {result['symbol_short']} ({result['symbol']})</h2>
          <p>Daily Support: {levels['daily_support']}</p>
          <p>Daily Resistance: {levels['daily_resistance']}</p>
          <p>Breakout Trigger: {levels['breakout_trigger']}</p>
          <p>Breakdown Trigger: {levels['breakdown_trigger']}</p>
          <pre style="background:#020617; border:1px solid #1f2937; border-radius:8px; padding:10px; white-space:pre-wrap;">
{report.get('full_text', '')}
          </pre>
          <p><a href="/" style="color:#93c5fd;">← Back to dashboard</a></p>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# AUTO DMR API
# -------------------------------------------------------------------


@app.get("/api/dmr/auto-inputs")
async def api_auto_inputs(
    symbol: str = "BTC",
    user: User = Depends(get_current_user),
):
    """
    Pull shelves/FRVPs/OR from Binance and return the raw inputs
    used by SSE engine.
    """
    ensure_can_use_auto(user)
    ensure_can_use_symbol_auto(user, _short_symbol(symbol))

    try:
        inputs = build_auto_inputs(symbol)
    except Exception as exc:  # broad but we want to surface errors cleanly
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error fetching data for {symbol}: {exc}",
        ) from exc

    return {
        "symbol": resolve_symbol(symbol),
        "symbol_short": _short_symbol(symbol),
        "inputs": inputs,
    }


@app.post("/api/dmr/run-auto")
async def api_run_auto_dmr(
    symbol: str = "BTC",
    user: User = Depends(get_current_user),
):
    """
    Full auto DMR pipeline:
      - fetch inputs from Binance
      - run SSE engine
      - run DMR narrative generator
    """
    ensure_can_use_auto(user)
    ensure_can_use_symbol_auto(user, _short_symbol(symbol))

    try:
        inputs = build_auto_inputs(symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error fetching data for {symbol}: {exc}",
        ) from exc

    result = _compute_dmr_result(symbol, inputs)
    return JSONResponse(result)


# -------------------------------------------------------------------
# KTBB ASSISTANT (GPT)
# -------------------------------------------------------------------


@app.post("/api/assistant/dmr-chat")
async def api_dmr_chat(
    payload: AssistantChatRequest,
    user: User = Depends(get_current_user),
):
    """
    GPT-powered assistant. Uses today's DMR as context.
    """
    ensure_can_use_gpt(user)

    if openai_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="KTBB Assistant is not configured (missing OPENAI_API_KEY or openai package).",
        )

    symbol = payload.symbol or "BTC"
    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question is required.",
        )

    # Compute a fresh DMR snapshot as context
    try:
        inputs = build_auto_inputs(symbol)
        dmr = _compute_dmr_result(symbol, inputs)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error building DMR context for assistant: {exc}",
        ) from exc

    dmr_text = dmr["report"].get("full_text", "")
    today = dmr.get("date") or datetime.utcnow().strftime("%Y-%m-%d")

    messages = [
        {
            "role": "system",
            "content": (
                "You are KTBB Assistant, a professional trading coach. "
                "Use the Daily Market Review (DMR) provided to answer questions. "
                "Be clear, concise and practical. Do not give investment, tax or legal advice; "
                "focus on interpreting the DMR structure, levels and trade posture."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Symbol: {dmr['symbol_short']} ({dmr['symbol']})\\n"
                f"Date: {today}\\n\\n"
                f"Here is today's full DMR:\\n\\n{dmr_text}\\n\\n"
                f"Trader's question: {question}"
            ),
        },
    ]

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
        )
        answer = completion.choices[0].message.content  # type: ignore[assignment]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error calling KTBB Assistant model: {exc}",
        ) from exc

    return {"answer": answer}


# -------------------------------------------------------------------
# AUTH JSON API (for future frontend)
# -------------------------------------------------------------------


@app.post("/auth/register")
async def register_user(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
):
    tier = payload.tier or Tier.TIER2_SINGLE_AUTO
    user_model = create_user(db, payload.email, payload.password, tier=tier)
    return {
        "id": user_model.id,
        "email": user_model.email,
        "tier": user_model.tier,
    }


@app.post("/auth/login")
async def login_user(
    payload: LoginRequest,
    db: Session = Depends(get_db),
):
    user_model = authenticate_user(db, payload.email, payload.password)
    if not user_model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token = create_session_token(db, user_model.id)
    resp = JSONResponse(
        {
            "message": "logged in",
            "email": user_model.email,
            "tier": user_model.tier,
        }
    )
    # 7 days for now
    resp.set_cookie(
        "ktbb_session",
        token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
    )
    return resp


@app.post("/auth/logout")
async def logout_user(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get("ktbb_session")
    if token:
        delete_session(db, token)
    resp = JSONResponse({"message": "logged out"})
    resp.delete_cookie("ktbb_session")
    return resp


# -------------------------------------------------------------------
# Very simple auth UIs so header links don't 404
# -------------------------------------------------------------------


@app.get("/auth/login-ui", response_class=HTMLResponse)
async def login_ui():
    html = """
    <html><body style="background:#020617; color:#e5e7eb; font-family:system-ui;">
      <div style="max-width:360px; margin:40px auto;">
        <h2>Login</h2>
        <form method="post" action="/auth/login-ui">
          <div><label>Email</label><input name="email" type="email" required /></div>
          <div><label>Password</label><input name="password" type="password" required /></div>
          <button type="submit">Login</button>
        </form>
        <p style="margin-top:12px;"><a href="/" style="color:#93c5fd;">Back to dashboard</a></p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


@app.post("/auth/login-ui")
async def login_ui_post(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user_model = authenticate_user(db, email, password)
    if not user_model:
        return HTMLResponse(
            "<html><body>Invalid credentials. <a href=\"/auth/login-ui\">Try again</a>.</body></html>",
            status_code=401,
        )

    token = create_session_token(db, user_model.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        "ktbb_session",
        token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
    )
    return resp


@app.get("/auth/register-ui", response_class=HTMLResponse)
async def register_ui():
    html = """
    <html><body style="background:#020617; color:#e5e7eb; font-family:system-ui;">
      <div style="max-width:360px; margin:40px auto;">
        <h2>Register (defaults to Tier 2)</h2>
        <form method="post" action="/auth/register-ui">
          <div><label>Email</label><input name="email" type="email" required /></div>
          <div><label>Password</label><input name="password" type="password" required /></div>
          <button type="submit">Create account</button>
        </form>
        <p style="margin-top:12px;"><a href="/" style="color:#93c5fd;">Back to dashboard</a></p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


@app.post("/auth/register-ui")
async def register_ui_post(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        create_user(db, email, password, tier=Tier.TIER2_SINGLE_AUTO)
    except HTTPException as exc:
        return HTMLResponse(
            f"<html><body>Error: {exc.detail}. "
            f'<a href="/auth/register-ui">Try again</a>.</body></html>',
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/auth/login-ui", status_code=302)


@app.get("/auth/logout-ui")
async def logout_ui(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get("ktbb_session")
    if token:
        delete_session(db, token)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("ktbb_session")
    return resp


@app.get("/billing/upgrade-ui", response_class=HTMLResponse)
async def upgrade_ui():
    html = """
    <html><body style="background:#020617; color:#e5e7eb; font-family:system-ui;">
      <div style="max-width:480px; margin:40px auto;">
        <h2>Upgrade (placeholder)</h2>
        <p>This is where Stripe / billing flows will live.</p>
        <p>For now, upgrades can be handled manually.</p>
        <p><a href="/" style="color:#93c5fd;">Back to dashboard</a></p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)
