# auth.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

# Session key name used by Starlette SessionMiddleware
SESSION_KEY = os.getenv("SESSION_KEY", "bb_user")

router = APIRouter()


def get_session_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Returns the session user dict if present, else None.
    Stored at request.session[SESSION_KEY]
    """
    if not hasattr(request, "session"):
        return None
    val = request.session.get(SESSION_KEY)
    if isinstance(val, dict):
        return val
    return None


def require_session_user(request: Request) -> Dict[str, Any]:
    """
    Hard requirement for authenticated session user.
    main.py expects this function to exist.
    """
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def set_session_user(request: Request, user: Dict[str, Any]) -> None:
    if not hasattr(request, "session"):
        raise HTTPException(status_code=500, detail="Session middleware not configured")
    request.session[SESSION_KEY] = user


def clear_session_user(request: Request) -> None:
    if hasattr(request, "session"):
        request.session.pop(SESSION_KEY, None)


@router.get("/api/auth/me")
async def me(request: Request):
    return JSONResponse({"ok": True, "user": get_session_user(request)})


@router.post("/api/auth/logout")
async def logout(request: Request):
    clear_session_user(request)
    return JSONResponse({"ok": True})


@router.post("/api/auth/dev-login")
async def dev_login(request: Request):
    """
    OPTIONAL: a dev-only login endpoint so you can get unstuck quickly.
    Lock it down with an env var in Render.
    """
    if os.getenv("ALLOW_DEV_LOGIN", "false").lower() != "true":
        raise HTTPException(status_code=403, detail="Dev login disabled")

    payload = await request.json()
    email = (payload.get("email") or "admin@local").strip().lower()
    role = (payload.get("role") or "admin").strip().lower()

    set_session_user(request, {"email": email, "role": role})
    return JSONResponse({"ok": True, "user": get_session_user(request)})
