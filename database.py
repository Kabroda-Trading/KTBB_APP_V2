from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base

# Create the Base class if it's not already imported/created
Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # --- NEW COLUMN (The only change) ---
    username = Column(String, nullable=True)
    # ------------------------------------

    # membership / app fields (Keep exactly as you had them)
    tier = Column(String, default="tier1_manual", nullable=False)
    session_tz = Column(String, default="UTC", nullable=False)

    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)