"""Recovery acknowledges only the requested immutable decision and current branch."""

from typing import cast
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import (
    MixedDispositionStatus,
    NotFoundMixedDisposition,
)
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService
from tests.domains.customer_returns.test_mixed_disposition import TENANT
from tests.domains.customer_returns.test_mixed_disposition_service import (
    DatabaseFailure,
    WriterSession,
    acknowledgment,
)

BRANCH = UUID(int=6)
DECISION = UUID(int=10)


class StatusRepository(MixedDispositionRepository):
    def __init__(self, result: MixedDispositionStatus) -> None:
        self.result = result
        self.calls = 0

    async def read_status(
        self, *, tenant_id: UUID, branch_id: UUID, decision_operation_id: UUID
    ) -> MixedDispositionStatus:
        self.calls += 1
        assert (tenant_id, branch_id, decision_operation_id) == (TENANT, BRANCH, DECISION)
        return self.result


@pytest.mark.parametrize("found", [True, False])
async def test_repository_and_service_read_one_scoped_result_without_writer_or_history(
    found: bool,
) -> None:
    saved: MixedDispositionStatus = (
        acknowledgment()
        if found
        else NotFoundMixedDisposition(
            state="not_found", decision_operation_id=DECISION, branch_id=BRANCH
        )
    )
    session = WriterSession(saved.model_dump(mode="json"))
    service = MixedDispositionService(MixedDispositionRepository(cast(AsyncSession, session)))
    result = await service.read_status(
        tenant_id=TENANT,
        branch_id=BRANCH,
        decision_operation_id=DECISION,
        allowed_branch_ids={BRANCH},
    )
    assert result == saved
    assert len(session.calls) == 1
    sql, values = session.calls[0]
    assert sql == (
        "SELECT public.mixed_customer_return_disposition_status(:tenant,:branch,:decision)"
    )
    assert values == {"tenant": TENANT, "branch": BRANCH, "decision": DECISION}


@pytest.mark.parametrize("scope", ["empty", "other_branch"])
async def test_python_scope_denies_before_lookup_even_for_unknown_decision(scope: str) -> None:
    repo = StatusRepository(
        NotFoundMixedDisposition(
            state="not_found", decision_operation_id=DECISION, branch_id=BRANCH
        )
    )
    with pytest.raises(PermissionDeniedError):
        await MixedDispositionService(repo).read_status(
            tenant_id=TENANT,
            branch_id=BRANCH,
            decision_operation_id=DECISION,
            allowed_branch_ids=set() if scope == "empty" else {UUID(int=99)},
        )
    assert repo.calls == 0


@pytest.mark.parametrize("invalid", ["tenant", "branch", "decision", "hash", "not_found_key"])
async def test_status_revalidates_identity_and_hash_without_exposing_corrupt_data(
    invalid: str,
) -> None:
    saved: MixedDispositionStatus = acknowledgment()
    if invalid == "not_found_key":
        saved = NotFoundMixedDisposition(
            state="not_found", decision_operation_id=UUID(int=99), branch_id=BRANCH
        )
    else:
        field = {
            "tenant": "tenant_id",
            "branch": "branch_id",
            "decision": "decision_operation_id",
            "hash": "request_hash",
        }[invalid]
        saved = saved.model_copy(update={field: "0" * 64 if invalid == "hash" else UUID(int=99)})
    with pytest.raises(BusinessRuleError, match="recovery result is invalid"):
        await MixedDispositionService(StatusRepository(saved)).read_status(
            tenant_id=TENANT,
            branch_id=BRANCH,
            decision_operation_id=DECISION,
            allowed_branch_ids=None,
        )


async def test_malformed_payload_and_database_denial_have_safe_errors() -> None:
    session = WriterSession({"state": "resolved", "comment": "private corrupted comment"})
    service = MixedDispositionService(MixedDispositionRepository(cast(AsyncSession, session)))
    with pytest.raises(BusinessRuleError) as error:
        await service.read_status(
            tenant_id=TENANT,
            branch_id=BRANCH,
            decision_operation_id=DECISION,
            allowed_branch_ids=None,
        )
    assert "private" not in str(error.value) and error.value.__suppress_context__
    session.error = DBAPIError("private SQL", {}, DatabaseFailure("42501"))
    with pytest.raises(PermissionDeniedError) as denied:
        await service.read_status(
            tenant_id=TENANT,
            branch_id=BRANCH,
            decision_operation_id=DECISION,
            allowed_branch_ids=None,
        )
    assert "private" not in str(denied.value) and denied.value.__suppress_context__


def test_not_found_contract_forbids_payload_and_requires_identity() -> None:
    adapter = TypeAdapter(MixedDispositionStatus)
    for payload in (
        {"state": "not_found"},
        {"state": "not_found", "decision_operation_id": DECISION, "branch_id": BRANCH, "cost": 1},
        {"state": "prepared", "decision_operation_id": DECISION, "branch_id": BRANCH},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(payload)
