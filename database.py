"""
database.py

SQLAlchemy database layer.

Your Render logs showed:
    ImportError: cannot import name 'init_db' from 'database'

`main.py` expects these exports:
- init_db()
- get_db()  (FastAPI dependency)
- UserModel (SQLAlchemy model)

This file provides those, using Postgres on Render (DATABASE_URL) and falling
back to SQLite for local dev.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _normalize_db_url(url: Optional[str]) -> str:
    """Make SQLAlchemy use psycopg (v3) on Render and fix legacy schemes."""
    if not url:
        return "sqlite:///./app.db"

    u = url.strip()

    # Render sometimes provides postgres://... (legacy)
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]

    # Prefer psycopg v3 driver if we have postgres.
    if u.startswith("postgresql://") and "+" not in u:
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)

    return u


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL"))

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
