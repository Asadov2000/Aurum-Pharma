"""Read original checkout history and committed return quantities without row locks."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository


class PhysicalCashRefundPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[Sale, PhysicalCashCheckout] | None:
        row = (
            await self.session.execute(
                select(Sale, PhysicalCashCheckout)
                .join(
                    PhysicalCashCheckout,
                    and_(
                        PhysicalCashCheckout.tenant_id == Sale.tenant_id,
                        PhysicalCashCheckout.sale_id == Sale.id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == parent_sale_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return None if row is None else (row[0], row[1])

    async def cursors(self, tenant_id: UUID, stock_command_id: UUID) -> dict[UUID, int]:
        return await PhysicalReturnJournalRepository(self.session).cursors(
            tenant_id, stock_command_id
        )
