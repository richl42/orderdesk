"""Screen 3: one client's order queue (upload, paste, the orders table) and a read-only order page.

The order page is a stand-in for screen 4 (review), which Milestone 5 builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session, func, select

from app.db import get_session
from app.models import CatalogueItem, Client, Customer, Order, OrderLine, OutputTemplate
from app.orders import service
from app.web.format import london
from app.web.pages import get_client
from app.web.summaries import WAITING, london_day_bounds, order_issue, summarise_client
from app.web.templating import templates
from pipeline.normalise import split_emails
from pipeline.vocab import (
    CLASSIFICATION_LABELS,
    MATCH_METHOD_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    Classification,
    Status,
)
from pipeline.workdays import LONDON

router = APIRouter()

CUSTOMER_HOW = {
    "email": "Recognised by their email address",
    "domain": "Recognised by their email domain",
    "name": "Recognised by the name in the order only",
    "operator": "Chosen by you",
}

FILTERS = [
    ("all", "All"),
    (Status.READY, "Ready"),
    (Status.CHECK, "Check"),
    (Status.PROBLEM, "Problem"),
    (Status.PASS_ON, "Pass on"),
    (Status.APPROVED, "Approved"),
]


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def get_order(session: Session, client: Client, order_id: int) -> Order:
    order = session.exec(select(Order).where(Order.client_id == client.id, Order.id == order_id)).first()
    if order is None:
        raise HTTPException(status_code=404, detail="No order with that number")
    return order


# ---------------------------------------------------------------- what the screen shows


@dataclass
class Pill:
    label: str
    css: str
    note: str


def order_pill(order: Order) -> Pill:
    """The status pill and one-line reason for an order, including while it is still being read."""
    if order.processing_state == service.READING:
        return Pill("Reading", "pill-reading", "Reading the file…")
    if order.processing_state == service.READ:
        return Pill("Reading", "pill-reading", "Matching customer and products…")
    if order.processing_state == service.FAILED:
        return Pill("Problem", "pill-problem", order_issue(order) if order.flags else "Could not be read")
    label = STATUS_LABELS.get(Status(order.status), order.status)
    return Pill(label, f"pill-{order.status.replace('_', '-')}", order_issue(order))


def when(moment: datetime, now: datetime) -> str:
    if moment.astimezone(LONDON).date() == now.astimezone(LONDON).date():
        return f"Today, {london(moment, '%H:%M')}"
    local = moment.astimezone(LONDON)
    return f"{local:%a} {local.day} {local:%b}, {local:%H:%M}"


@dataclass
class OrderRow:
    order: Order
    arrived: str
    customer: str
    po: str
    came_as: str
    lines: int | str
    pill: Pill
    action: str
    href: str
    retry: bool


@dataclass
class QueueData:
    client: Client
    rows: list[OrderRow]
    counts: dict[str, int]
    current: str
    received_today: int
    waiting: int
    need_fix: int
    still_reading: int
    approved: int
    output_file: str
    start_href: str | None

    @property
    def polling(self) -> bool:
        return self.still_reading > 0


def customer_label(order: Order, names: dict[int, str]) -> str:
    if order.customer_id and order.customer_id in names:
        return names[order.customer_id]
    return order.customer_name_raw or order.sender_email or "Unknown sender"


def queue_data(session: Session, client: Client, current: str = "all", now: datetime | None = None) -> QueueData:
    now = now or datetime.now(timezone.utc)
    orders = session.exec(select(Order).where(Order.client_id == client.id).order_by(Order.received_at, Order.id)).all()
    line_counts = dict(session.exec(
        select(OrderLine.order_id, func.count()).where(OrderLine.client_id == client.id).group_by(OrderLine.order_id)
    ).all())
    names = {c.id: c.name for c in session.exec(select(Customer).where(Customer.client_id == client.id)).all()}

    counts = {key: 0 for key, _ in FILTERS}
    counts["all"] = len(orders)
    for order in orders:
        if order.status in counts and order.processing_state != service.FAILED:
            counts[order.status] += 1

    if current not in counts:
        current = "all"
    shown = orders if current == "all" else [o for o in orders if o.status == current
                                             and o.processing_state != service.FAILED]
    rows = []
    for order in shown:
        reading = order.processing_state in (service.READING, service.READ)
        rows.append(OrderRow(
            order=order,
            arrived=when(order.received_at, now),
            customer=customer_label(order, names),
            po=order.po_number or "",
            came_as=SOURCE_LABELS.get(order.source_type, order.source_type),
            lines="" if reading else line_counts.get(order.id, 0),
            pill=order_pill(order),
            action="Open" if order.status in (Status.APPROVED, Status.EXPORTED, Status.PASS_ON) else "Review",
            href=f"/clients/{client.id}/orders/{order.id}",
            retry=order.processing_state == service.FAILED,
        ))

    today_start, today_end = london_day_bounds(now)
    waiting = [o for o in orders if o.status in WAITING]
    template = session.exec(select(OutputTemplate).where(OutputTemplate.client_id == client.id)).first()
    return QueueData(
        client=client,
        rows=rows,
        counts=counts,
        current=current,
        received_today=sum(1 for o in orders if today_start <= o.received_at < today_end),
        waiting=len(waiting),
        need_fix=sum(1 for o in waiting if o.status == Status.PROBLEM),
        still_reading=sum(1 for o in orders if o.processing_state in (service.READING, service.READ)),
        approved=counts[Status.APPROVED],
        output_file=template.source_filename if template else "",
        start_href=f"/clients/{client.id}/orders/{waiting[0].id}" if waiting else None,
    )


# ---------------------------------------------------------------- form values


def parse_received(value: str | None) -> tuple[datetime | None, str | None]:
    """A 'received' field (UK time, from a datetime-local input) as UTC, or an error message."""
    value = (value or "").strip()
    if not value:
        return None, None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        return None, "The received time was not understood. Use the date and time picker."
    if local.tzinfo is None:
        local = local.replace(tzinfo=LONDON)
    return local.astimezone(timezone.utc), None


def parse_sender(value: str | None) -> tuple[str | None, str | None]:
    value = (value or "").strip()
    if not value:
        return None, None
    emails = split_emails(value)
    if len(emails) != 1:
        return None, "The sender should be one email address, like orders@riverside-hotel.co.uk."
    return emails[0], None


# ---------------------------------------------------------------- routes


def page_context(session: Session, client: Client, current: str = "all", **extra) -> dict:
    return {
        "client": client,
        "summary": summarise_client(session, client, datetime.now(timezone.utc)),
        "q": queue_data(session, client, current),
        "nav": "orders",
        "tab": "orders",
        "notices": [],
        **extra,
    }


def live_response(request: Request, session: Session, client: Client, current: str = "all",
                  notices: list[tuple[str, str]] | None = None):
    """After an upload: the refreshed queue for HTMX, or a redirect back to the page."""
    if not is_htmx(request):
        return RedirectResponse(f"/clients/{client.id}/orders", status_code=303)
    return templates.TemplateResponse(request, "orders/live.html", {
        "client": client, "q": queue_data(session, client, current), "notices": notices or [],
    })


@router.get("/clients/{client_id}/orders")
def client_orders(client_id: str, request: Request, status: str = "all", session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return templates.TemplateResponse(request, "client_orders.html", page_context(session, client, status))


@router.get("/clients/{client_id}/orders/live")
def orders_live(client_id: str, request: Request, status: str = "all", session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return templates.TemplateResponse(request, "orders/live.html", {
        "client": client, "q": queue_data(session, client, status), "notices": [],
    })


def _upload_notices(orders: list[Order], refused: list[service.Refused]) -> list[tuple[str, str]]:
    notices = []
    if orders:
        n = len(orders)
        notices.append(("ready", f"{n} order{'s' if n != 1 else ''} added. Reading now."))
    for item in refused:
        notices.append(("problem", f"{item.filename}: {item.reason}"))
    return notices


@router.post("/clients/{client_id}/orders/upload")
async def upload_orders(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = await request.form()
    sender, sender_error = parse_sender(form.get("sender_email"))
    received, received_error = parse_received(form.get("received_at"))
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile) or hasattr(f, "read")]
    errors = [e for e in (sender_error, received_error) if e]
    if not files or all(not getattr(f, "filename", "") for f in files):
        errors.append("Choose at least one file to upload.")
    if errors:
        return live_response(request, session, client, notices=[("problem", e) for e in errors])
    uploads = [(f.filename or "upload", await f.read()) for f in files if f.filename]
    orders, refused = service.receive_files(session, client, uploads, sender_email=sender, received_at=received)
    return live_response(request, session, client, notices=_upload_notices(orders, refused))


@router.post("/clients/{client_id}/orders/paste")
async def paste_order(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    form = await request.form()
    sender, sender_error = parse_sender(form.get("sender_email"))
    received, received_error = parse_received(form.get("received_at"))
    text = str(form.get("text") or "")
    errors = [e for e in (sender_error, received_error) if e]
    if not text.strip():
        errors.append("Paste the email text first.")
    if errors:
        return live_response(request, session, client, notices=[("problem", e) for e in errors])
    service.receive_paste(session, client, text, sender_email=sender, received_at=received)
    return live_response(request, session, client, notices=[("ready", "Pasted email added. Reading now.")])


@router.post("/clients/{client_id}/orders/demo")
def load_demo(client_id: str, request: Request, session: Session = Depends(get_session)):
    from seed.demo_data import SEED_DIR

    client = get_client(session, client_id)
    if client.mode != "demo":
        raise HTTPException(status_code=404)
    orders, refused, error = service.load_demo_orders(session, client, SEED_DIR)
    notices = [("problem", error)] if error else _upload_notices(orders, refused)
    return live_response(request, session, client, notices=notices)


@router.post("/clients/{client_id}/orders/{order_id}/retry")
def retry(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    get_order(session, client, order_id)
    service.retry_order(session, client, order_id)
    if is_htmx(request):
        return live_response(request, session, client)
    return RedirectResponse(f"/clients/{client.id}/orders/{order_id}", status_code=303)


# ---------------------------------------------------------------- one order (read-only until Milestone 5)


@dataclass
class Segment:
    text: str
    line_no: int | None = None
    css: str = ""


def line_css(line: OrderLine) -> str:
    return f"hl-{line.status.replace('_', '-')}" if line.status else "hl-read"


def highlight_segments(text: str, lines: list[OrderLine]) -> list[Segment]:
    """Split the order text so each line's source_span can be highlighted where it came from."""
    marks: list[tuple[int, int, OrderLine]] = []
    cursor = 0
    for line in sorted(lines, key=lambda ln: ln.line_no):
        span = line.source_span
        if not span:
            continue
        start = text.find(span, cursor)
        if start < 0:
            start = text.find(span)
        if start < 0:
            continue
        end = start + len(span)
        if any(s < end and start < e for s, e, _ in marks):
            continue  # overlaps another line's highlight
        marks.append((start, end, line))
        cursor = end
    marks.sort(key=lambda m: m[0])
    segments: list[Segment] = []
    pos = 0
    for start, end, line in marks:
        if start > pos:
            segments.append(Segment(text[pos:start]))
        segments.append(Segment(text[start:end], line.line_no, line_css(line)))
        pos = end
    if pos < len(text):
        segments.append(Segment(text[pos:]))
    return segments


@router.get("/clients/{client_id}/orders/{order_id}")
def order_page(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    lines = session.exec(select(OrderLine).where(OrderLine.client_id == client.id, OrderLine.order_id == order.id)
                         .order_by(OrderLine.line_no)).all()
    customer = session.get(Customer, order.customer_id) if order.customer_id else None
    codes = [ln.product_code for ln in lines if ln.product_code]
    descriptions = dict(session.exec(select(CatalogueItem.product_code, CatalogueItem.description).where(
        CatalogueItem.client_id == client.id, CatalogueItem.product_code.in_(codes))).all()) if codes else {}
    classification = CLASSIFICATION_LABELS.get(Classification(order.classification)) \
        if order.classification else None
    return templates.TemplateResponse(request, "orders/order.html", {
        "client": client, "order": order, "lines": lines, "nav": "orders",
        "pill": order_pill(order),
        "segments": highlight_segments(order.raw_text or "", lines),
        "customer": customer if customer and customer.client_id == client.id else None,
        "classification": classification,
        "came_as": SOURCE_LABELS.get(order.source_type, order.source_type),
        "has_original": bool(order.stored_path) and Path(order.stored_path).exists(),
        "line_css": line_css,
        "descriptions": descriptions,
        "match_labels": MATCH_METHOD_LABELS,
        "customer_how": CUSTOMER_HOW.get(order.customer_match_method or "", ""),
        "reading": order.processing_state in (service.READING, service.READ),
    })


@router.get("/clients/{client_id}/orders/{order_id}/original")
def original_file(client_id: str, order_id: int, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    if not order.stored_path or not Path(order.stored_path).exists():
        raise HTTPException(status_code=404)
    name = order.original_filename or "pasted.txt"
    inline = Path(name).suffix.lower() in {".pdf", ".txt"}
    return FileResponse(order.stored_path, filename=name, content_disposition_type="inline" if inline else "attachment")
