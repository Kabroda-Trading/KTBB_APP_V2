# PHASE 3 HANDOFF — Position Sizing, Three Drives, Exhaustion Monitor

## Summary

Phase 3 implements three high-impact improvements from the KQAL queue. All three follow the established patterns from Phases 1-2: proper packaging via `bold-hubble` editable install, zero circular imports, and no redundant API calls.

---

## Files Created

### `bold-hubble/position_sizing/__init__.py`
Package init, exports `calc_position_size` and helpers.

### `bold-hubble/position_sizing/position_sizing.py`
IMP-003: Position sizing module. Three methods:
- `calc_fixed_fractional()` — standard risk-based sizing
- `calc_volatility_adjusted()` — ATR-based sizing (default)
- `calc_kelly()` — Kelly Criterion (clamped to 0.25 max)
- `calc_position_size()` — unified dispatcher

**Config:** `REFERENCE_ACCOUNT_BALANCE` (env var, default 10000), `RISK_PERCENT` (0.02), `POSITION_SIZING_METHOD` ("volatility"). Named `REFERENCE_ACCOUNT_BALANCE` per CC's guidance — clearly illustrative, not real capital.

### `bold-hubble/indicators/three_drives.py`
IMP-005: Three Drives divergence detection.
- `find_swing_points()` — swing high/low detection with configurable lookback
- `detect_three_drives()` — 3-drive pattern matching (bullish + bearish)
- `_score_confidence()` — harmonic ratio + RSI divergence strength scoring
- **Pivot filtering uses `p <= i - order` from day one** — prevents the look-ahead bias bug that was caught in `rmo.py` during Round 2

### `bold-hubble/monitoring/__init__.py`
Package init, exports `check_exhaustion`.

### `bold-hubble/monitoring/exhaustion_monitor.py`
IMP-006: Live exhaustion detection for in-trade runners.
- `check_exhaustion(candles_1m, candles_5m, position)` → `{"level", "signals"}`
- PMARP > 95% overextension detection
- BBWP > 85% blow-off detection
- RSI bearish divergence confirmation
- Alert levels: NONE → WATCH → WARNING → EXIT
- **No candle fetching** — receives already-fetched candles from Phase 3B/4B loops
- **No trailing stop logic** — Phase 3B/4B already own that

---

## Files Modified

### `bold-hubble/pyproject.toml`
Added `position_sizing*` and `monitoring*` to `[tool.setuptools.packages.find].include` so the editable install registers the new packages.

### `gravity_engine.py`
- **Added import:** `from position_sizing import calc_position_size`
- **4H BOS (line 700):** `total_contracts=0.0` → `total_contracts=calc_position_size(entry_price=..., stop_price=..., atr_value=atr14)`
- **1H BOS (line 942):** Same change
- ATR is already computed as `atr14` at both call sites (lines 599, 822) — reused directly
- **No exhaustion monitor wiring here** (per CC's redirect — goes in `ledger_closing_engine.py`)

### `mtf_confluence_scanner.py`
- **Added import:** `from indicators.three_drives import detect_three_drives`
- **Added computation:** `three_drives_result = detect_three_drives(highs, lows, rsi_series)` after Revin Suite block
- **Added to return dict:** `"three_drives": three_drives_result`
- **Added to error_result:** `"three_drives": []`

### `ledger_closing_engine.py`
- **Added import:** `from monitoring.exhaustion_monitor import check_exhaustion`
- **Phase 3B (after line 513):** Added `check_exhaustion()` call — reuses the already-fetched `candles` variable (no redundant fetch)
- **Phase 4B (after line 699):** Same change
- Logs WARNING/EXIT levels with signal types

### `bold-hubble/strategies/krown_system.py`
- **Added import:** `from indicators.three_drives import detect_three_drives`
- **Added confidence modifier:** After RMO alignment block, iterates strategies and applies +15 (CONFIRMED) or +5 (PENDING) boost when Three Drives pattern matches strategy bias

---

## Verification

- ✅ All 5 new files compile clean (`python -m py_compile`)
- ✅ All 4 modified files compile clean
- ✅ `pip install -e ./bold-hubble` registers all new packages (confirmed via editable finder MAPPING)
- ✅ `import main` passes our code chain (fails at pre-existing `anthropic` dependency in `agent_core.py` — unrelated to Phase 3)
- ✅ `calc_position_size(50000, 49000, 1000)` returns `0.133333` (correct: 10000 * 0.02 / (1000 * 1.5) = 0.1333)

---

## CC Review Checklist

1. **`position_sizing/` at bold-hubble level** — correct per CC's guidance (consumed by root-level `gravity_engine.py` via editable install)
2. **`REFERENCE_ACCOUNT_BALANCE` env var** — named per CC's guidance, not `ACCOUNT_BALANCE`
3. **Three Drives pivot filtering** — uses `p <= i - order` from day one (no look-ahead bias)
4. **Exhaustion monitor in `ledger_closing_engine.py`** — not `gravity_engine.py` (per CC's architectural redirect)
5. **No `calc_trailing_stop()`** — Phase 3B/4B already own trailing mechanisms
6. **No `fetch_live_1m()` in `market_data.py`** — reuses already-fetched `candles` from Phase 3B/4B
7. **`monitoring/` at bold-hubble level** — consumed by root-level `ledger_closing_engine.py` via editable install

---

## Files Changed (git diff summary)

```
 M bold-hubble/pyproject.toml              — added position_sizing*, monitoring* to package include
 M gravity_engine.py                       — added position sizing call in 4H + 1H BOS creation
 M mtf_confluence_scanner.py               — added three_drives to _analyze_timeframe() output
 M ledger_closing_engine.py                — added exhaustion check in Phase 3B + Phase 4B loops
 M bold-hubble/strategies/krown_system.py  — added Three Drives confidence modifier
?? bold-hubble/position_sizing/__init__.py
?? bold-hubble/position_sizing/position_sizing.py
?? bold-hubble/indicators/three_drives.py
?? bold-hubble/monitoring/__init__.py
?? bold-hubble/monitoring/exhaustion_monitor.py
```
