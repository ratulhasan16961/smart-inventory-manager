"""Time helpers. Services call ``timeutil.now()`` so tests can freeze the clock."""
from __future__ import annotations

from datetime import datetime

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    return datetime.now()


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).strftime(TS_FORMAT)


def parse(text: str) -> datetime:
    return datetime.strptime(text, TS_FORMAT)