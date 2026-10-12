"""Read and record decisions through the DB boundary, without stock or cash calls."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import BusinessRuleError
from app.domains.customer_returns.mixed_disposition import MixedDispositionRequest
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import (
    MixedDispositionHistory,
    ResolvedMixedDisposition,
)


class MixedDispositionService:
    def __init__(self, repository: MixedDispositionRepository) -> None:
        self.repository = repository

    async def list_history(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> MixedDispositionHistory:
        try:
            empty = MixedDispositionHistory(items=[], total=0, page=page, page_size=page_size)
        except ValidationError:
            raise BusinessRuleError("Mixed quarantine history pagination is invalid") from None
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
            result = MixedDispositionHistory(
                items=items, total=total, page=page, page_size=page_size
            )
        except ValidationError:
            raise BusinessRuleError("Saved mixed quarantine history is invalid") from None
        if any(
            item.tenant_id != tenant_id
            or (branch_id is not None and item.branch_id != branch_id)
            or (allowed_branch_ids is not None and item.branch_id not in allowed_branch_ids)
            for item in result.items
        ):
            raise BusinessRuleError("Saved mixed quarantine history is invalid")
        return result

    async def resolve(
        self, *, tenant_id: UUID, request: MixedDispositionRequest
    ) -> ResolvedMixedDisposition:
        # Prepared/browser display data is never used as authority or quantity source.
        request = MixedDispositionRequest.model_validate(request.model_dump())
        try:
            result = await self.repository.record(tenant_id=tenant_id, request=request)
            result = ResolvedMixedDisposition.model_validate(result.model_dump())
        except ValidationError:
            raise BusinessRuleError("Saved mixed quarantine decision is invalid") from None
        if result.tenant_id != tenant_id or result.request() != request:
            raise BusinessRuleError("Saved mixed quarantine decision does not match the request")
        return result
