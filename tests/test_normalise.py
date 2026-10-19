import pytest

from pipeline import normalise


@pytest.mark.parametrize("word,expected", [
    ("bottles", "bottle"), ("boxes", "box"), ("gloves", "glove"), ("supplies", "supply"),
    ("glass", "glass"), ("cs", "cs"), ("2ply", "2ply"), ("sponges", "sponge"), ("bus", "bus"),
])
def test_singular(word, expected):
    assert normalise.singular(word) == expected


def test_basic_keeps_codes_and_hyphenated_words():
    assert normalise.basic("TOW-310: Blue roll (2-ply)!") == "tow-310 blue roll 2-ply"
    assert normalise.basic("St Anne’s - Café") == "st annes café"


def test_description_key_ignores_case_plurals_and_fillers():
    assert normalise.description_key("Blue Roll 2ply, pack of 6") == normalise.description_key("blue rolls 2ply pack 6")


@pytest.mark.parametrize("text,qty,unit,expected", [
    ("120 bottles of the antibac spray", "120", "bottles", "antibac spray"),
    ("2 x blue roll 2ply", "2", None, "blue roll 2ply"),
    ("4 boxes medium blue gloves", "4", "boxes", "medium blue glove"),
    ("RH-0042 blue roll x 3", "3", None, "rh-0042 blue roll"),
    ("Antibac spray 750ml - 5 cases please", "5", "cases", "antibac spray 750ml"),
])
def test_alias_phrase_removes_quantity_and_unit_but_keeps_codes(text, qty, unit, expected):
    assert normalise.alias_phrase(text, qty=qty, unit=unit) == expected


def test_alias_phrase_without_extracted_values_uses_known_unit_words():
    units = {"bottle", "bottles", "box", "boxes"}
    assert normalise.alias_phrase("120 bottles of the antibac spray", unit_words=units) == "antibac spray"
    assert normalise.alias_phrase("3 blue gloves", unit_words=units) == "blue glove"


def test_alias_phrase_is_the_same_for_different_quantities():
    a = normalise.alias_phrase("120 bottles of the antibac spray", "120", "bottles")
    b = normalise.alias_phrase("60 Bottles of the Antibac Spray", "60", "Bottles")
    assert a == b


def test_code_key():
    assert normalise.code_key(" tow-310 ") == "TOW-310"
    assert normalise.code_key("5069 9900 0001 3") == "5069990000013"


def test_split_emails():
    cell = "Orders@Riverside.example; kitchen@riverside.example, orders@riverside.example"
    assert normalise.split_emails(cell) == ["orders@riverside.example", "kitchen@riverside.example"]
    assert normalise.split_emails("") == []
    assert normalise.split_emails(None) == []
