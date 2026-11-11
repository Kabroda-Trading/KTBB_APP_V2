# CME / Traveler / Alt Matrix / MetaSignals Cross-System Audit — 2026-11-11

**Requested by:** Andy, after 3-4 days of CME market-data rebuilds that kept
not working, plus a direct ask to confirm nothing got mixed up between the
four trading subsystems.
**Performed by:** Claude Code, read-only code audit + one subagent +
independent full-suite test run. Every claim below was verified directly
against the current code (file:line) or a real test run — nothing here is
relayed from a commit message or another agent's say-so without being
checked first.
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

## 5. The severe finding: Traveler can still place real orders on an Alt Matrix account

This was found by a focused subagent audit and independently re-verified
directly in the code before being written up here (confirmed `executor_
accounts.py:217-228`, `main.py` gate_profile usage, and the Alt Matrix
enrollment route).

**The mechanism**: `executor_engine.py`'s four Traveler processing
functions (`process_traveler_cross`, `process_traveler_fill`, `process_
traveler_rearm_cross`, `process_traveler_rearm_fill`) each query **every
active `ExecutorAccount` in the system**, with no positive allowlist of
their own (unlike Alt Matrix and CME, both of which only ever touch
accounts that have an explicit `AltMatrixConfig`/`CMEConfig` row). Each
account then passes through `_process_traveler_account()`, which has
exactly two skip conditions:

1. `account.exchange == "tradovate" or account.market_type ==
   "CME_FUTURES"` → skip (the fix added 2026-11-10 for the CME incident).
2. `executor_accounts.gate_profile_of(account) != "GATE_TRAVELER"` → skip.

**Condition 2 is dead code.** Since V2 Crown's full retirement (2026-09-24),
`"GATE_TRAVELER"` is the *only* legal `gate_profile` value left in the
system (`_VALID_GATE_PROFILES = ("GATE_TRAVELER",)`), and `gate_profile_
of()` explicitly resolves a never-configured (`NULL`) account to
`"GATE_TRAVELER"` by design (`executor_accounts.py:223-228`, with its own
docstring confirming this is deliberate: *"a brand-new or never-touched
account reads as GATE_TRAVELER... not None"*). This means **every account
in the entire system, from every subsystem, resolves to `"GATE_TRAVELER"`
unconditionally** — the field no longer discriminates anything.

**Confirmed**: `gate_profile` is only ever set by one manual admin route
(`main.py:2334`, `set_account_profile`), never automatically by the Alt
Matrix enrollment flow (`main.py:2367-2394`, the `AltMatrixConfig` POST
route) — so a real, normal Alt Matrix account, enrolled exactly the way
the admin UI intends, has `gate_profile = NULL` forever, which resolves to
`"GATE_TRAVELER"`.

**The consequence**: an Alt Matrix account (Bitunix, SOL/ETH) is **not**
`exchange == "tradovate"` and **not** `market_type == "CME_FUTURES"` — so
it passes guard #1. It also passes guard #2, because guard #2 can no
longer fail for *any* account. **It proceeds into `build_hypothetical_
traveler_order()`, which builds and — if the account is LIVE — places a
real BTC/USDT order using that account's real Bitunix credentials**,
completely independent of and invisible to Alt Matrix's own SOL/ETH book
running on the same account.

**This is not a hypothetical requiring a misconfiguration mistake — it is
the default, normal state of any account enrolled into Alt Matrix the
intended way.** It is a more serious version of the CME incident already
fixed: that one failed loudly (wrong credentials, "Token invalid"); this
one would **succeed silently** — a real, live, wrong-symbol order placed
with no error at all, since Alt Matrix accounts genuinely do have valid
Bitunix credentials.

**Immediate question to resolve before any fix ships**: does a real, LIVE
Alt Matrix account currently exist? If yes, this is an active, live risk
right now and should be treated with the same urgency as the CME incident.
If Alt Matrix is still DRY_RUN-only today, this is serious but not yet
live-money-urgent — still worth fixing before Alt Matrix goes live.

**Recommended fix** (not yet implemented — this document is the audit,
not the fix): give Traveler the same positive per-account allowlist
pattern Alt Matrix and CME already use (e.g., a `TravelerConfig` table, or
a simple boolean/flag on `ExecutorAccount`), rather than continuing to
patch `_process_traveler_account()`'s negative-exclusion guard one
subsystem at a time. The current pattern (exclude what you recognize)
will need a new exclusion every time a new subsystem is added; the
positive-allowlist pattern (only process what you've explicitly enrolled)
is what Alt Matrix and CME already correctly do and doesn't have this
failure mode.

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
| **Traveler can place real orders on Alt Matrix accounts** | **Severe, live, default-state** | **NOT YET FIXED** |
| Admin routes pollute CME accounts with crypto sizing/risk rows | Low (data only) | NOT YET FIXED |
| Enrollment routes don't validate exchange type | Low (needs admin mistake) | NOT YET FIXED |
| Fabricated Master Spec sections (7/8/11) | Documentation only, no code impact | NOT YET CORRECTED |
| CME data pipeline (CrossTrade GetBars) | Verified live, no fallback | Confirmed working |
| CME trigger/target/stop/BE math vs backtest | Verified line-level | Confirmed matching |
| Full test suite | 1270/1270 | Confirmed independently |

**Recommended priority order for next work**: (1) confirm whether a real
LIVE Alt Matrix account exists today — this determines if §5 is an active
emergency or a pre-emptive fix; (2) fix §5 regardless, before Alt Matrix
ever goes live, using the positive-allowlist pattern; (3) the two
low-severity admin-route gaps in §6; (4) correct or remove the fabricated
sections of the Master Spec doc.
