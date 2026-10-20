"""Database engine, sessions and client-scoped queries."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, delete, select

from app import models
from app.config import get_settings

_engine: Engine | None = None


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
    return engine


def add_missing_columns(engine: Engine) -> None:
    """Add columns introduced after a database was created. New columns are always nullable."""
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"New column {table.name}.{column.name} must be nullable to add it")
                kind = column.type.compile(dialect=engine.dialect)
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}')


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().db_path)
    return _engine


def reset_engine() -> None:
    """Forget the current engine (used by tests and the seed command)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def scoped(model, client_id: str):
    """select() for a client-data table, always filtered to one client."""
    return select(model).where(model.client_id == client_id)


def client_files_dir(client_id: str) -> Path:
    path = get_settings().clients_dir / client_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_client(session: Session, client_id: str) -> None:
    """Remove every row and every file belonging to one client."""
    for model in models.CLIENT_TABLES:
        session.exec(delete(model).where(model.client_id == client_id))
    client = session.get(models.Client, client_id)
    if client is not None:
        session.delete(client)
    session.commit()
    folder = get_settings().clients_dir / client_id
    if folder.exists():
        shutil.rmtree(folder)
