"""One-off notices that show on the next page, after a redirect (kept in the login cookie)."""

from __future__ import annotations

from fastapi import Request


def flash(request: Request, kind: str, text: str) -> None:
    """kind is ready (good news), check or problem, like the notice styles."""
    request.session.setdefault("flash", []).append([kind, text])
    request.session["flash"] = request.session["flash"]  # mark the session changed


def take_flashes(request: Request) -> list[tuple[str, str]]:
    if "session" not in request.scope:
        return []
    return [tuple(item) for item in request.session.pop("flash", [])]
