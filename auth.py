# auth.py
import os
import hmac
import hashlib
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, UserModel

# NOTE: Removed 'init_db' from imports as it should only be handled by main.py at startup

router = APIRouter()
SESSION_KEY = "kabroda_user_id"
templates = Jinja2Templates(directory="templates")

# --- PASSWORD CRYPTOGRAPHY ---
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
        if algo != "pbkdf2_sha256": return False
        candidate = _pbkdf2_hash_password(password, salt)
        return hmac.compare_digest(candidate, digest)
    except Exception: return False

def require_session_user(request: Request) -> int:
    user_id = request.session.get(SESSION_KEY)
    if not user_id: raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user_id)

def ensure_bootstrap_admin(db: Session) -> None:
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD") or ""
    if not admin_email or not admin_password: return
    existing = db.query(UserModel).filter(UserModel.email == admin_email).first()
    if existing: return
    
    # AUDIT FIX: Added 'tier' to satisfy Postgres NOT NULL constraint
    u = UserModel(
        email=admin_email, 
        password_hash=hash_password(admin_password), 
        tier="admin", 
        is_admin=True
    )
    db.add(u)
    db.commit()

# --- ROUTES ---
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    try:
        tmpl = templates.env.get_template("login.html")
        html = tmpl.render({"request": request, "error": request.query_params.get("error")})
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h2>System Error: login.html</h2><p>{str(e)}</p>", status_code=500)

@router.post("/login")
def login_action(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # AUDIT FIX: Removed init_db() to prevent SQLite deadlocks.
    ensure_bootstrap_admin(db)
    em = (email or "").strip().lower()
    u = db.query(UserModel).filter(UserModel.email == em).first()
    
    if not u or not verify_password(password or "", u.password_hash):
        return RedirectResponse(url="/login?error=Invalid+Credentials", status_code=303)
    
    request.session[SESSION_KEY] = int(u.id)
    return RedirectResponse(url="/suite", status_code=303)

# Public self-registration is closed (site is invite-only, not currently offering
# memberships). These routes stay defined -- harmless redirect for any stale
# bookmarked/indexed /register link -- rather than a dangling 404 or the old
# TemplateNotFound crash (register.html doesn't exist; this used to redirect to
# a hardcoded Whop checkout URL from a past membership-commerce era). Accounts
# are now created directly by an admin via POST /admin/create-user.
@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return RedirectResponse(url="/login")

@router.post("/register")
def register_action(request: Request):
    return RedirectResponse(url="/login", status_code=303)

@router.get("/logout")
def logout_action(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/", status_code=303)