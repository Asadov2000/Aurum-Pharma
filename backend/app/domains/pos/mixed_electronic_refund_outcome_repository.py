"""Evidence persistence; transaction and commit belong to the caller."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_electronic_refund_outcome_models import MixedElectronicRefundOutcome


class MixedElectronicRefundOutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self, tenant_id: UUID, operation_id: UUID
    ) -> tuple[MixedElectronicRefundOutcome, ...]:
        rows = await self.session.scalars(
            select(MixedElectronicRefundOutcome)
            .where(
                MixedElectronicRefundOutcome.tenant_id == tenant_id,
                MixedElectronicRefundOutcome.operation_id == operation_id,
            )
            .order_by(MixedElectronicRefundOutcome.original_payment_id)
            .execution_options(populate_existing=True)
        )
        return tuple(rows)

    async def insert(self, row: MixedElectronicRefundOutcome) -> None:
        self.session.add(row)
        await self.session.flush()
