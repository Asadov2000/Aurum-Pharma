"""Physical decisions preserve trusted quantities and bind their saved identity."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.customer_returns.physical_disposition import (
    PhysicalDispositionRequest,
    prepare_physical_disposition,
)
from app.domains.customer_returns.physical_disposition_repository import (
    PhysicalDispositionRepository,
)
from app.domains.customer_returns.physical_disposition_schemas import ResolvedPhysicalDisposition
from app.domains.customer_returns.physical_disposition_service import PhysicalDispositionService
from app.domains.customer_returns.physical_schemas import PhysicalCustomerReturnRead
from tests.domains.customer_returns.test_mixed_disposition import source as mixed_source
from tests.domains.customer_returns.test_physical_disposition_helpers import request

TENANT = UUID(int=1)


def source(
    intake_kind: Literal["cash_refund", "zero_return"] = "cash_refund",
) -> PhysicalCustomerReturnRead:
    body = mixed_source(kind="physical_unit", intake_kind=intake_kind).model_dump()
    for field in ("inventory_kind", "batch_id", "legacy_quantity"):
        body.pop(field)
    return PhysicalCustomerReturnRead.model_validate(body)


def acknowledgment() -> ResolvedPhysicalDisposition:
    original = source()
    prepared = prepare_physical_disposition(
        request(original, decision_operation_id=UUID(int=10)), original, tenant_id=TENANT
    )
    return ResolvedPhysicalDisposition.model_validate(
        {
            **prepared.model_dump(),
            "state": "resolved",
            "actor_id": UUID(int=11),
            "resolved_at": datetime(2026, 10, 12, tzinfo=UTC),
        }
    )


@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
def test_preparation_preserves_full_historical_units_and_roundtrips(
    intake_kind: Literal["cash_refund", "zero_return"],
) -> None:
    original = source(intake_kind)
    chosen = request(original)
    prepared = prepare_physical_disposition(chosen, original, tenant_id=TENANT)
    assert prepared.state == "prepared" and prepared.intake_kind == intake_kind
    assert prepared.target.base_units == 31 and prepared.target.unit_label == "капс."
    assert prepared.target.lot_id == original.lot_id
    saved = acknowledgment()
    assert ResolvedPhysicalDisposition.model_validate_json(saved.model_dump_json()) == saved
    assert saved.request().decision_operation_id == UUID(int=10)


@pytest.mark.parametrize("field", ["intake_operation_id", "line_id", "lot_id"])
def test_preparation_rejects_another_confirmed_target(field: str) -> None:
    original = source()
    chosen = request(original)
    changed = chosen.model_copy(
        update={"target": chosen.target.model_copy(update={field: uuid4()})}
    )
    with pytest.raises(ConflictError):
        prepare_physical_disposition(changed, original, tenant_id=TENANT)


@pytest.mark.parametrize(
    "patch",
    [
        {"state": "prepared"},
        {"version": True},
        {"version": "1"},
        {"actor_id": None},
        {"resolved_at": "2026-10-12T00:00:00"},
        {"request_hash": "0" * 64},
        {"comment": " unnormalized "},
        {"disposition_type": "restock"},
        {"cost_amount": "0.01"},
        {"permissions": ["customer_returns.resolve"]},
    ],
)
def test_saved_contract_rejects_untrusted_or_inconsistent_facts(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResolvedPhysicalDisposition.model_validate({**acknowledgment().model_dump(), **patch})


class SavedRepository(PhysicalDispositionRepository):
    def __init__(self, result: ResolvedPhysicalDisposition) -> None:
        self.result = result
        self.calls: list[tuple[UUID, PhysicalDispositionRequest]] = []

    async def record(
        self, *, tenant_id: UUID, request: PhysicalDispositionRequest
    ) -> ResolvedPhysicalDisposition:
        self.calls.append((tenant_id, request))
        return self.result


async def test_service_sends_every_retry_to_authorized_writer() -> None:
    saved = acknowledgment()
    repository = SavedRepository(saved)
    service = PhysicalDispositionService(repository)
    chosen = saved.request()
    assert await service.resolve(tenant_id=TENANT, request=chosen) == saved
    assert await service.resolve(tenant_id=TENANT, request=chosen) == saved
    assert repository.calls == [(TENANT, chosen), (TENANT, chosen)]


@pytest.mark.parametrize("mismatch", ["tenant", "operation", "target", "hash"])
async def test_service_revalidates_saved_identity_and_payload(mismatch: str) -> None:
    saved = acknowledgment()
    chosen = saved.request()
    tenant_id = TENANT
    if mismatch == "tenant":
        tenant_id = uuid4()
    elif mismatch == "operation":
        chosen = chosen.model_copy(update={"decision_operation_id": uuid4()})
    elif mismatch == "target":
        chosen = chosen.model_copy(
            update={"target": chosen.target.model_copy(update={"line_id": uuid4()})}
        )
    else:
        saved = saved.model_copy(update={"request_hash": "0" * 64})
    with pytest.raises(BusinessRuleError):
        await PhysicalDispositionService(SavedRepository(saved)).resolve(
            tenant_id=tenant_id, request=chosen
        )
