"""Shadow trial: load the client's staff entries, compare them with our orders, and work out the measures.

The comparison itself is pipeline/compare.py. This module reads and writes the
database: staff uploads, Comparison rows (with the operator's notes and fix
tags, kept across re-uploads), the summary paragraph, and what the trial screen
and client report show. Every query filters by client_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session, delete

from app.clientsetup.importers import ImportResult, MappingIncomplete, save_mapping, store_setup_file
from app.db import scoped
from app.models import (
    CatalogueItem,
    Client,
    Comparison,
    Customer,
    Event,
    Order,
    OrderLine,
    StaffEntryLine,
    StaffEntryUpload,
)
from pipeline import compare, units
from pipeline.normalise import code_key
from pipeline.products import Item
from pipeline.tables import Table, column_for, detect_date_format, missing_required, parse_decimal
from pipeline.vocab import FIX_TAG_LABELS, ComparisonResult, FixTag, MappingKind, Status
from pipeline.workdays import london_date, london_now, trial_day, working_days

OURS = [Status.APPROVED, Status.EXPORTED]


# ---------------------------------------------------------------- loading the staff file


def _parse_date(text: str, fmt: str | None) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for candidate in [fmt, "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"]:
        if not candidate:
            continue
        try:
            return datetime.strptime(text.split(" ")[0], candidate).date()
        except ValueError:
            continue
    return None


def import_staff_entries(session: Session, client: Client, path: Path, table: Table, mapping: dict[str, str],
                         filename: str | None = None) -> ImportResult:
    """Replace the client's staff entries with this file, then compare. The operator's notes are kept."""
    missing = missing_required(mapping, MappingKind.STAFF_ENTRIES)
    if missing:
        raise MappingIncomplete(missing)
    result = ImportResult()

    def col(fld: str) -> str | None:
        return column_for(mapping, fld)

    formats = {fld: detect_date_format([r.get(col(fld), "") for r in table.rows if col(fld) and r.get(col(fld))])
               for fld in ("order_date", "delivery_date")}
    notes = _saved_notes(session, client)
    for old in session.exec(scoped(StaffEntryUpload, client.id)).all():
        session.exec(delete(Comparison).where(Comparison.client_id == client.id, Comparison.upload_id == old.id))
        session.exec(delete(StaffEntryLine).where(StaffEntryLine.client_id == client.id,
                                                  StaffEntryLine.upload_id == old.id))
        session.delete(old)
    session.flush()

    stored = store_setup_file(client.id, path, MappingKind.STAFF_ENTRIES)
    record = save_mapping(session, client.id, MappingKind.STAFF_ENTRIES, table, mapping, filename or path.name, stored)
    session.flush()
    upload = StaffEntryUpload(client_id=client.id, filename=filename or path.name, stored_path=str(stored),
                              mapping_id=record.id)
    session.add(upload)
    session.flush()

    for row_no, row in enumerate(table.rows, start=1):
        def value(fld: str) -> str:
            c = col(fld)
            return (row.get(c) or "").strip() if c else ""

        code, account = value("product_code"), value("account_code")
        if not code or not account:
            if any(row.values()):
                result.skipped_rows.append(row_no)
            continue
        qty_text, price_text = value("qty"), value("unit_price")
        qty = parse_decimal(qty_text)
        if qty_text and qty is None:
            result.warnings.append(f"Row {row_no}: quantity '{qty_text}' is not a number")
        session.add(StaffEntryLine(
            client_id=client.id, upload_id=upload.id, po_number=value("po_number") or None, account_code=account,
            order_date=_parse_date(value("order_date"), formats["order_date"]), product_code=code, qty=qty,
            unit=value("unit") or None, unit_price=parse_decimal(price_text),
            delivery_date=_parse_date(value("delivery_date"), formats["delivery_date"]),
        ))
        result.count += 1
    upload.row_count = result.count
    session.add(upload)
    session.add(Event(client_id=client.id, type="staff_entries_upload",
                      details={"filename": upload.filename, "rows": result.count}))
    session.commit()
    run_comparison(session, client, notes)
    return result


# ---------------------------------------------------------------- comparing


def latest_upload(session: Session, client: Client) -> StaffEntryUpload | None:
    return session.exec(scoped(StaffEntryUpload, client.id).order_by(StaffEntryUpload.uploaded_at.desc(),
                                                                     StaffEntryUpload.id.desc())).first()


def _converter(session: Session, client: Client):
    items = {code_key(i.product_code): i for i in session.exec(scoped(CatalogueItem, client.id)).all()}
    synonyms = client.unit_synonyms or {}

    def to_sell_unit(code: str | None, qty: Decimal | None, unit: str | None) -> tuple[Decimal | None, str | None]:
        item = items.get(code_key(code))
        if item is None or qty is None:
            return qty, (unit or "").upper() or None
        if not unit:
            return qty, item.sell_unit
        conversion = units.convert(format(qty, "f"), unit, Item(item.product_code, item.description, item.sell_unit,
                                                                 item.pack_size, item.inner_unit, item.barcode),
                                   synonyms)
        if conversion.qty is not None:
            return conversion.qty, conversion.unit
        return qty, unit.upper()  # left as they wrote it, so it shows up as a difference

    return to_sell_unit


def _our_orders(session: Session, client: Client, first: date | None, last: date | None) -> list[compare.OurOrder]:
    rows = session.exec(scoped(Order, client.id).where(Order.status.in_(OURS)).order_by(Order.received_at)).all()
    customers = {c.id: c.account_code for c in session.exec(scoped(Customer, client.id)).all()}
    out = []
    for order in rows:
        day = london_date(order.received_at)
        if (first and day < first) or (last and day > last):
            continue
        lines = session.exec(scoped(OrderLine, client.id).where(OrderLine.order_id == order.id)
                             .order_by(OrderLine.line_no)).all()
        out.append(compare.OurOrder(
            order.id, customers.get(order.customer_id), order.po_number, day, order.requested_delivery_date,
            [compare.CompLine(ln.product_code, ln.qty, ln.unit, ln.unit_price) for ln in lines],
            order.initial_status, order.first_pass,
        ))
    return out


def _staff_orders(session: Session, client: Client, upload: StaffEntryUpload) -> list[compare.TheirOrder]:
    rows = session.exec(scoped(StaffEntryLine, client.id).where(StaffEntryLine.upload_id == upload.id)
                        .order_by(StaffEntryLine.id)).all()
    staff = [compare.StaffRow(r.po_number, r.account_code, r.order_date, r.product_code, r.qty, r.unit, r.unit_price,
                              r.delivery_date) for r in rows]
    return compare.group_staff_rows(staff, _converter(session, client))


def _date_range(theirs: list[compare.TheirOrder], client: Client) -> tuple[date | None, date | None]:
    dates = [t.order_date for t in theirs if t.order_date]
    if dates:
        return min(dates), max(dates)
    if client.trial_start_date:
        days = working_days(client.trial_start_date, client.trial_working_days)
        return days[0], days[-1]
    return None, None


def _note_key(order_id: int | None, staff_key: str, diff: dict) -> tuple:
    return (order_id or staff_key, diff.get("field"), diff.get("line"))


def _saved_notes(session: Session, client: Client) -> dict[tuple, dict]:
    saved = {}
    for comp in session.exec(scoped(Comparison, client.id)).all():
        for diff in comp.differences or []:
            if diff.get("note") or diff.get("tag"):
                saved[_note_key(comp.order_id, comp.staff_po_number or "", diff)] = {"note": diff.get("note", ""),
                                                                                    "tag": diff.get("tag")}
    return saved


def run_comparison(session: Session, client: Client, notes: dict[tuple, dict] | None = None) -> list[Comparison]:
    """Compare our orders with the latest staff entries and store the result. Keeps the operator's notes."""
    upload = latest_upload(session, client)
    if upload is None:
        return []
    notes = _saved_notes(session, client) if notes is None else notes
    session.exec(delete(Comparison).where(Comparison.client_id == client.id))
    theirs = _staff_orders(session, client, upload)
    first, last = _date_range(theirs, client)
    pairs = compare.pair_orders(_our_orders(session, client, first, last), theirs)
    stored = []
    for pair in pairs:
        staff_key = pair.theirs.key if pair.theirs else ""
        order_id = pair.ours.order_id if pair.ours else None
        diffs = []
        for diff in pair.differences:
            kept = notes.get(_note_key(order_id, staff_key, diff), {})
            diffs.append({**diff, "note": kept.get("note", ""), "tag": kept.get("tag")})
        comp = Comparison(client_id=client.id, upload_id=upload.id, order_id=order_id, staff_po_number=staff_key,
                          staff_account_code=pair.theirs.account_code if pair.theirs else None,
                          result=pair.result, differences=diffs)
        session.add(comp)
        stored.append(comp)
    session.commit()
    return stored


def set_note(session: Session, client: Client, comparison_id: int, index: int, note: str | None,
             tag: str | None) -> Comparison:
    comp = session.exec(scoped(Comparison, client.id).where(Comparison.id == comparison_id)).first()
    if comp is None or not 0 <= index < len(comp.differences or []):
        raise ValueError("That difference is not on this trial.")
    diffs = [dict(d) for d in comp.differences]
    if note is not None:
        diffs[index]["note"] = note.strip()[:500]
    if tag is not None:
        diffs[index]["tag"] = tag if tag in {t.value for t in FixTag} else None
    comp.differences = diffs
    session.add(comp)
    session.add(Event(client_id=client.id, order_id=comp.order_id, type="difference_note",
                      details={"comparison_id": comp.id, "index": index, "tag": diffs[index].get("tag")}))
    session.commit()
    return comp


# ---------------------------------------------------------------- what the screen and report show


@dataclass
class DayTile:
    day: date
    label: str  # "Mon 12 Oct"
    state: str  # done, today, upcoming, empty
    right: int = 0
    compared: int = 0

    @property
    def pct(self) -> str:
        return f"{round(100 * self.right / self.compared)}%" if self.compared else ""


@dataclass
class DiffRow:
    comparison_id: int
    index: int
    order_label: str
    order_href: str | None
    field: str
    ours: str
    theirs: str
    note: str
    tag: str | None


@dataclass
class TrialView:
    upload: StaffEntryUpload | None
    measures: compare.Measures | None
    days: list[DayTile]
    differences: list[DiffRow]
    first: date | None
    last: date | None
    trial_day: int | None
    matched_by_po: int = 0
    staff_orders: int = 0
    only_ours: int = 0
    only_theirs: int = 0
    staff_errors: int = 0
    summary: str = ""
    summary_edited: bool = False
    tags: dict = field(default_factory=lambda: dict(FIX_TAG_LABELS))


def _pairs_from_db(session: Session, client: Client, comps: list[Comparison]) -> list[compare.Pairing]:
    upload = latest_upload(session, client)
    theirs = {t.key: t for t in _staff_orders(session, client, upload)} if upload else {}
    first, last = _date_range(list(theirs.values()), client)
    ours = {o.order_id: o for o in _our_orders(session, client, first, last)}
    pairs = []
    for comp in comps:
        pairs.append(compare.Pairing(comp.result, ours.get(comp.order_id), theirs.get(comp.staff_po_number),
                                     comp.differences or []))
    return pairs


def _order_label(session: Session, client: Client, comp: Comparison) -> str:
    order = session.get(Order, comp.order_id) if comp.order_id else None
    if order is not None and order.client_id == client.id:
        customer = session.get(Customer, order.customer_id) if order.customer_id else None
        who = customer.name if customer else "Unknown customer"
        return f"PO {order.po_number} · {who}" if order.po_number else f"{who}, {london_date(order.received_at):%d/%m}"
    po = (comp.staff_po_number or "").split("|")
    return f"Staff entry {po[1] or 'with no PO'} · {comp.staff_account_code or ''}".strip(" ·")


def summary_text(client: Client, view: TrialView) -> str:
    m = view.measures
    if m is None or not m.compared:
        return ""
    span = f"{view.first.day} {view.first:%B} to {view.last.day} {view.last:%B %Y}" if view.first and view.last \
        else "the trial"
    system = client.erp_system or "your system"
    parts = [f"From {span}, Order Desk processed {m.compared} of your orders alongside your team, without entering "
             f"anything into {system}."]
    parts.append(f"{m.exactly_right} of {m.compared} ({m.pct(m.exactly_right):g}%) matched what your staff entered "
                 f"exactly.")
    if m.confidently_wrong == 0:
        parts.append("None of the orders the system was sure about turned out to be wrong.")
    else:
        parts.append(f"{m.confidently_wrong} order{'s' if m.confidently_wrong != 1 else ''} the system was sure about "
                     f"turned out to be wrong; {'each is' if m.confidently_wrong != 1 else 'it is'} explained below.")
    parts.append(f"{m.flagged} order{'s' if m.flagged != 1 else ''} ({m.pct(m.flagged):g}%) "
                 f"{'were' if m.flagged != 1 else 'was'} flagged for a person to check before approval, which is how "
                 f"the service is meant to work.")
    if view.differences:
        n = len(view.differences)
        parts.append(f"The {n} difference{'s' if n != 1 else ''} from your team's entries "
                     f"{'are' if n != 1 else 'is'} listed below with the cause of each"
                     + (f"; {view.staff_errors} of them {'were' if view.staff_errors != 1 else 'was'} a slip in the "
                        f"staff entry rather than ours." if view.staff_errors else "."))
    parts.append(f"Handling these orders for you would have saved your team about {m.hours_saved:g} hours "
                 f"({client.minutes_per_order} minutes per order).")
    return " ".join(parts)


def trial_view(session: Session, client: Client, today: date | None = None) -> TrialView:
    today = today or london_now().date()
    upload = latest_upload(session, client)
    comps = session.exec(scoped(Comparison, client.id).order_by(Comparison.id)).all() if upload else []
    pairs = _pairs_from_db(session, client, comps)
    theirs_count = sum(1 for p in pairs if p.theirs is not None)
    first, last = (_date_range([p.theirs for p in pairs if p.theirs], client) if pairs else (None, None))
    reviewed = [o.review_seconds for o in session.exec(scoped(Order, client.id).where(
        Order.status.in_(OURS), Order.review_seconds.is_not(None))).all()
        if (first is None or london_date(o.received_at) >= first) and (last is None or london_date(o.received_at) <= last)]
    m = compare.measures(pairs, reviewed, client.minutes_per_order) if upload else None

    days: list[DayTile] = []
    if client.trial_start_date:
        for day in working_days(client.trial_start_date, client.trial_working_days):
            on_day = [p for p in pairs if p.result in (ComparisonResult.EXACT, ComparisonResult.DIFFERENT)
                      and p.ours and p.ours.order_date == day]
            if on_day:
                state = "done"
            elif day == today:
                state = "today"
            elif day > today:
                state = "upcoming"
            else:
                state = "empty"
            days.append(DayTile(day, f"{day:%a} {day.day} {day:%b}", state,
                                sum(compare.is_exactly_right(p) for p in on_day), len(on_day)))

    rows: list[DiffRow] = []
    for comp in comps:
        label = _order_label(session, client, comp)
        href = f"/clients/{client.id}/orders/{comp.order_id}" if comp.order_id else None
        for i, diff in enumerate(comp.differences or []):
            line = f" ({diff['line']})" if diff.get("line") and diff.get("field") in ("quantity", "price") else ""
            rows.append(DiffRow(comp.id, i, label, href, f"{diff['field'].capitalize()}{line}", diff.get("ours", ""),
                                diff.get("theirs", ""), diff.get("note", ""), diff.get("tag")))

    view = TrialView(
        upload=upload, measures=m, days=days, differences=rows, first=first, last=last,
        trial_day=trial_day(client.trial_start_date, client.trial_working_days, today)
        if client.trial_start_date else None,
        matched_by_po=sum(1 for p in pairs if p.ours and p.theirs and p.theirs.po_number),
        staff_orders=theirs_count,
        only_ours=sum(1 for p in pairs if p.result == ComparisonResult.ONLY_OURS),
        only_theirs=sum(1 for p in pairs if p.result == ComparisonResult.ONLY_THEIRS),
        staff_errors=sum(1 for r in rows if r.tag == FixTag.STAFF_ERROR),
    )
    view.summary = (upload.summary if upload and upload.summary else summary_text(client, view))
    view.summary_edited = bool(upload and upload.summary)
    return view


def save_summary(session: Session, client: Client, text: str | None) -> None:
    """Keep the operator's wording. Empty text goes back to the generated paragraph."""
    upload = latest_upload(session, client)
    if upload is None:
        raise ValueError("Upload staff entries first.")
    upload.summary = (text or "").strip() or None
    session.add(upload)
    session.commit()


def trial_dates(client: Client) -> list[date]:
    return working_days(client.trial_start_date, client.trial_working_days) if client.trial_start_date else []

