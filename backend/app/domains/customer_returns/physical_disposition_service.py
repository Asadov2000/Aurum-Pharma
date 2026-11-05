"""Read and record decisions through the DB boundary, without stock or cash calls."""

from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.customer_returns.physical_disposition import PhysicalDispositionRequest
from app.domains.customer_returns.physical_disposition_repository import (
    PhysicalDispositionRepository,
)
from app.domains.customer_returns.physical_disposition_schemas import (
    PhysicalDispositionHistory,
    PhysicalDispositionStatus,
    ResolvedPhysicalDisposition,
)


class PhysicalDispositionService:
    def __init__(self, repository: PhysicalDispositionRepository) -> None:
        self.repository = repository

    async def read_status(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        decision_operation_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> PhysicalDispositionStatus:
        if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Physical quarantine recovery permission denied")
        try:
            result = await self.repository.read_status(
                tenant_id=tenant_id,
                branch_id=branch_id,
                decision_operation_id=decision_operation_id,
            )
            adapter: TypeAdapter[PhysicalDispositionStatus] = TypeAdapter(PhysicalDispositionStatus)
            result = adapter.validate_python(result.model_dump())
        except ValidationError:
            raise BusinessRuleError(
                "Saved physical quarantine recovery result is invalid"
            ) from None
        if (
            result.branch_id != branch_id
            or result.decision_operation_id != decision_operation_id
            or (isinstance(result, ResolvedPhysicalDisposition) and result.tenant_id != tenant_id)
        ):
            raise BusinessRuleError("Saved physical quarantine recovery result is invalid")
        return result

    async def list_history(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> PhysicalDispositionHistory:
        try:
            empty = PhysicalDispositionHistory(items=[], total=0, page=page, page_size=page_size)
        except ValidationError:
            raise BusinessRuleError("Physical quarantine history pagination is invalid") from None
        if allowed_branch_ids is not None and (
            not allowed_branch_ids
            or (branch_id is not None and branch_id not in allowed_branch_ids)
        ):
            return empty
        try:
            items, total = await self.repository.list_history(
                tenant_id=tenant_id,
                branch_id=branch_id,
                allowed_branch_ids=allowed_branch_ids,
                page=page,
                page_size=page_size,
            )
            result = PhysicalDispositionHistory(
                items=items, total=total, page=page, page_size=page_size
            )
        except ValidationError:
            raise BusinessRuleError("Saved physical quarantine history is invalid") from None
        if any(
            item.tenant_id != tenant_id
            or (branch_id is not None and item.branch_id != branch_id)
            or (allowed_branch_ids is not None and item.branch_id not in allowed_branch_ids)
            for item in result.items
        ):
            raise BusinessRuleError("Saved physical quarantine history is invalid")
        if result.items:
            try:
                result = PhysicalDispositionHistory.model_validate(
                    {
                        **result.model_dump(),
                        "report_timezone": await self.repository.history_timezone(tenant_id),
                    }
                )
            except ValidationError:
                raise BusinessRuleError("Saved physical quarantine history is invalid") from None
        return result

    async def resolve(
        self, *, tenant_id: UUID, request: PhysicalDispositionRequest
    ) -> ResolvedPhysicalDisposition:
        # Prepared/browser display data is never used as authority or quantity source.
        request = PhysicalDispositionRequest.model_validate(request.model_dump())
        try:
            result = await self.repository.record(tenant_id=tenant_id, request=request)
            result = ResolvedPhysicalDisposition.model_validate(result.model_dump())
        except ValidationError:
            raise BusinessRuleError("Saved physical quarantine decision is invalid") from None
        if result.tenant_id != tenant_id or result.request() != request:
            raise BusinessRuleError("Saved physical quarantine decision does not match the request")
        return result
