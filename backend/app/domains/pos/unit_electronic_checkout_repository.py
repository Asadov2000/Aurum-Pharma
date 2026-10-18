"""Scoped persistence for the internal physical electronic checkout coordinator."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import null, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domains.pos.models import POSPaymentAttempt, Sale, SalePayment
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_documents import PhysicalReceiptDocumentV3
from app.domains.pos.repository import POSRepository
from app.domains.pos.unit_contracts import CheckoutPaymentV2
from app.domains.pos.unit_payment_attempt_models import UnitPaymentAttemptBinding


class UnitElectronicCheckoutRepository(PhysicalCashCheckoutRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def sale(self, tenant_id: UUID, sale_id: UUID, *, lock: bool) -> Sale | None:
        statement = select(Sale).where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result: Sale | None = await self.session.scalar(statement)
        return result

    async def bindings(
        self, tenant_id: UUID, intent_id: UUID
    ) -> tuple[UnitPaymentAttemptBinding, ...]:
        rows = await self.session.scalars(
            select(UnitPaymentAttemptBinding)
            .where(
                UnitPaymentAttemptBinding.tenant_id == tenant_id,
                UnitPaymentAttemptBinding.intent_id == intent_id,
            )
            .order_by(UnitPaymentAttemptBinding.method)
        )
        return tuple(rows)

    async def insert_binding(self, binding: UnitPaymentAttemptBinding) -> None:
        self.session.add(binding)
        await self.session.flush()

    async def lock_attempt_ids(self, tenant_id: UUID, ids: set[UUID]) -> None:
        for attempt_id in sorted(ids, key=str):
            await POSRepository(self.session).lock_operation_id(
                tenant_id=tenant_id, operation_id=attempt_id
            )

    async def require_unused_attempt_ids(self, tenant_id: UUID, ids: set[UUID]) -> None:
        for attempt_id in sorted(ids, key=str):
            if await POSRepository(self.session).get_pos_operation_kinds(
                tenant_id=tenant_id, operation_id=attempt_id
            ):
                raise ConflictError("Attempt identifier is already used by another POS operation")

    async def attempts(
        self, tenant_id: UUID, sale_id: UUID, *, lock: bool
    ) -> tuple[POSPaymentAttempt, ...]:
        statement = (
            select(POSPaymentAttempt)
            .where(POSPaymentAttempt.tenant_id == tenant_id, POSPaymentAttempt.sale_id == sale_id)
            .order_by(POSPaymentAttempt.id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        return tuple(await self.session.scalars(statement))

    async def payments(self, tenant_id: UUID, sale_id: UUID) -> tuple[SalePayment, ...]:
        return tuple(
            await self.session.scalars(
                select(SalePayment).where(
                    SalePayment.tenant_id == tenant_id, SalePayment.sale_id == sale_id
                )
            )
        )

    async def insert_plan_payment(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        request_hash: str,
        payment: CheckoutPaymentV2,
    ) -> None:
        self.session.add(
            SalePayment(
                id=payment.id,
                tenant_id=tenant_id,
                sale_id=sale_id,
                payment_method=payment.method,
                amount=payment.amount,
                currency="TJS",
                operation_id=payment.id if payment.method == "cash" else uuid4(),
                operation_hash=request_hash,
                payment_attempt_id=None if payment.method == "cash" else payment.id,
                metadata_json=null(),
            )
        )
        await self.session.flush()

    async def create_attempt(self, binding: UnitPaymentAttemptBinding, operation_hash: str) -> None:
        await POSRepository(self.session).insert_payment_attempt(
            id=binding.id,
            tenant_id=binding.tenant_id,
            sale_id=binding.sale_id,
            cashier_user_id=binding.cashier_user_id,
            operation_id=binding.id,
            operation_hash=operation_hash,
            payment_method=binding.method,
            amount=binding.amount,
            currency=binding.currency,
            status="pending",
            evidence_required=True,
            created_by=binding.cashier_user_id,
        )

    async def validate_attempt_bindings(self) -> None:
        await self.session.execute(
            text("SET CONSTRAINTS unit_payment_attempt_binding_complete IMMEDIATE")
        )
        await self.session.execute(
            text("SET CONSTRAINTS unit_payment_attempt_binding_complete DEFERRED")
        )

    async def validate_completion(self) -> None:
        await self.validate_attempt_bindings()
        await super().validate_completion()

    async def complete_sale(
        self,
        sale: Sale,
        receipt: PhysicalReceiptDocumentV3,
        completed_at: datetime,
    ) -> None:
        # The frozen draft permits one final UPDATE with the exact stored total
        # and receipt. Never change its total separately before completion.
        sale.total_amount = receipt.total_amount
        await super().complete_sale(sale, receipt, completed_at)
