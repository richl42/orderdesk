"""The Order Desk web app. Start it with `make run` (listens on 127.0.0.1 only)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.db import get_engine
from app.orders import service as orders_service
from app.web import orders, pages, setup
from app.web.templating import templates

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    get_engine()  # creates the database and tables on first run
    orders_service.resume_unfinished()

    app = FastAPI(title="Order Desk", docs_url=None, redoc_url=None, openapi_url=None)
    # Middleware added last runs first: the session must be loaded before the login check.
    app.add_middleware(auth.LoginRequired)
    app.add_middleware(
        SessionMiddleware,
        secret_key=auth.secret_key(),
        session_cookie="orderdesk_session",
        max_age=auth.IDLE_LIMIT_SECONDS,
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(setup.router)
    app.include_router(orders.router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return templates.TemplateResponse(request, "not_built.html", {
            "nav": "", "title": "Page not found",
            "message": "There is nothing at this address. Use the links above to find your way back.",
        }, status_code=404)

    return app


app = create_app()
