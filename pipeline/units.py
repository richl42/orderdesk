"""Pipeline step 6: turn the quantity and unit a customer wrote into the client's sell unit.

Code only, never the AI. Anything that does not convert exactly is a Problem;
a missing unit is assumed to be the sell unit and marked Check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from pipeline.normalise import ROW_CELL, basic, is_table_row, singular
from pipeline.products import Item
from pipeline.tables import parse_decimal


@dataclass
class Conversion:
    qty: Decimal | None = None
    unit: str | None = None  # the client's sell unit
    note: str | None = None
    flags: list[dict] = field(default_factory=list)


def _flag(code: str, message: str, severity: str) -> dict:
    return {"code": code, "message": message, "severity": severity}


def plural(word: str, n: Decimal | int | None = None) -> str:
    word = word.lower()
    if word == "each" or (n is not None and Decimal(n) == 1):
        return word
    if word.endswith(("x", "s", "ch", "sh")):
        return word + "es"
    return word + "s"


def unit_lookup(synonyms: dict[str, list[str]]) -> dict[str, str]:
    """Every unit word (singular, lower case) -> its canonical unit: CASE, BOX... or an inner unit like bottle."""
    lookup: dict[str, str] = {}
    for canonical, words in (synonyms or {}).items():
        for word in [canonical, *words]:
            key = singular(basic(word))
            if key:
                lookup.setdefault(key, canonical)
    return lookup


def canonical_unit(written: str, lookup: dict[str, str]) -> str | None:
    words = [singular(w) for w in basic(written).split()]
    if not words:
        return None
    whole = " ".join(words)
    if whole in lookup:
        return lookup[whole]
    hits = {lookup[w] for w in words if w in lookup}
    return hits.pop() if len(hits) == 1 else None


def _whole(value: Decimal) -> bool:
    return value == value.to_integral_value()


def convert(qty_text: str | None, unit_text: str | None, item: Item, synonyms: dict[str, list[str]],
            unit_hint: str | None = None, convert_units: bool = True) -> Conversion:
    sell = item.sell_unit.upper()
    if not qty_text:
        return Conversion(flags=[_flag("no_quantity", "No quantity written", "problem")])
    qty = parse_decimal(qty_text)
    if qty is None:
        return Conversion(flags=[_flag("quantity_unclear", f"Quantity '{qty_text}' not understood", "problem")])
    if qty <= 0:
        return Conversion(flags=[_flag("quantity_unclear", f"Quantity {qty_text} is not more than zero", "problem")])

    lookup = unit_lookup(synonyms)
    flags: list[dict] = []
    if unit_text:
        unit = canonical_unit(unit_text, lookup)
        if unit is None:
            return Conversion(flags=[_flag("unit_unknown", f"Unit '{unit_text}' not recognised", "problem")])
        written = unit_text
    elif unit_hint:
        unit, written = unit_hint, unit_hint.lower()
    else:
        unit, written = sell, sell.lower()
        flags.append(_flag("no_unit", f"No unit written, so {sell.lower()} was assumed", "check"))

    if unit.upper() == sell:
        if not _whole(qty):
            return Conversion(flags=[_flag("quantity_unclear", f"{qty_text} {plural(sell, qty)} is not a whole "
                                                               f"number", "problem")])
        return Conversion(qty=qty, unit=sell, flags=flags)

    inner = (item.inner_unit or "").lower()
    if inner and unit.lower() == inner:
        if not convert_units:
            return Conversion(flags=[_flag("conversion", f"Ordered in {plural(inner)}, and converting units is "
                                                         f"switched off for this client", "problem")])
        if not item.pack_size:
            return Conversion(flags=[_flag("conversion", f"{item.code} has no pack size, so {plural(inner)} can't be "
                                                         f"turned into {plural(sell)}", "problem")])
        if not _whole(qty) or int(qty) % item.pack_size:
            return Conversion(flags=[_flag("conversion", f"{qty_text} {plural(inner, qty)} is not a whole number of "
                                                         f"{plural(sell)} of {item.pack_size}", "problem")])
        converted = Decimal(int(qty) // item.pack_size)
        note = f"{qty_text} {plural(inner, qty)} / {item.pack_size} = {converted} {plural(sell, converted)}"
        return Conversion(qty=converted, unit=sell, note=note, flags=flags)

    return Conversion(flags=[_flag("unit_mismatch", f"Ordered in {plural(written)}, but {item.code} is sold per "
                                                    f"{sell.lower()}", "problem")])


QTY_LABELS = {"qty", "quantity", "qtyordered", "orderqty", "quantityordered", "amount"}
QTY_LEAD_WORDS = {"x", "qty", "quantity", "of"}


def quantity_plausible(span: str, qty_text: str | None, unit_words: set[str]) -> bool:
    """Whether the quantity read for a line sits where a quantity would.

    A number inside the product name ("200 sheet", "750ml") must not pass as the quantity.
    In a table row, a quantity column must hold that quantity. In free text, the number must
    be next to a unit word, after "x", or at the start or end of the line.
    """
    if not qty_text or not span:
        return True
    qty_key = basic(qty_text)
    if is_table_row(span):
        for cell in span.split(" | "):
            label, value = ROW_CELL.match(cell).groups()
            if re.sub(r"[^a-z]", "", label.lower()) in QTY_LABELS:
                return basic(value) == qty_key
        return True
    tokens = basic(span).split()
    units = {singular(basic(w)) for w in unit_words}
    for i, token in enumerate(tokens):
        if token != qty_key:
            continue
        before = tokens[i - 1] if i > 0 else None
        after = tokens[i + 1] if i + 1 < len(tokens) else None
        if before is None or after is None or before in QTY_LEAD_WORDS or after == "x":
            return True
        if singular(after) in units or singular(before) in units:
            return True
    return False
