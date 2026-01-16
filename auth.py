# auth.py
import os
import hmac
import hashlib
from dataclasses import dataclass
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db, UserModel, init_db

router = APIRouter()

SESSION_KEY = "kabroda_user_id"


def _pbkdf2_hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return dk.hex()


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = _pbkdf2_hash_password(password, salt)
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        candidate = _pbkdf2_hash_password(password, salt)
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def require_session_user(request: Request) -> int:
    """
    Returns the user_id from session or raises 401.
    This is what your main.py expects.
    """
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user_id)


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def ensure_bootstrap_admin(db: Session) -> None:
    """
    Optional: create a default admin if ADMIN_EMAIL and ADMIN_PASSWORD are set.
    Safe to run on every boot.
    """
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD") or ""
    if not admin_email or not admin_password:
        return

    existing = db.query(UserModel).filter(UserModel.email == admin_email).first()
    if existing:
        return

    u = UserModel(
        email=admin_email,
        password_hash=hash_password(admin_password),
        is_admin=True,
    )
    db.add(u)
    db.commit()


@router.get("/login")
def login_page(request: Request):
    # Simple built-in form so you always have a way in
    return """
    <html><body style="font-family: Arial; max-width: 520px; margin: 40px auto;">
      <h2>Kabroda Login</h2>
      <form method="post" action="/login">
        <label>Email</label><br/>
        <input name="email" style="width: 100%; padding: 8px;"/><br/><br/>
        <label>Password</label><br/>
        <input name="password" type="password" style="width: 100%; padding: 8px;"/><br/><br/>
        <button type="submit" style="padding: 10px 14px;">Login</button>
      </form>
    </body></html>
    """


@router.post("/login")
def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    init_db()
    ensure_bootstrap_admin(db)

    em = (email or "").strip().lower()
    u = db.query(UserModel).filter(UserModel.email == em).first()
    if not u or not verify_password(password or "", u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    request.session[SESSION_KEY] = int(u.id)
    return RedirectResponse(url="/suite", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse(url="/login", status_code=303)
