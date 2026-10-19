"""Atomic internal posting persistence; caller owns the outer transaction."""

from uuid import UUID

from sqlalchemy import null, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.models import Sale, SalePayment
from app.domains.pos.unit_electronic_refund_posting_models import UnitElectronicRefundPosting
from app.domains.pos.unit_electronic_refund_posting_schemas import ElectronicRefundPaymentV3
from app.domains.pos.unit_refund_payment_models import UnitRefundPaymentComponent


class UnitElectronicRefundPostingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> UnitElectronicRefundPosting | None:
        result: UnitElectronicRefundPosting | None = await self.session.scalar(
            select(UnitElectronicRefundPosting)
            .where(
                UnitElectronicRefundPosting.tenant_id == tenant_id,
                UnitElectronicRefundPosting.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def sale(self, tenant_id: UUID, sale_id: UUID) -> Sale | None:
        result: Sale | None = await self.session.scalar(
            select(Sale)
            .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
            .execution_options(populate_existing=True)
        )
        return result

    async def insert(self, marker: UnitElectronicRefundPosting) -> None:
        self.session.add(marker)
        await self.session.flush()

    async def record_payment(
        self, marker: UnitElectronicRefundPosting, part: ElectronicRefundPaymentV3
    ) -> None:
        self.session.add(
            SalePayment(
                id=part.id,
                tenant_id=marker.tenant_id,
                sale_id=marker.sale_id,
                payment_method=part.method,
                amount=part.amount,
                currency="TJS",
                operation_id=None,
                operation_hash=None,
                payment_attempt_id=None,
                metadata_json=(
                    null()
                    if part.method == "cash"
                    else {"terminal_id": part.terminal_id, "document_number": part.document_number}
                ),
            )
        )
        await self.session.flush()
        self.session.add(
            UnitRefundPaymentComponent(
                tenant_id=marker.tenant_id,
                id=part.id,
                parent_sale_id=marker.parent_sale_id,
                sale_id=marker.sale_id,
                original_payment_id=part.original_payment_id,
                physical_refund_id=None,
                mixed_refund_id=None,
                electronic_refund_id=marker.id,
                method=part.method,
                amount=part.amount,
                currency="TJS",
                created_by=marker.actor_id,
            )
        )
        await self.session.flush()

    async def payments(self, tenant_id: UUID, sale_id: UUID) -> tuple[SalePayment, ...]:
        rows = await self.session.scalars(
            select(SalePayment).where(
                SalePayment.tenant_id == tenant_id, SalePayment.sale_id == sale_id
            )
        )
        return tuple(rows)

    async def components(
        self, tenant_id: UUID, sale_id: UUID
    ) -> tuple[UnitRefundPaymentComponent, ...]:
        rows = await self.session.scalars(
            select(UnitRefundPaymentComponent).where(
                UnitRefundPaymentComponent.tenant_id == tenant_id,
                UnitRefundPaymentComponent.sale_id == sale_id,
            )
        )
        return tuple(rows)

    async def validate_completion(self) -> None:
        for mode in ("IMMEDIATE", "DEFERRED"):
            await self.session.execute(
                text(
                    "SET CONSTRAINTS unit_electronic_refund_posting_complete, "
                    "physical_return_complete, unit_refund_payment_components_complete " + mode
                )
            )
