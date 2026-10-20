"""Unexpected errors: the details are kept for the operator, never shown on screen."""

from __future__ import annotations

import re
import traceback
from datetime import datetime, timezone

from fastapi import Request
from sqlmodel import Session

from app.config import get_settings
from app.db import get_engine
from app.models import Client, Event

CLIENT_PATH = re.compile(r"^/clients/([^/]+)/")


def record(request: Request, exc: BaseException) -> None:
    """Add the error to data/logs/errors.log, and to the client's event log when the page was about one client."""
    details = {
        "method": request.method,
        "path": request.url.path,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": "".join(traceback.format_exception(exc))[-8000:],
    }
    try:
        folder = get_settings().data_dir / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "errors.log").open("a", encoding="utf-8") as log:
            log.write(f"--- {datetime.now(timezone.utc).isoformat()} {details['method']} {details['path']}\n")
            log.write(details["traceback"] + "\n")
    except OSError:
        pass
    match = CLIENT_PATH.match(request.url.path)
    if not match:
        return
    try:
        with Session(get_engine()) as session:
            if session.get(Client, match.group(1)) is not None:
                session.add(Event(client_id=match.group(1), type="error", details=details))
                session.commit()
    except Exception:  # noqa: BLE001 - the error page must still show
        pass
