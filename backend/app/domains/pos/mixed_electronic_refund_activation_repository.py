"""Caller owns the activation transaction and must persist it before external work."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_electronic_refund_activation_models import (
    MixedElectronicRefundActivation,
)


class MixedElectronicRefundActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, tenant_id: UUID, operation_id: UUID
    ) -> MixedElectronicRefundActivation | None:
        result: MixedElectronicRefundActivation | None = await self.session.scalar(
            select(MixedElectronicRefundActivation)
            .where(
                MixedElectronicRefundActivation.tenant_id == tenant_id,
                MixedElectronicRefundActivation.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def insert(self, marker: MixedElectronicRefundActivation) -> None:
        self.session.add(marker)
        await self.session.flush()
