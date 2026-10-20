"""Order status rules (code only, never the AI).

An order's status is the worst of its parts, in the order Problem, Check, Ready.
Milestone 3 decides only what reading alone can settle; matching and checks
(Milestone 4) decide the rest.
"""

from __future__ import annotations

from pipeline.vocab import Classification, Status


def has_severity(flags: list[dict], severity: str) -> bool:
    return any(f.get("severity") == severity for f in flags or [])


def status_after_reading(problem: str | None, classification: str | None, flags: list[dict],
                         line_flags: list[list[dict]]) -> str | None:
    """The status reading alone settles, or None when matching still has to run.

    Problem is final because nothing later can make an order better than its worst part.
    Pass on is final because only new orders go further.
    """
    if problem:
        return Status.PROBLEM
    if classification and classification != Classification.NEW_ORDER:
        return Status.PASS_ON
    if has_severity(flags, Status.PROBLEM) or any(has_severity(f, Status.PROBLEM) for f in line_flags):
        return Status.PROBLEM
    return None
