"""Date helpers for reports: presets (Today, This week, ...) and half-open timestamp bounds."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import timeutil
from .errors import ValidationError

PRESETS = ("Today", "Yesterday", "This week", "This month", "Last 30 days", "Custom")
MAX_DAYS = 3660


def parse_day(text: str) -> date:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("Dates must look like YYYY-MM-DD (for example 2026-10-08)!") from None


def preset_range(name: str, today: date | None = None) -> tuple[date, date]:
    today = today or timeutil.now().date()
    if name == "Today":
        return today, today
    if name == "Yesterday":
        day = today - timedelta(days=1)
        return day, day
    if name == "This week":
        return today - timedelta(days=today.weekday()), today
    if name == "This month":
        return today.replace(day=1), today
    if name == "Last 30 days":
        return today - timedelta(days=29), today
    raise ValueError(f"Unknown period: {name}")


def bounds(start_text: str, end_text: str) -> tuple[str, str, date, date]:
    """Returns (start_ts, end_ts_exclusive, start_date, end_date) for 'created_at >= start AND created_at < end'."""
    start, end = parse_day(start_text), parse_day(end_text)
    if end < start:
        raise ValidationError("The end date is before the start date!")
    if (end - start).days > MAX_DAYS:
        raise ValidationError("Please choose a range of at most 10 years.")
    return (f"{start:%Y-%m-%d} 00:00:00", f"{end + timedelta(days=1):%Y-%m-%d} 00:00:00", start, end)
