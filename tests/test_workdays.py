from datetime import date, datetime, timezone

from pipeline import workdays


def test_weekends_and_bank_holidays_are_not_working_days():
    assert workdays.is_working_day(date(2026, 10, 19))  # Monday
    assert not workdays.is_working_day(date(2026, 10, 17))  # Saturday
    assert not workdays.is_working_day(date(2026, 12, 28))  # Boxing Day substitute
    assert not workdays.is_working_day(date(2026, 8, 31))  # Summer bank holiday


def test_next_working_day_skips_weekend_and_christmas():
    assert workdays.next_working_day(date(2026, 10, 16)) == date(2026, 10, 19)
    assert workdays.next_working_day(date(2026, 12, 24)) == date(2026, 12, 29)


def test_trial_day():
    start = date(2026, 10, 12)
    assert workdays.trial_day(start, 10, date(2026, 10, 9)) == 0
    assert workdays.trial_day(start, 10, date(2026, 10, 12)) == 1
    assert workdays.trial_day(start, 10, date(2026, 10, 19)) == 6
    assert workdays.trial_day(start, 10, date(2026, 10, 18)) == 5  # Sunday counts days done so far
    assert workdays.trial_day(start, 10, date(2026, 10, 23)) == 10
    assert workdays.trial_day(start, 10, date(2026, 10, 26)) == 11  # finished


def test_london_date_handles_late_evening_utc_in_summer():
    moment = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)  # 00:30 on 2 July in London
    assert workdays.london_date(moment) == date(2026, 7, 2)
