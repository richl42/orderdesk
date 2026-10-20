"""Formatting helpers for the templates: London times, money, durations."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from pipeline.vocab import STATUS_LABELS, Status
from pipeline.workdays import LONDON


def london(moment: datetime | None, fmt: str = "%H:%M") -> str:
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LONDON).strftime(fmt)


def moment(value: datetime | None) -> str:
    """'Mon 12 Oct 2026, 09:14' in London time."""
    if value is None:
        return ""
    local = value.astimezone(LONDON)
    return f"{local:%a} {local.day} {local:%b %Y}, {local:%H:%M}"


def long_date(day: date | None) -> str:
    if day is None:
        return ""
    return f"{day:%a} {day.day} {day:%b %Y}"


def money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"£{value:,.2f}"


def qty(value: Decimal | None) -> str:
    """10 -> '10', 2.50 -> '2.5'."""
    if value is None:
        return ""
    return format(value.normalize(), "f") if value != value.to_integral_value() else str(int(value))


def duration(seconds: float | int | None) -> str:
    """41 -> '41s', 125 -> '2m 05s', 7300 -> '2h 01m'."""
    if seconds is None:
        return "n/a"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def ago(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return ""
    now = now or datetime.now(timezone.utc)
    minutes = max(0, int((now - moment).total_seconds() // 60))
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def percent(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    value = 100 * part / whole
    return f"{value:.0f}%" if value in (0, 100) else f"{value:.1f}%"


def status_label(status: str | None) -> str:
    if not status:
        return "Reading"
    return STATUS_LABELS.get(Status(status), status)


def status_class(status: str | None) -> str:
    return f"pill-{(status or 'reading').replace('_', '-')}"
