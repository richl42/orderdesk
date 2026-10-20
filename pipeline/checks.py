"""Pipeline step 7: the checks. Each failed check adds a flag with a severity.

Pure functions over plain values; the caller supplies the client's rules and the
customer's earlier orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from pipeline.normalise import code_key
from pipeline.workdays import LONDON, london_date, next_working_day

OUTLIER_FACTOR = 5


def _flag(code: str, message: str, severity: str) -> dict:
    return {"code": code, "message": message, "severity": severity}


def money(value: Decimal) -> str:
    return f"£{value:,.2f}"


def day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b}"


@dataclass
class EarlierOrder:
    id: int
    received_at: datetime
    customer_id: int | None
    po_number: str | None
    content_hash: str | None


@dataclass
class CheckLine:
    line_no: int
    product_code: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    requested_price: Decimal | None
    pack_size: int | None = None
    converted: bool = False


@dataclass
class OrderFacts:
    received_at: datetime
    customer_id: int | None
    po_number: str | None
    content_hash: str | None
    delivery_date: date | None
    delivery_raw: str | None
    lines: list[CheckLine] = field(default_factory=list)


# ---------------------------------------------------------------- line checks


def price_flags(line: CheckLine, rules: dict) -> list[dict]:
    """A price written on the order that differs from the customer's price by more than the tolerance."""
    if line.product_code and line.unit_price is None:
        return [_flag("no_price", f"No price for {line.product_code} in the catalogue or price list", "check")]
    if not rules.get("flag_price_differences", True) or line.requested_price is None or line.unit_price is None:
        return []
    tolerance = Decimal(str(rules.get("price_tolerance") or "0.01"))
    written = line.requested_price
    if abs(written - line.unit_price) <= tolerance:
        return []
    if line.converted and line.pack_size and abs(written * line.pack_size - line.unit_price) <= tolerance:
        return []  # a price per bottle that matches the case price
    return [_flag("price_difference", f"They wrote {money(written)}; their price is {money(line.unit_price)}",
                  "check")]


def outlier_flags(line: CheckLine, largest_before: dict[str, Decimal]) -> list[dict]:
    """More than five times this customer's previous largest quantity of this product."""
    if not line.product_code or line.qty is None:
        return []
    largest = largest_before.get(line.product_code)
    if largest and line.qty > largest * OUTLIER_FACTOR:
        return [_flag("qty_outlier", f"{line.qty} is more than {OUTLIER_FACTOR} times their largest order of "
                                     f"{line.product_code} so far ({largest})", "check")]
    return []


# ---------------------------------------------------------------- order checks


def duplicate_flags(order: OrderFacts, earlier: list[EarlierOrder], window_days: int) -> list[dict]:
    """Same customer and PO number within the window, or exactly the same text, among orders that arrived before."""
    if not window_days:
        return []
    since = order.received_at - timedelta(days=window_days)
    recent = [e for e in earlier if since <= e.received_at <= order.received_at]
    po = code_key(order.po_number)
    for other in sorted(recent, key=lambda e: e.received_at):
        if po and order.customer_id is not None and other.customer_id == order.customer_id \
                and code_key(other.po_number) == po:
            when = day(london_date(other.received_at))
            return [_flag("duplicate", f"PO {order.po_number} already arrived on {when} (order {other.id})", "check")]
    for other in recent:
        if order.content_hash and other.content_hash == order.content_hash:
            when = day(london_date(other.received_at))
            return [_flag("duplicate", f"Exactly the same order already arrived on {when} (order {other.id})",
                          "check")]
    return []


def cutoff_flags(order: OrderFacts, rules: dict) -> list[dict]:
    """Received after the cutoff time and wanted by the next working day."""
    cutoff_text = rules.get("cutoff_time")
    if not cutoff_text or order.delivery_date is None:
        return []
    hours, minutes = (int(p) for p in str(cutoff_text).split(":")[:2])
    local = order.received_at.astimezone(LONDON)
    if local.time() <= time(hours, minutes):
        return []
    arrived = local.date()
    if arrived <= order.delivery_date <= next_working_day(arrived):
        return [_flag("cutoff", f"Arrived at {local:%H:%M}, after the {cutoff_text} cutoff, for delivery "
                                f"{day(order.delivery_date)}", "check")]
    return []


def order_value(order: OrderFacts) -> Decimal | None:
    """The order's value, or None until every line has a quantity and a price."""
    if not order.lines or any(ln.qty is None or ln.unit_price is None for ln in order.lines):
        return None
    return sum((ln.qty * ln.unit_price for ln in order.lines), Decimal(0))


def minimum_flags(order: OrderFacts, rules: dict) -> list[dict]:
    minimum = rules.get("min_order_value")
    if minimum in (None, ""):
        return []
    value = order_value(order)
    minimum = Decimal(str(minimum))
    if value is not None and value < minimum:
        return [_flag("minimum_order", f"Order value {money(value)} is below the {money(minimum)} minimum", "check")]
    return []


def po_flags(order: OrderFacts, rules: dict) -> list[dict]:
    if rules.get("require_po_number") and not order.po_number:
        return [_flag("po_missing", "No PO number, and this client needs one", "check")]
    return []


def delivery_flags(order: OrderFacts) -> list[dict]:
    arrived = london_date(order.received_at)
    if order.delivery_date is not None and order.delivery_date < arrived:
        return [_flag("delivery_past", f"Delivery date {day(order.delivery_date)} is before the order arrived",
                      "problem")]
    if order.delivery_date is None and order.delivery_raw:
        return [_flag("date_unclear", f"Delivery date '{order.delivery_raw}' not understood, so no date was set",
                      "check")]
    return []


def run_checks(order: OrderFacts, rules: dict, earlier: list[EarlierOrder],
               largest_before: dict[str, Decimal]) -> tuple[list[dict], dict[int, list[dict]]]:
    """(order flags, line flags by line number)."""
    line_flags = {ln.line_no: price_flags(ln, rules) + outlier_flags(ln, largest_before) for ln in order.lines}
    order_flags = (duplicate_flags(order, earlier, int(rules.get("duplicate_window_days") or 0))
                   + cutoff_flags(order, rules) + minimum_flags(order, rules) + po_flags(order, rules)
                   + delivery_flags(order))
    return order_flags, line_flags
