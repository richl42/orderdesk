"""Uploaded setup files waiting for the operator to check the column guesses.

An upload is kept on disk with a small JSON note beside it, so a half-finished
setup survives a restart. Saving imports it and clears the note.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.db import client_files_dir

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
TABLE_EXTENSIONS = {".xlsx", ".xlsm", ".csv", ".txt"}


class UploadRefused(ValueError):
    """A file we cannot use, with a message in plain words."""


def safe_filename(name: str) -> str:
    name = Path(name or "upload").name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .") or "upload"
    return name[:120]


def check_table_file(filename: str, data: bytes, allowed: set[str] = TABLE_EXTENSIONS) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xls":
        raise UploadRefused("This is an old-style Excel file (.xls). Open it in Excel and save it as .xlsx or .csv, "
                            "then upload that.")
    if suffix not in allowed:
        nice = ", ".join(sorted(allowed))
        raise UploadRefused(f"Files of type {suffix or 'unknown'} can't be read here. Use one of: {nice}.")
    if not data:
        raise UploadRefused("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRefused("That file is bigger than 25 MB. Remove any extra sheets or images and try again.")


@dataclass
class PendingUpload:
    kind: str
    filename: str  # as uploaded
    path: str  # where it is kept
    mapping: dict[str, str]

    @property
    def file(self) -> Path:
        return Path(self.path)


def _folder(client_id: str) -> Path:
    folder = client_files_dir(client_id) / "setup" / "pending"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save(client_id: str, kind: str, filename: str, data: bytes, mapping: dict[str, str] | None = None) -> PendingUpload:
    folder = _folder(client_id)
    clear(client_id, kind)
    path = folder / f"{kind}__{safe_filename(filename)}"
    path.write_bytes(data)
    upload = PendingUpload(kind=kind, filename=safe_filename(filename), path=str(path), mapping=mapping or {})
    write(client_id, upload)
    return upload


def write(client_id: str, upload: PendingUpload) -> None:
    (_folder(client_id) / f"{upload.kind}.json").write_text(json.dumps(asdict(upload)), encoding="utf-8")


def load(client_id: str, kind: str) -> PendingUpload | None:
    note = _folder(client_id) / f"{kind}.json"
    if not note.exists():
        return None
    upload = PendingUpload(**json.loads(note.read_text(encoding="utf-8")))
    return upload if upload.file.exists() else None


def clear(client_id: str, kind: str) -> None:
    folder = _folder(client_id)
    note = folder / f"{kind}.json"
    if note.exists():
        old = load(client_id, kind)
        if old and old.file.exists():
            old.file.unlink()
        note.unlink()
