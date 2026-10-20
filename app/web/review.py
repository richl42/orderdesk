"""Screen 4: review one order. Split view, editable header and lines, checks, export preview, approve.

Every change posts to its own small route and gets the whole review area back, so
the status, checks and export preview always match what was just changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from rapidfuzz import fuzz
from sqlmodel import Session

from app.db import get_session, scoped
from app.models import CatalogueItem, Client, Customer, ExportBatch, Order, OrderLine
from app.orders import review
from app.orders.standard import output_template, standard_order
from app.web.orders import get_order, is_htmx, order_pill
from app.web.pages import get_client
from app.web.templating import templates
from pipeline import checks, export
from pipeline.customers import CustomerRecord, name_scores
from pipeline.normalise import basic, code_key
from pipeline.units import canonical_unit, plural, unit_lookup
from pipeline.vocab import CLASSIFICATION_LABELS, MATCH_METHOD_LABELS, SOURCE_LABELS, Classification, Status

router = APIRouter()

CUSTOMER_HOW = {
    "email": "Matched by sender email",
    "domain": "Matched by email domain",
    "name": "Matched by the name in the order only",
    "operator": "Chosen by you",
}
SEARCH_LIMIT = 12
SEARCH_MIN_SCORE = 80  # each word typed must be close to a word in the description


# ---------------------------------------------------------------- highlights in the original text


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


# ---------------------------------------------------------------- what the screen shows


@dataclass
class Choice:
    code: str
    description: str
    note: str = ""
    sell: str = ""  # unit labels for this product, so the unit dropdown can follow the choice
    inner: str = ""


@dataclass
class LineView:
    line: OrderLine
    item: CatalogueItem | None
    how: str
    choices: list[Choice]
    qty: str
    unit_choice: str
    open: bool
    messages: list[str]


@dataclass
class CheckView:
    ok: bool
    text: str


@dataclass
class ReviewView:
    order: Order
    lines: list[LineView]
    customer: Customer | None
    customer_options: list[tuple[int, str]]
    customer_how: str
    po_note: str
    checks: list[CheckView]
    preview: str
    approve_reasons: list[str]
    position: tuple[int, int] | None
    next_href: str | None
    question: review.Question
    editable: bool
    error: str | None = None
    notice: str | None = None
    flags: list[dict] = field(default_factory=list)


def _choice(item: CatalogueItem, note: str = "") -> Choice:
    inner = ""
    if item.inner_unit and item.pack_size and item.pack_size > 1:
        inner = f"{plural(item.inner_unit)} ({item.pack_size} per {item.sell_unit.lower()})"
    return Choice(item.product_code, item.description, note, plural(item.sell_unit), inner)


def _line_view(line: OrderLine, items: dict[str, CatalogueItem], lookup: dict[str, str], open_id: int | None,
               editable: bool) -> LineView:
    item = items.get(line.product_code) if line.product_code else None
    choices: list[Choice] = []
    if item is not None:
        choices.append(_choice(item, "current"))
    for cand in line.candidates or []:
        cand_item = items.get(cand.get("product_code"))
        if cand_item is not None and all(c.code != cand_item.product_code for c in choices):
            choices.append(_choice(cand_item, cand.get("source") or ""))
    choices = choices[:5]
    if line.qty is not None:
        qty, unit_choice = format(line.qty.normalize(), "f"), "sell"
        if line.conversion_note and item is not None and item.pack_size:
            qty, unit_choice = str(int(line.qty * item.pack_size)), "inner"
    else:
        qty, unit_choice = line.requested_qty or "", "sell"
        written = canonical_unit(line.requested_unit, lookup) if line.requested_unit else None
        target = item or (items.get(choices[0].code) if choices else None)
        if written and target is not None and target.inner_unit and written.lower() == target.inner_unit.lower():
            unit_choice = "inner"
    how = MATCH_METHOD_LABELS.get(line.match_method, "") if line.match_method else ""
    messages = [f["message"] for f in line.flags or [] if f.get("severity") in (Status.PROBLEM, Status.CHECK)]
    return LineView(line, item, how, choices, qty, unit_choice,
                    editable and (line.status != Status.READY or line.id == open_id), messages)


def _checks(order: Order, lines: list[OrderLine], customer: Customer | None, rules: dict) -> list[CheckView]:
    codes = {f.get("code"): f for f in order.flags or []}
    out = []
    if customer is not None:
        out.append(CheckView(order.customer_match_method in ("email", "domain", "operator"),
                             f"Customer: {customer.name} ({CUSTOMER_HOW.get(order.customer_match_method, '').lower()})"))
    else:
        out.append(CheckView(False, "Customer not recognised"))
    for code in ("customer_conflict", "duplicate", "po_missing", "po_not_in_text"):
        if code in codes:
            out.append(CheckView(False, codes[code]["message"]))
    if "duplicate" not in codes:
        out.append(CheckView(True, f"PO {order.po_number} not seen before" if order.po_number
                             else "No PO number" + ("" if rules.get("require_po_number") else " (not needed)")))
    dated = [c for c in ("delivery_past", "date_unclear", "cutoff") if c in codes]
    for code in dated:
        out.append(CheckView(False, codes[code]["message"]))
    if not dated:
        when = order.requested_delivery_date
        out.append(CheckView(True, f"Delivery {when:%a} {when.day} {when:%b %Y}" if when else
                             "No delivery date asked for"))
    priced = [ln for ln in lines if ln.qty is not None and ln.unit_price is not None]
    if "minimum_order" in codes:
        out.append(CheckView(False, codes["minimum_order"]["message"]))
    elif priced and len(priced) == len(lines):
        value = sum((ln.qty * ln.unit_price for ln in priced), Decimal(0))
        out.append(CheckView(True, f"Order value {checks.money(value)}"))
    price_flags = [(ln.line_no, f["message"]) for ln in lines for f in ln.flags or []
                   if f.get("code") in ("price_difference", "no_price")]
    for no, message in price_flags:
        out.append(CheckView(False, f"Line {no}: {message}"))
    if not price_flags and lines:
        out.append(CheckView(True, "Prices from their price band or the catalogue"))
    waiting = [ln.line_no for ln in lines if ln.status != Status.READY]
    if lines:
        out.append(CheckView(not waiting, f"All {len(lines)} lines matched" if not waiting else
                             f"Line{'s' if len(waiting) > 1 else ''} {', '.join(map(str, waiting))} "
                             f"need{'' if len(waiting) > 1 else 's'} you"))
    return out


def _preview(session: Session, order: Order, lines: list[OrderLine]) -> str:
    template = output_template(session, order.client_id)
    if template is None:
        return "Set up the output format to see this order in their format."
    standard = standard_order(session, order, lines)
    for line in standard["lines"]:
        if not line["product_code"]:
            line["product_code"] = "[pick product]"
    return export.render_orders_text([standard], template)


def customer_options(session: Session, client: Client, order: Order) -> list[tuple[int, str]]:
    """Closest names to what the order says first, then every customer A to Z."""
    everyone = session.exec(scoped(Customer, client.id).order_by(Customer.name)).all()
    first: list[Customer] = []
    if order.customer_name_raw:
        records = [CustomerRecord(c.id, c.account_code, c.name) for c in everyone]
        ids = [r.id for score, r in name_scores(order.customer_name_raw, records)[:3] if score >= 50]
        first = [c for i in ids for c in everyone if c.id == i]
    rest = [c for c in everyone if c not in first]
    return [(c.id, f"{c.name} ({c.account_code})") for c in first + rest]


def review_view(session: Session, client: Client, order: Order, error: str | None = None,
                notice: str | None = None, open_id: int | None = None) -> ReviewView:
    lines = review.lines_of(session, order)
    items = {i.product_code: i for i in session.exec(scoped(CatalogueItem, client.id)).all()}
    customer = session.get(Customer, order.customer_id) if order.customer_id else None
    if customer is not None and customer.client_id != client.id:
        customer = None
    editable = review.editable(order)
    lookup = unit_lookup(client.unit_synonyms or {})
    codes = {f.get("code"): f for f in order.flags or []}
    nxt = review.next_order(session, client, order)
    rules = client.rules or {}
    return ReviewView(
        order=order,
        lines=[_line_view(ln, items, lookup, open_id, editable) for ln in lines],
        customer=customer,
        customer_options=customer_options(session, client, order),
        customer_how=CUSTOMER_HOW.get(order.customer_match_method or "", ""),
        po_note=codes["duplicate"]["message"] if "duplicate" in codes else "Not seen before",
        checks=_checks(order, lines, customer, rules) if order.classification == Classification.NEW_ORDER else [],
        preview=_preview(session, order, lines) if order.classification == Classification.NEW_ORDER else "",
        approve_reasons=review.approve_problems(session, order),
        position=review.queue_position(session, client, order),
        next_href=f"/clients/{client.id}/orders/{nxt.id}" if nxt else None,
        question=review.ask_client(session, client, order),
        editable=editable,
        error=error,
        notice=notice,
        flags=[f for f in order.flags or [] if f.get("severity") in (Status.PROBLEM, Status.CHECK)],
    )


def context(session: Session, client: Client, order: Order, **kwargs) -> dict:
    view = review_view(session, client, order, **kwargs)
    classification = CLASSIFICATION_LABELS.get(Classification(order.classification)) \
        if order.classification else None
    return {
        "client": client, "order": order, "v": view, "nav": "orders", "pill": order_pill(order),
        "segments": highlight_segments(order.raw_text or "", [lv.line for lv in view.lines]),
        "line_css": line_css, "classification": classification,
        "came_as": SOURCE_LABELS.get(order.source_type, order.source_type),
        "reading": order.processing_state in ("reading", "read"),
        "batch": session.get(ExportBatch, order.export_batch_id) if order.export_batch_id else None,
    }


def area(request: Request, session: Session, client: Client, order: Order, **kwargs):
    """The refreshed review area for HTMX, or a redirect back to the page."""
    if not is_htmx(request):
        return RedirectResponse(f"/clients/{client.id}/orders/{order.id}", status_code=303)
    session.refresh(order)
    return templates.TemplateResponse(request, "review/area.html", context(session, client, order, **kwargs))


def _act(request: Request, session: Session, client: Client, order: Order, action, open_id: int | None = None,
         notice: str | None = None):
    try:
        action()
    except review.ReviewRefused as refused:
        session.rollback()
        return area(request, session, client, order, error=str(refused), open_id=open_id)
    return area(request, session, client, order, notice=notice)


# ---------------------------------------------------------------- routes


@router.get("/clients/{client_id}/orders/{order_id}")
def review_page(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session),
                approved: str | None = None):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    review.open_review(session, order)
    notice = "Approved and added to the export." if approved else None
    return templates.TemplateResponse(request, "review/page.html", context(session, client, order, notice=notice))


@router.post("/clients/{client_id}/orders/{order_id}/customer")
async def change_customer(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    value = str((await request.form()).get("customer_id") or "")
    customer_id = int(value) if value.isdigit() else None
    return _act(request, session, client, order, lambda: review.set_customer(session, client, order, customer_id))


@router.post("/clients/{client_id}/orders/{order_id}/header")
async def change_header(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    form = await request.form()
    field_name, value = str(form.get("field") or ""), str(form.get("value") or "")
    return _act(request, session, client, order, lambda: review.set_header(session, client, order, field_name, value))


@router.post("/clients/{client_id}/orders/{order_id}/lines/add")
def add_line(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return _act(request, session, client, order, lambda: review.add_line(session, client, order))


@router.post("/clients/{client_id}/orders/{order_id}/lines/{line_id}")
async def change_line(client_id: str, order_id: int, line_id: int, request: Request,
                      session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    form = await request.form()
    product = str(form.get("product_code") or "").strip()
    qty = str(form.get("qty") or "")
    unit_choice = str(form.get("unit_choice") or "sell")
    remember = form.get("remember") in ("on", "1", "true")
    return _act(request, session, client, order,
                lambda: review.save_line(session, client, order, line_id, product, qty, unit_choice, remember),
                open_id=line_id)


@router.post("/clients/{client_id}/orders/{order_id}/lines/{line_id}/delete")
def delete_line(client_id: str, order_id: int, line_id: int, request: Request,
                session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return _act(request, session, client, order, lambda: review.delete_line(session, client, order, line_id))


@router.get("/clients/{client_id}/orders/{order_id}/lines/{line_id}/edit")
def edit_line(client_id: str, order_id: int, line_id: int, request: Request,
              session: Session = Depends(get_session)):
    """Open a Ready line for changing."""
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return area(request, session, client, order, open_id=line_id)


@router.post("/clients/{client_id}/orders/{order_id}/approve")
async def approve(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    seconds_text = str((await request.form()).get("seconds") or "")
    seconds = int(seconds_text) if seconds_text.isdigit() else None
    try:
        saved = review.approve(session, client, order, seconds)
    except review.ReviewRefused as refused:
        session.rollback()
        return area(request, session, client, order, error=str(refused))
    nxt = review.next_order(session, client, order)
    target = f"/clients/{client.id}/orders/{nxt.id}?approved=1" if nxt else \
        f"/clients/{client.id}/orders?approved={order.id}"
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": target, "X-Aliases-Saved": str(saved)})
    return RedirectResponse(target, status_code=303)


@router.post("/clients/{client_id}/orders/{order_id}/undo-approval")
def undo_approval(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return _act(request, session, client, order, lambda: review.undo_approval(session, client, order),
                notice="Approval undone. The order is back in review.")


@router.post("/clients/{client_id}/orders/{order_id}/pass-on")
def pass_on(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return _act(request, session, client, order, lambda: review.pass_on(session, client, order),
                notice="Marked as not an order. Use Ask client contact to pass it on.")


@router.post("/clients/{client_id}/orders/{order_id}/back-to-review")
def back_to_review(client_id: str, order_id: int, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    return _act(request, session, client, order, lambda: review.back_to_review(session, client, order),
                notice="Back in review. Add its lines if there are none.")


@router.get("/clients/{client_id}/orders/{order_id}/next")
def next_order(client_id: str, order_id: int, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    order = get_order(session, client, order_id)
    nxt = review.next_order(session, client, order)
    return RedirectResponse(f"/clients/{client.id}/orders/{nxt.id}" if nxt else f"/clients/{client.id}/orders",
                            status_code=303)


@router.get("/clients/{client_id}/review")
def start_reviewing(client_id: str, session: Session = Depends(get_session)):
    """Start reviewing: the oldest order that needs review."""
    client = get_client(session, client_id)
    nxt = review.next_order(session, client)
    return RedirectResponse(f"/clients/{client.id}/orders/{nxt.id}" if nxt else f"/clients/{client.id}/orders",
                            status_code=303)


def _words_score(words: list[str], description_words: list[str]) -> float:
    """How well every typed word matches some word of a description (the weakest word decides)."""
    if not words or not description_words:
        return 0
    return min(max(fuzz.ratio(w, d) for d in description_words) for w in words)


@router.get("/clients/{client_id}/catalogue/search")
def search_catalogue(client_id: str, request: Request, q: str = "", line: int = 0,
                     session: Session = Depends(get_session)):
    """Type to search the whole catalogue, for a line's product picker. Only this client's products."""
    client = get_client(session, client_id)
    query = (q or "").strip()
    found: list[CatalogueItem] = []
    if len(query) >= 2:
        rows = session.exec(scoped(CatalogueItem, client.id)).all()
        key, code = basic(query), code_key(query)
        words = key.split()
        scored = []
        for item in rows:
            description = basic(item.description)
            if code and (code_key(item.product_code).startswith(code) or (item.barcode or "") == code):
                score = 300
            elif words and all(w in description for w in words):
                score = 200  # every word typed is in the description
            else:
                score = _words_score(words, description.split())
            if score >= SEARCH_MIN_SCORE:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].product_code))
        found = [item for _, item in scored[:SEARCH_LIMIT]]
    return templates.TemplateResponse(request, "review/search.html", {
        "choices": [_choice(item) for item in found], "line_id": line, "query": query,
    })
