"""Time utilities. All datetimes in the system are UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def local_day_range(day: date, tz: str) -> tuple[datetime, datetime]:
    """Half-open UTC bounds of a local calendar day: [start, end).

    `start` is local midnight of `day`; `end` is local midnight of the next day,
    both converted to UTC. Keeps date filters sargable — `col >= start AND
    col < end` uses a plain index on the timestamptz column, unlike
    `(col AT TIME ZONE tz)::date = day` which wraps the column and defeats it.

    DST-correct: each midnight is built from its own calendar date, not by adding
    24h (Asia/Dushanbe has no DST, but other tenants might)."""
    zone = ZoneInfo(tz)
    next_day = day + timedelta(days=1)
    start_local = datetime(day.year, day.month, day.day, tzinfo=zone)
    end_local = datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
