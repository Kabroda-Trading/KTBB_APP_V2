# auth.py
import os
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

# Exported constants (main.py imports these)
COOKIE_NAME = os.environ.get("COOKIE_NAME", "ktbb_session_user")

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    SessionMiddleware stores session data in request.session.
    We keep the logged-in user dict under request.session["user"].
    """
    try:
        return request.session.get("user")  # type: ignore[attr-defined]
    except Exception:
        return None

def require_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency for protected pages / APIs.
    Raises 401 if not logged in.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user
