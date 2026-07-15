# KQAL Handoff for Claude Code — v2 (Post-Review Fixes)

## CC's Review Found 3 Real Issues — All Fixed Now

### 1. 🔴 CRITICAL: `timeframe_analyzer.py` — `is_canonical` filter was wrong
**CC was right.** `is_canonical` is deliberately `False` for every 4H/1H candidate row (gravity_engine.py's detectors set it that way to keep candidates out of the 15M production track record's KPIs). The old query `WHERE is_canonical = TRUE` meant `tf_1h` and `tf_4h` would always be empty — the module would report NO_DATA for 2 of the 3 timeframes it claims to analyze.

**Fix:** Changed to `WHERE (is_canonical = TRUE OR session_timeframe IN ('4H', '1H')) AND symbol = 'BTC/USDT'` — same pattern as `audit_ai.py`'s `_real_btc_row()`.

### 2. 🟡 MEDIUM: `market_context_oracle.py` — flat DataFrame fallback was corrupting data
**CC was right.** The v1.1 fix had a bug: if yfinance returned a flat DataFrame (single ticker), the fallback branch reused the same data for all 3 tickers (SPX, DXY, VIX) — silently assigning the same price/trend to all three. This feeds `risk_posture` which the Macro Structural Architect agent reads.

**Fix:** v1.2 now raises `ValueError` on flat DataFrame instead of reusing data. Each ticker fails independently to UNKNOWN. Safer to return UNKNOWN for all 3 than silently corrupt the risk posture.

### 3. 🟢 MINOR: `performance_auditor.py` — comment was architecturally wrong
**CC was right.** `bo_price`/`bd_price` are session-level upper/lower anchors (kabroda_mas_flow.py:1745-46): `bo = max(...)`, `bd = min(...)`. Always `bo >= bd` regardless of LONG/SHORT direction. The `abs()` is a harmless no-op, but the comment claimed "for SHORT sessions BD > BO" which is false in this system.

**Fix:** Comment now accurately describes the architecture. `abs()` kept as defensive no-op.

### 4. 🟡 NOTE: `db_reader.py` uses `psycopg2` (v2) but project uses `psycopg` (v3)
This is a pre-existing issue in the KQAL module. The module isn't wired into `main.py` so it won't break the live deploy, but `timeframe_analyzer.py` (which imports from it) will `ImportError` if someone tries to run it locally. CC should decide whether to:
- Switch `db_reader.py` to `psycopg` v3
- Add `psycopg2-binary` to `requirements.txt`
- Or leave it as-is (only runs on Render where the env may differ)

## Files Changed (4 total)
1. `market_context_oracle.py` — v1.2 (fixed flat DataFrame corruption)
2. `performance_auditor.py` — fixed misleading comment
3. `bold-hubble/kqal/timeframe_analyzer.py` — fixed `is_canonical` filter
4. `bold-hubble/kqal/output/CLAUDE_CODE_HANDOFF.md` — this handoff doc
