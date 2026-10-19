"""Shared words: order statuses, classifications, match methods and so on.

Stored values are the lower-case codes; the interface shows the labels.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    READY = "ready"
    CHECK = "check"
    PROBLEM = "problem"
    PASS_ON = "pass_on"
    APPROVED = "approved"
    EXPORTED = "exported"


STATUS_LABELS = {
    Status.READY: "Ready",
    Status.CHECK: "Check",
    Status.PROBLEM: "Problem",
    Status.PASS_ON: "Pass on",
    Status.APPROVED: "Approved",
    Status.EXPORTED: "Exported",
}

# Worst first. An order's status is the worst of its parts.
SEVERITY_ORDER = [Status.PROBLEM, Status.CHECK, Status.READY]


class Classification(StrEnum):
    NEW_ORDER = "new_order"
    AMENDMENT = "amendment"
    CANCELLATION = "cancellation"
    CHASE = "chase"
    QUOTE_REQUEST = "quote_request"
    OTHER = "other"


CLASSIFICATION_LABELS = {
    Classification.NEW_ORDER: "New order",
    Classification.AMENDMENT: "Amendment to an earlier order",
    Classification.CANCELLATION: "Cancellation",
    Classification.CHASE: "Delivery chase",
    Classification.QUOTE_REQUEST: "Price or quote question",
    Classification.OTHER: "Not an order",
}


class SourceType(StrEnum):
    PASTE = "paste"
    TXT = "txt"
    EML = "eml"
    PDF = "pdf"
    XLSX = "xlsx"
    CSV = "csv"
    EMAIL_FORWARD = "email_forward"


SOURCE_LABELS = {
    SourceType.PASTE: "Pasted email",
    SourceType.TXT: "Text file",
    SourceType.EML: "Email",
    SourceType.PDF: "PDF",
    SourceType.XLSX: "Excel",
    SourceType.CSV: "CSV",
    SourceType.EMAIL_FORWARD: "Forwarded email",
}


class CustomerMatch(StrEnum):
    EMAIL = "email"
    DOMAIN = "domain"
    NAME = "name"
    OPERATOR = "operator"


class MatchMethod(StrEnum):
    ALIAS = "alias"
    EXACT_CODE = "exact_code"
    EXACT_NAME = "exact_name"
    AI_SUGGESTED = "ai_suggested"
    OPERATOR = "operator"


MATCH_METHOD_LABELS = {
    MatchMethod.ALIAS: "Remembered phrasing",
    MatchMethod.EXACT_CODE: "Exact code match",
    MatchMethod.EXACT_NAME: "Exact name match",
    MatchMethod.AI_SUGGESTED: "Suggested match",
    MatchMethod.OPERATOR: "Chosen by you",
}


class ClientMode(StrEnum):
    DEMO = "demo"
    TRIAL = "trial"
    LIVE = "live"


class MappingKind(StrEnum):
    CATALOGUE = "catalogue"
    CUSTOMERS = "customers"
    PRICES = "prices"
    OUTPUT = "output"
    STAFF_ENTRIES = "staff_entries"


class ComparisonResult(StrEnum):
    EXACT = "exact"
    DIFFERENT = "different"
    ONLY_OURS = "only_ours"
    ONLY_THEIRS = "only_theirs"


class FixTag(StrEnum):
    RULE_ADDED = "rule_added"
    REMEMBERED = "remembered"
    ASK_CLIENT = "ask_client"
    OUR_ERROR = "our_error"
    STAFF_ERROR = "staff_error"


FIX_TAG_LABELS = {
    FixTag.RULE_ADDED: "Rule added",
    FixTag.REMEMBERED: "Remembered",
    FixTag.ASK_CLIENT: "Ask client",
    FixTag.OUR_ERROR: "Our error",
    FixTag.STAFF_ERROR: "Staff error",
}

DEFAULT_RULES = {
    "cutoff_time": "15:00",
    "min_order_value": None,
    "flag_price_differences": True,
    "price_tolerance": "0.01",
    "duplicate_window_days": 7,
    "convert_units": True,
    "allow_substitutions": False,
    "require_po_number": False,
    "allow_ai_ready": False,
}

# Synonyms for the client's sell units. The inner unit words found in the
# catalogue (bottle, roll, pair...) are added per client at setup.
DEFAULT_UNIT_SYNONYMS = {
    "CASE": ["case", "cases", "cs", "cse"],
    "BOX": ["box", "boxes", "bx"],
    "PACK": ["pack", "packs", "pk", "pkt", "pkts"],
    "EACH": ["each", "ea", "single", "singles", "unit", "units"],
}

SETUP_STEPS = ["details", "catalogue", "customers", "output", "rules"]
