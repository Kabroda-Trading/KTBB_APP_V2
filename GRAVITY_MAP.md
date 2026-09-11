# The Gravity Map — how it actually works

Written for cross-agent handoff (Claude Code → DeepSeek/Antigravity), 2026-09-11.
Everything below is read directly from the live code (`gravity_engine.py`,
`gravity_math.py`, `kabroda_macro_engine.py`, `database.py`, `main.py`,
`templates/gravity_map.html`, `templates/macro_war_room.html`) — not a
paraphrase of an old spec. Line/file references let you verify anything here
yourself.

## Read this first: it is NOT a trading input

As of 2026-08-30 the Gravity Map is a **standalone reference/visualization
system**. `decision_engine.py`'s calibrated gate — the thing that actually
decides TAKE/PASS and everything the Brain repo's backtests measure — does
not read from it at all. Andy's explicit call: gravity has real value as
something to *look at*, but it doesn't get to influence the trade call.

This matters for you specifically: nothing in the touch-fill / FULL_T1 /
fuel-floor work from the last two days touches this system, and it shouldn't.
If you ever see "kinetic friction," a macro beam, or a KDE peak show up near
a trade decision, that's a coincidence in the data, not a wired dependency —
go find the actual gate logic instead.

## What it is, in one paragraph

The Gravity Map turns a pile of historical price pivots — from multi-year
Elliott Wave structure down to 1-hour swing points — into a single continuous
"density wave" using Kernel Density Estimation (KDE), the same idea Bookmap
uses for order-flow heatmaps but applied to structural price memory instead
of live order book depth. Every remembered pivot is a price with a weight;
heavier/more-permanent pivots emit a wider, taller bell curve of "pull";
overlapping pivots compound. Where the curve peaks, price has historically
had to work hardest to pass — that's the whole idea.

## The six kinds of levels (all stored in one table: `gravity_memory`)

Every level is a row in `GravityMemory` (`database.py:665`): `symbol`,
`timestamp`, `source`, `level_type`, `price`, `permanence_class` (0/1/2),
`heat_multiplier`, `active`. `calculate_gravity_kde()` only ever reads rows
where `active == True`. Six `source` values populate it, each with different
permanence, weight, and refresh behavior:

| source | writer | permanence_class | own heat_multiplier | refresh |
|---|---|---|---|---|
| `MACRO_ENGINE_CLASS_0` | `kabroda_macro_engine.py` | 0 | 15.0 | full rewrite every 24h (boot + every 96th gravity loop) |
| `4H_PIVOT` / `DAILY_PIVOT` | `gravity_engine.py::_scan_for_pivots` | 1 | 1.0 (2.0 if pivot volume > 2× its trailing-20-bar average) | new row per fractal found; invalidated on close-through |
| `1H_PIVOT` | same, timeframe="1h" | 2 | 1.0 or 2.0 (same volume-spike rule) | same |
| `7_DAY_KABRODA` | `gravity_engine.py::log_kabroda_bedrock` | 2 | 1.0 | once per session lock, all 6 rows replaced together |
| `1W_MACRO_ANCHOR` | `gravity_engine.py::log_radar_anchors` | 1 | 5.0 | once per week, old row deactivated first |
| `168H_MICRO_ANCHOR` | same | 2 | 3.0 | every gravity loop tick (~15 min), old row deactivated first |

A seventh row type, `WEEKLY_200_SMA`, is written by the macro engine but
stored with `active=False` on purpose — it's a reference value read elsewhere
(macro trend bias), and is deliberately invisible to the KDE calculation.

### 1. Macro Beams — Class 0, the Elliott Wave layer

`kabroda_macro_engine.py` runs as its own subprocess (not blocking the main
app) on boot and every 24 hours. For each of BTC/ETH/SOL:

1. Pulls up to 1,500 days of daily SPOT candles from MEXC.
2. Runs a ZigZag pivot filter at a 20% deviation threshold (`_calculate_zigzag_pivots`)
   — this is a large threshold on purpose; it's meant to strip everything but
   genuine multi-month/multi-year swings, not intraday noise.
3. Finds the absolute cycle origin (lowest low before the highest high) and
   cycle top, then tries to map a 5-wave bull structure and whatever bear
   structure follows, enforcing two Elliott Wave rules as hard gates
   (`_find_macro_anchors`, `kabroda_macro_engine.py:79`):
   - Wave 4 may not overlap Wave 1's price territory.
   - Wave 2 may not break below the cycle origin.
   If either rule fails, that wave count is simply not written — there's no
   partial/best-guess fallback.
4. Writes whatever survives as `level_type` in `{CYCLE_ORIGIN, CYCLE_TOP,
   BULL_WAVE_1..4, BEAR_WAVE_1_MSB, BEAR_WAVE_2, BEAR_WAVE_3_LOW,
   BEAR_WAVE_4_BOUNCE}`, all `permanence_class=0`, `heat_multiplier=15.0`.

These are the "macro beams" surfaced separately in the API response
(`kde_data.macro_beams`) precisely because they're few, structurally
meaningful, and worth listing by name rather than just feeding the curve.

### 2. Kabroda Bedrock — Class 1/2, the intraday/session layer

`gravity_engine.py`'s background loop runs every 15 minutes for the same
three symbols and does three things:

- **Swing pivot scanning** (`_scan_for_pivots`, 3-bar left/right fractal) on
  4H, 1H, and daily candles → `4H_PIVOT`/`DAILY_PIVOT` (class 1) and
  `1H_PIVOT` (class 2). A pivot's volume vs. its trailing 20-bar average sets
  its own `heat_multiplier` to 1.0 or 2.0 (a mild "this move had size" signal,
  independent of the class-based KDE bonus below).
- **Zone touch/invalidation tracking** (`_update_zone_touches`) — every loop
  tick, checks whether price has since closed through a pivot by more than
  0.1% (invalidated, `active=False`) or merely approached within 0.3% (
  `touch_count` increments, stays active). This is why old, broken pivots
  don't linger in the density calc forever.
- **Session-lock mirroring** (`log_kabroda_bedrock`) — at every session lock,
  writes the exact same 6 numbers the calibrated gate itself just computed
  (`breakout_trigger`, `breakdown_trigger`, daily S/R, 30m range high/low) into
  `gravity_memory` as `source="7_DAY_KABRODA"`, class 2. This is a one-way
  mirror for visualization — the gate doesn't read this back.

Two more rolling reference points get written on their own cadence:
`1W_MACRO_ANCHOR` (last Sunday's daily close, class 1, weight 5.0, rewritten
weekly) and `168H_MICRO_ANCHOR` (the close exactly 168 hourly candles back —
7 days — class 2, weight 3.0, rewritten every 15-min tick). Both explicitly
deactivate their own previous row before writing the new one — see the
history note below on why that matters.

## The math: how a pile of prices becomes a wave

`gravity_math.py::calculate_gravity_kde()` (called fresh on every API hit,
nothing pre-computed/cached):

1. Pull every `active=True` row for the symbol.
2. Set the scan range to (min price × 0.98) → (max price × 1.02) — 2% padding
   so wave tails aren't clipped at the edges.
3. `sigma` (the width of each pivot's bell curve) = 15 basis points of the
   midpoint of that range — e.g. roughly a $110 radius on a $75,000 BTC
   range. This is one global bandwidth, not per-level.
4. Sample the range at 400 points. At each sample price, sum a Gaussian
   contribution from every active level:
   `pull = exp(-0.5 * ((price - level_price) / sigma)^2)`, multiplied by that
   level's **total weight**:
   - own `heat_multiplier` (1.0, 2.0, 3.0, 5.0, or 15.0 depending on source, see table above), **plus**
   - `+15.0` more if `permanence_class == 0` (so a macro beam's real total
     weight is 15.0 + 15.0 = **30.0** — this is the "kinetic friction
     multiplier," stacked on top of its own already-high base weight, not a
     replacement for it),
   - or `+3.0` more if `permanence_class == 1` (4H/daily pivots end up at
     1.0–2.0 + 3.0 = 4.0–5.0; the weekly anchor ends up at 5.0 + 3.0 = 8.0),
   - or `+1.5` more if `source == "7_DAY_KABRODA"` specifically (session
     levels end up at 1.0 + 1.5 = 2.5).
   - 1H pivots and the 168H micro anchor get none of these bonuses — they're
     the quietest layer, weight 1.0–3.0 flat.
5. That summed-density-per-price series is the `curve` in the API response —
   the continuous wave.
6. **Peaks**: walk the curve for local maxima where density exceeds 15% of
   the global max density. Bucket by intensity: `LIGHT` (>15%), `HEAVY`
   (≥40%), `MAXIMUM` (≥80%). Sort by heat descending. These are the discrete
   "structure" markers layered on top of the continuous wave.

Separately, `calculate_macro_fibs()` is a completely independent, pure-math
function: takes the last 30 daily candles, finds their swing high/low, and
returns three downward retracements from the high only (0.5/0.618/0.786 —
there is no corresponding "up from the low" retracement leg computed) plus
three upside extensions beyond the high and three downside extensions below
the low (0.272/0.618/1.0× the swing range each way), for blue-sky
breakout/price-discovery targeting. It shares an API response with the KDE
data but doesn't share any inputs with it.

## Where it's exposed and what it actually looks like

One endpoint serves both consumers below: `GET /api/gravity/scan?symbol=...`
(`main.py:892`, unauthenticated — the CLAUDE.md rule against putting
sensitive/user-specific data on this route is why it stays this simple).
Returns `{kde_data: {curve, peaks, max_density, macro_beams}, macro_fibs:
{swing_high/low, 3 retracements, 6 extensions, chart_data}}`.

**`templates/gravity_map.html` — the actual Gravity Map page.** A daily
candlestick chart (lightweight-charts) fills most of the screen. A `<canvas>`
overlay draws the KDE `curve` as a sideways, Bookmap-style density wave
bleeding off the right price axis — wider = denser, colored on a
blue→purple→red gradient as intensity rises toward the axis. On top of the
candles, horizontal price lines are drawn for every macro beam (red = cycle
origin/top, green = bull wave, orange = bear wave) and every KDE peak (blue
dotted = LIGHT, yellow dotted = HEAVY, red dotted = MAXIMUM). A right sidebar
holds collapsible sections — Cycle Boundaries / Bull Run Structure / Bear
Trend Structure (from `macro_beams`) and Micro Gravity Density (from
`peaks`) — plus a separate "Wave Context" panel that actually comes from a
different endpoint (`/api/narrative/latest`, the wave-tracking journal, not
`gravity_math.py`) showing the currently-tracked wave's label, % complete,
origin→target, and invalidation price. A red "KINETIC FRICTION" banner lights
up when live price sits within 1.5% of any Class 0 macro beam. An export
button copies a formatted price list (macro/max/heavy/std, by intensity) to
the clipboard for pasting into TradingView as manual levels.

**`templates/macro_war_room.html` — a second, much simpler consumer of the
same endpoint.** It only looks at `macro_beams` (not the curve or peaks at
all) to drive a handful of executive KPI cards: nearest macro beam above
price ("ceiling," or "BLUE SKY" if none) and below ("floor," or "PRICE
DISCOVERY"), the % distance to the nearer one (flags "KINETIC FRICTION
WARNING" under 3%), a bullish/bearish read based on whether price sits above
or below the ceiling/floor midpoint, and a cycle-structure label built off
which wave types are present. Worth knowing: this page never touches the
1H/4H/daily micro pivots or the KDE curve itself — from its perspective, only
the ~10-15 Class 0 macro beams exist.

## Two historical bugs, already fixed, worth knowing about if you see old numbers

Both `log_kabroda_bedrock` and `log_radar_anchors` used to insert a fresh row
every cycle without ever deactivating the previous one for the same
`level_type`. Confirmed in real production data (2026-07-06): 13
simultaneously-active `168H_MICRO_ANCHOR` rows clustered in one ~$160 band
summed into a duplicate-inflated density peak that stood as a false
"MAXIMUM" wall for nearly a month while price moved tens of thousands of
dollars. Fixed by deactivating the prior row before writing the new one —
that's why you see the explicit "set active=False first" step in three of
the six writers above. If you ever pull raw historical `gravity_memory` data
from before 2026-07-06, treat multi-row duplicate clusters as this bug, not
as real structure.
