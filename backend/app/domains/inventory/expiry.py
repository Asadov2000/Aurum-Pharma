"""Shared calendar boundaries for inventory expiry zones."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

DEFAULT_EXPIRY_THRESHOLDS = {"yellow": 6, "orange": 3, "red": 1}
ExpiryStatus = Literal["expired", "red", "orange", "yellow", "normal"]


@dataclass(frozen=True, slots=True)
class ExpiryBoundaries:
    today: date
    red_until: date
    orange_until: date
    yellow_until: date


def build_expiry_boundaries(
    today: date,
    thresholds: Mapping[str, int] | None = None,
) -> ExpiryBoundaries:
    values = DEFAULT_EXPIRY_THRESHOLDS if thresholds is None else thresholds
    red = int(values.get("red", DEFAULT_EXPIRY_THRESHOLDS["red"]))
    orange = int(values.get("orange", DEFAULT_EXPIRY_THRESHOLDS["orange"]))
    yellow = int(values.get("yellow", DEFAULT_EXPIRY_THRESHOLDS["yellow"]))
    return ExpiryBoundaries(
        today=today,
        red_until=_add_months(today, red),
        orange_until=_add_months(today, orange),
        yellow_until=_add_months(today, yellow),
    )


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)
