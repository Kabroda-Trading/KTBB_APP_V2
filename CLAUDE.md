# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Running the App

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Production deploys to Render at kabroda.com on port 10000. There is no test suite — validate by running the server and hitting routes.

## Required Environment Variables

```
ANTHROPIC_API_KEY     # Powers all 6 CrewAI agents and the Operator Commlink
SESSION_SECRET        # Cookie signing key
DATABASE_URL          # Default: sqlite:///./kabroda.db (prod: PostgreSQL)
PUBLIC_BASE_URL       # Used to auto-detect HTTPS for secure cookies
ADMIN_EMAIL           # Bootstrap admin on first boot
ADMIN_PASSWORD        # Bootstrap admin password
COINALYZE_API_KEY     # Optional — open interest fuel multiplier
```

---

## What This System Actually Does

Kabroda is a session-based crypto trading intelligence system. It does not give generic buy/sell signals. It mathematically derives a "battle zone" at the start of each trading session, then monitors whether price has earned permission to trade out of it.

### The Core Concept: Session Anchors and Triggers

At the open of each trading session (defined in `session_manager.py`), the system enters a **30-minute calibration window**. During this window, the highest high and lowest low form the **30M Range** (`r30_high`, `r30_low`). These bounds are the raw material for trigger calculation.

After 30 minutes, `sse_engine.py` computes two permanent levels for the session:

- **Breakout Trigger (`bo`)** — the price where a confirmed long trade becomes valid. Derived from `max(r30_high, 24h VRVP Value Area High)`, then pushed a minimum distance from the anchor to prevent false triggers.
- **Breakdown Trigger (`bd`)** — the price where a confirmed short trade becomes valid. Derived from `min(r30_low, 24h VRVP Value Area Low)`, same logic inverted.

These two triggers are the **Single Source of Truth (SSOT)** for the entire session. They are frozen into a `SessionLock` database record and never recomputed. Every downstream calculation — targets, stops, MAS analysis, structure state — derives from them.

### The Measured Move Rule (Inviolable)

All price targets are computed from the **distance between the two triggers**, not from arbitrary RR ratios:

```
Distance = breakout_trigger - breakdown_trigger

T1 = Entry ± Distance          (1:1 measured move)
T2 = Entry ± (Distance × 1.618)  (Fibonacci extension)
T3 = Entry ± (Distance × 2.618)  (Fibonacci extension)
```

**Nothing overrides this math.** The CRO agent is specifically instructed to reject any trade plan that uses a different target calculation. If a target is set manually or arrived at by a different method, it is wrong.

### Permission Logic: Acceptance Before Execution

Price crossing a trigger is not enough to trade. The **Structure State Engine** (`structure_state_engine.py`) counts how many consecutive 5m closes have occurred beyond the trigger. The default requirement is **2 consecutive closes** beyond the trigger line.

- 0 closes beyond → `action: HOLD FIRE`
- 1 close beyond → `action: WAIT` (acceptance in progress)
- 2+ consecutive closes beyond → `action: GO` (permission earned)

This is the "acceptance" protocol. It filters false breakouts. The 5m candles evaluated are always **post-lock only** — candles from during the 30m calibration window are never used for permission evaluation.

### The Gravity Map

The gravity system is a two-layer price memory model:

**Layer 1 — Macro Beams (Class 0, `permanence_class=0`)**: Multi-year Elliott Wave pivots mapped by `kabroda_macro_engine.py`. These are re-scanned on boot and every 24 hours. They carry a `heat_multiplier=15.0` and a `+15.0` KDE weight boost — the heaviest levels in the system. They represent structural cycle origins, wave tops, wave bottoms. In `gravity_math.py`, Class 0 levels receive a `+15.0` kinetic friction multiplier on top of their heat multiplier, making them massively visible in the density curve.

**Layer 2 — Kabroda Bedrock (Class 1/2)**: Intraday and session-level pivots logged by the gravity engine loop every 15 minutes. 4H pivots are Class 1 (`+3.0` KDE weight). Session-locked levels (triggers, daily S/R, 30m extremes) are Class 2 (`+1.5` weight via `7_DAY_KABRODA` source).

The `calculate_gravity_kde()` function transforms all stored pivots into a continuous Gaussian density wave (Bookmap-style). Each pivot emits a bell curve of influence with sigma = 15 bps of the mid-price. Overlapping pivots compound. The resulting peaks are the `kde_peaks` list injected into the MAS payload.

**Macro Fibs** (`calculate_macro_fibs()`): Separately derived from the 30-day daily swing high/low. Produces Fibonacci retracements (0.5, 0.618, 0.786) and extensions (1.272, 1.618, 2.0) in both directions for blue-sky breakout and price-discovery targets.

### The Macro Engine (Elliott Wave Scanner)

`kabroda_macro_engine.py` runs as a subprocess (not an asyncio task) on boot and every 24 hours. It:
1. Fetches up to 1500 days of daily candles for BTC, ETH, SOL from MEXC.
2. Runs a ZigZag pivot algorithm with 20% deviation threshold to strip noise.
3. Validates the resulting pivots against strict Elliott Wave rules (W4 cannot overlap W1 territory; W2 cannot break origin; etc.).
4. Writes confirmed wave levels (CYCLE_ORIGIN, BULL_WAVE_1 through _4, BEAR_WAVE_3_LOW, etc.) to `gravity_memory` as `permanence_class=0`.

These are the levels that create the heavy gravity walls the Liquidity Scavenger agent is trained to identify.

### Session AUTO Mode

`session_manager.resolve_current_session()` with `mode="AUTO"` is hardcoded to `us_ny_futures` (NY Futures, 8:30 AM ET). There is no dynamic session detection. Seven sessions are defined; manual override is passed via `manual_session_id` in the `/api/dmr/live` payload.

---

## The Six MAS Agents — What Each One Actually Does

All agents use `claude-sonnet-4-6`. They run sequentially. Each agent only has access to the data explicitly passed in its task description — they have no tools and no internet access.

### 1. Macro Structural Architect
**Input**: Daily S/R levels, macro bias (21-day weekly force), macro structure array (Elliott Wave labels from gravity_memory), macro_environment dict (SPX/DXY/VIX from Yahoo Finance).
**Job**: Determine whether the daily structure supports the session's directional bias. Identify if the market is in an impulsive trend or corrective chop based on wave labels. Assess whether traditional finance (risk-on/risk-off) is favorable.
**Good output**: "Daily structure is in BULL_WAVE_4 corrective territory with BEAR DXY and VIX below 20. Risk posture is RISK-ON. Bias: LONG."
**Fluff**: Any generic market commentary not tied to the specific wave labels or macro metrics provided.

### 2. Micro Liquidity Scavenger
**Input**: Breakout trigger, breakdown trigger, KDE peaks list (price + heat_score + intensity).
**Job**: Determine whether the airspace above the breakout trigger (for longs) or below the breakdown trigger (for shorts) is clear or blocked by a heavy KDE peak. A MAXIMUM intensity peak sitting 0.3% above the breakout trigger is a serious problem. A clear air zone means the measured move target is structurally viable.
**Good output**: "Breakout trigger at $97,450. Nearest KDE peak above is HEAVY intensity at $98,200 — 0.77% clearance. T1 at $98,900 is in clear airspace. LONG setup has viable runway."
**Fluff**: Describing what KDE means, generic liquidity commentary, vague statements about "strong levels nearby."

### 3. Kinematic Momentum Quant
**Input**: Fuel gauge (1H and 4H EMA trend + MACD momentum), 15M JEWEL (RSI, kinematic_grade, ribbon spread, EMA9/21/35/55, SMA200), micro_state (SWEET_ZONE / PULLBACK / HOSTILE_CEILING / EXHAUSTION / CHOP).
**Job**: Confirm whether there is sufficient kinetic energy for a breakout. The 15M JEWEL's `kinematic_grade` is the primary signal: PRIMED = fuel exists, OVEREXTENDED = exhaustion risk, TANGLED = no clear momentum. Cross-check with 1H/4H trend alignment.
**Good output**: "15M JEWEL: PRIMED. RSI 61. Ribbon spread 0.42% — not overextended. 1H: BULLISH trend, POSITIVE momentum. 4H: BULLISH tide. Harmonic state: SWEET_ZONE. System has velocity for a breakout."
**Fluff**: Describing what RSI means, generic momentum commentary, repeating input values without synthesis.

### 4. Chief Risk Officer (Ghost Lead)
**Input**: Reports from the three upstream agents + breakout/breakdown triggers + RAG memory (last 5 closed trades: win/loss count, net PnL, performance warning if losses > wins).
**Job**: The final gatekeeper. Synthesizes the three agent reports. If any two reports conflict, or if the macro posture is RISK-OFF/HIGH VOLATILITY, reject the setup. Apply Measured Move math to calculate exact entry, stop, and T1/T2/T3. The stop loss is always the opposing trigger (if entering long at breakout, stop = breakdown trigger). Consult the memory bank — if recent performance is poor, reject marginal setups. Output a plain-English execution plan with exact prices. **Do NOT output JSON.**
**Good output**: "APPROVED. LONG entry at $97,450 on breakout acceptance. Stop: $96,100 (breakdown trigger, 1.38% risk). T1: $98,800 (+$1,350), T2: $99,634 (+1.618R), T3: $100,947 (+2.618R). Macro aligned, airspace clear, fuel primed. Memory bank: 3W/1L, system performing. Execute."
**Fluff**: Hedged language, "consider the possibility that," restating what the upstream agents said without a verdict.

### 5. Chief Content Officer
**Input**: The CRO's plain-English execution plan (from task context chain).
**Job**: Format the CRO's output into a Markdown newsletter article using Kabroda terminology. Then emit the complete `ExecutiveBrief` Pydantic JSON with the formatted Markdown placed into `formatted_newsletter_md`. This task carries `output_pydantic=ExecutiveBrief` — CrewAI will attempt to parse the output as that schema. If parsing fails, `task_cco.output.pydantic` is `None` and the system logs `MAS_ERROR`.
**Required Pydantic fields**: `approval_status` (APPROVED/REJECTED/WAITING_FOR_15M), `tactical_brief`, `bias` (LONG/SHORT/NEUTRAL), `entry_price`, `stop_loss`, `t1`, `t2`, `t3`, `formatted_newsletter_md`.
**Kabroda terminology to use**: "Kinetic Friction," "Ghost Lead Verdict," "Tactical Perimeter," "Measured Move," "Permission Earned."
**Good output**: A Markdown article with a clear header, the verdict, exact numbers, and the rationale. No generic financial disclaimers.

### 6. Intel Auditor (Standalone — Not in Main MAS Crew)
Only used by `POST /api/research/audit-intel`. Takes a foreign signal (MetaSignals format), compares it against the current session's Kabroda SSOT, and outputs `IntelAuditReport` (verdict: CONFIRMED/REJECTED/HIGH_RISK, kabroda_variance, recalculated_target_1). Not part of the main 5-agent sequential crew.

---

## The Symbol Format Rule

**All DB operations must use `BTC/USDT` format (slash-separated).** Raw API inputs arrive as `BTCUSDT`. `_normalize_symbol()` in `battlebox_pipeline.py` converts them. Use it before any DB write or MAS trigger. The War Room normalizes via `.replace("USDT", "/USDT")`. Inconsistency here causes CampaignLog and SessionLock queries to silently miss — this was the original cause of the CCO brief stuck on PENDING.

The `gravity_memory` table is an exception: `kabroda_macro_engine.py` stores symbols as `BTCUSDT` (no slash), because it strips the slash via `.replace("/", "")`. The `calculate_gravity_kde()` function also strips the slash when querying. Do not change this — it is consistent within the gravity subsystem.

---

## CampaignLog Lifecycle

`CampaignLog` is not created by any user-facing route. It is created by `_inject_brief_to_database()` in `kabroda_mas_flow.py` as an **upsert** — if no record exists for `(symbol, session_id, date_key)`, it creates one using the `ExecutiveBrief` output. Fields `grade` and `total_contracts` default to `"MAS_AUTO"` and `0.0`.

The `ledger_closing_engine.py` monitors all records where `mas_approval_status == 'APPROVED'` and `closed_at IS NULL`. It fetches live prices from MEXC every 60 seconds and closes records at T1 (CLOSED_WIN, +1R) or SL (CLOSED_LOSS, -1R). Closed records become the RAG memory bank fed to the CRO on the next MAS run.

---

## What Must Never Be Changed

1. **The Measured Move formula.** `T1 = Entry ± (bo - bd)`. T2 and T3 use 1.618× and 2.618× that distance. No exceptions. Do not introduce fixed RR ratios (1:2, 1:3) anywhere.

2. **The 30-minute session lock.** The calibration window is exactly 1800 seconds from `anchor_time`. Levels computed during this window are the SSOT. They are never recomputed mid-session once locked, regardless of how much price moves.

3. **The acceptance gate.** Price crossing a trigger does NOT grant permission. Two consecutive 5m closes beyond the trigger are required. Do not reduce this to 1 or remove the check — it is the primary false-breakout filter.

4. **Class 0 KDE weighting.** `permanence_class=0` levels receive `+15.0` kinetic friction in the KDE calculation. This ensures Elliott Wave macro beams dominate the density curve and are visible as true walls. Do not reduce this multiplier.

5. **The stop loss assignment (15M).** Stop loss is *not* the raw opposing trigger. `trade_structure_analyst.py`'s `_structural_stop_long()`/`_structural_stop_short()` compute the actual stop as `r30_low − ATR×0.5` (long) / `r30_high + ATR×0.5` (short), snapped a further `ATR×0.25` beyond any intercepting HEAVY/MAXIMUM gravity wall. The raw opposing trigger (`bd`/`bo`) is retained only as an audit field (`original_stop` in `structure_reasoning`) — it is never the executable stop. T1/T2/T3 remain pinned to the raw trigger distance (rule #1 above) regardless of this stop adjustment — entry-to-stop and entry-to-target distances are not guaranteed equal, so realized R is computed at close time from the actual stored entry/stop/target values (`ledger_closing_engine.py`'s `_frac_r()`), never assumed to be a clean ±1R. Do not widen or tighten the ATR/wall-adjustment coefficients without evidence — this is the proven, live rule, not a placeholder.

6. **`_inject_brief_to_database` as an upsert.** It must create a new `CampaignLog` if one doesn't exist. If you change it back to update-only, MAS output is silently discarded.

7. **Symbol normalization before DB writes.** Always call `_normalize_symbol()` or equivalent before writing to `session_locks` or `campaign_logs`. The `gravity_memory` table uses the no-slash format — do not change that either.

---

## Database Schema Notes

Schema changes are raw `ALTER TABLE` statements wrapped in `try/except` inside `init_db()` in `database.py`. There is no migration framework. Add new columns there using the same pattern. The `try/except` silently skips if the column already exists, making it safe to re-run on existing databases.

## Background Tasks

Two tasks start on app boot via `lifespan()` in `main.py`:

- **Gravity Ingestion Loop** (`gravity_engine.py`) — scans 4H/1H/1D pivots for BTC, ETH, SOL every 15 minutes. Logs supply/demand pivots to `gravity_memory`. Also triggers `kabroda_macro_engine.py` as a subprocess on boot and every 24 hours (~96 loop iterations).
- **Ledger Closing Loop** (`ledger_closing_engine.py`) — checks live MEXC prices against open APPROVED campaigns every 60 seconds.

The macro engine (`kabroda_macro_engine.py`) runs as a **subprocess**, not an asyncio task — it has its own event loop and fetches 1500 days of daily data, which would block the main loop.

## The Unauthenticated Endpoint

`GET /api/gravity/scan` requires no login. The War Room JS polls it every 60 seconds to update the gravity map and KPI cards. Do not add sensitive position data or user-specific data to its response.
