from __future__ import annotations

from datetime import date

from app.domains.inventory.expiry import build_expiry_boundaries


def test_expiry_boundaries_use_calendar_months_at_month_end() -> None:
    boundaries = build_expiry_boundaries(
        date(2025, 1, 31),
        {"red": 1, "orange": 3, "yellow": 6},
    )

    assert boundaries.today == date(2025, 1, 31)
    assert boundaries.red_until == date(2025, 2, 28)
    assert boundaries.orange_until == date(2025, 4, 30)
    assert boundaries.yellow_until == date(2025, 7, 31)


def test_expiry_boundaries_preserve_leap_day_when_possible() -> None:
    boundaries = build_expiry_boundaries(
        date(2024, 2, 29),
        {"red": 12, "orange": 24, "yellow": 36},
    )

    assert boundaries.red_until == date(2025, 2, 28)
    assert boundaries.orange_until == date(2026, 2, 28)
    assert boundaries.yellow_until == date(2027, 2, 28)
