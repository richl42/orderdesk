"""Load a client's setup files into the database.

Used by the setup screen and by `make seed`, so the demo client is set up
exactly the way a real client is.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, delete, select

from app.db import client_files_dir
from app.models import (
    CatalogueItem,
    Client,
    ColumnMapping,
    Customer,
    OutputTemplate,
    PriceListEntry,
)
from pipeline import normalise
from pipeline.tables import (
    Table,
    column_for,
    guess_output_template,
    missing_required,
    parse_decimal,
    parse_whole_number,
)
from pipeline.vocab import DEFAULT_UNIT_SYNONYMS, MappingKind

# Email domains shared by many unrelated people, never used to recognise a customer.
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com",
    "live.co.uk", "msn.com", "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "mac.com",
    "aol.com", "aol.co.uk", "btinternet.com", "btopenworld.com", "sky.com", "virginmedia.com",
    "ntlworld.com", "talktalk.net", "tiscali.co.uk", "protonmail.com", "proton.me", "mail.com",
    "gmx.com", "gmx.co.uk", "zoho.com", "fastmail.com",
}


class MappingIncomplete(ValueError):
    def __init__(self, missing: list[str]):
        super().__init__(f"Columns not chosen yet: {', '.join(missing)}")
        self.missing = missing


@dataclass
class ImportResult:
    count: int = 0
    duplicates: list[str] = field(default_factory=list)  # codes that appeared more than once
    skipped_rows: list[int] = field(default_factory=list)  # 1-based row numbers with no code
    warnings: list[str] = field(default_factory=list)


def store_setup_file(client_id: str, source: Path, kind: str) -> Path:
    """Keep the uploaded file exactly as received."""
    folder = client_files_dir(client_id) / "setup"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{kind}_{source.name}"
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return target


def save_mapping(session: Session, client_id: str, kind: str, table: Table, mapping: dict[str, str],
                 filename: str, stored_path: Path) -> ColumnMapping:
    session.exec(delete(ColumnMapping).where(ColumnMapping.client_id == client_id, ColumnMapping.kind == kind))
    record = ColumnMapping(
        client_id=client_id,
        kind=kind,
        source_filename=filename,
        stored_path=str(stored_path),
        source_columns=list(table.columns),
        mapping=dict(mapping),
    )
    session.add(record)
    return record


def _value(row: dict[str, str], mapping: dict[str, str], fld: str) -> str:
    col = column_for(mapping, fld)
    return (row.get(col) or "").strip() if col else ""


def _extra(row: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    return {col: val for col, val in row.items() if mapping.get(col, "ignore") == "ignore" and val}


def mark_setup_step(client: Client, step: str) -> None:
    if step not in client.setup_done:
        client.setup_done = [*client.setup_done, step]


# ---------------------------------------------------------------- catalogue


def import_catalogue(session: Session, client: Client, path: Path, table: Table, mapping: dict[str, str],
                     filename: str | None = None) -> ImportResult:
    missing = missing_required(mapping, "catalogue")
    if missing:
        raise MappingIncomplete(missing)

    result = ImportResult()
    items: dict[str, CatalogueItem] = {}
    inner_units: set[str] = set()
    for row_no, row in enumerate(table.rows, start=1):
        code = _value(row, mapping, "product_code")
        description = _value(row, mapping, "description")
        if not code:
            if description:
                result.skipped_rows.append(row_no)
            continue
        if code in items:
            if code not in result.duplicates:
                result.duplicates.append(code)
            continue

        pack_text = _value(row, mapping, "pack_size")
        pack_size = parse_whole_number(pack_text)
        if pack_text and pack_size is None:
            result.warnings.append(f"{code}: pack size '{pack_text}' is not a whole number, left blank")

        inner = normalise.singular(_value(row, mapping, "inner_unit").lower()) or None
        if inner:
            inner_units.add(inner)

        price_text = _value(row, mapping, "price")
        price = parse_decimal(price_text)
        if price_text and price is None:
            result.warnings.append(f"{code}: price '{price_text}' is not a number, left blank")

        barcode = "".join(ch for ch in _value(row, mapping, "barcode") if ch.isdigit()) or None
        items[code] = CatalogueItem(
            client_id=client.id,
            product_code=code,
            description=description,
            sell_unit=(_value(row, mapping, "sell_unit") or "EACH").upper(),
            pack_size=pack_size,
            inner_unit=inner,
            price=price,
            barcode=barcode,
            extra=_extra(row, mapping),
            search_text=normalise.search_text(description, code, barcode),
        )

    session.exec(delete(CatalogueItem).where(CatalogueItem.client_id == client.id))
    session.add_all(items.values())
    stored = store_setup_file(client.id, path, MappingKind.CATALOGUE)
    save_mapping(session, client.id, MappingKind.CATALOGUE, table, mapping, filename or path.name, stored)

    synonyms = dict(client.unit_synonyms or DEFAULT_UNIT_SYNONYMS)
    for sell_unit in {item.sell_unit for item in items.values()}:
        synonyms.setdefault(sell_unit, [sell_unit.lower()])
    for inner in sorted(inner_units):
        synonyms.setdefault(inner, sorted({inner, inner + "s"}))
    client.unit_synonyms = synonyms
    mark_setup_step(client, "catalogue")
    session.add(client)
    session.commit()

    result.count = len(items)
    return result


# ---------------------------------------------------------------- customers


def import_customers(session: Session, client: Client, path: Path, table: Table, mapping: dict[str, str],
                     filename: str | None = None) -> ImportResult:
    missing = missing_required(mapping, "customers")
    if missing:
        raise MappingIncomplete(missing)

    result = ImportResult()
    customers: dict[str, Customer] = {}
    for row_no, row in enumerate(table.rows, start=1):
        code = _value(row, mapping, "account_code")
        name = _value(row, mapping, "name")
        if not code:
            if name:
                result.skipped_rows.append(row_no)
            continue
        if code in customers:
            if code not in result.duplicates:
                result.duplicates.append(code)
            continue
        emails = normalise.split_emails(_value(row, mapping, "emails"))
        domains = []
        for email in emails:
            domain = normalise.email_domain(email)
            if domain and domain not in PUBLIC_EMAIL_DOMAINS and domain not in domains:
                domains.append(domain)
        customers[code] = Customer(
            client_id=client.id,
            account_code=code,
            name=name,
            emails=emails,
            email_domains=domains,
            delivery_address=_value(row, mapping, "delivery_address"),
            price_band=_value(row, mapping, "price_band") or None,
            extra=_extra(row, mapping),
        )

    session.exec(delete(Customer).where(Customer.client_id == client.id))
    session.add_all(customers.values())
    stored = store_setup_file(client.id, path, MappingKind.CUSTOMERS)
    save_mapping(session, client.id, MappingKind.CUSTOMERS, table, mapping, filename or path.name, stored)
    mark_setup_step(client, "customers")
    session.add(client)
    session.commit()

    result.count = len(customers)
    return result


# ---------------------------------------------------------------- price list by band


def import_prices(session: Session, client: Client, path: Path, table: Table, mapping: dict[str, str],
                  filename: str | None = None) -> ImportResult:
    missing = missing_required(mapping, "prices")
    if missing:
        raise MappingIncomplete(missing)

    result = ImportResult()
    entries: dict[tuple[str, str], PriceListEntry] = {}
    for row in table.rows:
        band = _value(row, mapping, "price_band")
        code = _value(row, mapping, "product_code")
        price = parse_decimal(_value(row, mapping, "price"))
        if not band or not code:
            continue
        if price is None:
            result.warnings.append(f"{code} band {band}: price is not a number, skipped")
            continue
        key = (band, code)
        if key in entries:
            result.duplicates.append(f"{code} band {band}")
            continue
        entries[key] = PriceListEntry(client_id=client.id, price_band=band, product_code=code, price=price)

    session.exec(delete(PriceListEntry).where(PriceListEntry.client_id == client.id))
    session.add_all(entries.values())
    stored = store_setup_file(client.id, path, MappingKind.PRICES)
    save_mapping(session, client.id, MappingKind.PRICES, table, mapping, filename or path.name, stored)
    session.commit()

    result.count = len(entries)
    return result


# ---------------------------------------------------------------- output template


def import_output_template(session: Session, client: Client, path: Path, filename: str | None = None,
                           overrides: dict | None = None) -> OutputTemplate:
    guess = guess_output_template(path)
    values = {
        "columns": guess.columns,
        "fill": guess.fill,
        "date_format": guess.date_format,
        "decimal_places": guess.decimal_places,
        "delimiter": guess.delimiter,
        "encoding": guess.encoding,
        "include_header": guess.include_header,
    }
    values.update(overrides or {})

    store_setup_file(client.id, path, MappingKind.OUTPUT)
    template = session.exec(select(OutputTemplate).where(OutputTemplate.client_id == client.id)).first()
    if template is None:
        template = OutputTemplate(client_id=client.id)
    template.source_filename = filename or path.name
    for key, value in values.items():
        setattr(template, key, value)
    session.add(template)
    mark_setup_step(client, "output")
    session.add(client)
    session.commit()
    session.refresh(template)
    return template
