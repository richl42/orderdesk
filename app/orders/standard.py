"""Orders in the standard internal shape (BRIEF.md), and a client's output template as export.py reads it."""

from __future__ import annotations

from sqlmodel import Session

from app.db import scoped
from app.models import Customer, Order, OrderLine, OutputTemplate


def standard_order(session: Session, order: Order, lines: list[OrderLine] | None = None) -> dict:
    customer = session.get(Customer, order.customer_id) if order.customer_id else None
    if customer is not None and customer.client_id != order.client_id:
        customer = None
    if lines is None:
        lines = session.exec(scoped(OrderLine, order.client_id).where(OrderLine.order_id == order.id)
                             .order_by(OrderLine.line_no)).all()
    return {
        "client_id": order.client_id,
        "order_id": order.id,
        "classification": order.classification,
        "customer": {"account_code": customer.account_code, "name": customer.name,
                     "match_method": order.customer_match_method} if customer else None,
        "po_number": order.po_number,
        "received_at": order.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_delivery_date": order.requested_delivery_date.isoformat() if order.requested_delivery_date else None,
        "requested_delivery_raw": order.requested_delivery_raw,
        "delivery_address": order.delivery_address,
        "delivery_notes": order.delivery_notes,
        "lines": [{
            "line_no": ln.line_no,
            "source_span": ln.source_span,
            "product_code": ln.product_code,
            "qty": str(ln.qty) if ln.qty is not None else None,
            "unit": ln.unit,
            "conversion_note": ln.conversion_note,
            "unit_price": f"{ln.unit_price:.2f}" if ln.unit_price is not None else None,
            "match_method": ln.match_method,
            "status": ln.status,
            "flags": ln.flags,
        } for ln in lines],
        "status": order.status,
        "initial_status": order.initial_status,
        "flags": order.flags,
    }


def template_dict(template: OutputTemplate | None) -> dict | None:
    if template is None:
        return None
    return {
        "columns": list(template.columns), "fill": dict(template.fill), "date_format": template.date_format,
        "decimal_places": template.decimal_places, "delimiter": template.delimiter, "encoding": template.encoding,
        "include_header": template.include_header,
    }


def output_template(session: Session, client_id: str) -> dict | None:
    return template_dict(session.exec(scoped(OutputTemplate, client_id)).first())
