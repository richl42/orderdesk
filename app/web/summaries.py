"""Numbers shown on the Home screen and in client headers.

Read-only queries. Every per-client query filters by client_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from app.db import scoped
from app.models import CatalogueItem, Client, Comparison, Customer, Order, OutputTemplate
from app.web import format as fmt
from pipeline.vocab import CLASSIFICATION_LABELS, SETUP_STEPS, Classification, ClientMode, ComparisonResult, Status
from pipeline.workdays import LONDON, trial_day

WAITING = [Status.READY, Status.CHECK, Status.PROBLEM]
ATTENTION = [Status.CHECK, Status.PROBLEM, Status.PASS_ON]
REQUIRED_SETUP = ["details", "catalogue", "customers", "output"]

MODE_LABELS = {
    "demo": "Demo",
    "trial": "Shadow trial",
    "live": "Live",
    "setting_up": "Setting up",
}


def london_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """(start, end) of today in London, in UTC. End matters: test orders can be dated later in the week."""
    local = now.astimezone(LONDON).date()
    start = datetime(local.year, local.month, local.day, tzinfo=LONDON)
    end = datetime.combine(local + timedelta(days=1), datetime.min.time(), tzinfo=LONDON)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def count(session: Session, statement) -> int:
    return session.exec(statement).one()


def setup_complete(client: Client) -> bool:
    return all(step in client.setup_done for step in REQUIRED_SETUP)


def mode_key(client: Client) -> str:
    return client.mode if setup_complete(client) else "setting_up"


@dataclass
class ClientSummary:
    client: Client
    mode_key: str
    mode_label: str
    products: int
    customers: int
    orders_today: int
    to_review: int
    exactly_right: str
    meta: str
    facts: str
    foot: str
    cta_label: str
    cta_href: str
    trial_day: int | None = None
    output_file: str = ""


def exactly_right_since(session: Session, client_ids: list[str], since: datetime) -> tuple[int, int]:
    """(exactly right, compared) over paired orders received since a moment."""
    if not client_ids:
        return 0, 0
    rows = session.exec(
        select(Comparison.result)
        .join(Order, Comparison.order_id == Order.id)
        .where(
            Comparison.client_id.in_(client_ids),
            Order.client_id == Comparison.client_id,
            Comparison.result.in_([ComparisonResult.EXACT, ComparisonResult.DIFFERENT]),
            Order.received_at >= since,
        )
    ).all()
    return sum(1 for r in rows if r == ComparisonResult.EXACT), len(rows)


def summarise_client(session: Session, client: Client, now: datetime) -> ClientSummary:
    cid = client.id
    products = count(session, select(func.count()).select_from(CatalogueItem).where(CatalogueItem.client_id == cid))
    customers = count(session, select(func.count()).select_from(Customer).where(Customer.client_id == cid))
    today_start, today_end = london_day_bounds(now)
    orders_today = count(session, select(func.count()).select_from(Order).where(
        Order.client_id == cid, Order.received_at >= today_start, Order.received_at < today_end))
    to_review = count(session, select(func.count()).select_from(Order).where(
        Order.client_id == cid, Order.status.in_(WAITING)))
    right, compared = exactly_right_since(session, [cid], now - timedelta(days=7))
    template = session.exec(scoped(OutputTemplate, cid)).first()

    key = mode_key(client)
    steps_done = sum(1 for s in SETUP_STEPS if s in client.setup_done)
    day = None

    if client.mode == ClientMode.DEMO:
        meta = "Fictional data · used when pitching"
    else:
        meta = " · ".join(p for p in [client.erp_system, f"Contact: {client.contact_name}" if client.contact_name else ""] if p)

    if key == "setting_up":
        missing = [s for s in REQUIRED_SETUP if s not in client.setup_done]
        names = {"details": "client details", "catalogue": "product catalogue",
                 "customers": "customer list", "output": "output format"}
        foot = f"Setup: {steps_done} of {len(SETUP_STEPS)} steps done ({', '.join(names[m] for m in missing)} missing)"
        cta = ("Continue setup", f"/clients/{cid}/setup")
    elif client.mode == ClientMode.TRIAL and client.trial_start_date:
        day = trial_day(client.trial_start_date, client.trial_working_days, now.astimezone(LONDON).date())
        if day == 0:
            foot = f"Trial starts {fmt.long_date(client.trial_start_date)}"
        elif day > client.trial_working_days:
            foot = f"Trial finished after {client.trial_working_days} working days"
        else:
            foot = f"Day {day} of {client.trial_working_days} working days"
        cta = ("Open orders", f"/clients/{cid}/orders")
    elif client.mode == ClientMode.DEMO:
        foot = f"Setup: {steps_done} of {len(SETUP_STEPS)} steps done"
        cta = ("Open demo", f"/clients/{cid}/orders")
    else:
        foot = "Approved orders are downloaded as files in their format"
        cta = ("Open orders", f"/clients/{cid}/orders")

    return ClientSummary(
        client=client,
        mode_key=key,
        mode_label=MODE_LABELS[key],
        products=products,
        customers=customers,
        orders_today=orders_today,
        to_review=to_review,
        exactly_right=fmt.percent(right, compared),
        meta=meta,
        facts=f"{products} products · {customers} customers",
        foot=foot,
        cta_label=cta[0],
        cta_href=cta[1],
        trial_day=day,
        output_file=template.source_filename if template else "",
    )


@dataclass
class Tile:
    label: str
    value: str
    note: str


@dataclass
class AttentionItem:
    order: Order
    client_name: str
    customer_name: str
    issue: str
    title: str


@dataclass
class HomeData:
    tiles: list[Tile]
    attention: list[AttentionItem]
    clients: list[ClientSummary] = field(default_factory=list)


def order_issue(order: Order) -> str:
    """The most important thing wrong with an order, in plain words."""
    if order.status == Status.PASS_ON:
        label = CLASSIFICATION_LABELS.get(Classification(order.classification), "Not an order") \
            if order.classification else "Not an order"
        reason = f": {order.classification_reason.rstrip('. ')}" if order.classification_reason else ""
        return f"{label}{reason}. Forward to the client contact"
    for severity in (Status.PROBLEM, Status.CHECK):
        for flag in order.flags or []:
            if flag.get("severity") == severity:
                return flag.get("message", "")
    return "Needs a look"


def home_data(session: Session, now: datetime | None = None) -> HomeData:
    now = now or datetime.now(timezone.utc)
    clients = session.exec(select(Client).where(Client.archived_at.is_(None))).all()
    clients = sorted(clients, key=lambda c: (c.mode == ClientMode.DEMO, c.name.lower()))
    summaries = [summarise_client(session, c, now) for c in clients]

    # Totals leave out demo clients.
    real_ids = [c.id for c in clients if c.mode != ClientMode.DEMO]
    active = [c for c in clients if c.mode != ClientMode.DEMO and setup_complete(c)]

    today_start, today_end = london_day_bounds(now)
    orders_today = count(session, select(func.count()).select_from(Order).where(
        Order.client_id.in_(real_ids), Order.received_at >= today_start, Order.received_at < today_end)) \
        if real_ids else 0

    waiting_q = select(Order).where(Order.client_id.in_(real_ids), Order.status.in_(WAITING))
    waiting = session.exec(waiting_q.order_by(Order.received_at)).all() if real_ids else []
    oldest_note = f"Oldest arrived {fmt.ago(waiting[0].received_at, now)}" if waiting else "Nothing waiting"

    right, compared = exactly_right_since(session, real_ids, now - timedelta(days=7))

    week_ago = now - timedelta(days=7)
    review_times = session.exec(select(Order.review_seconds).where(
        Order.client_id.in_(real_ids), Order.approved_at >= week_ago, Order.review_seconds.is_not(None)
    )).all() if real_ids else []
    avg_review = sum(review_times) / len(review_times) if review_times else None

    tiles = [
        Tile("Orders in today", str(orders_today),
             f"Across {len(active)} active client{'s' if len(active) != 1 else ''}"),
        Tile("Waiting for your review", str(len(waiting)), oldest_note),
        Tile("Exactly right, last 7 days", fmt.percent(right, compared),
             "Checked against what staff entered" if compared else "No staff entries compared yet"),
        Tile("Your review time per order", fmt.duration(avg_review) if avg_review is not None else "n/a",
             "Average, last 7 days" if avg_review is not None else "No orders approved yet"),
    ]

    names = {c.id: c.name for c in clients}
    attention_orders = session.exec(
        select(Order).where(Order.client_id.in_(list(names)), Order.status.in_(ATTENTION))
        .order_by(Order.received_at).limit(100)
    ).all() if names else []
    customer_ids = {o.customer_id for o in attention_orders if o.customer_id}
    customer_names = {c.id: c.name for c in session.exec(select(Customer).where(Customer.id.in_(customer_ids))).all()} \
        if customer_ids else {}

    attention = []
    for order in attention_orders:
        who = customer_names.get(order.customer_id) or order.sender_email or "Unknown sender"
        title = f"PO {order.po_number} from {who}" if order.po_number else f"Order from {who}"
        attention.append(AttentionItem(order=order, client_name=names[order.client_id],
                                       customer_name=who, issue=order_issue(order), title=title))

    return HomeData(tiles=tiles, attention=attention, clients=summaries)
