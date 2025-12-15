# auth.py
import os
import json
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature

# Cookie name used by older versions / some UI code
COOKIE_NAME = os.environ.get("COOKIE_NAME", "ktbb_session")

# Used to sign legacy cookie if needed
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
SERIALIZER_SALT = os.environ.get("SESSION_SALT", "ktbb-session")

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET, salt=SERIALIZER_SALT)

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Primary source of truth: Starlette SessionMiddleware (request.session).
    Fallback: legacy signed cookie COOKIE_NAME.
    """
    # 1) SessionMiddleware user
    try:
        user = request.session.get("user")  # type: ignore[attr-defined]
        if isinstance(user, dict) and user.get("email"):
            return user
    except Exception:
        pass

    # 2) Legacy cookie fallback
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None

    try:
        data = _serializer().loads(raw, max_age=60 * 60 * 24 * 14)  # 14 days
        if isinstance(data, dict) and data.get("email"):
            # Hydrate session for future requests
            try:
                request.session["user"] = data  # type: ignore[attr-defined]
            except Exception:
                pass
            return data
    except BadSignature:
        return None
    except Exception:
        return None

    return None

def require_user(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user

def set_user_session(request: Request, user: Dict[str, Any]) -> None:
    """Store in SessionMiddleware session."""
    request.session["user"] = user  # type: ignore[attr-defined]

def clear_user_session(request: Request) -> None:
    try:
        request.session.pop("user", None)  # type: ignore[attr-defined]
    except Exception:
        pass

def make_legacy_cookie_value(user: Dict[str, Any]) -> str:
    """If you still want to set COOKIE_NAME for older UI code."""
    return _serializer().dumps(user)
