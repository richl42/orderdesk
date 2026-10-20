"""What the setup screen shows and saves, section by section."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.ai_client import get_ai
from app.clientsetup import importers, pending
from app.config import get_settings
from app.db import client_files_dir, scoped
from app.models import CatalogueItem, Client, ColumnMapping, Customer, Event, OutputTemplate, PriceListEntry
from pipeline import export
from pipeline.ai import AIUnavailable, cost_gbp
from pipeline.column_guess import suggest_columns
from pipeline.tables import (
    FIELDS,
    IGNORE,
    OUTPUT_FIELDS,
    column_for,
    duplicate_values,
    guess_mapping,
    guess_output_template,
    missing_required,
    parse_decimal,
    read_table,
)
from pipeline.vocab import DEFAULT_RULES, DEFAULT_UNIT_SYNONYMS, SETUP_STEPS, ClientMode, MappingKind
from pipeline.workdays import london_now, next_working_day

ERP_SYSTEMS = ["Sage 200", "Sage 50", "Unleashed", "Pegasus Opera", "Other"]
MODE_CHOICES = [
    (ClientMode.TRIAL, "Shadow trial (never entered into their system)"),
    (ClientMode.LIVE, "Live service"),
    (ClientMode.DEMO, "Demo"),
]
STEP_LABELS = {"details": "Details", "catalogue": "Catalogue", "customers": "Customers",
               "output": "Output format", "rules": "Rules"}
TABLE_KINDS = {
    MappingKind.CATALOGUE: ("products", "product"),
    MappingKind.CUSTOMERS: ("customers", "customer"),
    MappingKind.PRICES: ("prices", "price"),
    MappingKind.STAFF_ENTRIES: ("staff entries", "staff entry"),
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- step strip


@dataclass
class Step:
    number: int
    key: str
    label: str
    done: bool
    current: bool


def steps(client: Client | None) -> list[Step]:
    done = set(client.setup_done) if client else set()
    current = next((s for s in SETUP_STEPS if s not in done), None)
    return [Step(i, key, STEP_LABELS[key], key in done, key == current)
            for i, key in enumerate(SETUP_STEPS, start=1)]


# ---------------------------------------------------------------- 1. details


def default_short_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name.title() if name.islower() else name)[:24] or "Client"


def new_client_id(session: Session, short_name: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", short_name.lower())[:20] or "client"
    candidate, n = base, 2
    while session.get(Client, candidate) is not None:
        candidate = f"{base}{n}"
        n += 1
    return candidate


@dataclass
class DetailsForm:
    name: str = ""
    short_name: str = ""
    erp_system: str = "Sage 200"
    mode: str = ClientMode.TRIAL
    contact_name: str = ""
    contact_email: str = ""
    trial_start_date: str = ""
    trial_working_days: str = "10"
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_client(cls, client: Client) -> "DetailsForm":
        return cls(
            name=client.name, short_name=client.short_name, erp_system=client.erp_system or "Other",
            mode=client.mode, contact_name=client.contact_name, contact_email=client.contact_email,
            trial_start_date=client.trial_start_date.isoformat() if client.trial_start_date else "",
            trial_working_days=str(client.trial_working_days),
        )

    def validate(self) -> dict:
        """Check the form; return clean values, filling self.errors with plain words."""
        self.errors = {}
        name = self.name.strip()
        if not name:
            self.errors["name"] = "Enter the client's name."
        short = re.sub(r"[^A-Za-z0-9_-]", "", self.short_name.strip()) or default_short_name(name)
        if self.erp_system not in ERP_SYSTEMS:
            self.errors["erp_system"] = "Pick their order system from the list."
        if self.mode not in {m for m, _ in MODE_CHOICES}:
            self.errors["mode"] = "Pick a mode from the list."
        email = self.contact_email.strip()
        if email and not EMAIL_RE.match(email):
            self.errors["contact_email"] = "That doesn't look like an email address."
        start = None
        if self.trial_start_date.strip():
            try:
                start = date.fromisoformat(self.trial_start_date.strip())
            except ValueError:
                self.errors["trial_start_date"] = "Enter the date as day, month and year."
        try:
            days = int(self.trial_working_days or 10)
            if not 1 <= days <= 60:
                raise ValueError
        except ValueError:
            self.errors["trial_working_days"] = "Enter a number of working days between 1 and 60."
            days = 10
        return {
            "name": name, "short_name": short, "erp_system": self.erp_system, "mode": self.mode,
            "contact_name": self.contact_name.strip(), "contact_email": email,
            "trial_start_date": start, "trial_working_days": days,
        }


def create_client(session: Session, form: DetailsForm) -> Client | None:
    values = form.validate()
    if form.errors:
        return None
    client = Client(
        id=new_client_id(session, values["short_name"]),
        rules=dict(DEFAULT_RULES),
        unit_synonyms={k: list(v) for k, v in DEFAULT_UNIT_SYNONYMS.items()},
        setup_done=["details"],
        **values,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def update_details(session: Session, client: Client, form: DetailsForm) -> bool:
    values = form.validate()
    if form.errors:
        return False
    for key, value in values.items():
        setattr(client, key, value)
    importers.mark_setup_step(client, "details")
    session.add(client)
    session.commit()
    return True


# ---------------------------------------------------------------- 2, 3. table files


@dataclass
class ColumnRow:
    index: int
    column: str
    example: str
    selected: str
    unsure: bool


@dataclass
class TableSection:
    kind: str
    filename: str | None = None
    row_count: int = 0
    saved_count: int = 0
    is_pending: bool = False
    columns: list[ColumnRow] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    error: str | None = None
    notice: str | None = None
    warnings: list[str] = field(default_factory=list)
    extra_notes: list[str] = field(default_factory=list)

    @property
    def has_file(self) -> bool:
        return self.filename is not None

    @property
    def noun(self) -> str:
        return TABLE_KINDS[self.kind][0]

    @property
    def missing_labels(self) -> list[str]:
        return [self.options[m] for m in self.missing]


def saved_mapping(session: Session, client_id: str, kind: str) -> ColumnMapping | None:
    return session.exec(scoped(ColumnMapping, client_id).where(ColumnMapping.kind == kind)).first()


def saved_count(session: Session, client_id: str, kind: str) -> int:
    from app.models import StaffEntryLine

    model = {MappingKind.CATALOGUE: CatalogueItem, MappingKind.CUSTOMERS: Customer,
             MappingKind.PRICES: PriceListEntry, MappingKind.STAFF_ENTRIES: StaffEntryLine}[kind]
    return len(session.exec(select(model.id).where(model.client_id == client_id)).all())


def _shared_domain_note(session: Session, client_id: str) -> list[str]:
    owners: dict[str, list[str]] = {}
    for customer in session.exec(scoped(Customer, client_id)).all():
        for domain in customer.email_domains:
            owners.setdefault(domain, []).append(customer.name)
    notes = []
    for domain, names in owners.items():
        if len(names) > 1:
            notes.append(f"{' and '.join(names)} share the email domain {domain}, so orders from them are "
                         f"recognised by full email address only.")
    return notes


def table_section(session: Session, client: Client, kind: str, mapping: dict[str, str] | None = None,
                  error: str | None = None, notice: str | None = None, warnings: list[str] | None = None,
                  ) -> TableSection:
    """Everything the screen needs for one file section, from the pending upload if there is one."""
    section = TableSection(kind=kind, options=FIELDS[kind], error=error, notice=notice, warnings=warnings or [])
    section.saved_count = saved_count(session, client.id, kind)
    upload = pending.load(client.id, kind)
    saved = saved_mapping(session, client.id, kind)

    if upload:
        path, section.filename, section.is_pending = upload.file, upload.filename, True
        chosen = mapping or upload.mapping
    elif saved and Path(saved.stored_path).exists():
        path, section.filename = Path(saved.stored_path), saved.source_filename
        chosen = mapping or saved.mapping
    else:
        return section

    try:
        table = read_table(path)
    except Exception:
        section.error = "This file could not be read. Check it opens in Excel, then upload it again."
        return section

    guess = guess_mapping(table.columns, kind, table.rows)
    chosen = {col: chosen.get(col, guess.mapping[col]) for col in table.columns}
    section.row_count = len(table.rows)
    section.columns = [
        ColumnRow(i, col, table.example(col), chosen[col], col in guess.unsure and upload is not None)
        for i, col in enumerate(table.columns)
    ]
    section.missing = missing_required(chosen, kind)
    key_field = {"catalogue": "product_code", "customers": "account_code"}.get(kind)
    if key_field:
        section.duplicates = duplicate_values(table.rows, column_for(chosen, key_field))
    if kind == MappingKind.CUSTOMERS and not section.is_pending:
        section.extra_notes = _shared_domain_note(session, client.id)
    return section


def mapping_from_form(form: dict, columns: list[ColumnRow] | list[str]) -> dict[str, str]:
    names = [c.column if isinstance(c, ColumnRow) else c for c in columns]
    mapping = {}
    for i, col in enumerate(names):
        mapping[col] = form.get(f"map_{i}") or IGNORE
    # Each meaning may be used once; a second use of the same meaning is ignored.
    seen: set[str] = set()
    for col, fld in mapping.items():
        if fld != IGNORE and fld in seen:
            mapping[col] = IGNORE
        seen.add(fld)
    return mapping


def current_columns(session: Session, client: Client, kind: str) -> list[str]:
    upload = pending.load(client.id, kind)
    saved = saved_mapping(session, client.id, kind)
    if upload:
        return read_table(upload.file).columns
    if saved:
        return list(saved.source_columns)
    return []


def guess_with_ai(session: Session | None, client: Client, kind: str, table) -> dict[str, str]:
    """Guess each column from its header and values, then ask the AI about columns whose headers are unclear.

    If the AI can't be reached, the guesses from headers and values stand.
    """
    guess = guess_mapping(table.columns, kind, table.rows)
    mapping = dict(guess.mapping)
    if not guess.unsure:
        return mapping
    taken = {fld for col, fld in mapping.items() if col not in guess.unsure and fld != IGNORE}
    try:
        suggestions, call = suggest_columns(get_ai(), kind, table, guess.unsure, taken)
    except AIUnavailable:
        return mapping
    used = set(taken) | {fld for fld in suggestions.values() if fld != IGNORE}
    for col in guess.unsure:
        if col in suggestions:
            mapping[col] = suggestions[col]
        elif mapping[col] != IGNORE and mapping[col] in used:
            mapping[col] = IGNORE  # the AI gave this meaning to another column
        else:
            used.add(mapping[col])
    if session is not None:
        settings = get_settings()
        cost = cost_gbp(call, settings.ai_price_input_per_mtok, settings.ai_price_output_per_mtok)
        session.add(Event(client_id=client.id, type="ai_call", details={
            "purpose": "column_guess", "kind": kind, "model": call.model, "input_tokens": call.input_tokens,
            "cache_write_tokens": call.cache_write_tokens, "cache_read_tokens": call.cache_read_tokens,
            "output_tokens": call.output_tokens, "cost_gbp": str(cost) if cost is not None else None,
            "cached": call.cached, "usable": call.input is not None, "columns": len(guess.unsure),
        }))
        session.commit()
    return mapping


def receive_table_upload(client: Client, kind: str, filename: str, data: bytes,
                         session: Session | None = None) -> str | None:
    """Keep the upload as pending. Returns an error in plain words, or None."""
    try:
        pending.check_table_file(filename, data)
        upload = pending.save(client.id, kind, filename, data)
        table = read_table(upload.file)
    except pending.UploadRefused as refused:
        return str(refused)
    except Exception:
        pending.clear(client.id, kind)
        return "This file could not be read. Check it opens in Excel, then upload it again."
    if not table.columns or not table.rows:
        pending.clear(client.id, kind)
        return "No rows were found in that file. Check it is the right file and the first sheet has the list."
    upload.mapping = guess_with_ai(session, client, kind, table)
    pending.write(client.id, upload)
    return None


def save_table(session: Session, client: Client, kind: str, mapping: dict[str, str]) -> TableSection:
    upload = pending.load(client.id, kind)
    saved = saved_mapping(session, client.id, kind)
    if upload:
        path, filename = upload.file, upload.filename
    elif saved and Path(saved.stored_path).exists():
        path, filename = Path(saved.stored_path), saved.source_filename
    else:
        return table_section(session, client, kind, error="Upload a file first.")

    missing = missing_required(mapping, kind)
    if missing:
        return table_section(session, client, kind, mapping=mapping,
                             error="Choose which columns hold: " + ", ".join(FIELDS[kind][m] for m in missing) + ".")
    from app.trial.service import import_staff_entries

    importer = {MappingKind.CATALOGUE: importers.import_catalogue, MappingKind.CUSTOMERS: importers.import_customers,
                MappingKind.PRICES: importers.import_prices, MappingKind.STAFF_ENTRIES: import_staff_entries}[kind]
    table = read_table(path)
    result = importer(session, client, path, table, mapping, filename=filename)
    pending.clear(client.id, kind)

    noun = TABLE_KINDS[kind][0]
    parts = [f"Saved {result.count} {noun}."]
    if result.duplicates:
        parts.append(f"{len(result.duplicates)} repeated code{'s' if len(result.duplicates) != 1 else ''} "
                     f"kept once (first row used).")
    if result.skipped_rows:
        parts.append(f"{len(result.skipped_rows)} row{'s' if len(result.skipped_rows) != 1 else ''} with no code "
                     f"skipped (rows {', '.join(map(str, result.skipped_rows[:10]))}).")
    session.refresh(client)
    return table_section(session, client, kind, notice=" ".join(parts), warnings=result.warnings[:20])


# ---------------------------------------------------------------- 4. output format


@dataclass
class OutputRow:
    index: int
    column: str
    kind: str  # a field name, "fixed" or "blank"
    fixed: str
    example: str


@dataclass
class OutputSection:
    filename: str | None = None
    is_pending: bool = False
    rows: list[OutputRow] = field(default_factory=list)
    date_format: str = "%d/%m/%Y"
    decimal_places: int = 2
    delimiter: str = ","
    encoding: str = "utf-8"
    include_header: bool = True
    preview: str = ""
    error: str | None = None
    notice: str | None = None
    saved: bool = False

    @property
    def has_file(self) -> bool:
        return self.filename is not None

    def as_template(self) -> dict:
        fill = {}
        for row in self.rows:
            if row.kind == "fixed":
                fill[row.column] = {"fixed": row.fixed}
            elif row.kind == "blank":
                fill[row.column] = {"blank": True}
            else:
                fill[row.column] = {"field": row.kind}
        return {
            "columns": [r.column for r in self.rows], "fill": fill, "date_format": self.date_format,
            "decimal_places": self.decimal_places, "delimiter": self.delimiter, "encoding": self.encoding,
            "include_header": self.include_header,
        }


def output_file_path(client: Client, filename: str) -> Path:
    return client_files_dir(client.id) / "setup" / f"{MappingKind.OUTPUT}_{filename}"


def _rows_from(columns: list[str], fill: dict, examples: list[str]) -> list[OutputRow]:
    rows = []
    for i, col in enumerate(columns):
        spec = fill.get(col, {"blank": True})
        kind = spec.get("field") or ("fixed" if "fixed" in spec else "blank")
        rows.append(OutputRow(i, col, kind, str(spec.get("fixed", "")), examples[i] if i < len(examples) else ""))
    return rows


def output_section(session: Session, client: Client, form: dict | None = None,
                   error: str | None = None, notice: str | None = None) -> OutputSection:
    section = OutputSection(error=error, notice=notice)
    upload = pending.load(client.id, MappingKind.OUTPUT)
    template = session.exec(scoped(OutputTemplate, client.id)).first()

    if upload:
        guess = guess_output_template(upload.file)
        section.filename, section.is_pending = upload.filename, True
        examples = guess.example_rows[0] if guess.example_rows else []
        section.rows = _rows_from(guess.columns, guess.fill, examples)
        section.date_format, section.decimal_places = guess.date_format, guess.decimal_places
        section.delimiter, section.encoding, section.include_header = guess.delimiter, guess.encoding, guess.include_header
    elif template:
        path = output_file_path(client, template.source_filename)
        examples = []
        if path.exists():
            guess = guess_output_template(path)
            examples = guess.example_rows[0] if guess.example_rows else []
        section.filename, section.saved = template.source_filename, True
        section.rows = _rows_from(template.columns, template.fill, examples)
        section.date_format, section.decimal_places = template.date_format, template.decimal_places
        section.delimiter, section.encoding, section.include_header = (
            template.delimiter, template.encoding, template.include_header)
    else:
        return section

    if form is not None:
        apply_output_form(section, form)
    section.preview = export.render_orders_text([sample_order(session, client)], section.as_template())
    return section


def apply_output_form(section: OutputSection, form: dict) -> None:
    valid_fields = set(OUTPUT_FIELDS) | {"fixed", "blank"}
    for row in section.rows:
        kind = form.get(f"fill_{row.index}", row.kind)
        row.kind = kind if kind in valid_fields else "blank"
        row.fixed = form.get(f"fixed_{row.index}", row.fixed or "")
    if form.get("date_format") in export.DATE_FORMAT_NAMES:
        section.date_format = form["date_format"]
    if form.get("delimiter") in export.DELIMITER_NAMES:
        section.delimiter = form["delimiter"]
    if form.get("encoding") in export.ENCODING_NAMES:
        section.encoding = form["encoding"]
    try:
        section.decimal_places = max(0, min(4, int(form.get("decimal_places", section.decimal_places))))
    except ValueError:
        pass
    section.include_header = form.get("include_header") in ("on", "true", "1")


def receive_output_upload(client: Client, filename: str, data: bytes) -> str | None:
    try:
        pending.check_table_file(filename, data, allowed={".csv", ".txt"})
        upload = pending.save(client.id, MappingKind.OUTPUT, filename, data)
        guess = guess_output_template(upload.file)
    except pending.UploadRefused as refused:
        if filename.lower().endswith((".xlsx", ".xls")):
            return ("Exports are made as CSV files, so the example needs to be a CSV. Export one order from "
                    "their system as CSV (or save the Excel file as CSV) and upload that.")
        return str(refused)
    except Exception:
        pending.clear(client.id, MappingKind.OUTPUT)
        return "This file could not be read. Check it is the example import file from their system."
    if not guess.columns:
        pending.clear(client.id, MappingKind.OUTPUT)
        return "No columns were found in that file."
    return None


def save_output(session: Session, client: Client, form: dict) -> OutputSection:
    section = output_section(session, client, form=form)
    if not section.has_file:
        return output_section(session, client, error="Upload an example file first.")
    upload = pending.load(client.id, MappingKind.OUTPUT)
    template_values = section.as_template()
    if upload:
        importers.import_output_template(session, client, upload.file, filename=upload.filename,
                                         overrides=template_values)
        pending.clear(client.id, MappingKind.OUTPUT)
    else:
        template = session.exec(scoped(OutputTemplate, client.id)).one()
        for key, value in template_values.items():
            setattr(template, key, value)
        session.add(template)
        importers.mark_setup_step(client, "output")
        session.add(client)
        session.commit()
    session.refresh(client)
    return output_section(session, client, notice="Output format saved.")


def sample_order(session: Session, client: Client) -> dict:
    """A made-up order from this client's own customers and products, for previews."""
    customer = session.exec(scoped(Customer, client.id).order_by(Customer.account_code)).first()
    items = session.exec(scoped(CatalogueItem, client.id).order_by(CatalogueItem.product_code).limit(2)).all()
    band_prices = {}
    if customer and customer.price_band:
        band_prices = {p.product_code: p.price for p in session.exec(
            scoped(PriceListEntry, client.id).where(PriceListEntry.price_band == customer.price_band)).all()}

    received = london_now().replace(hour=10, minute=12, second=0, microsecond=0).astimezone(timezone.utc)
    delivery = next_working_day(london_now().date() + timedelta(days=1))
    lines = []
    for n, item in enumerate(items or [None, None], start=1):
        price = None
        if item is not None:
            price = band_prices.get(item.product_code) or item.price
        lines.append({
            "line_no": n,
            "product_code": item.product_code if item else f"PRODUCT-{n}",
            "qty": 10 if n == 1 else 4,
            "unit": item.sell_unit if item else "CASE",
            "unit_price": str(price) if price is not None else None,
        })
    return {
        "client_id": client.id,
        "customer": {"account_code": customer.account_code if customer else "ACCOUNT1",
                     "name": customer.name if customer else "Example customer"},
        "po_number": "2291",
        "received_at": received.isoformat(),
        "requested_delivery_date": delivery.isoformat(),
        "delivery_notes": "Kitchen entrance",
        "lines": lines,
    }


# ---------------------------------------------------------------- 5. rules


RULE_CHECKBOXES = [
    ("flag_price_differences", "Flag any price that differs from their price list"),
    ("duplicate_check", "Flag possible duplicates of orders from the last few days"),
    ("convert_units", "Convert units using pack sizes (for example, 120 bottles into 10 cases)"),
    ("require_po_number", "Flag orders with no PO number"),
    ("allow_substitutions", "Allow substitutions without asking the client"),
]


@dataclass
class RulesForm:
    cutoff_time: str = ""
    min_order_value: str = ""
    duplicate_window_days: str = "7"
    minutes_per_order: str = "5"
    checks: dict[str, bool] = field(default_factory=dict)
    allow_ai_ready: bool = False
    units: list[tuple[str, str]] = field(default_factory=list)  # (unit, comma separated words)
    errors: dict[str, str] = field(default_factory=dict)
    notice: str | None = None

    @classmethod
    def from_client(cls, client: Client) -> "RulesForm":
        rules = {**DEFAULT_RULES, **(client.rules or {})}
        checks = {key: bool(rules.get(key)) for key, _ in RULE_CHECKBOXES if key != "duplicate_check"}
        checks["duplicate_check"] = (rules.get("duplicate_window_days") or 0) > 0
        synonyms = client.unit_synonyms or DEFAULT_UNIT_SYNONYMS
        return cls(
            cutoff_time=rules.get("cutoff_time") or "",
            min_order_value=str(rules.get("min_order_value") or ""),
            duplicate_window_days=str(rules.get("duplicate_window_days") or 7),
            minutes_per_order=str(client.minutes_per_order),
            checks=checks,
            allow_ai_ready=bool(rules.get("allow_ai_ready")),
            units=[(unit, ", ".join(words)) for unit, words in synonyms.items()],
        )


def save_rules(session: Session, client: Client, form: dict) -> RulesForm:
    result = RulesForm.from_client(client)
    errors: dict[str, str] = {}

    cutoff = (form.get("cutoff_time") or "").strip()
    if cutoff:
        match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", cutoff)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            errors["cutoff_time"] = "Enter a time like 15:00."
        else:
            cutoff = f"{int(match.group(1)):02d}:{match.group(2)}"

    minimum_text = (form.get("min_order_value") or "").strip()
    minimum = parse_decimal(minimum_text) if minimum_text else None
    if minimum_text and (minimum is None or minimum < 0):
        errors["min_order_value"] = "Enter an amount in pounds, like 75.00, or leave it empty."

    try:
        window = int(form.get("duplicate_window_days") or 7)
        if not 1 <= window <= 90:
            raise ValueError
    except ValueError:
        errors["duplicate_window_days"] = "Enter a number of days between 1 and 90."
        window = 7

    try:
        minutes = int(form.get("minutes_per_order") or 5)
        if not 1 <= minutes <= 120:
            raise ValueError
    except ValueError:
        errors["minutes_per_order"] = "Enter a number of minutes between 1 and 120."
        minutes = 5

    units: dict[str, list[str]] = {}
    unit_rows: list[tuple[str, str]] = []
    i = 0
    while f"unit_{i}" in form:
        unit = (form.get(f"unit_{i}") or "").strip()
        words_text = form.get(f"words_{i}") or ""
        unit_rows.append((unit, words_text))
        words = [w.strip().lower() for w in words_text.split(",") if w.strip()]
        if unit:
            key = unit.upper() if unit.upper() in DEFAULT_UNIT_SYNONYMS or unit.isupper() else unit.lower()
            units[key] = sorted(set(words) | {unit.lower()})
        i += 1
    owner: dict[str, str] = {}
    for unit, words in units.items():
        for word in words:
            if word in owner and owner[word] != unit:
                errors["units"] = f"The word '{word}' is listed for both {owner[word]} and {unit}. Keep it in one place."
            owner[word] = unit

    checks = {key: form.get(key) == "on" for key, _ in RULE_CHECKBOXES}
    result.cutoff_time, result.min_order_value = cutoff, minimum_text
    result.duplicate_window_days, result.minutes_per_order = str(window), str(minutes)
    result.checks, result.allow_ai_ready = checks, form.get("allow_ai_ready") == "on"
    result.units = unit_rows
    if errors:
        result.errors = errors
        return result

    client.rules = {
        **DEFAULT_RULES, **(client.rules or {}),
        "cutoff_time": cutoff or None,
        "min_order_value": f"{minimum:.2f}" if minimum is not None else None,
        "flag_price_differences": checks["flag_price_differences"],
        "duplicate_window_days": window if checks["duplicate_check"] else 0,
        "convert_units": checks["convert_units"],
        "require_po_number": checks["require_po_number"],
        "allow_substitutions": checks["allow_substitutions"],
        "allow_ai_ready": result.allow_ai_ready,
    }
    client.unit_synonyms = units
    client.minutes_per_order = minutes
    importers.mark_setup_step(client, "rules")
    session.add(client)
    session.commit()
    session.refresh(client)
    saved = RulesForm.from_client(client)
    saved.notice = "Rules saved."
    return saved


def missing_steps(client: Client) -> list[str]:
    return [STEP_LABELS[s] for s in ("details", "catalogue", "customers", "output") if s not in client.setup_done]

