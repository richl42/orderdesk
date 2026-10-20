"""Screen 2: add a client / client settings. Each section saves on its own."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.clientsetup import pending, service
from app.db import get_session
from app.models import Client
from app.web.pages import get_client
from app.web.summaries import setup_complete
from app.web.templating import templates
from pipeline import export
from pipeline.tables import FIELDS, OUTPUT_FIELDS
from pipeline.vocab import MappingKind

router = APIRouter()

TABLE_SECTIONS = {
    MappingKind.CATALOGUE: {
        "number": 2, "title": "Product catalogue", "anchor": "catalogue",
        "intro": "What every order line gets matched against. Excel or CSV.",
    },
    MappingKind.CUSTOMERS: {
        "number": 3, "title": "Customer list", "anchor": "customers",
        "intro": "Lets the system work out who each order is from, and where it is going.",
    },
    MappingKind.PRICES: {
        "number": None, "title": "Prices by band (optional)", "anchor": "prices",
        "intro": "Only needed if some customers pay different prices. One row per band and product.",
    },
}


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def page_context(session: Session, client: Client | None, details: service.DetailsForm | None = None,
                 **extra) -> dict:
    context = {
        "client": client,
        "nav": "add" if client is None or not setup_complete(client) else "",
        "tab": "settings",
        "steps": service.steps(client),
        "details": details or (service.DetailsForm.from_client(client) if client else service.DetailsForm()),
        "erp_systems": service.ERP_SYSTEMS,
        "modes": service.MODE_CHOICES,
        "field_options": FIELDS,
        "output_fields": OUTPUT_FIELDS,
        "date_formats": export.DATE_FORMAT_NAMES,
        "delimiters": export.DELIMITER_NAMES,
        "encodings": export.ENCODING_NAMES,
        "rule_checkboxes": service.RULE_CHECKBOXES,
        "sections": TABLE_SECTIONS,
    }
    if client is not None:
        context.update(
            tables={kind: service.table_section(session, client, kind) for kind in TABLE_SECTIONS},
            output=service.output_section(session, client),
            rules=service.RulesForm.from_client(client),
            complete=setup_complete(client),
        )
    context.update(extra)
    return context


def setup_url(client_id: str, anchor: str = "") -> str:
    return f"/clients/{client_id}/setup" + (f"#{anchor}" if anchor else "")


# ---------------------------------------------------------------- page


@router.get("/clients/new")
def new_client_page(request: Request, client: str | None = None, session: Session = Depends(get_session)):
    if client:
        return RedirectResponse(setup_url(client), status_code=303)
    return templates.TemplateResponse(request, "setup/page.html", page_context(session, None))


@router.get("/clients/{client_id}/setup")
def setup_page(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return templates.TemplateResponse(request, "setup/page.html", page_context(session, client))


# ---------------------------------------------------------------- 1. details


def details_form(form) -> service.DetailsForm:
    return service.DetailsForm(**{k: str(form.get(k, "")) for k in (
        "name", "short_name", "erp_system", "mode", "contact_name", "contact_email",
        "trial_start_date", "trial_working_days")})


@router.post("/clients/new")
async def create_client(request: Request, session: Session = Depends(get_session)):
    form = details_form(await request.form())
    client = service.create_client(session, form)
    if client is None:
        return templates.TemplateResponse(request, "setup/page.html", page_context(session, None, details=form),
                                          status_code=422)
    return RedirectResponse(setup_url(client.id, "catalogue"), status_code=303)


@router.post("/clients/{client_id}/setup/details")
async def save_details(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = details_form(await request.form())
    ok = service.update_details(session, client, form)
    context = page_context(session, client, details=form, details_notice="Details saved." if ok else None)
    if is_htmx(request):
        return templates.TemplateResponse(request, "setup/details.html", {**context, "oob": True})
    if not ok:
        return templates.TemplateResponse(request, "setup/page.html", context, status_code=422)
    return RedirectResponse(setup_url(client.id, "details"), status_code=303)


# ---------------------------------------------------------------- 2, 3. table files


def table_response(request: Request, session: Session, client: Client, section: service.TableSection):
    if not is_htmx(request):
        return RedirectResponse(setup_url(client.id, TABLE_SECTIONS[section.kind]["anchor"]), status_code=303)
    context = page_context(session, client)
    return templates.TemplateResponse(request, "setup/table.html", {
        **context, "s": section, "meta": TABLE_SECTIONS[section.kind], "oob": True,
    })


def check_kind(kind: str) -> str:
    if kind not in TABLE_SECTIONS:
        raise HTTPException(status_code=404)
    return kind


@router.post("/clients/{client_id}/setup/files/{kind}/upload")
async def upload_table(client_id: str, kind: str, request: Request, file: UploadFile,
                       session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    kind = check_kind(kind)
    data = await file.read()
    error = service.receive_table_upload(client, kind, file.filename or "upload", data, session=session)
    return table_response(request, session, client, service.table_section(session, client, kind, error=error))


@router.post("/clients/{client_id}/setup/files/{kind}/check")
async def check_table(client_id: str, kind: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    kind = check_kind(kind)
    mapping = service.mapping_from_form(await request.form(), service.current_columns(session, client, kind))
    upload = pending.load(client.id, kind)
    if upload:  # remember the operator's choices across visits
        upload.mapping = mapping
        pending.write(client.id, upload)
    return table_response(request, session, client, service.table_section(session, client, kind, mapping=mapping))


@router.post("/clients/{client_id}/setup/files/{kind}/save")
async def save_table(client_id: str, kind: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    kind = check_kind(kind)
    mapping = service.mapping_from_form(await request.form(), service.current_columns(session, client, kind))
    section = service.save_table(session, client, kind, mapping)
    return table_response(request, session, client, section)


@router.post("/clients/{client_id}/setup/files/{kind}/discard")
async def discard_upload(client_id: str, kind: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    kind = check_kind(kind)
    pending.clear(client.id, kind)
    return table_response(request, session, client, service.table_section(session, client, kind))


# ---------------------------------------------------------------- 4. output format


def output_response(request: Request, session: Session, client: Client, section: service.OutputSection):
    if not is_htmx(request):
        return RedirectResponse(setup_url(client.id, "output"), status_code=303)
    context = page_context(session, client)
    return templates.TemplateResponse(request, "setup/output.html", {**context, "output": section, "oob": True})


@router.post("/clients/{client_id}/setup/output/upload")
async def upload_output(client_id: str, request: Request, file: UploadFile, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    error = service.receive_output_upload(client, file.filename or "upload", await file.read())
    return output_response(request, session, client, service.output_section(session, client, error=error))


@router.post("/clients/{client_id}/setup/output/preview")
async def preview_output(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    section = service.output_section(session, client, form=dict(await request.form()))
    return output_response(request, session, client, section)


@router.post("/clients/{client_id}/setup/output/discard")
async def discard_output(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    pending.clear(client.id, MappingKind.OUTPUT)
    return output_response(request, session, client, service.output_section(session, client))


@router.post("/clients/{client_id}/setup/output/save")
async def save_output(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    section = service.save_output(session, client, dict(await request.form()))
    return output_response(request, session, client, section)


# ---------------------------------------------------------------- 5. rules


@router.post("/clients/{client_id}/setup/rules")
async def save_rules(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    rules = service.save_rules(session, client, dict(await request.form()))
    if not is_htmx(request):
        if rules.errors:
            return templates.TemplateResponse(request, "setup/page.html",
                                              page_context(session, client, rules=rules), status_code=422)
        return RedirectResponse(setup_url(client.id, "rules"), status_code=303)
    return templates.TemplateResponse(request, "setup/rules.html",
                                      {**page_context(session, client), "rules": rules, "oob": True})


# ---------------------------------------------------------------- finish


@router.post("/clients/{client_id}/setup/finish")
def finish_setup(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    missing = service.missing_steps(client)
    if missing:
        return templates.TemplateResponse(request, "setup/page.html", page_context(
            session, client, finish_error="Still to do before this client can take orders: " + ", ".join(missing) + "."),
            status_code=422)
    return RedirectResponse("/", status_code=303)
