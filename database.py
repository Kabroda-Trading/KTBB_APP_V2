# database.py
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
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

Base = declarative_base()


def _normalize_db_url(url: str) -> str:
    """
    Render often provides DATABASE_URL like:
      - postgres://user:pass@host:port/db
      - postgresql://user:pass@host:port/db

    We force SQLAlchemy to use psycopg (v3) driver:
      - postgresql+psycopg://...
    """
    u = (url or "").strip()
    if not u:
        return u

    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]

    # If no explicit driver, default to psycopg (v3)
    if u.startswith("postgresql://") and "+psycopg" not in u and "+psycopg2" not in u:
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)

    return u


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))

if not DATABASE_URL:
    # Local dev fallback (optional). On Render you should always have DATABASE_URL set.
    DATABASE_URL = "sqlite:///./local.db"

# SQLite needs special connect args, Postgres does not.
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # membership / app fields (these appear referenced throughout your code)
    tier = Column(String, default="tier1_manual", nullable=False)
    session_tz = Column(String, default="UTC", nullable=False)

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
