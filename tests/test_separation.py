"""One client's data must never appear in another client's screens or exports."""

from sqlmodel import SQLModel, select

from app import models
from app.db import client_files_dir, delete_client, scoped
from app.models import CatalogueItem, Order
from app.web.summaries import summarise_client
from tests.helpers import add_client, add_order


def test_every_table_except_client_has_client_id():
    for name, table in SQLModel.metadata.tables.items():
        if name == "client":
            continue
        assert "client_id" in table.columns, f"{name} has no client_id"


def test_delete_client_covers_every_client_table():
    covered = {m.__tablename__ for m in models.CLIENT_TABLES}
    assert covered == set(SQLModel.metadata.tables) - {"client"}


def test_scoped_queries_only_return_one_clients_rows(session):
    a = add_client(session, "abc", "ABC Cleaning Supplies")
    b = add_client(session, "kes", "Kestrel Packaging")
    session.add(CatalogueItem(client_id="abc", product_code="X-1", description="Shared code"))
    session.add(CatalogueItem(client_id="kes", product_code="X-1", description="Shared code"))
    session.commit()
    add_order(session, "abc", "check")
    add_order(session, "kes", "problem")
    add_order(session, "kes", "problem")

    assert [o.client_id for o in session.exec(scoped(Order, "abc")).all()] == ["abc"]
    assert len(session.exec(scoped(CatalogueItem, "kes")).all()) == 1

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    assert summarise_client(session, a, now).to_review == 1
    assert summarise_client(session, b, now).to_review == 2
    assert summarise_client(session, a, now).products == 1


def test_delete_client_removes_only_that_clients_rows_and_files(session, demo_client):
    add_client(session, "abc", "ABC Cleaning Supplies")
    add_order(session, "abc", "check")
    add_order(session, "demo", "check")
    abc_folder = client_files_dir("abc")
    (abc_folder / "note.txt").write_text("x")
    demo_folder = client_files_dir("demo")

    delete_client(session, "abc")

    assert session.get(models.Client, "abc") is None
    assert not abc_folder.exists()
    for model in models.CLIENT_TABLES:
        assert session.exec(scoped(model, "abc")).first() is None
    assert session.get(models.Client, "demo") is not None
    assert session.exec(scoped(Order, "demo")).first() is not None
    assert session.exec(scoped(CatalogueItem, "demo")).first() is not None
    assert demo_folder.exists()
