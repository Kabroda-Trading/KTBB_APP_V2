## Decisions Made

1. **Import strategy**: Direct imports — add `bold-hubble/` to `sys.path` in root files, import like `from bold_hubble.indicators.revin_ribbons import ...`
2. **Duplication**: Keep both — Revin Suite is additive. Existing inline BBWP/PMARP stay as fallback. Will clean up later with "the best of the best" quality.
3. **Priority**: Wire first (Phase 1), clean up after (Phase 2).

---

## Phase 1: Wire Revin Suite into Live Pipeline

### 1a. `mtf_confluence_scanner.py` — Replace inline indicators with Revin Suite

**Current state**: Has its own inline `_calc_bbwp()`, `_calc_pmarp()`, `_calc_rsi_series()`, `_find_divergence()`. These are duplicates of what's in `bold-hubble/indicators/`.

**Changes**:
- Import `calculate_revin_ribbons`, `analyze_ribbon_state` from `bold-hubble.indicators.revin_ribbons`
- Import `calculate_rwp`, `analyze_rwp_state` from `bold-hubble.indicators.rwp`
- Import `calculate_rmo`, `analyze_rmo_state` from `bold-hubble.indicators.rmo`
- Add Revin Ribbons data to each timeframe's output (midline, gray dot test, zone)
- Add RMO to each timeframe's output (momentum score, overextended flag)
- Add RWP to each timeframe's output (squeeze detection, expansion flag)
- Keep existing BBWP/PMARP as fallback — Revin Suite is additive, not a replacement
- Update `_build_jewel_signal()` to consider RMO divergence + RWP squeeze as additional conviction factors
- Update `_build_summary()` to report Revin Ribbons state

**Risk**: Low. Additive change — existing logic untouched.

### 1b. `gravity_engine.py` — Add Revin context to candidate data

**Current state**: Gravity engine processes market data and generates candidate trade setups. It doesn't know about Revin Ribbons.

**Changes**:
- Import Revin Suite modules
- After fetching OHLCV data for each symbol, run `compute_revin_suite()`
- Add Revin state to candidate context dict (ribbon zone, RMO score, RWP state)
- Use Revin Ribbons midline as an additional support/resistance level
- Use RWP squeeze as an additional volatility gate condition

**Risk**: Low. Additive — existing gravity logic unchanged.

### 1c. `krown_to_kabroda_bridge.py` — Add Revin Ribbons mapping

**Current state**: Has `INDICATOR_TO_KABRODA_CONFIG` mapping for BBWP, PMARP, RSI divergence. No Revin entry.

**Changes**:
- Add `"revin_ribbons"` section to `INDICATOR_TO_KABRODA_CONFIG` with:
  - `above_midline` → bullish bias
  - `below_midline` → bearish bias
  - `gray_dot_tested` → support test alert
  - `outer_band_tested` → exhaustion warning
- Add `"rmo"` section with:
  - `strong_bullish` / `strong_bearish` → momentum extremes
  - `divergence_warning` → reversal risk
- Add `"rwp"` section with:
  - `extreme_squeeze` → breakout imminent
  - `active_expansion` → trend momentum

**Risk**: Low. Pure data mapping — no logic changes.

### 1d. `krown_system.py` — Integrate Revin Suite into strategy evaluation

**Current state**: Evaluates 5 strategies using BBWP, PMARP, RSI, trend. No Revin Suite.

**Changes**:
- Import `compute_revin_suite` from `bold-hubble.indicators.revin_suite_engine`
- Run Revin Suite alongside existing indicators
- Add Revin state to `regime_summary` output
- Use Revin Ribbons midline as additional trend filter
- Use RMO score to adjust strategy confidence (RMO > +60 boosts long confidence, RMO < -60 boosts short confidence)
- Use RWP squeeze as additional volatility gate

**Risk**: Low. Additive — existing strategy logic unchanged.

---

## Phase 2: Clean Up Architecture

### 2a. Resolve root vs bold-hubble split

**Problem**: The root directory has 43 Python files (main system). `bold-hubble/` has its own `indicators/`, `strategies/`, `pipeline/`, `kqal/`. The root's `mtf_confluence_scanner.py` has inline duplicates of indicators that now live in `bold-hubble/indicators/`.

**Changes**:
- Add `bold-hubble/` to `sys.path` in root files that need to import from it (or use relative imports)
- Better: Create a clean import bridge — `bold-hubble/__init__.py` that exposes the key modules
- Document the architecture: root = Kabroda core engine, bold-hubble = Krown-specific extensions

**Risk**: Medium. Import path changes could break things if not done carefully. Need to test after each change.

### 2b. Update `kqal/system_auditor.py` to recognize new indicators

**Current state**: System auditor checks for BBWP, PMARP, RSI divergence, trend_volatility. Doesn't know about Revin Suite.

**Changes**:
- Add Revin Ribbons, RMO, RWP, EMA Ribbon to the indicator registry
- Add parameter validation for the new indicators
- Add synthetic data tests for the new indicators

**Risk**: Low. The auditor is read-only — it reports, it doesn't modify.

---

## Phase 3: High-Priority Queue Items

### 3a. IMP-005: Three Drives Divergence Detection

**What**: Enhance `rsi_divergence.py` to detect 3-drive divergence patterns (Krown requires 3 swing points to confirm).

**Changes**:
- Add `detect_three_drives()` function to `bold-hubble/indicators/rsi_divergence.py`
- Detect 3 consecutive swing highs/lows with progressive RSI divergence
- Return structured data: pattern type, confidence, bar indices

**Risk**: Low. Additive — existing 2-point divergence detection unchanged.

### 3b. IMP-003: Position Sizing Module

**What**: Build `strategies/position_sizing.py` with fixed fractional, volatility-adjusted, and Kelly sizing.

**Changes**:
- Create `bold-hubble/strategies/position_sizing.py`
- Fixed fractional: 1-2% risk per trade
- Volatility-adjusted: ATR-based position scaling
- Kelly Criterion option
- Account balance input parameter

**Risk**: Low. Standalone module — no existing code changed.

### 3c. IMP-006: Live Exhaustion Monitor

**What**: Build `strategies/exhaustion_monitor.py` that watches for PMARP overextension + BBWP blow-off + RSI divergence.

**Changes**:
- Create `bold-hubble/strategies/exhaustion_monitor.py`
- Uses Revin Suite (RMO overextended + RWP extreme expansion) as primary detection
- Falls back to BBWP/PMARP for compatibility
- Outputs structured exhaustion warnings

**Risk**: Low. Standalone module.

---

## Phase 4: Low-Priority Queue Items

### 4a. IMP-007: Entry Mechanics Model
### 4b. IMP-009: Stand-Down Re-Arm Alerter
### 4c. IMP-010: Runner Mechanic
### 4d. IMP-008: News/Event Calendar Integration

These are lower priority and can be deferred or done in a future round.

---

## Verification Plan

### Automated Tests
```bash
# After each phase:
python -m py_compile bold-hubble/indicators/*.py  # Compile check
python -c "import main"  # Boot check
python -c "from bold-hubble.indicators import *; print('OK')"  # Import check

# Full smoke test
python bold-hubble/kqal/scratch/run_audit.py  # System audit
```

### Manual Verification
- Ask CC to review each phase before commit
- Deploy to Render and verify live site unaffected
- Run MTF confluence scan on a test symbol to verify Revin data appears

---

## Open Questions

1. **Import strategy**: Should root files import from `bold-hubble.indicators` directly, or should we create a re-export layer? Direct imports are simpler but create a dependency on `bold-hubble/` being in the path.

2. **Duplication vs migration**: Should `mtf_confluence_scanner.py`'s inline BBWP/PMARP be replaced entirely by the Revin Suite, or kept as fallbacks? Keeping both is safer but creates maintenance burden.

3. **Priority order**: Phase 1 (wiring) is the most impactful. Should we do Phase 2 (cleanup) before or after Phase 3 (new features)?
