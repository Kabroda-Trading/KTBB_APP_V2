# main.py — Unified KABRODA Backend Entry Point

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Route modules
from auth import router as auth_router
from billing import router as billing_router
from black_ops_engine import router as black_ops_router
from battle_control import router as battle_control_router
from session_control import router as session_control_router

app = FastAPI()

# Register API routers
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(black_ops_router)
app.include_router(battle_control_router)
app.include_router(session_control_router)

# Static file serving
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Frontend Pages (HTML) ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
async def read_about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/pricing", response_class=HTMLResponse)
async def read_pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})

@app.get("/account", response_class=HTMLResponse)
async def read_account(request: Request):
    return templates.TemplateResponse("account.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/omega", response_class=HTMLResponse)
async def omega_ui(request: Request):
    return templates.TemplateResponse("omega_ui.html", {"request": request})

@app.get("/research", response_class=HTMLResponse)
async def research_ui(request: Request):
    return templates.TemplateResponse("research.html", {"request": request})

@app.get("/analysis", response_class=HTMLResponse)
async def analysis_ui(request: Request):
    return templates.TemplateResponse("analysis.html", {"request": request})

@app.get("/battle-control", response_class=HTMLResponse)
async def battle_control_ui(request: Request):
    return templates.TemplateResponse("battle_control.html", {"request": request})

@app.get("/session-control", response_class=HTMLResponse)
async def session_control_ui(request: Request):
    return templates.TemplateResponse("session_control.html", {"request": request})

@app.get("/indicators", response_class=HTMLResponse)
async def indicators_ui(request: Request):
    return templates.TemplateResponse("indicators.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_ui(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})
