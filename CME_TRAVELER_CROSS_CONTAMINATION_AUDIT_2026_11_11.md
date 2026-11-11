# CME / Traveler / Alt Matrix / MetaSignals Cross-System Audit — 2026-11-11

**Requested by:** Andy, after 3-4 days of CME market-data rebuilds that kept
not working, plus a direct ask to confirm nothing got mixed up between the
four trading subsystems.
**Performed by:** Claude Code, read-only code audit + one subagent +
independent full-suite test run. Every claim below was verified directly
against the current code (file:line) or a real test run — nothing here is
relayed from a commit message or another agent's say-so without being
checked first.

> **CORRECTION (2026-11-11, same day, after further research):** Section 5
> below originally reported the Traveler/Alt Matrix finding as a severe,
> unpatched cross-contamination bug. A follow-up research pass (reading
> `AGENT_LOG.md`/`CC_INTERFACE.md`'s own committed 2026-10-09 "Option A"
> ruling from Andy, and `alt_matrix_portfolio.py` directly) found that
> finding was based on a **wrong assumption** — Alt Matrix sharing the same
> physical account as Traveler is Andy's own explicit, deliberate design,
> not a bug, and the real safety mechanism (a 30% free-margin reserve floor
> + 20% total-risk cap + 3-position cap, enforced one-way by Alt Matrix
> checking itself before every order, never by Traveler checking anything)
> is real, live, correctly wired, and does exactly what it's supposed to.
> Section 5 is kept below with strikethrough-equivalent framing and a
> correction block rather than silently rewritten, so the record is
> honest about what was initially gotten wrong and why. See the corrected
> version in Section 5 for what's actually true.
**Scope:** `KTBB_app_v2` (kabroda.com) as of commit `522b395`..`64c4813`
(2026-11-07 through 2026-11-10), audited 2026-11-11.

---

## 1. Why this audit happened

Over 2026-11-07 through 2026-11-10, the CME market-data feed was rebuilt
**twice** because each attempt turned out not to actually work live:

1. **Databento** (pre-existing) — batch-indexed REST candles, returned a
   hard HTTP 422 on a closed/weekend window instead of empty data, and was
   separately found hardcoded to `None` for the daily ATR14 input, which
   forced the reachability safety check to fail on *every* lock (a real
   trading day silently lost every single day — found and fixed 2026-11-05).
2. **Continuous yfinance** (Architecture A, 2026-11-09) — removed
   Databento's 422 problem, but live testing found a mandatory ~10-minute
   exchange delay on Yahoo's continuous-futures quotes — unacceptable for
   5-minute breakout confirmation (D2) and ratchet management (D3).
3. **CrossTrade `GetBars` RPC via a dedicated St. Louis VPS** (Session 11,
   2026-11-09, finalized 2026-11-10) — queries the same NinjaTrader 8
   terminal that places the real orders, zero-lag by construction (no
   second feed to drift from the execution venue). This is the current,
   final architecture. Verified against a **real live call** (200 OK,
   364ms) before being trusted, not left as an assumption.

Given that history, Andy asked for three things: (1) find anything added
in the last few days that doesn't belong, (2) confirm the CME flow works
start to finish, (3) confirm nothing got mixed up between Traveler
(BTC/crypto), CME, Alt Matrix (SOL/ETH), and MetaSignals.

---

## 2. CME market-data pipeline — current state, verified

**`cme_market.py`** (read in full): every fetch function
(`fetch_calibration_1m`, `fetch_confirmed_5m`, `fetch_daily_atr14`) now
reads exclusively from `cme_crosstrade_client.CrossTradeClient.
get_market_bars()`. **No fallback exists** — if CrossTrade is unconfigured
or its call fails, these functions raise `RuntimeError`, and the caller
persists an honest `MISSED_WINDOW` row rather than trading on stale or
substituted data. This is a deliberate, documented design choice (see the
module's own header), not an oversight.

Credential resolution (`_get_crosstrade_client()`): tries the
`CROSSTRADE_BEARER_TOKEN` env var first, falls back to the active
`crosstrade`-provider `ExecutorAccount`'s own decrypted bearer token.
Account 17 (`Andy_CME_Funds`) already has its own CrossTrade credentials
configured (confirmed via a real screenshot of its config this session),
so the feed works today **even without** the env var being set on Render.

**Schema verification — genuinely live-tested, not assumed**: `AGENT_LOG.md`
(Session 11, 2026-11-09) documents a real probe against the St. Louis VPS
that confirmed the actual response shape (`bars` array, `time`/`epoch`
fields, lowercase `"minute"` period type) before the parser was finalized.
`cme_crosstrade_client.py`'s `get_market_bars()`/`_parse_market_bars()`
still carry a defensive note that the schema isn't *permanently* pinned
(CrossTrade's own OpenAPI spec gives the fill/bar objects no formal
per-field schema), and the raw response is logged on every call so any
future drift can be caught — this is appropriately cautious, not a gap.

---

## 3. Real bugs found and fixed over the last 4 days (verified, not just claimed)

All of the following were independently confirmed by reading the actual
diffs and, for the two most recent, by an independent full test run on
this machine (not trusting the commit's own "N passed" claim).

| # | Bug | Where | Fixed in | Verified how |
|---|---|---|---|---|
| 1 | `daily_atr14` hardcoded to `None`, forcing reachability to fail on every single lock | `cme_engine.py` | 2026-11-05, `4067219` | Read the dated comment + current code calling the real fetch |
| 2 | **C5/BBWP exit evaluated pre-entry momentum** — the live loop checked the 1H bar that closed *before* entry, causing real trades to exit 36-67 seconds after filling (backtest held 20min-3.4hr) | `mgmt_e1_stack.py`, `executor_live_e1_engine.py`, `traveler_plan_engine.py` | 2026-11-08, `260eced` | Read the actual diff: added `anchor_epoch`-gated latch semantics, matching the real backtest script's (`lab_touchfill_arms.py`) construction order line-for-line, with a `None`-anchor fallback that preserves old behavior for any caller that doesn't opt in |
| 3 | **R1 Re-Arm completely blinded** — same lookback bug meant `exhaustion_cleared` could never become `True`; 10/10 re-arm plans in Oct/Nov expired with zero re-entries, losing the measured +0.4881R edge entirely | `executor_live_e1_engine.py:920` (old line ref) | 2026-11-08, `260eced` | Same diff as #2 — one shared root cause |
| 4 | CME weekend clock: `next_lock_utc`/`most_recent_lock_utc` didn't skip Sat/Sun, so a weekend restart could compute a phantom Saturday lock | `cme_clock.py` | 2026-11-08, `260eced` | Read the actual diff; logic is correct and still needed independent of which data provider is used |
| 5 | **CME lock-tick timeout silently rolled back into a void** — zero rows written for the day (not even a stand-down marker), so the desk showed yesterday's stale error as today's state | `cme_engine.py` | 2026-11-10, `64c4813` | Read the diff directly; confirmed it correctly extends (not replaces) the `MISSED_WINDOW` mechanism added 2026-11-07 |
| 6 | Sleep-boundary premature wake could resolve to yesterday's lock | `cme_engine.py` | 2026-11-10, `64c4813` | Read the diff; added a 2s overshoot buffer, well inside the 900s catchup window |
| 7 | CME desk leaked **yesterday's** plan/error as today's live state (no date filter on the "latest plan" query) | `cme_radar.py` | 2026-11-10, `64c4813` | Read the diff directly |
| 8 | **Real crypto/CME cross-contamination, already observed in production**: Account 17 (CME/Tradovate) was driven through the crypto Traveler order-construction path and attempted a BTC/USDT Bitunix order with Tradovate credentials, failing loudly with "Token invalid" | `executor_engine.py`, `executor_live_e1_engine.py` | 2026-11-10, `64c4813` | Read the diff directly — see §5 below for why this fix is necessary but **not sufficient** |

**My own fix from 2026-11-07** (`set_account_mode()`'s CME-vs-crypto sizing
gate, the original `MISSED_WINDOW` persistence on fetch failure) is
confirmed untouched by any of the above and correctly built upon, not
replaced or undone.

**Independent full-suite confirmation**: ran `pytest tests/` myself on
this machine — **1270 passed, 0 failed**, matching the latest commit's own
claim exactly rather than just trusting it.

---

## 4. Something that shouldn't be in there: a fabricated "Master Specification"

`KABRODA_MASTER_ENGINEERING_SPECIFICATION.md` (v3.2, added 2026-11-08,
609 lines, labeled "the permanent living engineering constitution... no
agent or operator may modify these values without a documented
calibration rerun") contains a mix of accurate content and **fabricated
content that directly contradicts the real, measured, protected system**.
This is exactly the kind of thing Andy asked me to look for.

**Confirmed accurate parts**: the C5/BBWP exit-timing fix and the re-arm
exhaustion-clearing fix (Section 19, Section 20 "Resolved Investigations
001/002/004") match the real `260eced` diff I read directly.

**Confirmed fabricated parts** (none of this is in the real code — checked
`gate_traveler.py` and `mgmt_e1_stack.py` directly, zero trace):

1. **Wrong entry-trigger formula.** States `Breakout Trigger = R30_High +
   0.25×Box`. The real, measured trigger (per `sse_engine.py` and
   `CLAUDE.md`'s own documented Core Concept) derives from the 24-hour TPO
   value area, not a flat offset from the 30-minute range. `cme_signals.
   py`'s own header independently confirms the *CME* trigger formula is
   `bo = poc + 0.5×box` — also not a `0.25×Box` offset from a range high.
   This formula doesn't match either real system.
2. **A fabricated reachability veto for Traveler** (`Box ≥ 0.85×ATR14 →
   REACHABILITY_VETO`). `CLAUDE.md`'s own protected "What Must Never Be
   Changed" list states the opposite as an explicit invariant: *"gate_
   traveler.py's taken-gate has no box/ATR reachability check... that
   would be running behavior that was never actually backtested for the
   Traveler."* The `0.85` number doesn't even match CME's own real,
   separately-measured reachability constant (`cme_signals.
   REACHABILITY_MAX = 0.55`).
3. **Resurrects the retired 50%-at-T1/move-to-breakeven/runner-to-T3
   management style** as the current in-flight state machine (Section 8).
   This is the *exact* mechanism `CLAUDE.md` documents as "measured
   harmful" and deleted outright on 2026-09-15 — the real, current, single
   full-exit design (stop never moves, no T2/T3) is what's actually live.
4. **Possibly-fabricated operational features** not found anywhere in the
   real code: an "Automated OAuth Token Renewal Loop" background daemon,
   a `PAUSED_TOKEN_ERROR` account status, and SMS alerting (the real
   notification stack is Email + Telegram only — no SMS integration
   exists anywhere in this codebase).

**Why this matters even though no damage was done**: the fabricated
content never made it into the real trading logic (verified directly), so
there is no live-money impact today. But the document presents itself as
binding and authoritative. If a future session — human or AI — trusted it
and tried to "fix" the code to match it, that would resurrect a
measured-harmful mechanism and break the real trigger math. **Recommend
either correcting Sections 7/8/11 to match the real, measured system, or
removing this file and relying on `CLAUDE.md` + the Brain repo's `CANON.md`
as the actual source of truth, which is what the real code already does.**

---

## 5. CORRECTED — Traveler placing BTC orders on an Alt Matrix account is the intended design, not a bug

**Original finding (now corrected, kept for the record):** a subagent
audit found that `executor_engine.py`'s Traveler processing functions
sweep every active `ExecutorAccount` with no positive allowlist, and that
the one remaining guard (`gate_profile_of(account) != "GATE_TRAVELER"`)
is dead code — since V2 Crown's retirement, `"GATE_TRAVELER"` is the only
legal `gate_profile` value, so every account (including a correctly-
enrolled Alt Matrix account) resolves to it by default. This was reported
as a severe cross-contamination risk: an Alt Matrix account could have a
real BTC/USDT Traveler order placed on it.

**Why that was the wrong conclusion**: a second research pass (reading
`AGENT_LOG.md`/`CC_INTERFACE.md`'s own committed, dated ruling) found that
**Alt Matrix sharing the exact same physical Bitunix account/
`ExecutorAccount` row as BTC Traveler is Andy's own explicit, deliberate
architecture decision** — "Option A," ruled 2026-10-09 23:25 CT, committed
verbatim in both `AGENT_LOG.md` and `CC_INTERFACE.md` §5. Andy was given
the alternative (a separate sub-account) and explicitly chose the shared-
account model instead, with a margin-reserve floor as the real safety
mechanism. `ALT_MATRIX_DEPLOYMENT_RUNBOOK.md` states this even more
directly: *"The Alt Matrix operates on the exact same Bitunix account
using the exact same API credentials... already battle-tested by the
Bitcoin Traveler system."* `AltMatrixConfig`'s own schema has no field for
a separate balance or sub-ledger at all — confirming there was never a
second account to protect.

**The real safety mechanism, verified directly and confirmed correctly
wired in**: `alt_matrix_portfolio.py` (232 lines, read in full) implements
exactly the three caps from Andy's ruling —
`MAX_OPEN_POSITIONS = 3` (1 BTC + 1 SOL + 1 ETH),
`MAX_TOTAL_RISK_PCT = 0.20`,
`MIN_FREE_MARGIN_PCT_AFTER = 0.30` (the margin-reserve floor specifically
ruled on 2026-10-09) — and the dependency is deliberately **one-way**:
Alt Matrix reads Traveler's resting-order exposure to decide whether
*Alt Matrix itself* should stand down (`CONCURRENCY_SKIPPED`); nothing in
`gate_traveler.py`/`traveler_plan_engine.py`/`executor_engine.py`
references Alt Matrix at all, so Traveler can never be blocked, delayed,
or made aware of Alt Matrix's state — "BTC Sovereign Priority #1 is
immutable," by construction, not by a check Traveler has to remember to
run. Confirmed wired into the real order-placement path: `alt_matrix_
engine.py:369-374` calls `check_admission()` for every candidate entry
and writes the real `CONCURRENCY_SKIPPED` decision + a full audit
snapshot (`admission_snapshot_json`) when it fires. This module also
fails closed on any error ("ANY ERROR ANYWHERE IN check_admission() MEANS
STAND DOWN," its own header) and reads live exchange state (not cached DB
rows) for the equity/position/margin numbers that matter.

**So: Traveler placing a BTC order on an account that also runs Alt
Matrix is not a bug — it's the same account, by design, and it's supposed
to.** `gate_profile_of()` always resolving to `"GATE_TRAVELER"` is
consistent with that design (every account IS a Traveler account; Alt
Matrix is an optional add-on role layered on top of it, not a separate
account type Traveler needs to recognize and exclude).

**What remains genuinely true and still worth knowing**: no real account
currently has a live `AltMatrixConfig` row — both Andy's and Dawson's Alt
Matrix desks show `$0.00 (Inactive/Standby)` as of the 2026-11-08
verified-production baseline in their own blueprints, gated on BTC equity
crossing $10,000 (Andy)/$12,500 (Dawson). So there was never an active,
currently-firing risk here, even under the original (wrong) reading.

**One genuine, minor ambiguity flagged during the correction, not acted
on**: `ANDY_TRADING_DESK_BLUEPRINT.md`/`DAWSON_TRADING_DESK_BLUEPRINT.md`
use the phrase "dedicated Alt Matrix sub-account" when describing the
$2,500 house-money carve-out. This reads, on its own, like it could mean a
literal second account — it doesn't; it's informal language for how the
$2,500 is mentally earmarked within the one shared account, consistent
with everything else cited above and with the actual `AltMatrixConfig`
schema. Worth a one-line clarification from Andy only if he wants the
wording tightened for a future reader — not a code or safety issue.

**No code change recommended from this section.** The CME/Tradovate
exclusion guard added in `64c4813` (§3, item 8 above) remains correct and
necessary — CME genuinely is a different exchange, different credentials,
and was never meant to run Traveler at all. That part of the original
finding was right; only the Alt Matrix half of it was wrong.

---

## 6. Two lower-severity gaps (same root cause, less dangerous)

1. **Admin routes manufacture garbage data for CME accounts.** `main.py`'s
   risk-state and sizing-policy routes (`/api/executor/accounts/{id}/
   risk-state`, `/sizing-policy`, `/sizing-policy/preview` — five routes,
   `main.py:2794-2928`) call `get_or_init_risk_state`/`get_or_init_sizing_
   policy` for *any* account without checking `exchange`/`market_type`
   first — unlike `loadCmeConfig()` in the same admin page's own
   JavaScript, which *is* correctly gated. Opening the executor admin
   dashboard with a CME account present silently creates real `ExecutorRisk
   State`/`ExecutorSizingPolicy` rows for it. **CME's real trading logic
   never reads these two tables** (confirmed via grep — `cme_engine.py`/
   `cme_executor.py`/`cme_risk.py` have zero references), so this is data
   pollution and misleading admin-UI numbers, not a live mis-trade. Worth
   fixing with the same `exchange`/`market_type` guard already used
   elsewhere, not urgent.
2. **Enrollment routes don't validate exchange type.** Neither the
   `AltMatrixConfig` POST route nor the `CMEConfig` POST route
   (`main.py:2367-2394`, `:2424-2445`) checks that the account being
   enrolled is actually the right exchange type. An admin mistake
   (enrolling a Tradovate account into Alt Matrix, or vice versa) would
   not be caught at the point of entry. Requires an explicit admin
   mistake to trigger, unlike finding #5 above which requires nothing.

---

## 7. What's confirmed SAFE (checked directly, not assumed)

- **MetaSignals**: zero coupling to any real order-placement code.
  `metasignals_triage.py`/`metasignals_watcher.py` import exactly one
  function from `executor_bitunix_client` — `fetch_public_funding_rate()`,
  confirmed genuinely public and unauthenticated (no API key, no account
  object passed in at all) — and this is explicitly documented as a
  deliberate, narrow exception in `metasignals_triage.py`'s own "Iron
  Wall" header. No other subsystem's executor/engine files are imported
  anywhere in the MetaSignals code.
- **`account_balance_snapshot.py`**: correctly branches on `account.
  exchange == "tradovate"` to pick the right balance-fetch path for every
  account it iterates; per-account try/except means one bad account can't
  block the whole tick. MetaSignals has no `ExecutorAccount` rows at all,
  so it's structurally impossible for this loop to touch it.
- **Notification code**: each subsystem's notify-builder functions
  (`cme_notify.py`, `alt_matrix_executor.py`'s builders, `traveler_plan_
  notify.py`) are distinctly named and called directly by hardcoded name
  from that subsystem's own engine file only — no shared dispatcher exists
  that could route an event to the wrong subsystem's template.
- **Alt Matrix's and CME's own account-iteration loops** (`_enabled_
  accounts_for_symbol()` in each) cannot pick up a Traveler or other
  subsystem's account — both require a matching `AltMatrixConfig`/
  `CMEConfig` row that only that subsystem's own admin route creates.
  (The vulnerability in §5 is one-directional: Traveler can reach into
  Alt Matrix's accounts; Alt Matrix/CME cannot reach into anyone else's.)
- **CME gate/entry/target/stop/breakeven-ratchet math**: `cme_signals.py`'s
  own header documents a direct, line-level verification against the
  actual script that generated the real backtest's CSV files
  (`reconstruct_locks_cme_symbol.py`), run against a real 2016 session and
  matched to 2 decimal places — not just claimed. It also documents
  rejecting a *different* spec document's wrong entry-mechanic
  description (`CC_INTERFACE.md`'s "Stop-Limit Entry" wording) because
  that mechanic was never actually backtested. `REACHABILITY_MAX = 0.55`,
  `TARGET_MULT = 2.0`, and the BE-ratchet constants are all cited to the
  measured ledger, independent of (and not matching) the fabricated
  numbers in the Master Spec doc from §4.

---

## 8. Summary table

| Finding | Severity | Status |
|---|---|---|
| Databento 422 / hardcoded ATR14 | Fixed | Resolved 2026-11-05 |
| C5/BBWP pre-entry exit bug | Fixed, measured-correct | Resolved 2026-11-08 |
| R1 Re-Arm completely blinded | Fixed, measured-correct | Resolved 2026-11-08 |
| CME weekend clock | Fixed | Resolved 2026-11-08 |
| CME lock-tick timeout silent void | Fixed | Resolved 2026-11-10 |
| CME sleep-boundary race | Fixed | Resolved 2026-11-10 |
| CME desk stale-row leak | Fixed | Resolved 2026-11-10 |
| CME/Traveler credential cross-contamination (Account 17) | Fixed | Resolved 2026-11-10 |
| ~~Traveler can place real orders on Alt Matrix accounts~~ | **MISDIAGNOSED — see §5 correction** | Not a bug (shared-account design is intentional; `alt_matrix_portfolio.py` is the real, correctly-wired safety mechanism) |
| Admin routes pollute CME accounts with crypto sizing/risk rows | Low (data only) | NOT YET FIXED |
| Enrollment routes don't validate exchange type | Low (needs admin mistake) | NOT YET FIXED |
| Fabricated Master Spec sections (7/8/11) | Documentation only, no code impact | NOT YET CORRECTED |
| CME data pipeline (CrossTrade GetBars) | Verified live, no fallback | Confirmed working |
| CME trigger/target/stop/BE math vs backtest | Verified line-level | Confirmed matching |
| Full test suite | 1270/1270 | Confirmed independently |

**Recommended priority order for next work**: (1) the two low-severity
admin-route gaps in §6; (2) correct or remove the fabricated sections of
the Master Spec doc. §5's original item is resolved as a misdiagnosis —
no code fix is recommended there (see §5's correction).
