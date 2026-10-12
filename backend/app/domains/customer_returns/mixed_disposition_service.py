"""Record decisions through the DB authorization boundary, without stock or cash calls."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import BusinessRuleError
from app.domains.customer_returns.mixed_disposition import MixedDispositionRequest
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import ResolvedMixedDisposition


class MixedDispositionService:
    def __init__(self, repository: MixedDispositionRepository) -> None:
        self.repository = repository

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
