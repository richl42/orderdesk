"""Working days: Monday to Friday, minus England and Wales bank holidays."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

# England and Wales bank holidays (gov.uk). Extend this list before 2029.
BANK_HOLIDAYS = {
    date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21), date(2025, 5, 5),
    date(2025, 5, 26), date(2025, 8, 25), date(2025, 12, 25), date(2025, 12, 26),
    date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4),
    date(2026, 5, 25), date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28),
    date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), date(2027, 5, 3),
    date(2027, 5, 31), date(2027, 8, 30), date(2027, 12, 27), date(2027, 12, 28),
    date(2028, 1, 3), date(2028, 4, 14), date(2028, 4, 17), date(2028, 5, 1),
    date(2028, 5, 29), date(2028, 8, 28), date(2028, 12, 25), date(2028, 12, 26),
}


def is_working_day(day: date) -> bool:
    return day.weekday() < 5 and day not in BANK_HOLIDAYS


def next_working_day(day: date) -> date:
    day += timedelta(days=1)
    while not is_working_day(day):
        day += timedelta(days=1)
    return day


def working_days(start: date, count: int) -> list[date]:
    """The first `count` working days starting on or after `start`."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if is_working_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def trial_day(start: date, total: int, today: date) -> int | None:
    """Which working day of the trial `today` is (1-based).

    0 means the trial has not started; a number above `total` means it has ended.
    On a weekend or holiday mid-trial, it is the number of working days done so far.
    """
    if today < start:
        return 0
    days = working_days(start, total)
    if today > days[-1]:
        return total + 1
    return sum(1 for d in days if d <= today)


def london_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LONDON).date()


def london_now() -> datetime:
    return datetime.now(LONDON)
