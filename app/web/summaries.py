"""Numbers shown on the Home screen and in client headers.

Read-only queries. Every per-client query filters by client_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlmodel import Session, func, select

from app.db import scoped
from app.models import CatalogueItem, Client, Comparison, Customer, Order, OutputTemplate
from app.web import format as fmt
from pipeline.vocab import CLASSIFICATION_LABELS, SETUP_STEPS, Classification, ClientMode, ComparisonResult, Status
from pipeline.workdays import LONDON, trial_day, working_days

WAITING = [Status.READY, Status.CHECK, Status.PROBLEM]
ATTENTION = [Status.CHECK, Status.PROBLEM, Status.PASS_ON]
REQUIRED_SETUP = ["details", "catalogue", "customers", "output"]
ATTENTION_FILTERS = [("all", "All"), (Status.CHECK, "Check"), (Status.PROBLEM, "Problem"), (Status.PASS_ON, "Pass on")]
DELETE_WITHIN_DAYS = 30  # trial clients that do not continue are deleted within 30 days of the trial ending

DEMO_ID = "demo"  # the demo client that Start the demo again rebuilds

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
class Averages:
    """Average AI cost and processing time per order, for the operator only."""
    orders: int
    cost: Decimal | None
    cost_orders: int
    tokens: int | None
    seconds: float | None

    @property
    def text(self) -> str:
        if not self.orders:
            return "No orders read yet"
        if self.cost is not None:
            ai = f"AI {fmt.pence(self.cost)}"
        else:
            ai = f"AI {self.tokens:,} tokens" if self.tokens is not None else "AI not used"
        read = f"read in {self.seconds:.1f}s" if self.seconds is not None else ""
        return " · ".join(p for p in [f"Per order: {ai}", read] if p)


def averages(session: Session, client_id: str) -> Averages:
    rows = session.exec(select(Order.ai_cost_gbp, Order.ai_input_tokens, Order.ai_output_tokens, Order.processing_ms)
                        .where(Order.client_id == client_id, Order.processing_state == "done")).all()
    costs = [c for c, *_ in rows if c is not None]
    tokens = [i + o for _, i, o, _ms in rows]
    times = [ms for *_, ms in rows if ms is not None]
    return Averages(
        orders=len(rows),
        cost=(sum(costs) / len(costs)) if costs else None,
        cost_orders=len(costs),
        tokens=round(sum(tokens) / len(tokens)) if tokens else None,
        seconds=(sum(times) / len(times) / 1000) if times else None,
    )


def trial_end(client: Client) -> date | None:
    if not client.trial_start_date or not client.trial_working_days:
        return None
    return working_days(client.trial_start_date, client.trial_working_days)[-1]


def delete_reminder(client: Client, today: date) -> tuple[str, str] | None:
    """(kind, text) for a trial client whose trial is over: their data must go within 30 days unless they continue."""
    if client.mode != ClientMode.TRIAL:
        return None
    end = trial_end(client)
    if end is None or today <= end:
        return None
    by = end + timedelta(days=DELETE_WITHIN_DAYS)
    if today > by:
        return "problem", (f"Trial ended {fmt.long_date(end)}: their data should have been deleted by "
                           f"{fmt.long_date(by)}. Delete it in Settings, or switch them to Live if they continued.")
    return "check", (f"Trial ended {fmt.long_date(end)}: delete their data by {fmt.long_date(by)} "
                     f"unless they continue.")


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
    averages: Averages | None = None
    reminder: tuple[str, str] | None = None


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
        averages=averages(session, cid),
        reminder=delete_reminder(client, now.astimezone(LONDON).date()),
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
    waiting: str = ""


@dataclass
class HomeData:
    tiles: list[Tile]
    attention: list[AttentionItem]
    clients: list[ClientSummary] = field(default_factory=list)
    attention_counts: dict[str, int] = field(default_factory=dict)
    attention_filter: str = "all"
    attention_more: int = 0
    filters: list[tuple[str, str]] = field(default_factory=lambda: list(ATTENTION_FILTERS))
    demo: Client | None = None


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
    if order.status == Status.READY:
        return "Everything matched and checked. Approve to add it to the export"
    return "Needs a look"


ATTENTION_LIMIT = 100


def waited(moment: datetime, now: datetime) -> str:
    """How long an order has been waiting, in plain words."""
    text = fmt.ago(moment, now)
    if moment > now:  # test orders can be dated later in the week
        return "Arrives later"
    return "Arrived just now" if text == "just now" else f"Waiting {text.removesuffix(' ago')}"


def home_data(session: Session, now: datetime | None = None, attention_filter: str = "all") -> HomeData:
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
    counts = {key: 0 for key, _ in ATTENTION_FILTERS}
    if names:
        for status, n in session.exec(select(Order.status, func.count()).where(
                Order.client_id.in_(list(names)), Order.status.in_(ATTENTION)).group_by(Order.status)).all():
            counts[status] = n
    counts["all"] = sum(counts[s] for s in ATTENTION)
    if attention_filter not in counts:
        attention_filter = "all"
    shown = ATTENTION if attention_filter == "all" else [attention_filter]
    attention_orders = session.exec(
        select(Order).where(Order.client_id.in_(list(names)), Order.status.in_(shown))
        .order_by(Order.received_at, Order.id).limit(ATTENTION_LIMIT)
    ).all() if names else []
    customer_ids = {o.customer_id for o in attention_orders if o.customer_id}
    customer_names = {c.id: c.name for c in session.exec(select(Customer).where(Customer.id.in_(customer_ids))).all()} \
        if customer_ids else {}

    attention = []
    for order in attention_orders:
        who = customer_names.get(order.customer_id) or order.sender_email or "Unknown sender"
        title = f"PO {order.po_number} from {who}" if order.po_number else f"Order from {who}"
        attention.append(AttentionItem(order=order, client_name=names[order.client_id],
                                       customer_name=who, issue=order_issue(order), title=title,
                                       waiting=waited(order.received_at, now)))

    return HomeData(tiles=tiles, attention=attention, clients=summaries, attention_counts=counts,
                    attention_filter=attention_filter, attention_more=max(0, counts[attention_filter] - len(attention)),
                    demo=next((c for c in clients if c.id == DEMO_ID), None))
