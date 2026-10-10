"""Persistence for zero-amount physical returns; the caller owns the transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.models import TenantSettings
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.physical_zero_return_models import PhysicalZeroReturn


class PhysicalZeroReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> PhysicalZeroReturn | None:
        result: PhysicalZeroReturn | None = await self.session.scalar(
            select(PhysicalZeroReturn).where(
                PhysicalZeroReturn.tenant_id == tenant_id, PhysicalZeroReturn.id == operation_id
            )
        )
        return result

    async def get_settings_for_pos(self, tenant_id: UUID) -> TenantSettings | None:
        return await FoundationRepository(self.session).get_settings_for_pos(tenant_id)

    async def insert(self, marker: PhysicalZeroReturn) -> None:
        self.session.add(marker)
        await self.session.flush()
