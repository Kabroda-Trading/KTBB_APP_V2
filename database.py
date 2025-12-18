# database.py
from __future__ import annotations

import os
import sqlite3
from typing import Generator

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session


# Database URL
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ktbb_app.db")

# Render Postgres URLs are often "postgresql://..." (or sometimes "postgres://...")
# Tell SQLAlchemy to use psycopg (v3) explicitly.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)


# -------------------------
# Models
# -------------------------
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)

    # Tier values are stored as strings like: tier2_single_auto / tier3_multi_gpt
    tier = Column(String, nullable=False, server_default="tier2_single_auto")

    # Session timezone key (IANA), e.g. "America/New_York"
    session_tz = Column(String, nullable=False, server_default="UTC")

    # --- Stripe fields (IMPORTANT: must be in the ORM model, not just migrations)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SessionModel(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# -------------------------
# FastAPI dependency
# -------------------------
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------
# Lightweight migrations (SQLite)
# -------------------------
def _sqlite_column_exists(table: str, col: str) -> bool:
    if not DATABASE_URL.startswith("sqlite"):
        return False

    db_path = DATABASE_URL.replace("sqlite:///", "")
    if db_path.startswith("./"):
        db_path = db_path[2:]

    if not os.path.exists(db_path):
        return False

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table});")
        cols = [r[1] for r in cur.fetchall()]
        return col in cols
    finally:
        conn.close()


def _sqlite_add_column(table: str, ddl: str) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    db_path = DATABASE_URL.replace("sqlite:///", "")
    if db_path.startswith("./"):
        db_path = db_path[2:]

    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl};")
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """
    Create tables + apply minimal migrations so older ktbb_app.db files keep working.
    """
    Base.metadata.create_all(bind=engine)

    # Minimal migrations for older DBs
    if _sqlite_column_exists("users", "tier") is False:
        _sqlite_add_column("users", "tier TEXT NOT NULL DEFAULT 'tier2_single_auto'")

    if _sqlite_column_exists("users", "session_tz") is False:
        _sqlite_add_column("users", "session_tz TEXT NOT NULL DEFAULT 'UTC'")

    # Stripe columns
    if _sqlite_column_exists("users", "stripe_customer_id") is False:
        _sqlite_add_column("users", "stripe_customer_id TEXT")

    if _sqlite_column_exists("users", "stripe_subscription_id") is False:
        _sqlite_add_column("users", "stripe_subscription_id TEXT")

    if _sqlite_column_exists("users", "stripe_price_id") is False:
        _sqlite_add_column("users", "stripe_price_id TEXT")

    if _sqlite_column_exists("users", "subscription_status") is False:
        _sqlite_add_column("users", "subscription_status TEXT")
