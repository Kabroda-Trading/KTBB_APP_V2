# auth.py
import os
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature

# Optional password verification (only used if you pass db/UserModel and user has a hash)
try:
    from passlib.context import CryptContext
    _PWD_CTX = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:
    _PWD_CTX = None


# Cookie name used by older versions / some UI code
COOKIE_NAME = os.environ.get("COOKIE_NAME", "ktbb_session")

# Used to sign legacy cookie if needed
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
SERIALIZER_SALT = os.environ.get("SESSION_SALT", "ktbb-session")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET, salt=SERIALIZER_SALT)


# -----------------------------------------------------------------------------
# Session helpers (your existing behavior)
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# Compatibility wrappers (so main.py can call older names safely)
# -----------------------------------------------------------------------------
def require_session_user(request: Request) -> Dict[str, Any]:
    # older name used by some versions of main.py
    return require_user(request)


def set_session_user(request: Request, user: Dict[str, Any]) -> None:
    # older name used by some versions of main.py
    set_user_session(request, user)


# -----------------------------------------------------------------------------
# DB auth helpers (ONLY used if caller passes db + UserModel)
# -----------------------------------------------------------------------------
def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user_by_email(*, db=None, email: str = "", UserModel=None, **_kwargs):
    if db is None or UserModel is None:
        return None
    try:
        return db.query(UserModel).filter(UserModel.email == _normalize_email(email)).first()
    except Exception:
        return None


def authenticate_user(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Flexible signature on purpose.
    Supports calls like:
      - authenticate_user(email=..., password=...)
      - authenticate_user(email=..., password=..., db=db, UserModel=UserModel)
      - authenticate_user(db=db, email=..., password=..., UserModel=UserModel)

    Returns a SAFE dict if authenticated, else None.
    Never raises (so login route won't 500).
    """
    try:
        email = kwargs.get("email")
        password = kwargs.get("password")

        # Support positional legacy patterns if they exist
        # e.g. authenticate_user(db, email, password)
        if (email is None or password is None) and len(args) >= 3:
            email = args[1]
            password = args[2]

        email = _normalize_email(str(email or ""))
        password = str(password or "")

        if not email or not password:
            return None

        db = kwargs.get("db", None)
        UserModel = kwargs.get("UserModel", None) or kwargs.get("user_model", None)

        # If no DB provided, we can't validate here (seed-admin is handled in main.py)
        if db is None or UserModel is None:
            return None

        u = get_user_by_email(db=db, email=email, UserModel=UserModel)
        if not u:
            return None

        # Try common hash field names
        hash_val = getattr(u, "password_hash", None) or getattr(u, "hashed_password", None) or getattr(u, "password", None)
        if not hash_val or _PWD_CTX is None:
            return None

        if not _PWD_CTX.verify(password, str(hash_val)):
            return None

        # Return safe fields only
        return {
            "email": getattr(u, "email", email),
            "tier": getattr(u, "tier", "Free"),
            "id": str(getattr(u, "id", "")) or None,
            "is_admin": bool(getattr(u, "is_admin", False)),
        }
    except Exception:
        return None


def registration_disabled() -> bool:
    # If you later want to disable signups without code changes
    return (os.getenv("REGISTRATION_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on")
