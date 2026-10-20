"""Pipeline step 6: every unit conversion rule, done by code."""

from decimal import Decimal

import pytest

from pipeline.products import Item
from pipeline.units import convert, quantity_plausible

SYNONYMS = {
    "CASE": ["case", "cases", "cs"], "BOX": ["box", "boxes", "bx"], "PACK": ["pack", "packs", "pk"],
    "EACH": ["each", "ea", "single", "unit"], "bottle": ["bottle", "bottles"], "roll": ["roll", "rolls"],
}
SPRAY = Item("CLN-101", "Antibacterial surface spray 750ml, case of 12", "CASE", 12, "bottle")
SINGLE_ROLL = Item("TOW-311", "Blue roll 2ply, single roll", "EACH", 1, "roll")
DISPENSER = Item("DSP-501", "Toilet roll dispenser", "EACH")


def test_sell_unit_or_a_synonym_is_unchanged():
    for unit in ("cases", "CS", "case", "Cases"):
        result = convert("2", unit, SPRAY, SYNONYMS)
        assert (result.qty, result.unit, result.note, result.flags) == (2, "CASE", None, [])


def test_inner_units_that_divide_exactly_are_converted():
    result = convert("120", "bottles", SPRAY, SYNONYMS)
    assert (result.qty, result.unit) == (Decimal(10), "CASE")
    assert result.note == "120 bottles / 12 = 10 cases"


def test_a_remainder_is_a_problem():
    result = convert("30", "bottles", SPRAY, SYNONYMS)
    assert result.qty is None
    assert result.flags[0] == {"code": "conversion", "severity": "problem",
                               "message": "30 bottles is not a whole number of cases of 12"}


def test_no_unit_uses_the_remembered_unit():
    result = convert("2", None, SPRAY, SYNONYMS, unit_hint="CASE")
    assert (result.qty, result.unit, result.flags) == (2, "CASE", [])


def test_no_unit_assumes_the_sell_unit_and_is_a_check():
    result = convert("6", None, SINGLE_ROLL, SYNONYMS)
    assert (result.qty, result.unit) == (6, "EACH")
    assert result.flags[0]["code"] == "no_unit" and result.flags[0]["severity"] == "check"


def test_unknown_unit_word_is_a_problem():
    assert convert("2", "tubs", SPRAY, SYNONYMS).flags[0]["code"] == "unit_unknown"


def test_a_different_sell_unit_is_a_problem():
    result = convert("2", "boxes", SPRAY, SYNONYMS)
    assert result.flags[0]["code"] == "unit_mismatch"
    assert "sold per case" in result.flags[0]["message"]


def test_inner_unit_without_a_pack_size_is_a_problem():
    item = Item("X-1", "Thing", "CASE", None, "bottle")
    assert convert("12", "bottles", item, SYNONYMS).flags[0]["code"] == "conversion"


def test_single_rolls_by_the_roll():
    result = convert("6", "rolls", SINGLE_ROLL, SYNONYMS)
    assert (result.qty, result.unit) == (6, "EACH")


def test_converting_can_be_switched_off():
    result = convert("120", "bottles", SPRAY, SYNONYMS, convert_units=False)
    assert result.qty is None and "switched off" in result.flags[0]["message"]


@pytest.mark.parametrize("qty", [None, "", "two", "0", "-1", "1.5"])
def test_quantities_that_are_missing_or_not_whole_are_problems(qty):
    result = convert(qty, "cases", SPRAY, SYNONYMS)
    assert result.qty is None and result.flags[0]["severity"] == "problem"


def test_each_for_an_item_sold_each():
    assert convert("1", "each", DISPENSER, SYNONYMS).qty == 1


UNIT_WORDS = {w for words in SYNONYMS.values() for w in words}


@pytest.mark.parametrize("span, qty, ok", [
    ("120 bottles of the antibac spray", "120", True),
    ("Toilet cleaner 750ml - 2 cases", "2", True),
    ("Blue nitrile gloves M x 3 boxes", "3", True),
    ("6 blue roll", "6", True),
    ("Blue roll 2ply 3", "3", True),
    ("Toilet roll 2ply 200 sheet x 1 case", "200", False),  # a number in the product name
    ("Toilet roll 2ply 200 sheet x 1 case", "1", True),
    ("Code: TOW-301 | Description: Toilet roll 200 sheet | Qty: 4 | Unit: CASE", "4", True),
    ("Code: TOW-301 | Description: Toilet roll 200 sheet | Qty: 4 | Unit: CASE", "200", False),
])
def test_quantity_must_sit_where_a_quantity_would(span, qty, ok):
    assert quantity_plausible(span, qty, UNIT_WORDS) is ok
