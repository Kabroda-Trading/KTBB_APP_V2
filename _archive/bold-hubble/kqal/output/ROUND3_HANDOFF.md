# Round 3 Handoff — Revin Suite Wiring & Cleanup

## Status

**Phase 1a — `mtf_confluence_scanner.py`** ✅ COMPLETE
- Added `sys.path` for `bold-hubble/` + Revin Suite imports
- Each timeframe now outputs 15 Revin Suite fields (ribbon zone, gray dot, outer band, RMO score/state, RWP squeeze/expansion, midline/band prices)
- `_build_jewel_signal()` uses RWP squeeze + RMO alignment to boost conviction
- `_build_summary()` reports Revin-specific conditions in plain English
- Compile check ✅, main app boot ✅

---

## Phase 1b — `gravity_engine.py` (NEEDS REVIEW)

### What needs to change

The gravity engine already receives a `confluence` dict from the MTF scanner (fetched once per loop tick in `run_gravity_ingestion_loop()` and passed to both `_detect_4h_bos()` and `_detect_1h_bos()`). The MTF scanner now includes Revin Suite data in each timeframe's output, so the `confluence` dict already has it.

**Changes:**

1. **Imports** — Add `sys.path` + Revin Suite imports (same pattern as MTF scanner)

2. **`_detect_4h_bos()`** — Extract Revin fields from `confluence` and add to `CampaignLog` row:
   - `revin_ribbon_zone` — which zone price is in on the dominant timeframe
   - `rmo_score` — RMO score from the dominant timeframe
   - `rwp_squeeze` — whether RWP confirms compression
   - `revin_midline_price` — Revin Ribbons midline as additional S/R level

3. **`_detect_1h_bos()`** — Same additions

4. **Optionally** — Use Revin Ribbons midline as a secondary S/R anchor alongside KDE/4H pivot logic

### Questions for CC

1. Does this approach look right given the CampaignLog schema and existing data flow?
2. The confluence dict already has Revin data from the MTF scanner — should we extract per-timeframe or just use the dominant timeframe's values?
3. Should Revin Ribbons midline be used as a secondary S/R anchor, or just logged for now?

---

## Phase 1c — `krown_to_kabroda_bridge.py` (NOT STARTED)

### What needs to change

The bridge has `INDICATOR_TO_KABRODA_CONFIG` mapping for BBWP, PMARP, RSI divergence. Needs Revin Suite entries.

**Changes:**
- Add `"revin_ribbons"` section: above/below midline, gray dot test, outer band test
- Add `"rmo"` section: strong bullish/bearish, divergence warning
- Add `"rwp"` section: extreme squeeze, active expansion

---

## Phase 1d — `krown_system.py` (NOT STARTED)

### What needs to change

The Krown system evaluates 5 strategies using BBWP, PMARP, RSI, trend. Needs Revin Suite integration.

**Changes:**
- Import `compute_revin_suite`
- Run Revin Suite alongside existing indicators
- Add Revin state to `regime_summary` output
- Use RMO score to adjust strategy confidence
- Use RWP squeeze as additional volatility gate

---

## Phase 2 — Cleanup (DEFERRED)

- Resolve root vs `bold-hubble/` import architecture
- Update `kqal/system_auditor.py` to recognize new indicators

---

## Phase 3 — Queue Items (DEFERRED)

- IMP-005: Three Drives divergence detection
- IMP-003: Position sizing module
- IMP-006: Live exhaustion monitor
