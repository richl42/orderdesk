"""Delivery dates are worked out by code from the customer's words, and unclear words give no date."""

from datetime import date, datetime

import pytest

from pipeline.dates import resolve_delivery_date
from pipeline.workdays import LONDON

MONDAY_MORNING = datetime(2026, 10, 12, 9, 14, tzinfo=LONDON)


@pytest.mark.parametrize("words, expected", [
    ("Thursday", date(2026, 10, 15)),
    ("thurs", date(2026, 10, 15)),
    ("on Thursday please", date(2026, 10, 15)),
    ("Thursday morning", date(2026, 10, 15)),
    ("this Thursday", date(2026, 10, 15)),
    ("Monday", date(2026, 10, 19)),  # strictly after the day it arrived
    ("tomorrow", date(2026, 10, 13)),
    ("tomorrow (Tuesday)", date(2026, 10, 13)),
    ("today", date(2026, 10, 12)),
    ("22/10/2026", date(2026, 10, 22)),
    ("22/10/26", date(2026, 10, 22)),
    ("22.10.2026", date(2026, 10, 22)),
    ("2026-10-22", date(2026, 10, 22)),
    ("22/10", date(2026, 10, 22)),
    ("22nd October", date(2026, 10, 22)),
    ("22 Oct 2026", date(2026, 10, 22)),
    ("October 22nd", date(2026, 10, 22)),
    ("Friday 16 October", date(2026, 10, 16)),
    ("Thursday 22nd", None),  # no month
    ("by Friday 16th October", date(2026, 10, 16)),
    ("10/10", date(2026, 10, 10)),  # in the past: kept, so the past-date check catches it
])
def test_dates_from_a_monday_morning(words, expected):
    assert resolve_delivery_date(words, MONDAY_MORNING) == expected


@pytest.mark.parametrize("words", [
    "", None, "asap", "as normal", "next week", "end of the week", "Thursday or Friday",
    "Thursday 23rd October",  # the 23rd is a Friday: ask, don't pick one
    "next Thursday",  # said on a Monday: this week or next?
    "tomorrow Thursday",  # tomorrow is Tuesday
    "31/02/2026",
])
def test_unclear_dates_give_no_date(words):
    assert resolve_delivery_date(words, MONDAY_MORNING) is None


def test_next_weekday_when_it_can_only_mean_the_following_week():
    friday = datetime(2026, 10, 16, 10, 0, tzinfo=LONDON)
    assert resolve_delivery_date("next Tuesday", friday) == date(2026, 10, 20)


def test_counts_from_the_london_day_not_utc():
    late = datetime(2026, 10, 12, 23, 30, tzinfo=LONDON)  # 22:30 UTC, still Monday in London
    assert resolve_delivery_date("tomorrow", late) == date(2026, 10, 13)
    just_after_midnight = datetime(2026, 10, 13, 0, 20, tzinfo=LONDON)  # 23:20 UTC Monday
    assert resolve_delivery_date("tomorrow", just_after_midnight) == date(2026, 10, 14)


def test_day_and_month_near_new_year_take_the_closest_year():
    december = datetime(2026, 12, 21, 9, 0, tzinfo=LONDON)
    assert resolve_delivery_date("04/01", december) == date(2027, 1, 4)
    assert resolve_delivery_date("5th January", december) == date(2027, 1, 5)
