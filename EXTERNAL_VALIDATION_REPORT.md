# External Validation Report — Krown Trading System Claims (CORRECTED)

**Date:** 2026-08-26 (revised)
**Purpose:** Validate every claim in the Phase 4 decision-layer design against (1) the library's own citations first, then (2) public internet sources — keeping **🔒 UNVERIFIED** (not publicly searchable) strictly separate from **❌ REFUTED** (actively contradicted).

---

## 0. The correction that changes everything

The first pass of this report treated "not found on the public web" as if it were evidence against a claim. That was wrong, and it produced several false "refuted" verdicts. The correct method is:

1. **Check the library's own citations first.** The library docs cite specific course modules (Quant Prime AI, Trading Bible, Ultimate TA Beginner) with direct transcript quotes. Those are paywalled — so a public web search legitimately returns nothing for them. That is a **🔒 UNVERIFIED** result, *not* evidence of fabrication.
2. **Reserve ❌ REFUTED** for cases where public information *actively contradicts* what the library says.

When I apply that method, the picture inverts: **the library already has traceable citations for almost every substantive value.** The genuinely un-cited items are a small set, and the library *already flags them* as 🔒 UNCONFIRMED.

---

## 1. Library citation audit — the key finding

This is the section the first pass skipped. For each claim, does the library cite a specific source, or does it cite nothing?

| Claim | Library citation? | Cited source (in library) | Public web? |
|---|---|---|---|
| 9/21/55 EMA "Krown Cross" (21 vs 55 = bias) | ✅ YES | `krown_cross_ma/README.md` + `TRADING_FRAMEWORK.md` | 💭 partial (9/21/55 is generic; 21/55 is the real signal) |
| BBWP Length 13 / lookback 252 / SMA-5 / StDev 2.0 | ✅ YES | `bbwp/README.md` — direct quote "Length 13, very specific there" (QPAI); 252 + SMA-5 corroborated by Trading Bible deep-dive | ✅ public |
| PMARP 20-VWMA / lookback 350 | ✅ YES | `pmarp/README.md` — QPAI + Trading Bible; 350 confirmed twice | ✅ public (as a config) |
| BBWP trigger zones ≤38 / ≥75 | ✅ YES | `TRADING_FRAMEWORK.md` L151-154 — Krown System capstone, direct quote | 🔒 not public |
| PMARP trigger zones ≥85 / ≤15 | ✅ YES | `TRADING_FRAMEWORK.md` L151-154 + `pmarp/README.md` L72 — direct quote | 🔒 not public |
| RSI divergence lookback 28 | ✅ YES | `rsi_divergence/README.md` L52 — direct quote "the divergence lookback bars... were at 28" | 🔒 not public |
| RSI 14 / Wilder's smoothing | ✅ YES | `rsi_divergence/README.md` L48-50 — direct quote "I only exclusively use Wilder's version" | ✅ public |
| Four divergence types | ✅ YES | `rsi_divergence/README.md` L38-43 | ✅ public |
| RSI zones (70/30 vs 80/20 vs 40-80/20-60) | ✅ YES | `rsi_divergence/README.md` L53-54, L95-110, L133-153 — three parallel course systems | ✅ public |
| "4 templates" (uptrend long / counter-trend short / etc.) | ✅ YES | `TRADING_FRAMEWORK.md` L142-159 — Krown System capstone, full table, direct quote | 🔒 not public |
| "Golden Rule" (trend vs volatility disagree → stand down) | ⚠️ SYNTHESIS | `TRADING_FRAMEWORK.md` L186 — explicitly "this library's own restatement... not a single direct quote" | 🔒 not public |
| Phantom Divergence | ⚠️ GAP | `rsi_divergence/README.md` L92-93 — explicitly 🔒 GAP, "does not give the exact geometric/algorithmic rule" | 🔒 not public |
| PMARP name = "Ratio" (not "Range") | ✅ YES | `pmarp/README.md` L1 — title is "Price Moving Average **Ratio** Percentile" | ✅ public |

**Genuinely un-cited (no library citation — and the library already flags these 🔒):**

| Claim | Library status |
|---|---|
| PMARP "Alternative MA 200" | 🔒 UNCONFIRMED (`pmarp/README.md` L68) |
| RSI "Pivot Lookback Order 3" | 🔒 UNCONFIRMED (`rsi_divergence/README.md` L55) |
| BBWP fine 5/15/85/95 zone table | 🔒 UNCONFIRMED (`bbwp/README.md` L73) |
| PMARP 95/5 sub-tier | 🔒 UNCONFIRMED (`pmarp/README.md` L76) |
| "PMARP works best on 4H/Daily" | 🔒 UNCONFIRMED (`pmarp/README.md` L121) |

**Conclusion:** The library is already doing the right thing. It has direct transcript citations for the real values, and it already flags the un-cited values as 🔒. The external validation's job was to confirm the *indicator math* is real and public — which it did — not to "catch" the library in fabrication, which it largely did not.

---

## 2. What was genuinely REFUTED (actively contradicted) — a short list

Only **one** claim was actively contradicted by public sources, and it was **not a library error**:

| Claim | Verdict | Detail |
|---|---|---|
| "PMARP = Price Moving Average *Range* Percentile" | ❌ REFUTED (but not a library error) | The correct expansion is **"Ratio"** (PMAR = Price / Moving Average). The library's own title already says "Ratio" (`pmarp/README.md` L1). The "Range" wording was a **drift introduced in the architect agent's summary**, not in the library. |

That is the *only* genuine ❌ REFUTED item. Everything else the first pass labeled "refuted" was actually a 🔒 UNVERIFIED (paywalled) or a mislabel by the external agent itself.

---

## 3. The "4 templates" — the external agent was WRONG

The first pass reported "4 templates ❌ REFUTED." That was a **false refutation**. The library has the four templates **directly quoted** from the Krown System capstone (`TRADING_FRAMEWORK.md` L142-159), with a full table:

| Template | Trend filter | Volatility trigger | Entry | Take-profit |
|---|---|---|---|---|
| 1. Uptrend Long | 21 EMA > 55 EMA | BBWP ≤ 38th %ile | Price ≤ 55 EMA or 0.618/0.786 Fib, stochastic crosses up | BBWP ≥ 75% then below MA, or PMARP ≥ 85% |
| 2. Counter-Trend Short | 9 > 21 > 55 EMA | BBWP ≥ 75%, falling below MA w/ negative slope | Stochastic crosses down, PMARP ≥ 85% | PMARP ≤ 20%, or 9 EMA crosses below 21 |
| 3. Downtrend Short | 21 EMA < 55 EMA | BBWP ≤ 38th %ile | Price ≥ 55 EMA or 0.618/0.786 Fib, stochastic crosses down | BBWP ≥ 75% then below MA, or PMARP ≤ 20% |
| 4. Downtrend Long (counter-trend) | 9 < 21 < 55 EMA | BBWP ≥ 75%, below MA, negative slope, PMARP ≤ 15% | Stochastic crosses up | 55 EMA test, PMARP ≥ 80%, or BBWP ≥ 75% |

The external agent searched the public web, found no "4 templates," and concluded "refuted." But the library's citation is to a **paywalled course module** (`05_the_krown_system.md`), which is exactly the kind of content that legitimately returns nothing on a public search. The correct verdict is **✅ cited (paywalled), not ❌ refuted.**

The external agent's *partial* finding was still useful: Krown's public-facing material emphasizes a **4-pillar framework** (Trend/Volatility/Structure/Momentum) and markets *against* static copy-paste templates. That's a real nuance — but it does not contradict the library's direct quote of the 4 templates from the capstone. Both are true: the framework is the public-facing concept, and the 4 templates are the concrete checklists inside the paid course.

---

## 4. The "Golden Rule" — already correctly labeled by the library

The first pass reported the "Golden Rule" as 🔒 UNVERIFIED. The library **already labels it correctly**: `TRADING_FRAMEWORK.md` L186 says it is "this library's own restatement of the source's repeated emphasis... not a single direct quote." So the library never claimed it was a verbatim Krown rule. The external "unverified" is consistent with the library's own honest labeling — it's a synthesis, not a fabrication.

---

## 5. Phantom Divergence — already correctly flagged as a gap

The first pass reported "Phantom Divergence" as 💭 PARTIAL / un-implementable. The library **already flags it as a 🔒 GAP** (`rsi_divergence/README.md` L92-93): the transcript describes what it looks like and confirms it drove the Strategy #1 backtest data, but "does not give the exact geometric/algorithmic rule." This is a genuine gap — correctly identified by both the library and the external agent. It is **not** a case of the library fabricating a rule; the library explicitly says the rule is not stated.

---

## 6. Indicator-definition split (the two separate questions)

The indicator-definition researcher should have split two questions. Here they are, answered separately:

**(a) Is BBWP/PMARP a real indicator with a public, documented formula (independent of Krown)?**

| Indicator | Verdict | Detail |
|---|---|---|
| BBWP | ✅ CONFIRMED | Real, public. BBW = (Upper−Lower)/Middle; BBWP = percentile rank over lookback. Attributed to "The_Caretaker." Not proprietary. |
| PMARP | ✅ CONFIRMED (concept) / 🔒 (exact params) | Real concept (PMAR = Price/MA, percentile-ranked). But the *specific* "Caretaker PMARP" is a paid MT5 product (Konstantin Meshcheriakov) + part of Krown's "CT Indicator Bundle." The *concept* is public; the *exact Krown parameters* are not. |

**(b) Are Krown's specific threshold recommendations (38/75, 85/15, etc.) publicly documented?**

| Threshold | Verdict | Detail |
|---|---|---|
| BBWP ≤38 / ≥75 | 🔒 NOT public | Has a library citation (Krown System capstone), but not on the public web. |
| PMARP ≥85 / ≤15 | 🔒 NOT public | Has a library citation (Trading Bible PMARP lecture, direct quote), but not public. |
| RSI 28 lookback | 🔒 NOT public | Has a library citation (QPAI RSI lecture, direct quote), but not public. |

**This is the normal, non-contradictory outcome you flagged:** (a) comes back ✅ CONFIRMED while (b) stays 🔒 UNVERIFIED. That's Krown's teaching choice layered on top of a public tool — **not a red flag on its own.**

---

## 7. Consolidated corrected verdict table

| Component | Tool | Library citation? | Public status | Corrected verdict |
|---|---|---|---|---|
| TREND | 21-vs-55 EMA cross | ✅ | 💭 partial | ✅ cited; 21/55 is the real signal (9 is secondary) |
| VOLATILITY | BBWP 13/252/SMA-5/StDev-2 | ✅ | ✅ public | ✅ cited + public |
| VOLATILITY | PMARP 20-VWMA/350 | ✅ | ✅ public (config) | ✅ cited + public |
| VOLATILITY | BBWP ≤38/≥75 trigger | ✅ | 🔒 not public | ✅ cited (paywalled) |
| VOLATILITY | PMARP ≥85/≤15 trigger | ✅ | 🔒 not public | ✅ cited (paywalled) |
| STRUCTURE | Kabroda gravity engine + locked-in core | n/a (Kabroda IP) | n/a | ✅ Kabroda's own, inviolable |
| MOMENTUM | RSI 14/Wilder's | ✅ | ✅ public | ✅ cited + public |
| MOMENTUM | Four divergence types | ✅ | ✅ public | ✅ cited + public |
| MOMENTUM | RSI 28 lookback | ✅ | 🔒 not public | ✅ cited (paywalled) |
| MOMENTUM | Phantom Divergence | ⚠️ GAP | 🔒 not public | ⚠️ genuine gap (no rule stated anywhere) |
| — | "4 templates" | ✅ | 🔒 not public | ✅ cited (paywalled) — NOT refuted |
| — | "Golden Rule" | ⚠️ synthesis | 🔒 not public | ⚠️ library already labels as synthesis |
| — | PMARP "Ratio" vs "Range" | ✅ (says "Ratio") | ✅ public | ✅ library correct; "Range" was architect drift |

---

## 8. What this means for the build

**Safe to build now (library-cited AND/OR publicly verified):**
- 21-vs-55 EMA trend bias
- BBWP 13/252/SMA-5/StDev-2 (indicator math)
- PMARP 20-VWMA/350 (indicator math)
- RSI 14/Wilder's
- Four divergence types
- The 4-pillar framework (Trend/Volatility/Structure/Momentum)
- The 4 templates (library-cited, paywalled — you have access to verify)

**Genuinely un-cited (library already flags 🔒 — do NOT silently use):**
- PMARP "Alternative MA 200"
- RSI "Pivot Lookback Order 3"
- BBWP fine 5/15/85/95 zone table
- PMARP 95/5 sub-tier
- "PMARP works best on 4H/Daily"

**Genuine gaps (no rule exists anywhere):**
- Phantom Divergence detection algorithm — the library explicitly says the rule is not stated. Un-implementable without inventing a rule.

**Genuinely refuted (actively contradicted):**
- Only one: "PMARP = Range" → correct is "Ratio." And this was architect drift, not a library error.

---

## 9. Source domains (canonical)

- Krown identity: teachable.com (krown-trading.teachable.com), quantalchemy.io, steemit.com, reddit.com, youtube.com
- BBWP: pineify.app, stockcharts.com, trendspider.com, forex-station.com, quantalchemy.io, mql5.com
- PMARP: mql5.com (Konstantin Meshcheriakov), github.com, quantalchemy.io
- RSI/divergence: fxopen.com, kraken.com, babypips.com, trendspider.com, macroption.com, wikipedia.org, fortraders.com, oanda.com
- Library citations (paywalled, you have access): `courses/quant_prime_ai/library/`, `courses/the_trading_bible/library/`, `courses/ultimate_trading_ta_beginner/library/`

> [!IMPORTANT]
> The search tool returned AI-summarized snippets with Google grounding-redirect URLs, not clean canonical deep-links. The domains above are real; specific article pages may need targeted re-searches. Where a value has a **library citation** but is not on the public web, it is tagged **🔒 not public** — this is *not* a fabrication flag; it is the expected result of Krown teaching through a paywalled course.
