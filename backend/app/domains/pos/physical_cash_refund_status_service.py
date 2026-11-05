"""Recover a cashier's completed payout without writing or re-quoting it."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.pos.physical_cash_refund_read import physical_cash_refund_snapshot
from app.domains.pos.physical_cash_refund_schemas import (
    PhysicalCashRefundReceiptV3,
    PhysicalCashRefundResultV3,
)
from app.domains.pos.physical_cash_refund_status_repository import (
    PhysicalCashRefundStatusRepository,
)
from app.domains.pos.physical_cash_refund_status_schemas import (
    PhysicalRefundOperationCompleted,
    PhysicalRefundOperationNotFound,
    PhysicalRefundOperationRead,
)
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_refund_payment_repository import UnitRefundPaymentRepository
from app.domains.pos.unit_refund_payment_service import UnitRefundPaymentService


class PhysicalCashRefundStatusService:
    def __init__(self, repository: PhysicalCashRefundStatusRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> PhysicalRefundOperationRead:
        authority = PhysicalReturnJournalRepository(self.repository.session)
        # The unscoped check validates the real app identity, without requiring pos.sell.
        await UnitCartRepository(self.repository.session).assert_actor(tenant_id, actor_id)
        snapshot = await self.repository.read(tenant_id, parent_sale_id, operation_id)
        if snapshot is None:
            raise NotFoundError("Physical refund parent sale not found")
        parent, marker, returned_sale = snapshot
        if allowed_branch_ids is not None and parent.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Physical refund branch access denied")
        await authority.assert_actor(tenant_id, actor_id, parent.branch_id)
        if marker is None or marker.actor_id != actor_id:
            return PhysicalRefundOperationNotFound(
                operation_id=operation_id, parent_sale_id=parent_sale_id
            )
        if returned_sale is None:
            raise AurumError("Stored physical refund has no receipt sale")
        try:
            result = PhysicalCashRefundResultV3.model_validate(marker.result_payload)
            receipt = PhysicalCashRefundReceiptV3.model_validate(marker.receipt_payload)
        except ValidationError as error:
            raise AurumError("Stored physical refund is inconsistent") from error
        if (
            parent.status != "completed"
            or parent.sale_type != "sale"
            or marker.tenant_id != tenant_id
            or marker.parent_sale_id != parent_sale_id
            or marker.id != operation_id
            or marker.branch_id != parent.branch_id
            or marker.register_id != parent.register_id
            or marker.payment_id != operation_id
            or result.operation_id != operation_id
            or result.parent_sale_id != parent_sale_id
            or result.tenant_id != tenant_id
            or result.sale_id != returned_sale.id
            or result.sale_id != marker.sale_id
            or result.branch_id != marker.branch_id
            or result.register_id != marker.register_id
            or result.shift_id != marker.shift_id
            or result.actor_id != actor_id
            or result.payment_id != marker.payment_id
            or returned_sale.tenant_id != tenant_id
            or returned_sale.branch_id != marker.branch_id
            or returned_sale.register_id != marker.register_id
            or returned_sale.shift_id != marker.shift_id
            or returned_sale.cashier_user_id != actor_id
            or returned_sale.operation_id != operation_id
            or returned_sale.operation_hash != marker.request_hash
            or returned_sale.total_amount != result.total_amount
            or receipt.parent_receipt_number != parent.receipt_number
            or receipt.is_test != parent.is_test
            or receipt != result.receipt
            or receipt != physical_cash_refund_snapshot(returned_sale)
        ):
            raise AurumError("Stored physical refund is inconsistent")
        await UnitRefundPaymentService(
            UnitRefundPaymentRepository(self.repository.session)
        ).validate_saved(marker.payment_journal_version, result)
        await authority.assert_actor(tenant_id, actor_id, parent.branch_id)
        return PhysicalRefundOperationCompleted(
            operation_id=operation_id,
            parent_sale_id=parent_sale_id,
            sale_id=result.sale_id,
            total_amount=result.total_amount,
            receipt=receipt,
        )
