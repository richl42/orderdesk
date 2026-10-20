"""Screen 4 actions: correct an order, approve it, pass it on, and learn from every correction.

Every change is logged as an Event, counts as a correction, and re-scores the
order in code (no AI). initial_status is never touched. Every query filters by
client_id, and every id from the screen is checked against the order it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlmodel import Session, select

from app.db import scoped
from app.models import Alias, CatalogueItem, Client, Customer, Event, Order, OrderLine
from app.orders.matching import CUSTOMER_CODES, score_order
from pipeline import products, units
from pipeline.vocab import CLASSIFICATION_LABELS, Classification, CustomerMatch, MatchMethod, Status
from pipeline.workdays import LONDON

WAITING = [Status.READY, Status.CHECK, Status.PROBLEM]
MAX_REVIEW_SECONDS = 2 * 60 * 60  # a page left open over lunch should not count as review time

# Problems from reading that no longer apply once the operator types the lines in by hand.
READING_PROBLEMS = {"scanned_pdf", "not_readable", "no_text", "unreadable", "no_lines"}

# Flags about a header field that no longer apply once the operator has set that field.
HEADER_FLAGS = {
    "po_number": {"po_not_in_text"},
    "requested_delivery_date": {"date_not_in_text", "date_unclear"},
    "delivery_address": {"address_not_in_text"},
    "delivery_notes": {"notes_not_in_text"},
}


class ReviewRefused(ValueError):
    """An action that can't be done, with the reason in plain words."""


def _event(session: Session, order: Order, type_: str, **details) -> None:
    session.add(Event(client_id=order.client_id, order_id=order.id, type=type_, details=details))


def lines_of(session: Session, order: Order) -> list[OrderLine]:
    return session.exec(scoped(OrderLine, order.client_id).where(OrderLine.order_id == order.id)
                        .order_by(OrderLine.line_no)).all()


def get_line(session: Session, order: Order, line_id: int) -> OrderLine:
    line = session.exec(scoped(OrderLine, order.client_id).where(OrderLine.order_id == order.id,
                                                                 OrderLine.id == line_id)).first()
    if line is None:
        raise ReviewRefused("That line is not on this order.")
    return line


def editable(order: Order) -> bool:
    return order.status in WAITING and order.processing_state == "done"


def _require_editable(order: Order) -> None:
    if order.status in (Status.APPROVED, Status.EXPORTED):
        raise ReviewRefused("This order is approved. Undo the approval to change it.")
    if order.processing_state != "done":
        raise ReviewRefused("This order is still being read.")


def _rescore(session: Session, client: Client, order: Order) -> str:
    result = score_order(session, client, order, lines_of(session, order))
    session.add(order)
    session.commit()
    return result


def _corrected(session: Session, order: Order, what: str, before, after) -> None:
    order.corrections_count += 1
    _event(session, order, "correction", field=what, before=str(before) if before is not None else None,
           after=str(after) if after is not None else None)


def open_review(session: Session, order: Order) -> None:
    if order.review_started_at is None and order.processing_state == "done":
        order.review_started_at = datetime.now(timezone.utc)
        session.add(order)
        _event(session, order, "review_opened")
        session.commit()


# ---------------------------------------------------------------- the header


def set_customer(session: Session, client: Client, order: Order, customer_id: int | None) -> None:
    _require_editable(order)
    customer = session.get(Customer, customer_id) if customer_id else None
    if customer_id and (customer is None or customer.client_id != client.id):
        raise ReviewRefused("That customer is not one of this client's customers.")
    before = order.customer_id
    order.customer_id = customer.id if customer else None
    order.customer_match_method = CustomerMatch.OPERATOR if customer else None
    order.flags = [f for f in order.flags or [] if f.get("code") not in CUSTOMER_CODES]
    if not customer:
        order.flags.append({"code": "unknown_customer", "message": "Choose the customer", "severity": "problem"})
    _corrected(session, order, "customer", before, order.customer_id)
    _rescore(session, client, order)


def set_header(session: Session, client: Client, order: Order, field: str, value: str) -> None:
    _require_editable(order)
    if field not in HEADER_FLAGS:
        raise ReviewRefused("That can't be changed here.")
    value = (value or "").strip()
    if field == "requested_delivery_date":
        try:
            new = date.fromisoformat(value) if value else None
        except ValueError as exc:
            raise ReviewRefused("Use the date picker for the delivery date.") from exc
    else:
        new = value or None
    before = getattr(order, field)
    if before == new:
        return
    setattr(order, field, new)
    order.flags = [f for f in order.flags or [] if f.get("code") not in HEADER_FLAGS[field]]
    _corrected(session, order, field, before, new)
    _rescore(session, client, order)


# ---------------------------------------------------------------- lines


def unit_choices(item: CatalogueItem) -> list[tuple[str, str]]:
    """(value, label) for the unit dropdown: the sell unit, and the inner unit when it converts."""
    choices = [("sell", units.plural(item.sell_unit))]
    if item.inner_unit and item.pack_size and item.pack_size > 1:
        choices.append(("inner", f"{units.plural(item.inner_unit)} ({item.pack_size} per "
                                 f"{item.sell_unit.lower()})"))
    return choices


def save_line(session: Session, client: Client, order: Order, line_id: int, product_code: str, qty_text: str,
              unit_choice: str = "sell", remember: bool = True) -> OrderLine:
    """The operator picks or confirms the product and quantity for a line. Code converts the units."""
    _require_editable(order)
    line = get_line(session, order, line_id)
    item = session.exec(scoped(CatalogueItem, client.id).where(CatalogueItem.product_code == product_code)).first()
    if item is None:
        raise ReviewRefused("Choose a product from the catalogue.")
    unit_word = item.inner_unit if unit_choice == "inner" and item.inner_unit else item.sell_unit
    conversion = units.convert((qty_text or "").strip(), unit_word, products.Item(
        item.product_code, item.description, item.sell_unit, item.pack_size, item.inner_unit, item.barcode),
        client.unit_synonyms or {}, convert_units=True)
    if conversion.qty is None:
        raise ReviewRefused(conversion.flags[0]["message"] if conversion.flags else "Enter a quantity.")

    before = (line.product_code, str(line.qty) if line.qty is not None else None, line.unit)
    line.product_code = item.product_code
    line.match_method = MatchMethod.OPERATOR
    line.corrected_by_operator = True
    line.qty, line.unit, line.conversion_note = conversion.qty, conversion.unit, conversion.note
    line.flags = []
    line.remember = bool(remember)
    session.add(line)
    _corrected(session, order, f"line {line.line_no}", before, (line.product_code, str(line.qty), line.unit))
    _rescore(session, client, order)
    return line


def add_line(session: Session, client: Client, order: Order) -> OrderLine:
    _require_editable(order)
    existing = lines_of(session, order)
    line = OrderLine(client_id=client.id, order_id=order.id, line_no=max((ln.line_no for ln in existing), default=0) + 1,
                     raw_text="Added by you", status=Status.PROBLEM, corrected_by_operator=True,
                     flags=[{"code": "no_product", "message": "Choose the product", "severity": "problem"}])
    if order.classification != Classification.NEW_ORDER:
        order.classification = Classification.NEW_ORDER  # e.g. a scanned order typed in by hand
    order.flags = [f for f in order.flags or [] if f.get("code") not in READING_PROBLEMS]
    session.add(line)
    session.flush()
    _corrected(session, order, "line added", None, line.line_no)
    _rescore(session, client, order)
    return line


def delete_line(session: Session, client: Client, order: Order, line_id: int) -> None:
    _require_editable(order)
    line = get_line(session, order, line_id)
    _corrected(session, order, f"line {line.line_no} deleted", line.product_code, None)
    session.delete(line)
    session.flush()
    _rescore(session, client, order)


# ---------------------------------------------------------------- finishing


def approve_problems(session: Session, order: Order) -> list[str]:
    """Why the order can't be approved yet, in plain words. Empty means it can."""
    if order.status in (Status.APPROVED, Status.EXPORTED):
        return ["This order is already approved."]
    if order.processing_state != "done":
        return ["This order is still being read."]
    if order.status == Status.PASS_ON:
        return ["This is not an order."]
    reasons = []
    if order.customer_id is None:
        reasons.append("Choose the customer.")
    lines = lines_of(session, order)
    if not lines:
        reasons.append("Add at least one line.")
    bad = [ln.line_no for ln in lines if ln.status == Status.PROBLEM]
    if bad:
        reasons.append(f"Fix line{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} first.")
    return reasons


def _save_aliases(session: Session, client: Client, order: Order, lines: list[OrderLine]) -> int:
    """A remembered phrase for every line the operator picked or changed with "Remember this" ticked."""
    if order.customer_id is None:
        return 0
    synonyms = client.unit_synonyms or {}
    unit_words = {w for words in synonyms.values() for w in words} | set(synonyms)
    items = {i.product_code: i for i in session.exec(scoped(CatalogueItem, client.id)).all()}
    saved = 0
    now = datetime.now(timezone.utc)
    for line in lines:
        if not (line.remember and line.match_method == MatchMethod.OPERATOR and line.product_code
                and line.source_span):
            continue
        price = f"{line.requested_price}" if line.requested_price is not None else None
        phrase = products.phrase_for(products.LineIn(line.line_no, line.source_span, line.requested_qty,
                                                     line.requested_unit, price), unit_words)
        if not phrase:
            continue
        item = items.get(line.product_code)
        unit_hint = (item.inner_unit if line.conversion_note and item else line.unit) if not line.requested_unit \
            else None
        alias = session.exec(scoped(Alias, client.id).where(Alias.customer_id == order.customer_id,
                                                            Alias.phrase == phrase)).first()
        if alias is None:
            alias = Alias(client_id=client.id, customer_id=order.customer_id, phrase=phrase, created_at=now,
                          product_code=line.product_code)
        alias.product_code, alias.unit_hint, alias.created_from_order_id = line.product_code, unit_hint, order.id
        session.add(alias)
        saved += 1
        _event(session, order, "alias_saved", phrase=phrase, product_code=line.product_code, unit_hint=unit_hint)
    return saved


def approve(session: Session, client: Client, order: Order, seconds: int | None = None) -> int:
    """Approve the order for the next export. Returns how many phrases were remembered."""
    _rescore(session, client, order)
    reasons = approve_problems(session, order)
    if reasons:
        raise ReviewRefused(" ".join(reasons))
    lines = lines_of(session, order)
    now = datetime.now(timezone.utc)
    if seconds is None and order.review_started_at is not None:
        seconds = int((now - order.review_started_at).total_seconds())
    seconds = max(0, min(int(seconds or 0), MAX_REVIEW_SECONDS))
    order.review_seconds = (order.review_seconds or 0) + seconds
    order.status = Status.APPROVED
    order.approved_at = now
    saved = _save_aliases(session, client, order, lines)
    session.add(order)
    _event(session, order, "approval", review_seconds=order.review_seconds, corrections=order.corrections_count,
           aliases_saved=saved)
    session.commit()
    return saved


def undo_approval(session: Session, client: Client, order: Order) -> None:
    if order.status != Status.APPROVED:
        raise ReviewRefused("Only an approved order that has not been exported can be reopened.")
    order.status = None
    order.approved_at = None
    _event(session, order, "approval_undone")
    _rescore(session, client, order)


def pass_on(session: Session, client: Client, order: Order) -> None:
    """Not an order: it goes to the client contact instead of the export."""
    _require_editable(order)
    order.status = Status.PASS_ON
    _corrected(session, order, "status", "review", "pass_on")
    _event(session, order, "pass_on")
    session.add(order)
    session.commit()


def back_to_review(session: Session, client: Client, order: Order) -> None:
    """Undo a Pass on made by the operator, or treat a message as an order after all."""
    if order.status != Status.PASS_ON:
        raise ReviewRefused("This order is not passed on.")
    order.status = None
    if order.classification != Classification.NEW_ORDER:
        order.classification = Classification.NEW_ORDER  # its lines are then added by hand
        order.flags = [f for f in order.flags or [] if f.get("code") not in READING_PROBLEMS]
    _corrected(session, order, "status", "pass_on", "review")
    _event(session, order, "back_to_review")
    _rescore(session, client, order)


def next_order(session: Session, client: Client, after: Order | None = None) -> Order | None:
    """The next order needing review, in queue order (oldest first), after the given one, wrapping round."""
    waiting = session.exec(scoped(Order, client.id).where(Order.status.in_(WAITING), Order.processing_state == "done")
                           .order_by(Order.received_at, Order.id)).all()
    if after is None:
        return waiting[0] if waiting else None
    later = [o for o in waiting if (o.received_at, o.id) > (after.received_at, after.id)]
    earlier = [o for o in waiting if o.id != after.id and o not in later]
    return (later or earlier or [None])[0]


def queue_position(session: Session, client: Client, order: Order) -> tuple[int, int] | None:
    ids = session.exec(select(Order.id).where(Order.client_id == client.id, Order.status.in_(WAITING),
                                              Order.processing_state == "done")
                       .order_by(Order.received_at, Order.id)).all()
    return (ids.index(order.id) + 1, len(ids)) if order.id in ids else None


# ---------------------------------------------------------------- asking the client


@dataclass
class Question:
    to: str
    subject: str
    body: str


def ask_client(session: Session, client: Client, order: Order) -> Question:
    """A ready-written email to the client's contact about what needs their answer."""
    customer = session.get(Customer, order.customer_id) if order.customer_id else None
    who = (customer.name if customer and customer.client_id == client.id else None) \
        or order.customer_name_raw or order.sender_email or "an unknown sender"
    received = order.received_at.astimezone(LONDON).strftime("%d/%m/%Y %H:%M")
    ref = f"PO {order.po_number}" if order.po_number else "an order with no PO number"
    issues = []
    if order.status == Status.PASS_ON:
        kind = CLASSIFICATION_LABELS.get(Classification(order.classification), "message").lower() \
            if order.classification else "message"
        issues.append(f"This looks like a {kind}, not a new order" + (f": {order.classification_reason}"
                                                                     if order.classification_reason else "."))
    for flag in order.flags or []:
        if flag.get("severity") in (Status.PROBLEM, Status.CHECK) and flag.get("code") not in ("line_problem",
                                                                                               "line_check"):
            issues.append(flag["message"])
    for line in lines_of(session, order):
        for flag in line.flags or []:
            if flag.get("severity") in (Status.PROBLEM, Status.CHECK):
                issues.append(f"Line {line.line_no} ('{line.source_span or line.raw_text}'): {flag['message']}")
    if not issues:
        issues.append("Please confirm this order is right before we enter it.")
    body = "\n".join([
        f"Hi {client.contact_name or 'there'},",
        "",
        f"We have {ref} from {who}, received {received}. Before it goes in, could you help with this?",
        "",
        *[f"- {issue}" for issue in issues],
        "",
        "Thanks,",
    ])
    return Question(to=client.contact_email or "", subject=f"Question about {ref} from {who}", body=body)

