"""Home screen, the client pickers, and screens still to be built."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import Client
from app.web.summaries import home_data
from app.web.templating import templates

router = APIRouter()


def get_client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if client is None or client.archived_at is not None:
        raise HTTPException(status_code=404, detail="No client with that id")
    return client


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    data = home_data(session)
    return templates.TemplateResponse(request, "home.html", {"data": data, "nav": "clients"})


@router.get("/orders")
def orders_index(request: Request, session: Session = Depends(get_session)):
    clients = session.exec(select(Client).where(Client.archived_at.is_(None)).order_by(Client.name)).all()
    return templates.TemplateResponse(request, "choose_client.html", {
        "clients": clients, "nav": "orders", "title": "Orders",
        "intro": "Pick a client to see and upload their orders.", "suffix": "orders",
    })


@router.get("/trials")
def trials_index(request: Request, session: Session = Depends(get_session)):
    clients = session.exec(select(Client).where(Client.archived_at.is_(None)).order_by(Client.name)).all()
    return templates.TemplateResponse(request, "choose_client.html", {
        "clients": clients, "nav": "trials", "title": "Trials",
        "intro": "Pick a client to see their shadow trial results.", "suffix": "trial",
    })


@router.get("/clients/{client_id}/trial")
def client_trial(client_id: str, request: Request, session: Session = Depends(get_session)):
    client = get_client(session, client_id)
    return templates.TemplateResponse(request, "not_built.html", {
        "nav": "trials", "title": f"{client.name}: trial results",
        "message": "Trial results are not built yet.",
    })
