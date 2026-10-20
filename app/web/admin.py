"""Things done without the terminal: back up the data, start the demo again, delete a client."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session

from app import backup
from app.db import delete_client, get_session
from app.models import Client
from app.web.flash import flash
from app.web.pages import get_client
from seed import demo_data

router = APIRouter()


def back_to(form, default: str = "/") -> str:
    """Where the form came from: only a path on this site."""
    target = str(form.get("back") or "")
    return target if target.startswith("/") and not target.startswith("//") else default


def confirmed(form, expected: str) -> bool:
    return " ".join(str(form.get("confirm") or "").split()).casefold() == " ".join(expected.split()).casefold()


@router.post("/backup")
def download_backup():
    path = backup.make_backup()
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/demo/restart")
async def restart_demo(request: Request, session: Session = Depends(get_session)):
    """The same as make seed: write the demo files again and rebuild the demo client from them."""
    form = await request.form()
    existing = session.get(Client, demo_data.DEMO_ID)
    if existing is not None and not confirmed(form, "DEMO"):
        flash(request, "problem", "Nothing was changed. Type DEMO in the box to start the demo again.")
        return RedirectResponse(back_to(form), status_code=303)
    files = demo_data.write_demo_files(demo_data.SEED_DIR)
    client = demo_data.load_demo_client(session, files)
    again = " again" if existing is not None else ""
    flash(request, "ready", f"{client.name} has been set up{again} from its files, with no orders. Check the "
                            f"settings below, then use Load the 20 test orders on the Orders tab.")
    return RedirectResponse(f"/clients/{client.id}/setup", status_code=303)


@router.post("/clients/{client_id}/delete")
async def remove_client(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = await request.form()
    if not confirmed(form, client.name):
        flash(request, "problem", f"Nothing was deleted. Type the client's name, {client.name}, exactly to "
                                  f"confirm.")
        return RedirectResponse(f"/clients/{client.id}/setup#delete", status_code=303)
    name = client.name
    delete_client(session, client.id)
    flash(request, "ready", f"{name} and all of its data and files have been deleted.")
    return RedirectResponse("/", status_code=303)
