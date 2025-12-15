# auth.py
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext

# -----------------------------
# Public constants (imported by main.py)
# -----------------------------
COOKIE_NAME = os.environ.get("COOKIE_NAME", "ktbb_session")
SESSION_USER_KEY = "user"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Returns the session user dict or None.
    SessionMiddleware must be enabled in main.py.
    """
    try:
        user = request.session.get(SESSION_USER_KEY)
        if isinstance(user, dict):
            return user
    except Exception:
        pass
    return None


def require_user(request: Request) -> Dict[str, Any]:
    """
    Dependency used by protected routes.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


@router.post("/logout")
def logout(request: Request):
    request.session.pop(SESSION_USER_KEY, None)
    return {"ok": True}


@router.post("/login")
async def login_post(request: Request):
    """
    Minimal session login:
    - accepts form OR json
    - sets request.session['user'] to a dict
    Replace credential checking with your DB check if you want.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    email = None
    password = None

    if "application/json" in content_type:
        data = await request.json()
        email = (data.get("email") or "").strip()
        password = (data.get("password") or "").strip()
    else:
        form = await request.form()
        email = (form.get("email") or "").strip()
        password = (form.get("password") or "").strip()

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    # -----
    # TODO: Replace this with your DB lookup.
    # For now, allow the seeded admin pattern or any non-empty credentials.
    # -----
    tier = "elite"
    user = {"email": email, "tier": tier, "timezone": "America/Chicago"}

    request.session[SESSION_USER_KEY] = user

    # If this was a browser form post, go to suite
    if "application/json" not in content_type:
        return RedirectResponse(url="/suite", status_code=303)

    return {"ok": True, "user": user}
