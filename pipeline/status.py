"""Pipeline step 8: order and line statuses (code only, never the AI).

An order's status is the worst of its parts, in the order Problem, Check, Ready.
It is Ready only if every one of these is true: it is a new order, the customer
was matched confidently (email or domain), every line is Ready, and no flag is at
Check or above.
"""

from __future__ import annotations

from pipeline.vocab import SEVERITY_ORDER, Classification, CustomerMatch, MatchMethod, Status

DETERMINISTIC = {MatchMethod.ALIAS, MatchMethod.EXACT_CODE, MatchMethod.EXACT_NAME, MatchMethod.OPERATOR}
AI_READY_SCORE = 90


def has_severity(flags: list[dict], severity: str) -> bool:
    return any(f.get("severity") == severity for f in flags or [])


def worst(statuses: list[str]) -> str:
    for status in SEVERITY_ORDER:
        if status in statuses:
            return status
    return Status.READY


def flags_status(flags: list[dict]) -> str:
    return worst([f["severity"] for f in flags or [] if f.get("severity") in SEVERITY_ORDER])


def line_status(product_code: str | None, method: str | None, flags: list[dict], allow_ai_ready: bool = False,
                score: int | None = None, ambiguous: bool = False) -> str:
    if not product_code or has_severity(flags, Status.PROBLEM):
        return Status.PROBLEM
    parts = [flags_status(flags)]
    if method == MatchMethod.AI_SUGGESTED:
        ai_ready = allow_ai_ready and not ambiguous and score is not None and score >= AI_READY_SCORE
        parts.append(Status.READY if ai_ready else Status.CHECK)
    elif method not in DETERMINISTIC:
        parts.append(Status.CHECK)
    return worst(parts)


def customer_status(customer_id: int | None, method: str | None) -> str:
    if customer_id is None:
        return Status.PROBLEM
    if method in (CustomerMatch.EMAIL, CustomerMatch.DOMAIN, CustomerMatch.OPERATOR):
        return Status.READY
    return Status.CHECK


def order_status(classification: str | None, read_problem: bool, customer: str, line_statuses: list[str],
                 flags: list[dict]) -> str:
    """customer is the customer match status (ready, check or problem)."""
    if read_problem or classification is None:
        return Status.PROBLEM
    if classification != Classification.NEW_ORDER:
        return Status.PASS_ON
    if not line_statuses:
        return Status.PROBLEM
    return worst([customer, *line_statuses, flags_status(flags)])


def line_summary_flags(lines: list[tuple[int, str, list[dict]]]) -> list[dict]:
    """Order-level flags that sum up the lines needing a person: [(line_no, status, flags)]."""
    out = []
    for severity in (Status.PROBLEM, Status.CHECK):
        hit = [(no, flags) for no, status, flags in lines if status == severity]
        if not hit:
            continue
        first_no, first_flags = hit[0]
        first = next((f["message"] for f in first_flags if f.get("severity") == severity), None)
        if severity == Status.CHECK and first is None:
            first = "Product suggested by the AI, please confirm"
        first = first or "Needs a fix"
        if len(hit) == 1:
            message = f"{first} (line {first_no})"
        else:
            message = f"{len(hit)} lines need {'a fix' if severity == Status.PROBLEM else 'a look'}, starting with " \
                      f"line {first_no}: {first[0].lower() + first[1:]}"
        out.append({"code": "line_problem" if severity == Status.PROBLEM else "line_check", "message": message,
                    "severity": severity})
    return out

