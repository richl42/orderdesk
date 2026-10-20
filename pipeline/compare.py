"""Shadow trial comparison: our approved orders against what the client's own staff entered.

Pure functions. Orders are paired by PO number and account code (and by order
date when the same PO appears more than once); orders with no PO are paired by
account code and order date, only when exactly one matches on each side. Nothing
unpaired is dropped: it is listed as only ours or only theirs. Within a pair,
lines are paired by product code; quantities are compared in the sell unit and
prices within a penny.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pipeline.normalise import code_key
from pipeline.units import plural
from pipeline.vocab import ComparisonResult, FixTag, Status

PRICE_TOLERANCE = Decimal("0.01")


@dataclass
class CompLine:
    product_code: str | None
    qty: Decimal | None  # in the sell unit
    unit: str | None
    unit_price: Decimal | None


@dataclass
class OurOrder:
    order_id: int
    account_code: str | None
    po_number: str | None
    order_date: date  # the London day it arrived
    delivery_date: date | None
    lines: list[CompLine]
    initial_status: str | None = None
    first_pass: dict | None = None  # the pipeline's own output before corrections


@dataclass
class TheirOrder:
    po_number: str | None
    account_code: str | None
    order_date: date | None
    delivery_date: date | None
    lines: list[CompLine]

    @property
    def key(self) -> str:
        return f"{self.account_code or ''}|{self.po_number or ''}|{self.order_date.isoformat() if self.order_date else ''}"


@dataclass
class Pairing:
    result: str  # ComparisonResult
    ours: OurOrder | None
    theirs: TheirOrder | None
    differences: list[dict] = field(default_factory=list)  # {field, ours, theirs, line?}


# ---------------------------------------------------------------- grouping the staff file


@dataclass
class StaffRow:
    po_number: str | None
    account_code: str | None
    order_date: date | None
    product_code: str | None
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    delivery_date: date | None


def group_staff_rows(rows: list[StaffRow], to_sell_unit: Callable[[str | None, Decimal | None, str | None],
                                                                     tuple[Decimal | None, str | None]]
                     ) -> list[TheirOrder]:
    """One staff order per account, PO and order date. Quantities are turned into the sell unit."""
    grouped: dict[tuple, TheirOrder] = {}
    for row in rows:
        key = (code_key(row.account_code), code_key(row.po_number), row.order_date)
        order = grouped.get(key)
        if order is None:
            order = grouped[key] = TheirOrder(row.po_number, row.account_code, row.order_date, None, [])
        if row.delivery_date and order.delivery_date is None:
            order.delivery_date = row.delivery_date
        qty, unit = to_sell_unit(row.product_code, row.qty, row.unit)
        order.lines.append(CompLine(row.product_code, qty, unit, row.unit_price))
    return list(grouped.values())


# ---------------------------------------------------------------- pairing orders


def pair_orders(ours: list[OurOrder], theirs: list[TheirOrder]) -> list[Pairing]:
    pairs: list[Pairing] = []
    left_ours, left_theirs = list(ours), list(theirs)

    def take(o: OurOrder, t: TheirOrder) -> None:
        left_ours.remove(o)
        left_theirs.remove(t)
        pairs.append(Pairing(ComparisonResult.EXACT, o, t))

    # 1. PO number and account code; the order date settles a PO that appears more than once.
    by_po: dict[tuple, list[TheirOrder]] = defaultdict(list)
    for t in theirs:
        if t.po_number:
            by_po[(code_key(t.account_code), code_key(t.po_number))].append(t)
    for o in sorted(ours, key=lambda x: (x.order_date, x.order_id)):
        if not o.po_number:
            continue
        options = [t for t in by_po.get((code_key(o.account_code), code_key(o.po_number)), []) if t in left_theirs]
        if len(options) > 1:
            options = [t for t in options if t.order_date == o.order_date] or options
        if options:
            take(o, options[0])

    # 2. No PO number: account code and order date, only when exactly one matches on each side.
    for o in [o for o in left_ours if not o.po_number]:
        same = [t for t in left_theirs if not t.po_number and code_key(t.account_code) == code_key(o.account_code)
                and t.order_date == o.order_date]
        rivals = [x for x in left_ours if not x.po_number and code_key(x.account_code) == code_key(o.account_code)
                  and x.order_date == o.order_date]
        if len(same) == 1 and len(rivals) == 1:
            take(o, same[0])

    for p in pairs:
        p.differences = compare_pair(p.ours, p.theirs)
        p.result = ComparisonResult.DIFFERENT if p.differences else ComparisonResult.EXACT
    pairs += [Pairing(ComparisonResult.ONLY_OURS, o, None, [{"field": "order", "ours": describe(o.lines),
                                                             "theirs": "Not entered by their team"}])
              for o in left_ours]
    pairs += [Pairing(ComparisonResult.ONLY_THEIRS, None, t, [{"field": "order", "ours": "Not in our orders",
                                                               "theirs": describe(t.lines)}])
              for t in left_theirs]
    return pairs


# ---------------------------------------------------------------- comparing a pair


def _fmt_qty(qty: Decimal | None, unit: str | None) -> str:
    if qty is None:
        return "no quantity"
    text = str(int(qty)) if qty == qty.to_integral_value() else format(qty.normalize(), "f")
    return f"{text} {plural(unit, qty) if unit else ''}".strip()


def describe_line(line: CompLine) -> str:
    return f"{line.product_code or 'no product'} × {_fmt_qty(line.qty, line.unit)}"


def describe(lines: list[CompLine]) -> str:
    return ", ".join(describe_line(ln) for ln in lines) or "no lines"


def _same_qty(a: CompLine, b: CompLine) -> bool:
    if a.qty is None or b.qty is None:
        return a.qty is None and b.qty is None
    return a.qty == b.qty and (not a.unit or not b.unit or a.unit.upper() == b.unit.upper())


def _same_price(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return True  # only compared when both sides have a price
    return abs(a - b) <= PRICE_TOLERANCE


def _merged(lines: list[CompLine]) -> dict[str, CompLine]:
    """Lines by product code; the same product twice is added together."""
    out: dict[str, CompLine] = {}
    for line in lines:
        key = code_key(line.product_code)
        if key in out and out[key].qty is not None and line.qty is not None:
            out[key] = CompLine(out[key].product_code, out[key].qty + line.qty, out[key].unit, out[key].unit_price)
        elif key not in out:
            out[key] = line
    return out


def compare_lines(ours: list[CompLine], theirs: list[CompLine]) -> list[dict]:
    diffs: list[dict] = []
    mine, staff = _merged(ours), _merged(theirs)
    for key in [k for k in mine if k in staff]:
        a, b = mine[key], staff[key]
        if not _same_qty(a, b):
            diffs.append({"field": "quantity", "line": a.product_code, "ours": _fmt_qty(a.qty, a.unit),
                          "theirs": _fmt_qty(b.qty, b.unit)})
        if not _same_price(a.unit_price, b.unit_price):
            diffs.append({"field": "price", "line": a.product_code, "ours": f"£{a.unit_price:.2f}",
                          "theirs": f"£{b.unit_price:.2f}"})
    only_mine = [mine[k] for k in mine if k not in staff]
    only_staff = [staff[k] for k in staff if k not in mine]
    # A product on one side only, facing another on the other side only, is one product difference.
    while only_mine and only_staff:
        a, b = only_mine.pop(0), only_staff.pop(0)
        diffs.append({"field": "product", "line": a.product_code, "ours": describe_line(a), "theirs": describe_line(b)})
    for a in only_mine:
        diffs.append({"field": "line", "line": a.product_code, "ours": describe_line(a), "theirs": "Not entered"})
    for b in only_staff:
        diffs.append({"field": "line", "line": b.product_code, "ours": "Not in our order", "theirs": describe_line(b)})
    return diffs


def compare_pair(ours: OurOrder, theirs: TheirOrder) -> list[dict]:
    diffs = []
    if ours.delivery_date and theirs.delivery_date and ours.delivery_date != theirs.delivery_date:
        diffs.append({"field": "delivery date", "ours": f"{ours.delivery_date:%d/%m/%Y}",
                      "theirs": f"{theirs.delivery_date:%d/%m/%Y}"})
    return diffs + compare_lines(ours.lines, theirs.lines)


def first_pass_exact(ours: OurOrder, theirs: TheirOrder) -> bool | None:
    """Whether the pipeline's own output, before any correction, matched the staff entry. None if not known."""
    fp = ours.first_pass
    if not fp:
        return None
    if code_key(fp.get("account_code")) != code_key(theirs.account_code):
        return False
    delivery = date.fromisoformat(fp["delivery_date"]) if fp.get("delivery_date") else None
    first = OurOrder(ours.order_id, fp.get("account_code"), fp.get("po_number"), ours.order_date, delivery, [
        CompLine(ln.get("product_code"), Decimal(ln["qty"]) if ln.get("qty") is not None else None, ln.get("unit"),
                 Decimal(ln["unit_price"]) if ln.get("unit_price") is not None else None)
        for ln in fp.get("lines") or []])
    return not compare_pair(first, theirs)


# ---------------------------------------------------------------- the measures


def counts_against_us(diff: dict) -> bool:
    """A difference tagged Staff error does not count against exactly right or confidently wrong."""
    return diff.get("tag") != FixTag.STAFF_ERROR


@dataclass
class Measures:
    compared: int
    exactly_right: int
    confidently_wrong: int
    flagged: int
    first_pass_exact: int | None
    first_pass_known: int
    review_seconds: float | None
    minutes_saved: int

    def pct(self, part: int | None) -> float | None:
        return round(100 * part / self.compared, 1) if self.compared and part is not None else None

    @property
    def hours_saved(self) -> float:
        return round(self.minutes_saved / 60, 1)


def is_exactly_right(pair: Pairing) -> bool:
    return pair.result in (ComparisonResult.EXACT, ComparisonResult.DIFFERENT) and \
        not any(counts_against_us(d) for d in pair.differences)


def measures(pairs: list[Pairing], review_seconds: list[int], minutes_per_order: int) -> Measures:
    paired = [p for p in pairs if p.result in (ComparisonResult.EXACT, ComparisonResult.DIFFERENT)]
    right = [p for p in paired if is_exactly_right(p)]
    wrong = [p for p in pairs if p.ours and p.ours.initial_status == Status.READY and p.result != ComparisonResult.ONLY_THEIRS
             and not is_exactly_right(p) and any(counts_against_us(d) for d in p.differences)]
    flagged = [p for p in paired if p.ours.initial_status in (Status.CHECK, Status.PROBLEM)]
    first = [first_pass_exact(p.ours, p.theirs) for p in paired]
    known = [f for f in first if f is not None]
    return Measures(
        compared=len(paired),
        exactly_right=len(right),
        confidently_wrong=len(wrong),
        flagged=len(flagged),
        first_pass_exact=sum(known) if known else None,
        first_pass_known=len(known),
        review_seconds=sum(review_seconds) / len(review_seconds) if review_seconds else None,
        minutes_saved=len(paired) * minutes_per_order,
    )
