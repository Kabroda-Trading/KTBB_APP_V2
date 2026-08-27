# Confluence Research Report — How Real Traders Build Graded Conviction Systems

**Date:** 2026-08-27
**Requested by:** Andy (via `CONFLUENCE_RESEARCH_BRIEF.md`)
**Method:** Six independent research agents — library-first, then open web. Every claim tagged ✅ CONFIRMED / 💭 PARTIAL / 🔒 UNVERIFIED / ❌ REFUTED. "Not found" ≠ "fabricated."

---

## 0. The problem this answers

Kabroda's Phase 4 decision layer gates trades with a strict AND: trend AND volatility AND momentum must ALL align, else STAND_DOWN. A 4-year backtest produced **1,213 stand-downs vs 228 approved trades**, and the approved trades **lost money on average (-0.126R)**.

Andy's diagnosis (correct): that's not "the market rarely sets up" — it's "the gate is too coarse." A real trader grades conviction (strong / lean / neutral), not binary pass/fail.

This report answers: **how do real, established traders actually build that graded judgment?**

---

## 1. The headline finding — every source converges on the same answer

The rigid AND-gate is a **documented anti-pattern** with a name: the **"unanimity trap."** It collapses trade frequency, causes "analysis paralysis," and is *especially* harmful when indicators are redundant (then "unanimous agreement" is one signal counted three times).

The fix is **not** "loosen AND to OR" and **not** "majority vote." It's a **funnel with a graded output**:

```
Gate (binary) → Confirmation (probabilistic) → Trigger
```

- **Gate** = "am I ALLOWED to trade now?" (environment/regime — trend, volatility regime). Disagreement = STAND_DOWN.
- **Confirmation** = "is THIS signal high-conviction?" (timing — momentum, volume, price action). Disagreement = downgrade conviction, *not* void.

This is stated near-identically across the practitioner literature, the named systems, AND the academic/quant literature. Three independent research angles converged on it.

---

## 2. What the library already contains (the source-grounded anchor)

The library agent found the **only directly-sourced confluence material** — and it's important to separate this from the library's own unverified synthesis.

### Directly sourced (✅, from Krown's actual transcripts):

1. **Trend → Volatility → Momentum precedence** — verbatim from `05_the_krown_system.md`: *"we will assume that trend take precedents over volatility, which take precedents over momentum."* Both courses (Trading Bible + Ultimate TA Beginner) agree **trend is unconditionally #1**. The rest is *loosely* ordered — **no numeric weights anywhere**.

2. **The 4 checklist templates** — exact thresholds, directly quoted (see `TRADING_FRAMEWORK.md` L149-154).

3. **The binary rule we're replacing** — verbatim from Ultimate TA Beginner: *"if one step fails, the trade idea dies... if you do not see both of those factors aligned, you do not have a fucking trade."* This is the ONLY directly-sourced confluence rule, and it's the binary gate that over-stands-down.

### The library's own synthesis (🔒, NOT directly sourced — do not treat as Krown's):

- **"Confluence Scorecard"** (4/4, 3/4, 2/4, ≤1/4) — explicitly self-flagged as "this library's own synthesis... not a direct citation."
- **"Decision Matrix"** (6 rows, 6 actions) — flagged "not a direct transcript quote."
- **"Golden Rule"** — flagged "this library's own restatement... not a single direct quote."
- **KQAL** ("0-10 alignment score") — **broken/incomplete**: `alignment_engine.py` was never implemented, `import kqal` raises `ModuleNotFoundError`.
- **Bridge code** — uses only a 2-tier `confidence` (`high`/`medium`), not 3-tier.

**Key implication:** a strong/lean/neutral conviction model is a **NEW design**, not something the library already prescribes. But it should be *anchored* to the sourced precedence (trend = hard gate; volatility/momentum = graded layers), not to the library's own unverified scorecard.

---

## 3. The graded model — what the sources converge on

### 3a. The natural 3-tier structure (source-grounded)

The precedence statement gives a clean 3-tier structure **without inventing weights the library never provides**:

| Signal | Role | Disagreement → |
|---|---|---|
| **TREND** | Binary hard gate (environment/regime, HTF) | STAND_DOWN (void) |
| **VOLATILITY** | Second gate (regime — "is the market even tradeable") | STAND_DOWN or heavy downgrade |
| **MOMENTUM** | Confirmation (trigger quality) | Downgrade to "lean"/"neutral", *not* void |

This maps directly onto "trend takes precedence over volatility, which takes precedence over momentum" — and it's what the hierarchy agent, the quant agent, AND the library agent independently converged on.

### 3b. Tier granularity — 3-tier vs 5-level

Both are legitimate. The field splits:

- **3-tier (strong/lean/neutral)** — common in retail/prop education. Named example: **SMB Capital's A/B/C** (Mike Bellafiore): A+ = best setups (most risk), B = edge but imperfect (less risk), C = "feeler" trades (least risk).
- **5-level (A+/A/B/C/F, or 1–5)** — the dominant convention in *systematic* contexts. Named examples: **Zacks Rank** (#1 Strong Buy → #5 Strong Sell), **Wall Street analyst ratings** (Strong Buy/Buy/Hold/Sell/Strong Sell), and the A+/A/B/C/F trade-journal grading.

**Recommendation (not decision):** 5 levels, or a 0–100 continuous score bucketed into 5, is the more defensible choice if we want to match what real systematic systems do. But 3-tier is a legitimate, simpler starting point.

### 3c. Conviction → position sizing (well-documented)

The mapping is consistent across sources:

| Grade | Risk % of account |
|---|---|
| A+ / Conviction 5 | 2.0% (max) |
| A / Conviction 4 | 1.5% |
| B / Conviction 3 | 1.0% (standard) |
| C / Conviction 2 | 0.5% |
| F / Conviction 1 | 0.25% or skip |

3-tier version: **Strong = full (2%), Lean = half (1%), Neutral = 0 or minimal "feeler" (0.5%).**

**Universal cross-cutting rule (every source agrees):** conviction adjusts the *risk percentage*, but the **stop-loss distance determines the actual share/contract count**. `Position Size = (Account × Risk%) ÷ (Entry − Stop)`. Conviction never bypasses the stop.

**Quantitative version (Kelly):** fractional Kelly (½ or ¼) × conviction multiplier. **Negative Kelly = mechanical "no trade"** — the cleanest quantitative definition of "neutral."

### 3d. The core problem solved — "neutral" vs "weak lean"

This is the single most important finding. The distinction is real and well-documented:

| State | Nature | Action |
|---|---|---|
| **Neutral / No Edge** | No directional bias; EV ≈ 0 or negative; "flat, no signal" | **Stand down / cash** (0%) |
| **Weak Lean / Weak Edge** | Positive but small/fragile EV; signal exists but low confidence | **Small/probe size** (25–50%) |
| **Strong** | High-conviction, multi-factor confluence | **Full size** |

**The one-line distinction:** **Neutral = "no edge" (a *lack* of a valid thesis). Weak lean = "weak edge" (a *valid but fragile* thesis).**

Our current system collapses these because it treats "not fully aligned" as a single boolean. Real systems separate them by asking a *different question*: **"Is there a positive-expectancy thesis at all?"**
- If NO → neutral → stand down (0%).
- If YES but weak → lean → small/probe size (25–50%).

The Kelly "negative EV = no trade" rule is the cleanest quantitative implementation of this split.

---

## 4. Named systems — the actual published checklists

| System | Combination logic | Partial-alignment handling |
|---|---|---|
| **Elder — Triple Screen** | Hard sequential gate (Tide→Wave→Ripple) | "If any screen contradicts, do not trade" — no partial credit |
| **Minervini — SEPA Trend Template** | Hard gate (8 criteria, "must satisfy ALL") | None at template stage (gateway filter) |
| **O'Neil — CANSLIM** | Filter/holistic, NOT weighted; **"M" is a hard veto** | "All or nearly all"; C/A/I non-negotiable; M = veto |
| **Turtle Traders** | Single-signal mechanical (no confluence) | N/A — deliberately avoided confluence |
| **Darvas Box** | Sequential gate (momentum→box→breakout) | Volume = soft confirmation |
| **Raschke/Connors "Holy Grail"** | Hard gate (ADX>30 + pullback + stop) | ADX<30 disqualifies |
| **Larry Williams OOPS** | Single trigger + context filters | Explicitly "not standalone" |
| **AQR/Man AHL TSMOM** | **Continuous score** (vol-adjusted return) | **Inherently graded — magnitude = position size** |
| **ICT/SMC** | Sequential interpretive checklist | Subjective soft gate |

**The key structural insight:** most *classic discretionary* systems are HARD GATES (they handle partial alignment by simply not trading — which is exactly our current problem). The **graded/continuous pattern comes from the QUANT side** (TSMOM: continuous signal → position size). The **"veto + soft remainder" hybrid is real and documented in CANSLIM** (M = hard veto, rest = "as many as possible").

**Two strongest defensible precedents for our design:**
1. **CANSLIM's veto + soft-remainder** (hard veto on trend, weighted score on the rest)
2. **TSMOM's continuous-signal → position-size** mapping

---

## 5. The academic/quant grounding

The literature directly supports the graded model:

1. **The "unanimous AND-gate" is a known anti-pattern** — collapses frequency, causes analysis paralysis, especially harmful with redundant indicators.

2. **The dominant modern approach is a graded composite score.** Factor investing: z-score each signal, linearly weight. Ensemble/ML: "weighted majority vote" → graded output (Strong Buy/Weak Buy/Hold/Weak Sell/Strong Sell).

3. **Weighted > majority > unanimous, and "diverse" > "many."** The single most consistent finding: combine *one indicator per category* (trend + momentum + volatility), weight by reliability.

**Named, citable sources (with DOIs):**
- **Worasucheep (2021)** — most on-point: heterogeneous ensemble + *modified majority voting* beat buy-and-hold, individual classifiers, AND standard majority voting. DOI: 10.1080/08839514.2021.2001178
- **Sullivan, Timmermann & White (1999)** — the formal "too many rules = false discoveries" (data-snooping). DOI: 10.1111/0022-1082.00163
- **Brock/Lakonishok/LeBaron (1992)**, **Lo/Mamaysky/Wang (2000)**, **Park & Irwin (2007)** — foundational TA predictive-power work.

**The direct design implication:** our "too many stand-downs" is the *recall* side of the precision/recall tradeoff. The literature's answer is **not** "loosen AND to OR" but "replace the boolean gate with a graded score so partial alignment still produces a *lean* signal rather than a full stand-down."

**Honest caveat:** no single peer-reviewed study cleanly isolates "unanimous vs majority vs weighted" as a controlled A/B on the same indicator set — the conclusion is triangulated, not a single clean experiment.

---

## 6. What this means for Kabroda's tier design (recommendations, NOT decisions)

> [!IMPORTANT]
> These are recommendations for Andy to decide on. Nothing here is settled.

### The recommended structure (converged across all six agents):

1. **Trend = binary hard gate.** If trend is against you, STAND_DOWN regardless of everything else. This is the one thing every source — Krown's own precedence, Elder's Triple Screen, CANSLIM's "M" veto, the quant "hard gate for extreme zones" — agrees on.

2. **Volatility = second gate (regime).** "Is the market even tradeable right now?" (cf. ADX<20 = close the gate). Disagreement = STAND_DOWN or heavy downgrade.

3. **Momentum = confirmation (trigger quality).** Disagreement = downgrade to "lean"/"neutral", *not* void.

4. **Emit a graded conviction, not a boolean.** Either 3-tier (strong/lean/neutral) or 5-level (A+/A/B/C/F). Map conviction to position size (strong=full, lean=half, neutral=0/feeler).

5. **Separate "neutral" from "weak lean" explicitly.** Neutral = "no edge" (no valid thesis → stand down). Weak lean = "weak edge" (valid but fragile → small/probe size). This is the single most important fix — it's what our current system gets wrong.

### The open design questions (for Andy):

1. **3-tier vs 5-level?** 3-tier is simpler and matches "strong/lean/neutral" language; 5-level is the dominant systematic convention. Which do we want?

2. **Is volatility a hard gate or a graded contributor?** The sources split: some treat it as a second gate (regime), others as a graded layer. This depends on whether we think "volatility regime" is a binary tradeable/not-tradeable question or a continuous one.

3. **Do we want numeric weights, or just ordered precedence?** The library has NO numeric weights (only "trend > volatility > momentum"). The quant literature uses z-score + linear weights. Do we invent weights (and backtest them), or stay with ordered precedence + a simple count?

4. **How does conviction map to the existing position-sizing code?** The universal rule is "conviction scales risk %, stop sets share count." Does Kabroda's existing sizing already do this, or does it need the conviction layer wired in?

### What we should NOT do (per the anti-fabrication discipline):

- **Do NOT** treat the library's "Confluence Scorecard" (4/4, 3/4, 2/4) or "Decision Matrix" as Krown-sourced — they're the library's own synthesis, explicitly flagged 🔒.
- **Do NOT** invent numeric weights and present them as Krown's — the library has no weights, only ordered precedence.
- **Do NOT** rebuild KQAL — it's broken/incomplete, and it was the library's own unverified design anyway.
- **Do NOT** test a partial build and present it as meaningful (the exact mistake CC flagged in the 2026-08-27 log entry).

---

## 7. Source quality caveat (honest)

The practitioner material (confluence, hierarchy, conviction grading) is dominated by retail-education blogs and SEO content — **not peer-reviewed**. The *consistency* across many independent sources is high (the three-role hierarchy and "trend is the gate" principle are stated near-identically everywhere), which raises confidence, but none of it is academic.

The **book authors** (Elder, Minervini, O'Neil, Grimes, Brooks, Shannon, Villahermosa, Gawande) and the **academic papers** (Worasucheep, Sullivan et al., Brock et al.) are the most defensible citations.

The **quant material** (z-score composite, weighted voting, Kelly) is the closest to a formal treatment and is the most defensible basis for the graded-conviction layer.

---

## Appendix — the six agents and their scope

1. **Library confluence researcher** — checked `Trading Knowledge` first; found the sourced precedence + 4 templates + binary rule, and the library's own unverified synthesis (scorecard, decision matrix, KQAL).
2. **Confluence methodology researcher** — "confluence" is the named methodology; count vs weighted; named teachers (Fuller, Brooks, Grimes, Shannon, Villahermosa, Bratby, Gawande, ICT).
3. **Named systems researcher** — extracted actual checklists from Elder, Minervini, O'Neil, Turtle, Darvas, Raschke, Williams, AQR/Man AHL, ICT.
4. **Signal hierarchy researcher** — single disagreement doesn't void unless it's the gate; gate-vs-confirmation is well-documented; "trend is the gate" principle.
5. **Conviction grading researcher** — 3-tier vs 5-level; conviction→sizing mapping; "neutral vs weak lean" distinction.
6. **Quantitative signal fusion researcher** — academic/quant approaches; the "unanimous AND-gate" anti-pattern; weighted > majority > unanimous.
