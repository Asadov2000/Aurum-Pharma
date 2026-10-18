"""Atomic internal cash payout paired with physical quarantine history."""

import json
import re
from decimal import Decimal, localcontext
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
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection, plan_physical_return
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalResult
from app.domains.pos.physical_return_journal_service import (
    PhysicalReturnJournalService,
    physical_return_fingerprint,
)
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_refund_payment_repository import UnitRefundPaymentRepository
from app.domains.pos.unit_refund_payment_service import UnitRefundPaymentService


def _normalize_reason(
    reason_code: str | None, comment: str | None
) -> tuple[str | None, str | None]:
    # The established refund normalizer is shared without introducing its eager
    # POS router imports into this internal module's import path.
    from app.domains.pos.service import POSService

    if (reason_code is not None and not isinstance(reason_code, str)) or (
        comment is not None and not isinstance(comment, str)
    ):
        raise BusinessRuleError("Refund reason and comment must be text")
    return POSService._normalize_refund_intent(reason_code, comment)


def _validate_expected_amount(expected: Decimal | None) -> None:
    if expected is None:
        return
    with localcontext(EXACT_CONTEXT):
        if (
            not isinstance(expected, Decimal)
            or not expected.is_finite()
            or not Decimal("0") < expected <= Decimal("999999999999.99")
            or expected != expected.quantize(Decimal("0.01"))
        ):
            raise BusinessRuleError("Reviewed refund amount must be positive money in whole cents")


def _check_expected_amount(actual: Decimal, expected: Decimal | None) -> None:
    if expected is not None and actual != expected:
        raise ConflictError("Refund amount changed; review the return again")


async def _replay(
    repository: PhysicalCashRefundRepository,
    existing: PhysicalCashRefund,
    fingerprint: str,
    expected: Decimal | None,
) -> PhysicalCashRefundResultV3:
    if existing.request_hash != fingerprint:
        raise ConflictError("Refund operation was used for a different request")
    result = PhysicalCashRefundResultV3.model_validate(existing.result_payload)
    _check_expected_amount(result.total_amount, expected)
    await UnitRefundPaymentService(UnitRefundPaymentRepository(repository.session)).validate_saved(
        existing.payment_journal_version, result
    )
    await PhysicalReturnJournalRepository(repository.session).assert_actor(
        result.tenant_id, result.actor_id, result.branch_id
    )
    return result


def physical_cash_refund_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    shift_id: UUID,
    selections: tuple[PhysicalReturnSelection, ...],
    *,
    reason_code: str | None = None,
    comment: str | None = None,
    review_hash: str | None = None,
) -> str:
    if review_hash is not None and (
        not isinstance(review_hash, str) or re.fullmatch(r"[0-9a-f]{64}", review_hash) is None
    ):
        raise BusinessRuleError("Refund review hash is invalid")
    payload: dict[str, object] = {
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
    reason_code, comment = _normalize_reason(reason_code, comment)
    if reason_code is not None or comment is not None or review_hash is not None:
        payload.update(
            version=4,
            reason_code=reason_code,
            comment_hash=(
                sha256(comment.encode("utf-8")).hexdigest() if comment is not None else None
            ),
            review_hash=review_hash,
        )
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
        reason_code: str | None = None,
        comment: str | None = None,
        expected_total_amount: Decimal | None = None,
        expected_revision_hash: str | None = None,
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
        reason_code, comment = _normalize_reason(reason_code, comment)
        _validate_expected_amount(expected_total_amount)
        fingerprint = physical_cash_refund_fingerprint(
            tenant_id,
            actor_id,
            parent_sale_id,
            shift_id,
            selections,
            reason_code=reason_code,
            comment=comment,
            review_hash=expected_revision_hash,
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
                return await _replay(self.repository, existing, fingerprint, expected_total_amount)
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
            received = await self._receive_reviewed(
                journal=journal,
                original=PhysicalCheckoutResultV3.model_validate(checkout.result_payload),
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                selections=selections,
                reason_code=reason_code,
                comment=comment,
                expected_total_amount=expected_total_amount,
                expected_revision_hash=expected_revision_hash,
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
                    payment_journal_version=2,
                    reason_code=reason_code,
                    comment=comment,
                    review_hash=expected_revision_hash,
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
            await self._complete_payout(sale, result)
            return result

    async def _complete_payout(self, sale: Sale, result: PhysicalCashRefundResultV3) -> None:
        await UnitRefundPaymentService(
            UnitRefundPaymentRepository(self.repository.session)
        ).record_cash(result)
        await self.repository.complete_sale(sale, result.receipt)
        await self.repository.validate_completion()
        await PhysicalReturnJournalRepository(self.repository.session).assert_actor(
            result.tenant_id, result.actor_id, result.branch_id
        )

    async def _receive_reviewed(
        self,
        *,
        journal: PhysicalReturnJournalRepository,
        original: PhysicalCheckoutResultV3,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[PhysicalReturnSelection, ...],
        reason_code: str | None,
        comment: str | None,
        expected_total_amount: Decimal | None,
        expected_revision_hash: str | None,
    ) -> PhysicalReturnJournalResult:
        # Caller holds the original sale lock. Policy remains stable until commit.
        settings = await self.repository.get_settings_for_pos(tenant_id)
        if settings is None:
            raise AurumError("POS settings are unavailable")
        if settings.refund_reason_mode == "off" and (
            reason_code is not None or comment is not None
        ):
            raise BusinessRuleError("Refund reason fields are disabled")
        if (
            settings.refund_reason_mode in {"required", "required_with_text"}
            and reason_code is None
        ):
            raise BusinessRuleError("Refund reason is required")
        if settings.refund_reason_mode == "required_with_text" and comment is None:
            raise BusinessRuleError("Refund comment is required")
        from app.domains.pos.physical_cash_refund_review import physical_refund_revision

        cursors = await journal.cursors(tenant_id, original.stock_command_id)
        if (
            expected_revision_hash is not None
            and expected_revision_hash != physical_refund_revision(original, cursors, selections)
        ):
            raise ConflictError("Refund history changed; review the return again")
        try:
            plan = plan_physical_return(
                original,
                selections,
                returned_base_units=cursors,
            )
        except ValueError as error:
            raise BusinessRuleError(str(error)) from error
        _check_expected_amount(plan.total_amount, expected_total_amount)
        return await PhysicalReturnJournalService(journal).receive(
            tenant_id=tenant_id,
            actor_id=actor_id,
            operation_id=operation_id,
            parent_sale_id=parent_sale_id,
            selections=selections,
        )
