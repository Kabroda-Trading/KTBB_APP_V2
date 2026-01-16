# database.py
# =============================================================================
# KABRODA — Database Layer (SQLAlchemy 2.x + psycopg v3)
# - Provides: init_db, get_db, UserModel
# - Works on Render Postgres (DATABASE_URL) and local SQLite fallback
# =============================================================================

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Generator, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_database_url(url: str) -> str:
    """
    Render commonly provides DATABASE_URL like:
      postgres://user:pass@host:5432/dbname
    SQLAlchemy wants:
      postgresql+psycopg://user:pass@host:5432/dbname
    """
    url = (url or "").strip()
    if not url:
        return url

    # Render/Heroku style
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        # Ensure psycopg v3 driver is used (NOT psycopg2)
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    return url


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", ""))

# Local fallback if DATABASE_URL is not set
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./app.db"

IS_SQLITE = DATABASE_URL.startswith("sqlite")


# -----------------------------------------------------------------------------
# SQLAlchemy Base + Engine + Session factory
# -----------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Login identity
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # Auth (argon2 hash lives here)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)

    # Permissions
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


# -----------------------------------------------------------------------------
# Public API expected by main.py
# -----------------------------------------------------------------------------
def init_db() -> None:
    """
    Create tables if they don't exist.
    Safe to call on startup.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency.
    Usage: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
