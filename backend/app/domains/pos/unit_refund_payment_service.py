"""Bind current cash payouts to their immutable original payment."""

from app.core.errors import AurumError
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.models import SalePayment
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.unit_refund_payment_models import UnitRefundPaymentComponent
from app.domains.pos.unit_refund_payment_repository import UnitRefundPaymentRepository

Refund = PhysicalCashRefundResultV3 | MixedCashRefundResultV4


class UnitRefundPaymentService:
    def __init__(self, repository: UnitRefundPaymentRepository) -> None:
        self.repository = repository

    async def _source(self, result: Refund) -> SalePayment:
        payments = await self.repository.payments(result.tenant_id, result.parent_sale_id)
        if len(payments) != 1:
            raise AurumError("Original cash refund payment is inconsistent")
        source = payments[0]
        if (
            source.tenant_id != result.tenant_id
            or source.sale_id != result.parent_sale_id
            or source.payment_method != "cash"
            or source.currency != "TJS"
            or source.payment_attempt_id is not None
            or not 0 < result.total_amount <= source.amount
        ):
            raise AurumError("Original cash refund payment is inconsistent")
        return source

    async def record_cash(self, result: Refund) -> None:
        # Both refund coordinators already hold the original sale lock. The DB
        # independently checks the cumulative limit, including legacy payouts.
        source = await self._source(result)
        mixed = isinstance(result, MixedCashRefundResultV4)
        await self.repository.insert(
            UnitRefundPaymentComponent(
                tenant_id=result.tenant_id,
                id=result.payment_id,
                parent_sale_id=result.parent_sale_id,
                sale_id=result.sale_id,
                original_payment_id=source.id,
                physical_refund_id=None if mixed else result.operation_id,
                mixed_refund_id=result.operation_id if mixed else None,
                method="cash",
                amount=result.total_amount,
                currency="TJS",
                created_by=result.actor_id,
            )
        )

    async def validate_saved(self, version: int, result: Refund) -> None:
        if version == 1:
            # Read historical refunds without creating payment-component history.
            return
        if version != 2:
            raise AurumError("Stored refund payment journal version is unsupported")
        source = await self._source(result)
        mixed = isinstance(result, MixedCashRefundResultV4)
        components = await self.repository.for_refund(
            result.tenant_id, result.operation_id, mixed=mixed
        )
        payouts = await self.repository.payments(result.tenant_id, result.sale_id)
        if len(components) != 1 or len(payouts) != 1:
            raise AurumError("Stored refund payment journal is inconsistent")
        component, payout = components[0], payouts[0]
        if (
            component.tenant_id != result.tenant_id
            or component.id != result.payment_id
            or component.parent_sale_id != result.parent_sale_id
            or component.sale_id != result.sale_id
            or component.original_payment_id != source.id
            or component.physical_refund_id != (None if mixed else result.operation_id)
            or component.mixed_refund_id != (result.operation_id if mixed else None)
            or component.method != "cash"
            or component.amount != result.total_amount
            or component.currency != "TJS"
            or component.created_by != result.actor_id
            or payout.tenant_id != result.tenant_id
            or payout.id != result.payment_id
            or payout.sale_id != result.sale_id
            or payout.payment_method != "cash"
            or payout.amount != result.total_amount
            or payout.currency != "TJS"
            or payout.payment_attempt_id is not None
        ):
            raise AurumError("Stored refund payment journal is inconsistent")
