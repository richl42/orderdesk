"""Export routes: make a batch file, download one order, and the export history."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app.db import get_session
from app.orders import export
from app.orders.standard import output_template
from app.web.orders import get_order
from app.web.pages import get_client
from app.web.summaries import summarise_client
from app.web.templating import templates

router = APIRouter()

CONTENT_TYPES = {"utf-8": "text/csv; charset=utf-8", "utf-8-sig": "text/csv; charset=utf-8",
                 "cp1252": "text/csv; charset=windows-1252"}


def file_response(data: bytes, file_name: str, encoding: str = "utf-8") -> Response:
    return Response(content=data, media_type=CONTENT_TYPES.get(encoding, "text/csv"),
                    headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


def history_page(request: Request, session: Session, client, new_id: int | None = None, error: str | None = None,
                 status_code: int = 200):
    batches = export.batches(session, client)
    return templates.TemplateResponse(request, "exports.html", {
        "client": client, "summary": summarise_client(session, client, datetime.now(timezone.utc)),
        "batches": batches, "new": next((b for b in batches if b.id == new_id), None), "error": error,
        "waiting": len(export.ready_to_export(session, client)), "nav": "orders", "tab": "orders",
    }, status_code=status_code)


@router.get("/clients/{client_id}/exports")
def exports_page(client_id: str, request: Request, new: int | None = None, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return history_page(request, session, client, new_id=new)


@router.post("/clients/{client_id}/exports")
def make_batch(client_id: str, request: Request, session: Session = Depends(get_session)):
    """Build the batch, then show it on the history page, which starts the download."""
    client = get_client(session, client_id)
    try:
        made = export.export_batch(session, client)
    except export.ExportRefused as refused:
        return history_page(request, session, client, error=str(refused), status_code=422)
    return RedirectResponse(f"/clients/{client.id}/exports?new={made.batch.id}", status_code=303)


@router.get("/clients/{client_id}/exports/{batch_id}/download")
def download_batch(client_id: str, batch_id: int, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    batch = export.get_batch(session, client, batch_id)
    if batch is None:
        raise HTTPException(status_code=404)
    try:
        data = export.stored_file(batch)
    except export.ExportRefused as refused:
        raise HTTPException(status_code=410, detail=str(refused)) from refused
    template = output_template(session, client.id) or {}
    return file_response(data, batch.file_name, template.get("encoding") or "utf-8")


@router.post("/clients/{client_id}/orders/{order_id}/download")
def download_order(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    """This order alone. It counts as exported, so it will not be in a later batch as well."""
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    try:
        made = export.export_one(session, client, order)
    except export.ExportRefused as refused:
        from app.web.review import context

        return templates.TemplateResponse(request, "review/page.html", context(session, client, order,
                                                                               error=str(refused)), status_code=422)
    return RedirectResponse(f"/clients/{client.id}/exports?new={made.batch.id}", status_code=303)
