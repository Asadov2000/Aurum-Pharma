"""Recover immutable cash evidence without quoting or changing a sale."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_checkout_schemas import (
    MixedCashOperationCompleted,
    MixedCashOperationNotFound,
    MixedCashOperationRead,
    MixedCashOperationSaved,
)
from app.domains.pos.mixed_cash_checkout_status_repository import (
    MixedCashCheckoutStatusRepository,
)
from app.domains.pos.mixed_documents import (
    MixedCheckoutResultV4,
    MixedReceiptDocumentV4,
    build_mixed_receipt,
)
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import UnitCashIntentService, cash_intent_request_hash
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_checkout_payment_service import UnitCheckoutPaymentService
from app.domains.pos.unit_contracts import CheckoutPaymentV2


class MixedCashCheckoutStatusService:
    def __init__(self, repository: MixedCashCheckoutStatusRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> MixedCashOperationRead:
        cart = UnitCartRepository(self.repository.session)
        await cart.assert_actor(tenant_id, actor_id)
        snapshot = await self.repository.read(tenant_id, sale_id, operation_id)
        if snapshot is None:
            raise NotFoundError("Mixed cash sale not found")
        sale, intent, checkout = snapshot
        branch_ids = {sale.branch_id} if allowed_branch_ids is None else allowed_branch_ids
        if sale.branch_id not in branch_ids or sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Mixed cash branch or cashier access denied")
        await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        missing = MixedCashOperationNotFound(operation_id=operation_id, sale_id=sale_id)
        if intent is None:
            if checkout is not None:
                raise AurumError("Stored mixed checkout has no cash confirmation")
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
        quote = saved.confirmation.quote
        if {line.inventory_kind for line in quote.lines} != {
            "legacy_pack",
            "physical_unit",
        }:
            if checkout is not None:
                raise AurumError("Stored mixed checkout has an incompatible confirmation")
            return missing
        if checkout is None:
            return MixedCashOperationSaved(
                operation_id=operation_id, sale_id=sale_id, saved_intent=saved
            )
        try:
            result = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            receipt = MixedReceiptDocumentV4.model_validate(checkout.receipt_payload)
            sale_receipt = MixedReceiptDocumentV4.model_validate(sale.receipt_snapshot)
        except ValidationError as error:
            raise AurumError("Stored mixed checkout is inconsistent") from error
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
            or checkout.tenant_id != tenant_id
            or checkout.id != operation_id
            or checkout.sale_id != sale.id
            or sale.status != "completed"
            or sale.sale_type != "sale"
            or sale.parent_sale_id is not None
            or sale.is_test
            or result.currency != sale.currency
            or tuple(line.model_dump(exclude={"name"}) for line in result.lines)
            != tuple(line.model_dump() for line in quote.lines)
            or result.total_amount != sale.total_amount
            or result.total_amount != request.expected_total_amount
            or result.payments
            != (CheckoutPaymentV2(id=operation_id, method="cash", amount=quote.total_amount),)
            or result.receipt_number != sale.receipt_number
            or result.receipt_sequence != sale.receipt_seq
            or result.created_at != sale.created_at
            or result.completed_at != sale.completed_at
            or result.is_test != sale.is_test
            or receipt != build_mixed_receipt(result)
            or receipt != sale_receipt
        ):
            raise AurumError("Stored mixed checkout is inconsistent")
        await UnitCheckoutPaymentService(
            UnitCheckoutPaymentRepository(self.repository.session)
        ).validate_saved(checkout.payment_journal_version, result)
        await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        return MixedCashOperationCompleted(
            operation_id=operation_id, sale_id=sale_id, saved_intent=saved, result=result
        )
