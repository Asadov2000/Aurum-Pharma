"""Preview historical cash refunds without reserving goods or issuing money."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.physical_cash_refund_api_schemas import (
    PhysicalRefundOptionLine,
    PhysicalRefundOptions,
    PhysicalRefundQuote,
    PhysicalRefundQuoteLine,
    PhysicalRefundQuoteRequest,
)
from app.domains.pos.physical_cash_refund_preview_repository import (
    PhysicalCashRefundPreviewRepository,
)
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3, build_physical_receipt
from app.domains.pos.physical_receipt_read import physical_receipt_snapshot
from app.domains.pos.physical_return_allocation import plan_physical_return
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_documents import UNIT_LABELS


class PhysicalCashRefundPreviewService:
    def __init__(self, repository: PhysicalCashRefundPreviewRepository) -> None:
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
    ) -> tuple[PhysicalCheckoutResultV3, dict[UUID, int]]:
        await UnitCartRepository(self.repository.session).assert_actor(tenant_id, actor_id)
        history = await self.repository.read(tenant_id, parent_sale_id)
        if history is None:
            raise NotFoundError("Original physical checkout not found")
        sale, checkout = history
        if allowed_branch_ids is not None and sale.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Physical refund branch access denied")
        authority = PhysicalReturnJournalRepository(self.repository.session)
        await authority.assert_actor(tenant_id, actor_id, sale.branch_id)
        try:
            original = PhysicalCheckoutResultV3.model_validate(checkout.result_payload)
        except ValidationError as error:
            raise AurumError("Original physical checkout is inconsistent") from error
        if (
            sale.sale_type != "sale"
            or sale.status != "completed"
            or original.tenant_id != tenant_id
            or original.sale_id != parent_sale_id
            or original.operation_id != checkout.id
            or original.branch_id != sale.branch_id
            or original.register_id != sale.register_id
            or original.shift_id != sale.shift_id
            or original.cashier_id != sale.cashier_user_id
            or original.total_amount != sale.total_amount
            or build_physical_receipt(original) != physical_receipt_snapshot(sale)
        ):
            raise AurumError("Original physical checkout is inconsistent")
        cursors = await self.repository.cursors(tenant_id, original.stock_command_id)
        originals = {line.id: line for line in original.lines}
        if any(
            line_id not in originals or not 0 <= amount <= originals[line_id].base_units
            for line_id, amount in cursors.items()
        ):
            raise AurumError("Stored refund quantities are inconsistent")
        await authority.assert_actor(tenant_id, actor_id, sale.branch_id)
        return original, cursors

    async def options(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> PhysicalRefundOptions:
        original, cursors = await self._history(
            tenant_id, parent_sale_id, actor_id, allowed_branch_ids
        )
        settings = await FoundationRepository(self.repository.session).get_settings(tenant_id)
        if settings is None:
            raise AurumError("POS settings are unavailable")
        return PhysicalRefundOptions.model_validate(
            {
                "parent_sale_id": parent_sale_id,
                "branch_id": original.branch_id,
                "register_id": original.register_id,
                "original_receipt_number": original.receipt_number,
                "reason_mode": settings.refund_reason_mode,
                "lines": tuple(
                    PhysicalRefundOptionLine(
                        line_id=line.id,
                        name=line.name,
                        unit_label=UNIT_LABELS[line.packaging.unit],
                        packaging=line.packaging,
                        sold_base_units=line.base_units,
                        returned_base_units=cursors.get(line.id, 0),
                        remaining_base_units=line.base_units - cursors.get(line.id, 0),
                        original_sale_format=line.sale_format,
                        original_retail_unit_price=line.original_retail_unit_price,
                    )
                    for line in original.lines
                ),
            }
        )

    async def quote(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: PhysicalRefundQuoteRequest,
    ) -> PhysicalRefundQuote:
        original, cursors = await self._history(
            tenant_id, parent_sale_id, actor_id, allowed_branch_ids
        )
        selections = request.selections()
        try:
            plan = plan_physical_return(original, selections, returned_base_units=cursors)
        except ValueError as error:
            raise BusinessRuleError("Choose an available whole refund quantity") from error
        return PhysicalRefundQuote(
            parent_sale_id=parent_sale_id,
            revision_hash=physical_refund_revision(original, cursors, selections),
            total_amount=plan.total_amount,
            lines=tuple(
                PhysicalRefundQuoteLine(
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
