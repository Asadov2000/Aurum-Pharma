"""Adapters bind the saved acknowledgment to the request without inventing authority."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.customer_returns.mixed_disposition import (
    MixedDispositionRequest,
    prepare_mixed_disposition,
)
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import ResolvedMixedDisposition
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService
from tests.domains.customer_returns.test_mixed_disposition import (
    TENANT,
    IntakeKind,
    InventoryKind,
    request,
    source,
)


def acknowledgment(
    *, kind: InventoryKind = "legacy_pack", intake_kind: IntakeKind = "cash_refund"
) -> ResolvedMixedDisposition:
    original = source(kind=kind, intake_kind=intake_kind)
    prepared = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    return ResolvedMixedDisposition.model_validate(
        {
            **prepared.model_dump(mode="json"),
            "state": "resolved",
            "actor_id": str(UUID(int=11)),
            "resolved_at": datetime(2026, 10, 12, tzinfo=UTC).isoformat(),
        }
    )


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
def test_saved_contract_preserves_quantity_kind_and_roundtrips(
    kind: InventoryKind, intake_kind: IntakeKind
) -> None:
    result = acknowledgment(kind=kind, intake_kind=intake_kind)
    assert result.state == "resolved"
    assert result.intake_kind == intake_kind
    assert result.target.inventory_kind == kind
    assert result.request() == request(source(kind=kind, intake_kind=intake_kind))
    assert ResolvedMixedDisposition.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        result.actor_id = uuid4()


@pytest.mark.parametrize(
    "patch",
    [
        {"state": "prepared"},
        {"version": True},
        {"version": 1.0},
        {"version": "1"},
        {"actor_id": None},
        {"resolved_at": "2026-10-12T00:00:00"},
        {"request_hash": "0" * 64},
        {"comment": ""},
        {"comment": " unnormalized "},
        {"comment": "different"},
        {"reason_code": "expired"},
        {"disposition_type": "supplier_claim"},
        {"decision_operation_id": str(UUID(int=2))},
        {"cost_amount": "0.01"},
        {"permissions": ["customer_returns.resolve"]},
    ],
)
def test_saved_contract_rejects_ambiguous_or_inconsistent_facts(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResolvedMixedDisposition.model_validate({**acknowledgment().model_dump(), **patch})


class SavedRepository(MixedDispositionRepository):
    def __init__(self, result: ResolvedMixedDisposition) -> None:
        self.result = result
        self.calls: list[tuple[UUID, MixedDispositionRequest]] = []

    async def record(
        self, *, tenant_id: UUID, request: MixedDispositionRequest
    ) -> ResolvedMixedDisposition:
        self.calls.append((tenant_id, request))
        return self.result


async def test_service_uses_writer_for_every_retry_and_preserves_original_author() -> None:
    saved = acknowledgment()
    repo = SavedRepository(saved)
    service = MixedDispositionService(repo)
    chosen = request(source())
    assert await service.resolve(tenant_id=TENANT, request=chosen) == saved
    assert await service.resolve(tenant_id=TENANT, request=chosen) == saved
    assert repo.calls == [(TENANT, chosen), (TENANT, chosen)]
    assert repo.result.actor_id == UUID(int=11)


@pytest.mark.parametrize("mismatch", ["tenant", "operation", "target", "hash", "state"])
async def test_service_revalidates_saved_identity_and_payload(mismatch: str) -> None:
    saved = acknowledgment()
    chosen = request(source())
    tenant_id = TENANT
    if mismatch == "tenant":
        tenant_id = uuid4()
    elif mismatch == "operation":
        chosen = chosen.model_copy(update={"decision_operation_id": uuid4()})
    elif mismatch == "target":
        chosen = chosen.model_copy(
            update={"target": chosen.target.model_copy(update={"line_id": uuid4()})}
        )
    elif mismatch == "hash":
        saved = saved.model_copy(update={"request_hash": "0" * 64})
    else:
        saved = saved.model_copy(update={"state": "prepared"})
    with pytest.raises(BusinessRuleError):
        await MixedDispositionService(SavedRepository(saved)).resolve(
            tenant_id=tenant_id, request=chosen
        )


async def test_invalid_request_is_rejected_before_writer_call() -> None:
    repo = SavedRepository(acknowledgment())
    malformed = request(source()).model_copy(update={"decision_operation_id": UUID(int=2)})
    with pytest.raises(ValidationError):
        await MixedDispositionService(repo).resolve(tenant_id=TENANT, request=malformed)
    assert repo.calls == []


class DatabaseFailure(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("private database details")
        self.sqlstate = sqlstate


class WriterSession:
    def __init__(self, payload: object, error: DBAPIError | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def scalar(self, statement: object, parameters: dict[str, object]) -> object:
        self.calls.append((str(statement), parameters))
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_repository_binds_only_request_fields_to_one_authorized_writer(
    kind: InventoryKind,
) -> None:
    saved = acknowledgment(kind=kind)
    session = WriterSession(saved.model_dump(mode="json"))
    result = await MixedDispositionRepository(cast(AsyncSession, session)).record(
        tenant_id=TENANT, request=request(source(kind=kind))
    )
    assert result == saved
    assert len(session.calls) == 1
    sql, values = session.calls[0]
    assert "public.record_mixed_customer_return_disposition(" in sql
    assert "mixed_customer_return_queue" not in sql
    assert set(values) == {
        "tenant",
        "decision",
        "intake",
        "line",
        "kind",
        "batch",
        "lot",
        "disposition",
        "reason",
        "comment",
    }
    assert values["batch"] == (UUID(int=4) if kind == "legacy_pack" else None)
    assert values["lot"] == (UUID(int=5) if kind == "physical_unit" else None)


@pytest.mark.parametrize(
    ("sqlstate", "error_type"),
    [
        ("42501", PermissionDeniedError),
        ("P0002", NotFoundError),
        ("23505", ConflictError),
        ("23514", BusinessRuleError),
    ],
)
async def test_database_denials_are_mapped_without_sql_or_private_details(
    sqlstate: str, error_type: type[Exception]
) -> None:
    error = DBAPIError("private SQL", {"comment": "private comment"}, DatabaseFailure(sqlstate))
    session = WriterSession(None, error)
    with pytest.raises(error_type) as raised:
        await MixedDispositionRepository(cast(AsyncSession, session)).record(
            tenant_id=TENANT, request=request(source())
        )
    assert "private" not in str(raised.value)
    assert raised.value.__suppress_context__ is True


async def test_unexpected_database_errors_are_not_misrepresented_as_business_conflicts() -> None:
    error = DBAPIError("unavailable", {}, DatabaseFailure("08006"))
    session = WriterSession(None, error)
    with pytest.raises(DBAPIError) as raised:
        await MixedDispositionRepository(cast(AsyncSession, session)).record(
            tenant_id=TENANT, request=request(source())
        )
    assert raised.value is error
