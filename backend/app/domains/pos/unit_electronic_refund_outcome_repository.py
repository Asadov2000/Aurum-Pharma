"""Evidence persistence; transaction and commit belong to the caller."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.unit_electronic_refund_outcome_models import UnitElectronicRefundOutcome


class UnitElectronicRefundOutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self, tenant_id: UUID, operation_id: UUID
    ) -> tuple[UnitElectronicRefundOutcome, ...]:
        rows = await self.session.scalars(
            select(UnitElectronicRefundOutcome)
            .where(
                UnitElectronicRefundOutcome.tenant_id == tenant_id,
                UnitElectronicRefundOutcome.operation_id == operation_id,
            )
            .order_by(UnitElectronicRefundOutcome.original_payment_id)
            .execution_options(populate_existing=True)
        )
        return tuple(rows)

    async def insert(self, row: UnitElectronicRefundOutcome) -> None:
        self.session.add(row)
        await self.session.flush()
