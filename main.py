# main.py
# Clean, single-source FastAPI entrypoint for Kabroda BattleBox
# Focus: DMR generation, auth redirects, Stripe handoff safety, AI coach routing

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import os
import traceback

# Local modules
from auth import require_user, get_current_user
from dmr_report import run_dmr
from kabroda_ai import run_ai_coach
from database import init_db

# ------------------------------------------------------------------
# App bootstrap
# ------------------------------------------------------------------

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret"),
    same_site="lax",
    https_only=True,
)

BASE_DIR = os.path.dirname(__file__)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ------------------------------------------------------------------
# Startup
# ------------------------------------------------------------------

@app.on_event("startup")
def startup():
    init_db()

# ------------------------------------------------------------------
# Error handling (NO PATCHES, GLOBAL + CLEAN)
# ------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Redirect unauthenticated users cleanly
    if exc.status_code == 401:
        return RedirectResponse(url="/login")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Always surface traceback to frontend (dev-safe, prod-toggle later)
    tb = traceback.format_exc()
    return JSONResponse(
        {
            "error": str(exc),
            "traceback": tb,
        },
        status_code=500,
    )

# ------------------------------------------------------------------
# Page routes
# ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": user,
        },
    )

# ------------------------------------------------------------------
# API: DMR generation (CORE PIPELINE)
# ------------------------------------------------------------------

@app.post("/api/run_dmr")
def api_run_dmr(request: Request, payload: dict, user=Depends(require_user)):
    symbol = payload.get("symbol")
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")

    # Run deterministic DMR pipeline (NO AI GUESSING HERE)
    dmr = run_dmr(
        symbol=symbol,
        user_timezone=user.get("timezone"),
    )

    return {"dmr": dmr}

# ------------------------------------------------------------------
# API: AI Coach (STRICTLY ANCHORED TO DMR)
# ------------------------------------------------------------------

@app.post("/api/ai_coach")
def api_ai_coach(payload: dict, user=Depends(require_user)):
    message = payload.get("message")
    dmr = payload.get("dmr")

    if not message or not dmr:
        raise HTTPException(status_code=400, detail="Message and DMR required")

    response = run_ai_coach(
        user_message=message,
        dmr_context=dmr,
        tier=user.get("tier"),
    )

    return {"reply": response}

# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}
