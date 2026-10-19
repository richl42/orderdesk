"""Database tables.

Every table except Client carries client_id, and every query filters by it.
Money and quantities are stored as exact decimal text, never floats.
Times are stored in UTC and shown in Europe/London time.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, Column, String, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DecimalText(TypeDecorator):
    """A Decimal stored as text, so pounds and pence never pass through a float."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(Decimal(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC datetime stored as ISO text."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetimes must be timezone-aware (UTC)")
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return datetime.fromisoformat(value).astimezone(timezone.utc)


def json_field(default_factory=list, **kwargs) -> Any:
    return Field(default_factory=default_factory, sa_column=Column(JSON, nullable=False), **kwargs)


def money_field(**kwargs) -> Any:
    return Field(default=None, sa_column=Column(DecimalText, nullable=True), **kwargs)


def utc_field(required: bool = False, **kwargs) -> Any:
    if required:
        return Field(default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False), **kwargs)
    return Field(default=None, sa_column=Column(UTCDateTime, nullable=True), **kwargs)


def client_fk() -> Any:
    return Field(foreign_key="client.id", index=True, ondelete="CASCADE")


class Client(SQLModel, table=True):
    id: str = Field(primary_key=True)  # short text id, e.g. "demo", "abc"
    name: str
    short_name: str  # used in export file names, e.g. "DemoCleaning"
    erp_system: str = ""
    mode: str = "trial"  # ClientMode
    contact_name: str = ""
    contact_email: str = ""
    inbound_address: Optional[str] = None
    trial_start_date: Optional[date] = None
    trial_working_days: int = 10
    minutes_per_order: int = 5
    rules: dict = json_field(dict)
    unit_synonyms: dict = json_field(dict)
    setup_done: list = json_field(list)  # names from SETUP_STEPS
    created_at: datetime = utc_field(required=True)
    archived_at: Optional[datetime] = utc_field()


class CatalogueItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "product_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    product_code: str
    description: str
    sell_unit: str = "EACH"
    pack_size: Optional[int] = None
    inner_unit: Optional[str] = None
    price: Optional[Decimal] = money_field()
    barcode: Optional[str] = Field(default=None, index=True)
    extra: dict = json_field(dict)
    search_text: str = ""


class PriceListEntry(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "price_band", "product_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    price_band: str
    product_code: str
    price: Optional[Decimal] = money_field()


class Customer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "account_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    account_code: str
    name: str
    emails: list = json_field(list)
    email_domains: list = json_field(list)
    delivery_address: str = ""
    price_band: Optional[str] = None
    extra: dict = json_field(dict)


class ColumnMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    kind: str  # MappingKind
    source_filename: str
    stored_path: str = ""
    source_columns: list = json_field(list)
    mapping: dict = json_field(dict)  # their column -> internal field (or "ignore")
    created_at: datetime = utc_field(required=True)


class OutputTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(foreign_key="client.id", unique=True, ondelete="CASCADE")
    source_filename: str = ""
    columns: list = json_field(list)  # their column names, in order
    # their column -> {"field": "<internal field>"} | {"fixed": "<value>"} | {"blank": true}
    fill: dict = json_field(dict)
    date_format: str = "%d/%m/%Y"
    decimal_places: int = 2
    delimiter: str = ","
    encoding: str = "utf-8"
    include_header: bool = True
    # Layout is always one row per order line, header fields repeated on every row.


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    source_type: str  # SourceType
    original_filename: Optional[str] = None
    stored_path: Optional[str] = None
    sender_email: Optional[str] = None
    subject: Optional[str] = None
    raw_text: str = ""
    content_hash: Optional[str] = Field(default=None, index=True)
    received_at: datetime = utc_field(required=True)
    processing_state: str = "reading"  # reading | done | failed

    classification: Optional[str] = None  # Classification
    classification_reason: Optional[str] = None
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", ondelete="SET NULL")
    customer_match_method: Optional[str] = None  # CustomerMatch
    po_number: Optional[str] = None
    requested_delivery_date: Optional[date] = None
    requested_delivery_raw: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_notes: Optional[str] = None

    initial_status: Optional[str] = None  # set once by the pipeline, never changed
    status: Optional[str] = Field(default=None, index=True)
    flags: list = json_field(list)  # [{code, message, severity}]

    ai_input_tokens: int = 0
    ai_output_tokens: int = 0
    ai_cost_gbp: Optional[Decimal] = money_field()
    processing_ms: Optional[int] = None

    review_started_at: Optional[datetime] = utc_field()
    approved_at: Optional[datetime] = utc_field()
    review_seconds: Optional[int] = None
    corrections_count: int = 0

    export_batch_id: Optional[int] = Field(default=None, foreign_key="exportbatch.id", ondelete="SET NULL")
    exported_at: Optional[datetime] = utc_field()


class OrderLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    order_id: int = Field(foreign_key="order.id", index=True, ondelete="CASCADE")
    line_no: int

    raw_text: str = ""
    source_span: Optional[str] = None
    requested_code: Optional[str] = None
    requested_description: Optional[str] = None
    requested_qty: Optional[str] = None  # as written
    requested_unit: Optional[str] = None  # as written
    requested_price: Optional[Decimal] = money_field()

    product_code: Optional[str] = None
    match_method: Optional[str] = None  # MatchMethod
    candidates: list = json_field(list)  # up to 5 {product_code, description, score}

    qty: Optional[Decimal] = money_field()  # final, in the client's sell unit
    unit: Optional[str] = None
    conversion_note: Optional[str] = None
    unit_price: Optional[Decimal] = money_field()
    status: Optional[str] = None
    flags: list = json_field(list)
    corrected_by_operator: bool = False


class Alias(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    # None means any customer of this client: offered as a candidate, never applied automatically.
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True, ondelete="CASCADE")
    phrase: str = Field(index=True)  # normalised
    product_code: str
    unit_hint: Optional[str] = None
    created_from_order_id: Optional[int] = None
    times_used: int = 0
    last_used_at: Optional[datetime] = utc_field()
    created_at: datetime = utc_field(required=True)


class ExportBatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    created_at: datetime = utc_field(required=True)
    file_name: str = ""
    file_path: str = ""
    order_ids: list = json_field(list)
    row_count: int = 0


class StaffEntryUpload(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    filename: str
    stored_path: str = ""
    uploaded_at: datetime = utc_field(required=True)
    mapping_id: Optional[int] = Field(default=None, foreign_key="columnmapping.id", ondelete="SET NULL")
    row_count: int = 0


class StaffEntryLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    upload_id: int = Field(foreign_key="staffentryupload.id", index=True, ondelete="CASCADE")
    po_number: Optional[str] = None
    account_code: Optional[str] = None
    order_date: Optional[date] = None
    product_code: Optional[str] = None
    qty: Optional[Decimal] = money_field()
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = money_field()
    delivery_date: Optional[date] = None


class Comparison(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = client_fk()
    upload_id: Optional[int] = Field(default=None, foreign_key="staffentryupload.id", ondelete="CASCADE")
    order_id: Optional[int] = Field(default=None, foreign_key="order.id", ondelete="SET NULL")
    staff_po_number: Optional[str] = None
    staff_account_code: Optional[str] = None
    result: str  # ComparisonResult
    differences: list = json_field(list)  # [{field, ours, theirs}]
    cause_note: str = ""
    fix_tag: Optional[str] = None  # FixTag
    created_at: datetime = utc_field(required=True)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = utc_field(required=True)
    client_id: str = client_fk()
    order_id: Optional[int] = Field(default=None, foreign_key="order.id", index=True, ondelete="SET NULL")
    type: str
    details: dict = json_field(dict)


# Every table that holds client data. Used by delete-client and the separation tests.
CLIENT_TABLES: list[type[SQLModel]] = [
    Event,
    Comparison,
    StaffEntryLine,
    StaffEntryUpload,
    OrderLine,
    Order,
    ExportBatch,
    Alias,
    OutputTemplate,
    ColumnMapping,
    Customer,
    PriceListEntry,
    CatalogueItem,
]
