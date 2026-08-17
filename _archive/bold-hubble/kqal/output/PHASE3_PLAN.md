# PHASE 3 PLAN — Position Sizing, Three Drives, Exhaustion Monitor

## Summary

Phase 3 implements three high-impact improvements from the KQAL queue. Each is a self-contained module that integrates into the existing gravity engine pipeline and/or indicator system. All three follow the established patterns from Phases 1-2: proper packaging, zero circular imports, and CC review before commit.

---

## Items

### IMP-003: Position Sizing Module [HIGH]

**Problem:** Kabroda has no position sizing. `CampaignLog.total_contracts` is always `0.0`. Krown emphasizes risk-based sizing (1-2% risk per trade, volatility-adjusted).

**What to build:** `bold-hubble/position_sizing/position_sizing.py`

**Functions:**
- `calc_fixed_fractional(account_balance, risk_percent, entry_price, stop_price)` → contracts
- `calc_volatility_adjusted(account_balance, risk_percent, entry_price, atr_value, atr_multiplier=1.5)` → contracts
- `calc_kelly(fraction_won, avg_win, avg_loss)` → optimal fraction (optional, advanced)
- `calc_position_size(account_balance, entry_price, stop_price, atr_value, method='volatility', ...)` → unified dispatcher

**Integration:**
- `gravity_engine.py` — after `_detect_4h_bos()` / `_detect_1h_bos()` create a candidate, call position sizing before writing `CampaignLog`
- `_calc_atr()` already exists in `gravity_engine.py:39` — reuse directly

**Config needed:**
- `REFERENCE_ACCOUNT_BALANCE` (env var, default 10000) — named clearly as a reference/illustrative value, not a real balance
- `RISK_PERCENT` (env var, default 0.02 for 2%)
- `POSITION_SIZING_METHOD` (env var, default 'volatility')

> [!IMPORTANT]
> **CC's guidance:** `CampaignLog.total_contracts` is never read by anything today. `ledger_closing_engine.py` is a monitoring/audit engine, not an order-execution engine against a real brokerage balance. The sizing math itself is standard and harmless, but the env var should be named `REFERENCE_ACCOUNT_BALANCE` to make clear it's a what-if calculation, not authoritative capital tracking. This is purely so the audit-AI can eventually backtest theoretical size vs. outcome.

**Verification:**
- Unit test: given account_balance=10000, risk_percent=0.02, entry=50000, stop=49000 → expect 0.2 BTC
- Unit test: volatility-adjusted with ATR=1000 → expect smaller size than fixed fractional
- Integration: verify `total_contracts` is populated in CampaignLog after BOS detection

---

### IMP-005: Three Drives Divergence Detection [MEDIUM]

**Problem:** Krown requires 3 drives (swing points) to confirm divergence patterns. `rsi_divergence.py` only detects single divergences between 2 points. This causes false signals.

**What to build:** `bold-hubble/indicators/three_drives.py`

**Functions:**
- `find_swing_points(highs, lows, pivot_lookback=3)` → `{"swing_highs": [...], "swing_lows": [...]}` — reusable pivot detection
- `detect_three_drives(highs, lows, rsi_values, pivot_lookback=3)` → `List[Dict]` — each dict has:
  - `pattern`: "BULLISH" | "BEARISH"
  - `drive_1`, `drive_2`, `drive_3`: bar indices for each drive
  - `price_1/2/3`, `rsi_1/2/3`: values at each drive
  - `harmonic_ratio`: ratio between drive 1-2 and drive 2-3 distances
  - `confidence`: 0-100 score based on harmonic alignment + RSI divergence strength
  - `signal`: "ACTIVE" | "PENDING" | "CONFIRMED"
- `score_three_drives_confidence(pattern, drives, rsi_values)` → float 0-100

> [!IMPORTANT]
> **CC's guidance — build pivot filtering from day one:** The plan reuses `find_local_extrema()` from `rsi_divergence.py`. That exact reuse pattern caused a confirmed look-ahead-bias bug in `rmo.py` during Round 2 review — computing pivots once over the whole series and then indexing `[-1]` per-bar leaks future data into every historical bar except the last. The fix that shipped in `rmo.py` filters pivots to `p <= i - order` before use on each bar. `three_drives.py` must use that identical filtering pattern from the start.

**Integration:**
- `mtf_confluence_scanner.py` — add `three_drives` field to `_analyze_timeframe()` output
- `krown_system.py` — add Three Drives as a signal modifier in the unified evaluator
- `krown_to_kabroda_bridge.py` — add to indicator mapping

**Verification:**
- Unit test: known 3-drive bullish pattern → returns correct drives and confidence > 70
- Unit test: random noise → returns empty list (no false positives)
- Unit test: pivot filtering prevents look-ahead bias (pivots at index > current bar are excluded)
- Integration: verify `three_drives` field appears in confluence scanner output

---

### IMP-006: Live Exhaustion Monitor [MEDIUM]

**Problem:** In-trade runner exhaustion signals are not monitored. Krown teaches that runners should be monitored for momentum exhaustion (PMARP > 95%, BBWP > 85%, RSI divergence). Kabroda has no in-trade monitoring capability.

**What to build:** `bold-hubble/monitoring/exhaustion_monitor.py`

**Functions:**
- `check_exhaustion(candles_1m, candles_5m, position)` → `{"level": "NONE"|"WATCH"|"WARNING"|"EXIT", "signals": [...]}`
  - PMARP overextension detection (> 95%)
  - BBWP blow-off detection (> 85%)
  - RSI divergence confirmation on each new bar
  - Returns alert level + list of triggered signals

> [!IMPORTANT]
> **CC's guidance — major architectural redirect:**
> 1. **Do NOT wire into `gravity_engine.py`.** `shadow_runner_*` fields on `CampaignLog` already have a single, established owner — `ledger_closing_engine.py`'s Phase 3B (15M, EMA-based) and Phase 4B (4H/1H, zone-based), both already live and running every 60 seconds. `gravity_engine.py` has zero references to `shadow_runner` today. Adding a second writer on two different cadences writing the same mutable fields on the same rows is a race condition.
> 2. **Wire `check_exhaustion()` into `ledger_closing_engine.py`'s existing Phase 3B/4B loops** — call it alongside the EMA/zone ratchet already happening there, using it to inform or accelerate that same stop-trail.
> 3. **Skip `calc_trailing_stop()`** — Phase 3B/4B already have their own trailing mechanisms (`_update_ema21`, `_nearest_zone_by_price`). A third one competing with those is the same collision problem.
> 4. **Both 15M and 4H/1H are covered for free** since both loops already exist in `ledger_closing_engine.py`.
> 5. **No new `fetch_live_1m()` needed** — `ledger_closing_engine.py` already has `_fetch_1m_since()` (line 97), already called inside both Phase 3B (line 509) and Phase 4B (line 683). The 1-minute candles will already be in scope as the local `candles` variable at the call site. `check_exhaustion()` should take that directly rather than triggering a second, redundant fetch. For 5-minute data, `market_data.fetch_live_5m()` already exists.

**Integration:**
- `ledger_closing_engine.py` — add `check_exhaustion()` call in Phase 3B and Phase 4B loops, passing the already-fetched `candles` variable
- `CampaignLog` — shadow runner fields already exist (lines 558-577), populated by existing Phase 3B/4B

**Verification:**
- Unit test: PMARP > 95 with BBWP > 85 → returns WARNING or EXIT
- Unit test: normal conditions → returns NONE
- Integration: verify exhaustion signals appear in Phase 3B/4B output

---

## Proposed Changes

### [NEW] `bold-hubble/position_sizing/__init__.py`
Package init, exports `calc_position_size` and helpers.

### [NEW] `bold-hubble/position_sizing/position_sizing.py`
Core position sizing logic — fixed fractional, volatility-adjusted, Kelly.

### [NEW] `bold-hubble/indicators/three_drives.py`
Three Drives divergence detection — swing point detection, pattern matching, confidence scoring. **Must use pivot-filtering pattern (`p <= i - order`) from day one to prevent look-ahead bias.**

### [NEW] `bold-hubble/monitoring/__init__.py`
Package init.

### [NEW] `bold-hubble/monitoring/exhaustion_monitor.py`
Exhaustion detection — PMARP/BBWP/RSI-divergence based alert levels. **No trailing stop logic** (Phase 3B/4B already own that). **No candle fetching** (reuses already-fetched `candles` from Phase 3B/4B loops).

### [MODIFY] `mtf_confluence_scanner.py`
Add `three_drives` field to `_analyze_timeframe()` output.

### [MODIFY] `krown_system.py`
Add Three Drives as a signal modifier in the unified evaluator.

### [MODIFY] `gravity_engine.py`
- Add position sizing call after BOS candidate creation (IMP-003 only)
- Import `position_sizing` module
- **No exhaustion monitor wiring here** (IMP-006 goes in `ledger_closing_engine.py`)

### [MODIFY] `ledger_closing_engine.py`
- Add `check_exhaustion()` call in Phase 3B and Phase 4B loops
- Pass the already-fetched `candles` variable (no redundant fetch)

### [MODIFY] `krown_to_kabroda_bridge.py`
Add Three Drives to indicator mapping.

### ~~[MODIFY] `market_data.py`~~ — **DROPPED per CC review.** No new fetch functions needed. `_fetch_1m_since()` already exists in `ledger_closing_engine.py`, `fetch_live_5m()` already exists in `market_data.py`.

---

## Verification Plan

### Automated Tests
- `python -m py_compile` on all new and modified files
- `pip install -e ./bold-hubble` to register new packages
- `python -c "import main"` to verify full boot chain
- Unit tests for each new module

### Manual Verification
- Deploy to Render, verify boot log shows no import errors
- Check first gravity loop tick for Three Drives in confluence output
- Check first BOS candidate for populated `total_contracts`
- Check Phase 3B/4B logs for exhaustion signal output

---

## CC's Answers to Open Questions

> [!NOTE]
> **Account balance source:** Env var with a default is the right mechanism, but rename to `REFERENCE_ACCOUNT_BALANCE=10000` to make clear it's a reference value, not a real balance — nothing today ties it to actual capital.

> [!NOTE]
> **Exhaustion scope:** Reframed — scope isn't "4H/1H vs 15M," it's "which existing loop owns this." Wired into `ledger_closing_engine.py`'s Phase 3B/4B, both 15M and 4H/1H are covered for free since both loops already exist.

> [!NOTE]
> **Three Drives depth:** Agreed — standalone indicator, wired into confluence scanner output, same pattern as Revin Suite. Build pivot-filtering fix (`p <= i - order`) from the start.

> [!NOTE]
> **No new `fetch_live_1m()`:** `ledger_closing_engine.py` already has `_fetch_1m_since()` — the 1m candles are already in scope in Phase 3B/4B. `check_exhaustion()` takes them as a parameter. `fetch_live_5m()` already exists in `market_data.py`.
