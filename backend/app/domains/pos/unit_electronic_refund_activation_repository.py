"""Caller owns the activation transaction and must persist it before external work."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.unit_electronic_refund_activation_models import UnitElectronicRefundActivation


class UnitElectronicRefundActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, tenant_id: UUID, operation_id: UUID
    ) -> UnitElectronicRefundActivation | None:
        result: UnitElectronicRefundActivation | None = await self.session.scalar(
            select(UnitElectronicRefundActivation)
            .where(
                UnitElectronicRefundActivation.tenant_id == tenant_id,
                UnitElectronicRefundActivation.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def insert(self, marker: UnitElectronicRefundActivation) -> None:
        self.session.add(marker)
        await self.session.flush()
