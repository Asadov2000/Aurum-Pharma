"""Time utilities. All datetimes in the system are UTC."""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
