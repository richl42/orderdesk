"""Single-password login with a signed session cookie that expires after 8 hours idle."""

from __future__ import annotations

import hmac
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.web.templating import templates

IDLE_LIMIT_SECONDS = 8 * 60 * 60
OPEN_PATHS = ("/login", "/static/")

router = APIRouter()


def secret_key() -> str:
    """SECRET_KEY from settings, or one generated once and kept in the data folder."""
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key
    path: Path = settings.data_dir / ".secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def password_matches(given: str) -> bool:
    expected = get_settings().app_password
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class LoginRequired(BaseHTTPMiddleware):
    """Send anyone not logged in (or idle for 8 hours) to the login page."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(OPEN_PATHS):
            return await call_next(request)
        session = request.session
        now = int(time.time())
        last_seen = session.get("last_seen")
        if not session.get("logged_in") or last_seen is None or now - last_seen > IDLE_LIMIT_SECONDS:
            session.clear()
            if request.headers.get("HX-Request"):
                response = RedirectResponse("/login", status_code=303)
                response.headers["HX-Redirect"] = "/login"
                return response
            return RedirectResponse("/login", status_code=303)
        session["last_seen"] = now
        return await call_next(request)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "no_password": not get_settings().app_password,
    })


@router.post("/login")
def login(request: Request, password: str = Form("")):
    if password_matches(password):
        request.session.clear()
        request.session["logged_in"] = True
        request.session["last_seen"] = int(time.time())
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "error": "That password is not right. Try again.",
        "no_password": not get_settings().app_password,
    }, status_code=401)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
