"""Cancellation persistence and existing stock evidence; caller owns commit."""

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_ledger_v2_models import UnitStockCartCommandV2
from app.domains.pos.physical_cash_cancellation_models import PhysicalCashCancellation


class PhysicalCashCancellationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> PhysicalCashCancellation | None:
        cancellation: PhysicalCashCancellation | None = await self.session.scalar(
            select(PhysicalCashCancellation)
            .where(
                PhysicalCashCancellation.tenant_id == tenant_id,
                PhysicalCashCancellation.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return cancellation

    async def has_stock_execution(self, tenant_id: UUID, sale_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        UnitStockCartCommandV2.tenant_id == tenant_id,
                        or_(
                            UnitStockCartCommandV2.checkout_sale_id == sale_id,
                            UnitStockCartCommandV2.mixed_checkout_sale_id == sale_id,
                        ),
                    )
                )
            )
        )

    async def insert(self, cancellation: PhysicalCashCancellation) -> None:
        self.session.add(cancellation)
        await self.session.flush()
