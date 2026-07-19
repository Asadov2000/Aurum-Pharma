"""Calendar-date boundaries for the audit API."""

from datetime import UTC, date, datetime

from app.domains.audit.service import audit_date_bounds


def test_calendar_day_is_resolved_in_the_pharmacy_timezone() -> None:
    start, end_inclusive, end_exclusive = audit_date_bounds(
        date_from=date(2026, 7, 19),
        date_to=date(2026, 7, 19),
        report_timezone="Asia/Dushanbe",
    )

    assert start == datetime(2026, 7, 18, 19, 0, tzinfo=UTC)
    assert end_inclusive is None
    assert end_exclusive == datetime(2026, 7, 19, 19, 0, tzinfo=UTC)


def test_date_range_handles_dst_by_resolving_each_midnight() -> None:
    start, _, end_exclusive = audit_date_bounds(
        date_from=date(2026, 11, 1),
        date_to=date(2026, 11, 1),
        report_timezone="America/New_York",
    )

    assert start == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert end_exclusive == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


def test_invalid_timezone_falls_back_to_the_pilot_timezone() -> None:
    start, _, end_exclusive = audit_date_bounds(
        date_from=date(2026, 7, 19),
        date_to=date(2026, 7, 19),
        report_timezone="not/a-timezone",
    )

    assert start == datetime(2026, 7, 18, 19, 0, tzinfo=UTC)
    assert end_exclusive == datetime(2026, 7, 19, 19, 0, tzinfo=UTC)


def test_legacy_datetime_filter_remains_inclusive() -> None:
    legacy_from = datetime(2026, 7, 19, 0, 0, tzinfo=UTC)
    legacy_to = datetime(2026, 7, 19, 23, 59, tzinfo=UTC)

    start, end_inclusive, end_exclusive = audit_date_bounds(
        date_from=legacy_from,
        date_to=legacy_to,
        report_timezone="Asia/Dushanbe",
    )

    assert start == legacy_from
    assert end_inclusive == legacy_to
    assert end_exclusive is None
