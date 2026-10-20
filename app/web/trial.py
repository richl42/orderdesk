"""Screen 5: shadow trial results, staff entry upload, and the printable client report."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from app.clientsetup import pending
from app.clientsetup import service as setup_service
from app.db import get_session
from app.trial import service
from app.web.orders import is_htmx
from app.web.pages import get_client
from app.web.summaries import summarise_client
from app.web.templating import templates
from pipeline.vocab import FIX_TAG_LABELS, MappingKind

router = APIRouter()

KIND = MappingKind.STAFF_ENTRIES
META = {"number": None, "title": "Staff entries", "anchor": "staff",
        "intro": "An export of what their team entered for the same days. Excel or CSV."}


def staff_section(request: Request, session: Session, client, section=None):
    section = section or setup_service.table_section(session, client, KIND)
    return templates.TemplateResponse(request, "setup/table.html", {
        "client": client, "s": section, "meta": META, "oob": False,
        "base_url": f"/clients/{client.id}/trial/staff", "field_options": {},
    })


def page(request: Request, session: Session, client, notice: str | None = None, error: str | None = None):
    view = service.trial_view(session, client)
    upload_open = pending.load(client.id, KIND) is not None or view.upload is None
    return templates.TemplateResponse(request, "trial.html", {
        "client": client, "summary": summarise_client(session, client, datetime.now(timezone.utc)),
        "v": view, "nav": "trials", "tab": "trial", "tags": FIX_TAG_LABELS, "notice": notice, "error": error,
        "staff": setup_service.table_section(session, client, KIND), "meta": META, "upload_open": upload_open,
        "base_url": f"/clients/{client.id}/trial/staff",
    })


@router.get("/clients/{client_id}/trial")
def trial_page(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return page(request, session, client)


@router.post("/clients/{client_id}/trial/staff/upload")
async def upload_staff(client_id: str, request: Request, file: UploadFile, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    error = setup_service.receive_table_upload(client, KIND, file.filename or "upload", await file.read(),
                                               session=session)
    section = setup_service.table_section(session, client, KIND, error=error)
    if not is_htmx(request):
        return RedirectResponse(f"/clients/{client.id}/trial", status_code=303)
    return staff_section(request, session, client, section)


@router.post("/clients/{client_id}/trial/staff/check")
async def check_staff(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    mapping = setup_service.mapping_from_form(await request.form(), setup_service.current_columns(session, client,
                                                                                                 KIND))
    upload = pending.load(client.id, KIND)
    if upload:
        upload.mapping = mapping
        pending.write(client.id, upload)
    return staff_section(request, session, client, setup_service.table_section(session, client, KIND,
                                                                               mapping=mapping))


@router.post("/clients/{client_id}/trial/staff/save")
async def save_staff(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    mapping = setup_service.mapping_from_form(await request.form(), setup_service.current_columns(session, client,
                                                                                                 KIND))
    section = setup_service.save_table(session, client, KIND, mapping)
    if section.error:
        return staff_section(request, session, client, section)
    if is_htmx(request):  # the whole page changes: tiles, days and differences
        return Response(status_code=204, headers={"HX-Redirect": f"/clients/{client.id}/trial"})
    return RedirectResponse(f"/clients/{client.id}/trial", status_code=303)


@router.post("/clients/{client_id}/trial/staff/discard")
def discard_staff(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    pending.clear(client.id, KIND)
    return staff_section(request, session, client)


@router.post("/clients/{client_id}/trial/compare")
def compare_again(client_id: str, request: Request, session: Session = Depends(get_session)):
    """Compare again, e.g. after approving more orders. Notes and tags are kept."""
    client = get_client(session, client_id)
    service.run_comparison(session, client)
    return RedirectResponse(f"/clients/{client.id}/trial", status_code=303)


@router.post("/clients/{client_id}/trial/differences/{comparison_id}/{index}")
async def save_difference(client_id: str, comparison_id: int, index: int, request: Request,
                          session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = await request.form()
    note = form.get("note")
    tag = form.get("tag")
    try:
        service.set_note(session, client, comparison_id, index, str(note) if note is not None else None,
                         str(tag) if tag is not None else None)
    except ValueError as exc:
        return HTMLResponse(f'<span class="saved-note problem">{exc}</span>', status_code=404)
    if not is_htmx(request):
        return RedirectResponse(f"/clients/{client.id}/trial#differences", status_code=303)
    return HTMLResponse('<span class="saved-note">Saved</span>')


@router.post("/clients/{client_id}/trial/summary")
async def save_summary(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = await request.form()
    text = "" if form.get("reset") else str(form.get("text") or "")
    try:
        service.save_summary(session, client, text)
    except ValueError as exc:
        return page(request, session, client, error=str(exc))
    return RedirectResponse(f"/clients/{client.id}/trial#summary", status_code=303)


@router.get("/clients/{client_id}/trial/report")
def client_report(client_id: str, request: Request, session: Session = Depends(get_session)):
    """The printable report for the client: no AI cost and nothing internal."""
    client = get_client(session, client_id)
    return templates.TemplateResponse(request, "trial_report.html", {
        "client": client, "v": service.trial_view(session, client), "tags": FIX_TAG_LABELS,
    })
