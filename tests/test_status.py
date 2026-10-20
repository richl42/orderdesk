"""Pipeline step 8: an order is Ready only when everything about it was certain."""

from pipeline.status import customer_status, line_status, line_summary_flags, order_status

CHECK = {"code": "x", "message": "Needs a look", "severity": "check"}
PROBLEM = {"code": "y", "message": "Needs a fix", "severity": "problem"}


def test_deterministic_matches_are_ready():
    for method in ("alias", "exact_code", "exact_name", "operator"):
        assert line_status("CLN-101", method, []) == "ready"


def test_ai_suggestions_are_check_by_default():
    assert line_status("CLN-101", "ai_suggested", [], score=100) == "check"


def test_allow_ai_ready_needs_a_high_score_and_no_ambiguity():
    assert line_status("CLN-101", "ai_suggested", [], True, score=95) == "ready"
    assert line_status("CLN-101", "ai_suggested", [], True, score=89) == "check"
    assert line_status("CLN-101", "ai_suggested", [], True, score=95, ambiguous=True) == "check"


def test_no_product_or_a_problem_flag_is_a_problem():
    assert line_status(None, None, []) == "problem"
    assert line_status("CLN-101", "exact_code", [PROBLEM]) == "problem"
    assert line_status("CLN-101", "exact_code", [CHECK]) == "check"
    assert line_status("CLN-101", "exact_code", [{"code": "note", "message": "x", "severity": "info"}]) == "ready"


def test_customer_by_email_or_domain_only_is_confident():
    assert customer_status(1, "email") == "ready" and customer_status(1, "domain") == "ready"
    assert customer_status(1, "name") == "check"
    assert customer_status(None, None) == "problem"


def test_order_is_the_worst_of_its_parts():
    assert order_status("new_order", False, "ready", ["ready", "ready"], []) == "ready"
    assert order_status("new_order", False, "check", ["ready"], []) == "check"
    assert order_status("new_order", False, "ready", ["ready", "check"], []) == "check"
    assert order_status("new_order", False, "ready", ["check", "problem"], []) == "problem"
    assert order_status("new_order", False, "ready", ["ready"], [CHECK]) == "check"
    assert order_status("new_order", False, "problem", ["ready"], []) == "problem"


def test_only_new_orders_with_lines_can_be_ready():
    assert order_status("chase", False, "ready", [], []) == "pass_on"
    assert order_status("amendment", False, "problem", [], [PROBLEM]) == "pass_on"
    assert order_status("new_order", False, "ready", [], []) == "problem"
    assert order_status(None, True, "ready", [], []) == "problem"


def test_line_summaries_name_the_first_line():
    flags = line_summary_flags([(1, "ready", []), (2, "problem", [PROBLEM]), (3, "check", [CHECK]),
                                (4, "check", [CHECK])])
    assert flags[0] == {"code": "line_problem", "message": "Needs a fix (line 2)", "severity": "problem"}
    assert flags[1]["message"] == "2 lines need a look, starting with line 3: needs a look"
