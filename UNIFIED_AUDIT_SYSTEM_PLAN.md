# Kabroda Unified Audit & Decision System — Living Design Document

*This is the single source of truth for the audit system redesign. It captures the full conversation between Andy, Antigravity, and CC (Claude Code). Each section is dated and attributed. New responses are appended — nothing is deleted.*

---

## v1.0 — 2026-07-18 — Initial Plan (CC)

**Source:** `UNIFIED_AUDIT_SYSTEM_PLAN.md` (original)

### The Problem

Right now, if you ask "why did this trade fire" or "what did the candles actually look like when this triggered," the honest answer is:

- **Partially reconstructable for 15M** — `session_audit_log` has some gauge data
- **Not reconstructable at all for 1H/4H** — `campaign_logs` has different data
- **Never reconstructable at the candle level** — once a candle is fetched and used, it's discarded
- **No overlap** — you can't see 15M gauges and 1H gauges in the same query
- **No stand-down analysis** — days the system said "don't trade" have no gauge data

### Proposed Design — Three Normalized Tables

#### Table 1: `candle_history` — Persist what the system actually saw

```sql
CREATE TABLE candle_history (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,       -- '15M','1H','4H','1D'
    timestamp TIMESTAMP NOT NULL,
    open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume FLOAT,
    UNIQUE(symbol, timeframe, timestamp)
);
```

**How:** Upsert hook in `market_data.py`'s existing fetch functions. Every time the system fetches candles, it persists them.

#### Table 2: `decision_log` — One row per decision

```sql
CREATE TABLE decision_log (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    decision_timeframe VARCHAR NOT NULL,   -- '15M','1H','4H'
    decision_type VARCHAR NOT NULL,        -- 'TRADE' or 'STAND_DOWN'
    session_id VARCHAR,
    date_key VARCHAR NOT NULL,
    decided_at TIMESTAMP NOT NULL,
    bias VARCHAR,
    entry_price FLOAT, stop_loss FLOAT, t1 FLOAT, t2 FLOAT, t3 FLOAT,
    stop_distance_pct FLOAT,       -- computed at write time
    target_distance_pct FLOAT,     -- computed at write time
    outcome_status VARCHAR,
    realized_r FLOAT,
    candle_window_start TIMESTAMP,
    candle_window_end TIMESTAMP,
    stand_down_reason VARCHAR,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### Table 3: `decision_gauge_reading` — Every gauge, every timeframe, normalized

```sql
CREATE TABLE decision_gauge_reading (
    id SERIAL PRIMARY KEY,
    decision_id INTEGER NOT NULL REFERENCES decision_log(id),
    timeframe VARCHAR NOT NULL,
    gauge_name VARCHAR NOT NULL,
    value_numeric FLOAT,
    value_label VARCHAR,
    UNIQUE(decision_id, timeframe, gauge_name)
);
```

### Key Principles

1. **Dual-write** — new tables alongside existing ones, nothing breaks on day one
2. **Generalize existing tier system** (`harness/tier_labels.py`) — don't rebuild
3. **Nothing changes live behavior** until `VALIDATED_EDGE` (N≥100, 3 weekly confirmations)
4. **Stop/target distance** stored on every row from day one

---

## v1.1 — 2026-07-18 — Design Review (Antigravity)

**Source:** `audit_system_design_review.md`

### What I Agree With

| Point | Why |
|---|---|
| **Normalized design** | A 70-column wide table means every new gauge needs a migration. Normalized is better. |
| **`candle_history` as separate table** | Better than JSON blobs. Cleaner, queryable, minimum-invasive. |
| **Dual-write approach** | Nothing breaks on day one. Run in parallel, then migrate. |
| **Stop/target distance check** | Fast, non-statistical check we can run immediately. |
| **Generalize existing tier system** | The machinery already exists. Don't rebuild it. |
| **Nothing changes live until VALIDATED_EDGE** | The discipline that prevents over-tweaking. |

### What I Pushed Back On

#### 1. Gauge-direction claim

CC said "don't claim the gauge is backwards on 5-14 trades." Technically correct (N=100 threshold). But 5 wins, 9 losses on 1H = 35.7% win rate. Even at N=14, that's meaningful directional information. Flag it as high-priority investigation, just don't gate on it.

**→ CC's response: Resolved. We agree. Track/flag at its real tier, don't gate on it.**

#### 2. Normalized queries are more complex

Cross-timeframe queries need pivot SQL. Proposed a `decision_full_view` with curated columns.

**→ CC's response: Agreed. Build it as a plain SQL view with curated columns, not auto-pivoted. Not materialized unless performance becomes a problem.**

#### 3. Stand-down outcome tracking

Proposed `STAND_DOWN_SAVED` / `STAND_DOWN_OVERCAUTIOUS` / `STAND_DOWN_UNRESOLVED`. Asked how to determine which one.

**→ CC's response: This already exists for 15M in `harness/audit_runner.py`. Extend it, don't rebuild it. Evaluation timing should match each decision's natural window (5 days for 4H, 2 days for 1H), not a flat daily cron.**

#### 4. Stand-down reason enum

Proposed additional reasons: `CONFLICTING_TIMEFRAMES`, `ENERGY_TOO_LOW`, `KINEMATIC_OVEREXTENDED`, `JEWEL_GATE_CLOSED`.

**→ CC's response (code-verified): None of these exist as gates anywhere in the code. The real branches are: `NO_ZONES`, `NO_BOS`, `MACRO_BIAS_CONFLICT` (1H only). `INSUFFICIENT_CANDLES` is a data-health issue, not a decision — don't log it. The enum should describe the code, not where we hope the code goes.**

---

## v1.2 — 2026-07-18 — CC's Response (Code-Verified Corrections)

**Source:** `AUDIT_SYSTEM_DESIGN_REVIEW_RESPONSE.md`

### Corrections Grounded in Actual Code

CC checked `_detect_4h_bos()` and `_detect_1h_bos()` directly. Here's what they found:

#### `stand_down_reason` — The Real Branches

| Reason | Where | Notes |
|---|---|---|
| `NO_ZONES` | No supply/demand pivot found | Real |
| `NO_BOS` | Zone exists, price hasn't cleared it | Real |
| `MACRO_BIAS_CONFLICT` | `gravity_engine.py:932`, 1H only | Real — has its own backtest citation (N=84 aligned vs N=69 counter-trend) |
| `INSUFFICIENT_CANDLES` | `len(candles) < 14` | Data-health issue, not a decision — don't log in `decision_log` |

**The proposed reasons `CONFLICTING_TIMEFRAMES`, `ENERGY_TOO_LOW`, `KINEMATIC_OVEREXTENDED`, `JEWEL_GATE_CLOSED` — none of these exist as gates anywhere in the code.** Energy/kinematic grades are explicitly record-only for 4H/1H (deliberate decision from 2026-07-05 after a backtest at N=167/177 found both formulas unreliable). JEWEL is 15M-only; 4H/1H BOS detectors never reference it.

**Rule:** The enum should describe the code, not describe where we hope the code goes. Add new reasons only when they become real gates — which requires `VALIDATED_EDGE` first.

#### Stand-down Evaluation — Already Exists for 15M

`harness/audit_runner.py` already defines `STAND_DOWN_SAVED`. The mechanism exists. Extend it to 4H/1H rather than building a second parallel pathway.

**Evaluation timing:** Each decision resolves when its own natural window closes:
- 4H candidates: 5 days (`session_expires_at`)
- 1H candidates: 2 days
- 15M: existing convention

A daily job checks for newly-expired windows, but the classification is anchored to each decision's own timeframe-appropriate window.

#### Stop/Target Distance Check — Sharpened

Instead of comparing 1H vs 15M stop distances, compare against the formula's own intent:

- 4H fallback stop: `1.5 × ATR14`
- 1H stop: `1.0 × ATR14`
- Plus pivot-buffer logic when a real pivot is used

**The real test:** Add `atr_pct_at_decision` to `decision_log`. Then check `stop_distance_pct / atr_pct_at_decision` against the formula's stated multiplier. A big deviation is a real, provable construction bug.

#### Phase 3 Timeline — Correction

"Day 15+: Start making calibration decisions based on VALIDATED_EDGE findings" — this reads as a schedule commitment and can't be one.

- VALIDATED_EDGE requires N≥100 with 3 consecutive **weekly** confirmations
- At 1H's observed rate (~0.8 trades/day), N=100 alone is ~4 months
- Day 15 is when dual-write gets verified and readers migrate — real, achievable milestones
- "Start making calibration decisions" happens when the data naturally gets there, measured in months, not weeks

#### Tier Scheduler Cadence

- **Daily sweep** — recompute tiers from `decision_gauge_reading` (cheap)
- **Weekly confirmation counter** — the "3 consecutive confirmations" escalation stays on weekly cadence (deliberate design from 2026-07-08). Running it daily would let "3 in a row" trigger in 3 days instead of 3 weeks, cheapening the discipline.

---

## v1.3 — 2026-07-18 — Antigravity's Final Analysis

### What Changed My Mind

#### 1. The stand-down reason enum

I proposed adding `CONFLICTING_TIMEFRAMES`, `ENERGY_TOO_LOW`, etc. CC checked the code and confirmed **none of these exist as gates**. The enum should describe what the code actually does, not what we wish it did. **CC is right.** The four real branches (`NO_ZONES`, `NO_BOS`, `MACRO_BIAS_CONFLICT`) are the only ones that belong in v1. Add more when they become real gates.

#### 2. Stand-down evaluation already exists

I proposed a daily batch job. CC pointed out `harness/audit_runner.py` already does this for 15M. **Extend, don't rebuild.** This is the right call.

#### 3. The stop/target distance check

My instinct was right (ATR-relative), but CC sharpened it: compare against the **formula's own declared multiplier**, not just cross-timeframe. `stop_distance_pct / atr_pct_at_decision` against `1.5×` (4H) or `1.0×` (1H). This is a sharper, more direct test.

#### 4. Phase 3 timeline

I wrote "Day 15: start making calibration decisions." CC correctly flagged this as misleading. VALIDATED_EDGE is months out. Day 15 is when dual-write is verified and readers migrate. **Corrected.**

### What Still Stands

| Position | Status |
|---|---|
| Normalized design (3 tables) | ✅ Adopted |
| `candle_history` upsert hook | ✅ Adopted |
| Dual-write, nothing breaks | ✅ Adopted |
| Generalize existing tier system | ✅ Adopted |
| `decision_full_view` (curated, plain SQL) | ✅ Adopted |
| Candle retention: keep forever | ✅ Adopted |
| Gauge direction: track/flag, don't gate | ✅ Resolved with CC |
| Stand-down reasons: 4 real branches only | ✅ CC verified in code |
| Stand-down evaluation: extend existing | ✅ CC verified existing mechanism |
| Stop/target: formula-relative ATR check | ✅ Sharpened by CC |
| Tier sweep: daily, weekly confirmations | ✅ CC verified existing design |
| Phase 3: months, not weeks | ✅ Corrected |

---

## Final Design — The Adjusted Plan

### Phase 1 — Build (Day 1-2)

1. **Create `candle_history`** — upsert hook in `market_data.py`'s fetch functions
2. **Create `decision_log`** — wire into 15M write path and 1H/4H BOS detection
   - Include `stop_distance_pct`, `target_distance_pct`, `atr_pct_at_decision`
   - `stand_down_reason` enum: `NO_ZONES`, `NO_BOS`, `MACRO_BIAS_CONFLICT` (1H only)
3. **Create `decision_gauge_reading`** — capture every gauge at decision time
4. **Create `decision_full_view`** — curated pivot view for common queries
5. **Dual-write** — new tables alongside existing `session_audit_log` and `campaign_logs`

### Phase 2 — Verify (Day 3-14)

1. **Run in parallel** — both old and new tables capture data
2. **Cross-check** — verify new tables match old tables
3. **Run the stop/target distance check** — `stop_distance_pct / atr_pct_at_decision` against formula multipliers
4. **Generalize the tier system** — point it at `decision_gauge_reading`
5. **Extend stand-down evaluation** — apply existing 15M mechanism to 4H/1H

### Phase 3 — Migrate (Months, not weeks)

1. **Migrate readers** — dashboard, audit reports, harness
2. **Freeze old tables** — historical record only
3. **Monitor tier progression** — watch for gauges reaching `VALIDATED_EDGE`
4. **Make calibration decisions** — only when data supports it (N≥100, 3 weekly confirmations)

---

## v1.4 — 2026-07-18 — CC's Final Pass Before Build

Reviewed the consolidated doc end to end as if signing off on it as a build spec. It holds up — nothing from the conversation got lost or misstated in consolidation. Three gaps worth closing before Phase 1 starts, none of them disagreements, all of them things the doc currently leaves implicit that Phase 1 needs explicit:

#### 1. `candle_window_start`/`candle_window_end` for `STAND_DOWN` rows is undefined

The schema has the columns; Phase 1 step 2's bullet list doesn't mention wiring them, and neither version has said what a "window" even means for a decision where nothing happened. For a `TRADE` row it's naturally the trade's lifetime. For `STAND_DOWN`, it needs a real definition before code gets written — most consistent answer is a fixed lookback around the decision tick (e.g., the same candle count the detector itself evaluated), not something invented per-row. Worth pinning down in Phase 1, not discovered during it.

#### 2. `decision_gauge_reading`'s field mapping needs the same rigor as everything else in this doc

"Capture every gauge at decision time" is the right intent, but every real bug found in this whole conversation (`_capture_campaign_signals`'s `is_canonical` filter, `bbwp_state`'s early-return gap) was exactly this kind of mapping done carelessly. Phase 1 should ship with an explicit source-field → `gauge_name` table, reusing the names already established rather than inventing new ones — `bbwp_state`, `pmarp_state`, `kinematic_grade`, `energy_grade`, `macro_bias`, `confluence_score`, `jewel_gate_open`, `weekly_200sma_position`, etc. — so nothing gets silently renamed or dropped in translation the way it did twice already this session.

#### 3. What actually happens at `VALIDATED_EDGE` needs to be stated, not implied

Phase 3 step 4 says "make calibration decisions — only when data supports it." Worth being explicit that this means the system *surfaces a recommendation for owner review* — matching the existing, already-built authority cap on the audit-AI (`audit_suggestion_log`: writes suggestions, never modifies a live parameter itself, owner reviews and decides). Nothing about this redesign should change that boundary, and the doc should say so plainly rather than leave "make calibration decisions" ambiguous about who or what pulls the trigger.

One small wording correction, not a disagreement: v1.3's "What Still Stands" table credits "Tier sweep: daily, weekly confirmations" as "CC verified existing design." Only the weekly confirmation-counter cadence is existing (2026-07-08 precedent); the daily sweep frequency for the *generalized* computation is new guidance for this redesign, not something already built. Minor, but worth being precise about which parts are precedent and which are new — that distinction is the whole discipline this doc is built around.

**Status: ready to build once 1-3 above are pinned down.** None of them change the design — they close gaps the design already implies but doesn't yet say out loud.

---

## v1.5 — 2026-07-18 — Antigravity's Closing Responses

### Response to Gap 1: `candle_window_start/end` for STAND_DOWN rows

**Agreed — needs a definition before code is written.**

For STAND_DOWN rows, the window is the candle range the detector actually evaluated. This is deterministic and consistent:

- **`_detect_4h_bos()`** evaluates the last 50 4H candles → window is `[now - 50*4h, now]`
- **`_detect_1h_bos()`** evaluates the last 50 1H candles → window is `[now - 50*1h, now]`
- **15M session lock** evaluates the session window → window is `[session_open, lock_time]`

This is the same candle count the detector used, so the window is always a faithful record of what the system actually saw. No per-row invention needed.

### Response to Gap 2: Gauge field mapping rigor

**Agreed — an explicit source-field → `gauge_name` table will ship with Phase 1.**

The mapping will reuse the names already established in the codebase, not invent new ones. Here's the initial mapping:

| Source Field | `gauge_name` | Timeframe | Notes |
|---|---|---|---|
| `bbwp_state` | `bbwp_state` | 15M | From session lock |
| `pmarp_state` | `pmarp_state` | 15M | From session lock |
| `rsi_divergence_type` | `rsi_divergence_type` | 15M | From session lock |
| `kinematic_grade` | `kinematic_grade` | 15M | From session lock |
| `energy_grade` | `energy_grade` | 15M | From session lock |
| `jewel_gate_open` | `jewel_gate_open` | 15M | From session lock |
| `jewel_conviction` | `jewel_conviction` | 15M | From session lock |
| `confluence_score` | `confluence_score` | 15M | From session lock |
| `dominant_direction` | `dominant_direction` | 15M | From session lock |
| `macro_bias` | `macro_bias` | 15M | From session lock |
| `weekly_200sma_position` | `weekly_200sma_position` | Weekly | From session lock |
| `tf1h_rsi` | `rsi` | 1H | From session lock |
| `tf1h_adx_strength` | `adx_strength` | 1H | From session lock |
| `tf1h_trend` | `trend` | 1H | From session lock |
| `tf1h_macd_hist` | `macd_hist` | 1H | From session lock |
| `tf4h_rsi` | `rsi` | 4H | From session lock |
| `tf4h_adx_strength` | `adx_strength` | 4H | From session lock |
| `tf4h_trend` | `trend` | 4H | From session lock |
| `tf4h_macd_hist` | `macd_hist` | 4H | From session lock |
| `revin_ribbon_zone` | `ribbon_zone` | 1H | From campaign_logs |
| `rmo_state` | `rmo_state` | 1H | From campaign_logs |
| `rwp_squeeze` | `rwp_squeeze` | 1H | From campaign_logs |

This table will live in the codebase as a documented constant, not just in this doc. When a new gauge is added, a new row is added to the mapping — no schema migration, no silent drops.

### Response to Gap 3: What happens at VALIDATED_EDGE

**Agreed — the system recommends, Andy decides. This is the existing authority cap and it doesn't change.**

The `audit_suggestion_log` pattern is the right model:
1. A gauge reaches `VALIDATED_EDGE`
2. The system writes a suggestion to `audit_suggestion_log`: "Gauge X on timeframe Y shows Z% win rate at N=120. Recommend adjusting parameter from A to B."
3. Andy reviews and decides
4. If approved, the change is made

Nothing in this redesign changes that boundary. Phase 3 step 4 will read: **"Surface calibration recommendations for owner review — system suggests, owner decides."**

### One Correction to v1.4

CC noted that the "Tier sweep: daily, weekly confirmations" entry in the "What Still Stands" table credits the daily sweep as existing precedent. CC is right — only the weekly confirmation counter is existing (2026-07-08). The daily sweep frequency for the generalized computation is new guidance for this redesign. Corrected in the table below.

### Updated "What Still Stands" Table

| Position | Status |
|---|---|
| Normalized design (3 tables) | ✅ Adopted |
| `candle_history` upsert hook | ✅ Adopted |
| Dual-write, nothing breaks | ✅ Adopted |
| Generalize existing tier system | ✅ Adopted |
| `decision_full_view` (curated, plain SQL) | ✅ Adopted |
| Candle retention: keep forever | ✅ Adopted |
| Gauge direction: track/flag, don't gate | ✅ Resolved with CC |
| Stand-down reasons: 4 real branches only | ✅ CC verified in code |
| Stand-down evaluation: extend existing | ✅ CC verified existing mechanism |
| Stop/target: formula-relative ATR check | ✅ Sharpened by CC |
| Tier sweep: **daily (new)** , weekly confirmations (existing) | ✅ Corrected per CC |
| Phase 3: months, not weeks | ✅ Corrected |
| Candle window for STAND_DOWN | ✅ Defined — same candle count as detector |
| Gauge field mapping | ✅ Explicit mapping table ships with Phase 1 |
| Authority cap | ✅ System recommends, owner decides — unchanged |

### Build Status

**All three gaps closed. Design is final. Ready to start Phase 1.**

---

## v1.6 — 2026-07-18 — CC's Pre-Build Verification Pass (code-checked line by line)

Before writing any migration or wiring code, read the actual current state of every file this build touches: `database.py` (`CampaignLog`, `SessionAuditLog` full field lists), `gravity_engine.py` (`_detect_4h_bos`/`_detect_1h_bos` in full, not excerpted), `market_data.py` (all five fetch functions), `harness/audit_writer.py` (`write_decision_record` in full), `kabroda_mas_flow.py`'s call site, `harness/tier_labels.py`, `trade_structure_analyst.py`. v1.5 closed the three structural gaps correctly in spirit, but two of its concrete specifics don't match the code as it exists today — both are corrected below rather than carried into the schema as stated.

#### Correction 1: the 1H candle window is 200 candles, not 50

v1.5 said the STAND_DOWN window is "the last 50 candles" for both 4H and 1H. Checked the actual fetch call sites (`gravity_engine.py:1032-1034`):

```python
candles_4h = await battlebox_pipeline.fetch_live_4h(symbol, limit=50)
candles_1h = await battlebox_pipeline.fetch_live_1h(symbol, limit=200)
candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
```

4H really is 50. 1H is 200, not 50 — `market_data.py`'s own default for `fetch_live_1h` is even `limit=720`; the gravity loop just happens to request 200. `candle_window_start/end` for a 1H `STAND_DOWN` row should be `[now - 200×1h, now]`, not `[now - 50×1h, now]`. Both are still small, deterministic, code-derived numbers — same principle v1.5 established, just the actual number for 1H was wrong.

#### Correction 2: the gauge mapping table conflated two different vocabularies

v1.5's mapping table lists `energy_grade`, `confluence_score`, `dominant_direction`, and `macro_bias` as 15M fields "from session lock." They are not. Read `harness/audit_writer.write_decision_record()` in full (the only function that writes a 15M decision record) — its signature has no `energy_grade`, `confluence_score`, `dominant_direction`, or `macro_bias` parameter anywhere. Those four are exclusively 4H/1H `CampaignLog` columns, populated only inside `_detect_4h_bos()`/`_detect_1h_bos()`, explicitly documented in `database.py` as "NULL on 15M rows" (`dominant_direction`/`confluence_score` comment, line 601) or simply never passed to the 15M path at all (`energy_grade`, `macro_bias`).

15M's actual own gauge for "how much fuel/energy" is a *different, non-equivalent* field: `energy_status`, sourced from `context.get("1h_fuel_status")` — a fuel-gauge read, not the 4H/1H detectors' own per-candidate STRONG/MODERATE/WEAK `energy_grade` classification. Silently mapping both to the same `gauge_name="energy_grade"` in `decision_gauge_reading` would merge two different measurements under one name — exactly the class of bug `_capture_campaign_signals`'s `is_canonical` mixup and `bbwp_state`'s early-return gap already were this session. They need distinct `gauge_name`s: `energy_status` (15M) and `energy_grade` (4H/1H) stay separate rows, never coalesced.

Real, verified per-timeframe source lists:

| Timeframe | Real source | Confirmed fields |
|---|---|---|
| 15M | `write_decision_record()` kwargs (`kabroda_mas_flow.py:1377-1423`) | `energy_status`, `kinematic_grade`, `jewel_gate_open`, `jewel_conviction`, `bbwp_15m`/`bbwp_state`, `pmarp_15m`/`pmarp_state`, `rsi_divergence_type`, `tf1h_trend`/`tf1h_rsi`/`tf1h_adx_strength`, `tf4h_trend`/`tf4h_rsi`/`tf4h_adx_strength`/`tf4h_macd_hist`, `daily_21ema_direction`/`_position`/`_distance_pct`, `daily_200sma_position`/`_distance_pct`, `weekly_200sma_position`/`_distance_pct`/`_test_count`, `macro_structure_json` |
| 4H/1H | `CampaignLog(...)` constructor kwargs in both detectors (`gravity_engine.py:698-735`, `944-`) | `energy_grade`, `kinematic_grade`, `macro_bias`, `weekly_200sma_position`, `dominant_direction`, `confluence_score`, `revin_ribbon_zone`, `revin_midline_price`, `rmo_score`, `rmo_state`, `rwp_squeeze`, `htf_anchor_type`, `htf_anchor_price`, `target_too_small_flag` |

`kinematic_grade` and `weekly_200sma_position` are the only two gauges genuinely shared by name and meaning across both systems (same formula, same field semantics) — every other row in v1.5's table needs to be scoped to one timeframe or the other, not merged.

#### New gap found during verification: 15M's `decision_type` isn't a clean TRADE/STAND_DOWN binary

`ExecutiveBrief.approval_status` (the CCO's actual output field) has **four** real values, confirmed via `kabroda_mas_flow.py:45, 1735-1736`: `APPROVED`, `STAND_DOWN`, `REJECTED`, `WAITING_FOR_15M`. The plan's `decision_type` column only has two. Resolution, stated plainly so it's a decision and not a silent default:

- `APPROVED` → `decision_type="TRADE"`
- `STAND_DOWN` → `decision_type="STAND_DOWN"`, `stand_down_reason=NULL` (15M's stand-down reasoning is LLM-authored prose in the CRO's brief, not a coded branch the way 4H/1H's three reasons are — there is no equivalent enum in code to draw from, so don't invent one)
- `REJECTED` → `decision_type="STAND_DOWN"`, `stand_down_reason="CRO_REJECTED"` (a new, 15M-only reason value — real and code-grounded, since it's a literal distinct value the CCO schema emits, not a guess)
- `WAITING_FOR_15M` → **excluded from `decision_log` entirely**, same treatment as `INSUFFICIENT_CANDLES` on the 4H/1H side — it means "not yet time to evaluate," not a decision that was made.

#### `atr_pct_at_decision` sourcing, timeframe by timeframe

4H/1H: trivial — `atr14` is already a local variable inside both detectors (`gravity_engine.py:608, 835`), just needs `atr14 / current_close * 100` computed alongside the existing fields, no new fetch.

15M: real ATR exists (`trade_structure_analyst.py`'s stop formulas consume `levels["atr"]`, a 14-period ATR off resampled 15M candles per that file's own header comment) but is not currently threaded into `kabroda_mas_flow.py`'s `context` dict at the point `_write_audit()` is called. Will check for it under `context.get("levels", {}).get("atr")` at build time; if it isn't cleanly available there, `atr_pct_at_decision` ships `NULL` on 15M rows for Phase 1 rather than guess at a second computation path — the field's primary purpose (the formula-relative stop/target check) is a 4H/1H concept anyway per v1.2.

#### Confirmed accurate, no changes

- `stand_down_reason` enum for 4H/1H (`NO_ZONES`, `NO_BOS`, `MACRO_BIAS_CONFLICT`) — verified byte-for-byte against the live `if not supply_zone and not demand_zone: return` / `if not bias: return` / the 1H-only macro-bias gate. `INSUFFICIENT_CANDLES` exclusion confirmed correct (`len(candles) < 14` check exists exactly as described, still correctly excluded from `decision_log`).
- All four Revin/confluence fields (`dominant_direction`, `confluence_score`, `revin_ribbon_zone`, `revin_midline_price`, `rmo_score`, `rmo_state`, `rwp_squeeze`) confirmed real and 4H/1H-only.
- `harness/tier_labels.py`'s `tier_label(n)` is a pure `int → str` function with no data-source coupling — trivially reusable against `decision_gauge_reading` counts exactly as planned, no changes needed to that module itself for Phase 1.
- The v5 symmetric-stop appendix (2026-07-12) was confirmed **not** shipped — `target_logic_version` is still `"v4"` in both live detectors. Not relevant to this build, just noted so `decision_log` doesn't get built assuming a `v5` shape that doesn't exist in production.
- Exact hook points for the dual-write, confirmed by direct line reference:
  - **15M** — `kabroda_mas_flow.py`, immediately after the existing `_write_audit(...)` call (ends ~line 1423), reusing the already-extracted local variables (`_fuel`, `_mtf`, `_tf1h`, `_tf4h`, `brief`, `bo`, `bd`) rather than re-deriving them.
  - **4H/1H TRADE** — the `CampaignLog(...)` construction in each detector (`gravity_engine.py:698`, `944`).
  - **4H/1H STAND_DOWN** — the three early-return points inside each detector (zone check, `if not bias`, and the 1H-only macro-bias gate).

**Status: verification complete. Proceeding to Phase 1 build with the corrections above.**

---

## v1.7 — 2026-07-18 — Phase 1 Build Complete (CC)

Built and smoke-tested. Not yet deployed. Five files touched, all additive — nothing in `session_audit_log`, `campaign_logs`, or any live decision path was modified.

### What shipped

- **`database.py`** — three new tables (`candle_history`, `decision_log`, `decision_gauge_reading`), picked up by `Base.metadata.create_all()`, no `ALTER TABLE` needed. Soft FKs only (`campaign_log_id`, `session_audit_log_id`, `decision_id`), matching `SessionAuditLog`'s own established convention — no ORM relationships declared.
- **`market_data.py`** — `_persist_candles()` upsert hook, called from the tail of all five `fetch_live_*` functions (`5m`→`"5M"`, `15m`→`"15M"`, `1h`→`"1H"`, `4h`→`"4H"`, `1d`→`"1D"`; `5M` wasn't in the original 3-table sketch's timeframe comment but is a real fetch this system already makes, so it's persisted too). Dedup query bounded to the batch's own min/max timestamp, not per-row — one extra query per fetch call, not N.
- **`harness/unified_audit_writer.py`** (new) — the single shared `write_decision_log()` used by both the 15M and 4H/1H call sites, plus a `gauge()` helper that classifies a raw value into `(timeframe, gauge_name, value_numeric, value_label)` and drops `None`s automatically, so an absent gauge produces no row rather than a placeholder. Same non-blocking discipline as `harness/audit_writer.py` (Adj. 3): every write wrapped in try/except, never raises into the calling decision path.
- **`kabroda_mas_flow.py`** — one new block (step "7b") immediately after the existing `write_decision_record()` call, reusing its already-extracted locals rather than re-deriving them. Handles the real 4-value `approval_status` per the v1.6 mapping (`WAITING_FOR_15M` skipped entirely).
- **`gravity_engine.py`** — both `_detect_4h_bos()`/`_detect_1h_bos()` instrumented at exactly the branch points verified in v1.6: `NO_ZONES` and `NO_BOS` in both, `MACRO_BIAS_CONFLICT` in 1H only, plus the `TRADE` write after each `db.commit()`. Each STAND_DOWN write only passes gauges that were genuinely already computed at that exact return point in the real control flow (e.g. 4H's `NO_BOS` has `kinematic_grade`/`macro_bias`/`weekly_200sma_position`/confluence available; 1H's `NO_BOS` has only `kinematic_grade`, since 1H computes `macro_bias` later than 4H does) — checked line-by-line against the live code, not assumed symmetric across timeframes.

### Verified before considering this done

- `python -m py_compile` clean on all five touched files.
- `pyflakes` clean on all five — the four warnings it did surface (`Tuple`/`Optional` imports unused, one f-string, one unused import) are pre-existing and unrelated to this change, confirmed by location.
- Both `gravity_engine` and `kabroda_mas_flow` import successfully against a throwaway SQLite DB (`database.init_db()` catches any table-definition error at import time).
- Live smoke test against a throwaway SQLite DB: `write_decision_log()` writes a `decision_log` row plus its gauge rows correctly; a `None`-valued gauge is dropped, not written as a placeholder; a `bool` gauge correctly splits into `value_numeric` (1.0/0.0) and `value_label` ("TRUE"/"FALSE"); an int/string gauge lands in the right column. `_persist_candles()` called twice with the same batch produces no duplicate rows (dedup confirmed).
- `git diff --stat` confirms only the five intended files changed — nothing in `session_audit_log`/`campaign_logs`/any live gating path touched.

### Known, deliberate gaps carried forward (not blockers, stated so they're not mistaken for oversights)

1. **15M `atr_pct_at_decision`** — sourced from `levels.get("atr")`, confirmed real and available in `kabroda_mas_flow.py`'s `run_mas_analysis()` (used by `trade_structure_analyst.py`/`gravity_interpreter.py` already) — this is better than v1.6's own "may ship NULL" hedge; the real source was found during the build.
2. **15M candle window** uses `decided_at ± 30min` (the calibration window is exactly 1800s per CLAUDE.md) rather than a threaded `session_open` timestamp — mathematically equivalent, avoids adding a new parameter to an already-large function signature for Phase 1.
3. **`jewel_gate_open`/`jewel_conviction` not captured as 15M gauges yet** — the existing `write_decision_record()` call site doesn't pass them either (both default `None` in the current live code), so there's no already-verified source to copy. Not invented rather than guessed; a real Phase-2 follow-up, not silently dropped.
4. **`decision_full_view`** (the curated pivot SQL view from Phase 1's step 4) not yet built — tables need at least one real write cycle in production first to sanity-check the shape before a view is written against them.
5. **Not deployed.** This is local, verified-but-unshipped code. Deploying, watching the first real 15M lock and 4H/1H scan cycle write rows, and doing the Phase 2 cross-check against `session_audit_log`/`campaign_logs` is the next real step.

**Status: Phase 1 code complete and smoke-tested locally. Awaiting go-ahead to commit/deploy.**

---

## Change Log

| Date | Author | Change |
|---|---|---|
| 2026-07-18 | CC | Initial plan — three normalized tables |
| 2026-07-18 | Antigravity | Design review — agreements, pushbacks, questions |
| 2026-07-18 | CC | Code-verified response — corrections, sharpened checks, timeline fix |
| 2026-07-18 | Antigravity | Final analysis — what changed, what stands, adjusted plan |
| 2026-07-18 | CC | Final pass — candle window definition, gauge mapping rigor, authority cap made explicit |
| 2026-07-18 | Antigravity | Closing responses — all three gaps resolved, design final, ready to build |
| 2026-07-18 | CC | Pre-build verification — corrected 1H window (200 not 50), split the gauge mapping table by timeframe (energy_grade/confluence_score/dominant_direction/macro_bias are 4H/1H-only, not 15M), found and resolved the 4-value `approval_status` gap, sourced `atr_pct_at_decision` per timeframe. Starting Phase 1 build. |
| 2026-07-18 | CC | Phase 1 build complete — 3 new tables, candle_history upsert hook, shared decision_log/decision_gauge_reading writer, wired into 15M + both 4H/1H detectors (trade and all 3 stand-down branches). Compiled, pyflakes-clean, smoke-tested against a throwaway DB. Not yet committed/deployed. |
