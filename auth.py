# auth.py
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import SessionModel, UserModel, get_db

# Single source of truth for the login session cookie name
COOKIE_NAME = "ktbb_session"

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    return pwd_context.hash(password)


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def create_user(db: Session, email: str, password: str, tier, session_tz: str = "auto") -> UserModel:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email.")

    existing = db.query(UserModel).filter(UserModel.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    u = UserModel(
        email=email,
        password_hash=_hash_password(password),
        tier=getattr(tier, "value", str(tier)),
        session_tz=(session_tz or "auto"),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def authenticate_user(db: Session, email: str, password: str) -> Optional[UserModel]:
    email = (email or "").strip().lower()
    u = db.query(UserModel).filter(UserModel.email == email).first()
    if not u:
        return None
    if not _verify_password(password, u.password_hash):
        return None
    return u


def create_session_token(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    s = SessionModel(user_id=user_id, token=token)
    db.add(s)
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    if not token:
        return
    db.query(SessionModel).filter(SessionModel.token == token).delete()
    db.commit()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in.")

    sess = db.query(SessionModel).filter(SessionModel.token == token).first()
    if not sess:
        raise HTTPException(status_code=401, detail="Not logged in.")

    u = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
    if not u:
        raise HTTPException(status_code=401, detail="Not logged in.")

    # Convert DB user → membership.User (what your app expects)
    from membership import Tier, User as MembershipUser

    try:
        tier_enum = Tier(u.tier)
    except Exception:
        tier_enum = Tier.TIER2_SINGLE_AUTO

    return MembershipUser(
        id=u.id,
        email=u.email,
        tier=tier_enum,
        session_tz=getattr(u, "session_tz", "auto"),
    )
