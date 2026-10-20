"""Pipeline step 4: product matching. Deterministic rules first; the AI only chooses from a short list."""

from pipeline.normalise import line_text, name_keys, strip_pack
from pipeline.products import AliasRecord, Catalogue, Item, LineIn, choose_with_ai, match_line
from tests.helpers import FakeAI

ITEMS = [
    Item("CLN-101", "Antibacterial surface spray 750ml, case of 12", "CASE", 12, "bottle", "5069990000014"),
    Item("CLN-102", "Disinfectant spray 750ml, case of 12", "CASE", 12, "bottle"),
    Item("CLN-110", "Toilet cleaner 750ml, case of 12", "CASE", 12, "bottle"),
    Item("TOW-301", "Toilet roll 2ply 200 sheet, case of 36", "CASE", 36, "roll"),
    Item("TOW-310", "Blue roll 2ply, pack of 6", "PACK", 6, "roll"),
    Item("TOW-311", "Blue roll 2ply, single roll", "EACH", 1, "roll"),
    Item("GLV-202", "Blue nitrile gloves powder free M, box of 100", "BOX", 100, "glove", "5069990000281"),
]
CATALOGUE = Catalogue(ITEMS)
UNITS = {"case", "cases", "box", "boxes", "pack", "packs", "bottle", "bottles", "roll", "rolls", "each"}
RIVERSIDE, OLD_MILL = 1, 2
ALIASES = [AliasRecord(10, RIVERSIDE, "antibac spray", "CLN-101"),
           AliasRecord(11, OLD_MILL, "big black bag", "TOW-301", "BOX")]


def match(span, qty=None, unit=None, customer=RIVERSIDE, price=None, aliases=ALIASES):
    return match_line(LineIn(1, span, qty, unit, price), customer, CATALOGUE, aliases, UNITS)


def test_pack_wording_is_stripped_from_catalogue_names():
    assert strip_pack("Blue roll 2ply, pack of 6") == "Blue roll 2ply"
    assert strip_pack("Blue roll 2ply, single roll") == "Blue roll 2ply"
    assert strip_pack("Yellow gloves L, pack of 12 pairs") == "Yellow gloves L"
    assert strip_pack("Hand sanitiser gel 5L") == "Hand sanitiser gel 5L"
    assert "toilet roll 2ply 200 sheet" in name_keys("Toilet roll 2ply 200 sheet, case of 36")


def test_table_rows_keep_only_the_product_words():
    row = "PO Number: TH-4410 | Stock Code: TOW-301 | Description: Toilet roll | Qty: 4 | Unit: CASE | Total: 67.60"
    assert line_text(row, "4", "CASE") == "TOW-301 Toilet roll"
    assert line_text("Toilet cleaner 750ml - 2 cases", "2", "cases") == "Toilet cleaner 750ml - 2 cases"


def test_this_customers_saved_phrase_matches_first():
    result = match("120 bottles of the antibac spray", "120", "bottles")
    assert (result.product_code, result.method, result.alias_id) == ("CLN-101", "alias", 10)


def test_another_customers_phrase_is_only_a_candidate():
    result = match("120 bottles of the antibac spray", "120", "bottles", customer=OLD_MILL)
    assert result.method is None and result.needs_ai
    assert result.candidates[0]["product_code"] == "CLN-101"
    assert result.candidates[0]["source"] == "another customer's phrasing"


def test_saved_phrase_brings_its_unit():
    result = match("3 big black bags", "3", None, customer=OLD_MILL)
    assert (result.product_code, result.unit_hint) == ("TOW-301", "BOX")


def test_code_or_barcode_written_in_the_line():
    assert match("CLN-110 x 2", "2").method == "exact_code"
    by_barcode = match("5069990000281 - 2 boxes", "2", "boxes")
    assert (by_barcode.product_code, by_barcode.method) == ("GLV-202", "exact_code")
    assert by_barcode.flags == []


def test_a_code_with_words_for_a_different_product_is_a_check():
    result = match("CLN-110 blue nitrile gloves x 2", "2")
    assert result.product_code == "CLN-110"
    assert result.flags[0]["code"] == "code_description_mismatch" and result.flags[0]["severity"] == "check"


def test_two_codes_on_one_line_are_not_an_exact_match():
    result = match("CLN-110 or CLN-102 x 2", "2")
    assert result.method is None and result.needs_ai
    assert result.flags[0]["code"] == "several_codes"
    assert {c["product_code"] for c in result.candidates} >= {"CLN-110", "CLN-102"}


def test_exact_name_without_the_pack_wording():
    result = match("Toilet cleaner 750ml - 2 cases", "2", "cases")
    assert (result.product_code, result.method) == ("CLN-110", "exact_name")


def test_near_twins_are_never_an_exact_name_match():
    result = match("6 blue roll 2ply", "6", None)
    assert result.method is None and result.needs_ai
    codes = [c["product_code"] for c in result.candidates]
    assert "TOW-310" in codes and "TOW-311" in codes


def test_misspelling_gets_the_right_product_as_top_candidate():
    result = match("disinfectent spary x 2 cases", "2", "cases")
    assert result.needs_ai and result.candidates[0]["product_code"] == "CLN-102"


def test_nothing_like_it_is_unknown_without_asking_the_ai():
    result = match("SA-0042 x 4", "4")
    assert result.product_code is None and not result.needs_ai
    assert result.flags[0]["code"] == "unknown_product"


def ai_lines(*spans):
    lines = [LineIn(i, span) for i, span in enumerate(spans, start=1)]
    matches = {ln.line_no: match_line(ln, RIVERSIDE, CATALOGUE, [], UNITS) for ln in lines}
    return lines, matches


def test_ai_choice_must_be_in_the_lines_candidates():
    lines, matches = ai_lines("disinfectent spary", "blue roll")
    ai = FakeAI({"lines": [
        {"line_no": 1, "product_code": "CLN-102", "reason": "Misspelt", "ambiguous": False},
        {"line_no": 2, "product_code": "CLN-110", "reason": "Made up", "ambiguous": False},
    ]})
    calls = choose_with_ai(ai, "Riverside Hotel", lines, matches, CATALOGUE)
    assert len(calls) == 1
    assert (matches[1].product_code, matches[1].method) == ("CLN-102", "ai_suggested")
    assert matches[2].product_code is None and matches[2].flags[0]["code"] == "ai_choice_rejected"
    sent = ai.calls[0]["user"]
    assert "Line 1: disinfectent spary" in sent and "CLN-102: Disinfectant spray 750ml" in sent
    assert "TOW-301" not in sent.split("Line 2")[0]  # only each line's own candidates are sent


def test_ai_null_means_not_found_and_ambiguous_null_says_so():
    lines, matches = ai_lines("disinfectent spary", "blue roll")
    ai = FakeAI({"lines": [
        {"line_no": 1, "product_code": None, "reason": "Not one of these", "ambiguous": False},
        {"line_no": 2, "product_code": None, "reason": "Pack or single?", "ambiguous": True},
    ]})
    choose_with_ai(ai, None, lines, matches, CATALOGUE)
    assert matches[1].flags[0]["code"] == "unknown_product"
    assert matches[2].flags[0]["code"] == "ai_ambiguous"


def test_an_unusable_ai_answer_is_retried_then_lines_are_problems():
    lines, matches = ai_lines("disinfectent spary")
    ai = FakeAI(None, {"lines": []})
    calls = choose_with_ai(ai, None, lines, matches, CATALOGUE)
    assert len(calls) == 2
    assert matches[1].product_code is None and matches[1].flags[-1]["severity"] == "problem"


def test_no_ai_call_when_every_line_matched_deterministically():
    lines = [LineIn(1, "CLN-110 x 2", "2")]
    matches = {1: match_line(lines[0], RIVERSIDE, CATALOGUE, [], UNITS)}
    ai = FakeAI()
    assert choose_with_ai(ai, None, lines, matches, CATALOGUE) == []
    assert ai.calls == []
