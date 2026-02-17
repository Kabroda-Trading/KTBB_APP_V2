# auth.py
import os
import hmac
import hashlib
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, UserModel, init_db

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
    u = UserModel(email=admin_email, password_hash=hash_password(admin_password), is_admin=True)
    db.add(u)
    db.commit()

# --- ROUTES ---
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": request.query_params.get("error")})

@router.post("/login")
def login_action(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    init_db()
    ensure_bootstrap_admin(db)
    em = (email or "").strip().lower()
    u = db.query(UserModel).filter(UserModel.email == em).first()
    
    if not u or not verify_password(password or "", u.password_hash):
        return RedirectResponse(url="/login?error=Invalid+Credentials", status_code=303)
    
    request.session[SESSION_KEY] = int(u.id)
    return RedirectResponse(url="/suite", status_code=303)

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
def register_action(
    request: Request, 
    first_name: str = Form(None),
    last_name: str = Form(None),
    username: str = Form(...),
    email: str = Form(...), 
    password: str = Form(...), 
    db: Session = Depends(get_db)
):
    init_db() 
    em = email.strip().lower()
    
    existing = db.query(UserModel).filter(UserModel.email == em).first()
    if existing:
        return RedirectResponse(url="/login?error=Email+already+registered", status_code=303)
    
    # Create the operative (Inactive until Whop Webhook fires)
    new_user = UserModel(
        email=em,
        username=username,
        password_hash=hash_password(password),
        subscription_status="inactive",
        is_admin=False
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Auto-login the user immediately
    request.session[SESSION_KEY] = int(new_user.id)
    
    # THE FIX: Route them directly to the Whop Checkout link!
    return RedirectResponse(url="https://whop.com/checkout/plan_TtQ6FGNPxooMc", status_code=303)

@router.get("/logout")
def logout_action(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/", status_code=303)