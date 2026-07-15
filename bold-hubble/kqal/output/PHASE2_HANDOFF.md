# PHASE 2 HANDOFF — Import Architecture Cleanup

## Summary

Extracted the shared data-fetching and calculation layer from `battlebox_pipeline.py` into a new zero-dependency module `market_data.py` at root level. This breaks the circular import chain:

```
battlebox_pipeline → gravity_engine → mtf_confluence_scanner → battlebox_pipeline
```

All existing call sites continue to work unchanged via re-exports from `battlebox_pipeline`.

---

## Files Created

### `market_data.py` (root level)
Contains everything that was extracted:
- `_exchange_live` — single Kraken ccxt client instance
- `_normalize_symbol()` — symbol normalization
- `fetch_live_5m()`, `fetch_live_15m()`, `fetch_live_1h()`, `fetch_live_4h()`, `fetch_live_daily()` — all 5 OHLCV fetchers
- `_calc_ema_series()` — EMA calculation
- `_calc_adx()` — Wilder's ADX with `rising` flag (matches the original `battlebox_pipeline.py` implementation exactly)

**Zero dependencies** on `battlebox_pipeline`, `gravity_engine`, or any other root-level module. Only depends on `ccxt` and Python stdlib.

### `bold-hubble/pyproject.toml`
Standard setuptools build config. Required for `pip install -e ./bold-hubble`.

---

## Files Modified

### `battlebox_pipeline.py`
- **Removed**: `_exchange_live`, `_normalize_symbol`, all 5 `fetch_live_*` functions, `_calc_ema_series`, `_calc_adx`
- **Added**: `from market_data import (...)` re-export block
- All call sites using `battlebox_pipeline.fetch_live_5m(...)` or `from battlebox_pipeline import fetch_live_15m` continue to work identically

### `gravity_engine.py`
- **Removed**: `import os`, `import sys`, the `sys.path.insert(0, ...)` hack for bold-hubble
- **Removed**: The lazy `import mtf_confluence_scanner` inside `run_gravity_ingestion_loop()`
- **Added**: Module-level `import mtf_confluence_scanner` (safe now that the circular import is broken)

### `mtf_confluence_scanner.py`
- **Removed**: `import os`, `import sys`, the `sys.path.insert(0, ...)` hack for bold-hubble
- **Changed**: `from battlebox_pipeline import (...)` → `from market_data import (...)`

### `requirements.txt`
- **Added**: `-e ./bold-hubble` as first line
- This rides along with the existing `pip install -r requirements.txt` on Render — no dashboard change needed

---

## Verification

- ✅ `market_data.py` imports standalone with zero dependencies
- ✅ All 4 core files compile cleanly (`python -m py_compile`)
- ✅ `pip install -e ./bold-hubble` succeeds
- ✅ No remaining `sys.path` + `bold-hubble` hacks in any production file
- ✅ Full import chain test (`import main`) — CC ran `pip install -e ./bold-hubble` then `import main` directly; both `sqlalchemy` and `psycopg` were already present locally (the earlier "blocked by missing deps" note was a misdiagnosis — the actual gap was the editable install not yet being run in that shell). All 6 downstream consumers (`main`, `market_radar`, `market_simulator`, `research_lab`, `session_monitor`, `lti_engine`) import cleanly.

---

## Files Changed (git diff summary)

```
 M battlebox_pipeline.py    — removed 7 functions + _exchange_live, added re-export
 M gravity_engine.py        — removed sys.path hack + lazy import
 M mtf_confluence_scanner.py — removed sys.path hack, changed import source
 M requirements.txt          — added -e ./bold-hubble
?? market_data.py            — new shared data layer
?? bold-hubble/pyproject.toml — new package config
```

---

## CC Review Checklist

1. **market_data.py at root level** — correct per CC's recommendation (consumed by 6+ root-level modules)
2. **All 5 fetch functions + _exchange_live extracted** — not 4 of 5 (CC's gap #2)
3. **battlebox_pipeline.py re-exports every moved name** — all call sites preserved (CC's gap #3)
4. **-e ./bold-hubble in requirements.txt** — not a manual step (CC's gap #1)
5. **market_data.py at root, not inside bold-hubble/** — correct per CC's Q3 answer
6. **Wilder's ADX preserved exactly** — the `_calc_adx` in market_data.py matches the original `battlebox_pipeline.py` implementation (with `rising` flag), not the simplified version I initially wrote
