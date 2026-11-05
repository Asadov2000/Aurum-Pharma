"""Read an immutable refund and its linked sales in a single statement."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund


class PhysicalCashRefundStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID, operation_id: UUID
    ) -> tuple[Sale, PhysicalCashRefund | None, Sale | None] | None:
        returned_sale = aliased(Sale)
        row = (
            await self.session.execute(
                select(Sale, PhysicalCashRefund, returned_sale)
                .outerjoin(
                    PhysicalCashRefund,
                    and_(
                        PhysicalCashRefund.tenant_id == Sale.tenant_id,
                        PhysicalCashRefund.parent_sale_id == Sale.id,
                        PhysicalCashRefund.id == operation_id,
                    ),
                )
                .outerjoin(
                    returned_sale,
                    and_(
                        returned_sale.tenant_id == PhysicalCashRefund.tenant_id,
                        returned_sale.id == PhysicalCashRefund.sale_id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == parent_sale_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1], row[2]
