"""Persistence for atomic physical cash refunds; caller owns commit."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import null, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.models import Register, TenantSettings
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.models import Sale, SalePayment, Shift
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundReceiptV3


class PhysicalCashRefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> PhysicalCashRefund | None:
        result: PhysicalCashRefund | None = await self.session.scalar(
            select(PhysicalCashRefund).where(
                PhysicalCashRefund.tenant_id == tenant_id, PhysicalCashRefund.id == operation_id
            )
        )
        return result

    async def get_settings_for_pos(self, tenant_id: UUID) -> TenantSettings | None:
        return await FoundationRepository(self.session).get_settings_for_pos(tenant_id)

    async def lock_shift(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        result: Shift | None = await self.session.scalar(
            select(Shift)
            .where(Shift.tenant_id == tenant_id, Shift.id == shift_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def create_sale(self, sale: Sale) -> None:
        self.session.add(sale)
        await self.session.flush()

    async def lock_active_register(
        self, tenant_id: UUID, branch_id: UUID, register_id: UUID
    ) -> bool:
        result = await self.session.scalar(
            select(Register.id)
            .where(
                Register.tenant_id == tenant_id,
                Register.branch_id == branch_id,
                Register.id == register_id,
                Register.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
        return result is not None

    async def insert(self, marker: PhysicalCashRefund) -> None:
        self.session.add(marker)
        await self.session.flush()

    async def insert_payment(
        self, *, tenant_id: UUID, sale_id: UUID, payment_id: UUID, amount: Decimal
    ) -> None:
        self.session.add(
            SalePayment(
                id=payment_id,
                tenant_id=tenant_id,
                sale_id=sale_id,
                payment_method="cash",
                amount=amount,
                currency="TJS",
                operation_id=None,
                operation_hash=None,
                metadata_json=null(),
            )
        )
        await self.session.flush()

    async def complete_sale(self, sale: Sale, receipt: PhysicalCashRefundReceiptV3) -> None:
        sale.status = "completed"
        sale.completed_at = receipt.completed_at
        sale.receipt_number = receipt.receipt_number
        sale.receipt_seq = receipt.receipt_sequence
        sale.receipt_snapshot = receipt.model_dump(mode="json")
        await self.session.flush()

    async def validate_completion(self) -> None:
        await self.session.execute(
            text(
                "SET CONSTRAINTS unit_refund_component_complete, "
                "unit_refund_payment_components_complete IMMEDIATE"
            )
        )
        await self.session.execute(
            text(
                "SET CONSTRAINTS unit_refund_component_complete, "
                "unit_refund_payment_components_complete DEFERRED"
            )
        )
        await self.session.execute(text("SET CONSTRAINTS physical_cash_refund_complete IMMEDIATE"))
        await self.session.execute(text("SET CONSTRAINTS physical_cash_refund_complete DEFERRED"))
