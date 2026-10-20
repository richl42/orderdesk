"""The accuracy test: its scoring, and the 20 demo orders replayed with saved AI answers.

The replay checks every step after the AI (customer and product matching, units,
checks, statuses) against the demo answer keys without calling the AI. If a prompt
changes, the saved answers no longer fit: run make eval ARGS="--save-test-answers".
"""

import pytest

from eval.run import FIXTURES_DIR, OrderResult, compare, run_demo, summarise
from pipeline.ai import AIUnavailable, AnthropicAI

ANSWER = {
    "customer": {"account_code": "RIVERS01"}, "po_number": "2291", "requested_delivery_date": "2026-10-15",
    "lines": [{"line_no": 1, "product_code": "CLN-101", "qty": "10", "unit": "CASE", "unit_price": "28.00"}],
}


def ours(**changes):
    order = {"customer": {"account_code": "RIVERS01"}, "po_number": "2291", "requested_delivery_date": "2026-10-15",
             "lines": [{"line_no": 1, "product_code": "CLN-101", "qty": "10.0", "unit": "CASE",
                        "unit_price": "28"}]}
    order.update(changes)
    return order


def test_compare_treats_equal_numbers_as_equal():
    assert compare(ours(), ANSWER) == []


@pytest.mark.parametrize("changes, words", [
    ({"customer": None}, "customer"),
    ({"po_number": "2292"}, "PO"),
    ({"requested_delivery_date": None}, "delivery"),
    ({"lines": []}, "0 lines"),
    ({"lines": [{"line_no": 1, "product_code": "CLN-102", "qty": "10", "unit": "CASE", "unit_price": "28.00"}]},
     "product"),
    ({"lines": [{"line_no": 1, "product_code": "CLN-101", "qty": "120", "unit": "bottle", "unit_price": "28.00"}]},
     "qty"),
    ({"lines": [{"line_no": 1, "product_code": "CLN-101", "qty": "10", "unit": "CASE", "unit_price": "28.01"}]},
     "price"),
])
def test_compare_finds_each_kind_of_difference(changes, words):
    diffs = compare(ours(**changes), ANSWER)
    assert diffs and words in diffs[0]


def test_confidently_wrong_and_wrong_status_are_counted():
    results = [
        OrderResult("a", "ready", "ready", True, False),
        OrderResult("b", "check", "check", False, False),
        OrderResult("c", "ready", "ready", False, True),
        OrderResult("d", "problem", "check", False, False),
    ]
    summary = summarise(results)
    assert summary["first_pass_exact"] == 1 and summary["confidently_wrong"] == 1
    assert summary["wrong_status"] == 1 and summary["flagged"] == 2


def test_eval_fails_when_an_order_is_confidently_wrong(monkeypatch, capsys):
    from eval import run

    monkeypatch.setattr(run, "run_demo", lambda *a, **k: [OrderResult("a", "ready", "ready", False, True,
                                                                         ["line 1 product X"])])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    from app.config import get_settings
    get_settings.cache_clear()
    assert run.main([]) == 1
    assert "1 confidently wrong (must be 0)" in capsys.readouterr().out


def test_the_20_demo_orders_reach_their_expected_status():
    if not FIXTURES_DIR.is_dir():
        pytest.skip("No saved AI answers: run make eval ARGS=\"--save-test-answers\"")
    replay = AnthropicAI("", "claude-sonnet-5-5", cache_dir=FIXTURES_DIR)
    try:
        results = run_demo(None, None, ai=replay)
    except AIUnavailable:
        pytest.fail("A prompt changed, so the saved AI answers no longer fit. "
                    "Run make eval ARGS=\"--save-test-answers\" to save new ones.")
    assert len(results) == 20
    wrong = [(r.order, r.expected_status, r.actual_status) for r in results if r.actual_status != r.expected_status]
    assert wrong == []
    assert not any(r.confidently_wrong for r in results)
    assert summarise(results)["first_pass_exact_pct"] >= 80
