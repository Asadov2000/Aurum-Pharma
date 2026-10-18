"""Read source payments and persist payout facts in the caller's transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.models import SalePayment
from app.domains.pos.unit_refund_payment_models import UnitRefundPaymentComponent


class UnitRefundPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(self, component: UnitRefundPaymentComponent) -> None:
        self.session.add(component)
        await self.session.flush()

    async def payments(self, tenant_id: UUID, sale_id: UUID) -> tuple[SalePayment, ...]:
        rows = await self.session.scalars(
            select(SalePayment).where(
                SalePayment.tenant_id == tenant_id, SalePayment.sale_id == sale_id
            )
        )
        return tuple(rows)

    async def for_refund(
        self, tenant_id: UUID, refund_id: UUID, *, mixed: bool
    ) -> tuple[UnitRefundPaymentComponent, ...]:
        parent = (
            UnitRefundPaymentComponent.mixed_refund_id
            if mixed
            else UnitRefundPaymentComponent.physical_refund_id
        )
        rows = await self.session.scalars(
            select(UnitRefundPaymentComponent).where(
                UnitRefundPaymentComponent.tenant_id == tenant_id, parent == refund_id
            )
        )
        return tuple(rows)
