# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, text, UniqueConstraint, Index
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")

# Render sets postgresql:// — SQLAlchemy needs postgresql+psycopg:// for psycopg3
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)

    # --- ONE-TIME DATA FIX (idempotent -- matches nothing once corrected) ---
    # 2026-09-05: executor_accounts.margin_mode's default was "ISOLATED"
    # for a short window before being corrected to "ISOLATION" (Bitunix's
    # actual API vocabulary). Andy's real account row was created during
    # that window and needs its stored value corrected, not just new rows
    # going forward -- see the column's own comment for why the string
    # must match exactly.
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE executor_accounts SET margin_mode = 'ISOLATION' WHERE margin_mode = 'ISOLATED'"))
    except Exception:
        pass

    # --- MIGRATION PATCHES (POSTGRESQL SAFE) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN activated_at TIMESTAMP"))
    except Exception:
        pass 

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN closed_at TIMESTAMP"))
    except Exception:
        pass 
        
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN diagnostic_data TEXT"))
    except Exception:
        pass 

    # --- MAS UPGRADE MIGRATIONS ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN mas_executive_brief TEXT"))
    except Exception:
        pass 

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN mas_approval_status VARCHAR DEFAULT 'PENDING'"))
    except Exception:
        pass 

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN formatted_newsletter TEXT"))
    except Exception:
        pass

    # --- PHASE 4 LOCK-IN (2026-08-27) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN conviction VARCHAR"))
    except Exception:
        pass

    # --- CALIBRATED GATE REBUILD (2026-08-30) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN tier VARCHAR"))
    except Exception:
        pass

    # --- DECISION JOURNAL OUTCOME MIGRATIONS (filled later by the 4H auditor task) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN outcome_price_4h FLOAT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN outcome_pct_move_4h FLOAT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN outcome_direction_correct BOOLEAN"))
    except Exception:
        pass

    # --- PHASE 3B SPECIALIST AUDIT TRAIL ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE macro_narrative_log ADD COLUMN wave_status TEXT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE macro_narrative_log ADD COLUMN wave_reasoning TEXT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE macro_narrative_log ADD COLUMN confirmation_condition TEXT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE system_audit_log ADD COLUMN ran_successfully BOOLEAN DEFAULT TRUE"))
    except Exception:
        pass

    # --- FIX 1 — Outcome tracker backfill ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN target_hit VARCHAR"))
    except Exception:
        pass

    # --- TRADE STRUCTURE ANALYST audit trail ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN structure_reasoning TEXT"))
    except Exception:
        pass

    # --- FIX 2 — kinematic_grade on decision_journal ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN kinematic_grade VARCHAR"))
    except Exception:
        pass

    # --- W-9 TRADE-LIFECYCLE MONITOR SCHEMA ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN entry_filled_at TIMESTAMP"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN session_expires_at TIMESTAMP"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN max_target_reached VARCHAR"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN t2_reached BOOLEAN DEFAULT FALSE"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN t3_reached BOOLEAN DEFAULT FALSE"))
    except Exception:
        pass

    # --- CANONICAL RECORD SEPARATION ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN is_canonical BOOLEAN DEFAULT FALSE"))
    except Exception:
        pass

    # --- JOB 2 / PHASE A — DecisionJournal ↔ InterpreterLog join key ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN session_id VARCHAR"))
    except Exception:
        pass

    # --- W-11 — DecisionJournal source column + historical backfill ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE decision_journal ADD COLUMN source VARCHAR"))
    except Exception:
        pass

    # Backfill source for pre-W-11 rows (idempotent — WHERE source IS NULL).
    # Production snapshot 2026-06-13: 30 MAS rows (MAS_APPROVED / MAS_REJECTED),
    # 393 radar rows (STAND_DOWN / GRADE_A / GRADE_B), 0 NULLs in decision_type.
    # Without this backfill, switching the auditor filter to source == "mas_flow"
    # would orphan all 423 historical rows (NULL source after ALTER TABLE).
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE decision_journal SET source = 'mas_flow' "
                "WHERE source IS NULL "
                "AND decision_type IN ('MAS_APPROVED', 'MAS_REJECTED')"
            ))
            conn.execute(text(
                "UPDATE decision_journal SET source = 'market_radar' "
                "WHERE source IS NULL "
                "AND decision_type IN ('GRADE_A', 'GRADE_B', 'STAND_DOWN')"
            ))
    except Exception:
        pass

    # --- INTRADAY MONITOR — micro_state at lock time (backfills condition re-derivation) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE session_audit_log ADD COLUMN micro_state_lock VARCHAR"))
    except Exception:
        pass

    # --- MTF STRUCTURAL SNAPSHOT PHASE 1 — new capture columns ---
    for _col in [
        "daily_21ema_direction VARCHAR",
        "daily_21ema_position VARCHAR",
        "daily_21ema_distance_pct FLOAT",
        "tf4h_200sma_position VARCHAR",
        "tf4h_200sma_distance_pct FLOAT",
        "tf1h_200sma_position VARCHAR",
        "tf1h_200sma_distance_pct FLOAT",
        "weekly_200sma_position VARCHAR",
        "weekly_200sma_distance_pct FLOAT",
        "weekly_200sma_test_count INTEGER",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE session_audit_log ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- CAMPAIGN LOGS — session_timeframe (4H/1H system support) ---
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN session_timeframe VARCHAR DEFAULT '15M'"))
    except Exception:
        pass

    # --- TARGET LOGIC v2 — audit fields on campaign_logs ---
    for _col in [
        "target_logic_version VARCHAR DEFAULT 'v1'",
        "target_too_small_flag BOOLEAN DEFAULT FALSE",
        "htf_anchor_type VARCHAR",
        "htf_anchor_price FLOAT",
        "energy_grade VARCHAR",
        "kinematic_grade VARCHAR",
        "macro_bias VARCHAR",
        "weekly_200sma_position VARCHAR",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- RUNNER MECHANIC (SHADOW MODE) — 15M-only, record-only T1-runner tracking ---
    for _col in [
        "shadow_runner_active BOOLEAN DEFAULT FALSE",
        "shadow_runner_stop FLOAT",
        "shadow_runner_ema21 FLOAT",
        "shadow_runner_bucket_ts TIMESTAMP",
        "shadow_runner_bucket_close FLOAT",
        "shadow_runner_last_scan_ts TIMESTAMP",
        "shadow_runner_closed_at TIMESTAMP",
        "shadow_runner_exit_reason VARCHAR",
        "shadow_runner_leg2_r FLOAT",
        "shadow_runner_blended_r FLOAT",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- RUNNER MECHANIC (LIVE) — 15M-only, the validated 30%-at-T1 /
    # fixed-runner-stop / 70%-to-T3 rule (KABRODA_REBUILD_SPEC.md SS6),
    # authoritative as of 2026-08-30 -- see the CampaignLog model comment.
    for _col in [
        "runner_active BOOLEAN DEFAULT FALSE",
        "runner_stop FLOAT",
        "runner_started_at TIMESTAMP",
        "t1_leg_r FLOAT",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- MTF CONFLUENCE CAPTURE — dominant_direction/confluence_score on campaign_logs ---
    for _col in [
        "dominant_direction VARCHAR",
        "confluence_score INTEGER",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- REVIN SUITE (R-SQUARED) — record-only Revin fields on campaign_logs ---
    # Added 2026-07-15 alongside Phase 1b gravity_engine.py wiring.
    # RECORD-ONLY — feeds audit_ai.py's Revin alignment hypothesis, does not
    # gate candidate creation. NULL on 15M rows.
    for _col in [
        "revin_ribbon_zone VARCHAR",
        "revin_midline_price FLOAT",
        "rmo_score FLOAT",
        "rmo_state VARCHAR",
        "rwp_squeeze BOOLEAN DEFAULT FALSE",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- TARGET LOGIC v3 — t2/t3 made nullable for single-target 4H/1H candidates ---
    # v3 rows write t2=None/t3=None by design (see database.py CampaignLog comment).
    # Column was still NOT NULL at the DB level, so every v3 4H/1H INSERT was failing
    # and rolling back silently (NotNullViolation) since the single-target deploy —
    # zero 4H/1H candidates recorded until this fix. v1/v2 rows are unaffected.
    for _col in ["t2", "t3"]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE campaign_logs ALTER COLUMN {_col} DROP NOT NULL"))
        except Exception:
            pass

    # --- GRAVITY MEMORY — zone strength fields ---
    for _col in [
        "departure_move_pct FLOAT",
        "touch_count INTEGER DEFAULT 0",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE gravity_memory ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- COMPONENT 0 EXTENSION — additional audit fields ---
    for _col in [
        "macro_structure_json TEXT",
        "tf1h_trend VARCHAR",
        "tf1h_rsi FLOAT",
        "tf1h_adx_strength VARCHAR",
        "tf4h_trend VARCHAR",
        "tf4h_rsi FLOAT",
        "tf4h_adx_strength VARCHAR",
        "tf4h_macd_hist FLOAT",
        "daily_200sma_position VARCHAR",
        "daily_200sma_distance_pct FLOAT",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE session_audit_log ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- CROWN SURGERY CUT 4 — BBWP/PMARP recording + RSI divergence placeholder ---
    for _col in [
        "bbwp_15m FLOAT",
        "bbwp_state VARCHAR",
        "pmarp_15m FLOAT",
        "pmarp_state VARCHAR",
        "rsi_divergence_type VARCHAR DEFAULT 'NONE'",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE session_audit_log ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- PHASE 3C JEWEL SPECIALIST — top-level scanner context columns ---
    for col_def in [
        "confluence_score INTEGER",
        "dominant_direction TEXT",
        "conviction TEXT",
        "any_tf_compressed BOOLEAN",
        "any_tf_overextended BOOLEAN",
        "any_tf_divergence BOOLEAN",
        "jewel_gate_open BOOLEAN",
        "jewel_conviction TEXT",
        "jewel_exit_warning BOOLEAN",
        "jewel_divergence_warning BOOLEAN",
        "jewel_signal_summary TEXT",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE jewel_snapshot_log ADD COLUMN {col_def}"))
        except Exception:
            pass

    # --- USERS TABLE MIGRATIONS (first_name/last_name added in M2 build) ---
    for _col in ["first_name VARCHAR", "last_name VARCHAR"]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- GATE_LOG SS9a MIGRATIONS (2026-08-31 -- see the GateLog class
    # docstring above for what each column is and why) ---
    for _col in [
        "daily_support FLOAT", "daily_resistance FLOAT",
        "f24_poc FLOAT", "f24_vah FLOAT", "f24_val FLOAT",
        "slope FLOAT", "structure_score FLOAT",
        "execution_entry_mode VARCHAR", "execution_fill_time TIMESTAMP",
        "execution_fill_price FLOAT", "execution_stop_price FLOAT",
        "execution_stop_basis VARCHAR", "execution_stop_dist_atr FLOAT",
        "reentry_used BOOLEAN", "execution_backfilled_at TIMESTAMP",
        "pressure VARCHAR", "would_have_r FLOAT",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE gate_log ADD COLUMN {_col}"))
        except Exception:
            pass

    # --- EXECUTOR STAGE 2 MIGRATIONS (2026-09-05 -- live tiny-order
    # mechanism test; see ExecutorGlobalConfig/ExecutorOrder/
    # ExecutorAuditLog's own comments for what each column is) ---
    for _col in [
        "live_orders_enabled BOOLEAN DEFAULT 0", "live_orders_enabled_at TIMESTAMP",
        "live_orders_enabled_by VARCHAR", "live_orders_enabled_reason VARCHAR",
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE executor_global_config ADD COLUMN {_col}"))
        except Exception:
            pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE executor_orders ADD COLUMN maintenance_margin_rate_used FLOAT"))
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE executor_audit_log ADD COLUMN executor_mechanism_test_id INTEGER"))
    except Exception:
        pass

# ---------------------------------------------------------
# EXISTING USER MODEL
# ---------------------------------------------------------
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    tradingview_id = Column(String)
    tier = Column(String, nullable=False, default="basic")
    session_tz = Column(String, nullable=False, default="UTC")
    
    stripe_customer_id = Column(String)
    stripe_subscription_id = Column(String)
    stripe_price_id = Column(String)
    subscription_status = Column(String, default="inactive")
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    
    is_admin = Column(Boolean, default=False)
    operator_flex = Column(Boolean, default=False)

# ---------------------------------------------------------
# EXISTING: GRAVITY GRID MEMORY VAULT
# ---------------------------------------------------------
class GravityMemory(Base):
    __tablename__ = "gravity_memory"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    
    source = Column(String, nullable=False)      
    level_type = Column(String, nullable=False)  
    price = Column(Float, nullable=False)
    
    permanence_class = Column(Integer, nullable=False)
    heat_multiplier = Column(Float, default=1.0)
    active = Column(Boolean, default=True)

    # Zone strength fields (v2 target logic)
    departure_move_pct = Column(Float, nullable=True)   # % price moved away in 3 bars after zone formation
    touch_count = Column(Integer, default=0)             # times price revisited this zone without breaking through

# ---------------------------------------------------------
# EXISTING: PERMANENT SESSION LOCKS
# ---------------------------------------------------------
class SessionLock(Base):
    __tablename__ = "session_locks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=False)
    date_key = Column(String, index=True, nullable=False)
    lock_time = Column(Integer, nullable=False)
    
    packet_data = Column(String, nullable=False) 

# ---------------------------------------------------------
# MISSION LEDGER (AUTOMATED TRADE TRACKER + MAS ORCHESTRATION)
# ---------------------------------------------------------
class CampaignLog(Base):
    __tablename__ = "campaign_logs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    date_key = Column(String, index=True, nullable=False)
    session_id = Column(String, nullable=False)

    bias = Column(String, nullable=False)     
    grade = Column(String, nullable=False)    

    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    t1 = Column(Float, nullable=False)
    # t2/t3 nullable: v3 single-target 4H/1H candidates write NULL for both by design
    # (see TARGET LOGIC AUDIT FIELDS comment below). v1/v2 rows still populate all three.
    t2 = Column(Float, nullable=True)
    t3 = Column(Float, nullable=True)

    total_contracts = Column(Float, nullable=False)

    status = Column(String, default="PENDING", nullable=False) 
    realized_pnl = Column(Float, default=0.0)

    activated_at = Column(DateTime, nullable=True) 
    closed_at = Column(DateTime, nullable=True)    

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    diagnostic_data = Column(String, nullable=True)

    # --- MAS UPGRADE COLUMNS ---
    mas_executive_brief = Column(String, nullable=True)
    mas_approval_status = Column(String, default="PENDING", nullable=False)
    # conviction: TAKE_PREMIUM/TAKE_STANDARD/ALMOST/PASS -- the calibrated
    # gate's real state (2026-08-30 rebuild, decision_engine.py). mas_approval_
    # status alone (APPROVED/STAND_DOWN) collapses TAKE_PREMIUM and
    # TAKE_STANDARD into the same value; this is the finer read, needed by
    # anything (the Brain project's read API) that wants the real state, not
    # just approved-or-not.
    conviction = Column(String, nullable=True)
    tier = Column(String, nullable=True)  # PREMIUM | STANDARD | None (2026-08-30 rebuild)
    formatted_newsletter = Column(String, nullable=True)
    target_hit = Column(String, nullable=True)   # T1 | T2 | T3 | STOP — the target the trade CLOSED AT
    structure_reasoning = Column(String, nullable=True)  # JSON: Trade Structure Analyst audit trail

    # --- W-9 TRADE-LIFECYCLE MONITOR COLUMNS ---
    # entry_filled_at: timestamp when price crossed entry_price during the session window.
    #   NULL = price never reached entry — this setup is a candidate for EXPIRED status.
    #   (activated_at exists as an orphaned column from an earlier design; never used — left alone)
    entry_filled_at = Column(DateTime, nullable=True)

    # session_expires_at: end of the valid NY Futures session window (8:30 AM – ~3:00 PM ET).
    #   A setup not filled by this time → status = EXPIRED, realized_pnl = null.
    session_expires_at = Column(DateTime, nullable=True)

    # max_target_reached: the FURTHEST price target ever touched, even after the trade was exited.
    #   Distinct from target_hit (which is the exit target). Used for target-optimization data:
    #   "system exited at T1 but price reached T3 on 80% of those sessions."
    #   Values: NONE | T1 | T2 | T3. NULL on open/expired trades.
    max_target_reached = Column(String, nullable=True)

    # t2_reached / t3_reached: persistent observation flags for target-optimization analysis.
    #   Set TRUE when price reaches T2 or T3 even if the trade was already closed at T1.
    #   Allows the auditor to ask: "how often does price continue past the exit target?"
    t2_reached = Column(Boolean, default=False, nullable=False, server_default="0")
    t3_reached = Column(Boolean, default=False, nullable=False, server_default="0")

    # is_canonical: TRUE = production-quality BTC/USDT record from 2026-05-27 onward.
    #   All dashboard / auditor / performance / lifecycle queries filter to is_canonical=TRUE.
    #   FALSE = legacy data (multi-symbol era, placeholder PnL, pre-track-record rows).
    #   Auto-set TRUE at creation for any BTC/USDT record. Historical set: IDs 74–90 (13 rows).
    is_canonical = Column(Boolean, default=False, nullable=False, server_default="0")

    # session_timeframe: which system generated this record.
    #   "15M" (default) = standard NY-session 15M system via MAS.
    #   "4H" = 4H BOS candidate detected by gravity engine.
    #   "1H" = 1H BOS candidate detected by gravity engine.
    session_timeframe = Column(String, nullable=True, default="15M")

    # --- TARGET LOGIC AUDIT FIELDS ---
    # These fields are written only by the corrected target/stop construction (v2+).
    # v1 rows have NULL on all of these. Audit-AI must filter on the exact version tag —
    # v2/v3/v4 rows have DIFFERENT SHAPES and must never be pooled together:
    #   'v1' = original broken (Class 0 / DAILY_PIVOT cascade targets) — excluded from all audit.
    #   'v2' = corrected equal-leg staged targets (T1/T2/T3 all populated). Legacy rows only,
    #          frozen at the 2026-07-01 single-target cutover — no new v2 rows written.
    #   'v3' = single structural target (T1 populated, T2/T3 always NULL by design — this
    #          was the v3 shape, not missing data). Legacy rows only, frozen at the 2026-07-04
    #          v4 cutover (stop-selection confirmed broken via real 2026-07-03 examples;
    #          see WORK_LOG.md) — no new v3 rows written.
    #   'v4' = windowed nearest-pivot stop (recency-bounded to a per-TF window empirically
    #          chosen via mtf_backtest_lab.py --window-test: 5 calendar days for 4H, 2 for 1H;
    #          no heat/touch/departure strength gate) + Fibonacci-staged T1/T2/T3
    #          (1.0x/1.618x/2.618x of the entry-to-stop leg). T2/T3 ALWAYS populated
    #          (unlike v3). htf_anchor_type/htf_anchor_price now describe the STOP's pivot
    #          source (STOP_PIVOT | ATR_FALLBACK), not a target-side opposing zone as in v2/v3.
    #          Current logic as of 2026-07-04.
    target_logic_version = Column(String, nullable=True, default="v1")
    target_too_small_flag = Column(Boolean, default=False)               # audit-only; T1 < 1.5x ATR — never gates trade
    htf_anchor_type = Column(String, nullable=True)                      # e.g. 'BULL_WAVE_3', 'DAILY_PIVOT', 'FIB_FALLBACK'
    htf_anchor_price = Column(Float, nullable=True)                      # price of the higher-TF level that set the target
    energy_grade = Column(String, nullable=True)                         # STRONG/MODERATE/WEAK at detection time

    # kinematic_grade: PRIMED/TANGLED/OVEREXTENDED, the 15M JEWEL's own market-state formula
    # (BBWP/PMARP/EMA9-55 ribbon based), ported to 4H/1H as a SECOND, purely observational
    # signal alongside energy_grade. RECORD-ONLY (2026-07-05 decision) -- neither this nor
    # energy_grade gates candidate creation. Backtested against v4-consistent trade
    # construction (N=167 1H, N=177 4H): no clean, reliable signal at current sample sizes
    # on either formula for either timeframe (kinematic_grade is actually backwards on 4H --
    # OVEREXTENDED outperforms PRIMED). Revisit enforcement only once real production data
    # clears N>=30 per timeframe with a stable signal -- see WORK_LOG.md 2026-07-05 entry.
    # NULL on 15M rows (they don't go through the 4H/1H detectors that compute this).
    kinematic_grade = Column(String, nullable=True)

    # macro_bias: BULLISH/BEARISH/NEUTRAL, battlebox_pipeline._calculate_weekly_force()
    # (21-day vs 7-day daily SMA crossover), reused directly from the 15M system.
    # Backtested (2026-07-06, v4-consistent construction): 1H aligned-with-bias signals
    # clearly outperform counter-trend (58.3%/+0.257R vs 46.4%/-0.028R, N=84/69) -- HARD
    # GATE on 1H: counter-trend candidates are never written. 4H is INVERTED in the same
    # backtest (counter-trend outperforms aligned) -- record-only, NOT enforced, since
    # blocking would remove the currently-winning subset. See WORK_LOG.md 2026-07-06.
    macro_bias = Column(String, nullable=True)

    # weekly_200sma_position: ABOVE/BELOW/AT, battlebox_pipeline._fetch_weekly_200sma()
    # + the same +-0.5% threshold _compute_mtf_structural_snapshot() already uses.
    # RECORD-ONLY on both 4H/1H -- not independently backtested (would need ~1400+ days
    # of daily history to test rigorously); revisit once real production data accumulates.
    weekly_200sma_position = Column(String, nullable=True)

    # --- RUNNER MECHANIC (SHADOW MODE, 2026-07-06) -- 15M ONLY, RECORD-ONLY ---
    # At a real T1 close, ledger_closing_engine.py's Phase 2 also seeds these fields
    # and Phase 3B ("Shadow Runner Tracking") continues walking 1m candles forward,
    # trailing a stop toward a 15m EMA21 (never loosening past breakeven) and
    # resolving against a STOP touch, T3 touch, or a 5-day time cap. The REAL
    # status/realized_pnl/closed_at fields are never touched by this -- shadow_runner_*
    # only models what a "close 50% at T1, run the rest" mechanic would have produced,
    # for comparison against the real, already-recorded T1 outcome before ever
    # considering flipping this live. NULL/False on every row until its own T1 hits
    # after this feature's deploy (no backfill of pre-existing T1 rows).
    shadow_runner_active = Column(Boolean, default=False, server_default="0")
    shadow_runner_stop = Column(Float, nullable=True)
    shadow_runner_ema21 = Column(Float, nullable=True)
    shadow_runner_bucket_ts = Column(DateTime, nullable=True)
    shadow_runner_bucket_close = Column(Float, nullable=True)
    shadow_runner_last_scan_ts = Column(DateTime, nullable=True)
    shadow_runner_closed_at = Column(DateTime, nullable=True)
    shadow_runner_exit_reason = Column(String, nullable=True)  # STOP | T3 | TIME_CAP
    shadow_runner_leg2_r = Column(Float, nullable=True)
    shadow_runner_blended_r = Column(Float, nullable=True)

    # --- RUNNER MECHANIC (LIVE, 2026-08-30) -- 15M ONLY, AUTHORITATIVE ---
    # The shadow_runner_* columns above modeled "close 50% at T1, run the
    # rest" (2026-07-06) as a record-only comparison against a real ledger
    # that closed 100% at T1 -- KABRODA_REBUILD_SPEC.md SS6 later validated a
    # DIFFERENT split (30% at T1, fixed runner-stop, 70% to T3) as the actual
    # winning management rule, beating both 50/50 and 100%-at-T1 in the
    # calibration backtest. Found 2026-08-30: the real status/realized_pnl
    # fields were still closing 100% at T1 -- the rejected alternative, not
    # the validated rule -- with the shadow tracker still only modeling the
    # OTHER rejected alternative. Neither matched what was actually
    # validated. These columns make the validated rule the real, live
    # mechanic: ledger_closing_engine.py's Phase 2 sets them at a T1 touch
    # and status/realized_pnl/closed_at are now only set when the runner leg
    # itself resolves (runner_stop touch, T3 touch, or session-open time cap).
    # shadow_runner_* above is left in place (legacy rows still resolve via
    # Phase 3B) but no longer seeded at new T1 touches -- superseded, not
    # deleted.
    runner_active = Column(Boolean, default=False, server_default="0")
    runner_stop = Column(Float, nullable=True)          # fixed level: entry -+ 0.15*box, set once at T1
    runner_started_at = Column(DateTime, nullable=True)  # candle ts of the T1 touch that opened the runner leg
    t1_leg_r = Column(Float, nullable=True)              # 0.30 * (T1's R relative to the original stop) -- the locked-in leg

    # --- MTF CONFLUENCE CAPTURE (2026-07-09) -- 4H/1H ONLY, RECORD-ONLY ---
    # At candidate-creation time, gravity_engine.py's two detectors call
    # mtf_confluence_scanner.run_mtf_confluence_scan() once and stash the live
    # 5-TF read here -- the same function that already powers Market Radar's
    # bundled scan and the new standalone /api/confluence view. Answers "did
    # the wider confluence read agree or oppose this specific candidate's
    # bias" for audit_ai.py's H10_TF_AGREEMENT hypothesis. NULL on 15M rows.
    dominant_direction = Column(String, nullable=True)   # BULLISH / BEARISH / NEUTRAL, from the scanner at fire time
    confluence_score = Column(Integer, nullable=True)    # 0-5 timeframes aligned, from the scanner at fire time

    # --- REVIN SUITE CAPTURE (2026-07-15) -- 4H/1H ONLY, RECORD-ONLY ---
    # Extracted from the same mtf_confluence_scanner run that populates
    # dominant_direction/confluence_score above. Uses the candidate's own
    # timeframe's Revin data (4H for 4H candidates, 1H for 1H candidates).
    # RECORD-ONLY -- feeds audit_ai.py's Revin alignment hypothesis, does
    # not gate candidate creation. NULL on 15M rows.
    revin_ribbon_zone = Column(String, nullable=True)     # ABOVE_MIDLINE / BELOW_MIDLINE / AT_BAND / UNKNOWN
    revin_midline_price = Column(Float, nullable=True)     # Revin Ribbons midline price (S/R level)
    rmo_score = Column(Float, nullable=True)               # RMO momentum score (-100 to +100)
    rmo_state = Column(String, nullable=True)              # BULLISH / BEARISH / NEUTRAL
    rwp_squeeze = Column(Boolean, nullable=True)           # RWP squeeze active (confirms compression)

# MtfReading ("Morning Brief" history) removed 2026-08-30 -- its only writer
# was market_radar.py's scan_sector(), which stopped populating it alongside
# the rest of the old confluence vote-tally purge. Zero readers anywhere
# (grepped). The underlying mtf_readings SQLite table is left in place (no
# migration framework -- see the "Database Schema Notes" section of
# CLAUDE.md), just unmapped.

# ---------------------------------------------------------
# TRADE PLAN (KABRODA_COM_TRADE_PLAN_SPEC.md SS3/SS5, 2026-08-31)
# One plan per session, generated once at the 8:00 CT (13:00 UTC) lock,
# never re-generated intraday -- the anti-flip-flop rule (SS1). State
# transitions are one-way. This is a NEW, additive object: it does not
# replace or feed CampaignLog.stop_loss / any R-multiple math (see
# stop_planner.py's header and docs/STOP_BASIS_ANSWER.md in the Kabroda AI
# Brain repo -- confirmed directly with Andy, 2026-08-31, before this table
# existed). management describes the VALIDATED rule already running in
# ledger_closing_engine.py (30% at T1, fixed runner-stop, 70% to T3, same
# both tiers) -- the spec's own first draft said something different
# (tier-dependent, stop-to-breakeven); caught as a real spec/code mismatch
# and corrected in the Brain repo (commit d8a33ce) rather than silently
# picking one. This table's own status vocabulary — NO_PLAN | WAITING |
# ARMED | VETOED | FILLED | STOPPED | REENTRY_ARMED | DONE — is unrelated
# to CampaignLog.status (PENDING/CLOSED_WIN/CLOSED_LOSS/...); don't conflate
# the two state machines.
# ---------------------------------------------------------
class TradePlan(Base):
    __tablename__ = "trade_plans"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    date_key = Column(String, index=True, nullable=False)
    session_id = Column(String, nullable=False)

    status = Column(String, default="NO_PLAN", nullable=False)
    direction = Column(String, nullable=True)     # LONG | SHORT | None (NO_PLAN)
    tier = Column(String, nullable=True)           # PREMIUM | STANDARD | None

    entry_mode = Column(String, nullable=True)     # TRIGGER_AT_LEVEL | RETEST_LIMIT_AT_LINE, set at commit
    trigger_price = Column(Float, nullable=True)
    commit_after = Column(DateTime, nullable=True)  # anchor_time + 45min (08:45 CT / 09:45 ET open-window rule)

    # Execution stop from stop_planner.py -- the 24h core-zone stop. NOT
    # CampaignLog.stop_loss (r30-based); see the table docstring above.
    stop_price = Column(Float, nullable=True)
    stop_basis = Column(String, nullable=True)
    stop_dist_atr = Column(Float, nullable=True)

    t1 = Column(Float, nullable=True)
    t2 = Column(Float, nullable=True)
    t3 = Column(Float, nullable=True)
    management = Column(String, nullable=True)      # human-readable rule text for the brief

    fuel_requirement = Column(String, nullable=True)
    rr_floor_ok = Column(Boolean, nullable=True)
    rr_ratio = Column(Float, nullable=True)

    no_plan_reason = Column(String, nullable=True)   # populated when status == NO_PLAN
    plan_text = Column(String, nullable=True)         # the rendered pre-commit brief (SS4)

    # --- intraday state, set by one-way transitions only (SS5) ---
    cross_time = Column(DateTime, nullable=True)
    fuel_at_cross = Column(String, nullable=True)    # FUELED | CONFLICTED | NO_FUEL
    fill_time = Column(DateTime, nullable=True)
    fill_price = Column(Float, nullable=True)
    faked_first = Column(Boolean, nullable=True)
    stopped_time = Column(DateTime, nullable=True)

    reentry_used = Column(Boolean, default=False, nullable=False, server_default="0")
    reentry_cross_time = Column(DateTime, nullable=True)
    reentry_fill_price = Column(Float, nullable=True)

    # The site displays THIS, not a new opinion (SS5) -- every transition
    # writes a plain-English reason here.
    last_transition_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------
# DECISION JOURNAL (PERFORMANCE AUDITOR FOUNDATION — DATA COLLECTION ONLY)
# ---------------------------------------------------------
class DecisionJournal(Base):
    __tablename__ = "decision_journal"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # MAS flow:      MAS_APPROVED / MAS_REJECTED / MAS_STAND_DOWN / MAS_WAITING
    # Market Radar:  GRADE_A / GRADE_B / STAND_DOWN
    decision_type = Column(String, nullable=False)

    confluence_score = Column(Integer, nullable=True, default=0)
    confluence_direction = Column(String, nullable=True, default="NEUTRAL")
    energy_status = Column(String, nullable=True, default="BUILDING")
    kinematic_grade = Column(String, nullable=True)   # PRIMED | OVEREXTENDED | TANGLED | UNKNOWN

    bo_price = Column(Float, nullable=True)
    bd_price = Column(Float, nullable=True)
    asset_price = Column(Float, nullable=True)

    session_date = Column(String, nullable=True)
    session_id   = Column(String, nullable=True)   # e.g. "us_ny_futures" — session TYPE label, not unique run id
    source       = Column(String, nullable=True)   # "mas_flow" | "market_radar"
    decision_reason = Column(String, nullable=True)

    # Outcome fields — null at creation, filled by the 4H gravity-engine task.
    outcome_price_4h = Column(Float, nullable=True)
    outcome_pct_move_4h = Column(Float, nullable=True)
    outcome_direction_correct = Column(Boolean, nullable=True)

    full_context_json = Column(String, nullable=True)


# ---------------------------------------------------------
# AGENT RUN LOG (PHASE 1 — COST INFRASTRUCTURE)
# Tracks every agent invocation: tokens, cost, status.
# Budget gate reads this table before any agent fires.
# ---------------------------------------------------------
class AgentRunLog(Base):
    __tablename__ = "agent_run_log"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, default="claude-sonnet-4-6")
    triggered_by = Column(String, nullable=False)

    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, default=0)

    estimated_cost_usd = Column(Float, default=0.0)

    # SUCCESS | ERROR | BUDGET_BLOCKED
    status = Column(String, nullable=False)
    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# MACRO NARRATIVE LOG (PHASE 2 — CROSS-DAY NARRATIVE MEMORY)
# Stores the Elliott Wave structural context and the Senior
# Analyst's daily brief text. Tomorrow's Senior Analyst reads
# yesterday's row before writing, creating genuine continuity.
#
# Writers:
#   elliott_wave_specialist — updates wave parameters Sunday
#   senior_analyst          — writes narrative_text + tactical_text daily
#   performance_auditor     — writes performance_note Sunday
# ---------------------------------------------------------
class MacroNarrativeLog(Base):
    __tablename__ = "macro_narrative_log"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, index=True, default="BTC/USDT")
    date_key = Column(String, nullable=False, index=True)

    # "elliott_wave_specialist" | "senior_analyst"
    authored_by = Column(String, nullable=False)

    # Elliott Wave parameters — written by Elliott Wave Specialist
    wave_label = Column(String, nullable=True)           # e.g. "BEAR_WAVE_4_BOUNCE"
    wave_origin_date = Column(String, nullable=True)     # e.g. "2026-02-05"
    wave_origin_price = Column(Float, nullable=True)     # e.g. 60055.00
    wave_target_price = Column(Float, nullable=True)     # e.g. 80632.00
    wave_day_count = Column(Integer, nullable=True)      # days since wave_origin_date
    completion_pct = Column(Float, nullable=True)        # % to wave_target_price
    invalidation_price = Column(Float, nullable=True)    # where this wave count dies

    # Specialist reasoning — written by Elliott Wave Specialist
    wave_status = Column(String, nullable=True)          # IN_PROGRESS | CONFIRMED | PENDING | QUESTIONABLE
    wave_reasoning = Column(String, nullable=True)       # Full EWT structural analysis with rule citations
    confirmation_condition = Column(String, nullable=True)  # Price events that confirm wave completion

    # Brief text — written by Senior Analyst
    narrative_text = Column(String, nullable=True)       # Part 1: the paragraph
    tactical_text = Column(String, nullable=True)        # Part 2: structured setup

    # Corrections — written by Performance Auditor
    performance_note = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# JewelSnapshotLog removed 2026-08-30 -- its only writer (jewel_specialist.py)
# is archived, and its only reader (main.py's /api/dashboard/jewel,
# /api/radar/snapshot, /api/narrative/latest jewel fields, kabroda_mas_flow.py's
# Senior Analyst context reader) are all removed alongside the jewel/confluence
# purge (grepped, zero live references left). The underlying jewel_snapshot_log
# SQLite table is left in place (no migration framework), just unmapped.

# NewsletterLog removed 2026-08-30 -- its only writer (publisher_crew.py) was
# archived when the Content Publishing Engine (Step 8 of the old MAS flow) was
# retired; the graded coded decision layer generates no newsletter. Its only
# reader (main.py's /api/dashboard/newsletters) is removed too. The underlying
# newsletter_log SQLite table is left in place, just unmapped.

# ---------------------------------------------------------
# SYSTEM AUDIT LOG (PERFORMANCE AUDITOR VAULT)
# Permanent home for weekly Performance Auditor output.
# Decoupled from MacroNarrativeLog — no dependency on a
# senior_analyst row existing. New table; created by
# Base.metadata.create_all(), no ALTER TABLE needed.
# ---------------------------------------------------------
class SystemAuditLog(Base):
    __tablename__ = "system_audit_log"

    id         = Column(Integer, primary_key=True, index=True)
    symbol     = Column(String,  index=True, nullable=False)
    date_key   = Column(String,  index=True, nullable=False)
    audit_md   = Column(String,  nullable=False)
    ran_successfully = Column(Boolean, default=True, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# INTERPRETER LOG (AUDITABILITY COVENANT)
# Persists every Bucket B interpreter's full output text,
# keyed to the session so it can be joined to CampaignLog
# and DecisionJournal for per-domain calibration queries.
#
# Writer: kabroda_mas_flow._log_interpreter() — called
#   immediately after each interpreter returns, fail-safe.
#   A row is written even on fail-open (output_text=None,
#   ran_successfully=False) so absences are visible.
#
# New table — picked up by Base.metadata.create_all() on
# deploy. No ALTER TABLE migration needed.
# ---------------------------------------------------------
class InterpreterLog(Base):
    __tablename__ = "interpreter_log"

    id               = Column(Integer, primary_key=True, index=True)
    symbol           = Column(String,  index=True, nullable=False)
    session_date     = Column(String,  index=True, nullable=False)  # date_key "YYYY-MM-DD"
    session_id       = Column(String,  index=True, nullable=False)  # e.g. "us_ny_futures"
    interpreter_name = Column(String,  index=True, nullable=False)  # "mtf_interpreter" | "gravity_interpreter"
    output_text      = Column(String,  nullable=True)               # Full prose — null if fail-opened
    ran_successfully = Column(Boolean, nullable=False, default=False)
    created_at       = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# KULTI LONG-TERM INVESTING MODULE (2026-07-07)
# Separate, advisory-only module -- BTC monthly-cadence buy-and-hold framework
# (Eric Crown's "KULTI" course, see WORK_LOG.md for full design rationale).
# Never auto-executes anything; "the framework flags WHEN, you decide HOW MUCH."
# ---------------------------------------------------------
class LtiCheckpoint(Base):
    """
    One frozen row per monthly LTI audit -- append-only, same write-once
    discipline as MacroNarrativeLog/InterpreterLog/SessionAuditLog. Never
    updated after creation.
    """
    __tablename__ = "lti_checkpoints"

    id         = Column(Integer, primary_key=True, index=True)
    symbol     = Column(String, nullable=False, default="BTC/USDT", index=True)
    date_key   = Column(String, nullable=False, index=True)  # "YYYY-MM" month key
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Raw component readings (Crown's 11-component KULTI stack, minus the
    # meta "Strong Buy Filter" which is the confluence count itself)
    bbwp                = Column(Float, nullable=True)
    bbwp_state          = Column(String, nullable=True)
    pmarp               = Column(Float, nullable=True)
    pmarp_state         = Column(String, nullable=True)
    rsi_weekly          = Column(Float, nullable=True)
    pct_below_high      = Column(Float, nullable=True)
    krown_cross_state   = Column(String, nullable=True)   # BULLISH_EXPANDING etc (JEWEL-style label)
    weekly_ema_trend    = Column(String, nullable=True)
    low_month_day_flag  = Column(Boolean, default=False)
    moon_phase_flag     = Column(Boolean, default=False)
    moon_phase_label    = Column(String, nullable=True)
    hash_ribbons_state  = Column(String, nullable=True)   # CAPITULATION | RECOVERY | NEUTRAL | UNAVAILABLE
    fear_greed_value    = Column(Integer, nullable=True)  # Pesto F&G proxy
    fear_greed_label    = Column(String, nullable=True)

    # Confluence engine output (Crown's Conviction Scale)
    accumulation_signals_firing = Column(Integer, default=0)
    distribution_signals_firing = Column(Integer, default=0)
    conviction_label            = Column(String, nullable=True)  # NO_ACTION|WATCH|EXECUTE|VERY_HIGH|GENERATIONAL

    # Kabroda-native additions (not in Crown's original course)
    wave_label_snapshot   = Column(String, nullable=True)   # from MacroNarrativeLog at audit time
    gravity_cross_confirm = Column(Boolean, default=False)
    nearest_macro_level   = Column(Float, nullable=True)


class LtiProtocol(Base):
    """
    The user's editable "One-Page Protocol" (Crown's 5-component + 3-rule
    checklist artifact) -- a mutable single row, matching how every other
    user-editable setting in this codebase works (e.g. UserModel via
    POST /account/settings), not an append-only log.
    """
    __tablename__ = "lti_protocol"

    id                 = Column(Integer, primary_key=True, index=True)
    universe           = Column(String, nullable=True, default="BTC")
    conviction_threshold = Column(Integer, default=4)   # user-chosen 3 / 4 / 5+
    drawdown_protocol  = Column(String, nullable=True)
    cash_floor_pct     = Column(Float, default=5.0)      # Crown's "Never-Fully-Out" floor
    residual_trim_pct  = Column(Float, default=15.0)     # Crown's "15% Residual Rule"
    updated_at         = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------
# COMPONENT 6 EXTENSION — DAILY PER-TRADE "WHY" DIGEST (2026-07-08)
# Pure deterministic fact-surfacing (Bucket A -- no LLM, no agent_core
# dependency, no cost). Companion to the ALREADY-EXISTING Audit-AI hypothesis
# ledger (harness/audit_runner.py + AuditSuggestionLog below, near
# SessionAuditLog) -- that engine checks aggregate statistical hypotheses
# (H1-H6) against SessionAuditLog (15M-only). This table covers the
# complementary need: a per-trade "why did this specific trade fire" record
# across ALL THREE timeframes (15M/1H/4H), reusing CampaignLog fields that
# already exist -- nothing invented, only surfaced and formatted.
# audit_ai.py has no import of anything that mutates trade construction, so
# it cannot touch a live parameter even by accident.
# ---------------------------------------------------------
class DailyAuditLog(Base):
    """
    One row per day. digest_json surfaces, per trade across 15M/1H/4H, WHY
    it fired (reusing already-populated fields -- mas_executive_brief/
    structure_reasoning for 15M; macro_bias/kinematic_grade/energy_grade/
    htf_anchor_type/htf_anchor_price for 4H/1H) -- nothing here is invented,
    only surfaced and formatted from what already exists on CampaignLog.
    """
    __tablename__ = "daily_audit_log"

    id                  = Column(Integer, primary_key=True, index=True)
    date_key            = Column(String, nullable=False, index=True)  # "YYYY-MM-DD"
    digest_json         = Column(String, nullable=False)
    trades_covered_15m  = Column(Integer, default=0)
    trades_covered_1h   = Column(Integer, default=0)
    trades_covered_4h   = Column(Integer, default=0)
    created_at          = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# SESSION AUDIT LOG (FORWARD-AUDIT LOOP — CANONICAL AUDIT RECORD)
# One row per MAS session decision. Write-once discipline:
#   - Frozen-at-decision columns set once when decision is made; never overwritten.
#   - Outcome columns (outcome_*) set once when trade resolves; never overwritten.
#   - outcome_set_at timestamps the back-fill.
#
# No hash chain (Adj. 2): single-operator system with no external auditor requiring
# cryptographic tamper evidence. The write-once column discipline is sufficient.
#
# Write paths:
#   - harness/audit_writer.write_decision_record()  — called from kabroda_mas_flow.py
#   - harness/audit_writer.backfill_outcome()       — called from ledger_closing_engine.py
#
# Both wrap their DB calls in try/except — a failed audit write never blocks the
# decision or close path. See Adjustment 3.
# ---------------------------------------------------------
class SessionAuditLog(Base):
    __tablename__ = "session_audit_log"

    id         = Column(Integer, primary_key=True, index=True)
    symbol     = Column(String, index=True, nullable=False)       # "BTC/USDT"
    date_key   = Column(String, index=True, nullable=False)       # "YYYY-MM-DD"
    session_id = Column(String, nullable=False)                   # "us_ny_futures"

    # Links to existing tables (soft FK — no ORM relationship declared)
    campaign_log_id      = Column(Integer, nullable=True)         # campaign_logs.id
    decision_journal_id  = Column(Integer, nullable=True)         # decision_journal.id
    jewel_snapshot_id    = Column(Integer, nullable=True)         # jewel_snapshot_log.id

    # ── FROZEN AT DECISION TIME (write-once; never overwritten after creation) ──
    decision_timestamp_utc = Column(DateTime, nullable=True)      # exact UTC moment MAS verdict produced
    approval_status        = Column(String,   nullable=True)      # APPROVED / STAND_DOWN / REJECTED / WAITING_FOR_15M
    bias                   = Column(String,   nullable=True)      # LONG / SHORT / NEUTRAL
    bo_trigger             = Column(Float,    nullable=True)      # breakout trigger at lock time
    bd_trigger             = Column(Float,    nullable=True)      # breakdown trigger at lock time
    box_size_pct           = Column(Float,    nullable=True)      # (bo - bd) / bo * 100, computed at decision time
    energy_status          = Column(String,   nullable=True)      # 1h_fuel_status at decision time
    kinematic_grade        = Column(String,   nullable=True)      # 15M JEWEL kinematic_grade
    jewel_gate_open        = Column(Boolean,  nullable=True)      # NY_OPEN JEWEL gate state
    jewel_conviction       = Column(String,   nullable=True)      # STRONG / MODERATE / WEAK
    kde_peaks_json         = Column(String,   nullable=True)      # kde_peaks list as presented to MAS (JSON)
    rag_memory_snapshot    = Column(String,   nullable=True)      # exact _fetch_cro_memory() return value — reused
                                                                  # reference, NOT a re-fetch. See audit_writer.py.
    agent_chain_json       = Column(String,   nullable=True)      # {"senior_analyst": <response text that passed JSON parse>}
    model_version          = Column(String,   nullable=True)      # model ID string at decision time
    entry_price            = Column(Float,    nullable=True)
    stop_loss              = Column(Float,    nullable=True)
    t1                     = Column(Float,    nullable=True)
    t2                     = Column(Float,    nullable=True)
    t3                     = Column(Float,    nullable=True)

    # ── BACK-FILLED AT RESOLUTION (write-once at resolution time; NULL until then) ──
    outcome_resolved_at_utc  = Column(DateTime, nullable=True)
    outcome_type             = Column(String,   nullable=True)    # CLOSED_WIN / CLOSED_LOSS / NO_TRIGGER /
                                                                  # EXPIRED / STAND_DOWN_SAVED /
                                                                  # STAND_DOWN_OVERCAUTIOUS / STAND_DOWN_UNRESOLVED
    outcome_direction_correct = Column(Boolean, nullable=True)    # True = price moved in declared direction
    realized_pnl_r           = Column(Float,   nullable=True)    # PnL in R units; NULL for stand-downs
    resolution_notes         = Column(String,  nullable=True)    # anomalies: manual close, slippage, etc.
    outcome_set_at           = Column(DateTime, nullable=True)   # when back-fill was written

    # ── INTRADAY MONITOR EXTENSION ──
    micro_state_lock = Column(String, nullable=True)  # micro_state (SWEET_ZONE/HOSTILE_CEILING/etc.) at decision time

    # ── MULTI-TF STRUCTURAL SNAPSHOT (Phase 1 — frozen at lock time; capture only) ──
    daily_21ema_direction      = Column(String,  nullable=True)  # SLOPING_UP / FLAT / SLOPING_DOWN
    daily_21ema_position       = Column(String,  nullable=True)  # ABOVE / AT / BELOW
    daily_21ema_distance_pct   = Column(Float,   nullable=True)  # (price - ema21) / ema21 * 100
    tf4h_200sma_position       = Column(String,  nullable=True)  # ABOVE / AT / BELOW (4H 200 SMA)
    tf4h_200sma_distance_pct   = Column(Float,   nullable=True)
    tf1h_200sma_position       = Column(String,  nullable=True)  # ABOVE / AT / BELOW (1H 200 SMA)
    tf1h_200sma_distance_pct   = Column(Float,   nullable=True)
    weekly_200sma_position     = Column(String,  nullable=True)  # ABOVE / AT / BELOW (weekly 200 SMA)
    weekly_200sma_distance_pct = Column(Float,   nullable=True)
    weekly_200sma_test_count   = Column(Integer, nullable=True)  # consecutive completed daily closes within 1% of weekly 200 SMA

    # ── COMPONENT 0 EXTENSION — additional audit fields frozen at decision time ──
    macro_structure_json      = Column(String,  nullable=True)  # JSON array of Elliott Wave label strings
    tf1h_trend                = Column(String,  nullable=True)  # BULLISH / BEARISH / NEUTRAL
    tf1h_rsi                  = Column(Float,   nullable=True)
    tf1h_adx_strength         = Column(String,  nullable=True)  # STRONG / MODERATE / WEAK
    tf4h_trend                = Column(String,  nullable=True)  # BULLISH / BEARISH / NEUTRAL
    tf4h_rsi                  = Column(Float,   nullable=True)
    tf4h_adx_strength         = Column(String,  nullable=True)  # STRONG / MODERATE / WEAK
    tf4h_macd_hist            = Column(Float,   nullable=True)
    daily_200sma_position     = Column(String,  nullable=True)  # ABOVE / AT / BELOW
    daily_200sma_distance_pct = Column(Float,   nullable=True)

    # ── CROWN SURGERY CUT 4 — BBWP/PMARP at decision time + RSI divergence placeholder ──
    bbwp_15m            = Column(Float,  nullable=True)   # BBWP percentile on 15M candles at lock time (0-100)
    bbwp_state          = Column(String, nullable=True)   # EXTREME_COMPRESSION / MODERATE_COMPRESSION / NEUTRAL / HIGH_EXPANSION / EXTREME_EXPANSION
    pmarp_15m           = Column(Float,  nullable=True)   # PMARP percentile on 15M candles at lock time (0-100)
    pmarp_state         = Column(String, nullable=True)   # EXTREME_DEPRESSED / MODERATE_DEPRESSED / NORMAL_DEVIATION / MODERATE_OVEREXTENDED / EXTREME_OVEREXTENDED
    rsi_divergence_type = Column(String, nullable=True, default="NONE")  # Phase 2 placeholder: NONE / HIDDEN_BULLISH / HIDDEN_BEARISH / REGULAR_BULLISH / REGULAR_BEARISH

    # ── AUDIT METADATA ──
    label_tier = Column(String, nullable=True)  # four-tier label at record time; updated at N milestones
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# TRIALS LOG (FORWARD-AUDIT LOOP — COMPARISONS-EVALUATED COUNTER)
# One row per replay, backtest, or binomial checkpoint run.
# This is the "trials spent" ledger. SELECT COUNT(*) WHERE
# against_n <= current_n gives the comparisons denominator
# for any multiple-comparisons correction.
#
# The hypothesis column is required for evidentiary integrity.
# An empty or NULL hypothesis auto-labels the row DATA_MINED —
# recording THAT a hypothesis was stated before testing, not
# that it was genuinely written before results were seen.
# Honesty is a human discipline this field cannot enforce. (Adj. 4)
#
# Write path: harness/binomial_checkpoint.py and any harness
# module that replays parameters against historical data.
# ---------------------------------------------------------
class TrialsLog(Base):
    __tablename__ = "trials_log"

    id            = Column(Integer, primary_key=True, index=True)
    logged_at_utc = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # REPLAY / PARAMETER_SWEEP / BINOMIAL_CHECKPOINT / ABLATION / MANUAL
    test_type  = Column(String, nullable=False)

    # Written before looking at results. NULL/empty → candidate_status auto-set DATA_MINED.
    # This records existence of a stated hypothesis, not that it preceded result inspection.
    hypothesis = Column(String, nullable=True)

    config_json        = Column(String,  nullable=True)   # complete parameter set tested (JSON)
    against_n          = Column(Integer, nullable=True)   # resolved observations in dataset at test time
    against_date_range = Column(String,  nullable=True)   # "YYYY-MM-DD → YYYY-MM-DD"

    result_summary      = Column(String,  nullable=True)  # findings with N on every percentage
    result_accuracy_pct = Column(Float,   nullable=True)  # extracted numeric for querying
    result_n            = Column(Integer, nullable=True)

    # UNDER_REVIEW / ACTIVE_CANDIDATE / FORWARD_WATCH / PROMOTED / REJECTED / SUPERSEDED / DATA_MINED
    candidate_status = Column(String, nullable=True, default="UNDER_REVIEW")

    notes                  = Column(String,  nullable=True)
    promoted_at_utc        = Column(DateTime, nullable=True)
    promotion_forward_n    = Column(Integer,  nullable=True)  # forward sessions confirmed before promotion


# ---------------------------------------------------------
# MONITOR EVENT LOG (INTRADAY SESSION MONITOR — v1)
# One row per 15-minute poll during the active session window
# (lock_time → 4:00 PM ET). Observe-and-log only.
#
# Hard wall: no FK to session_locks or campaign_logs.
# No write to any live column. Every write is wrapped in
# try/except — a failed row never stops the monitor loop.
#
# New table — picked up by Base.metadata.create_all() on
# deploy. No ALTER TABLE migration needed.
# ---------------------------------------------------------
class MonitorEventLog(Base):
    __tablename__ = "monitor_event_log"

    id            = Column(Integer, primary_key=True, index=True)
    symbol        = Column(String,  index=True, nullable=False)  # "BTC/USDT"
    session_date  = Column(String,  index=True, nullable=False)  # "YYYY-MM-DD"
    session_id    = Column(String,  nullable=False)              # "us_ny_futures"
    poll_sequence = Column(Integer, nullable=False)              # monotonic 1 → ~28

    poll_timestamp = Column(DateTime, nullable=False)
    btc_price      = Column(Float,   nullable=True)
    pct_from_bo    = Column(Float,   nullable=True)   # ((price - bo) / bo) * 100
    pct_from_bd    = Column(Float,   nullable=True)   # ((price - bd) / bd) * 100
    mas_verdict    = Column(String,  nullable=True)   # STAND_DOWN / APPROVED / PENDING / UNKNOWN

    # Full computed state snapshot at this poll (JSON)
    state_snapshot_json = Column(String, nullable=True)

    # Transition events vs previous poll: [{variable, prior_state, new_state}, ...]
    transitions_json = Column(String,  nullable=True)
    any_transition   = Column(Boolean, default=False, nullable=False)
    transition_count = Column(Integer, default=0,     nullable=False)

    # Blocking condition state — re-derived at session start from session_audit_log
    conditions_active_json     = Column(String,  nullable=True)   # {cond_1, cond_2, cond_3, any_active}
    stand_down_conds_all_clear = Column(Boolean, default=False, nullable=False)
    consecutive_clears         = Column(Integer, default=0,     nullable=False)
    notification_sent          = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# MONITOR CONFIG (NOTIFICATION GATE — v1)
# One row per monitored instrument (currently BTC only).
# Three gates must ALL clear before notifications can fire:
#   Gate A: 30+ resolved transition events (evidence threshold)
#   Gate B: human harness review confirms signal plausibility
#   Gate C: explicit human notification_enabled flag flip
# The monitor cannot enable itself. All three require human action.
#
# New table — picked up by Base.metadata.create_all() on
# deploy. No ALTER TABLE migration needed.
# ---------------------------------------------------------
class MonitorConfig(Base):
    __tablename__ = "monitor_config"

    id         = Column(Integer, primary_key=True, index=True)
    config_key = Column(String, unique=True, nullable=False)   # "btc_session_monitor"

    # Gate A: minimum resolved-session transition events before notifications unlock
    gate_a_min_events = Column(Integer, default=30, nullable=False)

    # Gate B: human harness review confirming signal quality
    gate_b_harness_reviewed = Column(Boolean, default=False, nullable=False)
    gate_b_reviewed_at      = Column(DateTime, nullable=True)
    gate_b_reviewed_by      = Column(String,   nullable=True)

    # Gate C: explicit human enable
    notification_enabled = Column(Boolean, default=False, nullable=False)
    enabled_at           = Column(DateTime, nullable=True)
    enabled_by           = Column(String,   nullable=True)

    # Notification behaviour
    confirmation_polls        = Column(Integer,  default=2,  nullable=False)  # 2 consecutive clean polls required
    cooldown_hours            = Column(Integer,  default=4,  nullable=False)  # max 1 notification per N hours
    last_notification_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------
# AUDIT SUGGESTION LOG (AUDIT-AI WEEKLY LEDGER)
# Stores hypothesis-level suggestions generated by harness/audit_runner.py.
# Written only when N_supporting >= 30 for a given hypothesis.
# Never modifies live parameters — observe and suggest only.
# Authority cap: harness/audit_runner.py WRITES HERE ONLY.
# New table — picked up by Base.metadata.create_all() on deploy.
# ---------------------------------------------------------
class AuditSuggestionLog(Base):
    __tablename__ = "audit_suggestion_log"

    id                        = Column(Integer, primary_key=True, index=True)
    logged_at                 = Column(DateTime, nullable=False)
    sessions_analyzed_n       = Column(Integer, nullable=False)
    sessions_with_outcomes_n  = Column(Integer, nullable=False)
    hypothesis_id             = Column(String, nullable=False, index=True)   # H1–H6
    hypothesis_text           = Column(String, nullable=False)
    current_param_label       = Column(String, nullable=True)
    tested_param_label        = Column(String, nullable=True)
    actual_win_rate           = Column(Float,  nullable=True)
    counterfactual_win_rate   = Column(Float,  nullable=True)
    relative_improvement_pct  = Column(Float,  nullable=True)
    tier_label                = Column(String, nullable=False)
    n_supporting              = Column(Integer, nullable=False)
    suggestion_text           = Column(String, nullable=False)
    consecutive_runs_surfaced = Column(Integer, default=1, nullable=False)
    status                    = Column(String, default="OPEN", nullable=False)  # OPEN / OWNER_REVIEWED / ACTED_ON / DISMISSED


class SystemAnalysisReport(Base):
    __tablename__ = "system_analysis_reports"
    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(String, unique=True, index=True, nullable=False)
    query = Column(String, nullable=False)
    status = Column(String, default="PENDING", nullable=False)
    report_json = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# SignalAccuracyLog, SystemAlertLog, SignalHealthLog, SignalWeight,
# AccuracyReport removed 2026-08-28 -- confirmed zero references anywhere in
# the live app (writer scripts, e.g. signal_accuracy_tracker.py, were already
# archived 2026-08-17; nothing ever read these tables either). Andy's call:
# "safe to pull out and get out of the way." No data existed to lose --
# tables were never written to in production.


class SignalPerformanceLog(Base):
    """Full indicator state snapshot at signal time, with outcome tracking.

    One row per signal analyzed. The indicator_snapshot column stores the
    complete multi-TF indicator state as JSON. Individual columns (confluence_score,
    jewel_gate_open, etc.) are extracted for queryable analysis.

    Idempotency: (source, symbol, direction, signal_timestamp) is unique.
    Duplicate POSTs with the same values silently return the existing row.
    """
    __tablename__ = "signal_performance_log"

    id = Column(Integer, primary_key=True, index=True)

    # Signal identity
    source = Column(String, nullable=False)             # "meta_signals" | "kabroda_radar"
    symbol = Column(String, nullable=False, index=True) # "BTC/USDT" (normalized)
    direction = Column(String, nullable=False)          # "LONG" | "SHORT"
    entry_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    tp1_price = Column(Float, nullable=True)
    tp2_price = Column(Float, nullable=True)
    tp3_price = Column(Float, nullable=True)
    signal_timeframe = Column(String, nullable=True)    # "15M" | "1H" | "4H" | etc.

    # Price action regime at signal time
    price_action_regime = Column(String, nullable=True)  # TRENDING | RANGING | COMPRESSING | EXPANDING

    # Full indicator state (JSON blob — all TFs, all indicators)
    indicator_snapshot = Column(Text, nullable=True)

    # Composite signals (extracted for queryable analysis)
    confluence_score = Column(Integer, nullable=True)
    dominant_direction = Column(String, nullable=True)
    conviction = Column(String, nullable=True)
    jewel_gate_open = Column(Boolean, nullable=True)
    jewel_direction = Column(String, nullable=True)
    jewel_conviction = Column(String, nullable=True)
    jewel_summary = Column(String, nullable=True)

    # Gravity (for BTC)
    nearest_support = Column(Float, nullable=True)
    nearest_resistance = Column(Float, nullable=True)

    # Kabroda read / analysis text
    kabroda_read = Column(Text, nullable=True)

    # Outcome (set after the move plays out)
    outcome_tp1_hit = Column(Boolean, nullable=True)
    outcome_tp2_hit = Column(Boolean, nullable=True)
    outcome_tp3_hit = Column(Boolean, nullable=True)
    outcome_stop_hit = Column(Boolean, nullable=True)
    outcome_max_favorable_pct = Column(Float, nullable=True)
    outcome_max_adverse_pct = Column(Float, nullable=True)
    outcome_price_action = Column(String, nullable=True)  # "UP" | "DOWN" | "SIDEWAYS"
    outcome_checked_at = Column(DateTime, nullable=True)

    # Post-mortem
    post_mortem = Column(Text, nullable=True)

    # Timestamps
    signal_timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------
# UNIFIED AUDIT SYSTEM (2026-07-18) -- Phase 1
# candle_history / decision_log / decision_gauge_reading
#
# Full design history in UNIFIED_AUDIT_SYSTEM_PLAN.md (v1.0-v1.6). Dual-write:
# these tables are additive and run alongside session_audit_log/campaign_logs,
# which remain the source of truth until Phase 3. Nothing here changes any
# live behavior -- write-only, no code anywhere reads these tables to gate
# a decision. New tables, no ALTER TABLE migration needed.
#
# Soft FKs only (campaign_log_id, session_audit_log_id, decision_id) --
# no ORM relationship declared, matching SessionAuditLog's own established
# convention in this file.
# ---------------------------------------------------------
class CandleHistory(Base):
    """Every candle the system actually fetched, persisted for replay/audit.
    Upsert hook lives in market_data.py's fetch_live_* functions. Retention:
    keep forever -- BTC-only, ~35k rows/year even at 15M granularity (see
    UNIFIED_AUDIT_SYSTEM_PLAN.md v1.1 Q4)."""
    __tablename__ = "candle_history"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, index=True)       # "BTC/USDT"
    timeframe = Column(String, nullable=False, index=True)    # "5M"/"15M"/"1H"/"4H"/"1D"
    timestamp = Column(DateTime, nullable=False, index=True)  # candle open time, UTC
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_history_symbol_tf_ts"),
    )


class DecisionLog(Base):
    """One row per decision across 15M/1H/4H -- TRADE or STAND_DOWN. See
    UNIFIED_AUDIT_SYSTEM_PLAN.md v1.6 for the decision_type mapping per
    timeframe (15M's approval_status has 4 real values, not 2 -- APPROVED /
    STAND_DOWN / REJECTED / WAITING_FOR_15M; WAITING_FOR_15M is excluded
    entirely, REJECTED maps to STAND_DOWN with stand_down_reason=CRO_REJECTED)."""
    __tablename__ = "decision_log"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, index=True)
    decision_timeframe = Column(String, nullable=False, index=True)  # "15M"/"1H"/"4H"
    decision_type = Column(String, nullable=False, index=True)       # "TRADE"/"STAND_DOWN"
    session_id = Column(String, nullable=True)
    date_key = Column(String, nullable=False, index=True)
    decided_at = Column(DateTime, nullable=False)

    bias = Column(String, nullable=True)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    t1 = Column(Float, nullable=True)
    t2 = Column(Float, nullable=True)
    t3 = Column(Float, nullable=True)
    stop_distance_pct = Column(Float, nullable=True)      # computed at write time
    target_distance_pct = Column(Float, nullable=True)    # computed at write time
    atr_pct_at_decision = Column(Float, nullable=True)    # NULL on 15M for now -- see v1.6

    outcome_status = Column(String, nullable=True)   # backfilled at resolution, mirrors campaign_logs.status /
                                                       # session_audit_log.outcome_type vocabulary per timeframe
    realized_r = Column(Float, nullable=True)

    # STAND_DOWN: fixed lookback = the raw candle range the detector evaluated
    # (4H: 50x4h, 1H: 200x1h, 15M: session_open->lock_time). TRADE: the trade's
    # own lifetime, backfilled at close. See v1.6 for the corrected 1H number.
    candle_window_start = Column(DateTime, nullable=True)
    candle_window_end = Column(DateTime, nullable=True)

    # NO_ZONES / NO_BOS / MACRO_BIAS_CONFLICT (4H/1H, code-verified branches) or
    # CRO_REJECTED (15M only). NULL for 15M's own STAND_DOWN verdict -- that
    # reasoning is LLM-authored prose (mas_executive_brief), not a coded branch.
    stand_down_reason = Column(String, nullable=True)

    # Soft FKs back to the still-authoritative source row this was dual-written from.
    campaign_log_id = Column(Integer, nullable=True)
    session_audit_log_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class DecisionGaugeReading(Base):
    """One row per (decision, timeframe, gauge) -- normalized, so a new
    indicator is a new row, never a schema migration. gauge_name reuses the
    exact source field names already established in the codebase (see
    UNIFIED_AUDIT_SYSTEM_PLAN.md v1.6's mapping table) -- 15M and 4H/1H each
    have their own distinct gauge set and are NEVER coalesced under a shared
    name unless the underlying computation really is identical (kinematic_grade,
    weekly_200sma_position are the only two that are)."""
    __tablename__ = "decision_gauge_reading"

    id = Column(Integer, primary_key=True, index=True)
    decision_id = Column(Integer, nullable=False, index=True)  # soft FK -> decision_log.id
    timeframe = Column(String, nullable=False)      # "15M"/"1H"/"4H"/"Daily"/"Weekly"
    gauge_name = Column(String, nullable=False, index=True)
    value_numeric = Column(Float, nullable=True)
    value_label = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("decision_id", "timeframe", "gauge_name", name="uq_decision_gauge_reading"),
    )


class GateLog(Base):
    """The calibrated-gate forward-incubation record — KABRODA_REBUILD_SPEC.md
    §9. One row per gate evaluation (every trigger-break, TAKE or PASS alike),
    logged at the break; backfilled with the real outcome after 24h. This IS
    the beta-phase track record the Kabroda AI Brain reads to confirm forward
    performance matches the backtest and propose calibration tweaks. Andy's
    explicit call, 2026-08-30: log every detail, no exceptions."""
    __tablename__ = "gate_log"

    id = Column(Integer, primary_key=True, index=True)

    # --- at the break ---
    date_key = Column(String, index=True, nullable=False)
    lock_ts = Column(DateTime, nullable=True)
    symbol = Column(String, index=True, nullable=False)
    side = Column(String, nullable=True)          # LONG | SHORT | None (no trigger yet)
    breakout_trigger = Column(Float, nullable=True)
    breakdown_trigger = Column(Float, nullable=True)
    box = Column(Float, nullable=True)
    anchor = Column(Float, nullable=True)
    range30m_high = Column(Float, nullable=True)
    range30m_low = Column(Float, nullable=True)
    daily_atr14 = Column(Float, nullable=True)
    box_atr_ratio = Column(Float, nullable=True)
    trigger_hour_utc = Column(Integer, nullable=True)
    push_vol_ratio = Column(Float, nullable=True)
    fuel_state = Column(String, nullable=True)     # FUELED | CONFLICTED | NO_FUEL | NO_PUSH | UNKNOWN
    trend_1h = Column(String, nullable=True)
    trend_4h = Column(String, nullable=True)
    htf_aligned = Column(Integer, nullable=True)
    htf_opposed = Column(Integer, nullable=True)
    hour_ok = Column(Boolean, nullable=True)
    daily_regime_table = Column(String, nullable=True)
    daily_regime_quality = Column(String, nullable=True)
    micro_regime = Column(String, nullable=True)
    veto = Column(String, nullable=True)           # which hard veto fired, if any
    gate_pass = Column(Boolean, nullable=True)
    gate_tier = Column(String, nullable=True)       # PREMIUM | STANDARD | None
    state = Column(String, nullable=False)          # TAKE_PREMIUM | TAKE_STANDARD | ALMOST | PASS
    headline = Column(String, nullable=True)
    entry = Column(Float, nullable=True)
    stop = Column(Float, nullable=True)
    t1 = Column(Float, nullable=True)
    t2 = Column(Float, nullable=True)
    t3 = Column(Float, nullable=True)
    subtrig_stop = Column(Float, nullable=True)
    gate_detail_json = Column(String, nullable=True)   # full gate dict, for audit

    # --- backfilled after 24h (ledger_closing_engine.py, TODO: wire the fill) ---
    first_target_hit = Column(String, nullable=True)    # T1 | T2 | T3 | none
    stopped_first = Column(Boolean, nullable=True)
    faked_first = Column(Boolean, nullable=True)
    bars_to_t1 = Column(Integer, nullable=True)
    bars_to_t2 = Column(Integer, nullable=True)
    bars_to_t3 = Column(Integer, nullable=True)
    r_t1only = Column(Float, nullable=True)
    r_runner = Column(Float, nullable=True)
    mfe_r = Column(Float, nullable=True)
    mgmt_label = Column(String, nullable=True)
    backfilled_at = Column(DateTime, nullable=True)

    # --- SS9a locked-level columns (2026-08-31, KABRODA_COM_TRADE_PLAN_
    # SPEC.md) -- captured verbatim from the 8:00 lock, same as the rest of
    # this table's "at the break" section above; genuinely available in
    # sse_engine.py's levels dict, just not previously wired here. ---
    daily_support = Column(Float, nullable=True)
    daily_resistance = Column(Float, nullable=True)
    f24_poc = Column(Float, nullable=True)
    f24_vah = Column(Float, nullable=True)
    f24_val = Column(Float, nullable=True)
    slope = Column(Float, nullable=True)
    structure_score = Column(Float, nullable=True)

    # --- SS9a execution columns -- TradePlan's additive execution layer
    # (stop_planner.py's core-zone stop, NOT this table's own `stop`
    # above, which is the r30-based analysis stop). Backfilled by a
    # SEPARATE pass (_backfill_gate_log_execution(), ledger_closing_
    # engine.py) once the matching TradePlan row itself reaches DONE --
    # decoupled from `backfilled_at` above (CampaignLog-sourced fields)
    # because a TradePlan row, especially one that goes through re-entry,
    # can easily still be open when CampaignLog has already resolved, and
    # the reverse. Two independent writers, two independent flags. ---
    execution_entry_mode = Column(String, nullable=True)
    execution_fill_time = Column(DateTime, nullable=True)
    execution_fill_price = Column(Float, nullable=True)
    execution_stop_price = Column(Float, nullable=True)
    execution_stop_basis = Column(String, nullable=True)
    execution_stop_dist_atr = Column(Float, nullable=True)
    reentry_used = Column(Boolean, nullable=True)
    execution_backfilled_at = Column(DateTime, nullable=True)

    # --- SS9a columns that are genuine, documented gaps -- present in the
    # schema (the spec names both) but never populated by any write path
    # yet. NULL here means "not yet computed," never a guess:
    #   pressure: brain/engine's pressure_checklist.py (pre-move energy
    #     scoring) has not been ported to kabroda.com at all.
    #   would_have_r: the counterfactual R a skipped (NO_PLAN/VETOED-done)
    #     day would have produced needs a real candle-level simulation
    #     against decision_engine.py's own theoretical entry/stop/targets
    #     -- not built. decision_engine.py currently zeroes entry/stop/t1/
    #     t2/t3 for non-TAKE states (see evaluate_15m_decision()'s
    #     _result()), so even the raw levels to simulate against don't
    #     exist yet without a further, deliberate change there. ---
    pressure = Column(String, nullable=True)
    would_have_r = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_gate_log_symbol_date", "symbol", "date_key"),
    )


# ---------------------------------------------------------
# EXECUTOR BOT (Stage 1, DRY-RUN only) -- Andy's request, design settled
# over a multi-day conversation with DeepSeek (Kabroda AI Brain repo
# AGENT_LOG.md, 2026-09-04). Converts an already-decided TradePlan FILLED
# row into a real exchange order on Bitunix futures. "Bot = hands, brain
# stays in Kabroda" -- these tables never feed a decision back into
# TradePlan/decision_engine.py, they only record what the executor did
# (or, in Stage 1, would have done) in response to a decision already
# made elsewhere. No ForeignKey() objects used here -- matching this
# file's own established convention (every other *_id column in this
# file is a plain Integer with a comment, not a real FK constraint).
# ---------------------------------------------------------
class ExecutorAccount(Base):
    """One row per real trading account (Andy's own Bitunix account is
    row 1; a second trader's account can be added the same way -- multi-
    account support from day one, not bolted on later). Credentials are
    stored ENCRYPTED (executor_crypto.py's Fernet helper) -- plaintext
    values never touch this table or any log."""
    __tablename__ = "executor_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)   # users.id, owner
    label = Column(String, nullable=False)                   # e.g. "andy_bitunix_main"
    exchange = Column(String, nullable=False, default="bitunix")
    mode = Column(String, nullable=False, default="DRY_RUN")  # DRY_RUN | PAPER | LIVE

    api_key_encrypted = Column(Text, nullable=True)
    api_secret_encrypted = Column(Text, nullable=True)
    credential_set_at = Column(DateTime, nullable=True)
    credential_set_by = Column(String, nullable=True)         # admin/owner email

    is_active = Column(Boolean, nullable=False, default=True)
    kill_switch_engaged = Column(Boolean, nullable=False, default=False)
    kill_switch_engaged_at = Column(DateTime, nullable=True)
    kill_switch_engaged_by = Column(String, nullable=True)
    kill_switch_reason = Column(String, nullable=True)

    # "ISOLATION", not "ISOLATED" -- matches Bitunix's own real API
    # vocabulary EXACTLY (their get_leverage_and_margin_mode endpoint's
    # marginMode field returns "ISOLATION"|"CROSS", confirmed against a
    # real account response 2026-09-05). A mismatched string here would
    # make executor_plan_builder.py's real-vs-configured margin-mode
    # check falsely reject every real trade.
    margin_mode = Column(String, nullable=False, default="ISOLATION")
    leverage_baseline = Column(Integer, nullable=False, default=10)
    max_margin_pct_of_balance = Column(Float, nullable=False, default=0.80)
    # Stage 1 placeholder -- there is no real balance query yet (no
    # exchange calls at all in Stage 1). Admin-edited so the leverage-
    # reduction math (executor_sizing.suggest_leverage()) can be built
    # and tested end to end now rather than deferred to Stage 2.
    assumed_balance_usd = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ExecutorRiskState(Base):
    """Persistent, restart-surviving compounding state -- one row per
    account (unique account_id). Andy's rule: risk_next = min(max(
    risk_last + compounding_factor*last_trade_pnl, floor), cap)."""
    __tablename__ = "executor_risk_state"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, unique=True, index=True)  # executor_accounts.id

    risk_last_usd = Column(Float, nullable=False, default=100.0)
    risk_floor_usd = Column(Float, nullable=False, default=100.0)
    risk_cap_usd = Column(Float, nullable=False, default=1000.0)
    compounding_factor = Column(Float, nullable=False, default=0.10)
    last_trade_pnl_usd = Column(Float, nullable=True)
    last_updated_from_trade_plan_id = Column(Integer, nullable=True)  # trade_plans.id, traceability only

    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ExecutorOrder(Base):
    """The 'would-place' record in Stage 1 (mode=DRY_RUN always here);
    becomes the real order record in Stage 2/3 without a schema change.
    One row per (TradePlan FILLED event) x (account that acted on it).
    entry/stop/t1/t2/t3 here are a DENORMALIZED SNAPSHOT for audit
    immutability -- TradePlan stays the one authoritative source, this
    table never overrides or feeds back into it."""
    __tablename__ = "executor_orders"

    id = Column(Integer, primary_key=True, index=True)
    trade_plan_id = Column(Integer, nullable=False, index=True)   # trade_plans.id
    account_id = Column(Integer, nullable=False, index=True)      # executor_accounts.id
    mode = Column(String, nullable=False)   # snapshot of account.mode at decision time

    symbol = Column(String, nullable=True)
    direction = Column(String, nullable=True)   # LONG | SHORT
    entry_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    t1_price = Column(Float, nullable=True)
    t2_price = Column(Float, nullable=True)
    t3_price = Column(Float, nullable=True)

    risk_dollars_used = Column(Float, nullable=True)
    stop_distance = Column(Float, nullable=True)
    qty = Column(Float, nullable=True)
    leverage_used = Column(Integer, nullable=True)
    margin_required_usd = Column(Float, nullable=True)
    liquidation_price_estimate = Column(Float, nullable=True)
    liquidation_check_passed = Column(Boolean, nullable=True)
    liquidation_check_detail = Column(String, nullable=True)
    # 2026-09-05 -- the real maintenance margin rate queried live from
    # Bitunix's get_position_tiers at decision time (never a hardcoded
    # table), folded into liquidation_price_estimate above. See
    # executor_sizing.py/executor_plan_builder.py's own headers.
    maintenance_margin_rate_used = Column(Float, nullable=True)

    # WOULD_PLACE | REJECTED | SKIPPED_KILL_SWITCH | SKIPPED_ACCOUNT_INACTIVE
    # | SKIPPED_ALREADY_IN_TRADE | ERROR
    decision = Column(String, nullable=False)
    decision_reason = Column(String, nullable=True)

    # Always NULL in Stage 1 -- populated once Stage 2/3 places real orders.
    exchange_order_id = Column(String, nullable=True)
    exchange_response_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_plan_id", "account_id", name="uq_executor_order_plan_account"),
    )


class ExecutorAuditLog(Base):
    """Append-only (insert-only by code convention -- never UPDATE/DELETE
    a row here). Every real decision point the executor makes, Stage 1 or
    later: would-place, rejected, kill-switch changes, credential changes,
    account changes. Andy's own standing rule for this project: every new
    mechanism ships with full audit tracking from day one."""
    __tablename__ = "executor_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    occurred_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    account_id = Column(Integer, nullable=True, index=True)      # executor_accounts.id
    trade_plan_id = Column(Integer, nullable=True)                # trade_plans.id
    executor_order_id = Column(Integer, nullable=True)            # executor_orders.id
    executor_mechanism_test_id = Column(Integer, nullable=True)   # executor_mechanism_tests.id

    # ORDER_WOULD_PLACE | ORDER_PLACED | ORDER_REJECTED | LIQUIDATION_CHECK_FAILED
    # | T1_PARTIAL_DETECTED | SL_MOVED_TO_BREAKEVEN | KILL_SWITCH_ENGAGED
    # | KILL_SWITCH_RELEASED | RISK_STATE_UPDATED | CREDENTIAL_SET
    # | CREDENTIAL_ROTATED | ACCOUNT_CREATED | ACCOUNT_DEACTIVATED
    # | MODE_CHANGED | ERROR | LIVE_ORDERS_ENABLED | LIVE_ORDERS_DISABLED
    # | TEST_MECHANISM_STARTED | TEST_MECHANISM_BLOCKED | TEST_ORDER_PLACED
    # | TEST_ORDER_FILL_CONFIRMED | TEST_INITIAL_TPSL_SET | TEST_PARTIAL_CLOSED
    # | TEST_SL_MOVED_TO_BREAKEVEN | TEST_POSITION_FLASH_CLOSED
    # | TEST_MECHANISM_FAILED | POSITION_CLOSED (reserved, future real Stage 3 close)
    # Stage 1 code only ever writes a subset of these -- the rest exist
    # now for forward schema compatibility with Stage 2/3, not dead weight.
    # The TEST_* values (2026-09-05) are deliberately distinct from the
    # real-production placeholders ORDER_PLACED/T1_PARTIAL_DETECTED/
    # SL_MOVED_TO_BREAKEVEN -- those stay reserved, untouched, for a real
    # future feature; every event the tiny mechanism test writes is
    # TEST_-prefixed so it can never be confused with one in this log.
    event_type = Column(String, nullable=False, index=True)
    actor = Column(String, nullable=True)   # "system" for bot-driven rows, an email for human-driven ones
    detail_json = Column(Text, nullable=True)
    message = Column(String, nullable=True)


class ExecutorGlobalConfig(Base):
    """Singleton row (config_key='executor_global'), MonitorConfig-shaped.
    The GLOBAL kill switch -- ANDed with each account's own kill_switch_
    engaged flag in executor_accounts.is_account_tradeable(); both must
    be clear for the bot to act on any account."""
    __tablename__ = "executor_global_config"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String, unique=True, nullable=False)

    global_kill_switch_engaged = Column(Boolean, nullable=False, default=False)
    global_kill_switch_engaged_at = Column(DateTime, nullable=True)
    global_kill_switch_engaged_by = Column(String, nullable=True)
    global_kill_switch_reason = Column(String, nullable=True)

    # 2026-09-05 -- persistent, default-OFF gate on real order placement
    # (Stage 2's tiny mechanism test and any future live trading). Same
    # shape/polarity as the kill switch above but the OPPOSITE meaning:
    # kill switch blocks trading when ON, this flag PERMITS real-money
    # order placement only when ON. Both this flag AND the kill switch
    # must independently allow an action for it to proceed -- see
    # executor_control.py/executor_mechanism_test.py.
    live_orders_enabled = Column(Boolean, nullable=False, default=False)
    live_orders_enabled_at = Column(DateTime, nullable=True)
    live_orders_enabled_by = Column(String, nullable=True)
    live_orders_enabled_reason = Column(String, nullable=True)

    stage_default_mode = Column(String, nullable=False, default="DRY_RUN")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ExecutorMechanismTest(Base):
    """Isolated lifecycle record for a manually-triggered, REAL-MONEY
    mechanism test of the order-placing/closing chain (Stage 2,
    2026-09-05) -- NOT a real trading decision. Deliberately has NO
    trade_plan_id and no relationship to ExecutorOrder/TradePlan at all
    (no shared unique constraint, no shared FK) -- structurally
    impossible for any existing dashboard/report that reads TradePlan/
    ExecutorOrder to ever pick this up as if it were a real trade.
    Always BTCUSDT/LONG -- this proves the mechanism, not a trading
    thesis. See executor_mechanism_test.py for the orchestration logic
    that writes these rows."""
    __tablename__ = "executor_mechanism_tests"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)   # executor_accounts.id

    symbol = Column(String, nullable=False, default="BTCUSDT")
    direction = Column(String, nullable=False, default="LONG")

    # STARTED | ORDER_PLACED | FILL_CONFIRMED | TPSL_SET | PARTIAL_CLOSED
    # | SL_MOVED_BREAKEVEN | FULLY_CLOSED | FAILED
    status = Column(String, nullable=False, index=True)

    min_trade_volume = Column(Float, nullable=True)     # snapshot from get_trading_pairs
    base_precision = Column(Integer, nullable=True)
    # Captured once at test start, not re-queried per step -- a symbol's
    # tick size doesn't legitimately drift mid-test the way leverage/
    # margin-mode/MMR can (those ARE re-queried live every time
    # elsewhere in this codebase; this is a deliberately different case).
    quote_precision = Column(Integer, nullable=True)
    qty = Column(Float, nullable=True)                    # opening qty actually sent

    exchange_order_id = Column(String, nullable=True)
    exchange_client_id = Column(String, nullable=True)
    place_order_response_json = Column(Text, nullable=True)

    position_id = Column(String, nullable=True)
    fill_price = Column(Float, nullable=True)             # real avgOpenPrice read back

    initial_tp_price = Column(Float, nullable=True)
    initial_sl_price = Column(Float, nullable=True)
    tpsl_exchange_order_id = Column(String, nullable=True)
    tpsl_response_json = Column(Text, nullable=True)

    partial_close_pct = Column(Float, nullable=True)
    partial_close_qty = Column(Float, nullable=True)
    partial_close_exchange_order_id = Column(String, nullable=True)
    partial_close_response_json = Column(Text, nullable=True)

    # Deliberately the exact fill price, fee-naive -- correct for
    # proving the mechanism, not true PnL-neutral breakeven. See
    # executor_mechanism_test.py's own docstring before reusing this
    # simplification in a real future feature.
    breakeven_sl_price = Column(Float, nullable=True)
    sl_breakeven_exchange_order_id = Column(String, nullable=True)
    sl_breakeven_response_json = Column(Text, nullable=True)

    flash_close_response_json = Column(Text, nullable=True)

    error_detail = Column(Text, nullable=True)            # last exception message if FAILED
    started_by = Column(String, nullable=True)             # actor email

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
