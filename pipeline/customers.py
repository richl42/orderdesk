"""Pipeline step 3: work out which of the client's customers sent the order.

Deterministic, in this order: the sender's exact email, then an email domain only
one customer uses (both confident), then the customer name written in the order
(a Check, never confident), otherwise Problem with the closest names offered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from pipeline.normalise import basic, email_domain
from pipeline.vocab import CustomerMatch, Status

NAME_MATCH = 92  # the written name must score this against one customer...
NAME_RIVAL = 85  # ...and no other customer may score above this


@dataclass
class CustomerRecord:
    id: int
    account_code: str
    name: str
    emails: list[str] = field(default_factory=list)
    email_domains: list[str] = field(default_factory=list)
    price_band: str | None = None


@dataclass
class CustomerResult:
    customer_id: int | None = None
    method: str | None = None
    status: str = Status.PROBLEM  # ready (confident), check (by name) or problem (unknown)
    candidates: list[dict] = field(default_factory=list)  # closest names, for the review dropdown
    flags: list[dict] = field(default_factory=list)


def _flag(code: str, message: str, severity: str) -> dict:
    return {"code": code, "message": message, "severity": severity}


def name_scores(written: str, customers: list[CustomerRecord]) -> list[tuple[int, CustomerRecord]]:
    """Every customer scored against a written name, best first."""
    key = basic(written)
    scored = [(int(round(fuzz.token_set_ratio(key, basic(c.name)))), c) for c in customers]
    return sorted(scored, key=lambda pair: (-pair[0], pair[1].name))


def _by_name(written: str | None, customers: list[CustomerRecord]) -> tuple[CustomerRecord | None, list]:
    """The one customer a written name clearly means (or None), and the scored list."""
    if not written or not basic(written):
        return None, []
    scored = name_scores(written, customers)
    if scored and scored[0][0] >= NAME_MATCH and not any(s > NAME_RIVAL for s, _ in scored[1:]):
        return scored[0][1], scored
    return None, scored


def match_customer(sender_email: str | None, written_name: str | None,
                   customers: list[CustomerRecord]) -> CustomerResult:
    sender = (sender_email or "").strip().lower()
    by_email = [c for c in customers if sender and sender in [e.lower() for e in c.emails]]
    domain = email_domain(sender) if sender else ""
    by_domain = [c for c in customers if domain and domain in [d.lower() for d in c.email_domains]]
    named, scored = _by_name(written_name, customers)

    found, method = None, None
    if len(by_email) == 1:
        found, method = by_email[0], CustomerMatch.EMAIL
    elif not by_email and len(by_domain) == 1:
        found, method = by_domain[0], CustomerMatch.DOMAIN

    if found:
        result = CustomerResult(found.id, method, Status.READY)
        if named and named.id != found.id:
            result.status = Status.CHECK
            result.flags.append(_flag("customer_conflict",
                                      f"Sent from {found.name}'s email, but the order names {named.name}", "check"))
        return result

    if named:
        return CustomerResult(named.id, CustomerMatch.NAME, Status.CHECK, flags=[_flag(
            "customer_by_name", f"Customer recognised by the name in the order ({written_name}), not by email",
            "check")])

    top = scored[:3] if scored else []
    result = CustomerResult(candidates=[{"customer_id": c.id, "name": c.name, "score": s} for s, c in top])
    if len(by_email) > 1:
        message = "Unknown customer: this email address belongs to more than one customer"
    elif written_name:
        message = f"Unknown customer: '{written_name}' does not clearly match one customer"
    else:
        message = "Unknown customer"
    result.flags.append(_flag("unknown_customer", message, "problem"))
    return result
