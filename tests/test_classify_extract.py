"""Pipeline step 2: everything the AI returns is checked against the order text, and nothing is guessed."""

from datetime import datetime, timezone
from decimal import Decimal

from pipeline.classify_extract import COULD_NOT_READ, TOOL, classify_and_extract, find_span
from pipeline.status import status_after_reading
from tests.helpers import FakeAI, order_answer

TEXT = """Hi,

Please can we have for Thursday:
120 bottles of the antibac spray
TOW-310   Blue roll  x 2 packs @ 14.40

PO 2291. Kitchen entrance please.
Maria, Riverside Hotel"""

RECEIVED = datetime(2026, 10, 12, 8, 14, tzinfo=timezone.utc)


def read(*answers, text=TEXT):
    ai = FakeAI(*answers)
    return classify_and_extract(ai, text, "Demo Cleaning Supplies", RECEIVED, "maria@riverside.example",
                                "Order"), ai


def good_answer(**header):
    return order_answer([
        ("120 bottles of the antibac spray", None, "antibac spray", "120", "bottles", None),
        ("TOW-310   Blue roll  x 2 packs @ 14.40", "TOW-310", "Blue roll", "2", "packs", "14.40"),
    ], **{"customer_name": "Riverside Hotel", "po_number": "2291", "delivery_date_written": "Thursday",
          "delivery_notes": "Kitchen entrance", **header})


def test_a_good_answer_keeps_what_was_written():
    result, ai = read(good_answer())
    assert result.problem is None and result.flags == []
    assert (result.classification, result.customer_name, result.po_number) == ("new_order", "Riverside Hotel", "2291")
    assert result.delivery_raw == "Thursday" and result.delivery_notes == "Kitchen entrance"
    one, two = result.lines
    assert one.source_span == "120 bottles of the antibac spray"
    assert (one.requested_qty, one.requested_unit) == ("120", "bottles")  # not converted
    assert two.requested_code == "TOW-310" and two.requested_price == Decimal("14.40")
    assert all(not line.flags for line in result.lines)
    assert len(ai.calls) == 1
    assert "Received: Monday 12 October 2026, 09:14 UK time" in ai.calls[0]["user"]


def test_tool_schema_is_strict_and_complete():
    schema = TOOL["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    line = schema["properties"]["lines"]["items"]
    assert line["additionalProperties"] is False and set(line["required"]) == set(line["properties"])


def test_span_that_differs_only_in_spacing_is_mapped_to_the_real_text():
    assert find_span("TOW-310 Blue roll x 2 packs @ 14.40", TEXT) == "TOW-310   Blue roll  x 2 packs @ 14.40"
    assert find_span("120 bottles of antibac spray", TEXT) is None  # a word missing is not allowed
    assert find_span("", TEXT) is None


def test_a_span_not_in_the_text_makes_that_line_a_problem():
    answer = good_answer()
    answer["lines"][0]["source_span"] = "120 bottles of antibacterial spray"
    result, _ = read(answer)
    bad = result.lines[0]
    assert bad.source_span is None and bad.raw_text == "120 bottles of antibacterial spray"
    assert [f["code"] for f in bad.flags] == ["span_missing"]
    assert status_after_reading(result.problem, result.classification, result.flags,
                                [line.flags for line in result.lines]) == "problem"


def test_quantity_code_unit_and_price_must_be_inside_the_span():
    answer = good_answer()
    answer["lines"][1].update(quantity="12", product_code="TOW-311", unit="cases", price="13.00")
    result, _ = read(answer)
    codes = {f["code"] for f in result.lines[1].flags}
    assert codes == {"qty_not_in_text", "code_not_in_text", "unit_not_in_text", "price_not_in_text"}
    assert result.lines[1].requested_price is None


def test_header_values_not_in_the_text_are_left_blank_and_flagged():
    result, _ = read(good_answer(po_number="2292", delivery_date_written="Friday", delivery_notes="Rear door"))
    assert result.po_number is None and result.delivery_raw is None and result.delivery_notes is None
    assert {f["code"] for f in result.flags} == {"po_not_in_text", "date_not_in_text", "notes_not_in_text"}
    assert all(f["severity"] == "check" for f in result.flags)


def test_customer_name_may_come_from_the_sender_line():
    result, _ = read(good_answer(customer_name="maria@riverside.example"))
    assert result.customer_name == "maria@riverside.example"


def test_an_unusable_answer_is_retried_once():
    result, ai = read(None, good_answer())
    assert result.problem is None and len(result.lines) == 2
    assert [c["attempt"] for c in ai.calls] == [1, 2]
    assert len(result.calls) == 2  # both calls count towards tokens and cost
    assert result.input_tokens == 2000


def test_two_unusable_answers_make_the_order_a_problem():
    result, ai = read(None, {"classification": "something"})
    assert result.problem == COULD_NOT_READ
    assert result.flags[0]["message"] == COULD_NOT_READ
    assert len(ai.calls) == 2


def test_not_a_new_order_is_pass_on_and_lines_are_ignored():
    answer = order_answer([("120 bottles of the antibac spray", None, "antibac", "120", "bottles", None)],
                          classification="amendment", classification_reason="Adds to yesterday's order")
    result, _ = read(answer)
    assert result.lines == []
    assert status_after_reading(result.problem, result.classification, result.flags, []) == "pass_on"


def test_several_orders_in_one_message_is_a_problem():
    result, _ = read(good_answer(several_orders=True, several_orders_reason="Two delivery addresses"))
    assert result.flags[0]["code"] == "several_orders"
    assert "Two delivery addresses" in result.flags[0]["message"]
    assert status_after_reading(result.problem, result.classification, result.flags, []) == "problem"


def test_a_new_order_with_no_lines_is_a_problem():
    result, _ = read(order_answer([]))
    assert [f["code"] for f in result.flags] == ["no_lines"]


def test_a_clean_new_order_waits_for_matching():
    result, _ = read(good_answer())
    assert status_after_reading(result.problem, result.classification, result.flags,
                                [line.flags for line in result.lines]) is None
