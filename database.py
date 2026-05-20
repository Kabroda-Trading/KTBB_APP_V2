# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")

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