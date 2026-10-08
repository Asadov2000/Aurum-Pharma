"""Recover immutable cash evidence without quoting or changing a sale."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.pos.physical_cash_checkout_schemas import (
    PhysicalCashOperationCompleted,
    PhysicalCashOperationNotFound,
    PhysicalCashOperationRead,
    PhysicalCashOperationSaved,
)
from app.domains.pos.physical_cash_checkout_status_repository import (
    PhysicalCashCheckoutStatusRepository,
)
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalReceiptDocumentV3,
    build_physical_receipt,
)
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import UnitCashIntentService, cash_intent_request_hash


class PhysicalCashCheckoutStatusService:
    def __init__(self, repository: PhysicalCashCheckoutStatusRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> PhysicalCashOperationRead:
        cart = UnitCartRepository(self.repository.session)
        await cart.assert_actor(tenant_id, actor_id)
        snapshot = await self.repository.read(tenant_id, sale_id, operation_id)
        if snapshot is None:
            raise NotFoundError("Physical cash sale not found")
        sale, intent, checkout = snapshot
        branch_ids = {sale.branch_id} if allowed_branch_ids is None else allowed_branch_ids
        if sale.branch_id not in branch_ids or sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Physical cash branch or cashier access denied")
        await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        missing = PhysicalCashOperationNotFound(operation_id=operation_id, sale_id=sale_id)
        if intent is None:
            if checkout is not None:
                raise AurumError("Stored physical checkout has no cash confirmation")
            return missing
        if intent.cashier_user_id != actor_id or intent.branch_id != sale.branch_id:
            return missing
        try:
            request = CashConfirmationRequestV2.model_validate(
                {
                    "operation_id": intent.id,
                    "sale_id": intent.sale_id,
                    "expected_revision_hash": intent.revision_hash,
                    "expected_total_amount": intent.expected_total_amount,
                    "cash_tendered": intent.cash_tendered,
                    "currency": intent.currency,
                }
            )
        except ValidationError as error:
            raise AurumError("Stored cash confirmation is inconsistent") from error
        saved = await UnitCashIntentService(
            UnitCashIntentRepository(self.repository.session)
        ).read_saved(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=branch_ids,
            request=request,
        )
        if (intent.tenant_id, intent.sale_id, intent.register_id, intent.shift_id) != (
            sale.tenant_id,
            sale.id,
            sale.register_id,
            sale.shift_id,
        ) or saved.confirmation.quote.sale_id != sale.id:
            raise AurumError("Stored cash confirmation differs from its sale")
        if not all(
            isinstance(line, PhysicalUnitDraftQuoteLineV2)
            for line in saved.confirmation.quote.lines
        ):
            if checkout is not None:
                raise AurumError("Stored physical checkout has an incompatible confirmation")
            return missing
        if checkout is None:
            return PhysicalCashOperationSaved(
                operation_id=operation_id, sale_id=sale_id, saved_intent=saved
            )
        try:
            result = PhysicalCheckoutResultV3.model_validate(checkout.result_payload)
            receipt = PhysicalReceiptDocumentV3.model_validate(checkout.receipt_payload)
            sale_receipt = PhysicalReceiptDocumentV3.model_validate(sale.receipt_snapshot)
        except ValidationError as error:
            raise AurumError("Stored physical checkout is inconsistent") from error
        if (
            checkout.request_hash != cash_intent_request_hash(request)
            or checkout.cashier_user_id != actor_id
            or checkout.branch_id != sale.branch_id
            or checkout.cash_intent_id != operation_id
            or checkout.stock_command_id != operation_id
            or checkout.payment_id != operation_id
            or checkout.register_id != sale.register_id
            or checkout.shift_id != sale.shift_id
            or result.operation_id != operation_id
            or result.event_id != operation_id
            or result.stock_command_id != operation_id
            or result.sale_id != sale.id
            or result.tenant_id != tenant_id
            or result.cashier_id != actor_id
            or result.branch_id != sale.branch_id
            or result.register_id != sale.register_id
            or result.shift_id != sale.shift_id
            or sale.status != "completed"
            or result.total_amount != sale.total_amount
            or result.total_amount != request.expected_total_amount
            or result.receipt_number != sale.receipt_number
            or result.receipt_sequence != sale.receipt_seq
            or result.created_at != sale.created_at
            or result.completed_at != sale.completed_at
            or result.is_test != sale.is_test
            or receipt != build_physical_receipt(result)
            or receipt != sale_receipt
        ):
            raise AurumError("Stored physical checkout is inconsistent")
        await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        return PhysicalCashOperationCompleted(
            operation_id=operation_id, sale_id=sale_id, saved_intent=saved, result=result
        )
