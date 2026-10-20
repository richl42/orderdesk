"""Render standard internal orders in a client's own import format.

The only code that knows about a client's format. Works from the standard
order (a dict in the shape described in BRIEF.md) plus the output template.
Anything missing is left blank, never guessed.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pipeline.workdays import london_date

DELIMITER_NAMES = {",": "Comma", ";": "Semicolon", "\t": "Tab", "|": "Pipe"}
ENCODING_NAMES = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 with BOM (Excel)", "cp1252": "Windows (ANSI)"}
DATE_FORMAT_NAMES = {
    "%d/%m/%Y": "19/10/2026",
    "%d/%m/%y": "19/10/26",
    "%Y-%m-%d": "2026-10-19",
    "%d-%m-%Y": "19-10-2026",
    "%d.%m.%Y": "19.10.2026",
    "%m/%d/%Y": "10/19/2026 (month first)",
    "%Y%m%d": "20261019",
}
LINE_END = "\r\n"


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return london_date(value)
    if isinstance(value, date):
        return value
    text = str(value)
    if "T" in text or " " in text:
        return london_date(datetime.fromisoformat(text.replace("Z", "+00:00")))
    return date.fromisoformat(text)


def format_quantity(value: Any) -> str:
    if value in (None, ""):
        return ""
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def format_money(value: Any, places: int) -> str:
    if value in (None, ""):
        return ""
    step = Decimal(1).scaleb(-places)
    return str(Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP))


def field_value(order: dict, line: dict, fld: str, template: dict) -> str:
    customer = order.get("customer") or {}
    date_format = template.get("date_format") or "%d/%m/%Y"
    if fld == "account_code":
        return customer.get("account_code") or ""
    if fld == "po_number":
        return order.get("po_number") or ""
    if fld == "date_received":
        day = _as_date(order.get("received_at"))
        return day.strftime(date_format) if day else ""
    if fld == "product_code":
        return line.get("product_code") or ""
    if fld == "quantity":
        return format_quantity(line.get("qty"))
    if fld == "unit":
        return line.get("unit") or ""
    if fld == "price":
        return format_money(line.get("unit_price"), int(template.get("decimal_places", 2)))
    if fld == "delivery_date":
        day = _as_date(order.get("requested_delivery_date"))
        return day.strftime(date_format) if day else ""
    if fld == "delivery_notes":
        return order.get("delivery_notes") or ""
    raise ValueError(f"Unknown output field: {fld}")


def cell_value(order: dict, line: dict, spec: dict, template: dict) -> str:
    if "fixed" in spec:
        return str(spec["fixed"])
    if "field" in spec:
        return field_value(order, line, spec["field"], template)
    return ""


def render_rows(order: dict, template: dict) -> list[list[str]]:
    """One row per order line, header fields repeated on every row, in the template's column order."""
    fill = template.get("fill") or {}
    columns = template.get("columns") or []
    return [
        [cell_value(order, line, fill.get(col, {"blank": True}), template) for col in columns]
        for line in order.get("lines") or []
    ]


def to_text(rows: list[list[str]], template: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=template.get("delimiter") or ",", lineterminator=LINE_END)
    if template.get("include_header", True):
        writer.writerow(template.get("columns") or [])
    writer.writerows(rows)
    return buffer.getvalue()


def render_orders_text(orders: list[dict], template: dict) -> str:
    rows: list[list[str]] = []
    for order in orders:
        rows.extend(render_rows(order, template))
    return to_text(rows, template)


def to_bytes(text: str, template: dict) -> bytes:
    return text.encode(template.get("encoding") or "utf-8", errors="replace")
