"""Persistence for zero-amount mixed returns; the caller owns the transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.models import TenantSettings
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.mixed_return_journal_models import MixedReturnLine
from app.domains.pos.mixed_zero_return_models import MixedZeroReturn


class MixedZeroReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> MixedZeroReturn | None:
        result: MixedZeroReturn | None = await self.session.scalar(
            select(MixedZeroReturn)
            .where(
                MixedZeroReturn.tenant_id == tenant_id, MixedZeroReturn.id == operation_id
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def lines(self, tenant_id: UUID, operation_id: UUID) -> tuple[MixedReturnLine, ...]:
        rows = await self.session.scalars(
            select(MixedReturnLine)
            .where(
                MixedReturnLine.tenant_id == tenant_id,
                MixedReturnLine.command_id == operation_id,
            )
            .order_by(MixedReturnLine.position)
            .execution_options(populate_existing=True)
        )
        return tuple(rows)

    async def get_settings_for_pos(self, tenant_id: UUID) -> TenantSettings | None:
        return await FoundationRepository(self.session).get_settings_for_pos(tenant_id)

    async def insert(self, marker: MixedZeroReturn) -> None:
        self.session.add(marker)
        await self.session.flush()
