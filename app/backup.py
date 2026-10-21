"""`make backup` and the Home screen's Back up button: zip the data folder into
backups/orderdesk-backup-<date>_<time>.zip."""

from __future__ import annotations

import sqlite3
import zipfile
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
        conn = sqlite3.connect(settings.db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            conn.close()
    backups_dir = backups_dir or settings.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(LONDON).strftime("%Y-%m-%d_%H%M%S")
    base = backups_dir / f"orderdesk-backup-{stamp}"
    n = 2
    while base.with_suffix(".zip").exists():  # two backups in the same second
        base = backups_dir / f"orderdesk-backup-{stamp}_{n}"
        n += 1
    archive = base.with_suffix(".zip")
    skip = backups_dir.resolve()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(data_dir.rglob("*")):
            # On a server the backups folder is inside the data folder: never put old backups in a new one.
            if not path.is_file() or path.resolve().is_relative_to(skip):
                continue
            zf.write(path, path.relative_to(data_dir).as_posix())
    return archive


if __name__ == "__main__":
    path = make_backup()
    print(f"Backup saved: {path}")
