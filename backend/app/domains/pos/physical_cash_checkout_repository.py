"""Scoped persistence for the physical cash coordinator; caller owns commit."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import null, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.models import TenantCatalog
from app.domains.pos.models import Sale, SalePayment
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_documents import PhysicalReceiptDocumentV3
from app.domains.pos.repository import POSRepository


class PhysicalCashCheckoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> PhysicalCashCheckout | None:
        result: PhysicalCashCheckout | None = await self.session.scalar(
            select(PhysicalCashCheckout).where(
                PhysicalCashCheckout.tenant_id == tenant_id,
                PhysicalCashCheckout.id == operation_id,
            )
        )
        return result

    async def names(self, tenant_id: UUID, catalog_ids: set[UUID]) -> dict[UUID, str]:
        rows = await self.session.execute(
            select(TenantCatalog.id, TenantCatalog.brand_name).where(
                TenantCatalog.tenant_id == tenant_id, TenantCatalog.id.in_(catalog_ids)
            )
        )
        return {row.id: row.brand_name for row in rows}

    async def insert(self, result: PhysicalCashCheckout) -> None:
        self.session.add(result)
        await self.session.flush()

    async def allocate_receipt(self, register_id: UUID) -> tuple[int, str] | None:
        return await POSRepository(self.session).allocate_receipt_number(register_id)

    async def insert_payment(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        request_hash: str,
        amount: Decimal,
    ) -> None:
        self.session.add(
            SalePayment(
                id=operation_id,
                tenant_id=tenant_id,
                sale_id=sale_id,
                payment_method="cash",
                amount=amount,
                currency="TJS",
                operation_id=operation_id,
                operation_hash=request_hash,
                metadata_json=null(),
            )
        )
        await self.session.flush()

    async def set_total(self, sale: Sale, total: Decimal) -> None:
        sale.total_amount = total
        await self.session.flush()

    async def complete_sale(
        self,
        sale: Sale,
        receipt: PhysicalReceiptDocumentV3,
        completed_at: datetime,
    ) -> None:
        sale.status = "completed"
        sale.completed_at = completed_at
        sale.receipt_number = receipt.receipt_number
        sale.receipt_seq = receipt.receipt_sequence
        sale.receipt_snapshot = receipt.model_dump(mode="json")
        await self.session.flush()

    async def validate_completion(self) -> None:
        await self.session.execute(
            text(
                "SET CONSTRAINTS physical_checkout_complete, "
                "physical_stock_checkout_complete IMMEDIATE"
            )
        )
        await self.session.execute(
            text(
                "SET CONSTRAINTS physical_checkout_complete, "
                "physical_stock_checkout_complete DEFERRED"
            )
        )
