# database.py
import os
from datetime import datetime
from typing import Generator

from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")

# Render uses "postgres://", SQLAlchemy needs "postgresql+psycopg://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# --- THE FIX: Define connect_args correctly ---
if "sqlite" in DATABASE_URL:
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}
# -----------------------------------------------

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    username = Column(String(255), nullable=True)          # Added missing column
    tradingview_id = Column(String(255), nullable=True)    # Added missing column
    subscription_status = Column(String(50), default="active") # Added missing column
    is_admin = Column(Boolean, default=False, nullable=False)
    operator_flex = Column(Boolean, default=False)         # Added missing column
    session_tz = Column(String(50), default="America/New_York") # Added missing column
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

def init_db() -> None:
    """Create tables."""
    Base.metadata.create_all(bind=engine)

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()