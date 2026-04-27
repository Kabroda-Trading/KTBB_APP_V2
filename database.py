# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
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

# ---------------------------------------------------------
# EXISTING USER MODEL (DO NOT ALTER)
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
    
    # Internal app settings
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
    
    source = Column(String, nullable=False)      # e.g., "4H_PIVOT", "1H_PIVOT"
    level_type = Column(String, nullable=False)  # e.g., "SUPPLY", "DEMAND"
    price = Column(Float, nullable=False)
    
    # 1 = Immortal Guardrail (4H), 2 = Intraday Dotted Line (1H)
    permanence_class = Column(Integer, nullable=False)
    
    # 1.0 = Normal, >1.0 = High Volume Node
    heat_multiplier = Column(Float, default=1.0)
    
    # Active state for unmitigated trauma
    active = Column(Boolean, default=True)

# ---------------------------------------------------------
# NEW: PERMANENT SESSION LOCKS (ANTI-AMNESIA)
# ---------------------------------------------------------
class SessionLock(Base):
    __tablename__ = "session_locks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=False)
    date_key = Column(String, index=True, nullable=False)
    lock_time = Column(Integer, nullable=False)
    
    # Stores the entire compiled SSE packet as a JSON string
    packet_data = Column(String, nullable=False)