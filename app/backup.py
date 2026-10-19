"""`make backup`: zip the data folder into backups/orderdesk-backup-<date>_<time>.zip."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_DIR, get_settings
from pipeline.workdays import LONDON


def make_backup(backups_dir: Path | None = None) -> Path:
    data_dir = get_settings().data_dir
    if not data_dir.exists():
        raise SystemExit(f"Nothing to back up: {data_dir} does not exist yet.")
    backups_dir = backups_dir or PROJECT_DIR / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(LONDON).strftime("%Y-%m-%d_%H%M%S")
    archive = shutil.make_archive(str(backups_dir / f"orderdesk-backup-{stamp}"), "zip", root_dir=data_dir)
    return Path(archive)


if __name__ == "__main__":
    path = make_backup()
    print(f"Backup saved: {path}")
