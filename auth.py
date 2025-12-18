# auth.py
import os
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature

# Optional password verification (only used if DB auth is enabled)
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
# Session helpers (existing behavior)
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
# Compatibility wrappers (older main.py names)
# -----------------------------------------------------------------------------
def require_session_user(request: Request) -> Dict[str, Any]:
    return require_user(request)


def set_session_user(request: Request, user: Dict[str, Any]) -> None:
    set_user_session(request, user)


# -----------------------------------------------------------------------------
# DB auth helpers + seed-admin fallback (this is what fixes your login)
# -----------------------------------------------------------------------------
def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user_by_email(*args, **kwargs):
    """
    Supports both:
      - get_user_by_email(db=db, email=..., UserModel=UserModel)
      - get_user_by_email(db, email, UserModel)
    """
    db = kwargs.get("db")
    email = kwargs.get("email")
    UserModel = kwargs.get("UserModel") or kwargs.get("user_model")

    # positional fallback: (db, email, UserModel)
    if db is None and len(args) >= 1:
        db = args[0]
    if email is None and len(args) >= 2:
        email = args[1]
    if UserModel is None and len(args) >= 3:
        UserModel = args[2]

    if db is None or UserModel is None:
        return None

    try:
        return db.query(UserModel).filter(UserModel.email == _normalize_email(str(email or ""))).first()
    except Exception:
        return None


def _seed_admin_auth(email: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Works even if DB is down / main.py isn't passing db/UserModel.
    """
    seed_email = _normalize_email(os.getenv("SEED_ADMIN_EMAIL", ""))
    seed_pw = os.getenv("SEED_ADMIN_PASSWORD", "") or ""

    if not seed_email or not seed_pw:
        return None

    if _normalize_email(email) == seed_email and password == seed_pw:
        return {
            "email": seed_email,
            "tier": os.getenv("SEED_ADMIN_TIER", "Elite"),
            "id": "seed-admin",
            "is_admin": True,
        }
    return None


def authenticate_user(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Backward compatible on purpose. Supports:
      - authenticate_user(email=..., password=...)
      - authenticate_user(email=..., password=..., db=db, UserModel=UserModel)
      - authenticate_user(db=db, email=..., password=..., UserModel=UserModel)
      - authenticate_user(db, email, password)
    """
    try:
        email = kwargs.get("email")
        password = kwargs.get("password")

        # positional legacy: (db, email, password)
        if (email is None or password is None) and len(args) >= 3:
            email = args[1]
            password = args[2]

        email = _normalize_email(str(email or ""))
        password = str(password or "")

        if not email or not password:
            return None

        # 1) Always allow seed-admin login (prevents 401 drift)
        seed_user = _seed_admin_auth(email, password)
        if seed_user:
            return seed_user

        # 2) DB auth if provided
        db = kwargs.get("db")
        UserModel = kwargs.get("UserModel") or kwargs.get("user_model")

        # positional db fallback: authenticate_user(db, email=..., password=...)
        if db is None and len(args) >= 1:
            db = args[0]

        if db is None or UserModel is None:
            return None

        u = get_user_by_email(db=db, email=email, UserModel=UserModel)
        if not u:
            return None

        # Try common hash field names
        hash_val = (
            getattr(u, "password_hash", None)
            or getattr(u, "hashed_password", None)
            or getattr(u, "password", None)
        )
        if not hash_val or _PWD_CTX is None:
            return None

        if not _PWD_CTX.verify(password, str(hash_val)):
            return None

        return {
            "email": getattr(u, "email", email),
            "tier": getattr(u, "tier", "Free"),
            "id": str(getattr(u, "id", "")) or None,
            "is_admin": bool(getattr(u, "is_admin", False)),
        }
    except Exception:
        return None


def registration_disabled() -> bool:
    return (os.getenv("REGISTRATION_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on")
