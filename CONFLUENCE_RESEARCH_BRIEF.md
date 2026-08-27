# Research Brief — How Real Traders Build Graded Confluence Systems

**For:** DeepSeek/Antigravity (or whichever research agent picks this up)
**Requested by:** Andy, 2026-08-27
**Purpose:** Answer a specific design question for Kabroda's Phase 4 decision layer — not "what does indicator X mean" (already validated, see `EXTERNAL_VALIDATION_REPORT.md`), but **how practitioners combine multiple partially-aligned or conflicting signals into a graded conviction call**, instead of a rigid all-or-nothing gate.

---

## Why this brief exists

Kabroda's Phase 4 decision layer currently gates trades with a strict AND: trend, volatility, and momentum must ALL align, or the system stands down. A 4-year backtest of that design produced 1,213 stand-downs against 228 approved trades — a system that almost never has a clear answer. Andy's read, and the reason for this brief: that's not evidence the market rarely sets up — it's evidence the gate is too coarse. A real trader doesn't have three outputs (long/short/nothing); they have a *graded* read — strong setup, a lean, or genuinely neutral — and they act (or size) differently depending on which.

Nobody on this project has verified how real, established traders actually build that graded judgment. Rather than invent it, go find out.

## Ground rules (same discipline as `EXTERNAL_VALIDATION_REPORT.md` — read that file's §0 first for the method)

1. **Check the library first.** `Trading Knowledge/knowledge/` may already have material on confluence scoring, checklist systems, or how Krown/QPAI/the Trading Bible weight multiple signals — read it before reaching for the open web.
2. **"Not found publicly" ≠ fabricated or wrong.** Tag every claim ✅ CONFIRMED / 💭 PARTIAL / 🔒 UNVERIFIED, and reserve ❌ REFUTED for genuine contradiction, not silence.
3. **Prefer real, named, checkable sources** — published trading books, well-documented systematic strategies, reputable trading educators' public material, academic work on technical-analysis confluence — over generic forum consensus. Cite what you can.
4. **This is about METHOD, not new indicators.** The goal is not to find new tools to bolt on — Kabroda's tools (EMA trend, BBWP/PMARP volatility, Stochastic/RSI momentum, structure) are already chosen and validated. The question is how the combination logic should work.

## The actual questions

1. **Is there an established "confluence scoring" methodology?** E.g., "X of N signals must agree for full size, fewer for reduced size/lean." Who teaches this, and what does their specific scoring look like? Is it typically a simple count, or weighted (some signals count more than others)?

2. **How do practitioners define the tiers themselves?** Is a 3-tier model (strong / lean / neutral) common, or do real systems use more granularity (5+ levels)? Is there a common name for this kind of system ("confluence trading," "checklist trading," "scorecard trading," etc.) worth searching specifically?

3. **What determines when one signal should be allowed to override or downweight others?** E.g., if trend and volatility agree but momentum disagrees, do real traders still lean with the majority, or does a single strong disagreement void the setup regardless of the others? Is there a documented hierarchy (some signals are gates, others are just confirmation)?

4. **How is "neutral" itself defined, distinct from "not fully aligned"?** Kabroda's current problem: everything short of perfect alignment currently collapses into the same STAND_DOWN bucket as genuinely flat/no-signal conditions. How do real systems distinguish "weak lean" from "truly nothing happening"?

5. **Real, checkable examples.** Find at least 2-3 named, real trading methodologies (with sources) that publish their actual confluence/checklist logic — not just the concept in the abstract. What do their real checklists look like, item by item?

## Output format

Same as `EXTERNAL_VALIDATION_REPORT.md`: a markdown report, confidence-tagged, organized by question above, ending with a plain "what this means for Kabroda's tier design" section — but **recommendations, not decisions**. This is Andy's call to make once the real answer is in front of him, not something to be decided in the research pass itself.
