# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, text
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

# ---------------------------------------------------------
# EXISTING USER MODEL
# ---------------------------------------------------------
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    username = Column(String)
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

# ---------------------------------------------------------
# MTF CONFLUENCE READINGS (MORNING BRIEF HISTORY)
# ---------------------------------------------------------
class MtfReading(Base):
    __tablename__ = "mtf_readings"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    confluence_score = Column(Integer, nullable=False, default=0)
    confluence_direction = Column(String, nullable=False, default="NEUTRAL")
    energy_status = Column(String, nullable=False, default="BUILDING")
    timeframe_data = Column(String, nullable=True)
    bo_price = Column(Float, nullable=True)
    bd_price = Column(Float, nullable=True)
    asset_price = Column(Float, nullable=True)
    session_date = Column(String, nullable=True)

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


# ---------------------------------------------------------
# JEWEL SNAPSHOT LOG (PHASE 2 — 6 DAILY TIMEFRAME SNAPSHOTS)
# Captures JEWEL state across all 5 timeframes at 6 fixed
# session transitions per day. Senior Analyst reads the last
# 6 entries (24 hours) before writing the morning brief.
#
# session_label values:
#   NY_OPEN, NY_MIDDAY, NY_CLOSE,
#   ASIA_OPEN, ASIA_MIDDAY, LONDON_OPEN
#
# tf_*_state fields: JSON strings with keys:
#   direction, zone, momentum, adx_strength
# ---------------------------------------------------------
class JewelSnapshotLog(Base):
    __tablename__ = "jewel_snapshot_log"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, index=True, default="BTC/USDT")
    timestamp = Column(DateTime, nullable=False, index=True)

    session_label = Column(String, nullable=False)   # NY_OPEN | NY_MIDDAY | ...

    asset_price = Column(Float, nullable=False)

    tf_15m_state = Column(String, nullable=True)     # JSON: direction/zone/momentum/adx
    tf_1h_state = Column(String, nullable=True)
    tf_4h_state = Column(String, nullable=True)
    tf_daily_state = Column(String, nullable=True)
    tf_weekly_state = Column(String, nullable=True)

    # --- PHASE 3C: scanner top-level context ---
    confluence_score      = Column(Integer, nullable=True)
    dominant_direction    = Column(String, nullable=True)
    conviction            = Column(String, nullable=True)
    any_tf_compressed     = Column(Boolean, nullable=True)
    any_tf_overextended   = Column(Boolean, nullable=True)
    any_tf_divergence     = Column(Boolean, nullable=True)
    jewel_gate_open       = Column(Boolean, nullable=True)
    jewel_conviction      = Column(String, nullable=True)
    jewel_exit_warning    = Column(Boolean, nullable=True)
    jewel_divergence_warning = Column(Boolean, nullable=True)
    jewel_signal_summary  = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------
# NEWSLETTER LOG (PHASE 6 — CONTENT PUBLISHING ENGINE)
# Stores the Publisher agent's daily newsletter output.
# publish_status lifecycle: DRAFT → PUBLISHED | FAILED
# ghost_post_id populated after Ghost API publish step.
# New table — created by Base.metadata.create_all(), no ALTER needed.
# ---------------------------------------------------------
class NewsletterLog(Base):
    __tablename__ = "newsletter_log"

    id            = Column(Integer, primary_key=True, index=True)
    symbol        = Column(String, index=True, nullable=False)
    date_key      = Column(String, index=True, nullable=False)
    session_id    = Column(String, nullable=False)

    approval_status = Column(String, nullable=True)    # APPROVED / REJECTED / WAITING_FOR_15M
    headline        = Column(String, nullable=True)
    newsletter_md   = Column(String, nullable=True)    # Full Markdown article
    newsletter_html = Column(String, nullable=True)    # Reserved for Ghost publish step

    publish_status = Column(String, default="DRAFT", nullable=False)  # DRAFT / PUBLISHED / FAILED
    published_at   = Column(DateTime, nullable=True)
    ghost_post_id  = Column(String, nullable=True)     # Populated after Ghost API publish

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


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