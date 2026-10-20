"""Pipeline step 1: work out what kind of file an order is and turn it into plain text.

Pure functions, no database or web code. Works from the file's bytes, so the
caller decides where originals are stored.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import pdfplumber

from pipeline.tables import detect_encoding, find_header_row, parse_decimal, read_sheets
from pipeline.vocab import SourceType

SCANNED_PDF = "Scanned PDF, not supported yet"
MIN_PAGE_CHARS = 20

EXTENSION_TYPES = {
    ".txt": SourceType.TXT,
    ".text": SourceType.TXT,
    ".eml": SourceType.EML,
    ".pdf": SourceType.PDF,
    ".xlsx": SourceType.XLSX,
    ".xlsm": SourceType.XLSX,
    ".csv": SourceType.CSV,
}

# Attachment types read as part of an email. Other attachments (logos, signatures) are skipped.
ATTACHMENT_TYPES = {SourceType.PDF, SourceType.XLSX, SourceType.CSV, SourceType.TXT}
MIME_TYPES = {
    "application/pdf": SourceType.PDF,
    "text/csv": SourceType.CSV,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": SourceType.XLSX,
}


class NotSupported(ValueError):
    """A file that cannot be read as an order at all, with a plain-words reason."""


@dataclass
class Ingested:
    source_type: str
    text: str
    sender_email: str | None = None
    sender_name: str | None = None
    subject: str | None = None
    sent_at: datetime | None = None
    attachments: list[str] = field(default_factory=list)  # attachment file names that were read
    problem: str | None = None  # a reason the order cannot be completed, e.g. a scanned PDF
    notes: list[str] = field(default_factory=list)  # things worth telling the operator

    @property
    def content_hash(self) -> str:
        return content_hash(self.text)


# ---------------------------------------------------------------- shared helpers


def detect_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in EXTENSION_TYPES:
        return EXTENSION_TYPES[suffix]
    if suffix == ".xls":
        raise NotSupported("Old Excel files (.xls) can't be read. Save it as .xlsx or .csv and upload it again.")
    if suffix in {".jpg", ".jpeg", ".png", ".heic", ".gif", ".tif", ".tiff"}:
        raise NotSupported("Photos of orders are not supported yet.")
    if suffix in {".doc", ".docx"}:
        raise NotSupported("Word files are not supported yet. Save it as PDF and upload it again.")
    raise NotSupported("This kind of file is not supported. Use an email, PDF, Excel, CSV or text file.")


def clean_text(text: str) -> str:
    """Tidy extracted text: Unix line ends, no non-breaking spaces, no trailing spaces, at most one blank line."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("​", "")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    """SHA-256 of the text with case and spacing normalised, so a re-sent copy hashes the same."""
    normalised = " ".join((text or "").lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def decode_text(data: bytes) -> str:
    return data.decode(detect_encoding(data), errors="replace")


# ---------------------------------------------------------------- HTML email bodies


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote",
             "section", "article", "header", "footer"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    lines = []
    for line in "".join(parser.parts).split("\n"):
        line = re.sub(r"^\s*\|\s*", "", line.strip())  # a leading cell separator
        lines.append(re.sub(r" {2,}", " ", line))
    return clean_text("\n".join(lines))


# ---------------------------------------------------------------- spreadsheets


def _join_loose(cells: list[str]) -> str:
    """A row outside the table (a title, 'PO number: 2291', a note) as one line of text."""
    if len(cells) == 2 and not cells[0].endswith(":"):
        return f"{cells[0]}: {cells[1]}"
    return " ".join(cells) if cells and cells[0].endswith(":") else " | ".join(cells)


def _order_header_row(grid: list[list[str]]) -> int:
    """The row of column headings in an order sheet. Rows above it (customer, PO, dates) are kept as text."""
    width = max((sum(1 for c in row if c) for row in grid[:20]), default=0)
    for i, row in enumerate(grid[:20]):
        filled = [c for c in row if c]
        if len(filled) >= max(3, (width + 1) // 2) and \
                sum(1 for c in filled if parse_decimal(c) is None) >= len(filled) * 0.75:
            return i
    return find_header_row(grid)


def grid_text(grid: list[list[str]]) -> str:
    """Turn one sheet into readable text: rows above the table as plain lines, each table row as 'Column: value'."""
    grid = [[(c or "").replace("\n", " ").strip() for c in row] for row in grid]
    grid = [row for row in grid if any(row)] if grid else []
    if not grid:
        return ""
    header_at = _order_header_row(grid)
    width = max(len(r) for r in grid)
    header = (grid[header_at] + [""] * width)[:width]
    columns = [h or f"Column {i + 1}" for i, h in enumerate(header)]
    used_columns = sum(1 for h in header if h)

    lines = [_join_loose([c for c in row if c]) for row in grid[:header_at]]
    if lines:
        lines.append("")
    for row in grid[header_at + 1:]:
        cells = (row + [""] * width)[:width]
        filled = [(columns[i], c) for i, c in enumerate(cells) if c]
        if used_columns >= 4 and len(filled) <= 2:
            lines.append(_join_loose([c for _, c in filled]))  # a note or total under the table
        else:
            lines.append(" | ".join(f"{col}: {value}" for col, value in filled))
    return "\n".join(lines)


def spreadsheet_text(data: bytes, filename: str) -> str:
    sheets = [(name, grid_text(grid)) for name, grid in read_sheets(data, filename)]
    sheets = [(name, text) for name, text in sheets if text.strip()]
    if len(sheets) == 1:
        return clean_text(sheets[0][1])
    return clean_text("\n\n".join(f"Sheet: {name}\n{text}" for name, text in sheets))


# ---------------------------------------------------------------- PDF


def _table_text(rows: list[list[str | None]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    header = rows[0]
    wordy = [c for c in header if c and parse_decimal(c) is None]
    if len(rows) < 2 or len(wordy) < max(2, len([c for c in header if c]) * 0.75):
        return "\n".join(" | ".join(c for c in row if c) for row in rows)
    columns = [h or f"Column {i + 1}" for i, h in enumerate(header)]
    out = []
    for row in rows[1:]:
        filled = [(columns[i] if i < len(columns) else f"Column {i + 1}", c) for i, c in enumerate(row) if c]
        out.append(" | ".join(f"{col}: {value}" for col, value in filled))
    return "\n".join(out)


def _page_text(page) -> str:
    """Text of one page, with ruled tables written out as 'Column: value' rows in their place."""
    tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
    if not tables:
        return page.extract_text() or ""
    x0, top, x1, bottom = page.bbox
    parts: list[str] = []
    cursor = top
    for table in tables:
        t_top, t_bottom = max(table.bbox[1], cursor), min(table.bbox[3], bottom)
        if t_top > cursor:
            parts.append(page.crop((x0, cursor, x1, t_top)).extract_text() or "")
        parts.append(_table_text(table.extract()))
        cursor = max(cursor, t_bottom)
    if cursor < bottom:
        parts.append(page.crop((x0, cursor, x1, bottom)).extract_text() or "")
    return "\n".join(p for p in parts if p.strip())


def pdf_text(data: bytes) -> tuple[str, bool]:
    """(text, scanned). A page with almost no text but an image on it means a scan or photo."""
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # pdfplumber raises several kinds of error for broken files
        raise NotSupported("This PDF could not be opened. It may be damaged or password protected.") from exc
    with pdf:
        pages = []
        scanned = False
        for page in pdf.pages:
            text = _page_text(page)
            if len(text.strip()) < MIN_PAGE_CHARS and page.images:
                scanned = True
            pages.append(text)
    text = clean_text("\n\n".join(pages))
    if len(text) < MIN_PAGE_CHARS:
        scanned = True
    return text, scanned


# ---------------------------------------------------------------- email


def _part_type(part: EmailMessage) -> str | None:
    name = part.get_filename() or ""
    suffix = Path(name).suffix.lower()
    if suffix in EXTENSION_TYPES:
        kind = EXTENSION_TYPES[suffix]
        return kind if kind in ATTACHMENT_TYPES else None
    return MIME_TYPES.get(part.get_content_type())


def _email_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def email_envelope(data: bytes) -> tuple[str | None, str | None, datetime | None]:
    """(sender email, subject, sent time) from an .eml file's headers, without reading the body."""
    msg = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)
    _name, address = parseaddr(str(msg.get("From", "")))
    return address.lower() or None, str(msg.get("Subject", "")).strip() or None, _email_date(msg.get("Date"))


def ingest_email(data: bytes) -> Ingested:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    name, address = parseaddr(str(msg.get("From", "")))
    result = Ingested(
        source_type=SourceType.EML,
        text="",
        sender_email=address.lower() or None,
        sender_name=name or None,
        subject=str(msg.get("Subject", "")).strip() or None,
        sent_at=_email_date(msg.get("Date")),
    )

    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = html_to_text(content) if body_part.get_content_type() == "text/html" else clean_text(content)

    sections = [body] if body else []
    for part in msg.iter_attachments():
        kind = _part_type(part)
        filename = part.get_filename() or "attachment"
        if kind is None:
            if part.get_content_maintype() != "image":
                result.notes.append(f"Attachment {filename} was not read (not an order file type).")
            continue
        payload = part.get_payload(decode=True) or b""
        try:
            inner = ingest_file(filename, payload)
        except NotSupported as exc:
            result.notes.append(f"Attachment {filename} was not read: {exc}")
            continue
        result.attachments.append(filename)
        if inner.problem and not result.problem:
            result.problem = inner.problem
        if inner.text:
            sections.append(f"--- Attached file: {filename} ---\n{inner.text}")
    result.text = clean_text("\n\n".join(sections))
    return result


# ---------------------------------------------------------------- entry points


def ingest_paste(text: str) -> Ingested:
    return Ingested(source_type=SourceType.PASTE, text=clean_text(text))


def ingest_file(filename: str, data: bytes) -> Ingested:
    """Read one uploaded file. Raises NotSupported for files that cannot be orders at all."""
    kind = detect_type(filename)
    if kind == SourceType.EML:
        return ingest_email(data)
    if kind == SourceType.TXT:
        return Ingested(source_type=kind, text=clean_text(decode_text(data)))
    if kind == SourceType.PDF:
        text, scanned = pdf_text(data)
        return Ingested(source_type=kind, text=text, problem=SCANNED_PDF if scanned else None)
    if kind in (SourceType.XLSX, SourceType.CSV):
        try:
            text = spreadsheet_text(data, filename)
        except Exception as exc:  # pandas and openpyxl raise many kinds of error for broken files
            raise NotSupported("This spreadsheet could not be opened. It may be damaged or password protected.") from exc
        return Ingested(source_type=kind, text=text)
    raise NotSupported("This kind of file is not supported.")

