# auth.py — User Authentication

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND
from starlette.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Dummy login for development / testing
@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # TODO: Replace with real authentication logic
    if username == "admin" and password == "kabroda":
        request.session["user"] = {"username": username}
        return RedirectResponse(url="/omega", status_code=HTTP_302_FOUND)
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=HTTP_302_FOUND)

@router.get("/me")
async def me(request: Request):
    user = request.session.get("user")
    return {"user": user}

# Registration can be handled similarly
@router.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...)):
    # Placeholder: Save user to DB (not implemented)
    request.session["user"] = {"username": username}
    return RedirectResponse(url="/account", status_code=HTTP_302_FOUND)
