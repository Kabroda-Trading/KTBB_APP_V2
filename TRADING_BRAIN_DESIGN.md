# Trading Brain — Design Conversation Record

> **Status:** conversation-only. No code written, no files moved/deleted.
> **Purpose:** capture the 2026-08-26 design discussion so Claude Code (and
> Andy) can review and verify it. Written to prevent drift/fabrication — every
> claim below is tiered so nothing gets blurred into "settled" when it isn't.

---

## Tier legend

- **✅ Verified** — read directly from files on disk; checkable, not recollection.
- **🗣️ Andy's stated intent** — what Andy said he wants, in his framing.
- **💡 My recommendation** — my opinion, explicitly *not* a decision.

---

## The three-way split (🗣️ Andy's stated intent)

1. **Site** — `KTBB_app_v2` (this repo). Clean up, correct the rule sets, make
   sure the information is correct. The radar keeps doing what it does and
   produces the daily opportunity.
2. **Indicators** — two separate "crafting table" projects, already on disk:
   - `C:\Users\Shadow\Workspace\Revin Ribbons Suite` → Revin Ribbons / RMO / RWP
   - `C:\Users\Shadow\Workspace\PA Pivots` → PA Pivots
3. **Brain** — a NEW project (not yet created). Multi-agent, dashboard-connected
   into Antigravity, monitors feeds/levels/rules, answers "can I trade today /
   which direction / should I enter."

---

## The live-feed question (💡 my recommendation — NOT settled)

There is no single "live feed." Three different plumbing paths, one per data type:

| Data | Feed path | Status |
|------|-----------|--------|
| Levels (bo/bd, T1/T2/T3, 30m range, gravity) | Read from the site's own `SessionLock`/DB/API — NOT OCR'd from a screenshot | ✅ Already live |
| Revin Ribbons / RMO / RWP | Reimplement in Python (specs in `Trading Knowledge` library; `.pine` = source of truth) | 💡 Native, deterministic, no browser |
| PA Pivots | Screenshot/browser or TradingView webhooks only | ⚠️ Time-limited + permanent gap (see below) |

---

## PA Pivots — the risk (✅ verified from `PA Pivots/AGENT_LOG.md`)

- **🔒 Permanent gap:** the core pivot-detection algorithm is undisclosed.
  Krown's PA Pivots is paid/invite-only, no source code, ever. The
  `leftBars`/`rightBars` investigation (2026-08-20) proved `ta.pivothigh`/
  `ta.pivotlow` with *any* fixed window is the wrong model.
- **Time-limited access:** Andy's access to the real indicator is a trial window
  of a few days, not a kept subscription.

**Consequence:** PA Pivots cannot be reimplemented in Python the way
Ribbons/RMO/RWP can. Its only "live feed" (screenshot or webhook) dies when the
trial ends.

---

## My architectural lean (💡 opinion, not decision)

- **Trend leg** → Ribbons / RMO (reimplemented in Python).
- **Volatility leg** → RWP + the fuel/movement question.
- **Structure leg** → bo/bd triggers (from the site) + PA Pivots as a
  **confirmation overlay only**, not a core input the decision depends on.

---

## Three OPEN questions (unresolved — need Andy's answer)

1. Is the brain's decision allowed to *depend* on PA Pivots, or is PA Pivots a
   nice-to-have overlay? (💡 My lean: overlay-only, to avoid a time-bomb when the
   trial ends.)
2. Live feed: reimplement indicators in Python (native/continuous) vs. read from
   TradingView (screenshots/webhooks)? (💡 My lean: reimplement Ribbons/RMO/RWP,
   reserve browser for PA Pivots only.)
3. Where does the brain live on disk, and does it get its own `AGENT_LOG.md` +
   rules from day one? (💡 My lean: fresh repo with full cross-agent structure.)

---

## Housekeeping note (✅ what happened this session)

Andy asked about "taking out garbage files" in this repo. I declined to act on a
vague instruction — the 2026-08-17 `AGENT_LOG.md` entry is the exact warning (a
runtime-loaded prompt spec was archived and broke a live production route). Andy
agreed to leave files alone for now. **No files were moved or deleted this
session.**
