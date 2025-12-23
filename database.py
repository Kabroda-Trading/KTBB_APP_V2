import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------------------------------------------------------
# 1. DATABASE CONNECTION
# ---------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback for local testing
    DATABASE_URL = "sqlite:///./kabroda.db"

# FIX FOR RENDER: Use 'postgresql+psycopg://' to match the installed driver (v3)
# The default 'postgresql://' looks for psycopg2, which you don't have.
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------
# 2. USER MODEL
# ---------------------------------------------------------
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # --- NEW COLUMN ---
    username = Column(String, nullable=True)
    # ------------------

    # Membership fields
    tier = Column(String, default="tier1_manual", nullable=False)
    session_tz = Column(String, default="UTC", nullable=False)

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# ---------------------------------------------------------
# 3. HELPER FUNCTIONS
# ---------------------------------------------------------
def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()