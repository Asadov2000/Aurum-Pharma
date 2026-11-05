"""Immutable review storage; transaction commit belongs to the caller."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_electronic_refund_intent_models import MixedElectronicRefundIntent


class MixedElectronicRefundIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_read_committed(self) -> bool:
        isolation: object = await self.session.scalar(text("SHOW transaction_isolation"))
        return isinstance(isolation, str) and isolation == "read committed"

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
            {"identity": f"mixed-electronic-refund-review:{tenant_id}:{operation_id}"},
        )

    async def get(self, tenant_id: UUID, operation_id: UUID) -> MixedElectronicRefundIntent | None:
        result: MixedElectronicRefundIntent | None = await self.session.scalar(
            select(MixedElectronicRefundIntent)
            .where(
                MixedElectronicRefundIntent.tenant_id == tenant_id,
                MixedElectronicRefundIntent.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def insert(self, intent: MixedElectronicRefundIntent) -> None:
        self.session.add(intent)
        await self.session.flush()
