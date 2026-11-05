"""Read mixed checkout history and persisted cursors without locks or writes."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.models import Sale


class MixedCashRefundPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[Sale, MixedCashCheckout] | None:
        row = (
            await self.session.execute(
                select(Sale, MixedCashCheckout)
                .join(
                    MixedCashCheckout,
                    and_(
                        MixedCashCheckout.tenant_id == Sale.tenant_id,
                        MixedCashCheckout.sale_id == Sale.id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == parent_sale_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return None if row is None else (row[0], row[1])

    async def cursors(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[dict[UUID, Decimal], dict[UUID, int]]:
        return await MixedReturnJournalRepository(self.session).cursors(tenant_id, parent_sale_id)
