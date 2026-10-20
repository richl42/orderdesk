"""Reading spreadsheets and CSV files, and guessing what each column means.

Shared by client setup (catalogue, customer list, price list, output template,
staff entries) and later by order intake. Pure functions, no database or web code.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from charset_normalizer import from_bytes

from pipeline.normalise import basic

# ---------------------------------------------------------------- reading


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, str]]
    header_row: int = 0  # 0-based index of the header row in the file
    encoding: str | None = None
    delimiter: str | None = None

    def example(self, column: str) -> str:
        """The first non-empty value in a column, for showing beside the dropdown."""
        for row in self.rows:
            if row.get(column):
                return row[column]
        return ""


def detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # UTF-8 first, then Windows-1252 (what Excel on a UK Windows PC saves). Guessing comes
    # last because short files fool the detector.
    for encoding in ("utf-8", "cp1252"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    best = from_bytes(data).best()
    if best is not None and best.encoding:
        return best.encoding
    return "latin-1"


def detect_delimiter(text: str) -> str:
    """The delimiter that splits the first lines into the most consistent number of columns."""
    lines = [line for line in text.splitlines() if line.strip()][:30]
    best, best_score = ",", (0.0, 0)
    for delimiter in (",", ";", "\t", "|"):
        widths = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        if not widths:
            continue
        common = max(set(widths), key=widths.count)
        if common < 2:
            continue
        score = (widths.count(common) / len(widths), common)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        if value.is_integer():
            return str(int(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d") if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    return str(value).strip()


def _looks_numeric(text: str) -> bool:
    return parse_decimal(text) is not None


def find_header_row(grid: list[list[str]], look_ahead: int = 15) -> int:
    """The first row that looks like column headings: several filled cells, mostly words."""
    width = max((sum(1 for c in row if c) for row in grid[:look_ahead]), default=0)
    for i, row in enumerate(grid[:look_ahead]):
        filled = [c for c in row if c]
        if len(filled) < max(2, (width + 1) // 2):
            continue
        wordy = sum(1 for c in filled if not _looks_numeric(c))
        if wordy >= len(filled) * 0.75:
            return i
    return 0


def _unique_names(cells: list[str]) -> list[str]:
    names: list[str] = []
    for i, cell in enumerate(cells):
        name = cell or f"Column {i + 1}"
        base, n = name, 2
        while name in names:
            name = f"{base} ({n})"
            n += 1
        names.append(name)
    return names


def grid_to_table(grid: list[list[str]], **extra) -> Table:
    grid = [[_cell_text(c) for c in row] for row in grid]
    while grid and not any(grid[-1]):
        grid.pop()
    if not grid:
        return Table(columns=[], rows=[], **extra)
    header_at = find_header_row(grid)
    width = max(len(r) for r in grid)
    header = grid[header_at] + [""] * (width - len(grid[header_at]))
    # Drop trailing columns with no heading and no values.
    while width and not header[width - 1] and not any(len(r) >= width and r[width - 1] for r in grid[header_at + 1:]):
        width -= 1
    columns = _unique_names(header[:width])
    rows = []
    for raw in grid[header_at + 1:]:
        cells = (raw + [""] * width)[:width]
        if not any(cells):
            continue
        rows.append(dict(zip(columns, cells)))
    return Table(columns=columns, rows=rows, header_row=header_at, **extra)


def decode_csv(data: bytes) -> tuple[str, str, str]:
    """(text, encoding, delimiter) for the bytes of a CSV file."""
    encoding = detect_encoding(data)
    text = data.decode(encoding, errors="replace")
    return text, encoding, detect_delimiter(text)


def read_csv_text(path: Path) -> tuple[str, str, str]:
    return decode_csv(path.read_bytes())


EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def _frame_grid(frame: pd.DataFrame) -> list[list[str]]:
    return [[_cell_text(c) for c in row] for row in frame.where(frame.notna(), None).values.tolist()]


def read_sheets(data: bytes, filename: str) -> list[tuple[str, list[list[str]]]]:
    """Every sheet of an Excel file (or the one 'sheet' of a CSV file) as (sheet name, grid of text cells)."""
    if Path(filename).suffix.lower() in EXCEL_SUFFIXES:
        frames = pd.read_excel(io.BytesIO(data), header=None, dtype=object, engine="openpyxl", sheet_name=None)
        return [(name, _frame_grid(frame)) for name, frame in frames.items()]
    text, _encoding, delimiter = decode_csv(data)
    return [("", [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)])]


def read_table(path: Path) -> Table:
    """Read the first sheet of an Excel file, or a CSV file, into columns and rows of text."""
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        frame = pd.read_excel(path, header=None, dtype=object, engine="openpyxl")
        grid = frame.where(frame.notna(), None).values.tolist()
        return grid_to_table(grid)
    text, encoding, delimiter = read_csv_text(path)
    grid = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return grid_to_table(grid, encoding=encoding, delimiter=delimiter)


# ---------------------------------------------------------------- values


def parse_decimal(text: str | None) -> Decimal | None:
    """A number written in a cell: '£1,234.50', '28', '12.0'. None if it is not a number."""
    if text is None:
        return None
    cleaned = str(text).strip().replace("£", "").replace(",", "").replace(" ", "")
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_whole_number(text: str | None) -> int | None:
    value = parse_decimal(text)
    if value is None or value != value.to_integral_value() or value <= 0:
        return None
    return int(value)


# ---------------------------------------------------------------- guessing columns

IGNORE = "ignore"

# Internal fields per kind of file, with the words shown in the dropdown.
FIELDS: dict[str, dict[str, str]] = {
    "catalogue": {
        "product_code": "Product code",
        "description": "Description",
        "sell_unit": "Unit they sell in",
        "pack_size": "Pack size",
        "inner_unit": "Inner unit",
        "price": "Price",
        "barcode": "Barcode",
    },
    "customers": {
        "account_code": "Account code",
        "name": "Customer name",
        "emails": "Order email",
        "delivery_address": "Delivery address",
        "price_band": "Price band",
    },
    "prices": {
        "price_band": "Price band",
        "product_code": "Product code",
        "price": "Price",
    },
}

REQUIRED: dict[str, list[str]] = {
    "catalogue": ["product_code", "description"],
    "customers": ["account_code", "name"],
    "prices": ["price_band", "product_code", "price"],
}

# Header names (normalised, spaces removed) that mean each field.
HEADER_SYNONYMS: dict[str, dict[str, list[str]]] = {
    "catalogue": {
        "product_code": ["stockcode", "sku", "item", "itemcode", "productcode", "code", "partnumber",
                         "partno", "productref", "itemno", "stockref", "productno", "ref"],
        "description": ["description", "desc", "productdescription", "itemdescription", "productname",
                        "name", "title", "product", "itemname", "stockdescription"],
        "sell_unit": ["unit", "sellunit", "uom", "unitofmeasure", "sellingunit", "salesunit", "units",
                      "unitofsale", "saleunit"],
        "pack_size": ["packsize", "packqty", "packquantity", "qtyperpack", "unitspercase", "casesize",
                      "pack", "innerqty", "perpack", "qtypercase", "unitsperpack", "casequantity"],
        "inner_unit": ["innerunit", "inneruom", "baseunit", "unittype", "eachunit", "innerunittype"],
        "price": ["price", "listprice", "sellprice", "sellingprice", "unitprice", "tradeprice",
                  "pricea", "salesprice", "priceband1", "price1"],
        "barcode": ["barcode", "ean", "ean13", "gtin", "upc", "barcodeean"],
    },
    "customers": {
        "account_code": ["accountref", "accountcode", "accref", "account", "accountno", "accountnumber",
                         "customercode", "custcode", "customerref", "acref", "acc", "custref", "customerno"],
        "name": ["accountname", "customername", "name", "company", "companyname", "businessname", "customer"],
        "emails": ["email", "orderemail", "emails", "emailaddress", "contactemail", "orderemails",
                   "emailaddresses"],
        "delivery_address": ["deliveryaddress", "address", "shipto", "delivery", "siteaddress",
                             "shippingaddress", "deliverto"],
        "price_band": ["priceband", "band", "pricelist", "pricelevel", "pricingband", "pricecategory"],
    },
    "prices": {
        "price_band": ["priceband", "band", "pricelist", "pricelevel", "pricingband"],
        "product_code": ["stockcode", "sku", "productcode", "code", "itemcode", "item", "productref"],
        "price": ["price", "bandprice", "unitprice", "sellprice", "listprice"],
    },
}


def header_key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", basic(header))


@dataclass
class MappingGuess:
    mapping: dict[str, str]  # column -> field or "ignore"
    unsure: list[str] = field(default_factory=list)  # columns whose headers told us nothing


SELL_UNIT_WORDS = {"CASE", "CS", "BOX", "BX", "PACK", "PK", "EACH", "EA", "UNIT", "ROLL", "PAIR", "BAG",
                   "BOTTLE", "TUB", "DRUM", "SLEEVE", "OUTER", "TRAY", "KG", "LITRE", "L"}


def _value_kind(values: list[str]) -> str | None:
    """What a column's values look like: barcode, email, money, whole, unit, band, code, text."""
    values = [v for v in values if v][:50]
    if not values:
        return None

    def share(test) -> float:
        return sum(1 for v in values if test(v)) / len(values)

    if share(lambda v: "@" in v) >= 0.8:
        return "email"
    if share(lambda v: v.isdigit() and len(v) in (8, 12, 13, 14)) >= 0.9:
        return "barcode"
    if share(lambda v: v.upper() in SELL_UNIT_WORDS) >= 0.9:
        return "unit"
    if share(lambda v: parse_whole_number(v) is not None and len(v) <= 5) >= 0.9:
        return "whole"
    if share(lambda v: parse_decimal(v) is not None) >= 0.9:
        return "money"
    if len(set(values)) <= 6 and share(lambda v: len(v) <= 2) >= 0.9:
        return "band"
    if share(lambda v: " " not in v.strip() and len(v) <= 20 and any(c.isdigit() for c in v)) >= 0.9:
        return "code"
    if share(lambda v: len(v.split()) >= 2) >= 0.7:
        return "text"
    return None


# For columns whose header told us nothing: which field each value pattern suggests, best first.
VALUE_FIELDS: dict[str, dict[str, list[str]]] = {
    "catalogue": {
        "code": ["product_code"], "text": ["description"], "unit": ["sell_unit"], "whole": ["pack_size"],
        "money": ["price"], "barcode": ["barcode"],
    },
    "customers": {
        "code": ["account_code"], "email": ["emails"], "band": ["price_band"], "text": ["name", "delivery_address"],
    },
    "prices": {"band": ["price_band"], "code": ["product_code"], "money": ["price"]},
}


def guess_mapping(columns: list[str], kind: str, rows: list[dict[str, str]] | None = None) -> MappingGuess:
    """Guess each column's meaning from its header, then from its values. Each field is used at most once.

    Columns guessed from values alone are still listed as unsure, so the screen can ask for a check.
    """
    synonyms = HEADER_SYNONYMS[kind]
    mapping = {col: IGNORE for col in columns}
    taken: set[str] = set()
    for col in columns:
        key = header_key(col)
        for fld, names in synonyms.items():
            if fld not in taken and key in names:
                mapping[col] = fld
                taken.add(fld)
                break
    unsure = [col for col in columns if mapping[col] == IGNORE]

    if rows:
        for col in unsure:
            kind_of_values = _value_kind([r.get(col, "") for r in rows])
            for fld in VALUE_FIELDS[kind].get(kind_of_values or "", []):
                if fld not in taken:
                    mapping[col] = fld
                    taken.add(fld)
                    break
    return MappingGuess(mapping=mapping, unsure=unsure)


def duplicate_values(rows: list[dict[str, str]], column: str | None) -> list[str]:
    """Values that appear more than once in a column, in first-seen order."""
    if not column:
        return []
    seen: set[str] = set()
    dupes: list[str] = []
    for row in rows:
        value = (row.get(column) or "").strip()
        if not value:
            continue
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return dupes


def missing_required(mapping: dict[str, str], kind: str) -> list[str]:
    mapped = set(mapping.values())
    return [fld for fld in REQUIRED[kind] if fld not in mapped]


def column_for(mapping: dict[str, str], fld: str) -> str | None:
    for col, f in mapping.items():
        if f == fld:
            return col
    return None


# ---------------------------------------------------------------- output template

OUTPUT_FIELDS: dict[str, str] = {
    "account_code": "Account code",
    "po_number": "PO number",
    "date_received": "Date received",
    "product_code": "Product code",
    "quantity": "Quantity",
    "unit": "Unit",
    "price": "Price",
    "delivery_date": "Delivery date",
    "delivery_notes": "Delivery notes",
}

OUTPUT_HEADER_SYNONYMS: dict[str, list[str]] = {
    "account_code": ["accountref", "accountcode", "account", "accref", "customeraccount", "acref",
                     "customercode", "custcode", "accountno"],
    "po_number": ["custorderno", "customerorderno", "customerordernumber", "po", "ponumber", "pono",
                  "orderref", "yourref", "customerref", "purchaseorder", "custref", "purchaseorderno"],
    "date_received": ["orderdate", "date", "datereceived", "documentdate", "orderdt"],
    "product_code": ["stockcode", "sku", "productcode", "itemcode", "code", "item", "partno"],
    "quantity": ["qty", "quantity", "orderqty", "qtyordered", "quantityordered"],
    "unit": ["unit", "uom", "unitofsale", "sellunit"],
    "price": ["unitprice", "price", "sellprice", "netprice"],
    "delivery_date": ["deliverydate", "requireddate", "duedate", "despatchdate", "dateneeded", "reqdate"],
    "delivery_notes": ["notes", "deliverynotes", "comments", "instructions", "deliveryinstructions", "memo"],
}

DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%m/%d/%Y", "%Y%m%d"]


def _fits_date_format(values: list[str], fmt: str) -> bool:
    if not values:
        return False
    for v in values:
        try:
            datetime.strptime(v, fmt)
        except ValueError:
            return False
    return True


def detect_date_format(values: list[str]) -> str | None:
    values = [v for v in values if v]
    for fmt in DATE_FORMATS:  # UK day-first formats are tried before US month-first
        if _fits_date_format(values, fmt):
            return fmt
    return None


def _decimal_places(text: str) -> int | None:
    value = parse_decimal(text)
    if value is None or "." not in text:
        return None
    return len(text.strip().split(".")[-1])


@dataclass
class OutputTemplateGuess:
    columns: list[str]
    fill: dict[str, dict]
    date_format: str
    decimal_places: int
    delimiter: str
    encoding: str
    include_header: bool
    example_rows: list[list[str]]


def guess_output_template(path: Path) -> OutputTemplateGuess:
    """Read an example import file from the client's system and work out its format."""
    text, encoding, delimiter = read_csv_text(path)
    grid = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in row)]
    first = grid[0] if grid else []
    include_header = bool(first) and all(c.strip() and not _looks_numeric(c) and detect_date_format([c]) is None
                                         for c in first)
    columns = _unique_names([c.strip() for c in first]) if include_header else [f"Column {i + 1}" for i in range(len(first))]
    body = grid[1:] if include_header else grid

    fill: dict[str, dict] = {}
    taken: set[str] = set()
    for col in columns:
        key = header_key(col)
        fill[col] = {"blank": True}
        for fld, names in OUTPUT_HEADER_SYNONYMS.items():
            if fld not in taken and key in names:
                fill[col] = {"field": fld}
                taken.add(fld)
                break

    date_format = "%d/%m/%Y"
    places: list[int] = []
    for i, _ in enumerate(columns):
        values = [row[i].strip() for row in body if i < len(row) and row[i].strip()]
        fmt = detect_date_format(values)
        if fmt and not all(re.fullmatch(r"\d+", v) for v in values):
            date_format = fmt
        for v in values:
            dp = _decimal_places(v)
            if dp is not None:
                places.append(dp)

    return OutputTemplateGuess(
        columns=columns,
        fill=fill,
        date_format=date_format,
        decimal_places=max(places) if places else 2,
        delimiter=delimiter,
        encoding=encoding,
        include_header=include_header,
        example_rows=body[:5],
    )
