"""Internal atomic cash payout paired with mixed quarantine history."""

import json
import re
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.pos.mixed_cash_refund_models import MixedCashRefund
from app.domains.pos.mixed_cash_refund_repository import MixedCashRefundRepository
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_cash_refund_schemas import (
    MixedCashRefundLineV4,
    MixedCashRefundReceiptV4,
    MixedCashRefundResultV4,
    MixedLegacyCashRefundLineV4,
    MixedPhysicalCashRefundLineV4,
)
from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnSelection,
    MixedReturnSelection,
    plan_mixed_return,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import (
    LegacyReturnJournalLine,
    MixedReturnJournalResult,
)
from app.domains.pos.mixed_return_journal_service import (
    MixedReturnJournalService,
    mixed_return_fingerprint,
)
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_refund_service import (
    _check_expected_amount,
    _normalize_reason,
    _validate_expected_amount,
)
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection


def mixed_cash_refund_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    shift_id: UUID,
    selections: tuple[MixedReturnSelection, ...],
    *,
    reason_code: str | None = None,
    comment: str | None = None,
    review_hash: str | None = None,
) -> str:
    if review_hash is not None and (
        not isinstance(review_hash, str) or re.fullmatch(r"[0-9a-f]{64}", review_hash) is None
    ):
        raise BusinessRuleError("Refund review hash is invalid")
    reason_code, comment = _normalize_reason(reason_code, comment)
    payload = {
        "version": 4,
        "kind": "mixed_cash_refund",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "shift_id": str(shift_id),
        "journal_request_hash": mixed_return_fingerprint(
            tenant_id, actor_id, parent_sale_id, selections
        ),
        "reason_code": reason_code,
        "comment_hash": (
            sha256(comment.encode("utf-8")).hexdigest() if comment is not None else None
        ),
        "review_hash": review_hash,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _replay(
    existing: MixedCashRefund, fingerprint: str, expected: Decimal | None
) -> MixedCashRefundResultV4:
    if existing.request_hash != fingerprint:
        raise ConflictError("Refund operation was used for a different request")
    result = MixedCashRefundResultV4.model_validate(existing.result_payload)
    _check_expected_amount(result.total_amount, expected)
    return result


def _receipt(
    original: MixedCheckoutResultV4,
    received: MixedReturnJournalResult,
    sequence: int,
    number: str,
) -> MixedCashRefundReceiptV4:
    source = {line.id: line for line in original.lines}
    lines: list[MixedCashRefundLineV4] = []
    for position, line in enumerate(received.lines, 1):
        historical = source[line.line_id]
        if isinstance(line, LegacyReturnJournalLine):
            if not isinstance(historical, LegacyResultLineV4):
                raise AurumError("Mixed refund source kind differs")
            lines.append(
                MixedLegacyCashRefundLineV4(
                    original_line_id=line.line_id,
                    position=position,
                    original_position=line.position,
                    name=historical.name,
                    original_retail_unit_price=historical.original_retail_unit_price,
                    quantity=line.quantity,
                    offset=line.offset,
                    gross_amount=line.gross_amount,
                    discount_amount=line.discount_amount,
                    net_amount=line.net_amount,
                )
            )
        else:
            if isinstance(historical, LegacyResultLineV4):
                raise AurumError("Mixed refund source kind differs")
            lines.append(
                MixedPhysicalCashRefundLineV4(
                    original_line_id=line.line_id,
                    position=position,
                    original_position=line.position,
                    name=historical.name,
                    packaging=historical.packaging,
                    original_sale_format=historical.sale_format,
                    original_retail_unit_price=historical.original_retail_unit_price,
                    sale_format=line.sale_format,
                    quantity=line.quantity,
                    base_units=line.base_units,
                    offset=line.offset,
                    gross_amount=line.gross_amount,
                    discount_amount=line.discount_amount,
                    net_amount=line.net_amount,
                )
            )
    return MixedCashRefundReceiptV4(
        parent_sale_id=original.sale_id,
        parent_receipt_number=original.receipt_number,
        receipt_number=number,
        receipt_sequence=sequence,
        completed_at=utc_now(),
        is_test=False,
        total_amount=received.total_amount,
        lines=tuple(lines),
    )


class MixedCashRefundService:
    def __init__(self, repository: MixedCashRefundRepository) -> None:
        self.repository = repository

    async def refund(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        shift_id: UUID,
        selections: tuple[MixedReturnSelection, ...],
        reason_code: str | None = None,
        comment: str | None = None,
        expected_total_amount: Decimal | None = None,
        expected_revision_hash: str | None = None,
    ) -> MixedCashRefundResultV4:
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
            type(selection) not in (LegacyReturnSelection, PhysicalReturnSelection)
            or not isinstance(selection.line_id, UUID)
            for selection in selections
        ):
            raise BusinessRuleError("Typed mixed return selections are required")
        selections = tuple(
            LegacyReturnSelection(UUID(str(selection.line_id)), selection.quantity)
            if isinstance(selection, LegacyReturnSelection)
            else PhysicalReturnSelection(
                UUID(str(selection.line_id)), selection.sale_format, selection.quantity
            )
            for selection in selections
        )
        reason_code, comment = _normalize_reason(reason_code, comment)
        _validate_expected_amount(expected_total_amount)
        fingerprint = mixed_cash_refund_fingerprint(
            tenant_id,
            actor_id,
            parent_sale_id,
            shift_id,
            selections,
            reason_code=reason_code,
            comment=comment,
            review_hash=expected_revision_hash,
        )
        journal = MixedReturnJournalRepository(self.repository.session)
        await journal.assert_identity(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            await journal.lock_operation(tenant_id, operation_id)
            existing = await self.repository.get(tenant_id, operation_id)
            if existing is not None:
                await journal.assert_actor(tenant_id, actor_id, existing.branch_id)
                return _replay(existing, fingerprint, expected_total_amount)
            if await journal.get_command(tenant_id, operation_id) is not None:
                raise ConflictError("Return operation already exists without a cash refund")
            checkout = await journal.get_checkout(tenant_id, parent_sale_id)
            if checkout is None:
                raise NotFoundError("Original mixed checkout not found")
            await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
            parent = await journal.lock_sale(tenant_id, parent_sale_id)
            await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
            if parent is None or parent.status != "completed" or parent.sale_type != "sale":
                raise BusinessRuleError("Completed original mixed sale required")
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
            original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            received = await self._receive_reviewed(
                journal=journal,
                original=original,
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
            financial = PhysicalCashCheckoutRepository(self.repository.session)
            allocation = await financial.allocate_receipt(parent.register_id)
            if allocation is None:
                raise AurumError("Mixed refund receipt allocation failed")
            sequence, number = allocation
            receipt = _receipt(original, received, sequence, number)
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
                is_test=False,
                total_amount=received.total_amount,
                currency="TJS",
                operation_id=operation_id,
                operation_hash=fingerprint,
            )
            result = MixedCashRefundResultV4(
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
                physical_cost_amount=received.physical_cost_amount,
                receipt=receipt,
            )
            # The mixed-parent boundary requires a validated marker before even
            # a draft Sale. Its deferred FK/completion guard forbids partial commit.
            await self.repository.insert(
                MixedCashRefund(
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
                    reason_code=reason_code,
                    comment=comment,
                    review_hash=expected_revision_hash,
                    result_payload=result.model_dump(mode="json"),
                    receipt_payload=receipt.model_dump(mode="json"),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            await self.repository.create_sale(sale)
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

    async def _receive_reviewed(
        self,
        *,
        journal: MixedReturnJournalRepository,
        original: MixedCheckoutResultV4,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[MixedReturnSelection, ...],
        reason_code: str | None,
        comment: str | None,
        expected_total_amount: Decimal | None,
        expected_revision_hash: str | None,
    ) -> MixedReturnJournalResult:
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
        legacy, physical = await journal.cursors(tenant_id, original.sale_id)
        if expected_revision_hash is not None and expected_revision_hash != mixed_refund_revision(
            original, legacy, physical, selections
        ):
            raise ConflictError("Refund history changed; review the return again")
        try:
            plan = plan_mixed_return(
                original,
                selections,
                returned_legacy_quantities=legacy,
                returned_base_units=physical,
            )
        except ValueError as error:
            raise BusinessRuleError(str(error)) from error
        if plan.total_amount <= 0:
            raise BusinessRuleError(
                "Cash refund requires a positive payout; use quarantine for zero-value goods"
            )
        _check_expected_amount(plan.total_amount, expected_total_amount)
        return await MixedReturnJournalService(journal).receive(
            tenant_id=tenant_id,
            actor_id=actor_id,
            operation_id=operation_id,
            parent_sale_id=parent_sale_id,
            selections=selections,
        )
