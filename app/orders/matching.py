"""Pipeline steps 3 to 9 for orders that have been read: match, convert, check, set status, log.

A client's orders are matched one at a time, in the order they arrived, and only
once every earlier order has been read, so the duplicate check always sees the
orders that came before. Every query filters by client_id.
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session, or_, select

from app.ai_client import get_ai
from app.config import get_settings
from app.db import get_engine, scoped
from app.models import Alias, CatalogueItem, Client, Customer, Event, Order, OrderLine, PriceListEntry
from pipeline import checks, customers, products, status, units
from pipeline.ai import AIUnavailable, cost_gbp
from pipeline.normalise import description_key
from pipeline.vocab import DEFAULT_RULES, Classification, MatchMethod, Status

READING, READ, DONE, FAILED = "reading", "read", "done", "failed"
# Order-level flags this step writes; they are replaced each time an order is matched.
SUMMARY_CODES = {"line_problem", "line_check"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _client_lock(client_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(client_id, threading.Lock())


def _event(session: Session, order: Order, type_: str, **details) -> None:
    session.add(Event(client_id=order.client_id, order_id=order.id, type=type_, details=details))


def match_waiting(client_id: str) -> None:
    """Match every read order of this client whose earlier orders have all been read, oldest first."""
    with _client_lock(client_id):
        while True:
            with Session(get_engine()) as session:
                order = session.exec(
                    select(Order).where(Order.client_id == client_id, Order.processing_state.in_([READING, READ]))
                    .order_by(Order.received_at, Order.id)
                ).first()
                if order is None or order.processing_state == READING:
                    return
                order_id = order.id
                try:
                    match_order(session, order)
                except Exception as exc:  # never leave an order stuck waiting
                    session.rollback()
                    order = session.get(Order, order_id)
                    order.flags = [*order.flags, status_flag("matching_failed", "Something went wrong matching this "
                                                             "order. Try again", Status.PROBLEM)]
                    order.processing_state, order.status = FAILED, Status.PROBLEM
                    session.add(order)
                    _event(session, order, "error", error=type(exc).__name__, trace=traceback.format_exc(limit=8))
                    session.commit()


def status_flag(code: str, message: str, severity: str) -> dict:
    return {"code": code, "message": message, "severity": severity}


# ---------------------------------------------------------------- loading what matching needs


def _customers(session: Session, client_id: str) -> list[customers.CustomerRecord]:
    return [customers.CustomerRecord(c.id, c.account_code, c.name, list(c.emails), list(c.email_domains),
                                     c.price_band)
            for c in session.exec(scoped(Customer, client_id)).all()]


def _catalogue(session: Session, client_id: str) -> tuple[products.Catalogue, dict[str, CatalogueItem]]:
    rows = session.exec(scoped(CatalogueItem, client_id)).all()
    items = [products.Item(r.product_code, r.description, r.sell_unit, r.pack_size, r.inner_unit, r.barcode)
             for r in rows]
    return products.Catalogue(items), {r.product_code: r for r in rows}


def _aliases(session: Session, client_id: str) -> list[products.AliasRecord]:
    return [products.AliasRecord(a.id, a.customer_id, a.phrase, a.product_code, a.unit_hint)
            for a in session.exec(scoped(Alias, client_id)).all()]


def _band_prices(session: Session, client_id: str, band: str | None) -> dict[str, Decimal]:
    if not band:
        return {}
    rows = session.exec(scoped(PriceListEntry, client_id).where(PriceListEntry.price_band == band)).all()
    return {r.product_code: r.price for r in rows if r.price is not None}


def _earlier(session: Session, order: Order) -> list[checks.EarlierOrder]:
    """New orders of the same client that arrived before this one."""
    rows = session.exec(scoped(Order, order.client_id).where(
        Order.id != order.id,
        or_(Order.classification.is_(None), Order.classification == Classification.NEW_ORDER),
        or_(Order.received_at < order.received_at, (Order.received_at == order.received_at) & (Order.id < order.id)),
    )).all()
    return [checks.EarlierOrder(o.id, o.received_at, o.customer_id, o.po_number, o.content_hash) for o in rows]


def _largest_before(session: Session, order: Order) -> dict[str, Decimal]:
    """This customer's largest quantity of each product on earlier approved or exported orders."""
    if order.customer_id is None:
        return {}
    rows = session.exec(
        select(OrderLine.product_code, OrderLine.qty).join(Order, Order.id == OrderLine.order_id)
        .where(OrderLine.client_id == order.client_id, Order.client_id == order.client_id,
               Order.customer_id == order.customer_id, Order.id != order.id, Order.received_at < order.received_at,
               Order.status.in_([Status.APPROVED, Status.EXPORTED]),
               OrderLine.product_code.is_not(None), OrderLine.qty.is_not(None))
    ).all()
    largest: dict[str, Decimal] = {}
    for code, qty in rows:  # quantities are stored as decimal text, so compare them here, not in SQL
        if qty > largest.get(code, Decimal(0)):
            largest[code] = qty
    return largest


# ---------------------------------------------------------------- matching one order


def match_order(session: Session, order: Order) -> None:
    started = time.monotonic()
    client = session.get(Client, order.client_id)
    rules = {**DEFAULT_RULES, **(client.rules or {})}

    who = customers.match_customer(order.sender_email, order.customer_name_raw, _customers(session, client.id))
    order.customer_id, order.customer_match_method = who.customer_id, who.method
    customer = session.get(Customer, who.customer_id) if who.customer_id else None
    if customer is not None and customer.client_id != client.id:
        customer = None
    base_flags = [f for f in order.flags or [] if f.get("code") not in SUMMARY_CODES]
    order_flags = [*base_flags, *who.flags]

    lines = session.exec(scoped(OrderLine, client.id).where(OrderLine.order_id == order.id)
                         .order_by(OrderLine.line_no)).all()
    calls = []
    line_results: list[tuple[int, str, list[dict]]] = []

    if order.classification == Classification.NEW_ORDER and lines:
        catalogue, rows = _catalogue(session, client.id)
        aliases = _aliases(session, client.id)
        synonyms = client.unit_synonyms or {}
        unit_words = {w for words in synonyms.values() for w in words} | set(synonyms)
        band = _band_prices(session, client.id, customer.price_band if customer else None)

        inputs: dict[int, products.LineIn] = {}
        matches: dict[int, products.LineMatch] = {}
        for line in lines:
            if status.has_severity(line.flags, Status.PROBLEM) or not line.source_span:
                continue  # its text could not be checked, so it is not matched
            price = f"{line.requested_price}" if line.requested_price is not None else None
            inputs[line.line_no] = products.LineIn(line.line_no, line.source_span, line.requested_qty,
                                                   line.requested_unit, price)
            matches[line.line_no] = products.match_line(inputs[line.line_no], who.customer_id, catalogue, aliases,
                                                        unit_words)
        try:
            calls = products.choose_with_ai(get_ai(), customer.name if customer else order.customer_name_raw,
                                            list(inputs.values()), matches, catalogue)
        except AIUnavailable as exc:
            order.flags = [*base_flags, status_flag("ai_unavailable", str(exc), Status.PROBLEM)]
            order.processing_state, order.status = FAILED, Status.PROBLEM
            session.add(order)
            _event(session, order, "ai_unavailable", message=str(exc))
            session.commit()
            return

        check_lines = []
        used_aliases = set()
        for line in lines:
            match = matches.get(line.line_no)
            if match is None:
                line.status = Status.PROBLEM
                line_results.append((line.line_no, line.status, line.flags))
                check_lines.append(checks.CheckLine(line.line_no, None, None, None, line.requested_price))
                continue
            flags = [*line.flags, *match.flags]
            key = description_key(products.phrase_for(inputs[line.line_no], unit_words))
            line.candidates = (match.candidates or catalogue.candidates(key))[:products.CANDIDATES_KEPT]
            line.product_code, line.match_method = match.product_code, match.method
            line.qty = line.unit = line.conversion_note = line.unit_price = None
            if match.method == MatchMethod.AI_SUGGESTED:
                why = f": {match.reason}" if match.reason else ""
                if match.ambiguous:
                    flags.append(status_flag("ai_ambiguous", f"Could be more than one product{why}", Status.CHECK))
                else:
                    flags.append(status_flag("ai_suggested", f"Suggested match, please confirm{why}", Status.CHECK))
            item = catalogue.items.get(match.product_code) if match.product_code else None
            converted = False
            if line.requested_qty and not units.quantity_plausible(line.source_span, line.requested_qty, unit_words):
                flags.append(status_flag("qty_check", f"Check the quantity: {line.requested_qty} was read from "
                                         f"'{line.source_span}'", Status.CHECK))
            if item is not None:
                conversion = units.convert(line.requested_qty, line.requested_unit, item, synonyms,
                                           match.unit_hint, bool(rules.get("convert_units", True)))
                line.qty, line.unit, line.conversion_note = conversion.qty, conversion.unit, conversion.note
                converted = conversion.note is not None
                flags += conversion.flags
                row = rows[item.code]
                line.unit_price = band.get(item.code, row.price)
                if match.alias_id:
                    used_aliases.add(match.alias_id)
            line.flags = flags
            check_lines.append(checks.CheckLine(line.line_no, line.product_code, line.qty, line.unit_price,
                                                line.requested_price, item.pack_size if item else None, converted))

        facts = checks.OrderFacts(order.received_at, order.customer_id, order.po_number, order.content_hash,
                                  order.requested_delivery_date, order.requested_delivery_raw, check_lines)
        found_order_flags, found_line_flags = checks.run_checks(facts, rules, _earlier(session, order),
                                                                _largest_before(session, order))
        order_flags += found_order_flags
        allow_ai_ready = bool(rules.get("allow_ai_ready", False))
        for line in lines:
            match = matches.get(line.line_no)
            if match is None:
                continue
            line.flags = [*line.flags, *found_line_flags.get(line.line_no, [])]
            line.status = status.line_status(line.product_code, line.match_method, line.flags, allow_ai_ready,
                                             match.score, match.ambiguous)
            line_results.append((line.line_no, line.status, line.flags))
        for line in lines:
            session.add(line)

        now = datetime.now(timezone.utc)
        for alias in session.exec(scoped(Alias, client.id).where(Alias.id.in_(used_aliases))).all() \
                if used_aliases else []:
            alias.times_used += 1
            alias.last_used_at = now
            session.add(alias)

    line_results.sort()
    order_flags = status.line_summary_flags(line_results) + order_flags
    result = status.order_status(order.classification, order.classification is None,
                                 status.customer_status(who.customer_id, who.method),
                                 [s for _, s, _ in line_results], order_flags)
    order.flags = order_flags
    order.status = result
    if order.initial_status is None:
        order.initial_status = result  # set once by the pipeline, never changed

    settings = get_settings()
    cost = order.ai_cost_gbp
    for call in calls:
        call_cost = cost_gbp(call, settings.ai_price_input_per_mtok, settings.ai_price_output_per_mtok)
        cost = None if cost is None or call_cost is None else cost + call_cost
        _event(session, order, "ai_call", purpose="choose_products", model=call.model,
               input_tokens=call.input_tokens, cache_write_tokens=call.cache_write_tokens,
               cache_read_tokens=call.cache_read_tokens, output_tokens=call.output_tokens,
               cost_gbp=str(call_cost) if call_cost is not None else None, cached=call.cached,
               usable=call.input is not None, problem=call.problem)
        order.ai_input_tokens += call.total_input_tokens
        order.ai_output_tokens += call.output_tokens
    order.ai_cost_gbp = cost
    order.processing_ms = (order.processing_ms or 0) + int((time.monotonic() - started) * 1000)
    order.processing_state = DONE
    session.add(order)
    _event(session, order, "order_processed", status=result, customer_match=who.method,
           processing_ms=order.processing_ms, ai_input_tokens=order.ai_input_tokens,
           ai_output_tokens=order.ai_output_tokens,
           ai_cost_gbp=str(order.ai_cost_gbp) if order.ai_cost_gbp is not None else None)
    session.commit()
