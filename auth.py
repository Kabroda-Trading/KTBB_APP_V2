# auth.py
import os
from fastapi import HTTPException, Request

# ---- Compatibility constants (used by older main.py versions) ----
COOKIE_NAME = os.getenv("COOKIE_NAME", "ktbb_session")

# If you already have a DB layer, we'll try to use it.
# If not, we fall back to a tiny sqlite lookup that won't crash imports.
def _try_db_lookup_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort user lookup.
    1) Try your project's database.py helpers/models if present.
    2) Fallback to sqlite3 against KTBB_DB_PATH.
    Returns a dict-like user object or None.
    """
    # ---- Try your database.py (SQLAlchemy-style) if present ----
    try:
        import database  # type: ignore

        # common patterns in projects
        if hasattr(database, "get_user_by_email"):
            u = database.get_user_by_email(email)  # type: ignore
            return _as_user_dict(u)

        if hasattr(database, "SessionLocal") and hasattr(database, "User"):
            SessionLocal = database.SessionLocal  # type: ignore
            User = database.User  # type: ignore
            with SessionLocal() as db:
                u = db.query(User).filter(User.email == email).first()
                return _as_user_dict(u)

    except Exception:
        pass

    # ---- Fallback: sqlite3 ----
    db_path = os.environ.get("KTBB_DB_PATH", "ktbb_app.db")
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # Table/column names may differ; this is best-effort.
        # If your schema differs, your database.py path above should be used instead.
        cur.execute("SELECT * FROM users WHERE email = ? LIMIT 1", (email,))
        row = cur.fetchone()
        con.close()
        if not row:
            return None
        return dict(row)
    except Exception:
        return None


def _as_user_dict(u: Any) -> Optional[Dict[str, Any]]:
    if u is None:
        return None
    if isinstance(u, dict):
        return u
    # SQLAlchemy model
    out = {}
    for k in ("id", "email", "tier", "timezone", "is_admin"):
        if hasattr(u, k):
            out[k] = getattr(u, k)
    # guarantee email if present
    if not out.get("email") and hasattr(u, "email"):
        out["email"] = getattr(u, "email")
    return out or None


def get_current_user(request: Request):
    """
    If your auth.py already has this, keep yours and delete this stub.
    This stub supports SessionMiddleware-based login.
    """
    try:
        return request.session.get("user")
    except Exception:
        return None

def require_user(request: Request):
    """
    Dependency helper used by main.py. If your auth.py already has this,
    keep yours and delete this stub.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def set_session_user(request: Request, user: Dict[str, Any]) -> None:
    request.session["user"] = {
        "id": user.get("id"),
        "email": user.get("email"),
        "tier": user.get("tier", "free"),
        "timezone": user.get("timezone"),
        "is_admin": bool(user.get("is_admin", False)),
    }


def clear_session(request: Request) -> None:
    try:
        request.session.clear()
    except Exception:
        pass


def authenticate_email_password(email: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Replace this with your real password verification logic.
    For now:
    - If database.py exposes verify_password / auth helpers, we try them.
    - Otherwise we accept the seeded admin user with ADMIN_PASSWORD env var.
    """
    email = (email or "").strip().lower()
    password = password or ""

    # Try project-provided auth verification if it exists
    try:
        import database  # type: ignore
        if hasattr(database, "authenticate_user"):
            u = database.authenticate_user(email, password)  # type: ignore
            return _as_user_dict(u)
    except Exception:
        pass

    # Fallback: allow ADMIN login if configured
    admin_email = os.environ.get("ADMIN_EMAIL", "you@yourdomain.com").strip().lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    if email == admin_email and admin_pw and password == admin_pw:
        u = _try_db_lookup_user_by_email(email) or {"email": email, "tier": "elite"}
        return u

    # Best effort: look up the user but DO NOT verify password unless you have real hashes.
    # (Keeps things from being insecure by accident.)
    return None
