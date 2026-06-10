"""local_day_range — half-open UTC bounds of a local calendar day."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.core.time import local_day_range


def test_dushanbe_offset_plus_five() -> None:
    # Asia/Dushanbe is UTC+5 (no DST): local 2026-06-08 = [06-07 19:00Z, 06-08 19:00Z).
    start, end = local_day_range(date(2026, 6, 8), "Asia/Dushanbe")
    assert start == datetime(2026, 6, 7, 19, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 8, 19, 0, tzinfo=UTC)


def test_utc_zone_is_midnight_to_midnight() -> None:
    start, end = local_day_range(date(2026, 6, 8), "UTC")
    assert start == datetime(2026, 6, 8, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 9, 0, 0, tzinfo=UTC)


def test_month_rollover() -> None:
    start, end = local_day_range(date(2026, 6, 30), "Asia/Dushanbe")
    assert start == datetime(2026, 6, 29, 19, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 30, 19, 0, tzinfo=UTC)  # next local day = 07-01 00:00+05


def test_year_rollover() -> None:
    start, end = local_day_range(date(2026, 12, 31), "Asia/Dushanbe")
    assert start == datetime(2026, 12, 30, 19, 0, tzinfo=UTC)
    assert end == datetime(2026, 12, 31, 19, 0, tzinfo=UTC)  # 2027-01-01 00:00+05


def test_range_is_exactly_24h_when_no_dst() -> None:
    start, end = local_day_range(date(2026, 6, 8), "Asia/Dushanbe")
    assert (end - start).total_seconds() == 24 * 3600
