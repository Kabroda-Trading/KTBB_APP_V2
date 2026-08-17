# Phase 2 — Import Architecture Cleanup

## Problem
Two hacks were introduced during Phase 1 to get Revin Suite imports working from root-level files:

### Hack 1: Duplicated `sys.path` insert
Both `gravity_engine.py` and `mtf_confluence_scanner.py` have identical blocks:
```python
_bh_path = os.path.join(os.path.dirname(__file__), "bold-hubble")
if _bh_path not in sys.path:
    sys.path.insert(0, _bh_path)
```

### Hack 2: Lazy import in `gravity_engine.py`
`mtf_confluence_scanner` is imported inside the loop body (line 1003) instead of at module level, with a 15-line comment explaining the circular import workaround:
```
battlebox_pipeline → gravity_engine → mtf_confluence_scanner → battlebox_pipeline
```

## Proposed Architecture

### Step 1: Package `bold-hubble/`
Add `pyproject.toml` to `bold-hubble/` so it's a proper installable package. Then `pip install -e bold-hubble/` makes all its modules importable from anywhere — no `sys.path` hacks needed.

### Step 2: Extract shared data layer
The circular import exists because `mtf_confluence_scanner.py` imports data-fetching functions (`fetch_live_15m`, `fetch_live_1h`, `_calc_ema_series`, `_calc_adx`, `_normalize_symbol`) from `battlebox_pipeline.py`, which itself imports `gravity_engine.py` at module level.

The proper fix: extract those shared utilities into a standalone `market_data.py` module that both `battlebox_pipeline.py` and `mtf_confluence_scanner.py` can import without creating a cycle.

### Step 3: Remove all hacks
- Delete `sys.path` blocks from `gravity_engine.py` and `mtf_confluence_scanner.py`
- Delete the lazy import — import `mtf_confluence_scanner` at module level
- Delete the 15-line workaround comment

## Files Touched

| File | Change |
|------|--------|
| `bold-hubble/pyproject.toml` | **NEW** — package config |
| `market_data.py` | **NEW** — extracted fetch/calc functions from battlebox_pipeline |
| `battlebox_pipeline.py` | **MODIFY** — import from `market_data` instead of defining fetch functions inline |
| `gravity_engine.py` | **MODIFY** — remove `sys.path` hack, remove lazy import |
| `mtf_confluence_scanner.py` | **MODIFY** — remove `sys.path` hack, import from `market_data` |

## Questions for CC
1. Does this approach look right for the architecture?
2. Any concerns about extracting fetch functions from `battlebox_pipeline.py` — it's a large file with a lot of history
3. Should `market_data.py` live at root level (alongside `battlebox_pipeline.py`) or inside `bold-hubble/`?
