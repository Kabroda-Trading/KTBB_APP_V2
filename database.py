# database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

def _normalize_database_url(url: str) -> str:
    """
    Render often provides DATABASE_URL starting with 'postgres://' (legacy).
    SQLAlchemy expects 'postgresql://'.
    We also force psycopg (v3) driver: 'postgresql+psycopg://'
    """
    if not url:
        return ""

    # Render legacy scheme
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # Force psycopg v3 driver if it's plain postgresql://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", "").strip())

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Add it in Render environment variables.")

# For Postgres, no sqlite connect_args needed
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    # Creates tables if they don't exist
    Base.metadata.create_all(bind=engine)
