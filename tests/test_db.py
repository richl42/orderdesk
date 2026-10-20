"""Databases made by an earlier milestone get new columns added on start-up."""

import sqlite3

from app import db
from app.config import get_settings


def test_missing_columns_are_added_to_an_old_database():
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = db.make_engine(path)
    engine.dispose()
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE "order" DROP COLUMN customer_name_raw')
        assert "customer_name_raw" not in {r[1] for r in conn.execute('PRAGMA table_info("order")')}

    db.make_engine(path).dispose()
    with sqlite3.connect(path) as conn:
        assert "customer_name_raw" in {r[1] for r in conn.execute('PRAGMA table_info("order")')}
