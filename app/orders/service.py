"""Taking in orders: store each file as received, then read it in the background.

Reading runs pipeline steps 1 and 2 (ingest, then AI call 1) and saves the
result. Every upload, AI call and result is logged as an Event. Every query
filters by client_id.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session, delete, select

from app.ai_client import get_ai
from app.clientsetup.pending import MAX_UPLOAD_BYTES, safe_filename
from app.config import get_settings
from app.db import client_files_dir, get_engine
from app.models import Client, Event, Order, OrderLine
from pipeline import ingest
from pipeline.ai import AIUnavailable, cost_gbp
from pipeline.classify_extract import classify_and_extract, flag
from pipeline.dates import resolve_delivery_date
from pipeline.status import status_after_reading
from pipeline.vocab import SourceType, Status

READING = "reading"
READ = "read"  # lines read, waiting for matching (Milestone 4)
DONE = "done"
FAILED = "failed"


_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="order-reader")


def run_later(fn: Callable, *args) -> None:
    """Run a job in the background. Swapped for a direct call in tests."""
    _executor.submit(fn, *args)


def log_event(session: Session, client_id: str, order_id: int | None, type_: str, **details) -> None:
    session.add(Event(client_id=client_id, order_id=order_id, type=type_, details=details))


# ---------------------------------------------------------------- receiving


@dataclass
class Refused:
    filename: str
    reason: str


def orders_folder(client_id: str) -> Path:
    folder = client_files_dir(client_id) / "orders"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _store(order: Order, filename: str, data: bytes) -> None:
    path = orders_folder(order.client_id) / f"{order.id:06d}_{safe_filename(filename)}"
    path.write_bytes(data)
    order.stored_path = str(path)


def receive_files(session: Session, client: Client, files: list[tuple[str, bytes]],
                  sender_email: str | None = None, received_at: datetime | None = None,
                  ) -> tuple[list[Order], list[Refused]]:
    """Store uploaded files as new orders and queue each for reading.

    sender_email and received_at, when given, apply to every file in the upload. An email
    file uses its own From and Date unless they are given.
    """
    orders: list[Order] = []
    refused: list[Refused] = []
    now = datetime.now(timezone.utc)
    for filename, data in files:
        filename = Path(filename or "upload").name
        try:
            kind = ingest.detect_type(filename)
        except ingest.NotSupported as exc:
            refused.append(Refused(filename, str(exc)))
            continue
        if not data:
            refused.append(Refused(filename, "This file is empty."))
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            refused.append(Refused(filename, "This file is bigger than 25 MB."))
            continue

        sender, subject, sent_at = (None, None, None)
        if kind == SourceType.EML:
            sender, subject, sent_at = ingest.email_envelope(data)
        order = Order(
            client_id=client.id,
            source_type=kind,
            original_filename=filename,
            sender_email=(sender_email or sender or None),
            subject=subject,
            received_at=received_at or sent_at or now,
            processing_state=READING,
        )
        session.add(order)
        session.flush()
        _store(order, filename, data)
        log_event(session, client.id, order.id, "upload", filename=filename, source_type=kind, size=len(data))
        orders.append(order)
    session.commit()
    for order in orders:
        run_later(read_order, order.id)
    return orders, refused


def receive_paste(session: Session, client: Client, text: str, sender_email: str | None = None,
                  received_at: datetime | None = None) -> Order | None:
    """A pasted email becomes an order like any file. None if nothing was pasted."""
    if not text or not text.strip():
        return None
    order = Order(
        client_id=client.id,
        source_type=SourceType.PASTE,
        sender_email=(sender_email or None),
        received_at=received_at or datetime.now(timezone.utc),
        processing_state=READING,
    )
    session.add(order)
    session.flush()
    _store(order, "pasted.txt", text.encode("utf-8"))
    log_event(session, client.id, order.id, "upload", source_type=SourceType.PASTE, size=len(text))
    session.commit()
    run_later(read_order, order.id)
    return order


def retry_order(session: Session, client: Client, order_id: int) -> Order | None:
    """Read an order again after the AI could not be reached."""
    order = session.exec(select(Order).where(Order.client_id == client.id, Order.id == order_id)).first()
    if order is None or order.processing_state != FAILED:
        return order
    order.processing_state = READING
    order.status = None
    order.flags = []
    session.add(order)
    log_event(session, client.id, order.id, "retry")
    session.commit()
    run_later(read_order, order.id)
    return order


def resume_unfinished() -> None:
    """After a restart, carry on reading any order that was still being read."""
    with Session(get_engine()) as session:
        ids = session.exec(select(Order.id).where(Order.processing_state == READING)).all()
    for order_id in ids:
        run_later(read_order, order_id)


# ---------------------------------------------------------------- reading


def _ingest(order: Order) -> ingest.Ingested:
    data = Path(order.stored_path).read_bytes() if order.stored_path else b""
    if order.source_type == SourceType.PASTE:
        return ingest.ingest_paste(data.decode("utf-8"))
    return ingest.ingest_file(order.original_filename or Path(order.stored_path).name, data)


def _finish(session: Session, order: Order, status: str | None, state: str, started: float) -> None:
    order.status = status
    if status is not None and state != FAILED:
        order.initial_status = status  # set once by the pipeline, never changed
    order.processing_state = state
    order.processing_ms = int((time.monotonic() - started) * 1000)
    session.add(order)
    log_event(session, order.client_id, order.id, "order_read", state=state, status=status,
              processing_ms=order.processing_ms, ai_input_tokens=order.ai_input_tokens,
              ai_output_tokens=order.ai_output_tokens,
              ai_cost_gbp=str(order.ai_cost_gbp) if order.ai_cost_gbp is not None else None)
    session.commit()


def read_order(order_id: int) -> None:
    """Background job: ingest one order, run AI call 1 and save what it found."""
    started = time.monotonic()
    with Session(get_engine()) as session:
        order = session.get(Order, order_id)
        if order is None or order.processing_state != READING:
            return
        client = session.get(Client, order.client_id)
        try:
            _read(session, client, order, started)
        except Exception as exc:  # never leave an order stuck on "Reading"
            session.rollback()
            order = session.get(Order, order_id)
            order.flags = [flag("reading_failed", "Something went wrong reading this file. Try again, or open the "
                                                  "original and paste the order text instead", "problem")]
            log_event(session, order.client_id, order.id, "error", error=type(exc).__name__,
                      trace=traceback.format_exc(limit=8))
            _finish(session, order, Status.PROBLEM, FAILED, started)


def _read(session: Session, client: Client, order: Order, started: float) -> None:
    try:
        found = _ingest(order)
    except ingest.NotSupported as exc:
        order.flags = [flag("not_readable", str(exc), "problem")]
        _finish(session, order, Status.PROBLEM, DONE, started)
        return

    order.raw_text = found.text
    order.content_hash = found.content_hash if found.text else None
    order.subject = order.subject or found.subject
    order.sender_email = order.sender_email or found.sender_email
    notes = [flag("note", note, "info") for note in found.notes]

    if found.problem:
        order.flags = [flag("scanned_pdf" if found.problem == ingest.SCANNED_PDF else "not_readable",
                            found.problem, "problem"), *notes]
        _finish(session, order, Status.PROBLEM, DONE, started)
        return
    if not found.text.strip():
        order.flags = [flag("no_text", "No text could be found in this file", "problem"), *notes]
        _finish(session, order, Status.PROBLEM, DONE, started)
        return

    session.commit()  # don't hold the database while waiting for the AI
    sender = f"{found.sender_name} <{order.sender_email}>" if found.sender_name and order.sender_email \
        else order.sender_email
    try:
        result = classify_and_extract(get_ai(), found.text, client.name, order.received_at, sender,
                                      order.subject)
    except AIUnavailable as exc:
        order.flags = [flag("ai_unavailable", str(exc), "problem"), *notes]
        log_event(session, order.client_id, order.id, "ai_unavailable", message=str(exc))
        _finish(session, order, Status.PROBLEM, FAILED, started)
        return

    settings = get_settings()
    total_cost: Decimal | None = Decimal(0)
    for call in result.calls:
        cost = cost_gbp(call, settings.ai_price_input_per_mtok, settings.ai_price_output_per_mtok)
        total_cost = None if cost is None or total_cost is None else total_cost + cost
        log_event(session, order.client_id, order.id, "ai_call", purpose="classify_extract", model=call.model,
                  input_tokens=call.input_tokens, cache_write_tokens=call.cache_write_tokens,
                  cache_read_tokens=call.cache_read_tokens, output_tokens=call.output_tokens,
                  cost_gbp=str(cost) if cost is not None else None, cached=call.cached,
                  usable=call.input is not None, problem=call.problem)
    order.ai_input_tokens = result.input_tokens
    order.ai_output_tokens = result.output_tokens
    order.ai_cost_gbp = total_cost

    order.classification = result.classification
    order.classification_reason = result.classification_reason
    order.customer_name_raw = result.customer_name
    order.po_number = result.po_number
    order.requested_delivery_raw = result.delivery_raw
    order.requested_delivery_date = resolve_delivery_date(result.delivery_raw, order.received_at)
    order.delivery_address = result.delivery_address
    order.delivery_notes = result.delivery_notes
    order.flags = [*result.flags, *notes]

    session.exec(delete(OrderLine).where(OrderLine.client_id == order.client_id, OrderLine.order_id == order.id))
    for line in result.lines:
        session.add(OrderLine(
            client_id=order.client_id,
            order_id=order.id,
            line_no=line.line_no,
            raw_text=line.raw_text,
            source_span=line.source_span,
            requested_code=line.requested_code,
            requested_description=line.requested_description,
            requested_qty=line.requested_qty,
            requested_unit=line.requested_unit,
            requested_price=line.requested_price,
            status=Status.PROBLEM if any(f["severity"] == Status.PROBLEM for f in line.flags) else None,
            flags=line.flags,
        ))

    problem_lines = [line for line in result.lines if any(f["severity"] == Status.PROBLEM for f in line.flags)]
    if problem_lines:
        n = len(problem_lines)
        first = next(f for f in problem_lines[0].flags if f["severity"] == Status.PROBLEM)["message"]
        summary = f"{first} (line {problem_lines[0].line_no})" if n == 1 else \
            f"{n} lines need a fix, starting with line {problem_lines[0].line_no}: {first.lower()}"
        order.flags = [flag("line_problem", summary, "problem"), *order.flags]

    status = status_after_reading(result.problem, result.classification, result.flags,
                                  [line.flags for line in result.lines])
    _finish(session, order, status, DONE if status else READ, started)


# ---------------------------------------------------------------- demo orders


def load_demo_orders(session: Session, client: Client, seed_dir: Path) -> tuple[list[Order], list[Refused], str | None]:
    """Upload the demo client's 20 test orders, each with the sender and arrival time in its answer key."""
    orders_dir, answers_dir = seed_dir / "orders", seed_dir / "answers"
    if not orders_dir.is_dir() or not answers_dir.is_dir():
        return [], [], "The demo files are missing. Run make seed first."
    all_orders: list[Order] = []
    all_refused: list[Refused] = []
    for answer_path in sorted(answers_dir.glob("*.json")):
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        path = orders_dir / answer["file"]
        if not path.exists():
            all_refused.append(Refused(answer["file"], "File missing. Run make seed again."))
            continue
        received = datetime.fromisoformat(answer["received_at"].replace("Z", "+00:00"))
        orders, refused = receive_files(session, client, [(path.name, path.read_bytes())],
                                        sender_email=answer.get("sender_email"), received_at=received)
        all_orders += orders
        all_refused += refused
    return all_orders, all_refused, None
