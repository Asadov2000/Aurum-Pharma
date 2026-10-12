"""History reads preserve saved facts and fail closed on invalid projection data."""

from collections.abc import Iterator
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.customer_returns.mixed_disposition import _disposition_hash
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import (
    MixedDispositionHistory,
    ResolvedMixedDisposition,
)
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService
from tests.domains.customer_returns.test_mixed_disposition import TENANT, IntakeKind, InventoryKind
from tests.domains.customer_returns.test_mixed_disposition_service import (
    DatabaseFailure,
    acknowledgment,
)

BRANCH = UUID(int=6)


class HistoryRepository(MixedDispositionRepository):
    def __init__(self, items: list[ResolvedMixedDisposition], total: int = 1) -> None:
        self.items = items
        self.total = total
        self.calls: list[dict[str, object]] = []

    async def list_history(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ResolvedMixedDisposition], int]:
        self.calls.append(
            {
                "tenant": tenant_id,
                "branch": branch_id,
                "allowed": allowed_branch_ids,
                "page": page,
                "page_size": page_size,
            }
        )
        return self.items, self.total


class HistoryRows:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = payloads

    def scalars(self) -> Iterator[object]:
        return iter(self.payloads)


class HistorySession:
    def __init__(
        self, payloads: list[object], error: DBAPIError | None = None, *, fail_page: bool = False
    ) -> None:
        self.payloads = payloads
        self.error = error
        self.fail_page = fail_page
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def scalar(self, statement: object, parameters: dict[str, object]) -> int:
        self.calls.append((str(statement), parameters))
        if self.error is not None and not self.fail_page:
            raise self.error
        return 7

    async def execute(self, statement: object, parameters: dict[str, object]) -> HistoryRows:
        self.calls.append((str(statement), parameters))
        if self.error is not None and self.fail_page:
            raise self.error
        return HistoryRows(self.payloads)


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
async def test_history_service_preserves_whole_saved_snapshot_and_original_author(
    kind: InventoryKind, intake_kind: IntakeKind
) -> None:
    saved = acknowledgment(kind=kind, intake_kind=intake_kind)
    repo = HistoryRepository([saved], total=7)
    result = await MixedDispositionService(repo).list_history(
        tenant_id=TENANT, branch_id=BRANCH, allowed_branch_ids={BRANCH}, page=2, page_size=1
    )
    assert result == MixedDispositionHistory(items=[saved], total=7, page=2, page_size=1)
    assert MixedDispositionHistory.model_validate_json(result.model_dump_json()) == result
    assert repo.calls == [
        {"tenant": TENANT, "branch": BRANCH, "allowed": {BRANCH}, "page": 2, "page_size": 1}
    ]


@pytest.mark.parametrize("scope", ["empty", "other_branch"])
async def test_service_and_repository_skip_queries_outside_explicit_branch_scope(
    scope: str,
) -> None:
    allowed = set() if scope == "empty" else {UUID(int=99)}
    repo = HistoryRepository([acknowledgment()])
    result = await MixedDispositionService(repo).list_history(
        tenant_id=TENANT, branch_id=BRANCH, allowed_branch_ids=allowed, page=3, page_size=20
    )
    assert result.items == [] and result.total == 0 and result.page == 3
    assert repo.calls == []
    session = HistorySession([])
    rows, total = await MixedDispositionRepository(cast(AsyncSession, session)).list_history(
        tenant_id=TENANT, branch_id=BRANCH, allowed_branch_ids=allowed, page=3, page_size=20
    )
    assert rows == [] and total == 0 and session.calls == []


@pytest.mark.parametrize(
    "invalid", ["tenant", "branch", "allowed_branch", "hash", "comment", "total"]
)
async def test_history_service_revalidates_saved_models_and_rejects_corruption(
    invalid: str,
) -> None:
    saved = acknowledgment()
    branch_id: UUID | None = BRANCH
    allowed = {BRANCH}
    if invalid == "tenant":
        other = UUID(int=99)
        saved = saved.model_copy(
            update={
                "tenant_id": other,
                "request_hash": _disposition_hash(saved.request(), tenant_id=other),
            }
        )
    elif invalid in {"branch", "allowed_branch"}:
        saved = saved.model_copy(update={"branch_id": UUID(int=99)})
        if invalid == "allowed_branch":
            branch_id = None
    elif invalid == "hash":
        saved = saved.model_copy(update={"request_hash": "0" * 64})
    elif invalid == "comment":
        saved = saved.model_copy(update={"comment": "private corrupted comment"})
    repo = HistoryRepository([saved], total=-1 if invalid == "total" else 1)
    with pytest.raises(
        BusinessRuleError, match="Saved mixed quarantine history is invalid"
    ) as error:
        await MixedDispositionService(repo).list_history(
            tenant_id=TENANT, branch_id=branch_id, allowed_branch_ids=allowed, page=1, page_size=20
        )
    assert "private" not in str(error.value) and not error.value.details


@pytest.mark.parametrize(
    ("field", "value"),
    [("page", 0), ("page", True), ("page_size", 0), ("page_size", 201), ("page_size", "20")],
)
async def test_invalid_pagination_is_rejected_before_database_read(
    field: str, value: object
) -> None:
    repo = HistoryRepository([])
    arguments = {"page": 1, "page_size": 20, field: value}
    with pytest.raises(BusinessRuleError, match="pagination is invalid"):
        await MixedDispositionService(repo).list_history(
            tenant_id=TENANT,
            branch_id=None,
            allowed_branch_ids=None,
            page=cast(int, arguments["page"]),
            page_size=cast(int, arguments["page_size"]),
        )
    assert repo.calls == []


@pytest.mark.parametrize("scoped", [False, True])
async def test_repository_uses_only_scoped_function_and_orders_pages_by_time_then_uuid(
    scoped: bool,
) -> None:
    saved = acknowledgment()
    session = HistorySession([saved.model_dump(mode="json")])
    allowed = {BRANCH, UUID(int=5)} if scoped else None
    rows, total = await MixedDispositionRepository(cast(AsyncSession, session)).list_history(
        tenant_id=TENANT, branch_id=None, allowed_branch_ids=allowed, page=3, page_size=2
    )
    assert rows == [saved] and total == 7 and len(session.calls) == 2
    count_sql, count_parameters = session.calls[0]
    page_sql, page_parameters = session.calls[1]
    for sql in (count_sql, page_sql):
        assert "public.mixed_customer_return_disposition_history(:tenant,:branch)" in sql
        assert "FROM public.mixed_customer_return_disposition " not in sql
        assert "record_mixed_customer_return_disposition" not in sql
        clause = "WHERE CAST(payload->>'branch_id' AS UUID)=ANY(CAST(:allowed AS UUID[]))"
        assert (clause in sql) == scoped
    assert count_sql.startswith("SELECT count(*)")
    assert "ORDER BY CAST(payload->>'resolved_at' AS TIMESTAMPTZ) DESC," in page_sql
    assert "CAST(payload->>'decision_operation_id' AS UUID) LIMIT :limit OFFSET :offset" in page_sql
    expected: dict[str, object] = {"tenant": TENANT, "branch": None}
    if scoped:
        expected["allowed"] = [UUID(int=5), BRANCH]
    assert count_parameters == expected
    assert page_parameters == {**expected, "limit": 2, "offset": 4}


async def test_service_converts_malformed_database_payload_to_safe_error() -> None:
    session = HistorySession([{"comment": "private corrupted payload", "cost_amount": "999.00"}])
    service = MixedDispositionService(MixedDispositionRepository(cast(AsyncSession, session)))
    with pytest.raises(
        BusinessRuleError, match="Saved mixed quarantine history is invalid"
    ) as error:
        await service.list_history(
            tenant_id=TENANT, branch_id=None, allowed_branch_ids=None, page=1, page_size=20
        )
    assert "private" not in str(error.value) and "999.00" not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    ("sqlstate", "error_type"),
    [("42501", PermissionDeniedError), ("22P02", BusinessRuleError), ("22007", BusinessRuleError)],
    ids=["permission", "invalid_uuid", "invalid_time"],
)
async def test_history_database_denials_and_corrupt_sort_fields_have_safe_errors(
    sqlstate: str, error_type: type[Exception]
) -> None:
    failure = DBAPIError("private SQL", {}, DatabaseFailure(sqlstate))
    session = HistorySession([], error=failure, fail_page=sqlstate != "42501")
    with pytest.raises(error_type) as error:
        await MixedDispositionRepository(cast(AsyncSession, session)).list_history(
            tenant_id=TENANT, branch_id=None, allowed_branch_ids=None, page=1, page_size=20
        )
    assert "private" not in str(error.value) and error.value.__suppress_context__ is True


async def test_empty_page_keeps_total_and_unexpected_database_error_propagates() -> None:
    session = HistorySession([])
    service = MixedDispositionService(MixedDispositionRepository(cast(AsyncSession, session)))
    result = await service.list_history(
        tenant_id=TENANT, branch_id=None, allowed_branch_ids=None, page=99, page_size=20
    )
    assert result.items == [] and result.total == 7 and result.page == 99
    failure = DBAPIError("unavailable", {}, DatabaseFailure("08006"))
    session.error = failure
    with pytest.raises(DBAPIError) as error:
        await service.list_history(
            tenant_id=TENANT, branch_id=None, allowed_branch_ids=None, page=1, page_size=20
        )
    assert error.value is failure


def test_history_contract_rejects_extra_fields_and_negative_totals() -> None:
    for patch in ({"total": -1}, {"cost_amount": "0.01"}, {"total": True}):
        with pytest.raises(ValidationError):
            MixedDispositionHistory.model_validate(
                {"items": [], "total": 0, "page": 1, "page_size": 200, **patch}
            )
