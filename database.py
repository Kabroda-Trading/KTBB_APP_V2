import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. SETUP DATABASE CONNECTION
# (This logic was missing in the previous version, causing the crash)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback for local testing if env var is missing
    DATABASE_URL = "sqlite:///./kabroda.db"

# Fix for Render's Postgres URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. DEFINE USER MODEL
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=False)
    # KEEPING YOUR EXACT COLUMN NAME:
    password_hash = Column(String, nullable=False)

    # --- NEW COLUMN ---
    username = Column(String, nullable=True)
    # ------------------

    # KEEPING YOUR EXACT COLUMN NAMES:
    tier = Column(String, default="tier1_manual", nullable=False)
    session_tz = Column(String, default="UTC", nullable=False)

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# 3. HELPER FUNCTIONS
# (These are what main.py is looking for)
def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()