"""Pipeline step 7: each check, and the rules that switch them on and off."""

from datetime import date, datetime, timezone
from decimal import Decimal

from pipeline.checks import CheckLine, EarlierOrder, OrderFacts, outlier_flags, price_flags, run_checks
from pipeline.vocab import DEFAULT_RULES
from pipeline.workdays import LONDON

MONDAY_10AM = datetime(2026, 10, 12, 10, 0, tzinfo=LONDON)
RULES = {**DEFAULT_RULES, "min_order_value": "25.00"}


def order(**kwargs):
    base = dict(received_at=MONDAY_10AM, customer_id=1, po_number="PO-1", content_hash="abc", delivery_date=None,
                delivery_raw=None, lines=[CheckLine(1, "CLN-101", Decimal(2), Decimal("28.00"), None)])
    base.update(kwargs)
    return OrderFacts(**base)


def codes(facts, rules=RULES, earlier=(), largest=None):
    order_flags, line_flags = run_checks(facts, rules, list(earlier), largest or {})
    return [f["code"] for f in order_flags], {n: [f["code"] for f in fl] for n, fl in line_flags.items()}


def test_a_clean_order_passes_every_check():
    assert codes(order()) == ([], {1: []})


def test_price_written_differs_by_more_than_a_penny():
    line = CheckLine(1, "TOW-301", Decimal(4), Decimal("15.89"), Decimal("16.90"))
    assert price_flags(line, RULES)[0]["message"] == "They wrote £16.90; their price is £15.89"
    assert price_flags(CheckLine(1, "X", Decimal(1), Decimal("15.89"), Decimal("15.90")), RULES) == []
    assert price_flags(line, {**RULES, "flag_price_differences": False}) == []


def test_price_per_bottle_matching_the_case_price_is_fine():
    exact = CheckLine(1, "CLN-101", Decimal(10), Decimal("24.00"), Decimal("2.00"), pack_size=12, converted=True)
    assert price_flags(exact, RULES) == []
    dearer = CheckLine(1, "CLN-101", Decimal(10), Decimal("24.00"), Decimal("2.50"), pack_size=12, converted=True)
    assert price_flags(dearer, RULES)[0]["code"] == "price_difference"


def test_no_price_known_is_a_check():
    assert price_flags(CheckLine(1, "X-1", Decimal(1), None, None), RULES)[0]["code"] == "no_price"


def test_same_customer_and_po_within_the_window_is_a_duplicate():
    earlier = [EarlierOrder(7, datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc), 1, "po-1", "zzz")]
    order_codes, _ = codes(order(), earlier=earlier)
    assert order_codes == ["duplicate"]
    assert codes(order(customer_id=2), earlier=earlier)[0] == []  # another customer's PO
    old = [EarlierOrder(7, datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), 1, "PO-1", "zzz")]
    assert codes(order(), earlier=old)[0] == []  # outside the 7 days
    assert codes(order(), {**RULES, "duplicate_window_days": 0}, earlier=earlier)[0] == []


def test_same_text_is_a_duplicate_even_without_a_po():
    earlier = [EarlierOrder(7, datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc), 1, None, "abc")]
    assert codes(order(po_number=None), earlier=earlier)[0] == ["duplicate"]


def test_after_the_cutoff_for_next_working_day():
    late_friday = datetime(2026, 10, 16, 15, 30, tzinfo=LONDON)
    assert codes(order(received_at=late_friday, delivery_date=date(2026, 10, 19)))[0] == ["cutoff"]
    assert codes(order(received_at=late_friday, delivery_date=date(2026, 10, 20)))[0] == []
    early_friday = datetime(2026, 10, 16, 14, 59, tzinfo=LONDON)
    assert codes(order(received_at=early_friday, delivery_date=date(2026, 10, 19)))[0] == []


def test_below_the_minimum_value_only_when_every_line_is_priced():
    small = [CheckLine(1, "CLO-621", Decimal(2), Decimal("2.40"), None)]
    assert codes(order(lines=small))[0] == ["minimum_order"]
    unpriced = small + [CheckLine(2, "X", None, None, None)]
    assert "minimum_order" not in codes(order(lines=unpriced))[0]
    assert codes(order(lines=small), {**RULES, "min_order_value": None})[0] == []


def test_missing_po_only_when_the_rule_is_on():
    assert codes(order(po_number=None))[0] == []
    assert codes(order(po_number=None), {**RULES, "require_po_number": True})[0] == ["po_missing"]


def test_delivery_date_before_the_order_arrived_is_a_problem():
    order_flags, _ = run_checks(order(delivery_date=date(2026, 10, 9)), RULES, [], {})
    assert order_flags[0]["code"] == "delivery_past" and order_flags[0]["severity"] == "problem"


def test_delivery_words_we_could_not_understand_are_a_check():
    assert codes(order(delivery_raw="as normal"))[0] == ["date_unclear"]


def test_quantity_outlier():
    line = CheckLine(1, "CLN-101", Decimal(60), Decimal("28.00"), None)
    assert outlier_flags(line, {"CLN-101": Decimal(10)})[0]["code"] == "qty_outlier"
    assert outlier_flags(line, {"CLN-101": Decimal(12)}) == []
    assert outlier_flags(line, {}) == []
