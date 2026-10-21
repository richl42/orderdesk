"""Protection for running on the internet: security headers, form posts only from Order Desk's own pages,
an upload size limit, and a limit on wrong passwords."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

# Scripts only from /static (no inline scripts or eval); inline style attributes are used by the templates.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
])
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/static/"):
            headers.setdefault("Cache-Control", "no-store")  # pages hold client data: never keep them in caches
        if get_settings().production:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _same_site(request: Request) -> bool:
    """A form post must come from a page on this site. Browsers send Origin (or at least Referer) on posts."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True  # not a browser (tests, curl): the login cookie still has to be valid
    if source == "null":
        return False
    hosts = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()} - {""}
    return urlsplit(source).netloc.lower() in hosts


class RequestChecks(BaseHTTPMiddleware):
    """Refuse posts from other sites, and uploads bigger than MAX_UPLOAD_MB."""

    async def dispatch(self, request: Request, call_next):
        if request.method in UNSAFE_METHODS:
            if not _same_site(request):
                return PlainTextResponse("This form did not come from Order Desk, so it was refused.",
                                         status_code=403)
            limit = get_settings().max_upload_mb * 1024 * 1024
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > limit:
                return PlainTextResponse(f"That upload is too big. The limit is {get_settings().max_upload_mb} MB "
                                         f"at a time; upload fewer files at once.", status_code=413)
        return await call_next(request)


class LoginLimiter:
    """After 5 wrong passwords from one address in 15 minutes, that address must wait 15 minutes."""

    def __init__(self, attempts: int = 5, window_seconds: int = 15 * 60):
        self.attempts = attempts
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        while failures and now - failures[0] > self.window:
            failures.popleft()
        return failures

    def wait_seconds(self, key: str, now: float | None = None) -> int:
        """0 when this address may try again, otherwise how long it must wait."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            failures = self._recent(key, now)
            if len(failures) < self.attempts:
                return 0
            return max(1, int(self.window - (now - failures[0])))

    def failed(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._recent(key, now).append(now)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_limiter = LoginLimiter()
# A backstop in case addresses are faked: 30 wrong passwords from anywhere in 15 minutes and everyone waits.
all_logins_limiter = LoginLimiter(attempts=30)
ANYONE = "*"


def login_wait_seconds(address: str) -> int:
    return max(login_limiter.wait_seconds(address), all_logins_limiter.wait_seconds(ANYONE))


def login_failed(address: str) -> None:
    login_limiter.failed(address)
    all_logins_limiter.failed(ANYONE)


def login_succeeded(address: str) -> None:
    login_limiter.succeeded(address)


def client_address(request: Request) -> str:
    """The visitor's address. Behind Render or Railway, uvicorn takes it from the proxy's X-Forwarded-For."""
    return request.client.host if request.client else "unknown"
