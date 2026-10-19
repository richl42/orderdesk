from datetime import datetime, timezone

from app.models import Client, Order


def add_client(session, client_id, name, mode="trial", setup_done=None, **extra):
    client = Client(id=client_id, name=name, short_name=name.replace(" ", ""), mode=mode,
                    setup_done=setup_done if setup_done is not None else ["details", "catalogue", "customers", "output"],
                    **extra)
    session.add(client)
    session.commit()
    return client


def add_order(session, client_id, status, received_at=None, flags=None, **extra):
    order = Order(client_id=client_id, source_type="paste", raw_text="test", status=status,
                  initial_status=status, processing_state="done",
                  received_at=received_at or datetime.now(timezone.utc), flags=flags or [], **extra)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order
