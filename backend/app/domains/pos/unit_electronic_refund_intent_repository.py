"""Immutable review storage; transaction commit belongs to the caller."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.unit_electronic_refund_intent_models import UnitElectronicRefundIntent


class UnitElectronicRefundIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
            {"identity": f"unit-electronic-refund-review:{tenant_id}:{operation_id}"},
        )

    async def get(self, tenant_id: UUID, operation_id: UUID) -> UnitElectronicRefundIntent | None:
        result: UnitElectronicRefundIntent | None = await self.session.scalar(
            select(UnitElectronicRefundIntent)
            .where(
                UnitElectronicRefundIntent.tenant_id == tenant_id,
                UnitElectronicRefundIntent.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def insert(self, intent: UnitElectronicRefundIntent) -> None:
        self.session.add(intent)
        await self.session.flush()
