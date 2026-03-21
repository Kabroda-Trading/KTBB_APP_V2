# database.py
import os
from datetime import datetime
from typing import Generator
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")

# Render postgres formatting fix
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)        
    last_name = Column(String(255), nullable=True)         
    tradingview_id = Column(String(255), nullable=True)    
    subscription_status = Column(String(50), default="inactive") 
    is_admin = Column(Boolean, default=False, nullable=False)
    operator_flex = Column(Boolean, default=False)         
    session_tz = Column(String(50), default="America/New_York") 
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    level = Column(String(20), default="INFO") 
    component = Column(String(50), nullable=False) 
    message = Column(String(500), nullable=False)
    resolved = Column(Boolean, default=False)

def init_db() -> None:
    """Creates tables and safely injects missing columns with STRICT logging."""
    Base.metadata.create_all(bind=engine)
    
    # Updated '0' to 'FALSE' to ensure Postgres compatibility
    patches = [
        "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users ADD COLUMN operator_flex BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN first_name VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN last_name VARCHAR(255)"
    ]
    
    for patch in patches:
        try:
            with engine.begin() as conn:
                conn.execute(text(patch))
                print(f"✅ DB PATCH SUCCESS: {patch}")
        except Exception as e:
            # We catch the error so the server doesn't crash, BUT we print it so we can see it!
            error_str = str(e).lower()
            if "duplicate column" in error_str or "already exists" in error_str:
                print(f"⏩ DB PATCH SKIPPED (Already exists): {patch}")
            else:
                print(f"❌ DB PATCH FAILED: {patch} | EXACT ERROR: {e}")

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()