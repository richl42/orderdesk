"""Text normalisation used for matching products, aliases and customer names.

Pure functions, no database or web code.
"""

from __future__ import annotations

import re
import unicodedata

# Words that carry no meaning about which product is wanted.
FILLER_WORDS = {"of", "the", "a", "an", "x", "please", "pls", "plz"}

_PUNCT = re.compile(r"[^\w\s-]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")
_LOOSE_HYPHEN = re.compile(r"(?<!\w)-|-(?!\w)")
_STANDALONE_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_NOT_PLURAL_ENDINGS = ("ss", "us", "is", "ous")


def basic(text: str) -> str:
    """Lower case, accents and fancy characters folded, punctuation turned into spaces.

    Hyphens inside words and codes are kept ("tow-310", "2-ply"); other punctuation goes.
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("×", " x ").replace("’", "'").lower()
    text = text.replace("'", "")
    text = _PUNCT.sub(" ", text)
    text = _LOOSE_HYPHEN.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def singular(word: str) -> str:
    """Strip a plural ending from one word. Conservative: leaves anything unclear alone."""
    if len(word) <= 3 or any(ch.isdigit() for ch in word):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("xes", "ches", "shes", "sses", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(_NOT_PLURAL_ENDINGS):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    return [singular(w) for w in basic(text).split()]


def description_key(text: str) -> str:
    """Key for exact-name matching: normalised words, plurals stripped, fillers removed."""
    return " ".join(w for w in words(text) if w not in FILLER_WORDS)


def code_key(code: str | None) -> str:
    """Key for exact-code matching: upper case, spaces removed."""
    return re.sub(r"\s+", "", (code or "")).upper()


def search_text(description: str, code: str = "", barcode: str | None = None) -> str:
    """The text fuzzy matching searches over for one catalogue item."""
    parts = [description_key(description), basic(code)]
    if barcode:
        parts.append(barcode.strip())
    return " ".join(p for p in parts if p)


def alias_phrase(
    line_text: str,
    qty: str | None = None,
    unit: str | None = None,
    unit_words: set[str] | None = None,
) -> str:
    """The normalised form of how a customer wrote a line, used to save and look up aliases.

    Removes the quantity and the unit word, keeps digits that are part of words or
    codes ("2ply", "750ml", "RH-0042"), strips plurals and filler words.

    Without qty, the first standalone number is treated as the quantity. Without unit,
    a word straight after the quantity is removed if it is a known unit word.
    """
    tokens = basic(line_text).split()
    unit_words = {singular(u) for u in (unit_words or set())}

    qty_key = basic(qty) if qty else None
    qty_index = None
    for i, tok in enumerate(tokens):
        if (qty_key and tok == qty_key) or (not qty_key and _STANDALONE_NUMBER.match(tok)):
            qty_index = i
            break
    if qty_index is not None:
        tokens.pop(qty_index)

    unit_key = singular(basic(unit)) if unit else None
    for i, tok in enumerate(tokens):
        word = singular(tok)
        if unit_key and word == unit_key:
            tokens.pop(i)
            break
        if not unit_key and qty_index is not None and i == qty_index and word in unit_words:
            tokens.pop(i)
            break

    kept = [singular(t) for t in tokens]
    return " ".join(t for t in kept if t not in FILLER_WORDS)


def email_domain(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1] if "@" in email else ""


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def split_emails(cell: str | None) -> list[str]:
    """All email addresses in one cell, lower case, in order, without repeats."""
    found: list[str] = []
    for email in EMAIL_PATTERN.findall(cell or ""):
        email = email.lower().strip(".")
        if email not in found:
            found.append(email)
    return found


# ---------------------------------------------------------------- order lines and catalogue names

# Columns in a spreadsheet row that never describe the product: quantities, prices, dates, references, notes.
_NOT_PRODUCT_LABELS = ("price", "total", "amount", "value", "vat", "date", "note", "ref", "po", "order", "qty",
                       "quantity", "unit", "uom", "each", "deliver", "comment", "line", "customer", "account")
ROW_CELL = re.compile(r"^\s*([^:|]{1,40}):\s*(.*?)\s*$")
_PACK_PHRASE = re.compile(
    r"^(?:(?:case|box|pack|bag|tub|sleeve|outer|tray|carton|pk|bundle)s?\s+of\s+\d+.*"
    r"|single(?:\s+\w+)?|\d+\s*(?:pack|pk|per\s+case|per\s+pack)|(?:each|single roll))$",
    flags=re.IGNORECASE,
)


def _label_is_product(label: str) -> bool:
    key = re.sub(r"[^a-z]", "", label.lower())
    if key in {"code", "stockcode", "productcode", "itemcode", "sku", "item", "product", "description",
               "productdescription", "itemdescription", "name", "productname", "barcode", "ourcode", "theircode"}:
        return True
    return not any(word in key for word in _NOT_PRODUCT_LABELS)


def is_table_row(text: str) -> bool:
    """A line written from a spreadsheet or PDF table: 'Code: CLN-101 | Description: ... | Qty: 2'."""
    cells = text.split(" | ")
    return len(cells) >= 2 and all(ROW_CELL.match(c) for c in cells)


def line_text(span: str, qty: str | None = None, unit: str | None = None, price: str | None = None) -> str:
    """The words of an order line that describe the product.

    Free text is used as written. For a table row the column labels are dropped, and so are cells
    holding the quantity, unit, price, totals, dates, references or notes.
    """
    span = span or ""
    if not is_table_row(span):
        return span
    skip = {basic(v) for v in (qty, unit, price) if v}
    kept = []
    for cell in span.split(" | "):
        label, value = ROW_CELL.match(cell).groups()
        if not value or basic(value) in skip or not _label_is_product(label):
            continue
        kept.append(value)
    return " ".join(kept)


def strip_pack(description: str) -> str:
    """A catalogue description without its pack wording: 'Blue roll 2ply, pack of 6' -> 'Blue roll 2ply'."""
    parts = [p.strip() for p in (description or "").split(",")]
    while len(parts) > 1 and _PACK_PHRASE.match(parts[-1]):
        parts.pop()
    return ", ".join(parts)


def name_keys(description: str) -> set[str]:
    """Keys a customer's wording can equal for an exact-name match: with and without the pack wording."""
    return {k for k in (description_key(description), description_key(strip_pack(description))) if k}
