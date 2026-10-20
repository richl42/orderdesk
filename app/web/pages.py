"""Home screen, the client pickers, and screens still to be built."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import Client
from app.trial.service import trial_view
from app.web import format as fmt
from app.web.summaries import MODE_LABELS, delete_reminder, home_data, mode_key
from app.web.templating import templates
from pipeline.workdays import london_now

router = APIRouter()


def get_client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if client is None or client.archived_at is not None:
        raise HTTPException(status_code=404, detail="No client with that id")
    return client


@router.get("/")
def home(request: Request, attention: str = "all", session: Session = Depends(get_session)):
    data = home_data(session, attention_filter=attention)
    return templates.TemplateResponse(request, "home.html", {"data": data, "nav": "clients"})


@router.get("/orders")
def orders_index(request: Request, session: Session = Depends(get_session)):
    clients = session.exec(select(Client).where(Client.archived_at.is_(None)).order_by(Client.name)).all()
    return templates.TemplateResponse(request, "choose_client.html", {
        "clients": clients, "nav": "orders", "title": "Orders",
        "intro": "Pick a client to see and upload their orders.", "suffix": "orders",
    })


@dataclass
class TrialRow:
    client: Client
    mode_key: str
    mode_label: str
    day: str
    staff: str
    exactly_right: str
    confidently_wrong: str
    confidently_wrong_count: int | None
    reminder: tuple[str, str] | None


def trial_row(session: Session, client: Client, today: date) -> TrialRow:
    view = trial_view(session, client, today=today)
    if view.trial_day is None:
        day = "No trial dates set"
    elif view.trial_day == 0:
        day = f"Starts {fmt.long_date(client.trial_start_date)}"
    elif view.trial_day > client.trial_working_days:
        day = f"Finished ({client.trial_working_days} working days)"
    else:
        day = f"Day {view.trial_day} of {client.trial_working_days}"
    m = view.measures
    if view.upload is None:
        staff = "Not loaded yet"
    else:
        staff = f"Loaded {fmt.moment(view.upload.uploaded_at)}"
    if m and m.compared:
        right = f"{m.exactly_right} of {m.compared} ({fmt.percent(m.exactly_right, m.compared)})"
        wrong, wrong_count = str(m.confidently_wrong), m.confidently_wrong
    else:
        right, wrong, wrong_count = "n/a", "n/a", None
    return TrialRow(client, mode_key(client), MODE_LABELS[mode_key(client)], day, staff, right, wrong, wrong_count,
                    delete_reminder(client, today))


@router.get("/trials")
def trials_index(request: Request, session: Session = Depends(get_session)):
    clients = session.exec(select(Client).where(Client.archived_at.is_(None)).order_by(Client.name)).all()
    today = london_now().date()
    return templates.TemplateResponse(request, "trials.html", {
        "rows": [trial_row(session, c, today) for c in clients], "nav": "trials",
    })
