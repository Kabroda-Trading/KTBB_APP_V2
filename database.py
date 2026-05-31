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
    t2 = Column(Float, nullable=False)
    t3 = Column(Float, nullable=False)

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
    target_hit = Column(String, nullable=True)   # T1 | T2 | T3 | STOP — written by outcome tracker
    structure_reasoning = Column(String, nullable=True)  # JSON: Trade Structure Analyst audit trail

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

    # STAND_DOWN / GRADE_A / GRADE_B / MAS_APPROVED / MAS_REJECTED / INTEL_AUDIT
    decision_type = Column(String, nullable=False)

    confluence_score = Column(Integer, nullable=True, default=0)
    confluence_direction = Column(String, nullable=True, default="NEUTRAL")
    energy_status = Column(String, nullable=True, default="BUILDING")
    kinematic_grade = Column(String, nullable=True)   # PRIMED | OVEREXTENDED | TANGLED | UNKNOWN

    bo_price = Column(Float, nullable=True)
    bd_price = Column(Float, nullable=True)
    asset_price = Column(Float, nullable=True)

    session_date = Column(String, nullable=True)
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