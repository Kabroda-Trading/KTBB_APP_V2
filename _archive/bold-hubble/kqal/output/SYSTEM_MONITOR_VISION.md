# Kabroda System Monitor — Unified Vision

## The Core Problem

We have all the pieces scattered across the codebase:
- Trades are tracked in `performance_auditor.py`
- System health is checked in `kqal/system_auditor.py`
- Krown alignment is scored in `kqal/alignment_engine.py`
- Trade validation is in `kqal/trade_validator.py`
- Timeframe analysis is in `kqal/timeframe_analyzer.py`
- Improvement suggestions are in `kqal/improvement_queue.py`
- Krown signal mapping is in `kqal/krown_signals.py`
- CrewAI agents exist in `agent_core.py`, `publisher_crew.py`, etc.
- A dashboard exists in `kqal/dashboard.py`

But there's no **single unified view** that answers:
1. **What did the system do?** — Trades fired, signals generated
2. **Why did it do that?** — Which indicators triggered? Which timeframes aligned?
3. **Was it right?** — Did the trade work out? Was the reasoning sound?
4. **Is the system healthy?** — Are all components running? Are parameters correct?
5. **Are we aligned with Krown?** — Does our output match Krown's methodology?
6. **What should we change?** — What parameters need tweaking for current market conditions?

---

## The Vision: One Dashboard, Daily Audit, Continuous Improvement

### Layer 1: The Unified Dashboard

A single web page (or set of pages) that shows:

**Top Section — System Health**
- All components green/yellow/red status
- Last audit timestamp
- Database connection status
- API health (yfinance, etc.)

**Middle Section — Trade Activity**
- Today's trades: what fired, why, result
- Win rate by timeframe (15m, 1h, 4h, daily, weekly)
- Win rate by strategy (S1-S5)
- Win rate by market regime (uptrend, downtrend, range)

**Bottom Section — Krown Alignment**
- BBWP alignment score
- PMARP alignment score
- Revin Ribbons alignment score
- RMO alignment score
- Overall Krown fidelity score
- Improvement queue items

### Layer 2: The Daily Audit (Automated)

A scheduled task that runs every 24h and:

1. **Pulls yesterday's trades** from the database
2. **Re-runs the indicators** on yesterday's data to verify they fired correctly
3. **Compares expected vs actual** — did the system behave as designed?
4. **Checks all component health** — any errors? Any missing data?
5. **Generates a report** — plain-English summary of findings
6. **Updates the improvement queue** — new items based on findings

### Layer 3: The "Why Did This Trade Fire?" Trace

For every trade, a trace that shows:
- Which strategy fired (S1-S5)
- Which indicators triggered (BBWP, PMARP, RSI, Revin Ribbons, RMO, RWP)
- What each indicator said at the moment of entry
- Which timeframes were aligned
- The exact bar data at entry
- The expected outcome vs actual outcome

### Layer 4: Market Regime Adaptation

The system should know:
- Are we in a trending or ranging market?
- Should we be more aggressive or conservative?
- Which strategies work best in current conditions?
- Are our parameters (BBWP thresholds, PMARP thresholds) right for now?

---

## What Already Exists

| Component | File | Status |
|-----------|------|--------|
| Trade performance tracking | `performance_auditor.py` | ✅ Built |
| System health audit | `kqal/system_auditor.py` | ✅ Built |
| Krown alignment scoring | `kqal/alignment_engine.py` | ✅ Built |
| Trade validation | `kqal/trade_validator.py` | ✅ Built |
| Timeframe win rate analysis | `kqal/timeframe_analyzer.py` | ✅ Built |
| Improvement queue | `kqal/improvement_queue.py` | ✅ Built |
| Krown signal mapping | `kqal/krown_signals.py` | ✅ Built |
| Web dashboard | `kqal/dashboard.py` | ✅ Built |
| Revin Suite indicators | `indicators/revin_ribbons.py` etc. | ✅ Built |
| CrewAI agents | `agent_core.py`, `publisher_crew.py` | ✅ Built |
| Database models | `database.py` | ✅ Built |
| **Unified view** | — | ❌ Missing |
| **Daily automated audit** | — | ❌ Missing |
| **Trade reason trace** | — | ❌ Missing |
| **Market regime adaptation** | — | ❌ Missing |

---

## What We Need to Build

### 1. Unified Dashboard (Upgrade `kqal/dashboard.py`)

Pull data from ALL existing KQAL modules into one view:
- System health from `system_auditor.py`
- Trade stats from `performance_auditor.py` (via `db_reader.py`)
- Krown alignment from `alignment_engine.py`
- Improvement queue from `improvement_queue.py`
- Timeframe analysis from `timeframe_analyzer.py`
- Krown signal status from `krown_signals.py`

### 2. Daily Audit Scheduler

A lightweight scheduler that:
- Runs `system_auditor.py` → checks all components
- Runs `timeframe_analyzer.py` → checks all timeframes
- Runs `alignment_engine.py` → checks Krown alignment
- Runs `trade_validator.py` → validates recent trades
- Aggregates results into a single report
- Posts report to the dashboard
- Sends notification if critical issues found

### 3. Trade Reason Tracer

For each trade in the database, trace back to:
- Which strategy evaluation produced it
- What the indicator values were at that moment
- Which timeframes were aligned
- What the JEWEL signal said
- Store this as a structured trace in the database

### 4. Market Regime Classifier

A module that:
- Classifies current market as TRENDING / RANGING / VOLATILE
- Tracks which strategies perform best in each regime
- Suggests parameter adjustments per regime
- Updates the improvement queue with regime-specific findings

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Unified Dashboard                   │
│              (kqal/dashboard.py + HTML)               │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│ System   │ Trade    │ Krown    │ Timeframe│ Improve- │
│ Health   │ Activity │ Alignment│ Analysis │ ment     │
│          │          │          │          │ Queue    │
├──────────┴──────────┴──────────┴──────────┴──────────┤
│              Daily Audit Scheduler                     │
│   (runs system_auditor + timeframe_analyzer + etc.)    │
├───────────────────────────────────────────────────────┤
│              Database (PostgreSQL on Render)           │
│   trades | signals | audits | traces | parameters     │
├───────────────────────────────────────────────────────┤
│              Live Pipeline                             │
│   gravity_engine → mtf_confluence_scanner → krown_sys │
└───────────────────────────────────────────────────────┘
```

---

## Next Steps

1. **Upgrade the dashboard** to show all KQAL modules in one view
2. **Build the daily audit scheduler** — cron-style, runs all audits
3. **Build the trade reason tracer** — capture indicator state at trade time
4. **Build the market regime classifier** — adapt parameters to conditions
5. **Wire it all together** — one URL to see everything
