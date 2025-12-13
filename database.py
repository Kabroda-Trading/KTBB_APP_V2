# database.py
from __future__ import annotations

import os
import sqlite3
from typing import Generator, Optional

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    func,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session


# -------------------------
# Database URL
# -------------------------
# Default is local sqlite file in your repo folder
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ktbb_app.db")

# For SQLite we need this flag
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

Base = declarative_base()


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
    """
    SQLite-only helper.
    """
    # DATABASE_URL is like sqlite:///./ktbb_app.db
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


def _sqlite_add_column(table: str, col: str, ddl: str) -> None:
    """
    SQLite-only ADD COLUMN helper.
    ddl should be like: "session_tz TEXT NOT NULL DEFAULT 'UTC'"
    """
    if not DATABASE_URL.startswith("sqlite"):
        return

    db_path = DATABASE_URL.replace("sqlite:///", "")
    if db_path.startswith("./"):
        db_path = db_path[2:]

    # If DB doesn't exist yet, skip (tables will be created fresh)
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl};")
        conn.commit()
    finally:
        conn.close()

def _ensure_user_columns(engine):
    # SQLite simple migration: add missing columns
    import sqlite3
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return

    # engine.url like sqlite:///./ktbb_app.db
    db_path = url.split("///")[-1]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cur.fetchall()}

        def add(col_name, sql_type):
            if col_name not in cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {sql_type}")

        add("stripe_customer_id", "TEXT")
        add("stripe_subscription_id", "TEXT")
        add("stripe_price_id", "TEXT")
        add("subscription_status", "TEXT")

        conn.commit()
    finally:
        conn.close()



def init_db() -> None:
    """
    Create tables + apply minimal migrations so older ktbb_app.db files keep working.
    """
    Base.metadata.create_all(bind=engine)

    _ensure_user_columns(engine)

    # Minimal migrations for older DBs:
    # If you already created users table earlier, it might be missing new cols.
    if _sqlite_column_exists("users", "tier") is False:
        _sqlite_add_column("users", "tier", "tier TEXT NOT NULL DEFAULT 'tier2_single_auto'")

    if _sqlite_column_exists("users", "session_tz") is False:
        _sqlite_add_column("users", "session_tz", "session_tz TEXT NOT NULL DEFAULT 'UTC'")

    # Sessions table might not exist in very old versions – Base.metadata.create_all handles that.
