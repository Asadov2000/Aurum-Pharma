"""Authorize and preview mixed cash refunds using immutable historical facts."""

from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedLegacyRefundOptionLine,
    MixedLegacyRefundQuoteLine,
    MixedPhysicalRefundOptionLine,
    MixedPhysicalRefundQuoteLine,
    MixedRefundOptions,
    MixedRefundQuote,
    MixedRefundQuoteRequest,
)
from app.domains.pos.mixed_cash_refund_preview_repository import MixedCashRefundPreviewRepository
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_documents import (
    LegacyResultLineV4,
    MixedCheckoutResultV4,
    MixedReceiptDocumentV4,
    build_mixed_receipt,
)
from app.domains.pos.mixed_receipt_read import mixed_receipt_snapshot
from app.domains.pos.mixed_return_allocation import LegacyReturnAllocation, plan_mixed_return
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import LegacyOffset
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.domains.pos.unit_contracts import EXACT_CONTEXT, CheckoutPaymentV2
from app.domains.pos.unit_documents import UNIT_LABELS

_LEGACY_OFFSET: TypeAdapter[Decimal] = TypeAdapter(LegacyOffset)


class MixedCashRefundPreviewService:
    def __init__(self, repository: MixedCashRefundPreviewRepository) -> None:
        self.repository = repository

    async def assert_access(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> None:
        await self._history(tenant_id, parent_sale_id, actor_id, allowed_branch_ids)

    async def _history(
        self,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> tuple[MixedCheckoutResultV4, dict[UUID, Decimal], dict[UUID, int]]:
        authority = MixedReturnJournalRepository(self.repository.session)
        await authority.assert_identity(tenant_id, actor_id)
        history = await self.repository.read(tenant_id, parent_sale_id)
        if history is None:
            raise NotFoundError("Original mixed checkout not found")
        sale, checkout = history
        if allowed_branch_ids is not None and sale.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Mixed refund branch access denied")
        await authority.assert_actor(tenant_id, actor_id, sale.branch_id)
        try:
            original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            receipt = MixedReceiptDocumentV4.model_validate(checkout.receipt_payload)
        except ValidationError as error:
            raise AurumError("Original mixed checkout is inconsistent") from error
        if (
            sale.tenant_id != tenant_id
            or sale.id != parent_sale_id
            or sale.sale_type != "sale"
            or sale.status != "completed"
            or sale.parent_sale_id is not None
            or sale.is_test
            or checkout.tenant_id != tenant_id
            or checkout.sale_id != parent_sale_id
            or checkout.branch_id != sale.branch_id
            or checkout.register_id != sale.register_id
            or checkout.shift_id != sale.shift_id
            or checkout.cashier_user_id != sale.cashier_user_id
            or checkout.cash_intent_id != checkout.id
            or checkout.stock_command_id != checkout.id
            or checkout.payment_id != checkout.id
            or original.tenant_id != tenant_id
            or original.sale_id != parent_sale_id
            or original.operation_id != checkout.id
            or original.event_id != checkout.id
            or original.stock_command_id != checkout.id
            or original.branch_id != sale.branch_id
            or original.register_id != sale.register_id
            or original.shift_id != sale.shift_id
            or original.cashier_id != sale.cashier_user_id
            or original.is_test != sale.is_test
            or original.currency != sale.currency
            or original.total_amount != sale.total_amount
            or original.created_at != sale.created_at
            or original.completed_at != sale.completed_at
            or original.receipt_number != sale.receipt_number
            or original.receipt_sequence != sale.receipt_seq
            or original.payments
            != (CheckoutPaymentV2(id=checkout.id, method="cash", amount=original.total_amount),)
            or receipt != build_mixed_receipt(original)
            or receipt != mixed_receipt_snapshot(sale)
        ):
            raise AurumError("Original mixed checkout is inconsistent")
        legacy, physical = await self.repository.cursors(tenant_id, parent_sale_id)
        legacy_lines = {
            line.id: line for line in original.lines if isinstance(line, LegacyResultLineV4)
        }
        physical_lines = {
            line.id: line for line in original.lines if isinstance(line, PhysicalResultLineV3)
        }
        try:
            for line_id, quantity in legacy.items():
                if (
                    type(line_id) is not UUID
                    or line_id not in legacy_lines
                    or type(quantity) is not Decimal
                    or _LEGACY_OFFSET.validate_python(quantity) > legacy_lines[line_id].quantity
                ):
                    raise ValueError("Invalid legacy cursor")
            for line_id, units in physical.items():
                if (
                    type(line_id) is not UUID
                    or line_id not in physical_lines
                    or type(units) is not int
                    or not 0 <= units <= physical_lines[line_id].base_units
                    or units % physical_lines[line_id].packaging.dispense_step
                ):
                    raise ValueError("Invalid physical cursor")
        except ValueError as error:
            raise AurumError("Stored refund quantities are inconsistent") from error
        await authority.assert_actor(tenant_id, actor_id, sale.branch_id)
        return original, legacy, physical

    async def options(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> MixedRefundOptions:
        original, legacy, physical = await self._history(
            tenant_id, parent_sale_id, actor_id, allowed_branch_ids
        )
        settings = await FoundationRepository(self.repository.session).get_settings(tenant_id)
        if settings is None:
            raise AurumError("POS settings are unavailable")
        lines: list[MixedLegacyRefundOptionLine | MixedPhysicalRefundOptionLine] = []
        for line in original.lines:
            if isinstance(line, LegacyResultLineV4):
                returned = legacy.get(line.id, Decimal("0.000"))
                with localcontext(EXACT_CONTEXT):
                    remaining = line.quantity - returned
                lines.append(
                    MixedLegacyRefundOptionLine(
                        line_id=line.id,
                        name=line.name,
                        sold_quantity=line.quantity,
                        returned_quantity=returned,
                        remaining_quantity=remaining,
                        original_retail_unit_price=line.original_retail_unit_price,
                    )
                )
            else:
                units = physical.get(line.id, 0)
                lines.append(
                    MixedPhysicalRefundOptionLine(
                        line_id=line.id,
                        name=line.name,
                        unit_label=UNIT_LABELS[line.packaging.unit],
                        packaging=line.packaging,
                        sold_base_units=line.base_units,
                        returned_base_units=units,
                        remaining_base_units=line.base_units - units,
                        original_sale_format=line.sale_format,
                        original_retail_unit_price=line.original_retail_unit_price,
                    )
                )
        return MixedRefundOptions.model_validate(
            {
                "parent_sale_id": parent_sale_id,
                "branch_id": original.branch_id,
                "register_id": original.register_id,
                "original_receipt_number": original.receipt_number,
                "reason_mode": settings.refund_reason_mode,
                "lines": tuple(lines),
            }
        )

    async def quote(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: MixedRefundQuoteRequest,
    ) -> MixedRefundQuote:
        original, legacy, physical = await self._history(
            tenant_id, parent_sale_id, actor_id, allowed_branch_ids
        )
        selections = request.selections()
        try:
            plan = plan_mixed_return(
                original,
                selections,
                returned_legacy_quantities=legacy,
                returned_base_units=physical,
            )
        except ValueError as error:
            raise BusinessRuleError("Choose an available refund quantity and format") from error
        return MixedRefundQuote(
            parent_sale_id=parent_sale_id,
            revision_hash=mixed_refund_revision(original, legacy, physical, selections),
            total_amount=plan.total_amount,
            lines=tuple(
                MixedLegacyRefundQuoteLine(
                    line_id=line.line_id,
                    quantity=line.quantity,
                    offset=line.offset,
                    gross_amount=line.gross_amount,
                    discount_amount=line.discount_amount,
                    net_amount=line.net_amount,
                )
                if isinstance(line, LegacyReturnAllocation)
                else MixedPhysicalRefundQuoteLine(
                    line_id=line.line_id,
                    sale_format=line.sale_format,
                    quantity=line.quantity,
                    base_units=line.base_units,
                    offset=line.offset,
                    gross_amount=line.gross_amount,
                    discount_amount=line.discount_amount,
                    net_amount=line.net_amount,
                )
                for line in plan.lines
            ),
        )
