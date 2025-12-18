# auth.py — DB-backed auth (no drift)

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status, Request
from sqlalchemy.orm import Session

from database import UserModel


_ph = PasswordHasher()


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    pw = (password or "").strip()
    if len(pw) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    return _ph.hash(pw)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, (password or "").strip())
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def get_user_by_email(db: Session, email: str) -> Optional[UserModel]:
    return db.query(UserModel).filter(UserModel.email == _norm_email(email)).first()


def create_user(db: Session, email: str, password: str) -> UserModel:
    email_n = _norm_email(email)
    if not email_n or "@" not in email_n:
        raise HTTPException(status_code=400, detail="Invalid email.")

    if get_user_by_email(db, email_n):
        raise HTTPException(status_code=400, detail="Email already registered.")

    u = UserModel(
        email=email_n,
        password_hash=hash_password(password),
        tier="tier1_manual",      # start free/manual
        session_tz="UTC",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def authenticate_user(db: Session, email: str, password: str) -> Optional[UserModel]:
    u = get_user_by_email(db, email)
    if not u:
        return None
    if not verify_password(password, u.password_hash):
        return None
    return u


def set_user_session(request: Request, user: UserModel, is_admin: bool = False) -> None:
    request.session["user"] = {
        "id": int(user.id),
        "email": user.email,
        "tier": user.tier,
        "session_tz": user.session_tz,
        "is_admin": bool(is_admin),
    }


def clear_user_session(request: Request) -> None:
    try:
        request.session.clear()
    except Exception:
        pass


def require_session_user(request: Request) -> Dict[str, Any]:
    u = request.session.get("user")
    if not isinstance(u, dict) or not u.get("email"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    return u


def registration_disabled() -> bool:
    v = os.getenv("DISABLE_REGISTRATION", "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")