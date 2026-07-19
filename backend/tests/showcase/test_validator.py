"""Tests for the read-only showcase data validator."""

from __future__ import annotations

import json
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import validate_showcase as validator_cli
from app.showcase.validator import (
    CHECK_NAMES,
    SupportSessionRequired,
    ValidationReport,
    ValidationResult,
    render_report,
    validate_pending_showcase,
    validate_showcase,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class _FakeSession:
    def __init__(
        self,
        counts: dict[str, int] | None = None,
        *,
        support_context: bool = True,
    ) -> None:
        self.counts = counts or {}
        self.support_context = support_context
        self.statements: list[str] = []

    async def execute(self, statement: object) -> _ScalarResult:
        sql = str(statement)
        self.statements.append(sql)

        if sql.startswith("SET TRANSACTION"):
            return _ScalarResult(None)
        if "set_config('app.support_session'" in sql:
            return _ScalarResult("true")
        if "showcase:validator_context" in sql:
            return _ScalarResult(self.support_context)
        if "showcase:pending_validator_context" in sql:
            return _ScalarResult(self.support_context)

        for check_name in CHECK_NAMES:
            if f"showcase:{check_name}" in sql:
                return _ScalarResult(self.counts.get(check_name, 0))
        raise AssertionError(f"Unexpected validator statement: {sql}")


def _report(**counts: int) -> ValidationReport:
    return ValidationReport(
        results=tuple(
            ValidationResult(name=name, count=counts.get(name, 0)) for name in CHECK_NAMES
        )
    )


@pytest.mark.asyncio
async def test_validator_uses_support_read_only_snapshot_and_passes_clean_data() -> None:
    fake_session = _FakeSession()

    report = await validate_showcase(cast(AsyncSession, fake_session))

    assert report.is_valid is True
    assert report.total_violations == 0
    assert report.counts == dict.fromkeys(CHECK_NAMES, 0)
    assert fake_session.statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert "set_config('app.support_session', 'true', true)" in fake_session.statements[1]
    assert "current_setting('transaction_read_only') = 'on'" in fake_session.statements[2]


@pytest.mark.parametrize("failed_check", CHECK_NAMES)
@pytest.mark.asyncio
async def test_each_integrity_violation_fails_validation(failed_check: str) -> None:
    fake_session = _FakeSession(counts={failed_check: 2})

    report = await validate_showcase(cast(AsyncSession, fake_session))

    assert report.is_valid is False
    assert report.total_violations == 2
    assert report.counts[failed_check] == 2


@pytest.mark.asyncio
async def test_validator_rejects_non_support_context_before_checks() -> None:
    fake_session = _FakeSession(support_context=False)

    with pytest.raises(SupportSessionRequired):
        await validate_showcase(cast(AsyncSession, fake_session))

    assert len(fake_session.statements) == 3


@pytest.mark.asyncio
async def test_pending_validator_checks_uncommitted_demo_transaction() -> None:
    fake_session = _FakeSession()

    report = await validate_pending_showcase(cast(AsyncSession, fake_session))

    assert report.is_valid is True
    assert "current_database() = 'aurum_demo'" in fake_session.statements[0]
    assert "transaction_read_only') = 'off'" in fake_session.statements[0]
    assert all(not statement.startswith("SET TRANSACTION") for statement in fake_session.statements)


def test_report_contains_only_stable_aggregate_fields() -> None:
    rendered = render_report(_report(tenant_scope_mismatches=3))
    payload = cast(dict[str, object], json.loads(rendered))
    checks = cast(dict[str, object], payload["checks"])

    assert payload["status"] == "failed"
    assert payload["total_violations"] == 3
    assert checks == {name: 3 if name == "tenant_scope_mismatches" else 0 for name in CHECK_NAMES}
    assert all(type(value) is int for value in checks.values())
    assert "@" not in rendered
    assert "tenant_id" not in rendered


@pytest.mark.parametrize(
    ("report", "expected_code"),
    [(_report(), 0), (_report(sale_total_mismatches=1), 1)],
)
@pytest.mark.asyncio
async def test_cli_returns_process_status_from_report(
    report: ValidationReport,
    expected_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_validate_database() -> ValidationReport:
        return report

    monkeypatch.setattr(validator_cli, "validate_database", fake_validate_database)

    assert await validator_cli.run_cli() == expected_code
    output = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert output["total_violations"] == report.total_violations


@pytest.mark.asyncio
async def test_cli_does_not_expose_database_error_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "person@example.invalid"

    async def fail_validation() -> ValidationReport:
        raise SQLAlchemyError(secret)

    monkeypatch.setattr(validator_cli, "validate_database", fail_validation)

    assert await validator_cli.run_cli() == 2
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "reason": "database_validation_error",
        "status": "error",
    }
