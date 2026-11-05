"""Untrusted write-off input and database errors never grant stock or cost authority."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.inventory.physical_writeoff_repository import (
    _raise_writeoff_error,
    _saved_result,
)
from app.domains.inventory.physical_writeoff_schemas import PhysicalWriteOffRequest


def request_payload() -> dict[str, object]:
    return {
        "operation_id": str(UUID(int=1)),
        "branch_id": str(UUID(int=2)),
        "lot_id": str(UUID(int=3)),
        "reason": "damaged",
        "comment": None,
        "selection": {"closed_packs": 1, "opened_packs": []},
    }


@pytest.mark.parametrize("value", ["\x00", "bad\ud800", "x" * 2001, 42])
def test_comment_rejects_database_unsafe_or_unbounded_input(value: object) -> None:
    with pytest.raises(ValidationError):
        PhysicalWriteOffRequest.model_validate(request_payload() | {"comment": value})


def test_comment_remains_exact_for_idempotent_identity() -> None:
    payload = request_payload() | {"comment": "  Проверено\n"}
    request = PhysicalWriteOffRequest.model_validate(payload)
    assert request.comment == "  Проверено\n"
    assert PhysicalWriteOffRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("field", ["tenant_id", "actor_id", "cost_amount", "purchase_cost"])
def test_command_cannot_supply_authority_or_valuation(field: str) -> None:
    with pytest.raises(ValidationError):
        PhysicalWriteOffRequest.model_validate(request_payload() | {field: "untrusted"})


@pytest.mark.parametrize("value", [True, 1.5, "1", -1])
def test_http_selection_does_not_coerce_invalid_pack_counts(value: object) -> None:
    with pytest.raises(ValidationError):
        PhysicalWriteOffRequest.model_validate(
            request_payload() | {"selection": {"closed_packs": value}}
        )


class WriteOffDriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("private source and bound comment")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "sqlstate,expected",
    [
        ("42501", PermissionDeniedError),
        ("P0002", NotFoundError),
        ("23505", ConflictError),
        ("23514", BusinessRuleError),
        ("22P02", BusinessRuleError),
        ("22003", BusinessRuleError),
        ("22023", BusinessRuleError),
    ],
)
def test_expected_db_errors_hide_private_sql_parameters(
    sqlstate: str, expected: type[AurumError]
) -> None:
    error = DBAPIError("private SQL", {"comment": "private comment"}, WriteOffDriverError(sqlstate))
    with pytest.raises(expected) as caught:
        _raise_writeoff_error(error)
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_unexpected_database_failure_is_not_reported_as_a_success_or_missing_operation() -> None:
    error = DBAPIError(None, None, WriteOffDriverError("08006"))
    with pytest.raises(DBAPIError) as caught:
        _raise_writeoff_error(error)
    assert caught.value is error


@pytest.mark.parametrize("extra", [{"cost_amount": "10.00"}, {"created_at": "bad"}])
def test_invalid_saved_result_cannot_leak_cost_or_private_validation_details(
    extra: dict[str, object],
) -> None:
    saved = {
        "operation_id": str(UUID(int=1)),
        "branch_id": str(UUID(int=2)),
        "lot_id": str(UUID(int=3)),
        "reason": "damaged",
        "comment": "private comment",
        "base_units": "10",
        "closed_packs": "0",
        "created_at": "2026-10-16T00:00:00Z",
    }
    with pytest.raises(BusinessRuleError) as caught:
        _saved_result(saved | extra)
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__ is True
