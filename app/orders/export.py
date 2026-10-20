"""Exports: approved orders become one file in the client's own format.

Built only from the standard internal order and the client's output template
(pipeline/export.py does the rendering). Only Approved orders are ever exported,
and every export is kept as a file with an ExportBatch, so it can be downloaded
again exactly as it was made, never rebuilt. Every query filters by client_id.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from app.db import client_files_dir, scoped
from app.models import Client, Event, ExportBatch, Order
from app.orders.standard import output_template, standard_order
from pipeline import export
from pipeline.vocab import Status
from pipeline.workdays import LONDON

_lock = threading.Lock()  # two clicks at once must not put the same orders in two files


class ExportRefused(ValueError):
    """Why no file could be made, in plain words."""


@dataclass
class ExportFile:
    batch: ExportBatch
    data: bytes


def short_name(client: Client) -> str:
    name = re.sub(r"[^A-Za-z0-9]", "", client.short_name or client.name)
    return name or client.id


def batch_file_name(client: Client, when: datetime) -> str:
    local = when.astimezone(LONDON)
    return f"{short_name(client)}_orders_{local:%Y-%m-%d_%H%M}.csv"


def order_file_name(client: Client, order: Order) -> str:
    po = re.sub(r"[^A-Za-z0-9-]", "", re.sub(r"^\s*PO[\s#:/-]*", "", order.po_number or "", flags=re.IGNORECASE))
    if po:
        return f"{short_name(client)}_PO{po}.csv"
    return f"{short_name(client)}_order{order.id}.csv"


def exports_folder(client_id: str) -> Path:
    folder = client_files_dir(client_id) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _free_path(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return path


def ready_to_export(session: Session, client: Client) -> list[Order]:
    """Every Approved order not yet exported, oldest first."""
    return session.exec(scoped(Order, client.id).where(Order.status == Status.APPROVED, Order.exported_at.is_(None))
                        .order_by(Order.received_at, Order.id)).all()


def render(session: Session, client: Client, orders: list[Order]) -> tuple[bytes, int]:
    """(file bytes, row count) for these orders in the client's format."""
    template = output_template(session, client.id)
    if template is None:
        raise ExportRefused("Set up this client's output format first (Settings, section 4).")
    standards = [standard_order(session, o) for o in orders]
    text = export.render_orders_text(standards, template)
    rows = sum(len(s["lines"]) for s in standards)
    return export.to_bytes(text, template), rows


def _export(session: Session, client: Client, orders: list[Order], name: str) -> ExportFile:
    for order in orders:
        if order.client_id != client.id or order.status != Status.APPROVED or order.exported_at is not None:
            raise ExportRefused("Only approved orders that have not been exported can go in an export.")
    data, rows = render(session, client, orders)
    path = _free_path(exports_folder(client.id), name)
    path.write_bytes(data)
    now = datetime.now(timezone.utc)
    try:
        batch = ExportBatch(client_id=client.id, created_at=now, file_name=path.name, file_path=str(path),
                            order_ids=[o.id for o in orders], row_count=rows)
        session.add(batch)
        session.flush()
        for order in orders:
            order.status = Status.EXPORTED
            order.exported_at = now
            order.export_batch_id = batch.id
            session.add(order)
            session.add(Event(client_id=client.id, order_id=order.id, type="export",
                              details={"batch_id": batch.id, "file_name": path.name}))
        session.add(Event(client_id=client.id, type="export_batch",
                          details={"batch_id": batch.id, "file_name": path.name, "orders": len(orders),
                                   "rows": rows}))
        session.commit()
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)  # nothing is marked exported, so the file must not exist either
        raise
    session.refresh(batch)
    return ExportFile(batch, data)


def export_batch(session: Session, client: Client) -> ExportFile:
    """One file of every approved order not yet exported. They are then marked Exported."""
    with _lock:
        orders = ready_to_export(session, client)
        if not orders:
            raise ExportRefused("There are no approved orders waiting to be exported.")
        return _export(session, client, orders, batch_file_name(client, datetime.now(timezone.utc)))


def export_one(session: Session, client: Client, order: Order) -> ExportFile:
    """This order alone in the client's format. It counts as exported, so it can't go in a later batch too."""
    with _lock:
        session.refresh(order)
        if order.status == Status.EXPORTED:
            raise ExportRefused("This order has already been exported. Download it again from Past exports.")
        if order.status != Status.APPROVED:
            raise ExportRefused("Approve this order before downloading it.")
        return _export(session, client, [order], order_file_name(client, order))


def batches(session: Session, client: Client) -> list[ExportBatch]:
    return session.exec(scoped(ExportBatch, client.id).order_by(ExportBatch.created_at.desc(), ExportBatch.id.desc())
                        ).all()


def get_batch(session: Session, client: Client, batch_id: int) -> ExportBatch | None:
    return session.exec(scoped(ExportBatch, client.id).where(ExportBatch.id == batch_id)).first()


def stored_file(batch: ExportBatch) -> bytes:
    """The file exactly as it was made. Never rebuilt."""
    path = Path(batch.file_path)
    if not path.exists():
        raise ExportRefused("The file for this export is missing from the data folder. Restore it from a backup.")
    return path.read_bytes()
