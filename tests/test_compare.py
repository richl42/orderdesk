"""Trial comparison: pairing, differences and the six measures, as pure functions."""

from datetime import date
from decimal import Decimal as D

from pipeline.compare import (
    CompLine,
    OurOrder,
    StaffRow,
    TheirOrder,
    compare_lines,
    group_staff_rows,
    is_exactly_right,
    measures,
    pair_orders,
)

MON, TUE = date(2026, 10, 12), date(2026, 10, 13)


def ours(oid, po="PO-1", account="A1", day=MON, lines=None, status="ready", delivery=None, first_pass=None):
    return OurOrder(oid, account, po, day, delivery, lines or [CompLine("CLN-101", D(10), "CASE", D("28.00"))],
                    status, first_pass)


def theirs(po="PO-1", account="A1", day=MON, lines=None, delivery=None):
    return TheirOrder(po, account, day, delivery, lines or [CompLine("CLN-101", D(10), "CASE", D("28.00"))])


def test_same_order_is_exact():
    [pair] = pair_orders([ours(1)], [theirs()])
    assert pair.result == "exact" and pair.differences == []


def test_po_and_account_must_both_match():
    pairs = pair_orders([ours(1)], [theirs(account="A2")])
    assert sorted(p.result for p in pairs) == ["only_ours", "only_theirs"]


def test_the_same_po_twice_is_settled_by_the_order_date():
    pairs = pair_orders([ours(1, day=MON), ours(2, day=TUE)], [theirs(day=TUE), theirs(day=MON)])
    assert {(p.ours.order_id, p.theirs.order_date) for p in pairs} == {(1, MON), (2, TUE)}


def test_no_po_pairs_by_account_and_date_only_when_one_each_side():
    assert pair_orders([ours(1, po=None)], [theirs(po=None)])[0].result == "exact"
    two = pair_orders([ours(1, po=None), ours(2, po=None)], [theirs(po=None)])
    assert sorted(p.result for p in two) == ["only_ours", "only_ours", "only_theirs"]


def test_nothing_unpaired_is_dropped():
    pairs = pair_orders([ours(1, po="X")], [theirs(po="Y")])
    assert {p.result for p in pairs} == {"only_ours", "only_theirs"}
    assert all(p.differences for p in pairs)


def test_quantity_price_and_delivery_differences():
    mine = [CompLine("CLN-101", D(10), "CASE", D("28.00")), CompLine("GLV-202", D(2), "BOX", D("6.20"))]
    staff = [CompLine("CLN-101", D(12), "CASE", D("28.00")), CompLine("GLV-202", D(2), "BOX", D("6.22"))]
    [pair] = pair_orders([ours(1, lines=mine, delivery=MON)], [theirs(lines=staff, delivery=TUE)])
    assert [d["field"] for d in pair.differences] == ["delivery date", "quantity", "price"]
    assert pair.differences[1]["ours"] == "10 cases" and pair.differences[1]["theirs"] == "12 cases"


def test_price_within_a_penny_and_dates_only_when_both_sides_have_one():
    [pair] = pair_orders([ours(1, lines=[CompLine("CLN-101", D(10), "CASE", D("28.00"))], delivery=MON)],
                         [theirs(lines=[CompLine("CLN-101", D(10), "CASE", D("28.01"))], delivery=None)])
    assert pair.result == "exact"


def test_a_swapped_product_is_one_difference():
    diffs = compare_lines([CompLine("BAG-404", D(2), "BOX", D("9.21"))], [CompLine("BAG-405", D(2), "BOX", D("10.20"))])
    assert diffs == [{"field": "product", "line": "BAG-404", "ours": "BAG-404 × 2 boxes", "theirs": "BAG-405 × 2 boxes"}]


def test_extra_and_missing_lines():
    diffs = compare_lines([CompLine("A", D(1), "EACH", None)], [CompLine("A", D(1), "EACH", None),
                                                                 CompLine("B", D(1), "EACH", None)])
    assert diffs == [{"field": "line", "line": "B", "ours": "Not in our order", "theirs": "B × 1 each"}]


def test_staff_quantities_are_converted_to_the_sell_unit():
    def to_sell(code, qty, unit):
        return (qty / 12, "CASE") if unit == "bottles" else (qty, unit.upper() if unit else "CASE")

    [order] = group_staff_rows([StaffRow("PO-1", "A1", MON, "CLN-101", D(120), "bottles", D("28.00"), None)], to_sell)
    assert order.lines[0].qty == 10 and order.lines[0].unit == "CASE"


def test_staff_rows_are_grouped_into_orders():
    rows = [StaffRow("PO-1", "A1", MON, "X", D(1), "CS", None, TUE), StaffRow("PO-1", "A1", MON, "Y", D(1), "CS", None, None),
            StaffRow("PO-2", "A1", MON, "X", D(1), "CS", None, None)]
    orders = group_staff_rows(rows, lambda c, q, u: (q, u))
    assert len(orders) == 2 and len(orders[0].lines) == 2 and orders[0].delivery_date == TUE


def test_the_measures():
    pairs = pair_orders(
        [ours(1, po="P1", status="ready"), ours(2, po="P2", status="check"),
         ours(3, po="P3", status="ready", lines=[CompLine("CLN-101", D(9), "CASE", D("28.00"))]),
         ours(4, po="P4", status="problem")],
        [theirs(po="P1"), theirs(po="P2"), theirs(po="P3")])
    m = measures(pairs, [30, 90], minutes_per_order=5)
    assert (m.compared, m.exactly_right, m.flagged) == (3, 2, 1)
    assert m.confidently_wrong == 1  # order 3: Ready and wrong. Order 4 was Problem, so it is not counted
    assert m.review_seconds == 60 and m.minutes_saved == 15 and m.hours_saved == 0.2


def test_staff_error_does_not_count_against_us():
    [pair] = pair_orders([ours(1, lines=[CompLine("CLN-101", D(9), "CASE", None)])], [theirs()])
    assert not is_exactly_right(pair)
    pair.differences[0]["tag"] = "staff_error"
    assert is_exactly_right(pair)
    assert measures([pair], [], 5).confidently_wrong == 0


def test_an_unentered_ready_order_counts_as_confidently_wrong_until_explained():
    pairs = pair_orders([ours(1, po="ONLY-OURS")], [])
    assert measures(pairs, [], 5).confidently_wrong == 1
    pairs[0].differences[0]["tag"] = "staff_error"
    assert measures(pairs, [], 5).confidently_wrong == 0


def test_first_pass_exact_uses_the_snapshot_before_corrections():
    snapshot = {"account_code": "A1", "po_number": "PO-1", "delivery_date": None,
                "lines": [{"product_code": "CLN-102", "qty": "10", "unit": "CASE", "unit_price": "28.00"}]}
    pairs = pair_orders([ours(1, first_pass=snapshot), ours(2, po="PO-2", first_pass=None)],
                        [theirs(), theirs(po="PO-2")])
    m = measures(pairs, [], 5)
    assert m.exactly_right == 2 and (m.first_pass_exact, m.first_pass_known) == (0, 1)
