"""Turn the delivery date a customer wrote ("Thursday", "22/10", "tomorrow") into a date.

Code, never the AI, does this, counting from the day the order arrived in
Europe/London time. Anything unclear gives None: the raw wording is kept and
the order gets flagged, rather than a guessed date reaching an export.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from pipeline.workdays import london_date

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "weds": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
# Words around a date that do not change which day is meant.
FILLER = {
    "please", "pls", "deliver", "delivery", "delivered", "on", "by", "for", "the", "of", "a", "morning",
    "afternoon", "am", "pm", "first", "thing", "if", "possible", "latest", "at", "before", "noon", "lunchtime",
    "lunch", "day", "date", "required", "needed", "due", "this", "coming", "in",
}


def _year_nearest(day: int, month: int, arrived: date) -> date | None:
    """A day and month with no year: the year that puts it closest to the arrival date."""
    best = None
    for year in (arrived.year - 1, arrived.year, arrived.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((candidate - arrived).days) < abs((best - arrived).days):
            best = candidate
    return best


def _full_year(text: str) -> int:
    year = int(text)
    return year + 2000 if year < 100 else year


def _make(day: int, month: int, year: int | None, arrived: date) -> date | None:
    if year is None:
        return _year_nearest(day, month, arrived)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_weekday(weekday: int, arrived: date) -> date:
    """The next such day strictly after the day the order arrived."""
    ahead = (weekday - arrived.weekday() - 1) % 7 + 1
    return arrived + timedelta(days=ahead)


def _clean(raw: str) -> str:
    text = raw.lower().replace(",", " ").replace("’", "'")
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"[()!?]", " ", text)
    return " ".join(text.split())


NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?$")
ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _explicit_date(words: list[str], arrived: date) -> tuple[date | None, list[str]]:
    """Find a written date (numeric, or day and month name) and return it with the words left over."""
    for i, word in enumerate(words):
        if m := ISO.match(word):
            return _make(int(m[3]), int(m[2]), int(m[1]), arrived), words[:i] + words[i + 1:]
        if m := NUMERIC.match(word):
            year = _full_year(m[3]) if m[3] else None
            return _make(int(m[1]), int(m[2]), year, arrived), words[:i] + words[i + 1:]
    for i, word in enumerate(words):
        if word not in MONTHS:
            continue
        month = MONTHS[word]
        used = {i}
        day = None
        if i > 0 and words[i - 1].isdigit():
            day, used = int(words[i - 1]), used | {i - 1}
        elif i + 1 < len(words) and words[i + 1].isdigit() and int(words[i + 1]) <= 31:
            day, used = int(words[i + 1]), used | {i + 1}
        if day is None:
            return None, words
        year = None
        after = max(used) + 1
        if after < len(words) and re.fullmatch(r"\d{4}", words[after]):
            year, used = int(words[after]), used | {after}
        return _make(day, month, year, arrived), [w for j, w in enumerate(words) if j not in used]
    return None, words


def resolve_delivery_date(raw: str | None, received_at: datetime) -> date | None:
    """The date a customer's wording means, or None when it is missing or not clear."""
    if not raw or not raw.strip():
        return None
    arrived = london_date(received_at)
    words = _clean(raw).split()

    found, rest = _explicit_date(words, arrived)
    weekdays = [WEEKDAYS[w] for w in rest if w in WEEKDAYS]
    rest = [w for w in rest if w not in WEEKDAYS]
    has_next = "next" in rest
    rest = [w for w in rest if w != "next"]

    relative = None
    if "today" in rest:
        relative = arrived
        rest.remove("today")
    elif "tomorrow" in rest:
        relative = arrived + timedelta(days=1)
        rest.remove("tomorrow")

    if any(w not in FILLER for w in rest):
        return None  # words we don't understand: keep the raw wording, don't guess
    if len(weekdays) > 1:
        return None

    if found is not None:
        if relative is not None or has_next:
            return None
        if weekdays and found.weekday() != weekdays[0]:
            return None  # "Thursday 23rd" when the 23rd is a Friday: ask, don't pick one
        return found
    if relative is not None:
        if has_next or (weekdays and relative.weekday() != weekdays[0]):
            return None
        return relative
    if weekdays:
        day = _next_weekday(weekdays[0], arrived)
        if has_next and day.isocalendar()[:2] == arrived.isocalendar()[:2]:
            return None  # "next Thursday" said on a Monday could mean this week or the one after
        return day
    return None
