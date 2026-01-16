# database.py
# ------------------------------------------------------------------------------
# Kabroda DB Layer (SQLite + SQLAlchemy)
# Exposes:
#   - init_db()
#   - get_db()
#   - UserModel
# ------------------------------------------------------------------------------

from __future__ import annotations

import os
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kabroda.db")

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"

connect_args = {}
if DATABASE_URL.startswith("sqlite:///"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # Core identity
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # Account + UI profile fields your main.py expects to exist / may update
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Subscription / membership (keep loose — your billing.py can manage meaning)
    subscription_end = Column(DateTime, nullable=True)
    plan = Column(String, nullable=True)

    # Preferences
    session_tz = Column(String, nullable=True)

    # “Best-effort migrations” in main.py try to add these; defining here prevents drift
    username = Column(String, nullable=True)
    tradingview_id = Column(String, nullable=True)
    operator_flex = Column(Boolean, default=False, nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
