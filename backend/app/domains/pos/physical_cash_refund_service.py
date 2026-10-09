"""Atomic internal cash payout paired with physical quarantine history."""

import json
from hashlib import sha256
from uuid import UUID, uuid4

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_schemas import (
    PhysicalCashRefundLineV3,
    PhysicalCashRefundReceiptV3,
    PhysicalCashRefundResultV3,
)
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_service import (
    PhysicalReturnJournalService,
    physical_return_fingerprint,
)


def physical_cash_refund_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    shift_id: UUID,
    selections: tuple[PhysicalReturnSelection, ...],
) -> str:
    payload = {
        "version": 3,
        "kind": "physical_cash_refund",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "shift_id": str(shift_id),
        "journal_request_hash": physical_return_fingerprint(
            tenant_id, actor_id, parent_sale_id, selections
        ),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PhysicalCashRefundService:
    def __init__(self, repository: PhysicalCashRefundRepository) -> None:
        self.repository = repository

    async def refund(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        shift_id: UUID,
        selections: tuple[PhysicalReturnSelection, ...],
    ) -> PhysicalCashRefundResultV3:
        if not all(
            isinstance(value, UUID)
            for value in (tenant_id, actor_id, operation_id, parent_sale_id, shift_id)
        ):
            raise BusinessRuleError("Typed refund identifiers are required")
        tenant_id, actor_id, operation_id, parent_sale_id, shift_id = (
            UUID(str(value))
            for value in (tenant_id, actor_id, operation_id, parent_sale_id, shift_id)
        )
        if type(selections) is not tuple or any(
            type(s) is not PhysicalReturnSelection or not isinstance(s.line_id, UUID)
            for s in selections
        ):
            raise BusinessRuleError("Typed return selections are required")
        selections = tuple(
            PhysicalReturnSelection(UUID(str(s.line_id)), s.sale_format, s.quantity)
            for s in selections
        )
        fingerprint = physical_cash_refund_fingerprint(
            tenant_id, actor_id, parent_sale_id, shift_id, selections
        )
        journal = PhysicalReturnJournalRepository(self.repository.session)
        checkout = await journal.get_checkout(tenant_id, parent_sale_id)
        if checkout is None:
            raise NotFoundError("Original physical checkout not found")
        await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
        async with self.repository.session.begin_nested():
            await journal.lock_operation(tenant_id, operation_id)
            parent = await journal.lock_sale(tenant_id, parent_sale_id)
            await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
            if parent is None or parent.status != "completed" or parent.sale_type != "sale":
                raise BusinessRuleError("Completed original physical sale required")
            existing = await self.repository.get(tenant_id, operation_id)
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise ConflictError("Refund operation was used for a different request")
                return PhysicalCashRefundResultV3.model_validate(existing.result_payload)
            if await journal.get_command(tenant_id, operation_id) is not None:
                raise ConflictError("Return operation already exists without a cash refund")
            shift = await self.repository.lock_shift(tenant_id, shift_id)
            if shift is None or (
                shift.status,
                shift.branch_id,
                shift.register_id,
                shift.opened_by_user_id,
                shift.currency,
            ) != ("open", parent.branch_id, parent.register_id, actor_id, "TJS"):
                raise BusinessRuleError("Refund requires your open shift on the original register")
            if not await self.repository.lock_active_register(
                tenant_id, parent.branch_id, parent.register_id
            ):
                raise BusinessRuleError("Refund requires an active original register")
            received = await PhysicalReturnJournalService(journal).receive(
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                selections=selections,
            )
            if received.total_amount <= 0:
                raise BusinessRuleError(
                    "Cash refund requires a positive payout; use quarantine for zero-value goods"
                )
            original = PhysicalCheckoutResultV3.model_validate(checkout.result_payload)
            financial = PhysicalCashCheckoutRepository(self.repository.session)
            allocation = await financial.allocate_receipt(parent.register_id)
            if allocation is None:
                raise AurumError("Physical refund receipt allocation failed")
            sequence, number = allocation
            completed_at = utc_now()
            source = {line.id: line for line in original.lines}
            selection = {line.line_id: line for line in selections}
            receipt = PhysicalCashRefundReceiptV3(
                parent_sale_id=parent_sale_id,
                parent_receipt_number=original.receipt_number,
                receipt_number=number,
                receipt_sequence=sequence,
                completed_at=completed_at,
                is_test=parent.is_test,
                total_amount=received.total_amount,
                lines=tuple(
                    PhysicalCashRefundLineV3(
                        original_line_id=line.line_id,
                        position=position,
                        name=source[line.line_id].name,
                        packaging=source[line.line_id].packaging,
                        original_sale_format=source[line.line_id].sale_format,
                        original_retail_unit_price=source[line.line_id].original_retail_unit_price,
                        sale_format=selection[line.line_id].sale_format,
                        quantity=selection[line.line_id].quantity,
                        base_units=line.base_units,
                        offset=line.offset,
                        gross_amount=line.gross_amount,
                        discount_amount=line.discount_amount,
                        net_amount=line.net_amount,
                    )
                    for position, line in enumerate(received.lines, 1)
                ),
            )
            sale = Sale(
                id=uuid4(),
                tenant_id=tenant_id,
                branch_id=parent.branch_id,
                register_id=parent.register_id,
                shift_id=shift_id,
                cashier_user_id=actor_id,
                sale_type="return",
                parent_sale_id=parent_sale_id,
                status="draft",
                is_test=parent.is_test,
                total_amount=received.total_amount,
                currency="TJS",
                operation_id=operation_id,
                operation_hash=fingerprint,
            )
            await self.repository.create_sale(sale)
            result = PhysicalCashRefundResultV3(
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                sale_id=sale.id,
                tenant_id=tenant_id,
                branch_id=parent.branch_id,
                register_id=parent.register_id,
                shift_id=shift_id,
                actor_id=actor_id,
                payment_id=operation_id,
                total_amount=received.total_amount,
                total_cost_amount=received.total_cost_amount,
                receipt=receipt,
            )
            await self.repository.insert(
                PhysicalCashRefund(
                    tenant_id=tenant_id,
                    id=operation_id,
                    parent_sale_id=parent_sale_id,
                    sale_id=sale.id,
                    branch_id=parent.branch_id,
                    register_id=parent.register_id,
                    shift_id=shift_id,
                    actor_id=actor_id,
                    payment_id=operation_id,
                    request_hash=fingerprint,
                    result_payload=result.model_dump(mode="json"),
                    receipt_payload=receipt.model_dump(mode="json"),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            await self.repository.insert_payment(
                tenant_id=tenant_id,
                sale_id=sale.id,
                payment_id=operation_id,
                amount=received.total_amount,
            )
            await self.repository.complete_sale(sale, receipt)
            await self.repository.validate_completion()
            await journal.assert_actor(tenant_id, actor_id, parent.branch_id)
            return result
