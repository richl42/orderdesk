"""`make backup` and the Home screen's Back up button: zip the data folder into
backups/orderdesk-backup-<date>_<time>.zip."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from pipeline.workdays import LONDON


def make_backup(backups_dir: Path | None = None) -> Path:
    settings = get_settings()
    data_dir = settings.data_dir
    if not data_dir.exists():
        raise SystemExit(f"Nothing to back up: {data_dir} does not exist yet.")
    if settings.db_path.exists():
        # Write recent changes from the WAL file into the database file, so the zip opens on its own.
        with sqlite3.connect(settings.db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
    backups_dir = backups_dir or settings.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(LONDON).strftime("%Y-%m-%d_%H%M%S")
    base = backups_dir / f"orderdesk-backup-{stamp}"
    n = 2
    while base.with_suffix(".zip").exists():  # two backups in the same second
        base = backups_dir / f"orderdesk-backup-{stamp}_{n}"
        n += 1
    archive = shutil.make_archive(str(base), "zip", root_dir=data_dir)
    return Path(archive)


if __name__ == "__main__":
    path = make_backup()
    print(f"Backup saved: {path}")
