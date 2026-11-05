"""Post physical warehouse write-offs without cash, sales or local stock mutations."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import BusinessRuleError
from app.domains.inventory.physical_writeoff_repository import PhysicalWriteOffRepository
from app.domains.inventory.physical_writeoff_schemas import (
    PhysicalWriteOffRead,
    PhysicalWriteOffRequest,
    PhysicalWriteOffStatus,
)


class PhysicalWriteOffService:
    def __init__(self, repo: PhysicalWriteOffRepository) -> None:
        self.repo = repo

    async def post(
        self, *, tenant_id: UUID, actor_id: UUID, request: PhysicalWriteOffRequest
    ) -> PhysicalWriteOffRead:
        try:
            request = PhysicalWriteOffRequest.model_validate(request.model_dump())
        except ValidationError:
            raise BusinessRuleError("Invalid physical write-off request") from None
        # The DB function authorizes every call and replay, then calculates and
        # journals under locks. An earlier preview cannot authorize a mutation.
        return await self.repo.post(tenant_id=tenant_id, actor_id=actor_id, request=request)

    async def cancel(
        self, *, tenant_id: UUID, actor_id: UUID, request: PhysicalWriteOffRequest
    ) -> PhysicalWriteOffStatus:
        try:
            request = PhysicalWriteOffRequest.model_validate(request.model_dump())
        except ValidationError:
            raise BusinessRuleError("Invalid physical write-off request") from None
        return await self.repo.cancel(tenant_id=tenant_id, actor_id=actor_id, request=request)

    async def read(
        self, *, tenant_id: UUID, branch_id: UUID, operation_id: UUID
    ) -> PhysicalWriteOffStatus:
        return await self.repo.read(
            tenant_id=tenant_id, branch_id=branch_id, operation_id=operation_id
        )
